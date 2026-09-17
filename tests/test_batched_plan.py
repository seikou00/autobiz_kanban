from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.code_task_context import build_context  # noqa: E402
from hooks.evidence_store import append_evidence, main as evidence_store_main  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    BATCH_STRATEGY,
    PlanJsonError,
    batch_plan_path,
    load_and_validate_plan,
    load_plan_bundle,
    task_set_digest,
    validate_plan_data,
    validate_plan_bundle_data,
    write_plan_json,
)
from hooks.plan_writer import _project_batches  # noqa: E402

def task(
    task_id: str,
    *,
    deps: list[str] | None = None,
    status: str = "todo",
    ui_required: bool = False,
) -> dict:
    item = {
        "id": task_id,
        "title": f"task {task_id}",
        "goal": f"deliver {task_id}",
        "status": status,
        "deps": deps or [],
        "uiRequired": ui_required,
        "workspaceRef": "default",
        "scope": {
            "modules": ["src"],
            "entrypoints": [],
            "pages": ["PAGE-001"] if ui_required else [],
            "dataObjects": [],
        },
        "implementationPoints": ["implement behavior", "cover boundary"],
        "acceptanceCriteria": [
            {
                "id": f"AC-{task_id}-01",
                "text": "behavior is observable",
                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
            }
        ],
        "validationBoundary": "public behavior seam validated by the task command",
        "nonGoals": ["do not change unrelated behavior"],
        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
        "designRefs": ["design.md#D-001"],
        "apiIds": [],
        "dataIds": [],
        "decisionIds": ["D-001"],
        "completionPolicy": "all_required_validations_pass",
        "validationCommands": [
            {
                "id": f"VAL-{task_id}-01",
                "argv": [sys.executable, "-c", "print('task validation')"],
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "covers": [f"AC-{task_id}-01"],
            }
        ],
        "expectedFiles": [],
        "evidenceIds": [],
        "completionEvidenceIds": [],
        "latestPassEvidenceId": None,
        "blockers": [],
    }
    if ui_required:
        item["uiRefs"] = {
            "pageRefs": ["PAGE-001"],
            "interactionRefs": ["UIX-001"],
            "visualSourceRefs": [],
            "frontendRoute": "spec-driven-ui",
        }
        item["nonGoals"] = ["do not change unrelated UI behavior"]
    return item


def root_plan(*, batches: list[dict], active: str | None = "B001", next_batch: str | None = None) -> dict:
    return {
        "featureId": "alpha",
        "status": "todo",
        "taskSetStatus": "finalized",
        "activeBatchId": active,
        "nextBatchId": next_batch,
        "taskValidationPolicy": {
            "mode": "defer_to_test_stages",
            "orchestration": "inline",
            "codeGate": "review_only",
            "maxTestStageRepairAttempts": 3,
        },
        "batchPolicy": {"maxTasks": 3, "strategy": BATCH_STRATEGY},
        "batches": batches,
    }


def batch_entry(
    batch_id: str,
    task_ids: list[str],
    *,
    deps: list[str] | None = None,
    execution_lane: str = "backend",
) -> dict:
    atomic = len(task_ids) > 1
    return {
        "id": batch_id,
        "path": f"plans/{batch_id}/plan.json",
        "title": f"batch {batch_id}",
        "specRoots": ["specs/cap/spec.md"],
        "executionLane": execution_lane,
        "deps": deps or [],
        "taskIds": task_ids,
        "deliveryKind": "atomic_group" if atomic else "single_task",
        **({"atomicGroupId": "AG001", "batchRationale": "test-only inseparable delivery loop"} if atomic else {}),
        "status": "todo",
    }


def batch_plan(batch_id: str, batch_tasks: list[dict], *, execution_lane: str = "backend") -> dict:
    atomic = len(batch_tasks) > 1
    return {
        "featureId": "alpha",
        "batchId": batch_id,
        "title": f"batch {batch_id}",
        "executionLane": execution_lane,
        "status": "todo",
        "taskCount": len(batch_tasks),
        "completedTaskCount": 0,
        "completionEvidenceIds": [],
        "deliveryKind": "atomic_group" if atomic else "single_task",
        **({"atomicGroupId": "AG001", "batchRationale": "test-only inseparable delivery loop"} if atomic else {}),
        "startedAt": None,
        "completedAt": None,
        "tasks": batch_tasks,
    }


def write_bundle(feature_dir: Path, batches: list[list[dict]]) -> None:
    entries = []
    for index, batch_tasks in enumerate(batches, start=1):
        batch_id = f"B{index:03d}"
        deps = [f"B{index - 1:03d}"] if index > 1 else []
        entries.append(batch_entry(batch_id, [item["id"] for item in batch_tasks], deps=deps))
        write_plan_json(batch_plan_path(feature_dir, batch_id), batch_plan(batch_id, batch_tasks))
    write_plan_json(
        feature_dir / "plan.json",
        root_plan(
            batches=entries,
            active="B001" if entries else None,
            next_batch="B002" if len(entries) > 1 else None,
        ),
    )


