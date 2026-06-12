from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import load_state_json_records_result  # noqa: E402
from hooks.skip_node import main as skip_node_main  # noqa: E402
from tests.test_workflow_skip import make_workspace, seed_feature  # noqa: E402


# make_workspace builds <root>/workspace/.autobizdevops/state.json, which maps to
# PLUGIN_WORKSPACE=<root> + PROJECT_CODE="workspace".
def _locator(root: Path) -> list[str]:
    return ["--plugin-workspace", str(root), "--project", "workspace", "--feature", "alpha"]


@contextlib.contextmanager
def _no_env():
    """Run with every PLUGIN_*/FEATURE_ID env cleared — prove arg-only定位."""
    with mock.patch.dict(os.environ, {}, clear=True):
        yield


def _run(argv: list[str]) -> tuple[int, dict | str]:
    buffer = io.StringIO()
    with _no_env(), contextlib.redirect_stdout(buffer):
        code = skip_node_main(argv)
    out = buffer.getvalue()
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out


class SkipNodeCliTests(unittest.TestCase):
    def test_ui_mode_skips_current_node_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "discuss_in_progress")

            code, payload = _run([*_locator(root), "--skip-node", "biz.discuss", "--json"])

            self.assertEqual(code, 0, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["old_checkpoint"], "discuss_in_progress")
            self.assertEqual(payload["new_checkpoint"], "prd_in_progress")
            self.assertEqual(payload["skip_nodes"], ["biz.discuss"])

            stored = load_state_json_records_result(workspace).records["alpha"]
            self.assertEqual(stored["checkpoint"], "prd_in_progress")
            self.assertEqual(stored["workflowSkippedNodes"], ["biz.discuss"])

    def test_skip_future_node_keeps_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "discuss_in_progress")

            code, payload = _run([*_locator(root), "--skip-node", "dev.utest", "--json"])

            self.assertEqual(code, 0, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["new_checkpoint"], "discuss_in_progress")
            stored = load_state_json_records_result(workspace).records["alpha"]
            self.assertEqual(stored["workflowSkippedNodes"], ["dev.utest"])

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "discuss_in_progress")

            code, payload = _run([*_locator(root), "--skip-node", "biz.discuss", "--dry-run", "--json"])

            self.assertEqual(code, 0, payload)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dry_run"])
            stored = load_state_json_records_result(workspace).records["alpha"]
            self.assertEqual(stored["checkpoint"], "discuss_in_progress")
            self.assertNotIn("workflowSkippedNodes", stored)

    def test_skip_rule_violation_is_surfaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = make_workspace(root)
            seed_feature(workspace, "discuss_in_progress")

            code, payload = _run([*_locator(root), "--skip-node", "dev.nope", "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(payload["ok"])
            self.assertTrue(any("未知节点" in error for error in payload["errors"]), payload)

    def test_invalid_workspace_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            make_workspace(root)  # creates <root>/workspace ...
            code, out = _run(
                ["--plugin-workspace", str(root / "absent"), "--project", "workspace",
                 "--feature", "alpha", "--skip-node", "biz.discuss", "--json"]
            )
            self.assertEqual(code, 1)

    def test_missing_required_argument_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            make_workspace(root)
            stderr = io.StringIO()
            with _no_env(), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
                # missing --project
                skip_node_main(["--plugin-workspace", str(root), "--feature", "alpha", "--skip-node", "biz.discuss"])
            self.assertNotEqual(ctx.exception.code, 0)
            self.assertIn("--project", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
