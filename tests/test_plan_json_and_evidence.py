from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_integrity_gate import check_code_done, check_integrity  # noqa: E402
from hooks.evidence_store import (  # noqa: E402
    EvidenceStoreError,
    append_evidence,
    index_path,
    main as evidence_store_main,
    read_records,
    stream_path,
    validate_record,
    write_index,
)
from hooks.plan_json import validate_plan_data, write_plan_json  # noqa: E402


def valid_plan(
    *,
    feature: str = "alpha",
    status: str = "done",
    evidence_ids: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict:
    return {
        "version": 1,
        "taskDetailVersion": 1,
        "featureId": feature,
        "tasks": [
            {
                "id": "T001",
                "title": "one",
                "goal": "deliver one observable behavior",
                "status": status,
                "deps": [],
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update the behavior", "cover the boundary"],
                "acceptanceCriteria": ["the behavior is observable"],
                "nonGoals": [],
                "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
                "expectedFiles": [],
                "evidenceIds": evidence_ids if evidence_ids is not None else ["ev_0001"],
                "blockers": blockers if blockers is not None else [],
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
    def test_plan_json_template_matches_initial_contract(self) -> None:
        template_path = ROOT / "skills" / "autodev" / "autodev-plan" / "templates" / "plan.json"
        data = json.loads(template_path.read_text(encoding="utf-8"))

        self.assertEqual(validate_plan_data(data, require_initial_status=True), [])

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
                "goal": "deliver two observable behavior",
                "status": "done",
                "deps": ["T003"],
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update the behavior", "cover the boundary"],
                "acceptanceCriteria": ["the behavior is observable"],
                "nonGoals": [],
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

    def test_initial_plan_requires_task_details(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        del plan["tasks"][0]["goal"]

        errors = validate_plan_data(plan, require_initial_status=True)

        self.assertIn("T001.goal_missing", errors)

    def test_ui_task_scope_pages_must_match_ui_refs(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["uiRequired"] = True
        task["uiRefs"] = {
            "pageRefs": ["PAGE-001"],
            "interactionRefs": ["UIX-001"],
            "visualSourceRefs": [],
            "frontendRoute": "spec-driven-ui",
        }
        task["scope"]["pages"] = ["PAGE-002"]
        task["nonGoals"] = ["do not implement unrelated pages"]

        errors = validate_plan_data(plan, require_initial_status=True)

        self.assertIn("T001.scope.pages_mismatch_uiRefs", errors)

    def test_api_task_requires_non_goals_in_detail_schema(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["apiIds"] = ["API-001"]
        task["nonGoals"] = []

        errors = validate_plan_data(plan, require_initial_status=True)

        self.assertIn("T001.nonGoals_missing", errors)

class EvidenceStoreTest(unittest.TestCase):
    def test_validation_evidence_requires_structured_result(self) -> None:
        record = {
            "version": 1,
            "evidenceId": "ev_0001",
            "featureId": "alpha",
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "validation",
            "createdAt": "2026-06-24T00:00:00Z",
            "validation": {},
        }

        errors = validate_record(record)

        self.assertIn("validation.command_missing", errors)
        self.assertIn("validation.exitCode_missing", errors)
        self.assertIn("validation.result_invalid", errors)

        record["validation"] = {"command": "pytest", "exitCode": 1, "result": "fail"}
        self.assertIn("validation.outputTailPath_missing", validate_record(record))

        record["validation"] = {"command": "pytest", "exitCode": 1, "result": "pass"}
        self.assertIn("validation.result_exitCode_mismatch", validate_record(record))

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

            with self.assertRaisesRegex(EvidenceStoreError, "evidence_stream_rewritten_or_truncated"):
                append_pass_evidence(feature_dir)

            self.assertIn("evidence_index_mismatch:lineCount", check_integrity(feature_dir))

    def test_write_index_rejects_rewritten_stream_after_index_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            append_pass_evidence(feature_dir)
            record = json.loads(stream_path(feature_dir).read_text(encoding="utf-8"))
            record["checkpoint"] = "unit_test_in_progress"
            stream_path(feature_dir).write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(EvidenceStoreError, "evidence_stream_rewritten_or_truncated"):
                write_index(feature_dir)

            self.assertIn("evidence_stream_rewritten_or_truncated:sha256", check_integrity(feature_dir))

    def test_write_index_rejects_missing_index_for_nonempty_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            append_pass_evidence(feature_dir)
            index_path(feature_dir).unlink()

            with self.assertRaisesRegex(EvidenceStoreError, "missing_evidence_index_for_nonempty_stream"):
                write_index(feature_dir)

            self.assertIn("missing_evidence_index", "\n".join(check_integrity(feature_dir)))

    def test_append_evidence_rejects_missing_index_for_nonempty_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            append_pass_evidence(feature_dir)
            index_path(feature_dir).unlink()

            with self.assertRaisesRegex(EvidenceStoreError, "missing_evidence_index_for_nonempty_stream"):
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

    def test_append_smoke_cli_writes_smoke_without_validation_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            exit_code = evidence_store_main(
                [
                    "append-smoke",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                    "--test-id",
                    "SMK-001",
                    "--checkpoint",
                    "code_in_progress",
                    "--node-id",
                    "dev.code",
                    "--skill",
                    "autodev-code",
                    "--task-id",
                    "T001",
                    "--command",
                    "echo ok",
                    "--exit-code",
                    "0",
                ]
            )

            self.assertEqual(exit_code, 0)
            records = read_records(stream_path(workspace / ".autobizdevops" / "features" / "alpha"))
            self.assertEqual(records[0]["action"], "smoke")
            self.assertIn("smoke", records[0])
            self.assertNotIn("validation", records[0])

    def test_evidence_cli_defaults_to_plugin_feature_artifact_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_workspace = root / "plugin-workspace"
            project_dir = "project-alpha"
            workspace = plugin_workspace / project_dir
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            cwd = root / "business-repo"
            feature_dir.mkdir(parents=True)
            cwd.mkdir()
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {}}),
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.dict(
                    os.environ,
                    {
                        "PLUGIN_WORKSPACE": str(plugin_workspace),
                        "PROJECT_DIR": project_dir,
                        "FEATURE_ID": "alpha",
                    },
                    clear=False,
                ):
                    exit_code = evidence_store_main(
                        [
                            "append-smoke",
                            "--feature",
                            "alpha",
                            "--test-id",
                            "SMK-001",
                            "--checkpoint",
                            "code_in_progress",
                            "--node-id",
                            "dev.code",
                            "--skill",
                            "autodev-code",
                            "--task-id",
                            "T001",
                            "--command",
                            "echo ok",
                            "--exit-code",
                            "0",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

            self.assertEqual(exit_code, 0)
            self.assertTrue(stream_path(feature_dir).is_file())
            self.assertFalse((cwd / ".autobizdevops" / "features" / "alpha" / "evidence" / "EVIDENCE.jsonl").exists())


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

    def test_code_done_gate_can_degrade_when_plan_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            append_pass_evidence(feature_dir, task_id="T001")

            self.assertTrue(any("missing_plan_json" in error for error in check_code_done(feature_dir)))
            self.assertEqual(check_code_done(feature_dir, require_plan=False), [])

    def test_code_done_gate_rejects_unresolved_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_plan_json(
                feature_dir / "plan.json",
                valid_plan(status="done", evidence_ids=["ev_0001"], blockers=["waiting for API contract"]),
            )
            append_pass_evidence(feature_dir)

            errors = check_code_done(feature_dir)

            self.assertIn("plan_json:T001.blockers_unresolved", errors)
            self.assertIn("unresolved_blocker:T001", errors)

    def test_code_done_gate_does_not_count_smoke_pass_as_validation_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_plan_json(feature_dir / "plan.json", valid_plan(status="done", evidence_ids=["ev_0001"]))
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                    "designRefs": ["design.md#D-001"],
                    "changedFiles": ["tests/smoke/cap_smoke.py"],
                    "validation": {"command": "python tests/smoke/cap_smoke.py", "exitCode": 0, "result": "pass"},
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/cap_smoke.py", "exitCode": 0, "result": "pass"},
                },
            )

            self.assertIn("missing_pass_evidence_for_task:T001", check_code_done(feature_dir))


if __name__ == "__main__":
    unittest.main()