class BatchedPlanContractTest(unittest.TestCase):
    def test_bundle_rejects_shared_write_path_across_tasks(self) -> None:
        first = task("T001")
        second = task("T002")
        first["scope"]["paths"] = ["sql/marketing.sql"]
        second["expectedFiles"] = ["sql/marketing.sql"]
        root = root_plan(batches=[
            batch_entry("B001", ["T001"]),
            batch_entry("B002", ["T002"]),
        ])

        errors = validate_plan_bundle_data(
            root,
            {
                "B001": batch_plan("B001", [first]),
                "B002": batch_plan("B002", [second]),
            },
        )

        self.assertIn(
            "shared_write_path_requires_single_owner:workspace=default:path=sql/marketing.sql:taskIds=T001,T002",
            errors,
        )

    def test_load_plan_bundle_rejects_task_outside_implementation_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "feature"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001")]])
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["implementationScope"] = "frontend_only"
            write_plan_json(root_path, root)

            with self.assertRaisesRegex(
                PlanJsonError,
                "T001\\.implementation_scope_frontend_only_required:frontend_only",
            ):
                load_plan_bundle(feature_dir)


    def test_task_validation_policy_is_required_and_bound_to_digest(self) -> None:
        root = root_plan(batches=[batch_entry("B001", ["T001"])])
        batch = batch_plan("B001", [task("T001")])
        policy_digest = task_set_digest(root, {"B001": batch})

        root.pop("taskValidationPolicy")
        self.assertNotEqual(task_set_digest(root, {"B001": batch}), policy_digest)
        self.assertIn("taskValidationPolicy_missing", validate_plan_data(root))


    def test_bundle_rejects_project_level_command_in_task_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            item = task("T001")
            item["validationCommands"].append(
                {
                    "id": "VAL-T001-02",
                    "argv": ["mvn", "compile", "-q"],
                    "cwd": ".",
                    "kind": "compile",
                    "required": True,
                    "covers": [],
                }
            )
            write_bundle(feature_dir, [[item]])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("T001.validationCommands[1].kind_invalid_for_lane:backend", errors)

    def test_bundle_rejects_disguised_compile_and_unscoped_maven_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            item = task("T001")
            item["validationCommands"][0].update({
                "argv": ["mvn.cmd", "compile", "-q"],
                "kind": "integration_test",
            })
            write_bundle(feature_dir, [[item]])
            _, compile_errors = load_and_validate_plan(feature_dir / "plan.json")
            self.assertIn("T001.validationCommands[0].batch_owned_command", compile_errors)

            item["validationCommands"][0]["argv"] = ["mvn.cmd", "test", "-q"]
            write_bundle(feature_dir, [[item]])
            _, test_errors = load_and_validate_plan(feature_dir / "plan.json")
            self.assertIn("T001.validationCommands[0].maven_test_selector_missing", test_errors)

            item["validationCommands"][0]["argv"] = [
                "mvn.cmd", "test", "-Dtest=ProtocolCtrlApplyTest", "-DskipTests=true"
            ]
            write_bundle(feature_dir, [[item]])
            _, bypass_errors = load_and_validate_plan(feature_dir / "plan.json")
            self.assertIn(
                "T001.validationCommands[0].maven_test_execution_skipped",
                bypass_errors,
            )


    def test_initial_bundle_allows_missing_compile_profile_for_used_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001")]])
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            load_plan_bundle(feature_dir, require_initial_status=True)

    def test_root_plan_requires_task_set_status(self) -> None:
        plan = root_plan(batches=[batch_entry("B001", ["T001"])])
        del plan["taskSetStatus"]

        self.assertIn("plan_json_taskSetStatus_invalid", validate_plan_data(plan))

    def test_monolithic_root_plan_requires_rebuild(self) -> None:
        monolithic = root_plan(batches=[])
        monolithic["tasks"] = [task("T001")]

        self.assertIn("monolithic_plan_requires_rebuild", validate_plan_data(monolithic))

    def test_bundle_rejects_more_than_three_tasks_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            tasks = [task(f"T{index:03d}") for index in range(1, 7)]
            write_bundle(feature_dir, [tasks])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

        self.assertIn("B001.atomic_group_task_limit_invalid", errors)

    def test_bundle_loads_flat_task_view_without_putting_tasks_in_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001")], [task("T002", deps=["T001"])]] )

            bundle = load_plan_bundle(feature_dir)
            root_file = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))

            self.assertNotIn("tasks", root_file)
            self.assertEqual([item["id"] for item in bundle.tasks], ["T001", "T002"])
            self.assertEqual(bundle.task_batches, {"T001": "B001", "T002": "B002"})

    def test_bundle_rejects_forward_batch_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001", deps=["T002"])], [task("T002")]])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("T001.dependency_not_in_earlier_batch:T002", errors)

    def test_code_context_rejects_task_outside_active_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "alpha": {
                                "feature": "alpha",
                                "checkpoint": "plan_in_progress",
                                "stage": "Plan",
                                "iteration": "1",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_bundle(feature_dir, [[task("T001")], [task("T002", deps=["T001"])]] )

            result = build_context(workspace=workspace, feature="alpha", task_id="T002")

            self.assertFalse(result.ok)
            self.assertEqual(result.errors[0]["reason"], "task_not_in_active_batch")


    def test_explicit_atomic_group_projects_two_tasks_to_one_auditable_batch(self) -> None:
        first = task("T001")
        second = task("T002", deps=["T001"])
        atomic = {
            "id": "AG001",
            "rationale": "Both changes form one state migration that cannot be accepted separately.",
        }
        first["atomicGroup"] = atomic
        second["atomicGroup"] = atomic
        data = root_plan(batches=[])
        data["tasks"] = [first, second]
        data["_batchAssignments"] = {}
        data["_batchPlans"] = {}

        root, batches = _project_batches(data)

        self.assertEqual(root["batchPolicy"], {"maxTasks": 3, "strategy": BATCH_STRATEGY})
        self.assertEqual(len(root["batches"]), 1)
        entry = root["batches"][0]
        self.assertEqual(entry["taskIds"], ["T001", "T002"])
        self.assertEqual(entry["deliveryKind"], "atomic_group")
        self.assertEqual(entry["atomicGroupId"], "AG001")
        self.assertEqual(entry["batchRationale"], atomic["rationale"])
        self.assertEqual(batches[entry["id"]]["deliveryKind"], "atomic_group")
        self.assertEqual(validate_plan_bundle_data(root, batches), [])

    def test_atomic_group_rejects_cross_workspace_members(self) -> None:
        first = task("T001")
        second = task("T002")
        atomic = {
            "id": "AG001",
            "rationale": "Both changes form one state migration that cannot be accepted separately.",
        }
        first["atomicGroup"] = atomic
        second["atomicGroup"] = atomic
        second["workspaceRef"] = "other"
        second["scope"]["workspaceRoots"] = {"other": "."}
        root = root_plan(batches=[batch_entry("B001", ["T001", "T002"])])
        batch = batch_plan("B001", [first, second])

        errors = validate_plan_bundle_data(root, {"B001": batch})

        self.assertIn("atomicGroup.AG001_workspaceRef_mismatch:taskIds=T001,T002", errors)


    def test_bundle_rejects_mixed_execution_lane_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001"), task("T002", ui_required=True)]])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("B001.mixed_execution_lanes", errors)

    def test_bundle_allows_dependency_order_to_cross_execution_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            frontend_task = task("T001", ui_required=True)
            backend_task = task("T002")
            backend_task["deps"] = ["T001"]
            entries = [
                batch_entry("B001", ["T001"], execution_lane="frontend"),
                batch_entry("B002", ["T002"], deps=["B001"], execution_lane="backend"),
            ]
            write_plan_json(batch_plan_path(feature_dir, "B001"), batch_plan("B001", [frontend_task], execution_lane="frontend"))
            write_plan_json(batch_plan_path(feature_dir, "B002"), batch_plan("B002", [backend_task], execution_lane="backend"))
            write_plan_json(feature_dir / "plan.json", root_plan(batches=entries, next_batch="B002"))

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

        self.assertNotIn("backend_batch_after_frontend:B002", errors)
        self.assertNotIn("T002.backend_dependency_on_frontend:T001", errors)


class EvidenceLayoutContractTest(unittest.TestCase):
    def test_new_evidence_uses_jsonl_and_log_without_json_sidecar(self) -> None:
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
                output_tail="real command output\n",
            )

            evidence_id = record["evidenceId"]
            self.assertEqual(record["artifactVersion"], 2)
            self.assertTrue((feature_dir / "evidence" / "EVIDENCE.jsonl").is_file())
            self.assertTrue((feature_dir / "evidence" / f"{evidence_id}.log").is_file())
            self.assertFalse((feature_dir / "evidence" / f"{evidence_id}.json").exists())

    def test_show_reads_one_record_from_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            record = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "manual-test",
                    "taskId": "T001",
                    "action": "validation",
                    "changedFiles": [],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
                output_tail="ok\n",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = evidence_store_main(
                    [
                        "show",
                        "--workspace",
                        str(workspace),
                        "--feature",
                        "alpha",
                        "--evidence-id",
                        record["evidenceId"],
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["evidenceId"], record["evidenceId"])


class BatchRunnerContractTest(unittest.TestCase):
    def test_legacy_complete_cli_is_removed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "task_runner.py"), "complete"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice: 'complete'", result.stderr)

    def test_legacy_batch_check_cli_is_removed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "task_runner.py"), "batch-check"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice: 'batch-check'", result.stderr)

    def test_legacy_validation_recovery_cli_is_removed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "task_runner.py"), "start-batch-task-validation"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice: 'start-batch-task-validation'", result.stderr)

if __name__ == "__main__":
    unittest.main()
