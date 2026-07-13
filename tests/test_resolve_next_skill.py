from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks.init_workspace import create_feature, init_workspace
from hooks.resolve_next_skill import main


class ResolveNextSkillCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.plugin_workspace = Path(self.tmp.name).resolve()
        self.project = self.plugin_workspace / "demo"
        self.project.mkdir()
        init_workspace(self.project)
        create_feature(self.project, "feature-a")
        self.env = {
            "PLUGIN_WORKSPACE": str(self.plugin_workspace),
            "PROJECT_DIR": "demo",
            "FEATURE_ID": "feature-a",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolves_workspace_and_feature_from_environment(self) -> None:
        stdout = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(stdout):
            exit_code = main(["--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["feature"], "feature-a")

    def test_rejects_external_workspace_and_feature_arguments(self) -> None:
        invalid_arguments = (
            ["--workspace", str(self.project), "--json"],
            ["--feature", "feature-a", "--json"],
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(stderr):
                    exit_code = main(arguments)

                self.assertEqual(exit_code, 2)
                self.assertIn("不接受 --workspace/-w 或 --feature/-f", stderr.getvalue())

    def test_reports_missing_feature_environment_variable_as_json(self) -> None:
        stdout = io.StringIO()
        env = {key: value for key, value in self.env.items() if key != "FEATURE_ID"}
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(stdout):
            exit_code = main(["--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("FEATURE_ID 未设置", payload["errors"][0])


if __name__ == "__main__":
    unittest.main()
