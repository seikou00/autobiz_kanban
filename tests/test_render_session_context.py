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

from hooks.render_session_context import (  # noqa: E402
    _heading_slug,
    _parse_selected,
    _unit_heading_label,
    render,
)


MANIFEST = {
    "schemaVersion": "v1",
    "systems": [
        {
            "systemId": "LF39",
            "description": "外联服务系统",
            "agentsPath": "LF3905/AGENTS.md",
            "deployUnits": [
                {
                    "deployUnitId": "LF39.18_Outservice",
                    "description": "外联出站服务",
                    "agentsPath": "LF3918/descition.md",
                },
                {"deployUnitId": "LF39.20_Inservice", "description": "外联入站服务", "agentsPath": ""},
            ],
        },
        {
            "systemId": "LA64",
            "description": "UEX 网关系统",
            "agentsPath": "shared/gateway/AGENTS.md",
            "deployUnits": [
                {"deployUnitId": "LA64.05_UEXgateway", "description": "UEX 网关", "agentsPath": ""}
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


def _workspace(body="# 会话工作区指令\n- 工作区约束\n"):
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
        out = _parse_selected('[{"deployUnitId":"U1","localRepoPath":"/r"}]')
        self.assertEqual(out, [{"deployUnitId": "U1", "localRepoPath": "/r", "description": ""}])

    def test_valid_with_description_and_extra_fields_ignored(self):
        # UI 实际传参：带 description，并含 deployUnitIdMapping 等脚本不关心的字段（原样忽略）。
        out = _parse_selected(
            '[{"deployUnitIdMapping":"abc-123","deployUnitId":"LX34","localRepoPath":"/d","description":"测试"}]'
        )
        self.assertEqual(out, [{"deployUnitId": "LX34", "localRepoPath": "/d", "description": "测试"}])

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            _parse_selected("not-json")

    def test_not_a_list_raises(self):
        with self.assertRaises(ValueError):
            _parse_selected('{"deployUnitId":"U1"}')

    def test_missing_deploy_unit_id_raises(self):
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
        pr = _plugin_root()
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=pr,
        )
        self.assertTrue(res["ok"])
        prompt = res["sessionContext"]
        # ① 适用范围
        self.assertIn("# 适用范围", prompt)
        self.assertIn("外联出站服务", prompt)
        self.assertIn("/repo/out", prompt)
        self.assertIn("LF39.18_Outservice", prompt)
        # ② 系统级 + ③ 单元级（三个层次各用裸 XML 标签外包，不再反引号包成 inline code）
        self.assertIn("# LF39 系统级", prompt)  # 被嵌入 md 自带标题，现包在标签内
        self.assertIn("# LF39.18 单元", prompt)
        self.assertNotIn("=====", prompt)
        self.assertIn("<SCOPE>", prompt)
        self.assertIn("</SCOPE>", prompt)
        self.assertIn('<SYSTEM id="sys-LF39"', prompt)
        self.assertIn("</SYSTEM>", prompt)
        self.assertIn('<UNIT id="unit-section"', prompt)  # 单元级整段只一对 <UNIT>
        self.assertIn("</UNIT>", prompt)
        # 命中单元：③ 里有 ## deployUnitId（描述）标题，① 表锚点指向它的 slug
        label = _unit_heading_label("LF39.18_Outservice", "外联出站服务")
        self.assertIn(f"## {label}", prompt)
        self.assertIn(f"[LF39.18_Outservice](#{_heading_slug(label)})", prompt)
        # agentmdLoadStatus 只反映单元级：仅一条（系统级不进状态），remote 成功
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertTrue(status[0]["loaded"] and status[0]["source"] == "remote")
        # remote 路径为绝对（原生分隔符），前缀为 <pluginPath>/sys
        self.assertEqual(status[0]["path"], str(pr / "sys" / "LF3918" / "descition.md"))
        # 系统级文件不出现在状态里
        self.assertFalse(any(s["path"] == str(pr / "sys" / "LF3905" / "AGENTS.md") for s in status))

    def test_two_units_same_system_system_md_pasted_once_not_in_status(self):
        pr = _plugin_root()
        res = render(
            [
                {"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"},
                {"deployUnitId": "LF39.20_Inservice", "localRepoPath": "/repo/in"},
            ],
            plugin_root=pr,
        )
        prompt = res["sessionContext"]
        # 正文系统段只拼一次（按 systemId 去重）
        self.assertEqual(prompt.count('<SYSTEM id="sys-LF39"'), 1)
        # deployUnitId 锚点：有单元正文→指向该单元 ## 标题 slug；无单元正文→回退指向系统段
        unit_anchor = _heading_slug(_unit_heading_label("LF39.18_Outservice", "外联出站服务"))
        self.assertIn(f"[LF39.18_Outservice](#{unit_anchor})", prompt)
        self.assertIn("[LF39.20_Inservice](#sys-LF39)", prompt)
        # 单元级整段只一对 <UNIT>（不再每单元一块）
        self.assertEqual(prompt.count("<UNIT "), 1)
        # 两个单元都在适用范围
        self.assertIn("/repo/out", prompt)
        self.assertIn("/repo/in", prompt)
        # agentmdLoadStatus 只反映单元级：系统级文件不进状态；LF39.20 无独立 md 不产出条目
        status = res["agentmdLoadStatus"]
        self.assertFalse(any(s["path"] == str(pr / "sys" / "LF3905" / "AGENTS.md") for s in status))
        self.assertEqual(len(status), 1)  # 仅 LF39.18 单元级
        self.assertEqual(status[0]["deployUnitId"], "LF39.18_Outservice")

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
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
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
                {"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/a"},
                {"deployUnitId": "LA64.05_UEXgateway", "localRepoPath": "/b"},
            ],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        self.assertIn('<SYSTEM id="sys-LF39"', prompt)
        self.assertIn('<SYSTEM id="sys-LA64"', prompt)
        # LA64.05 无独立 description.md → 锚点回退到系统段
        self.assertIn("[LA64.05_UEXgateway](#sys-LA64)", prompt)

    def test_tags_are_raw_html_and_blank_line_separated(self):
        # 标签为裸标签（无反引号）、前后留空行：裸标签使 id 成真 HTML 锚点；空行让裸标签各自成
        # HTML 块，紧随其后的 ## 标题照常按 Markdown 渲染、不被折进 HTML 块。
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        # 裸标签 + 开标签后、闭标签前都有空行
        self.assertIn("<SCOPE>\n\n", prompt)
        self.assertIn("\n\n</SCOPE>", prompt)
        self.assertRegex(prompt, r'<SYSTEM id="sys-LF39"[^>]*>\n\n')
        self.assertIn("\n\n</SYSTEM>", prompt)
        self.assertRegex(prompt, r'<UNIT id="unit-section"[^>]*>\n\n')
        self.assertIn("\n\n</UNIT>", prompt)
        # 不再用反引号把标签包成 inline code
        self.assertNotIn("`<SCOPE>`", prompt)
        self.assertNotIn("`<UNIT", prompt)
        self.assertNotIn("`<SYSTEM", prompt)
        # 标签与紧随的 Markdown 标题之间留空行（杜绝 "<SCOPE>\n## 适用范围" 同块）
        self.assertNotIn("<SCOPE>\n## 适用范围", prompt)


class RenderLocalFallbackTest(unittest.TestCase):
    def test_unit_remote_missing_falls_back_to_local(self):
        local = _local_repo()
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": local}],
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
            [{"deployUnitId": "GHOST.1", "localRepoPath": local}],
            plugin_root=_plugin_root(),
        )
        self.assertTrue(res["ok"])
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertTrue(status[0]["loaded"])
        self.assertEqual(status[0]["source"], "local")
        self.assertTrue(status[0]["path"].endswith("AGENTS.md"))
        prompt = res["sessionContext"]
        self.assertIn("## GHOST.1", prompt)  # 未命中清单单元也产出 ## 标题
        self.assertIn(f"[GHOST.1](#{_heading_slug('GHOST.1')})", prompt)  # 锚点指向该标题 slug
        self.assertIn("# 本地知识库", prompt)

    def test_unknown_unit_local_missing_reports_not_loaded(self):
        res = render(
            [{"deployUnitId": "GHOST.1", "localRepoPath": "/no/such/dir"}],
            plugin_root=_plugin_root(),
        )
        self.assertTrue(res["ok"])  # 绝不中断
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertFalse(status[0]["loaded"])
        self.assertEqual(status[0]["source"], "local")
        self.assertEqual(status[0]["message"], "未找到知识库 AGENTS.md 和本地 AGENTS.md")
        self.assertIn("缺 1", res["message"])
        self.assertNotIn("[GHOST.1](#", res["sessionContext"])  # 无内容→不加锚点链接

    def test_unknown_unit_uses_ui_description_as_ref(self):
        # 远端无配置（未命中清单）+ 本地有 AGENTS.md → 引用范围用 UI 传入的 description，
        # 不再是「(未匹配知识库)」；③ 标题与 ① 锚点都用该 description（三处同源）。
        local = _local_repo()
        res = render(
            [{"deployUnitId": "LX34", "localRepoPath": local, "description": "测试"}],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        self.assertNotIn("(未匹配知识库)", prompt)
        self.assertIn("| 测试 |", prompt)  # ① 引用范围列 = UI description
        self.assertIn("## LX34（测试）", prompt)  # ③ 标题含 description
        self.assertIn(f"[LX34](#{_heading_slug(_unit_heading_label('LX34', '测试'))})", prompt)

    def test_unknown_unit_missing_local_still_shows_ui_description(self):
        # 未命中清单 + 本地也无 AGENTS.md（NO_AGENTS.MD 那种）→ ① 引用范围仍显示 UI description，
        # 不再「(未匹配知识库)」；无正文则不加锚点。
        res = render(
            [{"deployUnitId": "NO_AGENTS.MD", "localRepoPath": "/no/such/dir", "description": "没有 AGENT.MD"}],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        self.assertNotIn("(未匹配知识库)", prompt)
        self.assertIn("| 没有 AGENT.MD |", prompt)
        self.assertNotIn("[NO_AGENTS.MD](#", prompt)  # 无正文→无锚点链接
        self.assertFalse(res["agentmdLoadStatus"][0]["loaded"])

    def test_matched_unit_prefers_manifest_description_over_ui(self):
        # 命中清单的单元：引用范围仍用清单 description，UI 传入的 description 不顶替（仅兜底用）。
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out", "description": "UI叫法"}],
            plugin_root=_plugin_root(),
        )
        prompt = res["sessionContext"]
        self.assertIn("外联出站服务", prompt)  # 清单 description 优先
        self.assertNotIn("UI叫法", prompt)

    def test_system_not_in_status_only_unit_reported(self):
        # 命中清单的单元：系统级 remote 缺失既不走 local 兜底、也不进 agentmdLoadStatus；
        # 状态只反映单元级（remote 缺失→local 兜底成功）。
        local = _local_repo()
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": local}],
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
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/no/such/dir"}],
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
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": local}],
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
        self.assertIn('<UNIT id="unit-section" unit="单元级">', prompt)
        self.assertIn("</UNIT>", prompt)
        self.assertIn("## 会话工作区指令", prompt)  # 工作区指令的 ## 标题
        self.assertIn("- 工作区约束", prompt)  # AGENTS.md 正文（用正文独有的项目符号校验，避免与 ## 标题串味）
        self.assertNotIn("<WORKSPACE", prompt)  # 不再用 WORKSPACE 标签
        self.assertNotIn("<SYSTEM", prompt)  # 未选单元 → 无系统段
        # 工作区指令进 agentmdLoadStatus：deployUnitId=本地工作区、source=local、loaded=True。
        status = res["agentmdLoadStatus"]
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["deployUnitId"], "本地工作区")
        self.assertEqual(status[0]["source"], "local")
        self.assertTrue(status[0]["loaded"])

    def test_workspace_appears_in_scope_table(self):
        # 工作区指令在适用范围表里占一行：引用范围=会话工作区指令、代码地址=工作区路径、锚点指向其 ## 标题。
        ws = _workspace()
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
            session_workspace_path=ws,
        )
        prompt = res["sessionContext"]
        ws_link = f"[会话工作区](#{_heading_slug('会话工作区指令')})"
        unit_link = f"[LF39.18_Outservice](#{_heading_slug(_unit_heading_label('LF39.18_Outservice', '外联出站服务'))})"
        self.assertIn(ws_link, prompt)  # 工作区行的链接单元格指向其 ## 标题
        self.assertIn("会话工作区指令", prompt)  # 引用范围列
        self.assertIn(f"| {ws} |", prompt)  # 代码地址列 = 工作区路径
        # 工作区行排在选中单元行之前
        self.assertLess(prompt.index(ws_link), prompt.index(unit_link))

    def test_workspace_only_no_selection_has_scope_row(self):
        # 未选单元但工作区有正文：适用范围表仍渲染、且含工作区那一行。
        ws = _workspace()
        res = render([], plugin_root=_plugin_root(), session_workspace_path=ws)
        prompt = res["sessionContext"]
        self.assertIn("<SCOPE>", prompt)
        self.assertIn(f"[会话工作区](#{_heading_slug('会话工作区指令')})", prompt)

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
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
            session_workspace_path=ws,
        )
        prompt = res["sessionContext"]
        self.assertEqual(prompt.count("<UNIT "), 1)  # 只一对 <UNIT>
        sys_at = prompt.index('<SYSTEM id="sys-LF39"')
        unit_tag_at = prompt.index('<UNIT id="unit-section"')
        ws_label_at = prompt.index("## 会话工作区指令")
        unit_label_at = prompt.index("## LF39.18_Outservice")
        self.assertLess(sys_at, unit_tag_at)  # 系统级在单元级整段之前
        self.assertLess(unit_tag_at, ws_label_at)  # 工作区指令在 <UNIT> 开标签之后
        self.assertLess(ws_label_at, unit_label_at)  # 工作区指令排在选中单元之前

    def test_workspace_same_file_as_selected_unit_local_dedups(self):
        # 会话工作区路径 == 选中单元的 localRepoPath，且单元走 local 兜底（remote 缺失）→
        # 同一 AGENTS.md 只注入一次：丢掉会话工作区段，由带 deployUnitId 身份的单元段承载。
        local = _local_repo()  # 该目录下有 AGENTS.md（# 本地知识库）
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": local}],
            plugin_root=_plugin_root(write_remote=("LF39.system",)),  # 单元 remote 缺失→local 兜底
            session_workspace_path=local,  # 会话工作区 == 单元 localRepoPath
        )
        prompt = res["sessionContext"]
        # 同一文件正文只出现一次
        self.assertEqual(prompt.count("# 本地知识库"), 1)
        # 会话工作区段被丢弃（无其 ## 标题、无 ① 表链接）
        self.assertNotIn("## 会话工作区指令", prompt)
        self.assertNotIn("[会话工作区]", prompt)
        # 单元段仍在，带 deployUnitId 身份
        self.assertIn("## LF39.18_Outservice（外联出站服务）", prompt)
        # agentmdLoadStatus：只有单元一条，没有「本地工作区」
        status = res["agentmdLoadStatus"]
        self.assertFalse(any(s["deployUnitId"] == "本地工作区" for s in status))
        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["deployUnitId"], "LF39.18_Outservice")
        self.assertEqual(status[0]["source"], "local")

    def test_workspace_kept_when_unit_loads_remote_not_local(self):
        # 会话工作区 == 单元 localRepoPath，但单元命中 remote（加载 sys/ 下文件，非本地 AGENTS.md）→
        # 二者非同一文件，不去重：会话工作区段照常注入。
        local = _local_repo()
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": local}],
            plugin_root=_plugin_root(),  # 单元 remote 存在 → 命中 remote
            session_workspace_path=local,
        )
        prompt = res["sessionContext"]
        self.assertIn("## 会话工作区指令", prompt)  # 工作区段保留
        status = res["agentmdLoadStatus"]
        self.assertEqual(status[0]["deployUnitId"], "本地工作区")
        self.assertEqual(status[1]["source"], "remote")  # 单元走 remote

    def test_workspace_is_first_status_entry_with_selection(self):
        # 选中单元 + 工作区有正文：agentmdLoadStatus 首条是工作区（本地工作区/local），
        # 其后才是各部署单元；message 的单元摘要不把工作区算进去。
        ws = _workspace()
        res = render(
            [{"deployUnitId": "LF39.18_Outservice", "localRepoPath": "/repo/out"}],
            plugin_root=_plugin_root(),
            session_workspace_path=ws,
        )
        status = res["agentmdLoadStatus"]
        self.assertEqual(status[0]["deployUnitId"], "本地工作区")
        self.assertEqual(status[0]["source"], "local")
        self.assertTrue(status[0]["loaded"])
        self.assertEqual(status[1]["deployUnitId"], "LF39.18_Outservice")
        self.assertIn("remote 1", res["message"])  # 单元摘要只数单元，不含工作区


if __name__ == "__main__":
    unittest.main()
