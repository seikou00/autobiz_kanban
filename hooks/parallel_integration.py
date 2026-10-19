#!/usr/bin/env python3
"""Feature integration worktrees and explicitly confirmed local promotion.

Repository ``gitRoot`` remains the source identity. Only ``integration_repo``
may select the mutable integration checkout; old manifests keep direct mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, feature_dir, resolve_feature, resolve_workspace
from hooks.parallel_runtime import global_worktrees_root, load_manifest, plan_digest, run_dir, run_lock, save_manifest, utc_now
from hooks.plan_json import load_plan_bundle
from hooks.repository_snapshot import git_status_porcelain

PROMOTION_STATES = {"awaiting_user_confirmation", "promotion_prepared", "promoting_main", "integration_retained"}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise ValueError(f"integration_git_failed:{args[0]}:{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def isolated(manifest: dict) -> bool:
    return manifest.get("integrationMode") == "isolated"


def integration_repo(binding: dict) -> Path:
    integration = binding.get("integration")
    if "integrationRepositoryRef" in binding and not isinstance(integration, dict):
        raise ValueError("integration_worktree_binding_missing")
    return Path(integration["worktreePath"] if isinstance(integration, dict) else binding["gitRoot"]).resolve()


def record_integration_head(manifest: dict, ref: str, sha: str) -> None:
    binding = manifest["repositories"][ref]
    binding["headSha"] = sha
    if isolated(manifest):
        canonical = binding.get("integrationRepositoryRef", ref)
        for other_ref, other in manifest["repositories"].items():
            if other.get("integrationRepositoryRef", other_ref) == canonical:
                other["headSha"] = sha


def assert_integration(binding: dict, *, clean: bool = True) -> Path:
    repo = integration_repo(binding)
    record = binding.get("integration")
    if not isinstance(record, dict) or record.get("owner") != "plugin":
        raise ValueError("integration_worktree_binding_missing")
    if isinstance(record, dict):
        source = Path(binding["gitRoot"])
        if repo == source.resolve() or git(repo, "symbolic-ref", "--short", "HEAD") != record["branchName"]:
            raise ValueError("integration_worktree_binding_mismatch")
        common = lambda root: (root / git(root, "rev-parse", "--git-common-dir")).resolve()
        if common(repo) != common(source) or Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
            raise ValueError("integration_repository_mismatch")
    if clean:
        status = git_status_porcelain(repo)
        if status.returncode or status.stdout.strip():
            raise ValueError(f"integration_worktree_dirty:{repo}")
        if git(repo, "rev-parse", "HEAD") != binding["headSha"]:
            raise ValueError(f"integration_head_changed:{repo}")
    return repo


def initialize_integrations(workspace: Path, feature: str, run_id: str) -> None:
    """Persist ownership before creation so interrupted provisioning is reusable."""
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if not isolated(manifest) or manifest.get("integrationReady"):
            return
        for ref, binding in manifest["repositories"].items():
            source = Path(binding["gitRoot"])
            canonical = binding.get("integrationRepositoryRef", ref)
            if canonical != ref:
                binding["integration"] = dict(manifest["repositories"][canonical]["integration"])
                save_manifest(workspace, feature, run_id, manifest)
                assert_integration(binding)
                continue
            record = binding.get("integration")
            if not isinstance(record, dict):
                token = hashlib.sha256(ref.encode()).hexdigest()[:12]
                branch = f"codex/integration/{feature}/{run_id}/{token}"
                git(source, "check-ref-format", "--branch", branch)
                record = {"branchName": branch, "worktreePath": str(global_worktrees_root(workspace) / run_id / token / "integration"), "owner": "plugin"}
                binding["integration"] = record
                save_manifest(workspace, feature, run_id, manifest)
            target = Path(record["worktreePath"])
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                branches = git(source, "for-each-ref", "--format=%(refname)", f"refs/heads/{record['branchName']}").splitlines()
                if branches:
                    if git(source, "rev-parse", f"refs/heads/{record['branchName']}") != binding["baseSha"]:
                        raise ValueError("integration_orphan_branch_changed")
                    git(source, "worktree", "add", str(target), record["branchName"])
                else:
                    git(source, "worktree", "add", "-b", record["branchName"], str(target), binding["baseSha"])
            assert_integration(binding)
        manifest["integrationReady"] = True
        manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)


def promotion_view(manifest: dict) -> dict:
    return {"integrationMode": manifest.get("integrationMode", "direct"),
            "integrationWorkspaces": {ref: {**binding["integration"], "headSha": binding["headSha"], "targetBranch": binding["baseBranch"], "baselineSha": binding["baseSha"]}
                                      for ref, binding in manifest.get("repositories", {}).items() if isinstance(binding.get("integration"), dict)},
            "mainPromotion": manifest.get("mainPromotion")}


def await_confirmation(manifest: dict) -> None:
    """Called under the evidence aggregate's run lock."""
    if not isolated(manifest):
        return
    for binding in manifest["repositories"].values():
        assert_integration(binding)
    old = manifest.get("mainPromotion") or {}
    if old.get("status") in {"promoting", "merged"}:
        raise ValueError("integration_promotion_already_started")
    manifest["mainPromotion"] = {"status": "awaiting_user_confirmation", "readyAt": utc_now()}
    manifest["status"] = "awaiting_user_confirmation"


