from __future__ import annotations

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
                result = analyze_batches("broken", workspace=artifact_workspace)

        self.assertFalse(result["useWorkflow"])
        self.assertEqual(result["strategy"], "blocked")
        self.assertTrue(result["reason"].startswith("launcher_error:ValueError:"))

    def test_launcher_uses_static_script_and_artifact_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts" / "project"
            code_workspace = root / "business-api"
            web_workspace = root / "business-web"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "three-paths"
            (plugin_path / "workflows").mkdir(parents=True)
            script = plugin_path / "workflows" / "code-batched-execution.workflow.js"
            script.write_text("export const meta = {};", encoding="utf-8")
            code_workspace.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=code_workspace, check=True)
            web_workspace.mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=web_workspace, check=True)
            feature_dir.mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")
            bundle = PlanBundle(
                root={
                    "codeWorkspaces": {"api": str(code_workspace), "web": str(web_workspace)},
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
                '{"codeWorkspaces": {"api": "' + str(code_workspace) + '", "web": "' + str(web_workspace) + '"}}',
                encoding="utf-8",
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle) as load_bundle, mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": True, "reason": "parallel_plan_valid"},
            ) as validate:
                result = analyze_batches("three-paths", plugin_path, artifact_workspace)
            self.assertTrue(
                (artifact_workspace / ".cmbdevclaw" / "workflows" / "code-batched-execution.workflow.js").is_file()
            )

        self.assertTrue(result["useWorkflow"])
        self.assertEqual(result["strategy"], "fixed")
        self.assertEqual(result["executionMode"], "fixed")
        self.assertTrue(result["canStartWorkflow"])
        self.assertEqual(result["requiredAction"], "start_fixed_workflow")
        self.assertEqual(result["artifactWorkspace"], str(artifact_workspace.resolve()))
        runtime_script = artifact_workspace / ".cmbdevclaw" / "workflows" / "code-batched-execution.workflow.js"
        self.assertEqual(result["workflowScript"], str(runtime_script.resolve()))
        self.assertEqual(result["workflowScriptPath"], str(runtime_script.resolve()))
        self.assertEqual(result["workflowScriptSource"], str(script.resolve()))
        self.assertEqual(result["codeWorkspaces"], {
            "api": str(code_workspace.resolve()),
            "web": str(web_workspace.resolve()),
        })
        self.assertEqual(result["executionIsolation"], "plugin_managed_git_worktrees")
        self.assertEqual(result["workflowArgs"], {
            "feature": "three-paths",
            "pluginPath": str(plugin_path.resolve()),
            "artifactWorkspace": str(artifact_workspace.resolve()),
            "codeWorkspaces": {
                "api": str(code_workspace.resolve()),
                "web": str(web_workspace.resolve()),
            },
            "maxParallel": 4,
            "timeoutPerBatch": 3600,
        })
        self.assertEqual(result["codeWorkspaceSource"], "plan_json")
        self.assertEqual(result["workspaceContractPath"], str((feature_dir / "plan.json").resolve()))
        self.assertEqual(result["reason"], "fixed_workflow_for_pending_batches:2")
        load_bundle.assert_called_once_with(feature_dir.resolve())
        validate.assert_called_once_with(artifact_workspace.resolve(), "three-paths")
        self.assertFalse((code_workspace / ".autobizdevops" / "features" / "three-paths" / "plan.json").exists())

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
                result = analyze_batches("missing-map", plugin_path, artifact_workspace)

        self.assertFalse(result["useWorkflow"])
        self.assertEqual(result["requiredAction"], "provide_code_workspace_mapping")
        self.assertTrue(result["reason"].startswith("code_workspace_mapping_missing:"))

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
                result = analyze_batches("fixed", plugin_path, artifact_workspace)

        self.assertFalse(result["useWorkflow"])
        self.assertEqual(result["reason"], "fixed_workflow_script_not_found")
        self.assertEqual(result["requiredAction"], "restore_fixed_workflow_script")


if __name__ == "__main__":
    unittest.main()
