"""Tests for hooks/render_session_context.py: selected units -> sessionContext (v2)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.render_session_context import _parse_selected, render  # noqa: E402


MANIFEST = {
    "schemaVersion": "v1",
    "systems": [
        {
            "systemId": "LF39",
            "description": "外联服务系统",
            "agentsPath": "LF3905/AGENTS.md",
            "serviceUnits": [
                {
                    "serviceUnitId": "LF39.18_Outservice",
                    "description": "外联出站服务",
                    "agentsPath": "LF3918/descition.md",
                },
                {"serviceUnitId": "LF39.20_Inservice", "description": "外联入站服务", "agentsPath": ""},
            ],
        },
        {
            "systemId": "LA64",
            "description": "UEX 网关系统",
            "agentsPath": "shared/gateway/AGENTS.md",
            "serviceUnits": [
                {"serviceUnitId": "LA64.05_UEXgateway", "description": "UEX 网关", "agentsPath": ""}
            ],
        },
    ],
}

# key -> (相对 sys/ 的路径, 文件内容)
REMOTE_FILES = {
    "LF39.system": ("LF3905/AGENTS.md", "# LF39 系统级\n- 系统约束\n"),
    "LF39.18.unit": ("LF3918/descition.md", "# LF39.18 单元\n- 出站细则\n"),
    "LA64.system": ("shared/gateway/AGENTS.md", "# LA64 系统级\n- 网关约束\n"),
}


def _plugin_root(write_remote=("LF39.system", "LF39.18.unit", "LA64.system"), *, manifest=MANIFEST):
    tmp = Path(tempfile.mkdtemp())
    sysd = tmp / "sys"
    sysd.mkdir(parents=True)
    if manifest is not None:
        (sysd / "agents.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for key in write_remote:
        rel, body = REMOTE_FILES[key]
        path = sysd / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp


def _local_repo(body="# 本地知识库\n- 本地约束\n"):
    d = Path(tempfile.mkdtemp())
    (d / "AGENTS.md").write_text(body, encoding="utf-8")
    return str(d)


class ParseSelectedTest(unittest.TestCase):
    def test_empty_and_blank(self):
        self.assertEqual(_parse_selected(None), [])
        self.assertEqual(_parse_selected(""), [])
        self.assertEqual(_parse_selected("   "), [])

    def test_valid(self):
        out = _parse_selected('[{"serviceUnitId":"U1","localRepoPath":"/r"}]')
        self.assertEqual(out, [{"serviceUnitId": "U1", "localRepoPath": "/r"}])

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            _parse_selected("not-json")

    def test_not_a_list_raises(self):
        with self.assertRaises(ValueError):
            _parse_selected('{"serviceUnitId":"U1"}')

    def test_missing_service_unit_id_raises(self):
        with self.assertRaises(ValueError):
            _parse_selected('[{"localRepoPath":"/r"}]')


class RenderShapeTest(unittest.TestCase):
    def test_output_uses_new_fields_only(self):
        res = render([], plugin_root=_plugin_root())
        self.assertIn("sessionContext", res)
        self.assertIn("agentmdLoadStatus", res)
        self.assertNotIn("inlineSystemPrompt", res)  # 破坏性切换，不留旧字段

    def test_empty_selection_is_noop(self):
        res = render([], plugin_root=_plugin_root())
        self.assertTrue(res["ok"])
        self.assertEqual(res["sessionContext"], "")
        self.assertEqual(res["agentmdLoadStatus"], [])


class RenderRemoteTest(unittest.TestCase):
    def test_single_unit_injects_system_and_unit_remote(self):
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
        )
        self.assertTrue(res["ok"])
        prompt = res["sessionContext"]
        # ① 适用范围
        self.assertIn("# 适用范围", prompt)
        self.assertIn("外联出站服务", prompt)
        self.assertIn("/repo/out", prompt)
        self.assertIn("LF39.18_Outservice", prompt)
        # ② 系统级 + ③ 单元级（三个层次各用 XML 标签外包，不再用 ## <a id> / ===== 围栏）
        self.assertIn("# LF39 系统级", prompt)  # 被嵌入 md 自带标题，现包在标签内
        self.assertIn("# LF39.18 单元", prompt)
        self.assertNotIn("=====", prompt)
        self.assertIn("<applicable_scope>", prompt)
        self.assertIn("</applicable_scope>", prompt)
        self.assertIn('<system_agents id="sys-LF39"', prompt)
        self.assertIn("</system_agents>", prompt)
        self.assertIn('<unit_description id="unit-LF39.18_Outservice"', prompt)
        self.assertIn("</unit_description>", prompt)
        self.assertIn("[LF39.18_Outservice](#unit-LF39.18_Outservice)", prompt)  # 锚点链接仍指向 id 属性
        # agentmdLoadStatus 只反映单元级：仅一条（系统级不进状态），remote 成功
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertTrue(status[0]["loaded"] and status[0]["source"] == "remote")
        self.assertEqual(status[0]["path"], "sys/LF3918/descition.md")
        # 系统级文件不出现在状态里
        self.assertFalse(any(s["path"] == "sys/LF3905/AGENTS.md" for s in status))

    def test_two_units_same_system_system_md_pasted_once_not_in_status(self):
        res = render(
            [
                {"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"},
                {"serviceUnitId": "LF39.20_Inservice", "localRepoPath": "/repo/in"},
            ],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        # 正文系统段只拼一次（按 systemId 去重）
        self.assertEqual(prompt.count('<system_agents id="sys-LF39"'), 1)
        # serviceUnitId 锚点：有单元段→指向单元段；无单元段→回退指向系统段
        self.assertIn("[LF39.18_Outservice](#unit-LF39.18_Outservice)", prompt)
        self.assertIn("[LF39.20_Inservice](#sys-LF39)", prompt)
        # 两个单元都在适用范围
        self.assertIn("/repo/out", prompt)
        self.assertIn("/repo/in", prompt)
        # agentmdLoadStatus 只反映单元级：系统级文件不进状态；LF39.20 无独立 md 不产出条目
        status = res["agentmdLoadStatus"]
        self.assertFalse(any(s["path"] == "sys/LF3905/AGENTS.md" for s in status))
        self.assertEqual(len(status), 1)  # 仅 LF39.18 单元级
        self.assertEqual(status[0]["serviceUnitId"], "LF39.18_Outservice")

    def test_multiple_systems_both_injected(self):
        res = render(
            [
                {"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/a"},
                {"serviceUnitId": "LA64.05_UEXgateway", "localRepoPath": "/b"},
            ],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        self.assertIn('<system_agents id="sys-LF39"', prompt)
        self.assertIn('<system_agents id="sys-LA64"', prompt)
        # LA64.05 无独立 description.md → 锚点回退到系统段
        self.assertIn("[LA64.05_UEXgateway](#sys-LA64)", prompt)


class RenderLocalFallbackTest(unittest.TestCase):
    def test_unit_remote_missing_falls_back_to_local(self):
        local = _local_repo()
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": local}],
            plugin_root=_plugin_root(write_remote=("LF39.system",)),  # 仅系统级远端存在
        )
        status = res["agentmdLoadStatus"]
        # agentmdLoadStatus 只反映单元级：系统级 remote 成功仍不进状态；
        # 单元级 remote 缺失→local 兜底成功
        self.assertEqual(len(status), 1)
        self.assertEqual((status[0]["source"], status[0]["loaded"]), ("local", True))
        self.assertIn("# LF39 系统级", res["sessionContext"])  # 系统段仍被拼入正文
        self.assertIn("# 本地知识库", res["sessionContext"])

    def test_unknown_unit_local_loaded(self):
        local = _local_repo()
        res = render(
            [{"serviceUnitId": "GHOST.1", "localRepoPath": local}],
            plugin_root=_plugin_root(),
        )
        self.assertTrue(res["ok"])
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertTrue(status[0]["loaded"])
        self.assertEqual(status[0]["source"], "local")
        self.assertTrue(status[0]["path"].endswith("AGENTS.md"))
        self.assertIn("[GHOST.1](#unit-GHOST.1)", res["sessionContext"])  # 锚点指向其本地段
        self.assertIn("# 本地知识库", res["sessionContext"])

    def test_unknown_unit_local_missing_reports_not_loaded(self):
        res = render(
            [{"serviceUnitId": "GHOST.1", "localRepoPath": "/no/such/dir"}],
            plugin_root=_plugin_root(),
        )
        self.assertTrue(res["ok"])  # 绝不中断
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertFalse(status[0]["loaded"])
        self.assertEqual(status[0]["source"], "local")
        self.assertEqual(status[0]["message"], "file not exist")
        self.assertIn("缺 1", res["message"])
        self.assertNotIn("#unit-GHOST.1", res["sessionContext"])  # 无内容→不加锚点链接

    def test_system_not_in_status_only_unit_reported(self):
        # 命中清单的单元：系统级 remote 缺失既不走 local 兜底、也不进 agentmdLoadStatus；
        # 状态只反映单元级（remote 缺失→local 兜底成功）。
        local = _local_repo()
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": local}],
            plugin_root=_plugin_root(write_remote=()),  # 无任何远端文件
        )
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)  # 只有单元级一条
        self.assertEqual(status[0]["source"], "local")
        self.assertTrue(status[0]["loaded"])
        # 没有任何 remote 来源（系统级）的状态条目
        self.assertFalse(any(s["source"] == "remote" for s in status))

    def test_system_and_unit_both_missing_reports_only_unit(self):
        # 系统级 + 单元级 remote 都缺、local 也无：状态只剩单元级一条 local 未命中。
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/no/such/dir"}],
            plugin_root=_plugin_root(write_remote=()),
        )
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertEqual((status[0]["source"], status[0]["loaded"]), ("local", False))
        self.assertIn("缺 1", res["message"])

    def test_manifest_absent_degrades_to_local(self):
        local = _local_repo()
        tmp = Path(tempfile.mkdtemp())
        (tmp / "sys").mkdir()  # 无 manifest
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": local}],
            plugin_root=tmp,
        )
        self.assertTrue(res["ok"])
        self.assertIn("LF39.18_Outservice", res["sessionContext"])  # 适用范围仍绑定
        self.assertIn("# 本地知识库", res["sessionContext"])  # 走 local 兜底
        self.assertEqual(res["agentmdLoadStatus"][0]["source"], "local")


if __name__ == "__main__":
    unittest.main()
