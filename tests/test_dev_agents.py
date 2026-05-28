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
    return write_sys_file(plugin_root, system_no, "AGENTS.md", content)


def write_sys_file(plugin_root: Path, system_no: str, relative_path: str, content: str) -> Path:
    path = plugin_root / "sys" / system_no / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class DevAgentsInitTests(unittest.TestCase):
    def assert_relative_symlink(self, target: Path, source: Path) -> None:
        self.assertTrue(target.is_symlink())
        self.assertFalse(Path(os.readlink(target)).is_absolute())
        self.assertEqual(target.resolve(), source.resolve())

    def test_system_id_links_matching_sys_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            source = write_sys_agents(plugin_root, "abc", "abc rules\n")

            result = init_dev_agents(
                code_workspace,
                env={"SYSTEM_ID": "abc"},
                plugin_root=plugin_root,
            )

            self.assertTrue(result["created"])
            self.assert_relative_symlink(code_workspace / "AGENTS.md", source)
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "abc rules\n")
            self.assertEqual(result["system_no"], "abc")
            self.assertEqual(len(result["links"]), 1)

    def test_system_id_matches_sys_directory_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            source = write_sys_agents(plugin_root, "abc", "abc rules\n")

            result = init_dev_agents(
                code_workspace,
                env={"SYSTEM_ID": "ABC"},
                plugin_root=plugin_root,
            )

            self.assertTrue(result["created"])
            self.assert_relative_symlink(code_workspace / "AGENTS.md", source)
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "abc rules\n")
            self.assertEqual(result["system_no"], "ABC")
            self.assertEqual(result["source"], str(source.resolve()))
            self.assertIn("sys/abc", result["message"])

    def test_missing_system_id_defaults_to_lf39(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            source = write_sys_agents(plugin_root, "lf39", "default rules\n")

            result = init_dev_agents(
                code_workspace,
                env={},
                plugin_root=plugin_root,
            )

            self.assertTrue(result["created"])
            self.assert_relative_symlink(code_workspace / "AGENTS.md", source)
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "default rules\n")
            self.assertEqual(result["system_no"], "lf39")

    def test_existing_agents_is_not_overwritten_and_companions_still_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(
                plugin_root,
                "abc",
                "\n".join(
                    [
                        "# abc",
                        "",
                        "## 文档地图",
                        "- `{project_root}/AGENTS.md`: entry",
                        "- `{project_root}/BACKEND_AGENTS.md`: backend",
                        "",
                        "## 其他",
                    ]
                ),
            )
            backend = write_sys_file(plugin_root, "abc", "BACKEND_AGENTS.md", "backend rules\n")
            (code_workspace / "AGENTS.md").write_text("existing\n", encoding="utf-8")

            result = init_dev_agents(
                code_workspace,
                env={"SYSTEM_ID": "abc"},
                plugin_root=plugin_root,
            )

            self.assertFalse(result["created"])
            self.assertTrue(result["skipped"])
            self.assertEqual((code_workspace / "AGENTS.md").read_text(encoding="utf-8"), "existing\n")
            self.assert_relative_symlink(code_workspace / "BACKEND_AGENTS.md", backend)

    def test_invalid_system_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_workspace = Path(tmp) / "code"
            code_workspace.mkdir()

            with self.assertRaisesRegex(DevAgentsInitError, "invalid SYSTEM_ID"):
                init_dev_agents(code_workspace, env={"SYSTEM_ID": "../abc"}, plugin_root=Path(tmp))

    def test_missing_source_agents_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_workspace = root / "code"
            code_workspace.mkdir()

            with self.assertRaisesRegex(DevAgentsInitError, "sys AGENTS.md not found"):
                init_dev_agents(code_workspace, env={"SYSTEM_ID": "abc"}, plugin_root=root / "plugin")

    def test_sys_agents_path_points_under_plugin_sys(self) -> None:
        plugin_root = Path("/tmp/plugin-root")
        self.assertEqual(
            get_sys_agents_md_path("abc", plugin_root),
            plugin_root.resolve() / "sys" / "abc" / "AGENTS.md",
        )

    def test_sys_agents_path_matches_directory_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = Path(tmp) / "plugin"
            source = write_sys_agents(plugin_root, "abc", "abc rules\n")

            self.assertEqual(get_sys_agents_md_path("ABC", plugin_root), source.resolve())

    def test_document_map_links_project_root_style_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(
                plugin_root,
                "LA6407",
                "\n".join(
                    [
                        "# LA6407",
                        "",
                        "## 文档地图",
                        "- `{project_root}/AGENTS.md`: entry",
                        "- `{project_root}/BACKEND_AGENTS.md`: backend",
                        "- `{project_root}/FRONT_AGENTS.md`: front",
                        "- `{project_root}/references/BACKEND_ARCHITECTURE.md`: backend arch",
                        "- `{project_root}/references/BACKEND_DB_GUIDE.md`: db",
                        "- `{project_root}/references/FRONT_ARCHITECTURE.md`: front arch",
                        "",
                        "## 按任务类型加载",
                    ]
                ),
            )
            backend = write_sys_file(plugin_root, "LA6407", "BACKEND_AGENTS.md", "backend\n")
            front = write_sys_file(plugin_root, "LA6407", "FRONT_AGENTS.md", "front\n")
            backend_arch = write_sys_file(
                plugin_root, "LA6407", "references/BACKEND_ARCHITECTURE.md", "backend arch\n"
            )
            db_guide = write_sys_file(plugin_root, "LA6407", "references/BACKEND_DB_GUIDE.md", "db\n")
            front_arch = write_sys_file(
                plugin_root, "LA6407", "references/FRONT_ARCHITECTURE.md", "front arch\n"
            )

            result = init_dev_agents(code_workspace, env={"SYSTEM_ID": "la6407"}, plugin_root=plugin_root)

            self.assertEqual(len(result["links"]), 6)
            self.assert_relative_symlink(code_workspace / "BACKEND_AGENTS.md", backend)
            self.assert_relative_symlink(code_workspace / "FRONT_AGENTS.md", front)
            self.assert_relative_symlink(code_workspace / "references" / "BACKEND_ARCHITECTURE.md", backend_arch)
            self.assert_relative_symlink(code_workspace / "references" / "BACKEND_DB_GUIDE.md", db_guide)
            self.assert_relative_symlink(code_workspace / "references" / "FRONT_ARCHITECTURE.md", front_arch)

    def test_document_map_links_plugin_dir_style_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(
                plugin_root,
                "LA01",
                "\n".join(
                    [
                        "# LA01",
                        "",
                        "## 文档地图",
                        "- `{PLUGIN_DIR}/sys/LA01/AGENTS.md`: entry",
                        "- `{PLUGIN_DIR}/sys/LA01/BACKEND_AGENTS.md`: backend",
                        "- `{PLUGIN_DIR}/sys/LA01/references/BACKEND_ARCHITECTURE.md`: backend arch",
                        "",
                        "## 按任务类型加载",
                    ]
                ),
            )
            backend = write_sys_file(plugin_root, "LA01", "BACKEND_AGENTS.md", "backend\n")
            backend_arch = write_sys_file(
                plugin_root, "LA01", "references/BACKEND_ARCHITECTURE.md", "backend arch\n"
            )

            result = init_dev_agents(code_workspace, env={"SYSTEM_ID": "LA01"}, plugin_root=plugin_root)

            self.assertEqual(len(result["links"]), 3)
            self.assert_relative_symlink(code_workspace / "BACKEND_AGENTS.md", backend)
            self.assert_relative_symlink(code_workspace / "references" / "BACKEND_ARCHITECTURE.md", backend_arch)

    def test_document_map_links_sys_prefix_style_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(
                plugin_root,
                "LF3905",
                "\n".join(
                    [
                        "# LF3905",
                        "",
                        "## 文档地图",
                        "- `sys/LF3905/AGENTS.md`: entry",
                        "- `sys/LF3905/BACKEND_AGENTS.md`: backend",
                        "- `sys/LF3905/references/BACKEND_DB_GUIDE.md`: db",
                        "",
                        "## 按任务类型加载",
                    ]
                ),
            )
            backend = write_sys_file(plugin_root, "LF3905", "BACKEND_AGENTS.md", "backend\n")
            db_guide = write_sys_file(plugin_root, "LF3905", "references/BACKEND_DB_GUIDE.md", "db\n")

            result = init_dev_agents(code_workspace, env={"SYSTEM_ID": "LF3905"}, plugin_root=plugin_root)

            self.assertEqual(len(result["links"]), 3)
            self.assert_relative_symlink(code_workspace / "BACKEND_AGENTS.md", backend)
            self.assert_relative_symlink(code_workspace / "references" / "BACKEND_DB_GUIDE.md", db_guide)

    def test_missing_document_map_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_root = root / "plugin"
            code_workspace = root / "code"
            code_workspace.mkdir()
            write_sys_agents(
                plugin_root,
                "abc",
                "\n".join(
                    [
                        "# abc",
                        "",
                        "## 文档地图",
                        "- `{project_root}/BACKEND_AGENTS.md`: missing",
                    ]
                ),
            )

            with self.assertRaisesRegex(DevAgentsInitError, "sys document map file not found"):
                init_dev_agents(code_workspace, env={"SYSTEM_ID": "abc"}, plugin_root=plugin_root)

            self.assertFalse((code_workspace / "AGENTS.md").exists())

    def test_invalid_document_map_paths_fail(self) -> None:
        cases = [
            "/tmp/arch.md",
            "../arch.md",
            "sys/other/BACKEND_AGENTS.md",
        ]
        for invalid_path in cases:
            with self.subTest(invalid_path=invalid_path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                plugin_root = root / "plugin"
                code_workspace = root / "code"
                code_workspace.mkdir()
                write_sys_agents(
                    plugin_root,
                    "abc",
                    "\n".join(
                        [
                            "# abc",
                            "",
                            "## 文档地图",
                            f"- `{invalid_path}`: invalid",
                        ]
                    ),
                )

                with self.assertRaisesRegex(DevAgentsInitError, "invalid AGENTS.md document map path"):
                    init_dev_agents(code_workspace, env={"SYSTEM_ID": "abc"}, plugin_root=plugin_root)


class LoadSysAgentsTests(unittest.TestCase):
    def test_load_sys_agents_uses_system_id_without_project_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sys_agents(root / "plugin", "abc", "abc rules\n")
            workspace = root / "workspace"
            workspace.mkdir()

            with patch.dict(os.environ, {"SYSTEM_ID": "abc"}), patch(
                "hooks.load_sys_agents.get_sys_agents_md_path",
                return_value=source,
            ):
                result = load_sys_agents(workspace)

            self.assertTrue(result["ok"])
            self.assertFalse(result["skipped"])
            self.assertEqual(result["system_no"], "abc")
            self.assertEqual(result["content"], "abc rules\n")
            self.assertIn("sys/abc/AGENTS.md", result["message"])


if __name__ == "__main__":
    unittest.main()
