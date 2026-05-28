from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from hooks.init_dev_agents import DevAgentsInitError, init_dev_agents
from hooks.load_sys_agents import load_sys_agents
from hooks.paths import get_sys_agents_md_path


def write_sys_agents(plugin_root: Path, system_no: str, content: str) -> Path:
    agents = plugin_root / "sys" / system_no / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text(content, encoding="utf-8")
    return agents


class DevAgentsInitTests(unittest.TestCase):
    def test_project_code_copies_matching_sys_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(plugin_root, "abc", "abc rules\n")

            result = init_dev_agents(
                code_workspace,
                env={"projectCode": "abc"},
                plugin_root=plugin_root,
            )

            self.assertTrue(result["created"])
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "abc rules\n")
            self.assertEqual(result["system_no"], "abc")

    def test_missing_project_code_defaults_to_lf39(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(plugin_root, "lf39", "default rules\n")

            result = init_dev_agents(
                code_workspace,
                env={},
                plugin_root=plugin_root,
            )

            self.assertTrue(result["created"])
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "default rules\n")
            self.assertEqual(result["system_no"], "lf39")

    def test_existing_agents_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(plugin_root, "abc", "abc rules\n")
            (code_workspace / "AGENTS.md").write_text("existing\n", encoding="utf-8")

            result = init_dev_agents(
                code_workspace,
                env={"projectCode": "abc"},
                plugin_root=plugin_root,
            )

            self.assertFalse(result["created"])
            self.assertTrue(result["skipped"])
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "existing\n")

    def test_invalid_project_code_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_workspace = Path(tmp) / "code"
            code_workspace.mkdir()

            with self.assertRaisesRegex(DevAgentsInitError, "invalid projectCode"):
                init_dev_agents(code_workspace, env={"projectCode": "../abc"}, plugin_root=Path(tmp))

    def test_missing_source_agents_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_workspace = root / "code"
            code_workspace.mkdir()

            with self.assertRaisesRegex(DevAgentsInitError, "sys AGENTS.md not found"):
                init_dev_agents(code_workspace, env={"projectCode": "abc"}, plugin_root=root / "plugin")

    def test_sys_agents_path_points_under_plugin_sys(self) -> None:
        plugin_root = Path("/tmp/plugin-root")
        self.assertEqual(
            get_sys_agents_md_path("abc", plugin_root),
            plugin_root.resolve() / "sys" / "abc" / "AGENTS.md",
        )


class LoadSysAgentsTests(unittest.TestCase):
    def test_load_sys_agents_uses_project_code_without_project_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sys_agents(root / "plugin", "abc", "abc rules\n")
            workspace = root / "workspace"
            workspace.mkdir()

            with patch.dict(os.environ, {"projectCode": "abc"}), patch(
                "hooks.load_sys_agents.get_sys_agents_md_path",
                return_value=source,
            ):
                result = load_sys_agents(workspace)

            self.assertTrue(result["ok"])
            self.assertFalse(result["skipped"])
            self.assertEqual(result["system_no"], "abc")
            self.assertEqual(result["content"], "abc rules\n")


if __name__ == "__main__":
    unittest.main()