def _require_ready(manifest: dict) -> None:
    if not isolated(manifest) or not manifest.get("integrationReady"):
        raise ValueError("integration_mode_required")
    report = manifest.get("finalEvidenceAggregate") or {}
    if not report.get("passed") or report.get("hasBlockingDeferredIssues"):
        raise ValueError("integration_final_validation_required")
    if any(batch.get("status") != "merged" or not batch.get("mergeCommitSha") for batch in manifest["batches"].values()):
        raise ValueError("integration_deliveries_incomplete")
    e2e = manifest.get("validationBatches", {}).get("V-E2E", {})
    expected = json.dumps({ref: b["headSha"] for ref, b in manifest["repositories"].items()}, sort_keys=True)
    if e2e.get("status") != "verified" or e2e.get("candidateSha") != expected:
        raise ValueError("integration_e2e_commit_mismatch")


def _assert_plan(workspace: Path, feature: str, manifest: dict) -> None:
    if plan_digest(load_plan_bundle(feature_dir(workspace, feature))) != manifest["planDigest"]:
        raise ValueError("integration_plan_changed_revalidation_required")


def _snapshot(manifest: dict) -> dict:
    _require_ready(manifest)
    repositories = {}
    for ref, binding in manifest["repositories"].items():
        if binding.get("integrationRepositoryRef", ref) != ref:
            continue
        repo = assert_integration(binding)
        target = git(repo, "rev-parse", f"refs/heads/{binding['baseBranch']}^{{commit}}")
        candidate = binding["headSha"]
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", target, candidate], cwd=repo, capture_output=True)
        if ancestor.returncode:
            raise ValueError(f"integration_main_sync_required:{ref}")
        repositories[ref] = {"targetBranch": binding["baseBranch"], "targetSha": target, "candidateSha": candidate,
                             "branchName": binding["integration"]["branchName"], "worktreePath": str(repo),
                             "diffStat": git(repo, "diff", "--stat", target, candidate),
                             "diffDigest": digest(git(repo, "diff", "--binary", target, candidate))}
    return {"runId": manifest["runId"], "repositories": repositories,
            "planDigest": manifest["planDigest"],
            "previousPromotions": [{"approvalDigest": item.get("approvalDigest"), "repositories": item.get("repositories", {})}
                                   for item in manifest.get("mainPromotionHistory", [])],
            "evidenceDigest": digest(manifest["finalEvidenceAggregate"])}


def prepare_promotion(workspace: Path, feature: str, run_id: str) -> dict:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") not in PROMOTION_STATES - {"promoting_main"}:
            raise ValueError("integration_not_waiting_for_confirmation")
        _assert_plan(workspace, feature, manifest)
        snapshot = _snapshot(manifest)
        approval = digest(snapshot)
        manifest["mainPromotion"] = {"status": "prepared", "approvalDigest": approval, "snapshot": snapshot, "preparedAt": utc_now()}
        manifest["status"] = "promotion_prepared"
        save_manifest(workspace, feature, run_id, manifest)
        path = run_dir(workspace, feature, run_id) / "main-promotion.json"
        atomic_write_json(path, manifest["mainPromotion"])
        return {"success": True, "runId": run_id, "status": manifest["status"], "reportPath": str(path), **promotion_view(manifest)}


def _target_worktree(repo: Path, branch: str) -> Path | None:
    # -z preserves paths containing spaces, quotes, and non-ASCII characters.
    records = git(repo, "worktree", "list", "--porcelain", "-z").split("\0\0")
    for record in records:
        fields = record.split("\0")
        if f"branch refs/heads/{branch}" in fields:
            return Path(next(field[9:] for field in fields if field.startswith("worktree ")))
    return None


