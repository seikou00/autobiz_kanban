from __future__ import annotations

import hashlib
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from hooks.plan_json import PlanBundle
from hooks.workflow_launcher import analyze_batches


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

    def test_launcher_uses_static_script_and_artifact_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts" / "project"
            code_workspace = root / "business-api"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "three-paths"
            (plugin_path / "workflows").mkdir(parents=True)
            script = plugin_path / "workflows" / "code-batched-execution.workflow.js"
            script.write_text("export const meta = {};", encoding="utf-8")
            code_workspace.mkdir(parents=True)
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
                result = analyze_batches("three-paths", plugin_path, artifact_workspace, "Z990692-294")
            self.assertTrue(
                (
                    artifact_workspace
                    / ".cmbdevclaw"
                    / "workflows"
                    / "three-paths"
                    / "code-batched-execution.workflow.js"
                ).is_file()
            )

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["strategy"], "fixed")
        self.assertEqual(result["executionMode"], "fixed")
        self.assertTrue(result["canStartWorkflow"])
        self.assertEqual(result["requiredAction"], "start_fixed_workflow")
        self.assertEqual(result["artifactWorkspace"], str(artifact_workspace.resolve()))
        runtime_script = (
            artifact_workspace
            / ".cmbdevclaw"
            / "workflows"
            / "three-paths"
            / "code-batched-execution.workflow.js"
        )
        self.assertEqual(result["workflowScript"], str(runtime_script.resolve()))
        self.assertEqual(result["workflowScriptPath"], str(runtime_script.resolve()))
        self.assertEqual(result["workflowWorkspaceRoot"], str(artifact_workspace.resolve()))
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
            "maxParallel": 4,
            "timeoutPerBatch": 3600,
            "runtimeConfig": {
                "parallelSchedulingMode": "conservative",
                "maxParallel": 4,
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
        self.assertEqual(execution_plan["maxParallel"], 4)
        self.assertEqual([item["id"] for item in execution_plan["batches"]], ["B001", "B002"])
        self.assertEqual(execution_plan["waves"][0]["batchIds"], ["B001"])
        self.assertEqual(execution_plan["waves"][1]["batchIds"], ["B002"])
        self.assertTrue(any("合并" in note and "下游" in note for note in execution_plan["notes"]))
        load_bundle.assert_called_once_with(feature_dir.resolve())
        validate.assert_called_once_with(artifact_workspace.resolve(), "three-paths")
        self.assertFalse((code_workspace / ".autobizdevops" / "features" / "three-paths" / "plan.json").exists())

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
