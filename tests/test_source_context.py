from __future__ import annotations

import hashlib
import json
import subprocess
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
    source_requirement_ids_for_target,
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
                # The digest is recorded for traceability, not compared as a hard gate.
                "sha256": "0" * 64,
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

    def test_snapshot_only_context_is_valid_without_digest_blocking(self) -> None:
        self.write_context(source_context())

        errors = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])

    def test_stale_marker_is_recorded_without_a_hard_gate(self) -> None:
        data = source_context()
        data["sources"][0]["freshness"] = "stale"
        self.write_context(data)

        errors = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertEqual(errors, [])

    def test_table_rows_must_be_registered_as_original_items(self) -> None:
        self.write_context(source_context(include_fallback=False))

        errors = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertIn("未登记表格/字段行", "\n".join(errors))

    def test_original_must_be_locatable_in_snapshot(self) -> None:
        data = source_context()
        data["sources"][0]["items"][0]["original"] = "接口永远不会超时"
        self.write_context(data)

        errors = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertIn("original 无法在快照中定位", "\n".join(errors))

    def test_never_provided_is_distinct_and_blocks_prd_completion(self) -> None:
        data = source_context()
        source = data["sources"][0]
        source["availability"] = "never_provided"
        source["readStatus"] = "unreadable"
        source["path"] = None
        source["sha256"] = None
        source["items"] = []
        self.write_context(data)

        errors = validate_source_context(self.feature_dir, {"SRC-001"})

        self.assertIn("从未提供", "\n".join(errors))

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

    def test_digest_cli_only_reads_feature_snapshot(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "hooks" / "source_context.py"),
                "digest",
                "--feature-dir",
                str(self.feature_dir),
                "--path",
                "sources/SRC-001/payment.md",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), hashlib.sha256(SNAPSHOT.encode("utf-8")).hexdigest())

        invalid = subprocess.run(
            [
                sys.executable,
                str(ROOT / "hooks" / "source_context.py"),
                "digest",
                "--feature-dir",
                str(self.feature_dir),
                "--path",
                "../payment.md",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("修复", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