def promote_main(workspace: Path, feature: str, run_id: str, *, approval_digest: str, approved_by: str, confirm: bool = False) -> dict:
    if not confirm or not approved_by.strip():
        raise ValueError("integration_explicit_user_confirmation_required")
    with run_lock(workspace, feature, run_id), ExitStack() as locks:
        manifest = load_manifest(workspace, feature, run_id)
        promotion = manifest.get("mainPromotion") or {}
        if promotion.get("approvalDigest") != approval_digest or promotion.get("status") not in {"prepared", "promoting", "merged"}:
            raise ValueError("integration_approval_mismatch")
        if promotion["status"] == "merged":
            return {"success": True, "reused": True, "runId": run_id, "status": manifest["status"], **promotion_view(manifest)}
        snapshot = promotion["snapshot"]
        _require_ready(manifest)
        _assert_plan(workspace, feature, manifest)
        if digest(snapshot) != approval_digest or digest(manifest["finalEvidenceAggregate"]) != snapshot["evidenceDigest"]:
            raise ValueError("integration_approval_stale")
        roots = {}
        for ref in snapshot["repositories"]:
            binding = manifest["repositories"][ref]
            repo = assert_integration(binding)
            common = (repo / git(repo, "rev-parse", "--git-common-dir")).resolve()
            roots[str(common)] = common
            item = snapshot["repositories"][ref]
            if binding["headSha"] != item["candidateSha"] or binding["baseBranch"] != item["targetBranch"]:
                raise ValueError("integration_approval_stale")
        for root in sorted(roots):
            locks.enter_context(FileLock(roots[root] / "autobiz-main-promotion.lock"))
        results = promotion.setdefault("repositories", {})
        # Validate every target before the first mutation. Later failures remain
        # a recoverable partial promotion, never a request to merge everything twice.
        targets = {}
        for ref, item in snapshot["repositories"].items():
            repo = integration_repo(manifest["repositories"][ref])
            current = git(repo, "rev-parse", f"refs/heads/{item['targetBranch']}")
            previous = results.get(ref, {})
            if previous.get("status") == "merged":
                if subprocess.run(["git", "merge-base", "--is-ancestor", item["candidateSha"], current], cwd=repo, capture_output=True).returncode:
                    raise ValueError(f"integration_promoted_target_rewritten:{ref}")
                continue
            if current == item["candidateSha"] and previous.get("status") == "promoting":
                results[ref] = {"status": "merged", "commitSha": current, "recovered": True}
                continue
            if current != item["targetSha"]:
                raise ValueError(f"integration_target_changed_reprepare_required:{ref}")
            target = _target_worktree(repo, item["targetBranch"])
            if target is not None:
                status = git_status_porcelain(target)
                if status.returncode or status.stdout.strip():
                    raise ValueError(f"integration_target_worktree_dirty:{ref}")
            targets[ref] = target
        promotion.update({"status": "promoting", "approvedBy": approved_by, "approvedAt": promotion.get("approvedAt") or utc_now()})
        manifest["status"] = "promoting_main"
        save_manifest(workspace, feature, run_id, manifest)
        for ref, target in targets.items():
            item = snapshot["repositories"][ref]
            repo = integration_repo(manifest["repositories"][ref])
            results[ref] = {"status": "promoting", "commitSha": item["candidateSha"]}
            save_manifest(workspace, feature, run_id, manifest)
            if target is None:
                git(repo, "update-ref", f"refs/heads/{item['targetBranch']}", item["candidateSha"], item["targetSha"])
            else:
                if git(target, "symbolic-ref", "--short", "HEAD") != item["targetBranch"] or git(target, "rev-parse", "HEAD") != item["targetSha"]:
                    raise ValueError(f"integration_target_changed_reprepare_required:{ref}")
                git(target, "merge", "--ff-only", item["candidateSha"])
            if git(repo, "rev-parse", f"refs/heads/{item['targetBranch']}") != item["candidateSha"]:
                raise ValueError(f"integration_promotion_sha_mismatch:{ref}")
            results[ref] = {"status": "merged", "commitSha": item["candidateSha"], "mergedAt": utc_now()}
            save_manifest(workspace, feature, run_id, manifest)
        promotion.update({"status": "merged", "mergedAt": utc_now()})
        manifest["status"] = "succeeded_with_issues" if manifest["finalEvidenceAggregate"].get("hasDeferredIssues") else "succeeded"
        save_manifest(workspace, feature, run_id, manifest)
        return {"success": True, "runId": run_id, "status": manifest["status"], **promotion_view(manifest)}


