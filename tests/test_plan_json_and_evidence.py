from __future__ import annotations

import json
import os
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_integrity_gate import check_code_done, check_integrity, check_plan_evidence_refs  # noqa: E402
from hooks.evidence_store import (  # noqa: E402
    EvidenceStoreError,
    append_evidence,
    index_path,
    main as evidence_store_main,
    read_records,
    stream_path,
    validate_detail_fields,
    validate_record,
    write_index,
)
from hooks.plan_json import (  # noqa: E402
    batch_plan_path,
    validate_plan_data,
    validate_task_collection,
    write_plan_json,
)
from hooks.evidence_kernel import check_record_artifacts, unlink_if_exists, write_pending  # noqa: E402
from hooks.evidence_kernel import write_sidecar  # noqa: E402


def valid_plan(
    *,
    feature: str = "alpha",
    status: str = "done",
    evidence_ids: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict:
    bound_evidence = evidence_ids if evidence_ids is not None else ["ev_0001"]
    return {
        "featureId": feature,
        "projectValidationCommands": [
            {
                "id": "PROJECT-VAL-001",
                "argv": [sys.executable, "-c", "print('project integration')"],
                "cwd": ".",
                "kind": "integration_test",
                "required": True,
            }
        ],
        "projectCheckEvidenceIds": [],
        "latestProjectCheckEvidenceId": None,
        "tasks": [
            {
                "id": "T001",
                "title": "one",
                "goal": "deliver one observable behavior",
                "status": status,
                "deps": [],
                "workspaceRef": "default",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update the behavior", "cover the boundary"],
                "acceptanceCriteria": [
                    {
                        "id": "AC-T001-01",
                        "text": "the behavior is observable",
                        "scenarioRefs": ["specs/capability/spec.md#SCN-001"],
                    }
                ],
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
                "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "completionPolicy": "all_required_validations_pass",
                "validationCommands": [
                    {
                        "id": "VAL-T001-01",
                        "argv": [sys.executable, "-c", "print('task validation')"],
                        "cwd": ".",
                        "kind": "behavior_test",
                        "required": True,
                        "covers": ["AC-T001-01"],
                    }
                ],
                "expectedFiles": [],
                "evidenceIds": bound_evidence,
                "completionEvidenceIds": bound_evidence,
                "latestPassEvidenceId": bound_evidence[-1] if bound_evidence else None,
                "blockers": blockers if blockers is not None else [],
            }
        ],
    }


def validate_test_tasks(plan: dict, **kwargs: object) -> list[str]:
    return validate_task_collection(str(plan.get("featureId", "alpha")), plan.get("tasks", []), **kwargs)


def write_test_plan(feature_dir: Path, plan: dict) -> None:
    task_items = plan["tasks"]
    execution_lane = "frontend" if any(item.get("uiRequired") is True for item in task_items) else "backend"
    all_done = bool(task_items) and all(item.get("status") == "done" for item in task_items)
    batch_status = "done" if all_done else "todo"
    root_status = "done" if all_done else "todo"
    write_plan_json(
        feature_dir / "plan.json",
        {
            "featureId": plan["featureId"],
            "status": root_status,
            "taskSetStatus": "finalized",
            "activeBatchId": None if all_done else "B001",
            "nextBatchId": None,
            "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
            "batchValidationProfiles": {
                execution_lane: {
                    "commands": [
                        {
                            "argv": [sys.executable, "-c", f"print('{execution_lane} compile')"],
                            "cwd": ".",
                            "kind": "compile",
                            "required": True,
                        }
                    ]
                }
            },
            "batches": [
                {
                    "id": "B001",
                    "path": "plans/B001/plan.json",
                    "title": "capability",
                    "specRoots": ["specs/capability/spec.md"],
                    "executionLane": execution_lane,
                    "deps": [],
                    "taskIds": [item["id"] for item in task_items],
                    "status": batch_status,
                }
            ],
            "projectValidationCommands": plan["projectValidationCommands"],
            "projectCheckEvidenceIds": plan.get("projectCheckEvidenceIds", []),
            "latestProjectCheckEvidenceId": plan.get("latestProjectCheckEvidenceId"),
        },
    )
    write_plan_json(
        batch_plan_path(feature_dir, "B001"),
        {
            "featureId": plan["featureId"],
            "batchId": "B001",
            "title": "capability",
            "executionLane": execution_lane,
            "status": batch_status,
            "taskCount": len(task_items),
            "completedTaskCount": sum(item.get("status") == "done" for item in task_items),
            "completionEvidenceIds": [
                evidence_id
                for item in task_items
                for evidence_id in item.get("completionEvidenceIds", [])
            ],
            "batchValidation": {
                "profile": execution_lane,
                "status": "passed" if all_done else "pending",
                "commands": [
                    {
                        "id": "BATCH-B001-VAL-001",
                        "argv": [sys.executable, "-c", f"print('{execution_lane} compile')"],
                        "cwd": ".",
                        "kind": "compile",
                        "required": True,
                    }
                ],
                "evidenceIds": [],
                "latestPassEvidenceIds": [],
                "activeRunId": None,
            },
            "startedAt": None,
            "completedAt": "2026-07-10T00:00:00Z" if all_done else None,
            "tasks": task_items,
        },
    )


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


def append_current_evidence(
    feature_dir: Path,
    *,
    task_id: str = "T001",
    command_id: str = "VAL-T001-01",
    checked_criteria: list[str] | None = None,
) -> dict:
    return append_evidence(
        feature_dir,
        {
            "featureId": feature_dir.name,
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": task_id,
            "action": "validation",
            "detailVersion": 2,
            "runId": "run-test",
            "completionMode": "verified_existing",
            "summary": "verified existing behavior",
            "implementation": {"noCodeChange": True, "whatChanged": [], "why": "already implemented"},
            "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
            "designRefs": ["design.md#D-001"],
            "changedFiles": [],
            "fileChanges": [],
            "supportingFiles": ["src/example.py"],
            "checkedCriteria": checked_criteria if checked_criteria is not None else ["AC-T001-01"],
            "validation": {
                "commandId": command_id,
                "argv": [sys.executable, "-c", "print('task validation')"],
                "command": f"{sys.executable} -c print('task validation')",
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "exitCode": 0,
                "result": "pass",
            },
        },
        output_tail="ok\n",
    )


class EvidenceKernelTest(unittest.TestCase):
    def test_unlink_if_exists_removes_file_and_tolerates_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cleanup.json"
            path.write_text("cleanup\n", encoding="utf-8")

            unlink_if_exists(path)
            self.assertFalse(path.exists())

            unlink_if_exists(path)


class PlanJsonTest(unittest.TestCase):
    def test_plan_accepts_structured_completion_contract(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])

        self.assertEqual(validate_test_tasks(plan), [])

    def test_plan_rejects_legacy_version_field(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        plan["version"] = 1

        self.assertIn("legacy_plan_requires_rebuild", validate_plan_data(plan))

    def test_plan_rejects_unknown_acceptance_coverage(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["validationCommands"][0]["covers"] = ["AC-T001-99"]

        self.assertIn("T001.validationCommands[0].covers_unknown:AC-T001-99", validate_test_tasks(plan))

    def test_plan_rejects_compile_as_acceptance_coverage(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["validationCommands"][0]["kind"] = "compile"

        self.assertIn(
            "T001.validationCommands[0].kind_invalid_for_lane:backend",
            validate_test_tasks(plan),
        )

    def test_frontend_plan_accepts_build_as_acceptance_coverage(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["uiRequired"] = True
        task["scope"]["pages"] = ["PAGE-001"]
        task["uiRefs"] = {
            "pageRefs": ["PAGE-001"],
            "interactionRefs": ["UIX-001"],
            "visualSourceRefs": [],
            "frontendRoute": "spec-driven-ui",
        }
        task["validationCommands"][0].update({
            "argv": ["npm", "run", "build"],
            "kind": "build",
        })

        self.assertEqual(validate_test_tasks(plan), [])

    def test_frontend_compile_kind_must_match_command(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["uiRequired"] = True
        task["scope"]["pages"] = ["PAGE-001"]
        task["uiRefs"] = {
            "pageRefs": ["PAGE-001"],
            "interactionRefs": [],
            "visualSourceRefs": [],
            "frontendRoute": "spec-driven-ui",
        }
        task["validationCommands"][0].update({
            "argv": ["npm", "run", "typecheck"],
            "kind": "build",
        })

        self.assertIn(
            "T001.validationCommands[0].frontend_compile_command_mismatch:build",
            validate_test_tasks(plan),
        )

    def test_plan_rejects_noop_placeholder_and_inline_shell_commands(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        command = plan["tasks"][0]["validationCommands"][0]
        command["argv"] = ["bash", "-c", "echo validation placeholder"]

        errors = validate_test_tasks(plan)

        self.assertIn("T001.validationCommands[0].validation_command_placeholder", errors)
        self.assertIn("T001.validationCommands[0].validation_command_inline_shell_forbidden", errors)

        command["argv"] = ["echo", "ok"]
        self.assertIn(
            "T001.validationCommands[0].validation_command_noop",
            validate_test_tasks(plan),
        )

    def test_plan_requires_required_commands_to_cover_every_acceptance_criterion(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        plan["tasks"][0]["validationCommands"][0]["required"] = False

        self.assertIn(
            "T001.acceptanceCriteria_uncovered:AC-T001-01",
            validate_test_tasks(plan),
        )

    def test_external_dependency_nonempty_validation_plan_reports_only_mode_error(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task.update({
            "executionMode": "external_dependency",
            "externalDependency": {
                "system": "external-system",
                "owner": "external-team",
                "trackingRefs": ["design.md#D-001"],
            },
            "completionPolicy": "external_dependency_recorded",
            "validationCommands": [],
            "validationTestPlan": [{"malformed": True}],
        })

        errors = validate_test_tasks(plan)

        self.assertIn("T001.external_dependency_validationTestPlan_forbidden", errors)
        self.assertFalse(
            any(error.startswith("T001.validationTestPlan") for error in errors),
            errors,
        )

    def test_defer_to_test_stages_validation_test_plan_schema_is_strict(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["validationTestPlan"] = [
            {
                "commandId": "VAL-T001-01",
                "assetType": "unit_test",
                "executionStage": "with_code",
                "covers": ["AC-T001-01"],
                "testIntent": {
                    "behavior": "the behavior is observable",
                    "acceptanceCriteria": task["acceptanceCriteria"],
                },
            }
        ]

        self.assertEqual(
            validate_test_tasks(plan, defer_to_test_stages=True),
            [],
        )

        task["validationTestPlan"][0]["covers"] = ["T001"]
        task["validationTestPlan"][0]["testIntent"]["acceptanceCriteria"] = []
        errors = validate_test_tasks(plan, defer_to_test_stages=True)
        self.assertIn(
            "T001.validationTestPlan[0].covers_unknown:T001",
            errors,
        )
        self.assertIn(
            "T001.validationTestPlan[0].testIntent.acceptanceCriteria_mismatch",
            errors,
        )

    def test_plan_requires_workspace_roots_for_nonempty_scope_paths(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        plan["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]

        self.assertIn("T001.scope.workspaceRoots_missing", validate_test_tasks(plan))

    def test_plan_rejects_scope_path_that_repeats_workspace_root(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["scope"].update({
            "workspaceRoots": {"default": "backend/service"},
            "paths": ["backend/service/src/main/java/example"],
        })
        task["validationCommands"][0]["cwd"] = "backend/service"

        self.assertIn(
            "T001.scope.path_repeats_workspace_root:backend/service/src/main/java/example",
            validate_test_tasks(plan),
        )

    def test_plan_rejects_task_validation_cwd_outside_workspace_root(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["scope"].update({
            "workspaceRoots": {"default": "backend/service"},
            "paths": ["src/main/java/example"],
        })
        task["validationCommands"][0]["cwd"] = "."

        self.assertIn(
            "T001.validationCommands[0].cwd_outside_workspace_root:backend/service",
            validate_test_tasks(plan),
        )

    def test_plan_requires_acceptance_scenarios_to_exist_in_task_spec_refs(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        plan["tasks"][0]["acceptanceCriteria"][0]["scenarioRefs"] = ["#SCN-999"]

        self.assertIn(
            "T001.acceptanceCriteria[0].scenario_not_in_task_specRefs:SCN-999",
            validate_test_tasks(plan),
        )

    def test_root_plan_is_not_a_static_template(self) -> None:
        template_path = ROOT / "skills" / "autodev" / "autodev-plan" / "templates" / "plan.json"

        self.assertFalse(template_path.exists())

    def test_plan_stage_allows_empty_evidence_ids_until_done_gate(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])

        self.assertEqual(validate_test_tasks(plan), [])
        self.assertIn("T001.evidenceIds_missing", validate_test_tasks(plan, require_all_done=True))

    def test_plan_requires_completion_and_project_pointers_to_reference_history(self) -> None:
        plan = valid_plan(status="done", evidence_ids=["ev_0001"])
        task = plan["tasks"][0]
        task["completionEvidenceIds"] = ["ev_0002"]
        task["latestPassEvidenceId"] = "ev_0002"
        plan["projectCheckEvidenceIds"] = ["ev_0003", "ev_0004"]
        plan["latestProjectCheckEvidenceId"] = "ev_0003"

        errors = validate_test_tasks(plan, require_all_done=True)
        errors.extend(validate_plan_data({
            "featureId": "alpha",
            "status": "done",
            "taskSetStatus": "finalized",
            "activeBatchId": None,
            "nextBatchId": None,
            "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
            "batches": [{
                "id": "B001", "path": "plans/B001/plan.json", "title": "capability",
                "specRoots": ["specs/capability/spec.md"], "executionLane": "backend",
                "deps": [], "taskIds": ["T001"], "status": "done",
            }],
            "projectValidationCommands": plan["projectValidationCommands"],
            "projectCheckEvidenceIds": plan["projectCheckEvidenceIds"],
            "latestProjectCheckEvidenceId": plan["latestProjectCheckEvidenceId"],
        }, require_all_done=True))

        self.assertIn("T001.completionEvidenceId_not_in_evidenceIds:ev_0002", errors)
        self.assertIn("latestProjectCheckEvidenceId_not_latest:ev_0003", errors)

    def test_detail_v2_allows_command_without_direct_acceptance_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            record = append_current_evidence(feature_dir, checked_criteria=[])

            self.assertEqual(record["checkedCriteria"], [])

    def test_validate_plan_detects_unknown_dependency_and_cycle(self) -> None:
        plan = valid_plan()
        plan["tasks"].append(
            {
                "id": "T002",
                "title": "two",
                "goal": "deliver two observable behavior",
                "status": "done",
                "deps": ["T003"],
                "workspaceRef": "default",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update the behavior", "cover the boundary"],
                "acceptanceCriteria": ["the behavior is observable"],
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
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

        errors = validate_test_tasks(plan)

        self.assertIn("T002.dependency_unknown:T003", errors)

        plan["tasks"][0]["deps"] = ["T002"]
        plan["tasks"][1]["deps"] = ["T001"]
        errors = validate_test_tasks(plan)

        self.assertTrue(any(error.startswith("task_dependency_cycle:") for error in errors))

    def test_initial_plan_requires_task_details(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        del plan["tasks"][0]["goal"]

        errors = validate_test_tasks(plan, require_initial_status=True)

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

        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.scope.pages_mismatch_uiRefs", errors)

    def test_ui_task_requires_complete_ui_refs(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["uiRequired"] = True
        task["nonGoals"] = ["do not implement unrelated pages"]

        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.uiRefs_missing", errors)

        task["uiRefs"] = {}
        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.uiRefs.pageRefs_missing", errors)
        self.assertIn("T001.uiRefs.interactionRefs_missing", errors)
        self.assertIn("T001.uiRefs.visualSourceRefs_missing", errors)
        self.assertIn("T001.uiRefs.frontendRoute_missing", errors)

    def test_every_task_requires_non_goals_in_detail_schema(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["nonGoals"] = []

        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.nonGoals_missing", errors)

    def test_every_task_requires_non_empty_validation_boundary(self) -> None:
        for boundary in (None, "   ", "too short"):
            plan = valid_plan(status="todo", evidence_ids=[])
            task = plan["tasks"][0]
            if boundary is None:
                task.pop("validationBoundary", None)
            else:
                task["validationBoundary"] = boundary

            errors = validate_test_tasks(plan, require_initial_status=True)

            self.assertIn("T001.validationBoundary_missing_or_too_short", errors)

    def test_every_task_requires_workspace_ref(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        plan["tasks"][0].pop("workspaceRef")

        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.workspaceRef_missing_or_invalid", errors)

    def test_non_goals_reject_blank_items(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        plan["tasks"][0]["nonGoals"] = ["keep scope", "   "]

        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.nonGoals_empty_item", errors)

    def test_task_cannot_bind_multiple_workspace_roots(self) -> None:
        plan = valid_plan(status="todo", evidence_ids=[])
        task = plan["tasks"][0]
        task["workspaceRef"] = "backend-repo"
        task["scope"]["workspaceRoots"] = {
            "backend-repo": ".",
            "frontend-repo": ".",
        }
        task["validationCommands"][0]["repo"] = "backend-repo"

        errors = validate_test_tasks(plan, require_initial_status=True)

        self.assertIn("T001.scope.workspaceRoots_multiple_forbidden", errors)

class EvidenceStoreTest(unittest.TestCase):
    def test_append_rejects_caller_supplied_evidence_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            record = {
                "evidenceId": "ev_0042",
                "featureId": "alpha",
                "checkpoint": "code_in_progress",
                "nodeId": "dev.code",
                "skill": "autodev-code",
                "taskId": "T001",
                "action": "validation",
                "changedFiles": [],
                "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
            }

            with self.assertRaisesRegex(EvidenceStoreError, "evidence_id_must_be_allocated_by_store"):
                append_evidence(feature_dir, record, output_tail="ok\n")

            self.assertFalse(stream_path(feature_dir).exists())

    def test_append_recovers_partially_written_pending_jsonl_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            first = append_pass_evidence(feature_dir)
            first_stream = stream_path(feature_dir).read_bytes()
            first_index = index_path(feature_dir).read_text(encoding="utf-8")
            second = append_pass_evidence(feature_dir)
            second_line = json.dumps(second, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            stream_path(feature_dir).write_bytes(first_stream + second_line[: len(second_line) // 2])
            index_path(feature_dir).write_text(first_index, encoding="utf-8")
            write_pending(feature_dir, second)

            third = append_pass_evidence(feature_dir)

            self.assertEqual(first["evidenceId"], "ev_0001")
            self.assertEqual(third["evidenceId"], "ev_0003")
            self.assertEqual([record["evidenceId"] for record in read_records(stream_path(feature_dir))], [
                "ev_0001",
                "ev_0002",
                "ev_0003",
            ])
            self.assertEqual(check_integrity(feature_dir), [])

    def test_append_recovers_pending_index_before_allocating_next_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            first = append_pass_evidence(feature_dir)
            first_index = index_path(feature_dir).read_text(encoding="utf-8")
            second = append_pass_evidence(feature_dir)
            write_pending(feature_dir, second)
            index_path(feature_dir).write_text(first_index, encoding="utf-8")

            third = append_pass_evidence(feature_dir)

            self.assertEqual(first["evidenceId"], "ev_0001")
            self.assertEqual(third["evidenceId"], "ev_0003")
            restored = read_records(stream_path(feature_dir))[1]
            self.assertEqual(restored, second)
            self.assertFalse((feature_dir / "evidence" / ".pending" / "ev_0002.json").exists())
            self.assertEqual(check_integrity(feature_dir), [])

    def test_append_skips_sidecar_and_hashes_captured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"

            record = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "changedFiles": [],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
                output_tail="command output\n",
            )

            evidence_dir = feature_dir / "evidence"
            sidecar = evidence_dir / "ev_0001.json"
            log = evidence_dir / "ev_0001.log"
            self.assertFalse(sidecar.exists())
            self.assertTrue(log.is_file())
            self.assertEqual(log.read_text(encoding="utf-8"), "command output\n")
            self.assertEqual(
                record["validation"]["outputSha256"],
                hashlib.sha256(b"command output\n").hexdigest(),
            )
            self.assertEqual(record["validation"]["outputBytes"], len(b"command output\n"))

    def test_append_forces_current_artifact_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"

            record = append_evidence(
                feature_dir,
                {
                    "artifactVersion": 999,
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "changedFiles": [],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
                output_tail="ok\n",
            )

            self.assertEqual(record["artifactVersion"], 2)
            self.assertEqual(check_integrity(feature_dir), [])

    def test_append_redacts_secrets_and_truncates_large_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            output = "password=hunter2\nAuthorization: Bearer abc.def\n" + ("x" * 1_100_000)

            record = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "changedFiles": [],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
                output_tail=output,
            )

            log = (feature_dir / "evidence" / "ev_0001.log").read_text(encoding="utf-8")
            validation = record["validation"]
            self.assertNotIn("hunter2", log)
            self.assertNotIn("abc.def", log)
            self.assertIn("[REDACTED]", log)
            self.assertTrue(validation["outputRedacted"])
            self.assertTrue(validation["outputTruncated"])
            self.assertEqual(validation["originalOutputBytes"], len(output.encode("utf-8")))
            self.assertEqual(validation["originalOutputSha256"], hashlib.sha256(output.encode("utf-8")).hexdigest())

    def test_append_rejects_log_that_duplicates_evidence_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            raw_record = {
                "featureId": "alpha",
                "checkpoint": "code_in_progress",
                "nodeId": "dev.code",
                "skill": "autodev-code",
                "taskId": "T001",
                "action": "validation",
                "changedFiles": [],
                "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
            }

            with self.assertRaisesRegex(EvidenceStoreError, "evidence_log_duplicates_record"):
                append_evidence(feature_dir, raw_record, output_tail=json.dumps(raw_record))

    def test_integrity_ignores_unreferenced_sidecar_for_current_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            append_pass_evidence(feature_dir)
            sidecar = feature_dir / "evidence" / "ev_0001.json"

            self.assertEqual(check_integrity(feature_dir), [])

    def test_integrity_keeps_historical_artifact_v1_sidecar_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            current = append_pass_evidence(feature_dir)
            legacy = dict(current)
            legacy["artifactVersion"] = 1
            stream_path(feature_dir).write_text(
                json.dumps(legacy, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            sidecar = write_sidecar(feature_dir, legacy)
            write_index(feature_dir, feature_id="alpha", verify_existing=False)

            self.assertEqual(check_integrity(feature_dir), [])
            sidecar.write_text("{}\n", encoding="utf-8")

            self.assertIn("sidecar_record_mismatch:ev_0001", check_integrity(feature_dir))

    def test_artifact_check_rejects_log_path_bound_to_another_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            record = append_pass_evidence(feature_dir)
            record["validation"]["outputTailPath"] = "evidence/ev_9999.log"

            self.assertIn(
                "evidence_log_path_mismatch:ev_0001",
                check_record_artifacts(feature_dir, record),
            )

    def test_plan_refs_reject_unknown_project_check_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            plan = valid_plan(status="todo", evidence_ids=[])
            plan["projectCheckEvidenceIds"] = ["ev_9999"]
            plan["latestProjectCheckEvidenceId"] = "ev_9999"
            write_test_plan(feature_dir, plan)
            append_pass_evidence(feature_dir)

            errors = check_plan_evidence_refs(feature_dir)

            self.assertIn("unknown_project_check_evidence_id:ev_9999", errors)

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

    def test_validation_evidence_validates_transient_validation_files(self) -> None:
        record = {
            "version": 1,
            "evidenceId": "ev_0001",
            "featureId": "alpha",
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "validation",
            "createdAt": "2026-07-14T00:00:00Z",
            "transientValidationFiles": "src/test/example_test.py",
            "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
        }

        self.assertIn("invalid_transientValidationFiles", validate_record(record))
        record["transientValidationFiles"] = ["src/test/example_test.py"]
        self.assertNotIn("invalid_transientValidationFiles", validate_record(record))

    def test_detail_version_validates_file_changes_projection(self) -> None:
        record = {
            "version": 1,
            "detailVersion": 1,
            "evidenceId": "ev_0001",
            "featureId": "alpha",
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "validation",
            "createdAt": "2026-06-24T00:00:00Z",
            "summary": "实现订单取消状态校验",
            "implementation": {
                "whatChanged": ["OrderService 增加状态判断"],
                "why": "满足 SCN-001 的业务约束",
            },
            "changedFiles": ["src/new/OrderService.java", "src/old/OrderService.java"],
            "fileChanges": [
                {
                    "path": "src/new/OrderService.java",
                    "fromPath": "src/old/OrderService.java",
                    "operation": "renamed",
                    "kind": "source",
                    "summary": "移动服务类到新模块",
                    "symbols": ["OrderService"],
                    "reason": "对齐模块结构",
                }
            ],
            "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
        }

        self.assertEqual(validate_record(record), [])

        record["changedFiles"] = ["src/new/OrderService.java"]
        self.assertIn("invalid_evidence_detail_changedFiles_projection", validate_detail_fields(record))

    def test_detail_version_supports_explicit_no_code_change(self) -> None:
        record = {
            "version": 1,
            "detailVersion": 1,
            "evidenceId": "ev_0001",
            "featureId": "alpha",
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "validation",
            "createdAt": "2026-06-24T00:00:00Z",
            "summary": "验证已有实现满足行为契约",
            "implementation": {
                "noCodeChange": True,
                "whatChanged": [],
                "why": "本条 evidence 只执行验证命令，没有修改代码",
            },
            "changedFiles": [],
            "fileChanges": [],
            "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
        }

        self.assertEqual(validate_record(record), [])

        record["implementation"]["whatChanged"] = ["src/foo.py"]
        self.assertIn("invalid_evidence_detail_noCodeChange_whatChanged", validate_detail_fields(record))

    def test_legacy_evidence_without_detail_version_is_unchanged(self) -> None:
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
            "changedFiles": ["src/foo.py"],
            "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
        }

        self.assertEqual(validate_detail_fields(record), [])

    def test_detail_version_two_requires_captured_log_metadata(self) -> None:
        record = {
            "version": 1,
            "detailVersion": 2,
            "evidenceId": "ev_0001",
            "featureId": "alpha",
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "validation",
            "createdAt": "2026-06-24T00:00:00Z",
            "runId": "run-test",
            "completionMode": "verified_existing",
            "summary": "verified existing behavior",
            "implementation": {"noCodeChange": True, "whatChanged": [], "why": "already implemented"},
            "changedFiles": [],
            "fileChanges": [],
            "supportingFiles": ["src/example.py"],
            "checkedCriteria": ["AC-T001-01"],
            "validation": {
                "commandId": "VAL-T001-01",
                "argv": [sys.executable, "-c", "print('task validation')"],
                "command": f"{sys.executable} -c print('task validation')",
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "exitCode": 0,
                "result": "pass",
            },
        }

        errors = validate_record(record)

        self.assertIn("invalid_evidence_detail_artifactVersion", errors)
        self.assertIn("missing_evidence_detail_validation_outputTailPath", errors)
        self.assertIn("missing_evidence_detail_validation_outputSha256", errors)
        self.assertIn("missing_evidence_detail_validation_outputBytes", errors)

    def test_detail_version_rejects_null_or_unknown_version(self) -> None:
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
            "changedFiles": ["src/foo.py"],
            "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
        }

        record["detailVersion"] = None
        self.assertIn("invalid_evidence_detail_version", validate_detail_fields(record))

        record["detailVersion"] = 3
        self.assertIn("invalid_evidence_detail_version", validate_detail_fields(record))

    def test_detail_version_does_not_require_file_changes_for_non_validation(self) -> None:
        record = {
            "version": 1,
            "detailVersion": 1,
            "evidenceId": "ev_0001",
            "featureId": "alpha",
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "review",
            "createdAt": "2026-06-24T00:00:00Z",
            "summary": "review evidence summary",
        }

        self.assertEqual(validate_detail_fields(record), [])

    def test_append_evidence_rejects_invalid_detailed_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            with self.assertRaisesRegex(EvidenceStoreError, "missing_evidence_detail_summary"):
                append_evidence(
                    feature_dir,
                    {
                        "featureId": "alpha",
                        "checkpoint": "code_in_progress",
                        "nodeId": "dev.code",
                        "skill": "autodev-code",
                        "taskId": "T001",
                        "action": "validation",
                        "detailVersion": 1,
                        "changedFiles": ["src/foo.py"],
                        "fileChanges": [],
                        "validation": {"command": "pytest", "exitCode": 0, "result": "pass"},
                    },
                )

            self.assertFalse(stream_path(feature_dir).exists())

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

    def test_append_cli_rejects_code_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)

            exit_code = evidence_store_main(
                [
                    "append",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
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

            self.assertNotEqual(exit_code, 0)
            self.assertFalse(stream_path(workspace / ".autobizdevops" / "features" / "alpha").exists())

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
    def test_code_done_requires_project_check_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            plan = valid_plan()
            write_test_plan(feature_dir, plan)
            append_current_evidence(feature_dir)

            errors = check_code_done(feature_dir)

            self.assertIn("missing_project_validation_pass:PROJECT-VAL-001", errors)

    def test_code_done_rejects_legacy_completion_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(feature_dir, valid_plan())
            append_pass_evidence(feature_dir)

            errors = check_code_done(feature_dir)

            self.assertIn("T001.completion_evidence_requires_detail_v2:ev_0001", errors)

    def test_code_done_requires_completion_evidence_to_belong_to_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(feature_dir, valid_plan())
            append_current_evidence(feature_dir, task_id="T002")

            errors = check_code_done(feature_dir)

            self.assertIn("T001.evidence_task_mismatch:ev_0001:T002", errors)

    def test_code_done_requires_planned_command_and_acceptance_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(feature_dir, valid_plan())
            append_current_evidence(
                feature_dir,
                command_id="VAL-T001-99",
                checked_criteria=["AC-T001-99"],
            )

            errors = check_code_done(feature_dir)

            self.assertIn("T001.unplanned_validation_command:VAL-T001-99", errors)
            self.assertIn("T001.missing_acceptance_coverage:AC-T001-01", errors)
            self.assertIn("T001.missing_required_validation_pass:VAL-T001-01", errors)

    def test_code_done_gate_requires_structured_done_plan_and_runner_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(feature_dir, valid_plan(status="todo"))
            append_pass_evidence(feature_dir)

            self.assertTrue(any(error.startswith("plan_json:") for error in check_code_done(feature_dir)))

            self.assertTrue(check_code_done(feature_dir))

    def test_code_done_gate_requires_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            append_pass_evidence(feature_dir, task_id="T001")

            self.assertTrue(any("missing_plan_json" in error for error in check_code_done(feature_dir)))

    def test_code_done_gate_rejects_unresolved_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(
                feature_dir,
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
            write_test_plan(feature_dir, valid_plan(status="done", evidence_ids=["ev_0001"]))
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

            errors = check_code_done(feature_dir)
            self.assertTrue(any("completion_evidence_not_pass" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
