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


def _workspace(body="# 当前工作区指令\n- 工作区约束\n"):
    """临时会话工作区目录；body 为 None 时不写 AGENTS.md（目录存在但无指令文件）。"""
    d = Path(tempfile.mkdtemp())
    if body is not None:
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
        self.assertIn("`<SCOPE>`", prompt)
        self.assertIn("`</SCOPE>`", prompt)
        self.assertIn('`<SYSTEM id="sys-LF39"', prompt)
        self.assertIn("`</SYSTEM>`", prompt)
        self.assertIn('`<UNIT id="unit-section"', prompt)  # 单元级整段只一对 <UNIT>
        self.assertIn("`</UNIT>`", prompt)
        self.assertIn("[LF39.18_Outservice](#unit-section)", prompt)  # 命中单元锚点指向单元级整段
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
        self.assertEqual(prompt.count('`<SYSTEM id="sys-LF39"'), 1)
        # serviceUnitId 锚点：有单元正文→指向单元级整段；无单元正文→回退指向系统段
        self.assertIn("[LF39.18_Outservice](#unit-section)", prompt)
        self.assertIn("[LF39.20_Inservice](#sys-LF39)", prompt)
        # 单元级整段只一对 <UNIT>（不再每单元一块）
        self.assertEqual(prompt.count("`<UNIT "), 1)
        # 两个单元都在适用范围
        self.assertIn("/repo/out", prompt)
        self.assertIn("/repo/in", prompt)
        # agentmdLoadStatus 只反映单元级：系统级文件不进状态；LF39.20 无独立 md 不产出条目
        status = res["agentmdLoadStatus"]
        self.assertFalse(any(s["path"] == "sys/LF3905/AGENTS.md" for s in status))
        self.assertEqual(len(status), 1)  # 仅 LF39.18 单元级
        self.assertEqual(status[0]["serviceUnitId"], "LF39.18_Outservice")

    def test_project_root_placeholder_replaced_system_only(self):
        # 系统级（②）正文里的 {project_root} → <pluginPath>/sys/<systemId>；单元级（③）不替换。
        tmp = Path(tempfile.mkdtemp())
        sysd = tmp / "sys"
        (sysd / "LF3905").mkdir(parents=True)
        (sysd / "LF3918").mkdir(parents=True)
        (sysd / "agents.manifest.json").write_text(
            json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8"
        )
        (sysd / "LF3905" / "AGENTS.md").write_text(
            "# 系统级\n后端入口: {project_root}/src/\n", encoding="utf-8"
        )
        (sysd / "LF3918" / "descition.md").write_text(
            "# 单元\n配置: {project_root}/conf\n", encoding="utf-8"
        )
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=tmp,
        )
        expected = str(sysd / "LF39")  # systemId 是 LF39（不是路径段 LF3905）
        prompt = res["sessionContext"]
        # 系统级已替换
        self.assertIn(f"后端入口: {expected}/src/", prompt)
        # 单元级占位符原样保留
        self.assertIn("配置: {project_root}/conf", prompt)

    def test_multiple_systems_both_injected(self):
        res = render(
            [
                {"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/a"},
                {"serviceUnitId": "LA64.05_UEXgateway", "localRepoPath": "/b"},
            ],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        self.assertIn('`<SYSTEM id="sys-LF39"', prompt)
        self.assertIn('`<SYSTEM id="sys-LA64"', prompt)
        # LA64.05 无独立 description.md → 锚点回退到系统段
        self.assertIn("[LA64.05_UEXgateway](#sys-LA64)", prompt)

    def test_tags_are_backtick_wrapped_and_blank_line_separated(self):
        # 每个标签用反引号包成 inline code、前后留空行：渲染时各自独占一个 <p><code> 块，
        # 字符原样保留、块间换行；否则裸标签会被当原始 HTML 折叠到一行 / 整块不解析。
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        # 反引号包裹 + 开标签后、闭标签前都有空行
        self.assertIn("`<SCOPE>`\n\n", prompt)
        self.assertIn("\n\n`</SCOPE>`", prompt)
        self.assertRegex(prompt, r'`<SYSTEM id="sys-LF39"[^`]*>`\n\n')
        self.assertIn("\n\n`</SYSTEM>`", prompt)
        self.assertRegex(prompt, r'`<UNIT id="unit-section"[^`]*>`\n\n')
        self.assertIn("\n\n`</UNIT>`", prompt)
        # 标签不会与紧随的 Markdown 标题直接相连（杜绝 "<SCOPE> ## 适用范围" 同块）
        self.assertNotIn("`<SCOPE>`\n## 适用范围", prompt)


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
        self.assertIn("[GHOST.1](#unit-section)", res["sessionContext"])  # 锚点指向单元级整段
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
        self.assertNotIn("#unit-section", res["sessionContext"])  # 无内容→不加锚点链接

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


class RenderWorkspaceTest(unittest.TestCase):
    def test_workspace_only_no_selection_injects_as_unit(self):
        # 未选任何单元，但工作区有 AGENTS.md → 注入为唯一一对 <UNIT id="unit-section">，无 SCOPE/SYSTEM。
        ws = _workspace()
        res = render([], plugin_root=_plugin_root(), session_workspace_path=ws)
        self.assertTrue(res["ok"])
        prompt = res["sessionContext"]
        self.assertIn('`<UNIT id="unit-section" unit="单元级">`', prompt)
        self.assertIn("`</UNIT>`", prompt)
        self.assertIn("**当前工作区指令**", prompt)  # 工作区指令的加粗标签
        self.assertIn("# 当前工作区指令", prompt)  # AGENTS.md 正文
        self.assertNotIn("`<WORKSPACE", prompt)  # 不再用 WORKSPACE 标签
        self.assertNotIn("`<SYSTEM", prompt)  # 未选单元 → 无系统段
        self.assertEqual(res["agentmdLoadStatus"], [])

    def test_workspace_appears_in_scope_table(self):
        # 工作区指令在适用范围表里占一行：引用范围=当前工作区指令、代码地址=工作区路径、锚点指向 unit-section。
        ws = _workspace()
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
            session_workspace_path=ws,
        )
        prompt = res["sessionContext"]
        self.assertIn("[当前工作区](#unit-section)", prompt)  # 工作区行的链接单元格
        self.assertIn("当前工作区指令", prompt)  # 引用范围列
        self.assertIn(f"| {ws} |", prompt)  # 代码地址列 = 工作区路径
        # 工作区行排在选中单元行之前
        ws_row = prompt.index("[当前工作区](#unit-section)")
        unit_row = prompt.index("[LF39.18_Outservice](#unit-section)")
        self.assertLess(ws_row, unit_row)

    def test_workspace_only_no_selection_has_scope_row(self):
        # 未选单元但工作区有正文：适用范围表仍渲染、且含工作区那一行。
        ws = _workspace()
        res = render([], plugin_root=_plugin_root(), session_workspace_path=ws)
        prompt = res["sessionContext"]
        self.assertIn("`<SCOPE>`", prompt)
        self.assertIn("[当前工作区](#unit-section)", prompt)

    def test_workspace_path_empty_is_noop_when_no_selection(self):
        res = render([], plugin_root=_plugin_root(), session_workspace_path="")
        self.assertEqual(res["sessionContext"], "")

    def test_workspace_missing_file_skips_section(self):
        # 目录存在但无 AGENTS.md → 不注入该段，退回「未选择」空注入。
        ws = _workspace(body=None)
        res = render([], plugin_root=_plugin_root(), session_workspace_path=ws)
        self.assertEqual(res["sessionContext"], "")

    def test_workspace_blank_file_skips_section(self):
        ws = _workspace(body="   \n\n")
        res = render([], plugin_root=_plugin_root(), session_workspace_path=ws)
        self.assertEqual(res["sessionContext"], "")

    def test_workspace_is_first_inside_single_unit_block(self):
        # 单元级只一对 <UNIT>；块内工作区指令排在选中单元正文之前，整段在系统级之后。
        ws = _workspace()
        res = render(
            [{"serviceUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
            session_workspace_path=ws,
        )
        prompt = res["sessionContext"]
        self.assertEqual(prompt.count("`<UNIT "), 1)  # 只一对 <UNIT>
        sys_at = prompt.index('`<SYSTEM id="sys-LF39"')
        unit_tag_at = prompt.index('`<UNIT id="unit-section"')
        ws_label_at = prompt.index("**当前工作区指令**")
        unit_label_at = prompt.index("**LF39.18_Outservice")
        self.assertLess(sys_at, unit_tag_at)  # 系统级在单元级整段之前
        self.assertLess(unit_tag_at, ws_label_at)  # 工作区指令在 <UNIT> 开标签之后
        self.assertLess(ws_label_at, unit_label_at)  # 工作区指令排在选中单元之前


if __name__ == "__main__":
    unittest.main()
