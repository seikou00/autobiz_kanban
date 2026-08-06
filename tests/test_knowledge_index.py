"""Tests for hooks/knowledge_index.py: frontmatter 解析与全库扫描。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import parse_manifest  # noqa: E402
from hooks.knowledge_index import (  # noqa: E402
    ENTRIES_DIRNAME,
    INDEX_NAME,
    FrontmatterError,
    build,
    group,
    normalize_type,
    parse_frontmatter,
    render_entry_md,
    scan,
)


def _doc(**fields):
    """拼一份带 frontmatter 的 md 文本；tags 用块式列表。"""
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append("{}:".format(key))
            for item in value:
                lines.append("  - {}".format(item))
        else:
            lines.append("{}: {}".format(key, value))
    lines.extend(["---", "", "# 正文标题", "正文内容"])
    return "\n".join(lines)


FULL = _doc(
    type="Service Knowledge",
    title="领域术语表",
    description="focusone服务的领域术语表",
    sub_product="LF39.18",
    deploy_unit="LF39.18_focusone",
    tags=["术语表", "中台导航"],
    timestamp="2025-07-29",
)


class ParseFrontmatterTest(unittest.TestCase):
    def test_parse_full(self):
        data = parse_frontmatter(FULL)
        self.assertEqual(data["type"], "Service Knowledge")
        self.assertEqual(data["title"], "领域术语表")
        self.assertEqual(data["sub_product"], "LF39.18")
        self.assertEqual(data["deploy_unit"], "LF39.18_focusone")
        self.assertEqual(data["tags"], ["术语表", "中台导航"])
        self.assertEqual(data["timestamp"], "2025-07-29")

    def test_inline_list(self):
        text = '---\ntype: reference\ntags: ["a", "b", "c"]\n---\n正文'
        self.assertEqual(parse_frontmatter(text)["tags"], ["a", "b", "c"])

    def test_empty_inline_list(self):
        text = "---\ntype: reference\ntags: []\n---\n正文"
        self.assertEqual(parse_frontmatter(text)["tags"], [])

    def test_quotes_stripped(self):
        text = '---\ntitle: "带引号的标题"\ndescription: \'单引号\'\n---\n'
        data = parse_frontmatter(text)
        self.assertEqual(data["title"], "带引号的标题")
        self.assertEqual(data["description"], "单引号")

    def test_comment_and_blank_lines_skipped(self):
        text = "---\n# 这是注释\n\ntype: reference\n---\n"
        data = parse_frontmatter(text)
        self.assertEqual(data["type"], "reference")
        self.assertNotIn("# 这是注释", data)

    def test_value_containing_colon(self):
        text = "---\ndescription: 说明: 冒号后面还有内容\n---\n"
        self.assertEqual(parse_frontmatter(text)["description"], "说明: 冒号后面还有内容")

    def test_leading_blank_lines_tolerated(self):
        self.assertEqual(parse_frontmatter("\n\n---\ntype: reference\n---\n")["type"], "reference")

    def test_crlf_tolerated(self):
        text = "---\r\ntype: reference\r\ntitle: T\r\n---\r\n正文"
        data = parse_frontmatter(text)
        self.assertEqual(data["type"], "reference")
        self.assertEqual(data["title"], "T")

    def test_missing_fence_raises_with_fix_hint(self):
        with self.assertRaises(FrontmatterError) as ctx:
            parse_frontmatter("# 裸 markdown\n没有头")
        self.assertIn("修复", str(ctx.exception))

    def test_unclosed_fence_raises_with_fix_hint(self):
        with self.assertRaises(FrontmatterError) as ctx:
            parse_frontmatter("---\ntype: reference\n正文没有闭合围栏")
        self.assertIn("修复", str(ctx.exception))


class NormalizeTypeTest(unittest.TestCase):
    def test_case_and_whitespace(self):
        self.assertEqual(normalize_type("Service Knowledge"), "service knowledge")
        self.assertEqual(normalize_type("  PRODUCT   KNOWLEDGE  "), "product knowledge")


class ScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_scan_collects_entries(self):
        self._write("LF3918/03_Backend_Services/services/focusone/glossary.md", FULL)
        entries, warnings = scan(self.root)
        self.assertEqual(warnings, [])
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.path, "LF3918/03_Backend_Services/services/focusone/glossary.md")
        self.assertEqual(entry.doc_type, "service knowledge")
        self.assertEqual(entry.sub_product, "LF39.18")
        self.assertEqual(entry.deploy_unit, "LF39.18_focusone")
        self.assertFalse(entry.is_system_level)

    def test_system_level_when_deploy_unit_absent(self):
        self._write(
            "LF3918/AGENTS.md",
            _doc(
                type="product knowledge",
                title="中台导航",
                description="系统总览",
                sub_product="LF39.18",
            ),
        )
        entries, warnings = scan(self.root)
        self.assertEqual(warnings, [])
        self.assertTrue(entries[0].is_system_level)
        self.assertEqual(entries[0].deploy_unit, "")

    def test_empty_deploy_unit_value_is_system_level(self):
        self._write(
            "LF3918/a.md",
            "---\ntype: reference\ntitle: T\ndescription: D\nsub_product: S\ndeploy_unit:\n---\n",
        )
        entries, _ = scan(self.root)
        self.assertTrue(entries[0].is_system_level)

    def test_dot_dirs_skipped(self):
        self._write(".git/objects/x.md", FULL)
        self._write(".entries/LF39.18/index.md", FULL)
        self._write(".cmbdevclaw/note.md", FULL)
        entries, warnings = scan(self.root)
        self.assertEqual(entries, [])
        self.assertEqual(warnings, [])

    def test_missing_required_field_warns_and_skips(self):
        self._write("a.md", _doc(type="reference", title="T", description="D"))
        entries, warnings = scan(self.root)
        self.assertEqual(entries, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("sub_product", warnings[0])
        self.assertIn("修复", warnings[0])

    def test_unknown_type_warns_and_skips(self):
        self._write("a.md", _doc(type="random stuff", title="T", description="D", sub_product="S"))
        entries, warnings = scan(self.root)
        self.assertEqual(entries, [])
        self.assertIn("不在枚举内", warnings[0])
        self.assertIn("修复", warnings[0])

    def test_bare_markdown_warns_and_skips(self):
        self._write("a.md", "# 没有 frontmatter\n")
        entries, warnings = scan(self.root)
        self.assertEqual(entries, [])
        self.assertIn("缺少 frontmatter", warnings[0])

    def test_non_md_ignored(self):
        self._write("a.txt", FULL)
        self._write("b.json", FULL)
        entries, warnings = scan(self.root)
        self.assertEqual(entries, [])
        self.assertEqual(warnings, [])

    def test_scan_order_is_stable(self):
        for name in ("z.md", "a.md", "m.md"):
            self._write("LF3918/" + name, FULL)
        first = [e.path for e in scan(self.root)[0]]
        second = [e.path for e in scan(self.root)[0]]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first))

    def test_missing_root_raises_with_fix_hint(self):
        with self.assertRaises(FrontmatterError) as ctx:
            scan(self.root / "nope")
        self.assertIn("修复", str(ctx.exception))


class BuildTestBase(unittest.TestCase):
    """搭一份带 frontmatter 的小知识库：一个系统 + 两个单元。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._write(
            "LF3918/AGENTS.md",
            _doc(type="product knowledge", title="中台导航", description="系统总览",
                 sub_product="LF39.18"),
        )
        self._write(
            "LF3918/02_Product_Knowledge/business-rules.md",
            _doc(type="product knowledge", title="业务规则", description="不可随意改的规则",
                 sub_product="LF39.18"),
        )
        self._write(
            "LF3918/03_Backend_Services/services/focusone/glossary.md",
            _doc(type="service knowledge", title="领域术语表", description="业务术语与缩写",
                 sub_product="LF39.18", deploy_unit="LF39.18_focusone"),
        )
        self._write(
            "LF3918/03_Backend_Services/services/focusone/architecture.md",
            _doc(type="service knowledge", title="架构说明", description="分层结构与模块职责",
                 sub_product="LF39.18", deploy_unit="LF39.18_focusone"),
        )
        self._write(
            "LF3918/04_Frontend_Apps/services/wgWebFlow/ui.md",
            _doc(type="component reference", title="UI 体系", description="前端组件与样式约定",
                 sub_product="LF39.18", deploy_unit="LF39.18_wgflowweb"),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _index(self):
        import json
        return json.loads((self.root / INDEX_NAME).read_text(encoding="utf-8"))


class RenderEntryTest(BuildTestBase):
    def test_map_format_and_order(self):
        entries, _ = scan(self.root)
        groups, _ = group(entries)
        unit = groups[0].units[0]
        text = render_entry_md(unit.docs)
        self.assertEqual(
            text,
            "## 文档地图\n"
            "\n"
            "- `{plugin_root}/LF3918/03_Backend_Services/services/focusone/architecture.md`: "
            "分层结构与模块职责\n"
            "- `{plugin_root}/LF3918/03_Backend_Services/services/focusone/glossary.md`: "
            "业务术语与缩写\n",
        )

    def test_placeholder_is_the_one_injection_supports(self):
        from hooks.render_session_context import PLUGIN_ROOT_PLACEHOLDER

        entries, _ = scan(self.root)
        groups, _ = group(entries)
        text = render_entry_md(groups[0].units[0].docs)
        self.assertIn(PLUGIN_ROOT_PLACEHOLDER, text)
        # 真实库手写地图用的是 ${plugin_root}，替换后会残留 $；生成物不得沿用
        self.assertNotIn("${plugin_root}", text)


class GroupTest(BuildTestBase):
    def test_units_and_system_docs_split(self):
        entries, _ = scan(self.root)
        groups, warnings = group(entries)
        self.assertEqual(warnings, [])
        self.assertEqual(len(groups), 1)
        system = groups[0]
        self.assertEqual(system.sub_product, "LF39.18")
        self.assertEqual([d.path for d in system.system_docs],
                         ["LF3918/02_Product_Knowledge/business-rules.md", "LF3918/AGENTS.md"])
        self.assertEqual([u.deploy_unit for u in system.units],
                         ["LF39.18_focusone", "LF39.18_wgflowweb"])

    def test_cross_sub_product_unit_warns_and_picks_first(self):
        self._write(
            "OTHER/dup.md",
            _doc(type="service knowledge", title="重复", description="D",
                 sub_product="AA.01", deploy_unit="LF39.18_focusone"),
        )
        entries, _ = scan(self.root)
        groups, warnings = group(entries)
        self.assertEqual(len(warnings), 1)
        self.assertIn("LF39.18_focusone", warnings[0])
        self.assertIn("修复", warnings[0])
        owner = {u.deploy_unit: g.sub_product for g in groups for u in g.units}
        self.assertEqual(owner["LF39.18_focusone"], "AA.01")   # 排序最靠前者


class BuildTest(BuildTestBase):
    def test_index_parses_with_existing_manifest_parser(self):
        """AC1：生成的索引必须能被既有解析层直接吃下。"""
        build(self.root)
        manifest = parse_manifest(self._index())
        self.assertEqual([s.system_id for s in manifest.systems], ["LF39.18"])
        units = manifest.systems[0].deploy_units
        self.assertEqual([u.deploy_unit_id for u in units],
                         ["LF39.18_focusone", "LF39.18_wgflowweb"])

    def test_entry_paths_recorded_in_index(self):
        build(self.root)
        system = self._index()["systems"][0]
        self.assertEqual(system["agentsPath"], ".entries/LF39.18/index.md")
        self.assertEqual(system["deployUnits"][0]["agentsPath"],
                         ".entries/LF39.18/LF39.18_focusone.md")
        for rel in [system["agentsPath"]] + [u["agentsPath"] for u in system["deployUnits"]]:
            self.assertTrue((self.root / rel).is_file(), rel)

    def test_system_display_name_from_agents_md_title(self):
        build(self.root)
        self.assertEqual(self._index()["systems"][0]["description"], "中台导航")

    def test_system_display_name_falls_back_to_id(self):
        (self.root / "LF3918/AGENTS.md").unlink()
        build(self.root)
        self.assertEqual(self._index()["systems"][0]["description"], "LF39.18")

    def test_unit_display_name_left_empty_with_aggregate_warning(self):
        """OQ1：单元展示名无来源，留空回落 UI，并汇总成一条 warning 而非每单元一条。"""
        result = build(self.root)
        units = self._index()["systems"][0]["deployUnits"]
        self.assertEqual([u["description"] for u in units], ["", ""])
        naming = [w for w in result["warnings"] if "展示名来源" in w]
        self.assertEqual(len(naming), 1)
        self.assertIn("2 个部署单元", naming[0])

    def test_build_is_byte_stable(self):
        """AC5：同一份知识库连续两次生成结果逐字节一致。"""
        build(self.root)
        first = {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in sorted((self.root / ENTRIES_DIRNAME).rglob("*.md"))
        }
        first[INDEX_NAME] = (self.root / INDEX_NAME).read_bytes()

        build(self.root)
        second = {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in sorted((self.root / ENTRIES_DIRNAME).rglob("*.md"))
        }
        second[INDEX_NAME] = (self.root / INDEX_NAME).read_bytes()

        self.assertEqual(first, second)

    def test_stale_entries_removed_on_rebuild(self):
        build(self.root)
        stale = self.root / ENTRIES_DIRNAME / "LF39.18" / "LF39.18_gone.md"
        stale.write_text("旧产物", encoding="utf-8")
        build(self.root)
        self.assertFalse(stale.exists())

    def test_generated_entries_not_rescanned(self):
        """入口是 .entries/ 下的产物，第二次扫描不得把它们当知识文档。"""
        build(self.root)
        first_docs = build(self.root)["documents"]
        self.assertEqual(first_docs, 5)

    def test_no_system_entry_when_only_unit_docs(self):
        (self.root / "LF3918/AGENTS.md").unlink()
        (self.root / "LF3918/02_Product_Knowledge/business-rules.md").unlink()
        build(self.root)
        system = self._index()["systems"][0]
        self.assertEqual(system["agentsPath"], "")
        self.assertFalse((self.root / ENTRIES_DIRNAME / "LF39.18" / "index.md").exists())

    def test_empty_repo_writes_no_index(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = build(empty)
        self.assertFalse(result["generated"])
        self.assertFalse((empty / INDEX_NAME).exists())

    def test_legacy_repo_keeps_manifest_fallback_alive(self):
        """灰度回落：旧知识库全是裸 md，绝不能写出空索引挡住 agents.manifest.json。"""
        legacy = self.root / "legacy"
        (legacy / "LF3919").mkdir(parents=True)
        (legacy / "LF3919" / "AGENTS.md").write_text("# 裸 markdown", encoding="utf-8")
        (legacy / "agents.manifest.json").write_text("{}", encoding="utf-8")

        result = build(legacy)

        self.assertFalse(result["generated"])
        self.assertEqual(result["documents"], 0)
        self.assertFalse((legacy / INDEX_NAME).exists())
        self.assertFalse((legacy / ENTRIES_DIRNAME).exists())
        self.assertTrue((legacy / "agents.manifest.json").exists())
        self.assertTrue(any("缺少 frontmatter" in w for w in result["warnings"]))
        self.assertTrue(any("回落 agents.manifest.json" in w for w in result["warnings"]))

    def test_generated_artifacts_cleaned_when_repo_regresses(self):
        """曾生成过索引的目录，若后来扫不到 frontmatter，产物必须被清掉。"""
        build(self.root)
        self.assertTrue((self.root / INDEX_NAME).exists())
        for md in self.root.rglob("*.md"):
            if ENTRIES_DIRNAME not in md.parts:
                md.write_text("# 裸 markdown", encoding="utf-8")

        result = build(self.root)

        self.assertFalse(result["generated"])
        self.assertFalse((self.root / INDEX_NAME).exists())
        self.assertFalse((self.root / ENTRIES_DIRNAME).exists())


if __name__ == "__main__":
    unittest.main()
