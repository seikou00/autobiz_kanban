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
    binding_path,
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
        first_run_path = self.feature_dir / ".task-runs" / "T000" / "run.json"
        first_run_path.parent.mkdir(parents=True)
        first_run_path.write_text(
            json.dumps(
                {
                    "status": "done",
                    "repositories": [{"id": "business-repo", "path": str(self.first)}],
                }
            ),
            encoding="utf-8",
        )
        run_path = self.feature_dir / ".task-runs" / "T001" / "run.json"
        run_path.parent.mkdir(parents=True)
        run_path.write_text(
            json.dumps(
                {
                    "status": "implemented",
                    "repositories": [
                        {"id": "business-repo", "path": str(self.second)}
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _git_repo(self, path):
        path.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return path.resolve()

    def test_ambiguous_candidates_require_user_selection_by_id(self):
        with self.assertRaises(UTestWorkspaceBindingError) as caught:
            resolve_workspace_binding(
                self.workspace,
                "alpha",
                "business-repo",
            )

        error = caught.exception
        self.assertEqual("workspace_binding_ambiguous", error.code)
        self.assertEqual("request_user_workspace_candidate_selection", error.required_action)
        self.assertEqual(2, len(error.candidates))
        self.assertFalse(binding_path(self.workspace).exists())

        selected = error.candidates[0]
        result = resolve_workspace_binding(
            self.workspace,
            "alpha",
            "business-repo",
            selected["candidateId"],
        )

        self.assertEqual(selected["root"], result["root"])
        saved = json.loads(binding_path(self.workspace).read_text(encoding="utf-8"))
        self.assertEqual(
            selected["candidateId"],
            saved["features"]["alpha"]["business-repo"]["candidateId"],
        )

    def test_persisted_selection_prevents_future_model_choice(self):
        with self.assertRaises(UTestWorkspaceBindingError) as caught:
            resolve_workspace_binding(self.workspace, "alpha", "business-repo")
        selected = caught.exception.candidates[1]
        resolve_workspace_binding(
            self.workspace,
            "alpha",
            "business-repo",
            selected["candidateId"],
        )

        result = resolve_workspace_binding(self.workspace, "alpha", "business-repo")

        self.assertEqual(selected["root"], result["root"])
        self.assertEqual("persisted_binding", result["source"])

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
