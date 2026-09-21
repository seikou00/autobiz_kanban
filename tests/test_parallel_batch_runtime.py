from __future__ import annotations

import json
import copy
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path

from hooks.batch_merger import _merge_probe, preflight_merge, recover_plan_state_after_merge
from hooks.parallel_batch_lifecycle import cleanup_run, rollback_run
from hooks.parallel_final_verify import verify_final
from hooks.parallel_runtime import (
    acquire_lease,
    check_lease,
    generate_run_id,
    lease_path,
    load_manifest,
    plan_digest,
    reclaim_lease,
    ready_batches,
    release_lease,
    resource_groups,
    save_manifest,
)
from hooks.repository_snapshot import current_git_branch, git_status_porcelain
from hooks.json_writer_common import WriterResult
from hooks.parallel_batch_scheduler import (
    assert_batch_worktree_isolated,
    create_run as _create_run,
    ensure_run,
    manual_resume_run,
    mark_batch,
    resume_run,
    schedule,
    validate_plan_for_parallel,
)
from hooks.plan_json import PlanBundle
from hooks.plan_json import task_set_digest
from hooks.worktree_manager import provision_parallel_worktree, remove_parallel_worktree, seal_parallel_batch
from hooks.task_runner import _assert_parallel_context
from tests.test_task_runner import (
    _add_second_compile_only_batch,
    _configure_defer_to_test_stages,
    _refresh_parallel_pipeline,
    _configure_runtime_ignore,
    _git as task_runner_git,
    _workspace,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def create_run(workspace: Path, feature: str, **kwargs):
    """Create a fixed-workflow scheduler run for runtime tests."""
    kwargs.setdefault("task_card_id", "Z990692-294")
    return _create_run(workspace, feature, **kwargs)


def _create_native_worktree(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Simulate a plugin-provisioned native Git worktree.

    Production uses ``worktree_manager.py provision``. Tests create the same
    Git-registered checkout outside the source repository and record it through
    the scheduler boundary.
    """
    manifest = load_manifest(workspace, feature, run_id)
    batch = manifest["batches"][batch_id]
    repository_ref = batch["repositoryRef"]
    git_root = Path(manifest["repositories"][repository_ref]["gitRoot"])
    if repo_path is not None and repo_path.resolve() != git_root.resolve():
        return {"success": False, "error": f"parallel_repository_binding_mismatch:{repository_ref}"}
    target = workspace.parent / "native-worktrees" / run_id / batch_id
    target.parent.mkdir(parents=True, exist_ok=True)
    branch = f"cmbcowork/{run_id.lower()}/{batch_id.lower()}"
    base_sha = manifest["repositories"][repository_ref]["headSha"]
    created = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(target), base_sha],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        return {"success": False, "error": f"git_worktree_add_failed:{created.stderr.strip()}"}
    mark_batch(
        workspace,
        feature,
        run_id,
        batch_id,
        "running",
        worktreePath=str(target),
        branchName=branch,
    )
    return {"success": True, "worktreePath": str(target.resolve()), "branchName": branch, "error": None}


def _seal_native_worktree(
    workspace: Path,
    feature: str,
    run_id: str,
    batch_id: str,
    repo_path: Path | None,
    owner_token: str,
) -> dict[str, Any]:
    """Seal a plugin-provisioned native delivery worktree."""
    return seal_parallel_batch(workspace, feature, run_id, batch_id, repo_path, owner_token)


class ParallelBatchRuntimeTest(unittest.TestCase):
    def test_generated_run_id_contains_time_and_disambiguates_same_millisecond(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "artifacts"
            fixed_now = datetime(2026, 9, 9, 1, 2, 3, 456_000, tzinfo=timezone.utc)
            with patch("hooks.parallel_runtime.datetime") as clock:
                clock.now.return_value = fixed_now
                first = generate_run_id(workspace, "alpha")
                (workspace / ".autobizdevops" / "features" / "beta" / ".parallel-runs" / first).mkdir(parents=True)
                second = generate_run_id(workspace, "alpha")

            self.assertEqual(first, "cw-20260909-010203-456")
            self.assertEqual(second, "cw-20260909-010203-456-001")
            self.assertRegex(first, re.compile(r"^cw-\d{8}-\d{6}-\d{3}$"))

    def test_current_branch_uses_legacy_git_compatible_plumbing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)

            self.assertEqual(
                current_git_branch(repo),
                _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD"),
            )

    def test_ensure_reuses_existing_scheduler_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            reused = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(reused["reused"])
            self.assertEqual(reused["runId"], created["runId"])
            self.assertEqual(reused["scheduledGroups"], [["B001"]])

    def test_run_requires_task_card_id_only_when_creating_a_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)

            with self.assertRaisesRegex(ValueError, "parallel_task_card_id_required"):
                _create_run(
                    workspace,
                    "alpha",
                    max_parallel=4,
                    timeout_seconds=60,
                    code_workspaces=[str(repo)],
                )

            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            reused = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                task_card_id="Z990692-295",
            )
            self.assertTrue(reused["reused"])
            self.assertEqual(load_manifest(workspace, "alpha", created["runId"])["taskCardId"], "Z990692-294")

    def test_create_run_persists_workspace_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            config_dir = workspace / ".autobiz"
            config_dir.mkdir()
            (config_dir / "runtime_config.json").write_text(
                json.dumps(
                    {
                        "parallelSchedulingMode": "optimistic",
                        "maxParallel": 6,
                        "conflictResolution": {"maxAttempts": 3, "enableAutoResolve": True},
                    }
                ),
                encoding="utf-8",
            )

            created = create_run(
                workspace,
                "alpha",
                max_parallel=6,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            manifest = load_manifest(workspace, "alpha", created["runId"])
            self.assertEqual(
                manifest["runtimeConfig"],
                {
                    "parallelSchedulingMode": "optimistic",
                    "maxParallel": 6,
                    "conflictResolution": {"maxAttempts": 3, "enableAutoResolve": True},
                },
            )
            self.assertEqual(manifest["batches"]["B001"]["deliveryKind"], "single_task")
            self.assertIsNone(manifest["batches"]["B001"]["atomicGroupId"])

    def test_resume_reports_unresolved_merge_train_without_global_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["mergeTrains"] = {
                "default:wave-001": {
                    "repositoryRef": "default",
                    "wave": 1,
                    "batchIds": ["B001"],
                    "status": "needs_resolution",
                },
            }
            save_manifest(workspace, "alpha", created["runId"], manifest)

            resumed = resume_run(workspace, "alpha", created["runId"])

            self.assertEqual(resumed["status"], "running")
            self.assertTrue(resumed["recoveryRequired"])
            self.assertEqual(resumed["unresolvedMergeTrains"], ["default:wave-001"])
            self.assertEqual(resumed["scheduledGroups"], [])

    def test_ensure_scopes_needs_resolution_batch_without_global_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            mark_batch(workspace, "alpha", created["runId"], "B001", "needs_resolution")

            ensured = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertEqual(ensured["runId"], created["runId"])
            self.assertEqual(ensured["status"], "running")
            self.assertTrue(ensured["recoveryRequired"])
            self.assertEqual(ensured["unresolvedBatches"], ["B001"])
            self.assertEqual(ensured["scheduledGroups"], [])

    def test_ensure_blocks_merged_batch_without_a_merge_commit(self) -> None:
        """A legacy/corrupt merged flag must never release dependent work."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["batches"]["B001"].update({"status": "merged", "mergeCommitSha": None})
            save_manifest(workspace, "alpha", created["runId"], manifest)

            ensured = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(ensured["reused"])
            self.assertEqual(ensured["runId"], created["runId"])
            self.assertEqual(ensured["status"], "blocked")
            self.assertEqual(ensured["scheduledGroups"], [])
            self.assertIn("parallel_batch_merge_evidence_required:B001", ensured["errors"])
            self.assertEqual(load_manifest(workspace, "alpha", created["runId"])["batches"]["B001"]["status"], "blocked")

    def test_ensure_blocks_shared_source_head_drift_without_a_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            (repo / "external.txt").write_text("outside run\n", encoding="utf-8")
            task_runner_git(repo, "add", "external.txt")
            task_runner_git(repo, "commit", "-m", "external source change")

            ensured = ensure_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            self.assertTrue(ensured["reused"])
            self.assertEqual(ensured["runId"], created["runId"])
            self.assertEqual(ensured["status"], "blocked")
            self.assertTrue(any(error.startswith("parallel_repository_head_changed:default:") for error in ensured["errors"]))

    def test_worker_facing_mark_batch_cannot_mark_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            with self.assertRaisesRegex(ValueError, "parallel_batch_merge_owner_required:B001"):
                mark_batch(workspace, "alpha", created["runId"], "B001", "merged")

    def test_retry_pending_resumes_without_blocking_independent_batches(self) -> None:
        """A transient Batch failure must not terminate its independent peer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            second_path = feature_dir / "plans" / "B002" / "plan.json"
            second = json.loads(second_path.read_text(encoding="utf-8"))
            second["tasks"][0]["deps"] = []
            second_path.write_text(json.dumps(second), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            next(entry for entry in plan["batches"] if entry["id"] == "B002")["deps"] = []
            root_path.write_text(json.dumps(plan), encoding="utf-8")
            _refresh_parallel_pipeline(feature_dir)
            config_dir = workspace / ".autobiz"
            config_dir.mkdir()
            (config_dir / "runtime_config.json").write_text(
                json.dumps({"parallelSchedulingMode": "optimistic", "maxParallel": 4}),
                encoding="utf-8",
            )

            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            self.assertEqual(created["scheduledGroups"], [["B001", "B002"]])

            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "retry_pending",
                error="transient_worker_failure",
            )
            first_resume = resume_run(workspace, "alpha", run_id)
            first_manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(first_resume["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(first_manifest["batches"]["B001"]["status"], "pending")
            self.assertEqual(first_manifest["batches"]["B001"]["recovery"]["retryAttempts"], 1)
            self.assertEqual(first_manifest["status"], "running")
            self.assertEqual(first_resume["scheduledGroups"], [["B001", "B002"]])

            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "retry_pending",
                error="repeat_worker_failure",
            )
            exhausted = resume_run(workspace, "alpha", run_id)
            exhausted_manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(exhausted["retryExhaustedBatches"], ["B001"])
            self.assertEqual(exhausted_manifest["batches"]["B001"]["status"], "blocked")
            self.assertEqual(exhausted_manifest["batches"]["B002"]["status"], "pending")
            self.assertEqual(exhausted["blockedBatches"], ["B001"])
            self.assertEqual(exhausted["scheduledGroups"], [["B002"]])

            # An operator can explicitly requeue a retry-exhausted Batch after
            # addressing its diagnostics; it is no longer a permanently
            # terminal `failed` record.
            mark_batch(workspace, "alpha", run_id, "B001", "retry_pending", error="operator_retry")
            requeued = resume_run(workspace, "alpha", run_id)
            self.assertEqual(requeued["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "pending")

    def test_retry_pending_transition_clears_lease_before_reschedule(self) -> None:
        """A retry marker must take lease authority away from a failed worker."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            self.assertTrue(check_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"]))

            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "retry_pending",
                error="empty_agent_output",
            )

            marked = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            self.assertEqual(marked["status"], "retry_pending")
            self.assertIsNone(marked["lease"])
            self.assertFalse(lease_path(workspace, "alpha", run_id, "B001").exists())

            resumed = resume_run(workspace, "alpha", run_id)

            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "pending")
            self.assertEqual(resumed["scheduledGroups"], [["B001"]])

    def test_dirty_unsealed_worktree_resumes_implementation_without_provision(self) -> None:
        """Interrupted implementation is recovered in its owned worktree, not discarded."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            worktree = Path(provisioned["worktreePath"])
            (worktree / "interrupted-implementation.txt").write_text("preserve this implementation\n", encoding="utf-8")

            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "retry_pending",
                error="worker_interrupted",
            )
            marked = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            self.assertEqual(marked["recovery"]["kind"], "implementation_resume")
            self.assertTrue(marked["recovery"]["preserveWorktree"])
            self.assertFalse(marked["recovery"]["reprovision"])

            # Existing runs were written before implementation_resume existed.
            # Resume must upgrade their retry_dispatch marker from the live
            # worktree facts, rather than provisioning over the dirty files.
            legacy_manifest = load_manifest(workspace, "alpha", run_id)
            legacy_manifest["batches"]["B001"]["recovery"].update({
                "kind": "retry_dispatch",
                "preserveWorktree": False,
                "reprovision": True,
            })
            save_manifest(workspace, "alpha", run_id, legacy_manifest)

            resumed = resume_run(workspace, "alpha", run_id)
            batch = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            preserved = (worktree / "interrupted-implementation.txt").is_file()

            # A later mismatch must remain protected as an implementation
            # recovery problem; it may not fall through to a new provision.
            invalid_manifest = load_manifest(workspace, "alpha", run_id)
            repository_ref = invalid_manifest["batches"]["B001"]["repositoryRef"]
            invalid_manifest["repositories"][repository_ref]["headSha"] = "0" * 40
            save_manifest(workspace, "alpha", run_id, invalid_manifest)
            guarded = schedule(workspace, "alpha", run_id)

        self.assertEqual(batch["status"], "pending")
        self.assertEqual(batch["recovery"]["kind"], "implementation_resume")
        self.assertTrue(batch["recovery"]["preserveWorktree"])
        self.assertEqual(resumed["scheduledGroups"], [])
        self.assertEqual(resumed["implementationRecoveryBatches"][0]["batchId"], "B001")
        self.assertEqual(resumed["implementationRecoveryBatches"][0]["worktreePath"], provisioned["worktreePath"])
        self.assertEqual(resumed["implementationRecoveryBatches"][0]["recoveryKind"], "implementation_resume")
        self.assertFalse(resumed["implementationRecoveryBatches"][0]["reprovision"])
        self.assertTrue(preserved)
        self.assertEqual(guarded["scheduledGroups"], [])
        self.assertEqual(guarded["implementationRecoveryBatches"], [])
        self.assertEqual(guarded["excludedImplementationRecoveryBatches"], [
            {"batchId": "B001", "reason": "worktree_verification_failed"},
        ])

    def test_manual_resume_resets_retry_exhausted_batch(self) -> None:
        """An explicit user retry is a fresh admission, not a third automatic retry."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]

            mark_batch(workspace, "alpha", run_id, "B001", "retry_pending", error="first_failure")
            resume_run(workspace, "alpha", run_id)
            mark_batch(workspace, "alpha", run_id, "B001", "retry_pending", error="second_failure")
            exhausted = resume_run(workspace, "alpha", run_id)
            self.assertEqual(exhausted["retryExhaustedBatches"], ["B001"])

            resumed = manual_resume_run(workspace, "alpha", run_id)
            batch = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]

            self.assertEqual(resumed["manualRetryBatches"], ["B001"])
            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(batch["status"], "pending")
            self.assertEqual(batch["recovery"]["manualRetryAttempts"], 1)
            self.assertEqual(batch["recovery"]["retryAttempts"], 1)

    def test_resume_repairs_legacy_retry_pending_lease(self) -> None:
        """Resume must repair retry records written before atomic lease cleanup."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            manifest = load_manifest(workspace, "alpha", run_id)
            manifest["batches"]["B001"].update({
                "status": "retry_pending",
                "recovery": {
                    "retryAttempts": 1,
                    "resumeStatus": "pending",
                    "status": "pending_retry",
                },
            })
            save_manifest(workspace, "alpha", run_id, manifest)

            resumed = resume_run(workspace, "alpha", run_id)

            repaired = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            self.assertIsNone(repaired["lease"])
            self.assertFalse(lease_path(workspace, "alpha", run_id, "B001").exists())
            self.assertEqual(repaired["status"], "pending")
            self.assertEqual(resumed["scheduledGroups"], [["B001"]])

    def test_retry_pending_restores_a_merge_candidate(self) -> None:
        """A failed promotion retry must not strand completed stage evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            tree = Path(delivery["worktreePath"])
            (tree / "delivery.txt").write_text("candidate\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", compileStatus="passed")
            sealed = _seal_native_worktree(workspace, "alpha", run_id, "B001", tree, lease["ownerToken"])
            self.assertTrue(sealed["success"], sealed)
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "ready_to_candidate",
                worktreePath=delivery["worktreePath"],
                branchName=delivery["branchName"],
                commitSha=sealed["commitSha"],
            )
            # Simulate the interruption window that previously left a
            # candidate's durable lease metadata behind after its worker had
            # already returned control to the Workflow.
            interrupted = load_manifest(workspace, "alpha", run_id)
            interrupted["batches"]["B001"]["lease"] = {"host": "interrupted-worker"}
            save_manifest(workspace, "alpha", run_id, interrupted)
            lease_path(workspace, "alpha", run_id, "B001").write_text("{}", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B001", "retry_pending", error="promotion_transport_failure")

            resumed = resume_run(workspace, "alpha", run_id)

            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            resumed_batch = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
            self.assertEqual(resumed_batch["status"], "ready_to_candidate")
            self.assertIsNone(resumed_batch["lease"])
            self.assertFalse(lease_path(workspace, "alpha", run_id, "B001").exists())
            self.assertEqual(resumed["mergeableBatches"], ["B001"])

    def test_legacy_failed_batch_can_enter_retry_pending(self) -> None:
        """An old worker's terminal failed marker must no longer strand a run."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            mark_batch(workspace, "alpha", run_id, "B001", "failed", error="legacy_worker_failure")
            mark_batch(workspace, "alpha", run_id, "B001", "retry_pending", error="recover_legacy_failure")

            resumed = resume_run(workspace, "alpha", run_id)

            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["status"], "running")
            self.assertEqual(manifest["batches"]["B001"]["status"], "pending")

    def test_resume_scopes_conflicting_delivery_without_global_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["batches"]["B001"].update({"status": "conflict", "error": "merge_conflict"})
            save_manifest(workspace, "alpha", created["runId"], manifest)

            resumed = resume_run(workspace, "alpha", created["runId"])

            self.assertEqual(resumed["status"], "running")
            self.assertTrue(resumed["recoveryRequired"])
            self.assertEqual(resumed["unresolvedBatches"], ["B001"])
            self.assertEqual(resumed["scheduledGroups"], [])

    def test_final_verify_rejects_merged_batch_without_merge_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["batches"]["B001"].update({"status": "merged", "mergeCommitSha": None})
            manifest["status"] = "succeeded"
            save_manifest(workspace, "alpha", created["runId"], manifest)

            verified = verify_final(workspace, "alpha", created["runId"])
            self.assertFalse(verified["passed"])
            self.assertIn("B001.not_merged", verified["errors"])

    def test_resume_treats_succeeded_with_issues_as_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            manifest = load_manifest(workspace, "alpha", created["runId"])
            manifest["status"] = "succeeded_with_issues"
            save_manifest(workspace, "alpha", created["runId"], manifest)

            resumed = resume_run(workspace, "alpha", created["runId"])

            self.assertEqual(resumed["status"], "succeeded_with_issues")
            self.assertEqual(resumed["skipped"], "terminal_run")

    def test_resume_blocks_when_sealed_native_delivery_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = scheduled["runId"]
            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            tree = Path(delivery["worktreePath"])
            (tree / "delivery.txt").write_text("sealed\n", encoding="utf-8")
            runtime_file = tree / ".cmbdevclaw" / "workflows" / "batch.journal"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text("platform runtime\n", encoding="utf-8")
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", compileStatus="passed")
            sealed = _seal_native_worktree(workspace, "alpha", run_id, "B001", tree, lease["ownerToken"])
            self.assertTrue(sealed["success"], sealed)
            self.assertEqual(
                _git(tree, "log", "-1", "--format=%s", sealed["commitSha"]),
                "Z990692-294 #comment 实现 alpha B001",
            )
            committed_files = _git(tree, "show", "--format=", "--name-only", sealed["commitSha"]).splitlines()
            self.assertIn("delivery.txt", committed_files)
            self.assertNotIn(".cmbdevclaw/workflows/batch.journal", committed_files)
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)

            resumed = resume_run(workspace, "alpha", run_id)

            self.assertEqual(resumed["status"], "blocked")
            self.assertTrue(resumed["recoveryRequired"])
            self.assertIn("native_worktree_delivery_missing:B001:worktree", resumed["errors"])

    def test_unsealed_batch_cannot_be_released_or_report_an_empty_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _feature_dir, repo = _workspace(Path(tmp))
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = scheduled["runId"]
            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                None,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            mark_batch(workspace, "alpha", run_id, "B001", "sealed", compileStatus="passed")

            with self.assertRaisesRegex(ValueError, "parallel_batch_not_sealed:B001"):
                release_lease(
                    workspace,
                    "alpha",
                    run_id,
                    "B001",
                    lease["ownerToken"],
                    final_status="sealed",
                )
            self.assertTrue(check_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"]))

            resumed = resume_run(workspace, "alpha", run_id)
            self.assertEqual(resumed["status"], "blocked")
            self.assertIn("parallel_batch_seal_required:B001", resumed["errors"])

            self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "blocked")

    def test_platform_workflow_runtime_files_do_not_dirty_merge_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _feature_dir, repo = _workspace(Path(tmp))
            runtime_file = repo / ".cmbdevclaw" / "workflows" / "run.journal"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text("initial\n", encoding="utf-8")
            task_runner_git(repo, "add", ".cmbdevclaw/workflows/run.journal")
            task_runner_git(repo, "commit", "-m", "track runtime fixture")
            runtime_file.write_text("changed by platform\n", encoding="utf-8")

            self.assertTrue(preflight_merge(repo)["ok"])
            (repo / "business.txt").write_text("must block\n", encoding="utf-8")
            preflight = preflight_merge(repo)
            self.assertFalse(preflight["ok"])
            self.assertEqual(preflight["error"], "main_worktree_dirty")
            self.assertEqual(len(preflight["changes"]), 1)

    def test_partial_rollback_run_removes_native_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )
            lease = acquire_lease(workspace, "alpha", scheduled["runId"], "B001")
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                scheduled["runId"],
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            delivery_path = Path(delivery["worktreePath"])

            rollback = rollback_run(workspace, "alpha", scheduled["runId"], mode="partial", confirm=True)
            cleanup = cleanup_run(workspace, "alpha", scheduled["runId"])

            self.assertEqual(rollback["status"], "rolled_back")
            self.assertEqual(cleanup["status"], "cleaned")
            self.assertNotIn(str(delivery_path), cleanup["retainedWorktrees"])
            self.assertTrue(any(Path(path).resolve() == delivery_path.resolve() for path in cleanup["removedWorktrees"]))
            self.assertFalse(delivery_path.exists())

    def test_full_rollback_uses_the_run_task_card_id_in_its_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=1,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            (repo / "delivered.txt").write_text("delivery\n", encoding="utf-8")
            task_runner_git(repo, "add", "delivered.txt")
            task_runner_git(repo, "commit", "-m", "delivery fixture")
            delivered_sha = _git(repo, "rev-parse", "HEAD")
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            manifest["batches"]["B001"].update({"status": "merged", "mergeCommitSha": delivered_sha})
            save_manifest(workspace, "alpha", scheduled["runId"], manifest)

            rollback = rollback_run(workspace, "alpha", scheduled["runId"], mode="full", confirm=True)

            self.assertEqual(rollback["status"], "rolled_back")
            self.assertFalse((repo / "delivered.txt").exists())
            self.assertEqual(
                _git(repo, "log", "-1", "--format=%s"),
                f"Z990692-294 #comment 回滚工作流 alpha {delivered_sha}",
            )

    def test_scheduler_does_not_bind_a_run_to_the_workflow_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            neutral = root / "neutral-artifact-directory"
            neutral.mkdir()
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=neutral,
            )
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            self.assertEqual(manifest["isolation"]["mode"], "native_git_worktrees")

    def test_scheduler_rejects_source_checkout_as_batch_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )

            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            with self.assertRaisesRegex(ValueError, "parallel_batch_worktree_not_isolated"):
                assert_batch_worktree_isolated(manifest, "B001", repo)
            with self.assertRaisesRegex(ValueError, "parallel_batch_worktree_not_isolated"):
                mark_batch(
                    workspace,
                    "alpha",
                    scheduled["runId"],
                    "B001",
                    "running",
                    worktreePath=str(repo),
                    branchName="main",
                )

            self.assertEqual(load_manifest(workspace, "alpha", scheduled["runId"])["batches"]["B001"]["status"], "pending")

    def test_scheduler_defers_worktree_provisioning_to_plugin_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=repo,
            )
            run_id = scheduled["runId"]
            provisioned = scheduled["batchWorkspaces"]["B001"]
            self.assertIsNone(provisioned["worktreePath"])
            self.assertIsNone(provisioned["branchName"])
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["status"], "pending")
            self.assertIsNone(manifest["batches"]["B001"]["lease"])

            lease = acquire_lease(workspace, "alpha", run_id, "B001")
            delivery = _create_native_worktree(workspace, "alpha", run_id, "B001", repo, lease["ownerToken"])
            self.assertTrue(delivery["success"], delivery)
            tree = Path(delivery["worktreePath"])
            try:
                manifest = load_manifest(workspace, "alpha", run_id)
                assert_batch_worktree_isolated(manifest, "B001", tree)
                self.assertEqual(load_manifest(workspace, "alpha", run_id)["batches"]["B001"]["status"], "running")
            finally:
                subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)

    def test_plugin_worktree_manager_provisions_reuses_and_removes_native_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=root / "artifact-host",
            )
            run_id = scheduled["runId"]

            first = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(first["success"], first)
            worktree = Path(first["worktreePath"])
            self.assertTrue(worktree.is_dir())
            self.assertEqual(_git(worktree, "rev-parse", "--show-toplevel"), str(worktree))
            self.assertEqual(first["repositoryRef"], "default")
            manifest = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(manifest["batches"]["B001"]["worktreeOwner"], "plugin")

            reused = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(reused["success"], reused)
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["worktreePath"], first["worktreePath"])

            removed = remove_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(removed["success"], removed)
            self.assertTrue(removed["removed"])
            self.assertFalse(worktree.exists())
            self.assertIsNotNone(load_manifest(workspace, "alpha", run_id)["batches"]["B001"].get("worktreeRemovedAt"))

    def test_plugin_worktree_manager_reconciles_worktree_created_before_manifest_save(self) -> None:
        """A retry adopts only the exact native worktree left by an interrupted provision."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=root / "artifact-host",
            )
            run_id = scheduled["runId"]
            first = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(first["success"], first)
            worktree = Path(first["worktreePath"])
            self.assertEqual(
                worktree,
                (workspace / ".autobizdevops" / "worktrees" / run_id / "B001").resolve(),
            )
            try:
                # Simulate termination after `git worktree add` and before
                # `save_manifest`: Git has the checkout but the run record
                # has no binding to reuse.
                manifest = load_manifest(workspace, "alpha", run_id)
                batch = manifest["batches"]["B001"]
                batch["worktreePath"] = None
                batch["branchName"] = None
                batch.pop("worktreeOwner", None)
                save_manifest(workspace, "alpha", run_id, manifest)

                reconciled = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
                self.assertTrue(reconciled["success"], reconciled)
                self.assertTrue(reconciled["reused"])
                self.assertEqual(reconciled["worktreePath"], str(worktree))
                restored = load_manifest(workspace, "alpha", run_id)["batches"]["B001"]
                self.assertEqual(restored["worktreePath"], str(worktree))
                self.assertEqual(restored["branchName"], reconciled["branchName"])
            finally:
                removed = remove_parallel_worktree(workspace, "alpha", run_id, "B001")
                self.assertTrue(removed["success"], removed)

    def test_plugin_worktree_manager_rebuilds_interrupted_checkout_before_reconcile(self) -> None:
        """An empty index plus Git's initializing locks is not a reusable Worktree."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=root / "artifact-host",
            )
            run_id = scheduled["runId"]
            first = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(first["success"], first)
            worktree = Path(first["worktreePath"])
            try:
                # Simulate a host timeout after Git registered the linked
                # worktree but before checkout finished.  Git then reports
                # every HEAD file as a staged deletion unless provision
                # discards and recreates this plugin-owned remnant.
                git_dir_raw = _git(worktree, "rev-parse", "--git-dir")
                git_dir = Path(git_dir_raw)
                if not git_dir.is_absolute():
                    git_dir = (worktree / git_dir).resolve()
                (git_dir / "index").unlink()
                (git_dir / "index.lock").touch()
                (git_dir / "locked").write_text("initializing\n", encoding="utf-8")

                manifest = load_manifest(workspace, "alpha", run_id)
                batch = manifest["batches"]["B001"]
                batch["worktreePath"] = None
                batch["branchName"] = None
                batch.pop("worktreeOwner", None)
                save_manifest(workspace, "alpha", run_id, manifest)

                rebuilt = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
                self.assertTrue(rebuilt["success"], rebuilt)
                self.assertFalse(rebuilt["reused"])
                self.assertTrue(rebuilt["recoveredIncomplete"])
                self.assertEqual(
                    _git(worktree, "rev-parse", "HEAD"),
                    load_manifest(workspace, "alpha", run_id)["repositories"]["default"]["headSha"],
                )
                self.assertTrue(_git(worktree, "ls-files"))
                self.assertFalse((git_dir / "index.lock").exists())
                self.assertFalse((git_dir / "locked").exists())
            finally:
                removed = remove_parallel_worktree(workspace, "alpha", run_id, "B001", force=True)
                self.assertTrue(removed["success"], removed)

    def test_plugin_worktree_manager_rebuilds_corrupt_reused_worktree_without_lease(self) -> None:
        """A retry must not loop by reusing a bound but incomplete checkout."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=root / "artifact-host",
            )
            run_id = scheduled["runId"]
            first = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(first["success"], first)
            worktree = Path(first["worktreePath"])
            try:
                git_dir_raw = _git(worktree, "rev-parse", "--git-dir")
                git_dir = Path(git_dir_raw)
                if not git_dir.is_absolute():
                    git_dir = (worktree / git_dir).resolve()
                (git_dir / "index").unlink()
                (git_dir / "index.lock").touch()
                (git_dir / "locked").write_text("initializing\n", encoding="utf-8")

                rebuilt = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
                self.assertTrue(rebuilt["success"], rebuilt)
                self.assertFalse(rebuilt["reused"])
                self.assertTrue(rebuilt["recoveredIncomplete"])
                self.assertTrue(_git(worktree, "ls-files"))
            finally:
                removed = remove_parallel_worktree(workspace, "alpha", run_id, "B001", force=True)
                self.assertTrue(removed["success"], removed)

    def test_plugin_worktree_manager_reclaims_unbound_branch_left_by_interrupted_provision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                workflow_workspace=root / "artifact-host",
            )
            run_id = scheduled["runId"]
            manifest = load_manifest(workspace, "alpha", run_id)
            head = manifest["repositories"]["default"]["headSha"]
            branch = f"autodev/alpha/{run_id}/B001"
            subprocess.run(["git", "branch", branch, head], cwd=repo, check=True)

            provisioned = provision_parallel_worktree(workspace, "alpha", run_id, "B001")
            self.assertTrue(provisioned["success"], provisioned)
            self.assertFalse(provisioned["reused"])
            self.assertEqual(provisioned["branchName"], branch)
            try:
                self.assertEqual(_git(repo, "rev-parse", branch), head)
                self.assertTrue(Path(provisioned["worktreePath"]).is_dir())
            finally:
                removed = remove_parallel_worktree(workspace, "alpha", run_id, "B001")
                self.assertTrue(removed["success"], removed)


    def test_scheduler_rejects_dirty_repository_without_explicit_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / "existing.txt").write_text("changed before Code\n", encoding="utf-8")
            (repo / "uncommitted.txt").write_text("preserve this baseline\n", encoding="utf-8")
            runtime = repo / ".cmbdevclaw" / "setup-state.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("platform runtime\n", encoding="utf-8")
            task_runner_git(repo, "add", str(runtime.relative_to(repo)))

            before_head = _git(repo, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "parallel_code_workspace_bootstrap_required:dirty_worktree"):
                create_run(
                    workspace,
                    "alpha",
                    max_parallel=4,
                    timeout_seconds=60,
                    code_workspaces=[str(repo)],
                )

            self.assertEqual(_git(repo, "rev-parse", "HEAD"), before_head)
            self.assertIn("existing.txt", _git(repo, "status", "--porcelain"))
            self.assertFalse((feature_dir / ".parallel-runs").exists())

    def test_scheduler_bootstraps_dirty_repository_only_when_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            (repo / "existing.txt").write_text("changed before Code\n", encoding="utf-8")
            (repo / "uncommitted.txt").write_text("preserve this baseline\n", encoding="utf-8")
            runtime = repo / ".cmbdevclaw" / "setup-state.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("platform runtime\n", encoding="utf-8")
            task_runner_git(repo, "add", str(runtime.relative_to(repo)))

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                allow_bootstrap=True,
            )

            self.assertEqual(git_status_porcelain(repo).stdout, "")
            self.assertIn("?? .cmbdevclaw/", _git(repo, "status", "--porcelain"))
            self.assertEqual(
                _git(repo, "show", "-s", "--format=%s", "HEAD"),
                "Z990692-294 #comment 初始化 alpha 工作流基线",
            )
            self.assertNotIn(".cmbdevclaw/setup-state.json", _git(repo, "show", "--format=", "--name-only", "HEAD"))
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            bootstrap = manifest["repositories"]["default"]["bootstrap"]
            self.assertTrue(bootstrap["performed"])
            self.assertFalse(bootstrap["initialized"])
            self.assertEqual(bootstrap["reason"], "dirty_worktree")
            self.assertIn(
                ".autobizdevops/features/*/.parallel-runs/",
                manifest["repositories"]["default"]["runtimeIgnoreAdditions"],
            )
            self.assertIn(
                ".autobizdevops/features/*/.parallel-runs/",
                (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
            )

            lease = acquire_lease(workspace, "alpha", scheduled["runId"], "B001")
            worktree = _create_native_worktree(
                workspace,
                "alpha",
                scheduled["runId"],
                "B001",
                None,
                lease["ownerToken"],
            )
            self.assertTrue(worktree["success"], worktree)
            self.assertEqual(
                (Path(worktree["worktreePath"]) / "uncommitted.txt").read_text(encoding="utf-8"),
                "preserve this baseline\n",
            )

    def test_scheduler_ignores_platform_runtime_when_source_is_otherwise_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            runtime = repo / ".cmbdevclaw" / "workflows" / "thread" / "journal"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("platform runtime\n", encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )

            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            additions = manifest["repositories"]["default"]["runtimeIgnoreAdditions"]
            self.assertNotIn(".cmbdevclaw/workflows/", additions)
            self.assertEqual(manifest["repositories"]["default"]["bootstrap"]["reason"], None)

    def test_scheduler_initializes_unborn_repository_and_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, _committed_repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            repo = root / "uninitialized-code"
            repo.mkdir()
            (repo / "src.txt").write_text("initial source\n", encoding="utf-8")

            scheduled = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
                allow_bootstrap=True,
            )

            self.assertTrue((repo / ".git").is_dir())
            self.assertTrue(_git(repo, "rev-parse", "--verify", "HEAD"))
            self.assertEqual(_git(repo, "status", "--porcelain"), "")
            manifest = load_manifest(workspace, "alpha", scheduled["runId"])
            bootstrap = manifest["repositories"]["default"]["bootstrap"]
            self.assertTrue(bootstrap["performed"])
            self.assertTrue(bootstrap["initialized"])
            self.assertEqual(bootstrap["reason"], "unborn_head")

            lease = acquire_lease(workspace, "alpha", scheduled["runId"], "B001")
            worktree = _create_native_worktree(
                workspace,
                "alpha",
                scheduled["runId"],
                "B001",
                None,
                lease["ownerToken"],
            )
            self.assertTrue(worktree["success"], worktree)
            self.assertEqual(
                (Path(worktree["worktreePath"]) / "src.txt").read_text(encoding="utf-8"),
                "initial source\n",
            )

    def test_plan_digest_ignores_execution_updates_but_detects_contract_drift(self) -> None:
        root = {"status": "todo", "taskSetDigest": "before", "batches": [{"id": "B001", "status": "todo", "completedTaskCount": 0}]}
        batches = {"B001": {"tasks": [{"id": "T001", "status": "todo", "goal": "original"}]}}
        bundle = PlanBundle(root=root, batches=batches, tasks=batches["B001"]["tasks"], task_batches={"T001": "B001"})
        digest = plan_digest(bundle)
        updated = copy.deepcopy(bundle)
        updated.root.update({"status": "in_progress", "taskSetDigest": "after"})
        updated.root["batches"][0].update({"status": "done", "completedTaskCount": 1})
        updated.batches["B001"]["tasks"][0]["status"] = "done"
        self.assertEqual(digest, plan_digest(updated))
        updated.batches["B001"]["tasks"][0]["goal"] = "changed"
        self.assertNotEqual(digest, plan_digest(updated))

    def test_resource_groups_isolate_overlapping_write_sets(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "workspaceRef": "api", "executionLane": "backend", "writeSet": ["a.py"], "dependencies": []},
                "B002": {"status": "pending", "workspaceRef": "api", "executionLane": "backend", "writeSet": ["a.py"], "dependencies": []},
                "B003": {"status": "pending", "workspaceRef": "worker", "executionLane": "backend", "writeSet": ["c.py"], "dependencies": []},
                "B004": {"status": "pending", "workspaceRef": "cli", "executionLane": "backend", "writeSet": ["d.py"], "dependencies": []},
            }
        }
        groups = resource_groups(manifest, ["B001", "B002", "B003", "B004"])
        self.assertEqual(groups, [["B001", "B003", "B004"], ["B002"]])

    def test_resource_groups_treats_repository_root_write_set_as_conflicting(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "repositoryRef": "api", "writeSet": ["."], "dependencies": []},
                "B002": {"status": "pending", "repositoryRef": "api", "writeSet": ["src/api.py"], "dependencies": []},
            }
        }
        self.assertEqual(resource_groups(manifest, ["B001", "B002"]), [["B001"], ["B002"]])

    def test_merge_probe_does_not_write_tree_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task_runner_git(repo, "init", "-b", "main")
            task_runner_git(repo, "config", "user.email", "test@example.com")
            task_runner_git(repo, "config", "user.name", "Test")
            (repo / "shared.txt").write_text("base\n", encoding="utf-8")
            task_runner_git(repo, "add", "shared.txt")
            task_runner_git(repo, "commit", "-m", "base")
            task_runner_git(repo, "checkout", "-b", "feature")
            (repo / "shared.txt").write_text("feature\n", encoding="utf-8")
            task_runner_git(repo, "commit", "-am", "feature")
            source = _git(repo, "rev-parse", "HEAD")
            task_runner_git(repo, "checkout", "main")
            (repo / "shared.txt").write_text("main\n", encoding="utf-8")
            task_runner_git(repo, "commit", "-am", "main")
            target = _git(repo, "rev-parse", "HEAD")
            before = set(_git(repo, "count-objects", "-v").splitlines())
            probe = _merge_probe(repo, target, "feature")
            after = set(_git(repo, "count-objects", "-v").splitlines())
            self.assertFalse(probe["success"])
            self.assertTrue(probe["conflicts"])
            self.assertEqual(before, after)
            self.assertNotEqual(source, target)

    def test_plan_state_recovery_marks_git_delivered_batch_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(workspace / ".autobizdevops" / "features" / "alpha")
            scheduled = create_run(workspace, "alpha", max_parallel=1, timeout_seconds=60, code_workspaces=[str(repo)])
            run_id = scheduled["runId"]
            (repo / "merged.txt").write_text("merged\n", encoding="utf-8")
            task_runner_git(repo, "add", "merged.txt")
            task_runner_git(repo, "commit", "-m", "merged delivery")
            commit_sha = _git(repo, "rev-parse", "HEAD")
            manifest = load_manifest(workspace, "alpha", run_id)
            manifest["batches"]["B001"].update(
                {
                    "status": "needs_resolution",
                    "mergeCommitSha": commit_sha,
                    "resolution": {
                        "kind": "plan_state_update",
                        "mergeCommitSha": commit_sha,
                        "deliveryRunId": run_id,
                    },
                }
            )
            manifest["repositories"]["default"]["headSha"] = commit_sha
            save_manifest(workspace, "alpha", run_id, manifest)
            with patch(
                "hooks.batch_merger.mark_parallel_batch_tasks_merged",
                return_value=WriterResult(ok=True, changed=False, errors=[]),
            ):
                recovered = recover_plan_state_after_merge(workspace, "alpha", run_id, "B001")
            self.assertTrue(recovered["success"], recovered)
            persisted = load_manifest(workspace, "alpha", run_id)
            self.assertEqual(persisted["batches"]["B001"]["status"], "merged")
            self.assertEqual(persisted["batches"]["B001"]["mergeCommitSha"], commit_sha)
            self.assertNotIn("resolution", persisted["batches"]["B001"])

    def test_special_execution_stages_are_serialized_before_parallel_work(self) -> None:
        manifest = {
            "batches": {
                "B001": {"status": "pending", "repositoryRef": "api", "executionStage": "parallel", "writeSet": ["api.py"]},
                "B002": {"status": "pending", "repositoryRef": "api", "executionStage": "proto", "writeSet": ["schema.proto"]},
                "B003": {"status": "pending", "repositoryRef": "api", "executionStage": "global", "writeSet": ["application.yml"]},
            }
        }
        self.assertEqual(resource_groups(manifest, ["B001", "B002", "B003"]), [["B002"]])
        manifest["batches"]["B002"]["status"] = "merged"
        self.assertEqual(resource_groups(manifest, ["B001", "B003"]), [["B003"]])

    def test_multi_repository_run_binds_each_workspace_and_schedules_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, api = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            web = root / "web"
            web.mkdir()
            task_runner_git(web, "init", "-b", "main")
            task_runner_git(web, "config", "user.email", "test@example.com")
            task_runner_git(web, "config", "user.name", "Test")
            _configure_runtime_ignore(web)
            (web / "site.txt").write_text("base\n", encoding="utf-8")
            task_runner_git(web, "add", ".")
            task_runner_git(web, "commit", "-m", "initial")

            b1_path = feature_dir / "plans" / "B001" / "plan.json"
            b2_path = feature_dir / "plans" / "B002" / "plan.json"
            b1 = json.loads(b1_path.read_text(encoding="utf-8"))
            b2 = json.loads(b2_path.read_text(encoding="utf-8"))
            b2["tasks"][0].update({"workspaceRef": "web", "deps": []})
            b2["tasks"][0]["scope"]["workspaceRoots"] = {"web": "."}
            b2_path.write_text(json.dumps(b2), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"][1].update({"workspaceRef": "web", "deps": []})
            plan["taskSetDigest"] = task_set_digest(plan, {"B001": b1, "B002": b2})
            root_path.write_text(json.dumps(plan), encoding="utf-8")
            _refresh_parallel_pipeline(feature_dir)

            verdict = validate_plan_for_parallel(workspace, "alpha")
            self.assertTrue(verdict["canParallel"], verdict)
            self.assertEqual(verdict["workspaceRefs"], ["default", "web"])
            with self.assertRaisesRegex(ValueError, "parallel_code_workspace_missing:web"):
                create_run(workspace, "alpha", max_parallel=4, timeout_seconds=60, code_workspaces=[f"default={api}"])
            run = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[f"default={api}", f"web={web}"],
            )
            self.assertEqual(run["scheduledGroups"], [["B001", "B002"]])
            bindings = run["batchWorkspaces"]
            self.assertEqual(bindings["B001"]["requestedPath"], str(api.resolve()))
            self.assertEqual(bindings["B002"]["requestedPath"], str(web.resolve()))

            api_wave = schedule(workspace, "alpha", run["runId"], workspace_refs=["default"])
            web_wave = schedule(workspace, "alpha", run["runId"], workspace_refs=["web"])
            self.assertEqual(api_wave["scheduledGroups"], [["B001"]])
            self.assertEqual(web_wave["scheduledGroups"], [["B002"]])
            self.assertEqual(api_wave["workspaceRefs"], ["default"])
            self.assertEqual(web_wave["workspaceRefs"], ["web"])
            self.assertEqual(api_wave["allParallelGroups"], [["B001", "B002"]])

            waiting_manifest = load_manifest(workspace, "alpha", run["runId"])
            waiting_manifest["batches"]["B001"]["dependencies"] = ["B002"]
            waiting_manifest["batches"]["B002"]["status"] = "running"
            save_manifest(workspace, "alpha", run["runId"], waiting_manifest)
            waiting = schedule(workspace, "alpha", run["runId"], workspace_refs=["default"])
            self.assertTrue(waiting["waitingForRepositories"])
            self.assertEqual(waiting["scheduledGroups"], [])


    def test_expired_lease_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000000-test"
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            manifest = {
                "runId": run,
                "batches": {"B001": {"status": "pending", "lease": None}},
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            lease = acquire_lease(workspace, feature, run, "B001", ttl_seconds=1)
            self.assertTrue(lease["ownerToken"])
            persisted = load_manifest(workspace, feature, run)["batches"]["B001"]["lease"]
            self.assertNotIn("ownerToken", persisted)
            time.sleep(1.05)
            self.assertTrue(reclaim_lease(workspace, feature, run, "B001"))

    def test_lease_guard_does_not_require_a_background_child_process(self) -> None:
        """A completed acquire command remains valid without a shell child."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000002-managed"
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({"features": {feature: {"checkpoint": "code_in_progress"}}}), encoding="utf-8")
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"runId": run, "batches": {"B001": {"status": "pending", "lease": None}}}),
                encoding="utf-8",
            )
            manager = Path(__file__).resolve().parents[1] / "hooks" / "batch_lease_manager.py"
            legacy = subprocess.run(
                [sys.executable, str(manager), "heartbeat"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(legacy.returncode, 2)
            self.assertIn("invalid choice: 'heartbeat'", legacy.stderr)
            acquired = subprocess.run(
                [
                    sys.executable,
                    str(manager),
                    "acquire",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    feature,
                    "--run-id",
                    run,
                    "--batch-id",
                    "B001",
                    "--ttl-seconds",
                    "3",
                    "--lease-guard",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            payload = json.loads(acquired.stdout)
            token = payload["lease"]["ownerToken"]
            self.assertEqual(payload["leaseGuard"]["mode"], "command_boundary_renewal")

            try:
                first_expiry = json.loads(lease_path(workspace, feature, run, "B001").read_text(encoding="utf-8"))["expiresEpoch"]
                time.sleep(1.1)
                checked = subprocess.run(
                    [
                        sys.executable,
                        str(manager),
                        "check",
                        "--workspace",
                        str(workspace),
                        "--feature",
                        feature,
                        "--run-id",
                        run,
                        "--batch-id",
                        "B001",
                        "--owner-token",
                        token,
                        "--require-lease-guard",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(checked.returncode, 0, checked.stderr)
                checked_payload = json.loads(checked.stdout)
                self.assertTrue(checked_payload["valid"])
                self.assertEqual(checked_payload["leaseGuard"]["mode"], "command_boundary_renewal")
                _assert_parallel_context(workspace, feature, run, "B001", token)
                renewed_expiry = json.loads(lease_path(workspace, feature, run, "B001").read_text(encoding="utf-8"))["expiresEpoch"]
                self.assertGreater(renewed_expiry, first_expiry)
            finally:
                released = subprocess.run(
                    [
                        sys.executable,
                        str(manager),
                        "release",
                        "--workspace",
                        str(workspace),
                        "--feature",
                        feature,
                        "--run-id",
                        run,
                        "--batch-id",
                        "B001",
                        "--owner-token",
                        token,
                        "--final-status",
                        "pending",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(released.returncode, 0, released.stderr)

            self.assertEqual(load_manifest(workspace, feature, run)["batches"]["B001"]["status"], "pending")

    def test_force_reclaim_cli_does_not_require_worker_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000003-test"
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({"features": {feature: {"checkpoint": "code_in_progress"}}}), encoding="utf-8")
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"runId": run, "batches": {"B001": {"status": "leased", "lease": {}}}}),
                encoding="utf-8",
            )
            acquire_lease(workspace, feature, run, "B001", ttl_seconds=60)
            manager = Path(__file__).resolve().parents[1] / "hooks" / "batch_lease_manager.py"
            result = subprocess.run(
                [
                    sys.executable,
                    str(manager),
                    "reclaim",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    feature,
                    "--run-id",
                    run,
                    "--batch-id",
                    "B001",
                    "--force",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["reclaimed"])
            self.assertEqual(load_manifest(workspace, feature, run)["batches"]["B001"]["status"], "pending")

    def test_lease_rejects_dependency_without_merge_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature = "alpha"
            run = "cw-20260819-000001-test"
            run_dir = workspace / ".autobizdevops" / "features" / feature / ".parallel-runs" / run
            (run_dir / "leases").mkdir(parents=True)
            manifest = {
                "runId": run,
                "batches": {
                    "B001": {"status": "merged", "mergeCommitSha": None, "lease": None},
                    "B002": {"status": "pending", "dependencies": ["B001"], "lease": None},
                },
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(ready_batches(manifest), [])
            with self.assertRaisesRegex(ValueError, "parallel_batch_dependency_incomplete:B002:B001"):
                acquire_lease(workspace, feature, run, "B002")

    def test_expired_running_lease_recovers_without_blocking_independent_peer(self) -> None:
        """Scheduler recovery must not depend on a failed worker writing retry state."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            second_path = feature_dir / "plans" / "B002" / "plan.json"
            second = json.loads(second_path.read_text(encoding="utf-8"))
            second["tasks"][0]["deps"] = []
            second_path.write_text(json.dumps(second), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            next(entry for entry in plan["batches"] if entry["id"] == "B002")["deps"] = []
            root_path.write_text(json.dumps(plan), encoding="utf-8")
            _refresh_parallel_pipeline(feature_dir)
            config_dir = workspace / ".autobiz"
            config_dir.mkdir()
            (config_dir / "runtime_config.json").write_text(
                json.dumps({"parallelSchedulingMode": "optimistic", "maxParallel": 4}),
                encoding="utf-8",
            )

            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            self.assertEqual(created["scheduledGroups"], [["B001", "B002"]])
            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            worktree = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(worktree["success"], worktree)

            # Simulate a model/process failure after it marked the Batch
            # running, before it could call the Workflow's retry cleanup.
            stale_lease_path = lease_path(workspace, "alpha", run_id, "B001")
            stale_lease = json.loads(stale_lease_path.read_text(encoding="utf-8"))
            stale_lease["expiresEpoch"] = 0
            stale_lease_path.write_text(json.dumps(stale_lease), encoding="utf-8")

            resumed = resume_run(workspace, "alpha", run_id)
            manifest = load_manifest(workspace, "alpha", run_id)

            self.assertEqual(resumed["status"], "running")
            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(resumed["scheduledGroups"], [["B001", "B002"]])
            self.assertEqual(resumed["activeWorkers"], 0)
            self.assertEqual(
                resumed["reclaimedStaleBatches"],
                [{"batchId": "B001", "reason": "lease_expired"}],
            )
            self.assertEqual(manifest["batches"]["B001"]["status"], "pending")
            self.assertEqual(manifest["batches"]["B001"]["recovery"]["retryAttempts"], 1)
            self.assertIsNone(manifest["batches"]["B001"]["lease"])
            self.assertFalse(stale_lease_path.exists())

    def test_resume_reconciles_retry_manifest_lease_when_lease_file_is_missing(self) -> None:
        """A crash after lease unlink must be repaired from durable retry state."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            manifest = load_manifest(workspace, "alpha", run_id)
            manifest["batches"]["B001"].update(
                {
                    "status": "retry_pending",
                    "lease": {"batchId": "B001", "expiresAt": "2099-01-01T00:00:00Z"},
                }
            )
            save_manifest(workspace, "alpha", run_id, manifest)

            resumed = resume_run(workspace, "alpha", run_id)
            persisted = load_manifest(workspace, "alpha", run_id)

            self.assertEqual(resumed["status"], "running")
            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(resumed["scheduledGroups"], [["B001"]])
            self.assertEqual(persisted["batches"]["B001"]["status"], "pending")
            self.assertIsNone(persisted["batches"]["B001"]["lease"])
            self.assertFalse(lease_path(workspace, "alpha", run_id, "B001").exists())

    def test_expired_ready_candidate_lease_reenters_recovery_before_merge(self) -> None:
        """A stale handoff lease cannot leave a candidate outside recovery."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            manifest = load_manifest(workspace, "alpha", run_id)
            manifest["batches"]["B001"].update(
                {
                    "status": "ready_to_candidate",
                    "commitSha": "draft-sha",
                    "lease": {"batchId": "B001", "expiresAt": "2099-01-01T00:00:00Z"},
                }
            )
            save_manifest(workspace, "alpha", run_id, manifest)
            residual_lease = lease_path(workspace, "alpha", run_id, "B001")
            residual_lease.parent.mkdir(parents=True, exist_ok=True)
            residual_lease.write_text(json.dumps({"expiresEpoch": 0}), encoding="utf-8")

            resumed = resume_run(workspace, "alpha", run_id)
            persisted = load_manifest(workspace, "alpha", run_id)

            self.assertEqual(
                resumed["reclaimedStaleBatches"],
                [{"batchId": "B001", "reason": "lease_expired"}],
            )
            self.assertEqual(resumed["rescheduledRetryBatches"], ["B001"])
            self.assertEqual(resumed["mergeableBatches"], ["B001"])
            self.assertEqual(persisted["batches"]["B001"]["status"], "ready_to_candidate")
            self.assertEqual(persisted["batches"]["B001"]["recovery"]["retryAttempts"], 1)
            self.assertIsNone(persisted["batches"]["B001"]["lease"])
            self.assertFalse(residual_lease.exists())

    def test_unresolved_merge_train_isolates_independent_peer_and_dependent(self) -> None:
        """A conflicted candidate must not freeze unrelated work or unlock its dependent."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, repo = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)  # B002 remains dependent on B001.

            second_path = feature_dir / "plans" / "B002" / "plan.json"
            second = json.loads(second_path.read_text(encoding="utf-8"))
            third = copy.deepcopy(second)
            third["batchId"] = "B003"
            third["title"] = "independent peer"
            third["taskIds"] = ["T003"]
            third_task = third["tasks"][0]
            third_task.update(
                {
                    "id": "T003",
                    "title": "deliver independent behavior",
                    "deps": [],
                    "specRefs": ["specs/independent/spec.md#REQ-003", "specs/independent/spec.md#SCN-003"],
                    "acceptanceCriteria": [
                        {
                            "id": "AC-T003-01",
                            "text": "independent behavior is observable",
                            "scenarioRefs": ["specs/independent/spec.md#SCN-003"],
                        }
                    ],
                }
            )
            third_path = feature_dir / "plans" / "B003" / "plan.json"
            third_path.parent.mkdir(parents=True)
            third_path.write_text(json.dumps(third), encoding="utf-8")
            root_path = feature_dir / "plan.json"
            plan = json.loads(root_path.read_text(encoding="utf-8"))
            plan["batches"].append(
                {
                    "id": "B003",
                    "path": "plans/B003/plan.json",
                    "title": "independent peer",
                    "specRoots": ["specs/independent/spec.md"],
                    "executionLane": "backend",
                    "deps": [],
                    "taskIds": ["T003"],
                    "deliveryKind": "single_task",
                    "status": "todo",
                }
            )
            root_path.write_text(json.dumps(plan), encoding="utf-8")
            _refresh_parallel_pipeline(feature_dir)
            config_dir = workspace / ".autobiz"
            config_dir.mkdir()
            (config_dir / "runtime_config.json").write_text(
                json.dumps({"parallelSchedulingMode": "optimistic", "maxParallel": 4}),
                encoding="utf-8",
            )

            created = create_run(
                workspace,
                "alpha",
                max_parallel=4,
                timeout_seconds=60,
                code_workspaces=[str(repo)],
            )
            run_id = created["runId"]
            self.assertEqual(created["scheduledGroups"], [["B001", "B003"]])

            lease = acquire_lease(workspace, "alpha", run_id, "B001", ttl_seconds=60)
            delivery = _create_native_worktree(
                workspace,
                "alpha",
                run_id,
                "B001",
                repo,
                lease["ownerToken"],
            )
            self.assertTrue(delivery["success"], delivery)
            worktree = Path(delivery["worktreePath"])
            (worktree / "conflicted-delivery.txt").write_text("delivery\n", encoding="utf-8")
            task_runner_git(worktree, "add", "conflicted-delivery.txt")
            task_runner_git(worktree, "commit", "-m", "delivery")
            commit_sha = _git(worktree, "rev-parse", "HEAD")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "sealed",
                worktreePath=delivery["worktreePath"],
                branchName=delivery["branchName"],
                commitSha=commit_sha,
                compileStatus="passed",
            )
            release_lease(workspace, "alpha", run_id, "B001", lease["ownerToken"], final_status="sealed")
            mark_batch(
                workspace,
                "alpha",
                run_id,
                "B001",
                "ready_to_candidate",
                worktreePath=delivery["worktreePath"],
                branchName=delivery["branchName"],
                commitSha=commit_sha,
            )
            manifest = load_manifest(workspace, "alpha", run_id)
            manifest["mergeTrains"] = {
                "default:wave-001": {
                    "repositoryRef": "default",
                    "wave": 1,
                    "batchIds": ["B001"],
                    "status": "candidate_conflicted",
                    "worktreePath": str(worktree),
                    "branchName": "candidate-conflicted",
                    "error": "parallel_merge_train_conflict:B001",
                }
            }
            save_manifest(workspace, "alpha", run_id, manifest)

            resumed = resume_run(workspace, "alpha", run_id)
            persisted = load_manifest(workspace, "alpha", run_id)

            self.assertEqual(resumed["status"], "running")
            self.assertTrue(resumed["recoveryRequired"])
            self.assertEqual(resumed["unresolvedMergeTrains"], ["default:wave-001"])
            self.assertEqual(resumed["scheduledGroups"], [["B003"]])
            self.assertNotIn("B001", resumed["mergeableBatches"])
            self.assertEqual(persisted["batches"]["B001"]["status"], "ready_to_candidate")
            self.assertEqual(persisted["batches"]["B002"]["status"], "pending")
            self.assertNotIn("B002", [batch_id for group in resumed["scheduledGroups"] for batch_id in group])

if __name__ == "__main__":
    unittest.main()