def keep_integration(workspace: Path, feature: str, run_id: str) -> dict:
    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if manifest.get("status") not in PROMOTION_STATES - {"promoting_main"}:
            raise ValueError("integration_not_waiting_for_confirmation")
        manifest["mainPromotion"] = {"status": "retained", "retainedAt": utc_now()}
        manifest["status"] = "integration_retained"
        save_manifest(workspace, feature, run_id, manifest)
        return {"success": True, "runId": run_id, "status": manifest["status"], **promotion_view(manifest)}


def integration_sync_write_error(manifest: dict, binding: dict, target: Path) -> str | None:
    """Authorize exactly the conflict paths registered by sync-main."""
    from hooks.merge_resolution import outside_diff_digest, resolution_files

    try:
        if manifest.get("status") != "integration_sync_conflicted":
            return "integration_sync_resolution_not_running"
        state = binding.get("mainSync") or {}
        if state.get("status") != "conflicted":
            return "integration_sync_resolution_not_running"
        repo = assert_integration(binding, clean=False)
        files = resolution_files(repo, state.get("conflictedFiles"))
        if target.resolve().relative_to(repo).as_posix() not in files:
            return "integration_sync_file_not_authorized"
        if git(repo, "rev-parse", "HEAD") != state["baseSha"] or git(repo, "rev-parse", "MERGE_HEAD") != state["targetSha"]:
            return "integration_sync_binding_changed"
        if outside_diff_digest(repo, files) != state["outsideDigest"]:
            return "integration_sync_outside_changes"
    except (ValueError, OSError) as exc:
        return str(exc)
    return None


