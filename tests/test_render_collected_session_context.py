"""Tests for the collector adapter without changing legacy renderer tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.render_collected_session_context import render  # noqa: E402


def _proc(payload, *, returncode=0, stderr=""):
    stdout = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _legacy_plugin_root() -> Path:
    root = Path(tempfile.mkdtemp())
    sys_dir = root / "sys"
    unit_dir = sys_dir / "LF3918"
    unit_dir.mkdir(parents=True)
    manifest = {
        "schemaVersion": "v1",
        "systems": [
            {
                "systemId": "LF39",
                "description": "外联系统",
                "agentsPath": "",
                "deployUnits": [
                    {
                        "deployUnitId": "LF39.18_wg_flow",
                        "description": "中台导航",
                        "agentsPath": "LF3918/description.md",
                    }
                ],
            }
        ],
    }
    (sys_dir / "agents.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (unit_dir / "description.md").write_text("# 旧知识库正文\n", encoding="utf-8")
    return root


class CollectedSessionContextTest(unittest.TestCase):
    def test_calls_list_then_deploy_and_injects_system_prompt(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs.get("cwd")))
            if command[-1] == "install":
                return _proc("")
            if "--listDeployUnits" in command:
                return _proc(["LF39.18_wg_flow"])
            return _proc(
                {
                    "systemPrompt": (
                        "<knowledge>\n"
                        "path: D:\\back\\LF39_Product_Knowledge\\architecture.md\n"
                        "type: Product Knowledge\n"
                        "title: 架构设计\n"
                        "</knowledge>"
                    )
                }
            )

        selected = [
            {
                "deployUnitId": "LF39.18_wg_flow",
                "localRepoPath": "D:\\repo\\wg-flow",
                "description": "中台导航",
            }
        ]
        with patch(
            "hooks.render_collected_session_context.subprocess.run", side_effect=fake_run
        ):
            result = render(
                selected,
                collector_script="collect-knowledge.js",
                knowledge_path="D:\\back\\LF39_Product_Knowledge",
                platform="win32",
            )

        self.assertTrue(result["ok"])
        self.assertIn("<knowledge>", result["sessionContext"])
        self.assertIn("title: 架构设计", result["sessionContext"])
        self.assertEqual(result["message"], "remote 1 / local 0 / 缺 0")
        self.assertEqual(result["agentmdLoadStatus"][0]["source"], "remote")
        self.assertEqual(calls[0][0][-1], "install")
        self.assertEqual(calls[0][1], str(Path("collect-knowledge.js").resolve().parent))
        self.assertEqual(
            [command for command, _cwd in calls[1:]],
            [
                [
                    "node",
                    "collect-knowledge.js",
                    "--listDeployUnits",
                    "--knowledgePath",
                    "D:\\back\\LF39_Product_Knowledge",
                ],
                [
                    "node",
                    "collect-knowledge.js",
                    "--deployUnit",
                    "LF39.18_wg_flow",
                    "--knowledgePath",
                    "D:\\back\\LF39_Product_Knowledge",
                ],
            ],
        )

    def test_list_interface_failure_delegates_to_legacy_renderer(self):
        plugin_root = _legacy_plugin_root()
        with patch(
            "hooks.render_collected_session_context.subprocess.run",
            return_value=_proc("", returncode=1, stderr="collector unavailable"),
        ):
            result = render(
                [{"deployUnitId": "LF39.18_wg_flow", "localRepoPath": ""}],
                plugin_root=plugin_root,
                collector_script="collect-knowledge.js",
            )

        self.assertTrue(result["ok"])
        self.assertIn("# 旧知识库正文", result["sessionContext"])
        self.assertIn("已回退旧逻辑", result["message"])
        self.assertTrue(result["agentmdLoadStatus"][0]["loaded"])

    def test_supported_unit_invalid_payload_falls_back_to_local_agents(self):
        local = Path(tempfile.mkdtemp())
        (local / "AGENTS.md").write_text("# 本地知识\n", encoding="utf-8")
        responses = [_proc(""), _proc(["U1"]), _proc({"unexpected": "value"})]
        with patch(
            "hooks.render_collected_session_context.subprocess.run", side_effect=responses
        ):
            result = render(
                [{"deployUnitId": "U1", "localRepoPath": str(local)}],
                collector_script="collect-knowledge.js",
            )

        self.assertIn("# 本地知识", result["sessionContext"])
        self.assertEqual(result["agentmdLoadStatus"][0]["source"], "local")
        self.assertTrue(result["agentmdLoadStatus"][0]["loaded"])
        self.assertIn("缺少非空 systemPrompt", result["agentmdLoadStatus"][0]["message"])

    def test_npm_install_failure_still_allows_collector(self):
        def fake_run(command, **_kwargs):
            if command[-1] == "install":
                return _proc("", returncode=1, stderr="registry unreachable")
            if "--listDeployUnits" in command:
                return _proc(["U1"])
            return _proc({"systemPrompt": "# 远端知识\n"})

        with patch(
            "hooks.render_collected_session_context.subprocess.run", side_effect=fake_run
        ):
            result = render(
                [{"deployUnitId": "U1", "localRepoPath": ""}],
                collector_script="collect-knowledge.js",
            )

        self.assertIn("# 远端知识", result["sessionContext"])
        self.assertEqual(result["agentmdLoadStatus"][0]["source"], "remote")

    def test_npm_install_failure_is_reported_when_collector_also_fails(self):
        plugin_root = _legacy_plugin_root()
        with patch(
            "hooks.render_collected_session_context.subprocess.run",
            return_value=_proc("", returncode=1, stderr="registry unreachable"),
        ):
            result = render(
                [{"deployUnitId": "LF39.18_wg_flow", "localRepoPath": ""}],
                plugin_root=plugin_root,
                collector_script="collect-knowledge.js",
            )

        self.assertIn("npm install 失败", result["message"])
        self.assertIn("已回退旧逻辑", result["message"])

    def test_empty_selection_does_not_start_collector(self):
        with patch("hooks.render_collected_session_context.subprocess.run") as run:
            result = render([])
        run.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["sessionContext"], "")

    def test_board_config_routes_session_hook_to_adapter(self):
        config = json.loads(
            (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        )
        for platform in ("darwin", "linux", "win32"):
            command = config["inspectCommands"][platform]["session_context_inject"]
            separator = "\\" if platform == "win32" else "/"
            self.assertIn("render_collected_session_context.py", command)
            self.assertIn(
                "--knowledge-collector ${{pluginPath}}{0}hooks{0}collect-knowledge.js".format(
                    separator
                ),
                command,
            )
            self.assertIn(
                "--knowledge-path ${{pluginPath}}{0}sys".format(separator),
                command,
            )


if __name__ == "__main__":
    unittest.main()
