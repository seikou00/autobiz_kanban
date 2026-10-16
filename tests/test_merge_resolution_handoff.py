"""Exercise the model-to-plugin handoff with real native Git worktrees."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "hooks"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import frontend_route_write_guard as write_guard
from hooks.parallel_merge_train import _candidate_paths, complete_resolution, prepare_resolution
from hooks.parallel_runtime import load_manifest, save_manifest


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


@contextlib.contextmanager
def conflicted_candidate(filename: str = "src/service.py", maximum: int = 2, scenario: str = "modify"):
    def content(side):
        if scenario == "binary":
            return {"base": b"base\0bytes\xff", "main": b"main\0bytes\xfe", "delivery": b"delivery\0bytes\xff"}[side]
        if scenario == "multi-hunk":
            return (f"start = '{side}'\n" + "unchanged\n" * 20 + f"end = '{side}'\n").encode()
        return f"value = '{side}'\n".encode()

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = root / "business"
        repo.mkdir()
        workspace = root / "artifacts"
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Merge Test")
        git(repo, "config", "user.email", "merge@example.com")
        if scenario == "multi-hunk":
            git(repo, "config", "merge.conflictStyle", "diff3")
        source = repo / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        if scenario != "add-add":
            source.write_bytes(content("base"))
        (repo / "clean.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "base")
        git(repo, "checkout", "-b", "delivery")
        source.write_bytes(content("delivery"))
        (repo / "clean.txt").write_text("auto-merged delivery\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "delivery")
        delivery = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "checkout", "main")
        source.parent.mkdir(parents=True, exist_ok=True)
        if scenario == "delete-modify":
            source.unlink()
        else:
            source.write_bytes(content("main"))
        git(repo, "add", ".")
        git(repo, "commit", "-am", "main update")
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        candidate, branch = _candidate_paths(workspace, "feature", "run", "default", 1)
        candidate.parent.mkdir(parents=True)
        git(repo, "worktree", "add", "-b", branch, str(candidate), base)
        merge = git(candidate, "merge", "--no-ff", "-m", "merge delivery", "delivery", check=False)
        assert merge.returncode != 0 and "CONFLICT" in merge.stdout
        record = {
            "repositoryRef": "default", "wave": 1, "batchIds": ["B001"],
            "baseSha": base, "deliveryCommits": {"B001": delivery},
            "worktreePath": str(candidate), "branchName": branch, "status": "candidate_conflicted",
            "conflictContext": {
                "baseSha": base, "batchIds": ["B001"], "conflictedFiles": [filename],
                "candidateWorktree": str(candidate), "repositoryRef": "default", "wave": 1,
                "attempts": 0, "taskCardId": "Z990692-294",
                "conflictMarkers": {filename: (candidate / filename).read_text(encoding="utf-8", errors="replace")}
                if (candidate / filename).is_file() else {},
            },
        }
        manifest = {
            "runId": "run", "featureId": "feature", "status": "blocked", "taskCardId": "Z990692-294",
            "repositories": {"default": {"gitRoot": str(repo)}},
            "mergeTrains": {"default:wave-001": record},
            "runtimeConfig": {"conflictResolution": {"maxAttempts": maximum, "enableAutoResolve": False}},
        }
        save_manifest(workspace, "feature", "run", manifest)
        (workspace / ".autobizdevops/state.json").write_text("{}", encoding="utf-8")
        yield workspace, repo, candidate, filename, base, delivery


def prepare(workspace: Path):
    return prepare_resolution(workspace, "feature", "run", 1, "default")


def complete(workspace: Path):
    return complete_resolution(workspace, "feature", "run", 1, "default")


class MergeResolutionHandoffTest(unittest.TestCase):
    def test_context_provides_exact_paths_three_way_snapshots_and_business_references(self):
        with conflicted_candidate("src/申请书 [单笔].py") as (workspace, repo, candidate, filename, base, delivery):
            manifest = load_manifest(workspace, "feature", "run")
            evidence_path = workspace / "stage evidence.json"
            evidence_path.write_text('{"evidenceId":"review-001"}')
            manifest["batches"] = {"B001": {"taskIds": ["T004"], "stageStates": {
                "review": {"status": "passed", "evidenceIds": ["review-001"], "evidencePath": str(evidence_path)}}},
                "B000": {"taskIds": ["T001"]}}
            ancestor = git(repo, "rev-parse", base + "^").stdout.strip()
            manifest["mergeTrains"]["default:wave-000"] = {
                "status": "promoted", "repositoryRef": "default", "batchIds": ["B000"],
                "baseSha": ancestor, "candidateSha": base}
            save_manifest(workspace, "feature", "run", manifest)
            plan = workspace / ".autobizdevops/features/feature/plans/B001/plan.json"
            plan.parent.mkdir(parents=True)
            plan.write_text(json.dumps({"tasks": [{"id": "T004", "goal": "单笔下载", "acceptanceCriteria": ["保留鉴权"]}]}))
            prepared = prepare(workspace)
            self.assertTrue(prepared["success"], prepared)
            index = json.loads(Path(prepared["contextPath"]).read_bytes())
            self.assertEqual(Path(prepared["manifestPath"]), Path(prepared["runDir"]) / "manifest.json")
            self.assertEqual(index["identity"]["parents"], [base, delivery])
            entry = index["files"][0]
            self.assertEqual(entry["candidatePath"], str((candidate / filename).resolve()))
            for side, expected in (("base", "base"), ("ours", "main"), ("theirs", "delivery")):
                self.assertEqual(Path(entry["sides"][side]["snapshotPath"]).read_text(), f"value = '{expected}'\n")
            self.assertNotEqual(entry["sides"]["base"]["blobSha"], entry["sides"]["ours"]["blobSha"])
            self.assertEqual(len(entry["hunks"]), 1)
            self.assertIn("<<<<<<<", json.loads(Path(entry["hunksPath"]).read_text())[0]["content"])
            changes = Path(entry["changesPath"]).read_text()
            self.assertIn("+++ ours", changes)
            self.assertIn("+++ theirs", changes)
            business = json.loads(Path(index["businessContextPath"]).read_text())
            self.assertEqual(business["oursRelatedBatchIds"], ["B000"])
            incoming = business["batches"]["B001"]
            self.assertEqual(incoming["taskIds"], ["T004"])
            self.assertEqual(incoming["planSnapshot"]["tasks"][0]["goal"], "单笔下载")
            self.assertEqual(incoming["stageEvidence"][0]["evidencePath"], str(evidence_path))
            self.assertEqual(business["batches"]["B000"]["planStatus"], "missing")
            self.assertFalse(Path(prepared["contextPath"]).is_relative_to(candidate.resolve()))

    def test_context_reuses_original_snapshots_and_keeps_partial_edits(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            first = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="immediate")
            index_bytes = Path(first["contextPath"]).read_bytes()
            (candidate / filename).write_text("partially resolved\n")
            repeated = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="immediate")
            self.assertTrue(repeated["success"], repeated)
            self.assertEqual(first["contextDigest"], repeated["contextDigest"])
            self.assertEqual(Path(first["contextPath"]).read_bytes(), index_bytes)
            final = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="final")
            self.assertEqual(first["contextPath"], final["contextPath"])
            self.assertEqual(final["attempts"], 2)
            self.assertEqual((candidate / filename).read_text(), "partially resolved\n")

    def test_missing_or_modified_context_fails_without_rebuilding_it(self):
        for damage in ("missing-index", "modified-index", "modified-snapshot", "wrong-identity"):
            with self.subTest(damage=damage), conflicted_candidate() as (workspace, _, _, _, _, _):
                first = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="same")
                index_path = Path(first["contextPath"])
                index = json.loads(index_path.read_bytes())
                if damage == "missing-index":
                    index_path.unlink()
                elif damage == "modified-index":
                    index_path.write_text("{}")
                elif damage == "modified-snapshot":
                    Path(index["files"][0]["sides"]["ours"]["snapshotPath"]).write_text("tampered")
                else:
                    manifest = load_manifest(workspace, "feature", "run")
                    manifest["mergeTrains"]["default:wave-001"]["resolutionContext"]["identityDigest"] = "wrong"
                    save_manifest(workspace, "feature", "run", manifest)
                repeated = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="same")
                self.assertFalse(repeated["success"], repeated)
                self.assertIn("candidate_context_", repeated["error"])

    def test_context_marks_missing_sides_and_preserves_binary_bytes(self):
        for scenario, absent in (("add-add", "base"), ("delete-modify", "ours"), ("binary", None)):
            with self.subTest(scenario=scenario), conflicted_candidate(scenario=scenario) as (workspace, _, _, _, _, _):
                prepared = prepare(workspace)
                self.assertTrue(prepared["success"], prepared)
                index = json.loads(Path(prepared["contextPath"]).read_bytes())
                entry = index["files"][0]
                if absent:
                    self.assertEqual(entry["sides"][absent], {"exists": False, "snapshotPath": None})
                else:
                    self.assertFalse(entry["sides"]["ours"]["textUtf8"])
                    self.assertEqual(Path(entry["sides"]["ours"]["snapshotPath"]).read_bytes(), b"main\0bytes\xfe")
                business = json.loads(Path(index["businessContextPath"]).read_bytes())
                self.assertEqual(business["oursTraceStatus"], "unavailable")
                self.assertEqual(business["batches"]["B001"]["planStatus"], "missing")

    def test_rebuilt_candidate_gets_new_context_without_replacing_old_snapshots(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            first = prepare(workspace)
            original = Path(first["contextPath"]).read_bytes()
            git(candidate, "merge", "--abort")
            (candidate / filename).write_text("value = 'new main'\n")
            git(candidate, "commit", "-am", "advance candidate base")
            new_base = git(candidate, "rev-parse", "HEAD").stdout.strip()
            git(candidate, "merge", "--no-ff", "-m", "retry", "delivery", check=False)
            manifest = load_manifest(workspace, "feature", "run")
            record = manifest["mergeTrains"]["default:wave-001"]
            # A rebuild creates a fresh candidate record, not an editing retry.
            record.pop("resolutionContext")
            record.pop("resolution")
            record["baseSha"] = new_base
            record["conflictContext"].update({"baseSha": new_base, "attempts": 0,
                "conflictMarkers": {filename: (candidate / filename).read_text()}})
            save_manifest(workspace, "feature", "run", manifest)
            rebuilt = prepare(workspace)
            self.assertTrue(rebuilt["success"], rebuilt)
            self.assertNotEqual(first["contextPath"], rebuilt["contextPath"])
            self.assertEqual(Path(first["contextPath"]).read_bytes(), original)

    def test_context_indexes_multiple_hunks(self):
        with conflicted_candidate(scenario="multi-hunk") as (workspace, _, _, _, _, _):
            prepared = prepare(workspace)
            index = json.loads(Path(prepared["contextPath"]).read_bytes())
            entry = index["files"][0]
            hunks = json.loads(Path(entry["hunksPath"]).read_bytes())
            self.assertEqual([(h["startLine"], h["endLine"]) for h in hunks], [(1, 7), (28, 34)])
            self.assertIn("|||||||", hunks[0]["content"])
            self.assertIn("start = 'delivery'", hunks[0]["content"])
            self.assertIn("end = 'main'", hunks[1]["content"])

    def test_legacy_prepared_candidate_gets_full_context_without_losing_partial_edits(self):
        from hooks.merge_resolution import outside_diff_digest

        with conflicted_candidate(scenario="multi-hunk") as (workspace, _, candidate, filename, base, delivery):
            manifest = load_manifest(workspace, "feature", "run")
            record = manifest["mergeTrains"]["default:wave-001"]
            record["resolution"] = {"status": "running", "files": [filename], "parents": [base, delivery],
                                    "outsideDiffDigest": outside_diff_digest(candidate, [filename]), "attemptId": "legacy"}
            record["conflictContext"]["attempts"] = 1
            original = (candidate / filename).read_text()
            partial = "start = 'merged'\n" + original.split(">>>>>>> delivery\n", 1)[1]
            (candidate / filename).write_text(partial)
            save_manifest(workspace, "feature", "run", manifest)
            prepared = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="legacy")
            self.assertTrue(prepared["success"], prepared)
            self.assertEqual(prepared["attempts"], 1)
            index = json.loads(Path(prepared["contextPath"]).read_bytes())
            self.assertEqual(len(index["files"][0]["hunks"]), 2)
            self.assertEqual((candidate / filename).read_text(), partial)

    def test_plugin_commits_unstaged_model_edits_and_preserves_auto_merged_files(self):
        with conflicted_candidate(maximum=1) as (workspace, repo, candidate, filename, base, delivery):
            self.assertEqual(prepare(workspace)["status"], "resolution_required")
            # Simulate the model: only edit the file, without staging or committing.
            (candidate / filename).write_text("values = ['main', 'delivery']\n", encoding="utf-8")
            result = complete(workspace)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["resolutionAttempts"], 1)
            self.assertEqual(git(candidate, "show", "-s", "--format=%P", "HEAD").stdout.split(), [base, delivery])
            self.assertEqual(git(candidate, "show", "-s", "--format=%s", "HEAD").stdout.strip(),
                             "Z990692-294 #comment cmbdevcalw提交 解决冲突 B001")
            self.assertEqual((candidate / "clean.txt").read_text(), "auto-merged delivery\n")
            self.assertEqual(git(candidate, "status", "--porcelain").stdout, "")
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), base)
            self.assertNotEqual(result["candidateSha"], base)
            self.assertEqual(complete(workspace)["candidateSha"], result["candidateSha"])
            self.assertTrue(prepare(workspace)["reused"])

    def test_remaining_markers_prevent_commit(self):
        with conflicted_candidate() as (workspace, _, candidate, _, base, _):
            prepare(workspace)
            result = complete(workspace)
            self.assertFalse(result["success"])
            self.assertIn("candidate_conflict_markers_remain", result["error"])
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)

    def test_platform_journals_are_preserved_and_never_committed(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            journal = candidate / ".cmbdevclaw/workflows/workflow/state.json"
            journal.parent.mkdir(parents=True)
            journal.write_text("{}")
            result = complete(workspace)
            self.assertTrue(result["success"], result)
            self.assertTrue(journal.is_file())
            self.assertEqual(git(candidate, "ls-files", ".cmbdevclaw/").stdout, "")
            self.assertTrue(prepare(workspace)["reused"])

    def test_plugin_stages_deletions_and_literal_paths(self):
        with conflicted_candidate("src/service [new].py") as (workspace, _, candidate, filename, base, delivery):
            self.assertTrue(prepare(workspace)["success"])
            (candidate / filename).unlink()
            result = complete(workspace)
            self.assertTrue(result["success"], result)
            self.assertFalse((candidate / filename).exists())
            self.assertEqual(git(candidate, "show", "-s", "--format=%P", "HEAD").stdout.split(), [base, delivery])

    def test_changes_outside_conflict_files_prevent_commit(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, base, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            (candidate / "clean.txt").write_text("unrelated edit\n")
            result = complete(workspace)
            self.assertEqual(result["error"], "candidate_resolution_out_of_scope_changes")
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)

    def test_unrelated_staged_changes_cannot_be_hidden_by_restoring_the_working_copy(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, base, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            clean = candidate / "clean.txt"
            clean.write_text("unrelated staged edit\n")
            git(candidate, "add", "clean.txt")
            clean.write_text("auto-merged delivery\n")
            self.assertEqual(complete(workspace)["error"], "candidate_resolution_out_of_scope_changes")
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)

    def test_candidate_writes_have_their_own_authorization_including_frontend_and_tests(self):
        for filename in ("src/service.py", "src/app.tsx", "src/test/test_service.py", "pom.xml"):
            with self.subTest(filename=filename), conflicted_candidate(filename) as (workspace, _, candidate, _, _, _):
                prepare(workspace)
                payload = json.dumps({"cwd": str(workspace), "tool_input": {"file_path": str(candidate / filename)}})
                with mock.patch.object(write_guard, "read_stdin_text", return_value=payload), mock.patch.object(
                    write_guard, "workspace_from_payload", return_value=workspace
                ), mock.patch.object(write_guard, "current_feature", return_value="feature"), mock.patch.object(
                    write_guard, "current_checkpoint", return_value="code_in_progress"
                ):
                    # No active production TASK or frontend route run is needed.
                    self.assertEqual(write_guard.main(), 0)
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / "other.py"), 2)
                (candidate / filename).write_text("resolved\n")
                self.assertTrue(complete(workspace)["success"])
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / filename), 2)

    def test_git_identity_changes_revoke_write_permission(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            git(candidate, "merge", "--abort")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / filename), 2)
            self.assertEqual(complete(workspace)["error"], "candidate_resolution_commit_mismatch")

    def test_needs_resolution_can_be_prepared_again_with_a_bounded_budget(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            manifest = load_manifest(workspace, "feature", "run")
            manifest["mergeTrains"]["default:wave-001"]["status"] = "needs_resolution"
            save_manifest(workspace, "feature", "run", manifest)
            self.assertEqual(prepare(workspace)["attempts"], 2)
            self.assertEqual(prepare(workspace)["error"], "max_resolution_attempts_exceeded")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / filename), 2)

    def test_repeated_preparation_of_the_same_workflow_step_does_not_consume_attempts(self):
        with conflicted_candidate(maximum=1) as (workspace, _, _, _, _, _):
            first = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="immediate")
            repeated = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="immediate")
            self.assertEqual(first["attempts"], 1)
            self.assertEqual(repeated["attempts"], 1)
            self.assertTrue(repeated["reused"])
            final = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="final")
            self.assertEqual(final["error"], "max_resolution_attempts_exceeded")

    def test_commit_hooks_remain_enabled_and_commit_retry_does_not_need_model_edits(self):
        with conflicted_candidate() as (workspace, repo, candidate, filename, base, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            hook = repo / ".git/hooks/pre-commit"
            hook.write_text("#!/bin/sh\necho 'test rejection' >&2\nexit 1\n")
            hook.chmod(0o755)
            result = complete(workspace)
            self.assertIn("candidate_resolution_commit_failed", result["error"])
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)
            self.assertEqual(prepare(workspace)["status"], "ready_to_complete")
            hook.unlink()
            self.assertTrue(complete(workspace)["success"])

    def test_commit_success_before_manifest_failure_is_recovered_without_another_commit(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            real_save = save_manifest

            def interrupted_save(workspace, feature, run_id, manifest):
                if manifest["mergeTrains"]["default:wave-001"]["status"] == "built":
                    raise OSError("simulated registration interruption")
                real_save(workspace, feature, run_id, manifest)

            with mock.patch("hooks.parallel_merge_train.save_manifest", side_effect=interrupted_save):
                self.assertFalse(complete(workspace)["success"])
            sha = git(candidate, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(prepare(workspace)["status"], "ready_to_complete")
            self.assertEqual(complete(workspace)["candidateSha"], sha)
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), sha)

    def test_cli_handoff_uses_plugin_commands_for_both_operations(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            arguments = ["--workspace", str(workspace), "--feature", "feature", "--run-id", "run", "--repository-ref", "default", "--wave", "1"]
            script = str(ROOT / "hooks/parallel_merge_train.py")
            prepared = subprocess.run([sys.executable, "-X", "utf8", script, "prepare-resolution", *arguments], capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            self.assertEqual(json.loads(prepared.stdout)["status"], "resolution_required")
            packet = json.loads(prepared.stdout)
            query = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "hooks/query_json_fields.py"),
                                    "--file", packet["contextPath"], "--field", "identity.parents",
                                    "--field", "files[0].hunks", "--field", "businessContextPath"],
                                   capture_output=True, text=True)
            self.assertEqual(query.returncode, 0, query.stdout + query.stderr)
            self.assertEqual(len(json.loads(query.stdout)["fields"]["files[0].hunks"]), 1)
            (candidate / filename).write_text("resolved\n")
            committed = subprocess.run([sys.executable, "-X", "utf8", script, "complete-resolution", *arguments], capture_output=True, text=True)
            self.assertEqual(committed.returncode, 0, committed.stdout + committed.stderr)
            self.assertEqual(json.loads(committed.stdout)["status"], "built")


if __name__ == "__main__":
    unittest.main()
