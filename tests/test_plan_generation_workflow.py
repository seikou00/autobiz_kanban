from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

from hooks.plan_generation_state import PlanGenerationError, PlanGenerationStore  # noqa: E402
from hooks.design_contract_lock import sync_design_contract_lock  # noqa: E402
from hooks.plan_generation_launcher import (  # noqa: E402
    _cmd_commit_details,
    _existing_collecting_draft_matches,
)
from hooks.plan_writer import _task_group_digest  # noqa: E402
from hooks.plan_workflow_launcher import build_workflow_request  # noqa: E402


def _workspace(root: Path) -> tuple[Path, Path, Path]:
    workspace = root / "artifacts"
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    feature_dir.mkdir(parents=True)
    (workspace / ".autobizdevops" / "state.json").write_text(
        json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {"alpha": {"checkpoint": "design_done"}}}),
        encoding="utf-8",
    )
    (feature_dir / "design.md").write_text(
        "# Design\n"
        "- x-auto-no-http-api: true\n"
        "- x-auto-no-sql: true\n"
        "| ID | Decision |\n"
        "|----|----------|\n"
        "| D-001 | implementation choice |\n",
        encoding="utf-8",
    )
    lock_result = sync_design_contract_lock(workspace, "alpha")
    if not lock_result.ok:
        raise AssertionError(lock_result.errors)
    specs = feature_dir / "specs" / "orders"
    specs.mkdir(parents=True)
    (specs / "spec.md").write_text(
        "### Requirement [REQ-001]: orders\n#### Scenario [SCN-001]: create\n",
        encoding="utf-8",
    )
    repo = root / "business-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "plan@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Plan Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    return workspace, feature_dir, repo


