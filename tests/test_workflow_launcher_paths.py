from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hooks.plan_json import PlanBundle
from hooks.workflow_launcher import analyze_batches


class WorkflowLauncherPathContractTest(unittest.TestCase):
    def test_launcher_reads_plan_from_artifact_workspace_not_plugin_or_code_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_path = root / "plugin"
            artifact_workspace = root / "artifacts" / "project"
            code_workspace = root / "business-code"
            feature_dir = artifact_workspace / ".autobizdevops" / "features" / "three-paths"
            plugin_path.mkdir(parents=True)
            code_workspace.mkdir()
            (artifact_workspace / ".autobizdevops").mkdir(parents=True)
            (artifact_workspace / ".autobizdevops" / "state.json").write_text("{}", encoding="utf-8")
            feature_dir.mkdir(parents=True)
            (feature_dir / "plan.json").write_text("{}", encoding="utf-8")

            bundle = PlanBundle(
                root={
                    "batches": [
                        {"id": "B001", "status": "todo", "executionLane": "backend", "deps": []},
                        {"id": "B002", "status": "todo", "executionLane": "frontend", "deps": []},
                    ]
                },
                batches={"B001": {"tasks": []}, "B002": {"tasks": []}},
                tasks=[],
                task_batches={},
            )
            with mock.patch("hooks.workflow_launcher.load_plan_bundle", return_value=bundle) as load_bundle, mock.patch(
                "hooks.workflow_launcher.validate_plan_for_parallel",
                return_value={"canParallel": True, "reason": "parallel_plan_valid"},
            ) as validate:
                result = analyze_batches("three-paths", plugin_path, artifact_workspace)

            self.assertTrue(result["useWorkflow"])
            self.assertEqual(result["artifactWorkspace"], str(artifact_workspace.resolve()))
            self.assertEqual(
                result["workflowScript"],
                str((plugin_path / "workflows" / "code-batched-execution.workflow.js").resolve()),
            )
            load_bundle.assert_called_once_with(feature_dir.resolve())
            validate.assert_called_once_with(artifact_workspace.resolve(), "three-paths")
            self.assertFalse((plugin_path / ".autobizdevops" / "features" / "three-paths" / "plan.json").exists())
            self.assertFalse((code_workspace / ".autobizdevops" / "features" / "three-paths" / "plan.json").exists())

    def test_workflow_keeps_plugin_artifact_and_code_workspace_inputs_separate(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / "workflows" / "code-batched-execution.workflow.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const pluginPath = args.pluginPath", workflow)
        self.assertIn("const artifactWorkspace = args.artifactWorkspace;", workflow)
        self.assertIn("const codeWorkspaces = args.codeWorkspaces", workflow)
        self.assertIn('create --workspace "${artifactWorkspace}"', workflow)
        self.assertNotIn('--workspace "${pluginPath}"', workflow)


if __name__ == "__main__":
    unittest.main()
