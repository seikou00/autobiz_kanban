#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.utest_workspace_binding import (  # noqa: E402
    UTestWorkspaceBindingError,
    resolve_workspace_binding,
)


class UTestWorkspaceBindingTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.workspace = root / "output"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        (self.workspace / ".autobizdevops" / "state.json").write_text(
            "{}\n", encoding="utf-8"
        )
        self.first = self._git_repo(root / "first" / "business-repo")
        self.second = self._git_repo(root / "second" / "business-repo")

    def _git_repo(self, path):
        path.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return path.resolve()

    def test_missing_plan_mapping_does_not_fall_back_to_task_runs(self):
        run_path = self.feature_dir / ".task-runs" / "T001" / "run.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(
            json.dumps(
                {
                    "status": "implemented",
                    "repositories": [{"id": "business-repo", "path": str(self.first)}],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(UTestWorkspaceBindingError) as caught:
            resolve_workspace_binding(self.workspace, "alpha", "business-repo")

        error = caught.exception
        self.assertEqual("workspace_binding_missing", error.code)
        self.assertEqual("repair_plan_code_workspaces", error.required_action)

    def test_plan_code_workspace_mapping_is_authoritative(self):
        (self.feature_dir / "plan.json").write_text(
            json.dumps(
                {
                    "codeWorkspaces": {"business-repo": str(self.first)},
                    "batches": [{"id": "B001", "path": "plans/B001/plan.json"}],
                }
            ),
            encoding="utf-8",
        )

        result = resolve_workspace_binding(self.workspace, "alpha", "business-repo")

        self.assertEqual(str(self.first), result["root"])
        self.assertEqual("plan_code_workspaces", result["source"])

    def test_model_authored_path_is_not_an_input(self):
        with self.assertRaises(TypeError):
            resolve_workspace_binding(
                self.workspace,
                "alpha",
                "business-repo",
                repository_path=str(self.first),
            )


if __name__ == "__main__":
    unittest.main()