class PlanGenerationStateTest(unittest.TestCase):
    def test_run_uses_design_lock_and_invalidates_only_after_design_relock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            store = PlanGenerationStore(workspace, "alpha")
            first = store.ensure_run([str(repo)], requested_partitions=None, owner_id="workflow-a", ttl_seconds=60)
            self.assertFalse(first["resumed"])
            self.assertEqual(first["status"], "generating_groups")
            self.assertEqual(first["partitions"], ["specs/orders"])
            token = first["leaseToken"]
            store.release(first["runId"], token)

            resumed = store.ensure_run([str(repo)], requested_partitions=None, owner_id="workflow-b", ttl_seconds=60)
            self.assertTrue(resumed["resumed"])
            self.assertEqual(resumed["runId"], first["runId"])

            (feature_dir / "design.md").write_text(
                (feature_dir / "design.md").read_text(encoding="utf-8") + "\n<!-- raw change -->\n",
                encoding="utf-8",
            )
            store.assert_current(first["runId"])

            lock_result = sync_design_contract_lock(workspace, "alpha")
            self.assertTrue(lock_result.ok, lock_result.errors)
            with self.assertRaises(PlanGenerationError) as caught:
                store.assert_current(first["runId"])
            self.assertEqual(caught.exception.reason, "plan_generation_input_changed")
            self.assertEqual(store.load(first["runId"])["status"], "invalidated")

    def test_worker_proposals_are_snapshot_bound_and_detail_jobs_are_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            store = PlanGenerationStore(workspace, "alpha")
            run = store.ensure_run([str(repo)], requested_partitions=["specs/orders"], owner_id="workflow", ttl_seconds=60)
            digest = run["snapshot"]["digest"]
            recorded = store.record_group_proposal(run["runId"], "specs/orders", {
                "schemaVersion": 1,
                "snapshotDigest": digest,
                "partitionKey": "specs/orders",
                "groups": [],
                "assumptions": [],
            })
            self.assertEqual(recorded["groupJobs"]["specs/orders"]["status"], "completed")
            group_file = feature_dir / ".tmp" / "plan_writer" / "task-groups.json"
            group_file.parent.mkdir(parents=True)
            group_file.write_text("{}", encoding="utf-8")
            staged = store.set_draft_tasks(run["runId"], run["leaseToken"], ["T001"], group_file)
            self.assertEqual(staged["status"], "generating_details")
            detail = store.record_detail_proposal(run["runId"], "T001", {
                "schemaVersion": 1,
                "snapshotDigest": digest,
                "taskId": "T001",
                "detail": {"goal": "deliver"},
            })
            self.assertEqual(detail["detailJobs"]["T001"]["status"], "completed")
            self.assertTrue(Path(detail["detailJobs"]["T001"]["proposalPath"]).is_file())

            requeued = store.retry_detail_jobs(
                run["runId"],
                run["leaseToken"],
                ["T001"],
                error={"reason": "plan_generation_writer_rejected", "validation": {"repairable": True}},
            )
            self.assertEqual(requeued["status"], "generating_details")
            self.assertEqual(requeued["detailJobs"]["T001"]["status"], "pending")
            self.assertEqual(requeued["detailJobs"]["T001"]["attempts"], 1)
            retried = store.record_detail_proposal(run["runId"], "T001", {
                "schemaVersion": 1,
                "snapshotDigest": digest,
                "taskId": "T001",
                "detail": {"goal": "deliver after validation feedback"},
            })
            self.assertEqual(retried["detailJobs"]["T001"]["attempts"], 2)

    def test_dirty_code_content_change_invalidates_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, repo = _workspace(Path(tmp))
            store = PlanGenerationStore(workspace, "alpha")
            run = store.ensure_run([str(repo)], requested_partitions=None, owner_id="workflow", ttl_seconds=60)
            (repo / "README.md").write_text("first local edit\n", encoding="utf-8")
            with self.assertRaises(PlanGenerationError) as first_change:
                store.assert_current(run["runId"])
            self.assertEqual(first_change.exception.reason, "plan_generation_input_changed")

    def test_writer_detail_rejection_requeues_only_repairable_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            store = PlanGenerationStore(workspace, "alpha")
            run = store.ensure_run([str(repo)], requested_partitions=None, owner_id="workflow", ttl_seconds=60)
            digest = run["snapshot"]["digest"]
            store.record_group_proposal(run["runId"], "specs/orders", {
                "schemaVersion": 1,
                "snapshotDigest": digest,
                "partitionKey": "specs/orders",
                "groups": [],
                "assumptions": [],
            })
            group_file = feature_dir / ".tmp" / "plan_writer" / "task-groups.json"
            group_file.parent.mkdir(parents=True)
            group_file.write_text("{}", encoding="utf-8")
            store.set_draft_tasks(run["runId"], run["leaseToken"], ["T001"], group_file)
            store.record_detail_proposal(run["runId"], "T001", {
                "schemaVersion": 1,
                "snapshotDigest": digest,
                "taskId": "T001",
                "detail": {"goal": "first proposal"},
            })
            rejected = {
                "ok": False,
                "validation": {
                    "repairable": True,
                    "repairableTaskIds": ["T001"],
                    "requiresTaskGroupRepair": False,
                    "requiresIntegrityRepair": False,
                },
                "errors": [{"reason": "task_detail_invalid", "taskId": "T001"}],
            }
            args = argparse.Namespace(
                workspace=str(workspace), feature="alpha", run_id=run["runId"], lease_token=run["leaseToken"],
            )
            with patch("hooks.plan_generation_launcher._writer_result", return_value=(False, rejected)):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(_cmd_commit_details(args), 1)
            response = json.loads(output.getvalue())
            self.assertEqual(response["recovery"], {"action": "regenerate_task_details", "taskIds": ["T001"]})
            manifest = store.load(run["runId"])
            self.assertEqual(manifest["status"], "generating_details")
            self.assertEqual(manifest["detailJobs"]["T001"]["status"], "pending")

    def test_collecting_draft_can_be_adopted_after_state_write_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            store = PlanGenerationStore(workspace, "alpha")
            group_file = feature_dir / ".tmp" / "plan_writer" / "task-groups.json"
            group_file.parent.mkdir(parents=True)
            groups = {"featureId": "alpha", "groups": [{"id": "T001"}]}
            group_file.write_text(json.dumps(groups), encoding="utf-8")
            lock_path = feature_dir / ".tmp" / "plan_writer" / "draft" / "lock.json"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(json.dumps({
                "featureId": "alpha",
                "status": "collecting",
                "readyTaskIds": [],
                "groupFile": str(group_file.resolve()),
                "groupingDigest": _task_group_digest(groups),
                "codeWorkspaces": [str(repo.resolve())],
            }), encoding="utf-8")
            self.assertTrue(_existing_collecting_draft_matches(store, group_file, [str(repo)]))

            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["readyTaskIds"] = ["T001"]
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            self.assertFalse(_existing_collecting_draft_matches(store, group_file, [str(repo)]))

    def test_fixed_workflow_request_uses_git_root_workspace_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, repo = _workspace(Path(tmp))
            request = build_workflow_request(
                workspace,
                "alpha",
                [str(repo)],
                partitions=["specs/orders"],
                max_parallel=3,
                lease_ttl_seconds=600,
            )
            self.assertTrue(request["ok"])
            self.assertTrue(request["useWorkflow"])
            self.assertEqual(request["strategy"], "fixed")
            self.assertEqual(request["executionMode"], "fixed")
            self.assertTrue(request["canStartWorkflow"])
            self.assertEqual(request["requiredAction"], "start_fixed_plan_generation_workflow")
            self.assertEqual(request["workflowArgs"]["codeWorkspaces"], {repo.name: str(repo.resolve())})
            self.assertTrue(Path(request["workflowScriptPath"]).is_file())
            self.assertEqual(request["workflowScript"], request["workflowScriptPath"])
            self.assertEqual(request["workflowArgs"]["maxParallel"], 3)

    def test_plan_skill_uses_the_fixed_host_workflow_invocation(self) -> None:
        content = (ROOT / "skills" / "autodev" / "autodev-plan" / "SKILL.md").read_text(encoding="utf-8")
        required = [
            "plan-generation.workflow.js",
            "canStartWorkflow=true",
            "requiredAction=start_fixed_plan_generation_workflow",
            "直接作为顶层 Workflow tool",
            "scriptPath: launcher.workflowScriptPath",
            "args: launcher.workflowArgs",
            "绝不能 `JSON.stringify`",
            "不得创建 wrapper Workflow",
            "await workflow({ scriptPath: \"…\" }, childArgs)",
            "不得调用 Python launcher 代替 Workflow",
            "无论 capability 或候选 Task 数量是多少",
            "调度整个 Plan 产物生成",
            "也不存在串行回退路径",
            "不是父会话可替代 Workflow 逐条执行的步骤",
            "只有固定 Workflow 已返回 `{ok:true, finalStatus:\"finalized\"}`",
        ]
        self.assertEqual([phrase for phrase in required if phrase not in content], [])

    def test_fixed_plan_workflow_owns_every_plan_materialization_step(self) -> None:
        content = (ROOT / "workflows" / "plan-generation.workflow.js").read_text(encoding="utf-8")
        required = [
            "record-group-proposal",
            "accept-groups",
            "record-detail-proposal",
            "commit-details",
            "plan-engineering-preflight",
            "finalize --workspace",
            'finalStatus: "finalized"',
        ]
        self.assertEqual([phrase for phrase in required if phrase not in content], [])


if __name__ == "__main__":
    unittest.main()