def sync_main(workspace: Path, feature: str, run_id: str, *, complete: bool = False) -> dict:
    """Merge target changes into integration only, then require a fresh E2E.

    On conflicts the owned integration checkout is retained. ``complete-sync``
    seals only the registered conflict files after the operator resolves them.
    """
    from hooks.merge_resolution import has_conflict_markers, outside_diff_digest, resolution_files
    from hooks.commit_message import build_commit_message

    with run_lock(workspace, feature, run_id):
        manifest = load_manifest(workspace, feature, run_id)
        if not isolated(manifest) or manifest.get("status") not in PROMOTION_STATES | {"integration_sync_required", "integration_sync_conflicted", "running", "verifying"}:
            raise ValueError("integration_sync_not_allowed")
        if any(b.get("status") != "merged" for b in manifest["batches"].values()):
            raise ValueError("integration_deliveries_incomplete")
        if manifest.get("status") == "promoting_main":
            # A remaining target may advance after another repository merged.
            # Preserve that partial result, then require a new combined review.
            manifest.setdefault("mainPromotionHistory", []).append(manifest["mainPromotion"])
            manifest["mainPromotion"] = {"status": "validation_required"}
            manifest.pop("finalEvidenceAggregate", None)
            manifest["status"] = "integration_sync_required"
            save_manifest(workspace, feature, run_id, manifest)
        for ref, binding in manifest["repositories"].items():
            if binding.get("integrationRepositoryRef", ref) != ref:
                continue
            repo = assert_integration(binding, clean=False)
            state = binding.get("mainSync") or {}
            if state.get("status") == "conflicted":
                if not complete:
                    continue
                files = resolution_files(repo, state["conflictedFiles"])
                if git(repo, "rev-parse", "HEAD") != state["baseSha"] or git(repo, "rev-parse", "MERGE_HEAD") != state["targetSha"]:
                    raise ValueError("integration_sync_binding_changed")
                if outside_diff_digest(repo, files) != state["outsideDigest"]:
                    raise ValueError("integration_sync_outside_changes")
                if any(has_conflict_markers(repo / file) for file in files):
                    raise ValueError("integration_sync_conflict_markers_remaining")
                git(repo, "add", "--", *(f":(literal){file}" for file in files))
                binding["mainSync"] = {**state, "status": "merging", "resolvedTreeSha": git(repo, "write-tree")}
                save_manifest(workspace, feature, run_id, manifest)
                git(repo, "-c", "core.editor=true", "commit", "--no-edit")
                record_integration_head(manifest, ref, git(repo, "rev-parse", "HEAD"))
                binding["mainSync"] = {"status": "merged", "targetSha": state["targetSha"]}
                save_manifest(workspace, feature, run_id, manifest)
                continue
            # Reconcile an interruption after Git committed but before JSON.
            if state.get("status") == "merging":
                merge_head = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=repo, capture_output=True, text=True)
                if merge_head.returncode:
                    head = git(repo, "rev-parse", "HEAD")
                    if head != state["baseSha"] and git(repo, "show", "-s", "--format=%P", "HEAD").split() == [state["baseSha"], state["targetSha"]]:
                        record_integration_head(manifest, ref, head)
                        binding["mainSync"] = {"status": "merged", "targetSha": state["targetSha"]}
                        save_manifest(workspace, feature, run_id, manifest)
                    else:
                        assert_integration(binding)
                else:
                    files = [name for name in git(repo, "diff", "--name-only", "--diff-filter=U", "-z").split("\0") if name]
                    if not files:
                        if complete and state.get("resolvedTreeSha") == git(repo, "write-tree"):
                            git(repo, "-c", "core.editor=true", "commit", "--no-edit")
                            record_integration_head(manifest, ref, git(repo, "rev-parse", "HEAD"))
                            binding["mainSync"] = {"status": "merged", "targetSha": state["targetSha"]}
                            save_manifest(workspace, feature, run_id, manifest)
                            continue
                        raise ValueError("integration_sync_interrupted_commit_requires_inspection")
                    binding["mainSync"] = {**state, "status": "conflicted", "conflictedFiles": files, "outsideDigest": outside_diff_digest(repo, files)}
                    save_manifest(workspace, feature, run_id, manifest)
                    continue
            assert_integration(binding)
            target = git(repo, "rev-parse", f"refs/heads/{binding['baseBranch']}")
            if subprocess.run(["git", "merge-base", "--is-ancestor", target, binding["headSha"]], cwd=repo, capture_output=True).returncode == 0:
                continue
            binding["mainSync"] = {"status": "merging", "baseSha": binding["headSha"], "targetSha": target}
            manifest["status"] = "integration_sync_required"
            manifest.pop("finalEvidenceAggregate", None)
            manifest["mainPromotion"] = {"status": "validation_required"}
            save_manifest(workspace, feature, run_id, manifest)
            result = subprocess.run(["git", "merge", "--no-ff", "-m", build_commit_message(manifest["taskCardId"], f"同步主分支 {binding['baseBranch']}"), target], cwd=repo, capture_output=True, text=True)
            if result.returncode:
                files = [name for name in git(repo, "diff", "--name-only", "--diff-filter=U", "-z").split("\0") if name]
                binding["mainSync"].update({"status": "conflicted" if files else "merging", "conflictedFiles": files, "outsideDigest": outside_diff_digest(repo, files) if files else None})
                manifest["status"] = "integration_sync_conflicted" if files else "integration_sync_required"
                save_manifest(workspace, feature, run_id, manifest)
                if not files:
                    raise ValueError(f"integration_sync_failed:{result.stderr.strip()}")
            else:
                record_integration_head(manifest, ref, git(repo, "rev-parse", "HEAD"))
                binding["mainSync"] = {"status": "merged", "targetSha": target}
                save_manifest(workspace, feature, run_id, manifest)
        conflicted = {ref: b["mainSync"] for ref, b in manifest["repositories"].items() if (b.get("mainSync") or {}).get("status") == "conflicted"}
        if conflicted:
            manifest["status"] = "integration_sync_conflicted"
        elif (manifest.get("mainPromotion") or {}).get("status") == "validation_required":
            manifest["status"] = "running"
        save_manifest(workspace, feature, run_id, manifest)
        return {"success": not conflicted, "runId": run_id, "status": manifest["status"], "conflicts": conflicted, **promotion_view(manifest)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "prepare", "promote", "keep", "sync-main", "complete-sync"))
    parser.add_argument("--workspace")
    parser.add_argument("--feature", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--approval-digest")
    parser.add_argument("--approved-by", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        workspace, feature = resolve_workspace(args.workspace), resolve_feature(args.feature)
        if args.command == "prepare":
            result = prepare_promotion(workspace, feature, args.run_id)
        elif args.command == "promote":
            result = promote_main(workspace, feature, args.run_id, approval_digest=args.approval_digest or "", approved_by=args.approved_by, confirm=args.confirm)
        elif args.command == "keep":
            result = keep_integration(workspace, feature, args.run_id)
        elif args.command in {"sync-main", "complete-sync"}:
            result = sync_main(workspace, feature, args.run_id, complete=args.command == "complete-sync")
        else:
            manifest = load_manifest(workspace, feature, args.run_id)
            result = {"success": True, "runId": args.run_id, "status": manifest["status"], **promotion_view(manifest)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["success"] else 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
