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
    discover_candidates,
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
        cache = (
            self.feature_dir
            / "cache"
            / "code-exploration"
            / "business-repo"
            / "backend.json"
        )
        cache.parent.mkdir(parents=True)
        cache.write_text(
            json.dumps(
                {
                    "schemaVersion": "autodev.code-exploration.v1",
                    "repository": {
                        "id": "business-repo",
                        "root": str(self.first),
                    }
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
        pending = json.loads(binding_path(self.workspace).read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(item["candidateId"] for item in error.candidates),
            pending["pendingSelections"]["alpha"]["business-repo"]["candidateIds"],
        )

        selected = error.candidates[0]
        result = resolve_workspace_binding(
            self.workspace,
            "alpha",
            "business-repo",
            selected["candidateId"],
        )

        self.assertEqual(selected["root"], result["root"])
        self.assertEqual("candidate_selected", result["source"])
        saved = json.loads(binding_path(self.workspace).read_text(encoding="utf-8"))
        self.assertEqual(
            selected["candidateId"],
            saved["features"]["alpha"]["business-repo"]["candidateId"],
        )
        self.assertNotIn("pendingSelections", saved)

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

    def test_candidate_selection_cannot_overwrite_persisted_binding(self):
        with self.assertRaises(UTestWorkspaceBindingError) as caught:
            resolve_workspace_binding(self.workspace, "alpha", "business-repo")
        selected = caught.exception.candidates[0]
        resolve_workspace_binding(
            self.workspace,
            "alpha",
            "business-repo",
            selected["candidateId"],
        )
        before = binding_path(self.workspace).read_text(encoding="utf-8")
        other = next(
            item
            for item in discover_candidates(self.workspace, "alpha", "business-repo")
            if item["candidateId"] != selected["candidateId"]
        )

        with self.assertRaises(UTestWorkspaceBindingError) as rejected:
            resolve_workspace_binding(
                self.workspace,
                "alpha",
                "business-repo",
                other["candidateId"],
            )

        self.assertEqual("workspace_binding_selection_not_required", rejected.exception.code)
        self.assertEqual(before, binding_path(self.workspace).read_text(encoding="utf-8"))

    def test_candidate_selection_requires_pending_ambiguity(self):
        candidate = discover_candidates(self.workspace, "alpha", "business-repo")[0]

        with self.assertRaises(UTestWorkspaceBindingError) as caught:
            resolve_workspace_binding(
                self.workspace,
                "alpha",
                "business-repo",
                candidate["candidateId"],
            )

        self.assertEqual("workspace_binding_selection_not_pending", caught.exception.code)
        self.assertFalse(binding_path(self.workspace).exists())

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
