"""Regression coverage for collector -> manifest -> local knowledge loading."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.render_collected_session_context import (  # noqa: E402
    KnowledgeCollectorError,
    render,
)

COLLECTOR_MODULE = "hooks.render_collected_session_context"
LOGGED_UNIT_ID = "LF39.05_bccompliancemng"


class CollectorManifestFallbackTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.plugin_root = self.root / "plugin"
        self.knowledge_root = self.plugin_root / "sys"
        self.knowledge_root.mkdir(parents=True)
        self.local_root = self.root / "local-repo"
        self.local_root.mkdir()

    def write(self, root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def manifest(self, units, *, root=None, system_content=None):
        knowledge_root = root or self.knowledge_root
        payload = {
            "schemaVersion": "v1",
            "systems": [
                {
                    "systemId": "LF39",
                    "description": "零售客户经营",
                    "agentsPath": "LF39/AGENTS.md",
                    "deployUnits": [
                        {
                            "deployUnitId": uid,
                            "description": f"知识配置 {uid}",
                            "agentsPath": relative,
                        }
                        for uid, relative in units
                    ],
                }
            ],
        }
        self.write(
            knowledge_root,
            "agents.manifest.json",
            json.dumps(payload, ensure_ascii=False),
        )
        if system_content is not None:
            self.write(knowledge_root, "LF39/AGENTS.md", system_content)

    def selection(self, *unit_ids):
        return [
            {
                "deployUnitId": uid,
                "description": f"选中 {uid}",
                "localRepoPath": str(self.local_root),
            }
            for uid in unit_ids
        ]

    def render_with_collector(
        self, selected, *, supported=(), prompts=None, list_error=None, **kwargs
    ):
        prompts = prompts or {}

        def deploy_prompt(_script, uid, **_kwargs):
            response = prompts[uid]
            if isinstance(response, Exception):
                raise response
            return response

        with patch(
            f"{COLLECTOR_MODULE}.list_supported_deploy_units",
            return_value=list(supported),
            side_effect=list_error,
        ) as listing, patch(
            f"{COLLECTOR_MODULE}._deploy_unit_prompt", side_effect=deploy_prompt
        ) as deploy, contextlib.redirect_stderr(io.StringIO()):
            result = render(
                selected,
                plugin_root=self.plugin_root,
                collector_script="test-collector.js",
                **kwargs,
            )
        return result, listing, deploy

    def assert_remote(self, result, count=1):
        self.assertTrue(result["ok"])
        self.assertIn(f"remote {count} / local 0 / 缺 0", result["message"])
        self.assertEqual(len(result["agentmdLoadStatus"]), count)
        for status in result["agentmdLoadStatus"]:
            self.assertTrue(status["loaded"], status["message"])
            self.assertEqual(status["source"], "remote")

    def test_logged_unit_missing_from_ten_collector_ids_uses_manifest(self):
        self.manifest(
            [(LOGGED_UNIT_ID, "LF39/compliance.md")],
            system_content="合规管理系统知识",
        )
        unit_path = self.write(
            self.knowledge_root, "LF39/compliance.md", "合规管理微服务知识"
        )
        self.write(self.local_root, "AGENTS.md", "不应使用的本地知识")

        result, _listing, deploy = self.render_with_collector(
            self.selection(LOGGED_UNIT_ID),
            supported=[f"HEADER_UNIT_{index}" for index in range(10)],
        )

        self.assert_remote(result)
        self.assertEqual(result["agentmdLoadStatus"][0]["path"], str(unit_path))
        self.assertIn("合规管理系统知识", result["sessionContext"])
        self.assertIn("合规管理微服务知识", result["sessionContext"])
        self.assertNotIn("不应使用的本地知识", result["sessionContext"])
        deploy.assert_not_called()

    def test_empty_collector_list_still_loads_manifest(self):
        self.manifest([("U1", "LF39/unit.md")])
        self.write(self.knowledge_root, "LF39/unit.md", "空列表时的清单知识")

        result, _listing, deploy = self.render_with_collector(self.selection("U1"))

        self.assert_remote(result)
        self.assertIn("空列表时的清单知识", result["sessionContext"])
        deploy.assert_not_called()

    def test_deploy_interface_failure_uses_manifest_before_local(self):
        self.manifest([("U1", "LF39/unit.md")])
        self.write(self.knowledge_root, "LF39/unit.md", "接口失败后的清单知识")
        self.write(self.local_root, "AGENTS.md", "不应使用的本地知识")

        result, _listing, deploy = self.render_with_collector(
            self.selection("U1"),
            supported=["U1"],
            prompts={"U1": KnowledgeCollectorError("应用类型接口调用失败")},
        )

        self.assert_remote(result)
        self.assertIn("接口失败后的清单知识", result["sessionContext"])
        self.assertNotIn("不应使用的本地知识", result["sessionContext"])
        deploy.assert_called_once()

    def test_mixed_sources_keep_collector_content_and_deduplicate_system(self):
        self.manifest(
            [("COLLECTOR", "LF39/collector.md"), ("U1", "LF39/u1.md"), ("U2", "LF39/u2.md")],
            system_content="共用系统知识只出现一次",
        )
        self.write(self.knowledge_root, "LF39/collector.md", "已被接口覆盖的清单正文")
        self.write(self.knowledge_root, "LF39/u1.md", "第一个清单单元正文")
        self.write(self.knowledge_root, "LF39/u2.md", "第二个清单单元正文")

        result, _listing, deploy = self.render_with_collector(
            self.selection("COLLECTOR", "U1", "U2"),
            supported=["COLLECTOR"],
            prompts={"COLLECTOR": "必须保留的 collector 正文"},
        )

        self.assert_remote(result, count=3)
        prompt = result["sessionContext"]
        for content in (
            "必须保留的 collector 正文",
            "第一个清单单元正文",
            "第二个清单单元正文",
        ):
            self.assertEqual(prompt.count(content), 1)
        self.assertEqual(prompt.count("共用系统知识只出现一次"), 1)
        self.assertEqual(prompt.count('<SYSTEM id="sys-LF39"'), 1)
        self.assertNotIn("已被接口覆盖的清单正文", prompt)
        deploy.assert_called_once()

    def test_collector_success_takes_priority_over_manifest_and_local(self):
        self.manifest(
            [("U1", "LF39/unit.md")], system_content="不应注入的清单系统知识"
        )
        self.write(self.knowledge_root, "LF39/unit.md", "不应注入的清单单元知识")
        self.write(self.local_root, "AGENTS.md", "不应注入的本地知识")

        result, _listing, deploy = self.render_with_collector(
            self.selection("U1"), supported=["U1"], prompts={"U1": "接口优先知识"}
        )

        self.assert_remote(result)
        self.assertIn("接口优先知识", result["sessionContext"])
        self.assertNotIn("不应注入", result["sessionContext"])
        deploy.assert_called_once()

    def test_system_only_manifest_entry_counts_as_remote_and_links_system(self):
        self.manifest([("U1", "")], system_content="无单元文档时使用系统知识")

        result, _listing, _deploy = self.render_with_collector(self.selection("U1"))

        self.assert_remote(result)
        self.assertIn("无单元文档时使用系统知识", result["sessionContext"])
        self.assertIn('<SYSTEM id="sys-LF39"', result["sessionContext"])
        self.assertIn("[U1](#sys-LF39)", result["sessionContext"])

    def test_missing_or_invalid_manifest_and_missing_files_continue_to_local(self):
        self.write(self.local_root, "AGENTS.md", "最后一级本地知识")
        scenarios = ("missing_manifest", "invalid_json", "invalid_schema", "missing_files")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                knowledge_root = self.root / scenario
                knowledge_root.mkdir()
                if scenario == "invalid_json":
                    self.write(knowledge_root, "agents.manifest.json", "{not json")
                elif scenario == "invalid_schema":
                    self.write(knowledge_root, "agents.manifest.json", '{"systems": {}}')
                elif scenario == "missing_files":
                    self.manifest([("U1", "LF39/missing.md")], root=knowledge_root)

                result, _listing, deploy = self.render_with_collector(
                    self.selection("U1"), knowledge_path=str(knowledge_root)
                )

                self.assertTrue(result["ok"])
                self.assertEqual(result["message"], "remote 0 / local 1 / 缺 0")
                status = result["agentmdLoadStatus"][0]
                self.assertTrue(status["loaded"])
                self.assertEqual(status["source"], "local")
                self.assertIn("最后一级本地知识", result["sessionContext"])
                deploy.assert_not_called()

    def test_explicit_knowledge_root_controls_files_and_windows_placeholder_paths(self):
        actual_root = self.root / "external-knowledge"
        self.manifest(
            [("U1", "LF39/unit.md")],
            root=actual_root,
            system_content="系统引用 `{plugin_root}/LF39/architecture.md`",
        )
        unit_path = self.write(
            actual_root, "LF39/unit.md", "单元引用 `{plugin_root}/LF39/domain.md`"
        )
        self.manifest(
            [("U1", "LF39/unit.md")], system_content="错误默认根的系统正文"
        )
        self.write(self.knowledge_root, "LF39/unit.md", "错误默认根的单元正文")

        result, listing, _deploy = self.render_with_collector(
            self.selection("U1"), knowledge_path=str(actual_root), platform="win32"
        )

        self.assert_remote(result)
        prompt = result["sessionContext"]
        self.assertIn(str(actual_root / "LF39/architecture.md").replace("/", "\\"), prompt)
        self.assertIn(str(actual_root / "LF39/domain.md").replace("/", "\\"), prompt)
        self.assertNotIn("{plugin_root}", prompt)
        self.assertNotIn("错误默认根", prompt)
        self.assertEqual(
            result["agentmdLoadStatus"][0]["path"], str(unit_path).replace("/", "\\")
        )
        self.assertEqual(listing.call_args.kwargs["knowledge_path"], str(actual_root))

    def test_list_failure_legacy_fallback_uses_explicit_knowledge_root(self):
        actual_root = self.root / "external-knowledge"
        self.manifest(
            [("U1", "LF39/unit.md")],
            root=actual_root,
            system_content="外部系统引用 `{plugin_root}/LF39/architecture.md`",
        )
        unit_path = self.write(
            actual_root, "LF39/unit.md", "外部单元引用 `{plugin_root}/LF39/domain.md`"
        )
        self.manifest(
            [("U1", "LF39/unit.md")], system_content="错误默认根的系统正文"
        )
        self.write(self.knowledge_root, "LF39/unit.md", "错误默认根的单元正文")

        result, _listing, deploy = self.render_with_collector(
            self.selection("U1"),
            list_error=KnowledgeCollectorError("collector unavailable"),
            knowledge_path=str(actual_root),
            platform="win32",
        )

        self.assert_remote(result)
        prompt = result["sessionContext"]
        self.assertIn("外部系统引用", prompt)
        self.assertIn("外部单元引用", prompt)
        self.assertIn(str(actual_root / "LF39/architecture.md").replace("/", "\\"), prompt)
        self.assertIn(str(actual_root / "LF39/domain.md").replace("/", "\\"), prompt)
        self.assertNotIn("错误默认根", prompt)
        self.assertNotIn("{plugin_root}", prompt)
        self.assertEqual(
            result["agentmdLoadStatus"][0]["path"], str(unit_path).replace("/", "\\")
        )
        deploy.assert_not_called()

    def test_duplicate_manifest_ids_are_rejected_before_collector_or_local(self):
        self.manifest(
            [("U1", "LF39/unit.md"), ("U1", "LF39/other.md")],
            system_content="不能通过重复清单加载的系统知识",
        )
        self.write(self.knowledge_root, "LF39/unit.md", "不能通过重复清单加载的单元知识")
        self.write(self.local_root, "AGENTS.md", "不能绕过重复校验的本地知识")

        result, _listing, deploy = self.render_with_collector(
            self.selection("U1"),
            supported=["U1"],
            prompts={"U1": "不能绕过重复校验的接口知识"},
        )

        self.assertTrue(result["ok"])
        self.assertIn("remote 0 / local 0 / 缺 1", result["message"])
        status = result["agentmdLoadStatus"][0]
        self.assertFalse(status["loaded"])
        self.assertIn("重复", status["message"])
        self.assertNotIn("不能", result["sessionContext"])
        deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
