from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.source_context import (  # noqa: E402
    sync_source_context,
    validate_source_context,
)


SNAPSHOT = """# 支付接口

| 字段 | 约束 |
| --- | --- |
| timeout | 3 秒 |
| fallback | 返回最近 5 分钟缓存 |
"""
SECOND_SNAPSHOT = "# 对账规则\n\n每日 02:00 生成对账文件。\n"


def source_context(*, include_second: bool = True) -> dict:
    """记录单位是文件：一份资料一条 source。"""
    sources = [
        {
            "id": "SRC-001",
            "name": "支付接口文档",
            "path": "sources/SRC-001/payment.md",
            "availability": "snapshot_only",
            "readStatus": "complete",
            "freshness": "unknown",
        }
    ]
    if include_second:
        sources.append(
            {
                "id": "SRC-002",
                "name": "对账规则说明",
                "path": "sources/SRC-002/reconcile.md",
                "availability": "snapshot_only",
                "readStatus": "complete",
                "freshness": "unknown",
            }
        )
    return {"version": 1, "sources": sources}


def expected_ids(*, include_second: bool = True) -> set:
    return {"SRC-001", "SRC-002"} if include_second else {"SRC-001"}


class SourceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.feature_dir = Path(self._tmp.name) / ".autobizdevops" / "features" / "alpha"
        first = self.feature_dir / "sources" / "SRC-001" / "payment.md"
        first.parent.mkdir(parents=True)
        first.write_text(SNAPSHOT, encoding="utf-8")
        second = self.feature_dir / "sources" / "SRC-002" / "reconcile.md"
        second.parent.mkdir(parents=True)
        second.write_text(SECOND_SNAPSHOT, encoding="utf-8")

    def write_context(self, data: dict) -> None:
        (self.feature_dir / "source-context.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_prd(self, *, include_second: bool = True) -> None:
        rows = (
            "| SRC-001 | 数据字典 | 支付接口文档 | sources/SRC-001/payment.md | 超时 | 可访问 |\n"
        )
        if include_second:
            rows += (
                "| SRC-002 | 规则说明 | 对账规则说明 | sources/SRC-002/reconcile.md | 对账 | 可访问 |\n"
            )
        (self.feature_dir / "PRD.md").write_text(
            "# 需求摘要\n\n## 外部资料与实现约束\n\n"
            "| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 状态 |\n"
            "| --- | --- | --- | --- | --- | --- |\n" + rows,
            encoding="utf-8",
        )

    def test_snapshot_only_context_is_valid(self) -> None:
        self.write_context(source_context())

        errors, warnings = validate_source_context(self.feature_dir, expected_ids())

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_stale_marker_is_recorded_without_a_hard_gate(self) -> None:
        data = source_context()
        data["sources"][0]["freshness"] = "stale"
        self.write_context(data)

        errors, _ = validate_source_context(self.feature_dir, expected_ids())

        self.assertEqual(errors, [])

    def test_never_provided_is_reported_as_warning_not_error(self) -> None:
        data = source_context()
        source = data["sources"][0]
        source["availability"] = "never_provided"
        source["readStatus"] = "unreadable"
        source["path"] = None
        self.write_context(data)

        errors, warnings = validate_source_context(self.feature_dir, expected_ids())

        self.assertEqual(errors, [])
        self.assertIn("从未提供", "\n".join(warnings))

    def test_duplicate_source_id_still_blocks(self) -> None:
        data = source_context()
        data["sources"][1]["id"] = "SRC-001"
        self.write_context(data)

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertIn("来源 ID 重复", "\n".join(errors))

    def test_legacy_item_fields_are_ignored(self) -> None:
        """旧 json 残留的 items/requirements 不再参与校验，文件本身就是最小记录单位。"""
        data = source_context()
        data["sources"][0]["items"] = [
            {
                "id": "SRC-001-I001",
                "location": "第 5 行",
                "original": "| timeout | 3 秒 |",
                "disposition": "requirement",
                "requirements": [{"id": "SRC-001-R001", "text": "超时 3 秒"}],
            }
        ]
        self.write_context(data)

        errors, warnings = validate_source_context(self.feature_dir, expected_ids())

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_sync_records_files_only(self) -> None:
        """sync 只做文件登记和快照定位，不产出任何阶段路由字段。"""
        self.write_prd()

        code, messages = sync_source_context(self.feature_dir)
        self.assertEqual(code, 0)
        self.assertIn("sources/SRC-001/payment.md", "\n".join(messages))

        generated = json.loads((self.feature_dir / "source-context.json").read_text(encoding="utf-8"))
        source = generated["sources"][0]
        self.assertEqual(source["id"], "SRC-001")
        self.assertEqual(source["path"], "sources/SRC-001/payment.md")
        self.assertNotIn("targets", source)
        self.assertNotIn("items", source)
        self.assertNotIn("sha256", source)

        errors, warnings = validate_source_context(self.feature_dir, expected_ids())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_sync_marks_source_without_snapshot_as_never_provided(self) -> None:
        self.write_prd()
        for leftover in (self.feature_dir / "sources" / "SRC-002").iterdir():
            leftover.unlink()

        code, messages = sync_source_context(self.feature_dir)

        self.assertEqual(code, 0)
        self.assertIn("SRC-002 未找到快照", "\n".join(messages))
        generated = json.loads((self.feature_dir / "source-context.json").read_text(encoding="utf-8"))
        second = generated["sources"][1]
        self.assertEqual(second["availability"], "never_provided")
        self.assertEqual(second["readStatus"], "unreadable")

if __name__ == "__main__":
    unittest.main()
