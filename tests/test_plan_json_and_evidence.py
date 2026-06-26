from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_integrity_gate import check_code_done, check_integrity  # noqa: E402
from hooks.evidence_store import append_evidence, index_path, stream_path  # noqa: E402
from hooks.plan_json import parse_plan_markdown, validate_plan_data, write_plan_json  # noqa: E402


def valid_plan(*, feature: str = "alpha", status: str = "done", evidence_ids: list[str] | None = None) -> dict:
    return {
        "version": 1,
        "featureId": feature,
        "tasks": [
            {
                "id": "T001",
                "title": "one",
                "status": status,
                "deps": [],
                "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
                "expectedFiles": [],
                "evidenceIds": evidence_ids if evidence_ids is not None else ["ev_0001"],
                "blockers": [],
            }
        ],
    }


def append_pass_evidence(feature_dir: Path, *, task_id: str = "T001") -> dict:
    return append_evidence(
        feature_dir,
        {
            "featureId": feature_dir.name,
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": task_id,
            "action": "validation",
            "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
            "designRefs": ["design.md#D-001"],
            "changedFiles": ["src/example.py"],
            "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
        },
    )


class PlanJsonTest(unittest.TestCase):
    def test_plan_stage_allows_empty_evidence_ids_until_done_gate(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])

        self.assertEqual(validate_plan_data(plan), [])
        self.assertIn("T001.evidenceIds_missing", validate_plan_data(plan, require_all_done=True))

    def test_validate_plan_detects_unknown_dependency_and_cycle(self) -> None:
        plan = valid_plan()
        plan["tasks"].append(
            {
                "id": "T002",
                "title": "two",
                "status": "done",
                "deps": ["T003"],
                "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
                "expectedFiles": [],
                "evidenceIds": ["ev_0002"],
                "blockers": [],
            }
        )

        errors = validate_plan_data(plan)

        self.assertIn("T002.dependency_unknown:T003", errors)

        plan["tasks"][0]["deps"] = ["T002"]
        plan["tasks"][1]["deps"] = ["T001"]
        errors = validate_plan_data(plan)

        self.assertTrue(any(error.startswith("task_dependency_cycle:") for error in errors))

    def test_parse_plan_markdown_reads_deps_from_summary_table(self) -> None:
        data = parse_plan_markdown(
            "\n".join(
                [
                    "## 任务总览",
                    "| Task ID | 任务 | 依赖 | 覆盖规格/设计项 | 状态 |",
                    "| ------- | ---- | ---- | --------------- | ---- |",
                    "| T001 | one | 无 | REQ-001/SCN-001 / D-001 | 待做 |",
                    "| T002 | two | T001 | REQ-001/SCN-001 / D-001 | 待做 |",
                    "",
                    "## 任务详情",
                    "### Task [T001]: one",
                    "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                    "- **decision_id:** D-001",
                    "- **证据依据:** ev_0001",
                    "- **验证命令:** echo one",
                    "- **状态:** 待做",
                    "",
                    "### Task [T002]: two",
                    "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                    "- **decision_id:** D-001",
                    "- **证据依据:** ev_0002",
                    "- **验证命令:** echo two",
                    "- **状态:** 待做",
                ]
            ),
            feature_id="alpha",
        )

        self.assertEqual(data["tasks"][1]["deps"], ["T001"])


class EvidenceStoreTest(unittest.TestCase):
    def test_append_evidence_creates_sequential_stream_tail_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            first = append_pass_evidence(feature_dir)
            second = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "unit_test_in_progress",
                    "nodeId": "dev.utest",
                    "skill": "autodev-utest",
                    "taskId": "T001",
                    "action": "validation",
                    "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
                },
                output_tail="pytest ok",
            )

            self.assertEqual(first["evidenceId"], "ev_0001")
            self.assertEqual(second["evidenceId"], "ev_0002")
            self.assertTrue((feature_dir / "evidence" / "ev_0002.log").is_file())
            self.assertTrue(index_path(feature_dir).is_file())
            self.assertEqual(check_integrity(feature_dir), [])

    def test_append_evidence_rejects_truncated_stream_after_index_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            append_pass_evidence(feature_dir)
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "unit_test_in_progress",
                    "nodeId": "dev.utest",
                    "skill": "autodev-utest",
                    "taskId": "T001",
                    "action": "validation",
                    "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
                },
            )
            first_line = stream_path(feature_dir).read_text(encoding="utf-8").splitlines()[0]
            stream_path(feature_dir).write_text(first_line + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "evidence_stream_rewritten_or_truncated"):
                append_pass_evidence(feature_dir)

            self.assertIn("evidence_index_mismatch:lineCount", check_integrity(feature_dir))

    def test_check_integrity_detects_non_sequential_id_and_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            append_pass_evidence(feature_dir)
            record = json.loads(stream_path(feature_dir).read_text(encoding="utf-8"))
            record["evidenceId"] = "ev_0002"
            stream_path(feature_dir).write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            errors = check_integrity(feature_dir)

            self.assertIn("non_sequential_evidence_id:line=1:id=ev_0002", errors)
            self.assertIn("evidence_stream_rewritten_or_truncated:sha256", errors)


class EvidenceGateTest(unittest.TestCase):
    def test_code_done_gate_requires_done_plan_and_pass_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_plan_json(feature_dir / "plan.json", valid_plan(status="todo"))
            append_pass_evidence(feature_dir)

            self.assertTrue(any(error.startswith("plan_json:") for error in check_code_done(feature_dir)))

            write_plan_json(feature_dir / "plan.json", valid_plan(status="done", evidence_ids=["ev_0001"]))

            self.assertEqual(check_code_done(feature_dir), [])


if __name__ == "__main__":
    unittest.main()
