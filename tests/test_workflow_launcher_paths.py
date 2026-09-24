from __future__ import annotations

import hashlib
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from hooks.plan_json import PlanBundle
from hooks.workflow_launcher import analyze_batches, main


class WorkflowLauncherPathContractTest(unittest.TestCase):

    def test_launcher_never_falls_back_to_serial_on_plan_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_workspace = Path(tmp) / "artifacts"
            (artifact_workspace / ".autobizdevops" / "features" / "broken").mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (artifact_workspace / ".autobizdevops" / "features" / "broken" / "plan.json").write_text("{}", encoding="utf-8")
            with mock.patch(
                "hooks.workflow_launcher.load_plan_bundle",
                side_effect=ValueError("B001.completedTaskCount_mismatch"),
            ):
                result = analyze_batches("broken", workspace=artifact_workspace, task_card_id="Z990692-294")

        self.assertFalse(result["useWorkflow"])
        self.assertEqual(result["strategy"], "blocked")
        self.assertTrue(result["reason"].startswith("launcher_error:ValueError:"))

    def test_launcher_uses_static_script_and_mounted_workflow_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts" / "project"
            workflow_workspace = root / "workflow-session"
            code_workspace = root / "business-api"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "three-paths"
            (plugin_path / "workflows").mkdir(parents=True)
            script = plugin_path / "workflows" / "code-batched-execution.workflow.js"
            script.write_text("export const meta = {};", encoding="utf-8")
            code_workspace.mkdir(parents=True)
            workflow_workspace.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(code_workspace), "web": str(code_workspace)},
                    "batches": [
                        {"id": "B001", "status": "todo", "executionLane": "backend", "workspaceRef": "api", "deps": []},
                        {"id": "B002", "status": "todo", "executionLane": "frontend", "workspaceRef": "web", "deps": []},
                    ]
                },
                batches={
                    "B001": {"tasks": [{"workspaceRef": "api"}]},
                    "B002": {"tasks": [{"workspaceRef": "web"}]},
                },
                tasks=[],
                task_batches={},
            )
            (feature_dir / "plan.json").write_text(
                '{"codeWorkspaces": {"api": "' + str(code_workspace) + '", "web": "' + str(code_workspace) + '"}}',
                encoding="utf-8",
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle) as load_bundle, mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": True, "reason": "parallel_plan_valid"},
            ) as validate:
                result = analyze_batches(
                    "three-paths",
                    plugin_path,
                    artifact_workspace,
                    "Z990692-294",
                    workflow_workspace,
                )
            self.assertTrue(
                (
                    artifact_workspace
                    / ".cmbdevclaw"
                    / "workflows"
                    / "three-paths"
                    / "code-batched-execution.workflow.js"
                ).is_file()
            )
            launch_script = (
                workflow_workspace
                / ".cmbdevclaw"
                / "workflows"
                / "three-paths"
                / "code-batched-execution.workflow.js"
            )
            self.assertTrue(launch_script.is_file())
            self.assertEqual(launch_script.read_bytes(), script.read_bytes())

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["strategy"], "fixed")
        self.assertEqual(result["executionMode"], "fixed")
        self.assertTrue(result["canStartWorkflow"])
        self.assertEqual(result["requiredAction"], "start_fixed_workflow")
        self.assertEqual(result["artifactWorkspace"], str(artifact_workspace.resolve()))
        artifact_runtime_script = (
            artifact_workspace
            / ".cmbdevclaw"
            / "workflows"
            / "three-paths"
            / "code-batched-execution.workflow.js"
        )
        runtime_script = (
            workflow_workspace
            / ".cmbdevclaw"
            / "workflows"
            / "three-paths"
            / "code-batched-execution.workflow.js"
        )
        self.assertEqual(result["workflowScript"], str(runtime_script.resolve()))
        self.assertEqual(result["workflowScriptPath"], str(runtime_script.resolve()))
        self.assertEqual(result["workflowArtifactScriptPath"], str(artifact_runtime_script.resolve()))
        self.assertEqual(result["workflowWorkspaceRoot"], str(workflow_workspace.resolve()))
        self.assertEqual(
            result["workflowScriptRelativePath"],
            ".cmbdevclaw/workflows/three-paths/code-batched-execution.workflow.js",
        )
        self.assertEqual(result["workflowScriptSource"], str(script.resolve()))
        self.assertNotIn("workflowScriptContent", result)
        self.assertEqual(
            result["workflowScriptSha256"],
            hashlib.sha256(b"export const meta = {};").hexdigest(),
        )
        self.assertEqual(result["codeWorkspaces"], {
            "api": str(code_workspace.resolve()),
            "web": str(code_workspace.resolve()),
        })
        self.assertEqual(result["executionIsolation"], "native_git_worktrees")
        self.assertEqual(result["workflowHostGitRoot"], str(code_workspace.resolve()))
        self.assertEqual(result["workflowArgs"], {
            "feature": "three-paths",
            "pluginPath": str(plugin_path.resolve()),
            "artifactWorkspace": str(artifact_workspace.resolve()),
            "codeWorkspaces": {
                "api": str(code_workspace.resolve()),
                "web": str(code_workspace.resolve()),
            },
            "workflowHostGitRoot": str(code_workspace.resolve()),
            "maxParallel": 5,
            "timeoutPerBatch": 3600,
            "runtimeConfig": {
                "parallelSchedulingMode": "conservative",
                "maxParallel": 5,
                "conflictResolution": {
                    "maxAttempts": 2,
                    "enableAutoResolve": False,
                },
            },
            "taskCardId": "Z990692-294",
            "resumeMode": "automatic",
            "resumeRunId": None,
        })
        self.assertEqual(result["codeWorkspaceSource"], "plan_json")
        self.assertEqual(result["workspaceContractPath"], str((feature_dir / "plan.json").resolve()))
        self.assertEqual(result["reason"], "fixed_workflow_for_pending_batches:2")
        execution_plan = result["batchExecutionPlan"]
        self.assertEqual(execution_plan["maxParallel"], 5)
        self.assertEqual([item["id"] for item in execution_plan["batches"]], ["B001", "B002"])
        self.assertEqual(execution_plan["waves"][0]["batchIds"], ["B001"])
        self.assertEqual(execution_plan["waves"][1]["batchIds"], ["B002"])
        self.assertTrue(any("合并" in note and "下游" in note for note in execution_plan["notes"]))
        load_bundle.assert_called_once_with(feature_dir.resolve())
        validate.assert_called_once_with(artifact_workspace.resolve(), "three-paths")
        self.assertFalse((code_workspace / ".autobizdevops" / "features" / "three-paths" / "plan.json").exists())

    def test_cli_uses_the_launch_directory_for_the_workflow_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow_workspace = Path(tmp) / "mounted-workspace"
            workflow_workspace.mkdir()
            with mock.patch("hooks.workflow_launcher.analyze_batches", return_value={"useWorkflow": False, "reason": "plan_not_found"}) as analyze, mock.patch.object(
                sys,
                "argv",
                ["workflow_launcher.py", "--feature", "missing", "--task-card-id", "Z990692-294"],
            ), mock.patch("hooks.workflow_launcher.Path.cwd", return_value=workflow_workspace):
                self.assertEqual(main(), 0)

        self.assertEqual(analyze.call_args.args[4], workflow_workspace)

    def test_launcher_uses_one_fixed_workflow_for_multiple_worktree_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts"
            api = root / "api"
            web = root / "web"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "multi"
            (plugin_path / "workflows").mkdir(parents=True)
            (plugin_path / "workflows" / "code-batched-execution.workflow.js").write_text("export const meta = {};", encoding="utf-8")
            for repo in (api, web):
                repo.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(api), "web": str(web)},
                    "batches": [
                        {"id": "B001", "status": "todo", "workspaceRef": "api", "deps": []},
                        {"id": "B002", "status": "todo", "workspaceRef": "web", "deps": []},
                    ],
                },
                batches={"B001": {"tasks": [{"workspaceRef": "api"}]}, "B002": {"tasks": [{"workspaceRef": "web"}]}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": True, "reason": "parallel_plan_valid"},
            ):
                result = analyze_batches("multi", plugin_path, artifact_workspace, "Z990692-294")

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["strategy"], "fixed")
        self.assertEqual(result["executionMode"], "fixed")
        self.assertEqual(result["requiredAction"], "start_fixed_workflow")
        self.assertTrue(result["canStartWorkflow"])
        self.assertIsNone(result["workflowHostGitRoot"])
        self.assertEqual(result["workflowHostGitRoots"], [str(api.resolve()), str(web.resolve())])
        self.assertEqual(result["workflowArgs"]["codeWorkspaces"], {
            "api": str(api.resolve()),
            "web": str(web.resolve()),
        })
        self.assertIsNone(result["workflowArgs"]["workflowHostGitRoot"])
        self.assertEqual(result["workflowArgs"]["taskCardId"], "Z990692-294")
        self.assertNotIn("repositoryCoordinator", result)

    def test_launcher_blocks_without_code_workspace_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts" / "project"
            code_workspace = root / "business-code"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "missing-map"
            (plugin_path / "workflows").mkdir(parents=True)
            (plugin_path / "workflows" / "code-batched-execution.workflow.js").write_text(
                "export const meta = {};", encoding="utf-8"
            )
            code_workspace.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={"batches": [{"id": "B001", "status": "todo", "workspaceRef": "business", "deps": []}]},
                batches={"B001": {"tasks": [{"workspaceRef": "business"}]}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": True, "reason": "single_batch_workflow_valid"},
            ):
                result = analyze_batches("missing-map", plugin_path, artifact_workspace, "Z990692-294")

        self.assertFalse(result["useWorkflow"])
        self.assertEqual(result["requiredAction"], "provide_code_workspace_mapping")
        self.assertTrue(result["reason"].startswith("code_workspace_mapping_missing:"))

    def test_launcher_reenters_active_run_when_plan_batch_is_failed(self) -> None:
        """A failed Plan projection cannot hide a retry-exhausted scheduler run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts"
            code_workspace = root / "business-code"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "resume"
            (plugin_path / "workflows").mkdir(parents=True)
            (plugin_path / "workflows" / "code-batched-execution.workflow.js").write_text(
                "export const meta = {};", encoding="utf-8"
            )
            code_workspace.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(code_workspace)},
                    "batches": [{"id": "B001", "status": "failed", "workspaceRef": "api", "deps": []}],
                },
                batches={"B001": {"tasks": [{"workspaceRef": "api"}]}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": False, "reason": "no_pending_batches", "errors": []},
            ), mock.patch("hooks.workflow_launcher.get_active_run", return_value="cw-resume-001"), mock.patch(
                "hooks.workflow_launcher.load_manifest",
                return_value={
                    "runId": "cw-resume-001",
                    "batches": {"B001": {"status": "blocked", "recovery": {"status": "retry_exhausted"}}},
                },
            ):
                result = analyze_batches("resume", plugin_path, artifact_workspace, "Z990692-294")

        self.assertTrue(result["useWorkflow"])
        self.assertTrue(result["canStartWorkflow"])
        self.assertEqual(result["requiredAction"], "resume_fixed_workflow")
        self.assertEqual(result["workflowArgs"]["resumeMode"], "manual")
        self.assertEqual(result["workflowArgs"]["resumeRunId"], "cw-resume-001")
        self.assertEqual(result["batches"][0]["id"], "B001")

    def test_launcher_keeps_every_manifest_retry_batch_in_manual_recovery(self) -> None:
        """Manual recovery must not narrow B001/B007/B015 to its first dispatch slot."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts"
            code_workspace = root / "business-code"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "resume-all"
            (plugin_path / "workflows").mkdir(parents=True)
            (plugin_path / "workflows" / "code-batched-execution.workflow.js").write_text(
                "export const meta = {};", encoding="utf-8"
            )
            code_workspace.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            batch_ids = ["B001", "B007", "B015"]
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(code_workspace)},
                    # The Plan may already project these deliveries as done or
                    # failed. The manifest remains the recovery authority.
                    "batches": [
                        {"id": "B001", "status": "failed", "workspaceRef": "api", "deps": []},
                        {"id": "B007", "status": "done", "workspaceRef": "api", "deps": []},
                        {"id": "B015", "status": "failed", "workspaceRef": "api", "deps": []},
                    ],
                },
                batches={batch_id: {"tasks": [{"workspaceRef": "api"}]} for batch_id in batch_ids},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": False, "reason": "no_pending_batches", "errors": []},
            ), mock.patch("hooks.workflow_launcher.get_active_run", return_value="cw-resume-all"), mock.patch(
                "hooks.workflow_launcher.load_manifest",
                return_value={
                    "runId": "cw-resume-all",
                    "batches": {batch_id: {"status": "retry_pending"} for batch_id in batch_ids},
                },
            ):
                result = analyze_batches("resume-all", plugin_path, artifact_workspace, "Z990692-294")

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["requiredAction"], "resume_fixed_workflow")
        self.assertEqual(result["workflowArgs"]["resumeRunId"], "cw-resume-all")
        self.assertEqual([batch["id"] for batch in result["batches"]], batch_ids)
        self.assertEqual(result["batchCount"], len(batch_ids))
        self.assertEqual(
            result["reason"],
            "fixed_workflow_for_manual_recovery:cw-resume-all:B001,B007,B015",
        )

    def test_launcher_starts_fixed_workflow_for_sealed_stage_recovery(self) -> None:
        """A Plan-projected done Batch must not hide an interrupted UTest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts"
            code_workspace = root / "business-code"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "stage-recovery"
            (plugin_path / "workflows").mkdir(parents=True)
            (plugin_path / "workflows" / "code-batched-execution.workflow.js").write_text(
                "export const meta = {};", encoding="utf-8"
            )
            code_workspace.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(code_workspace)},
                    "batches": [{"id": "B006", "status": "done", "workspaceRef": "api", "deps": []}],
                },
                batches={"B006": {"tasks": [{"workspaceRef": "api"}]}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": False, "reason": "no_pending_batches", "errors": []},
            ), mock.patch("hooks.workflow_launcher.get_active_run", return_value="cw-stage-006"), mock.patch(
                "hooks.workflow_launcher.load_manifest",
                return_value={
                    "runId": "cw-stage-006",
                    "batches": {
                        "B006": {
                            "status": "sealed",
                            "commitSha": "sealed-commit",
                            "lease": None,
                            "stageStates": {
                                "prepare": {"status": "passed"},
                                "implement": {"status": "passed"},
                                "review": {"status": "passed"},
                                "test": {"status": "running"},
                            },
                        },
                    },
                },
            ):
                result = analyze_batches("stage-recovery", plugin_path, artifact_workspace, "Z990692-294")

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["requiredAction"], "resume_fixed_workflow")
        self.assertEqual(result["workflowArgs"]["resumeMode"], "automatic")
        self.assertIsNone(result["workflowArgs"]["resumeRunId"])
        self.assertEqual(result["stageRecoveryBatchIds"], ["B006"])
        self.assertEqual(result["retryRecoveryBatchIds"], [])
        self.assertEqual(result["batches"], [
            {
                "id": "B006",
                "title": None,
                "lane": "unknown",
                "executionLane": "unknown",
                "workspaceRef": "api",
                "executionStage": "parallel",
                "deps": [],
                "status": "done",
                "taskCount": 1,
                "taskIds": [],
                "writeSet": [],
            },
        ])
        self.assertEqual(result["reason"], "fixed_workflow_for_stage_recovery:cw-stage-006:B006")

    def test_launcher_starts_fixed_workflow_for_dirty_implementation_recovery(self) -> None:
        """A pre-seal owned worktree resumes automatically, never as manual provision."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts"
            code_workspace = root / "business-code"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "implementation-recovery"
            (plugin_path / "workflows").mkdir(parents=True)
            (plugin_path / "workflows" / "code-batched-execution.workflow.js").write_text(
                "export const meta = {};", encoding="utf-8"
            )
            code_workspace.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(code_workspace)},
                    "batches": [{"id": "B008", "status": "done", "workspaceRef": "api", "deps": []}],
                },
                batches={"B008": {"tasks": [{"workspaceRef": "api"}]}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": False, "reason": "no_pending_batches", "errors": []},
            ), mock.patch("hooks.workflow_launcher.get_active_run", return_value="cw-implementation-008"), mock.patch(
                "hooks.workflow_launcher.load_manifest",
                return_value={
                    "runId": "cw-implementation-008",
                    "batches": {
                        "B008": {
                            "status": "retry_pending",
                            "worktreePath": str(code_workspace / "B008"),
                            "branchName": "autodev/workspace/cw-implementation-008/B008",
                            "recovery": {
                                "kind": "implementation_resume",
                                "preserveWorktree": True,
                                "reprovision": False,
                            },
                        },
                    },
                },
            ):
                result = analyze_batches("implementation-recovery", plugin_path, artifact_workspace, "Z990692-294")

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["requiredAction"], "resume_fixed_workflow")
        self.assertEqual(result["workflowArgs"]["resumeMode"], "automatic")
        self.assertIsNone(result["workflowArgs"]["resumeRunId"])
        self.assertEqual(result["implementationRecoveryBatchIds"], ["B008"])
        self.assertEqual(result["retryRecoveryBatchIds"], [])
        self.assertEqual(result["reason"], "fixed_workflow_for_implementation_recovery:cw-implementation-008:B008")

    def test_launcher_blocks_when_static_workflow_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "fixed"
            plugin_path.mkdir()
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={"batches": [{"id": "B001", "status": "todo", "deps": []}]},
                batches={"B001": {"tasks": []}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle), mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": True, "reason": "single_batch_workflow_valid"},
            ):
                result = analyze_batches("fixed", plugin_path, artifact_workspace, "Z990692-294")

        self.assertFalse(result["useWorkflow"])
        self.assertEqual(result["reason"], "fixed_workflow_script_not_found")
        self.assertEqual(result["requiredAction"], "restore_fixed_workflow_script")


if __name__ == "__main__":
    unittest.main()
