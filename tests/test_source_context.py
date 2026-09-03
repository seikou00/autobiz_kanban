from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.artifact_ref_validator import (  # noqa: E402
    validate_plan_source_coverage,
    validate_task_artifact_refs,
)
from hooks.source_context import (  # noqa: E402
    resolve_source_requirement_refs,
    source_ids_for_target,
    source_requirement_ids_for_target,
    sync_source_context,
    validate_source_context,
)


SNAPSHOT = """# 支付接口

| 字段 | 约束 |
| --- | --- |
| timeout | 3 秒 |
| fallback | 返回最近 5 分钟缓存 |
"""


def source_context(*, include_fallback: bool = True) -> dict:
    items = [
        {
            "id": "SRC-001-I001",
            "location": "第 5 行",
            "original": "| timeout | 3 秒 |",
            "disposition": "requirement",
            "requirements": [
                {
                    "id": "SRC-001-R001",
                    "text": "调用超时时间为 3 秒",
                    "targets": ["spec", "design", "plan", "code", "reviewer", "e2e"],
                }
            ],
        }
    ]
    if include_fallback:
        items.append(
            {
                "id": "SRC-001-I002",
                "location": "第 6 行",
                "original": "| fallback | 返回最近 5 分钟缓存 |",
                "disposition": "requirement",
                "requirements": [
                    {
                        "id": "SRC-001-R002",
                        "text": "超时后返回最近 5 分钟缓存",
                        "targets": ["spec", "design", "plan", "code", "reviewer", "e2e"],
                    }
                ],
            }
        )
    return {
        "version": 1,
        "sources": [
            {
                "id": "SRC-001",
                "name": "支付接口文档",
                "path": "sources/SRC-001/payment.md",
                "availability": "snapshot_only",
                "readStatus": "complete",
                "freshness": "unknown",
                "items": items,
            }
        ],
    }


class SourceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.feature_dir = Path(self._tmp.name) / ".autobizdevops" / "features" / "alpha"
        snapshot = self.feature_dir / "sources" / "SRC-001" / "payment.md"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(SNAPSHOT, encoding="utf-8")

    def write_context(self, data: dict) -> None:
        (self.feature_dir / "source-context.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_snapshot_only_context_is_valid(self) -> None:
        self.write_context(source_context())

        errors, warnings = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_stale_marker_is_recorded_without_a_hard_gate(self) -> None:
        data = source_context()
        data["sources"][0]["freshness"] = "stale"
        self.write_context(data)

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])

    def test_partial_row_coverage_no_longer_blocks(self) -> None:
        """行覆盖由 sync 生成，不再作为阻断项。"""
        self.write_context(source_context(include_fallback=False))

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])

    def test_original_wording_no_longer_blocks(self) -> None:
        """逐字原文由 sync 写入，模型改写措辞不再阻断。"""
        data = source_context()
        data["sources"][0]["items"][0]["original"] = "接口永远不会超时"
        self.write_context(data)

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])

    def test_never_provided_is_reported_as_warning_not_error(self) -> None:
        data = source_context()
        source = data["sources"][0]
        source["availability"] = "never_provided"
        source["readStatus"] = "unreadable"
        source["path"] = None
        source["items"] = []
        self.write_context(data)

        errors, warnings = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])
        self.assertIn("从未提供", "\n".join(warnings))

    def test_missing_requirement_targets_still_block(self) -> None:
        data = source_context()
        data["sources"][0]["items"][0]["requirements"][0]["targets"] = []
        self.write_context(data)

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertIn("targets 必须是非空数组", "\n".join(errors))

    def test_duplicate_requirement_id_still_blocks(self) -> None:
        data = source_context()
        data["sources"][0]["items"][1]["requirements"][0]["id"] = "SRC-001-R001"
        self.write_context(data)

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertIn("要求 ID 重复", "\n".join(errors))

    def test_sync_generates_items_from_snapshot_and_keeps_judgements(self) -> None:
        prd = self.feature_dir / "PRD.md"
        prd.write_text(
            "# 需求摘要\n\n## 外部资料与实现约束\n\n"
            "| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| SRC-001 | 数据字典 | 支付接口文档 | sources/SRC-001/payment.md | 超时 | Specs | 可访问 |\n",
            encoding="utf-8",
        )

        code, messages = sync_source_context(self.feature_dir)
        self.assertEqual(code, 0)
        output = "\n".join(messages)
        self.assertNotIn("待判定", output)
        self.assertNotIn("逐行判定", output)
        self.assertIn("不要求逐条生成下游要求", output)

        generated = json.loads((self.feature_dir / "source-context.json").read_text(encoding="utf-8"))
        source = generated["sources"][0]
        self.assertEqual(source["id"], "SRC-001")
        self.assertNotIn("sha256", source)
        self.assertTrue(source["items"])
        self.assertTrue(all(item["original"] in SNAPSHOT for item in source["items"]))
        self.assertTrue(all(item["disposition"] == "background" for item in source["items"]))

        # 模型填入判定后重跑 sync，判定必须保留。
        source["items"][0]["disposition"] = "requirement"
        source["items"][0]["requirements"] = [
            {"id": "SRC-001-R001", "text": "调用超时时间为 3 秒", "targets": ["spec"]}
        ]
        self.write_context(generated)

        code, _ = sync_source_context(self.feature_dir)
        self.assertEqual(code, 0)

        resynced = json.loads((self.feature_dir / "source-context.json").read_text(encoding="utf-8"))
        first = resynced["sources"][0]["items"][0]
        self.assertEqual(first["disposition"], "requirement")
        self.assertEqual(first["requirements"][0]["id"], "SRC-001-R001")

        errors, _ = validate_source_context(self.feature_dir, {"SRC-001"})
        self.assertEqual(errors, [])

    def test_plan_coverage_uses_multi_target_requirement_ids(self) -> None:
        data = source_context()
        self.write_context(data)

        errors = validate_plan_source_coverage(
            self.feature_dir,
            [{"id": "TASK-001", "sourceRefs": ["SRC-001-R001"]}],
        )

        self.assertEqual(errors[0]["reason"], "missing_plan_source_requirement_coverage")
        self.assertIn("SRC-001-R002", errors[0]["detail"])

        errors = validate_plan_source_coverage(
            self.feature_dir,
            [{"id": "TASK-001", "sourceRefs": ["SRC-001-R001", "SRC-001-R002"]}],
        )
        self.assertEqual(errors, [])

    def test_task_and_code_resolution_reject_unknown_requirement_ids(self) -> None:
        self.write_context(source_context())
        task = {"id": "TASK-001", "specRefs": [], "designRefs": [], "sourceRefs": ["SRC-001-R999"]}

        task_errors = validate_task_artifact_refs(self.feature_dir, task)
        resolved, resolution_errors = resolve_source_requirement_refs(
            self.feature_dir,
            task["sourceRefs"],
        )

        self.assertEqual(task_errors[0]["reason"], "unknown_source_requirement_ref")
        self.assertEqual(resolved, [{"ref": "SRC-001-R999", "found": False}])
        self.assertEqual(resolution_errors[0]["reason"], "unknown_source_requirement_ref")

    def test_targets_are_queryable_for_downstream_artifacts(self) -> None:
        data = source_context()

        self.assertEqual(
            source_requirement_ids_for_target(data, "spec"),
            {"SRC-001-R001", "SRC-001-R002"},
        )
        self.assertEqual(source_ids_for_target(data, "spec"), {"SRC-001"})
        self.assertEqual(source_ids_for_target(data, "unknown"), set())

if __name__ == "__main__":
    unittest.main()
