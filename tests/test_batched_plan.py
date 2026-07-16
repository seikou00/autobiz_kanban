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
    validate_plan_data,
    write_plan_json,
)
from hooks.plan_writer import record_project_check_attempt  # noqa: E402


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
        "nonGoals": [],
        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
        "designRefs": ["design.md#D-001"],
        "apiIds": [],
        "dataIds": [],
        "decisionIds": ["D-001"],
        "completionPolicy": "all_required_validations_pass",
        "validationCommands": [
            {
                "id": f"VAL-{task_id}-01",
                "argv": ["echo", "ok"],
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
        "batchPolicy": {"maxTasks": 5, "strategy": BATCH_STRATEGY},
        "batches": batches,
        "batchValidationProfiles": {
            "backend": {
                "commands": [
                    {
                        "argv": ["echo", "backend compile"],
                        "cwd": ".",
                        "kind": "compile",
                        "required": True,
                    }
                ]
            },
            "frontend": {
                "commands": [
                    {
                        "argv": ["echo", "frontend build"],
                        "cwd": ".",
                        "kind": "build",
                        "required": True,
                    }
                ]
            },
        },
        "projectValidationCommands": [
            {
                "id": "PROJECT-VAL-001",
                "argv": ["echo", "compile"],
                "cwd": ".",
                "kind": "compile",
                "required": True,
            }
        ],
        "projectCheckEvidenceIds": [],
        "latestProjectCheckEvidenceId": None,
    }


def batch_entry(
    batch_id: str,
    task_ids: list[str],
    *,
    deps: list[str] | None = None,
    execution_lane: str = "backend",
) -> dict:
    return {
        "id": batch_id,
        "path": f"plans/{batch_id}/plan.json",
        "title": f"batch {batch_id}",
        "specRoots": ["specs/cap/spec.md"],
        "executionLane": execution_lane,
        "deps": deps or [],
        "taskIds": task_ids,
        "status": "todo",
    }


def batch_plan(batch_id: str, batch_tasks: list[dict], *, execution_lane: str = "backend") -> dict:
    command = {
        "id": f"BATCH-{batch_id}-VAL-001",
        "argv": ["echo", "frontend build" if execution_lane == "frontend" else "backend compile"],
        "cwd": ".",
        "kind": "build" if execution_lane == "frontend" else "compile",
        "required": True,
    }
    return {
        "featureId": "alpha",
        "batchId": batch_id,
        "title": f"batch {batch_id}",
        "executionLane": execution_lane,
        "status": "todo",
        "taskCount": len(batch_tasks),
        "completedTaskCount": 0,
        "completionEvidenceIds": [],
        "batchValidation": {
            "profile": execution_lane,
            "status": "pending",
            "commands": [command],
            "evidenceIds": [],
            "latestPassEvidenceIds": [],
            "activeRunId": None,
        },
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


def write_plan_state(workspace: Path) -> None:
    state_path = workspace / ".autobizdevops" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
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


class BatchedPlanContractTest(unittest.TestCase):
    def test_plan_writer_projects_lane_batch_validation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            write_plan_state(workspace)

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "hooks" / "plan_writer.py"),
                        *args,
                        "--workspace",
                        str(workspace),
                        "--feature",
                        "alpha",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            body = Path(tmp) / "T001.json"
            body.write_text(json.dumps(task("T001")), encoding="utf-8")
            self.assertEqual(writer("add-task", "--body-file", str(body)).returncode, 0)

            added = writer(
                "add-batch-validation-command",
                "--lane",
                "backend",
                "--command",
                "echo backend compile",
                "--kind",
                "compile",
            )

            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch = json.loads(batch_plan_path(feature_dir, "B001").read_text(encoding="utf-8"))
            self.assertEqual(root["batchValidationProfiles"]["backend"]["commands"][0]["kind"], "compile")
            self.assertEqual(batch["batchValidation"]["commands"][0]["id"], "BATCH-B001-VAL-001")
            self.assertEqual(batch["batchValidation"]["status"], "pending")

    def test_bundle_rejects_project_level_command_in_task_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            item = task("T001")
            item["validationCommands"].append(
                {
                    "id": "VAL-T001-02",
                    "argv": ["echo", "compile"],
                    "cwd": ".",
                    "kind": "compile",
                    "required": True,
                    "covers": [],
                }
            )
            write_bundle(feature_dir, [[item]])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("T001.validationCommands[1].kind_invalid", errors)

    def test_initial_bundle_requires_profile_for_every_used_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001")]])
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            del root["batchValidationProfiles"]["backend"]
            write_plan_json(root_path, root)

            with self.assertRaisesRegex(PlanJsonError, "batchValidationProfiles_missing_lane:backend"):
                load_plan_bundle(feature_dir, require_initial_status=True)

    def test_root_plan_requires_task_set_status(self) -> None:
        plan = root_plan(batches=[batch_entry("B001", ["T001"])])
        del plan["taskSetStatus"]

        self.assertIn("plan_json_taskSetStatus_invalid", validate_plan_data(plan))

    def test_monolithic_root_plan_requires_rebuild(self) -> None:
        monolithic = root_plan(batches=[])
        monolithic["tasks"] = [task("T001")]

        self.assertIn("monolithic_plan_requires_rebuild", validate_plan_data(monolithic))

    def test_bundle_rejects_more_than_five_tasks_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            tasks = [task(f"T{index:03d}") for index in range(1, 7)]
            write_bundle(feature_dir, [tasks])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("B001.batch_task_limit_exceeded:6>5", errors)

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

    def test_plan_writer_splits_six_same_spec_tasks_into_five_and_one(self) -> None:
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

            init = subprocess.run(
                [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), "init", "--workspace", str(workspace), "--feature", "alpha"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            for index in range(1, 7):
                body = Path(tmp) / f"task-{index}.json"
                body.write_text(json.dumps(task(f"T{index:03d}")), encoding="utf-8")
                added = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "hooks" / "plan_writer.py"),
                        "add-task",
                        "--workspace",
                        str(workspace),
                        "--feature",
                        "alpha",
                        "--body-file",
                        str(body),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            first = json.loads(batch_plan_path(feature_dir, "B001").read_text(encoding="utf-8"))
            second = json.loads(batch_plan_path(feature_dir, "B002").read_text(encoding="utf-8"))

            self.assertNotIn("tasks", root)
            self.assertEqual([entry["id"] for entry in root["batches"]], ["B001", "B002"])
            self.assertEqual(len(first["tasks"]), 5)
            self.assertEqual(len(second["tasks"]), 1)
            self.assertEqual(root["activeBatchId"], "B001")
            self.assertEqual(root["nextBatchId"], "B002")

    def test_plan_writer_starts_frontend_task_in_new_batch_for_same_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            write_plan_state(workspace)

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args, "--workspace", str(workspace), "--feature", "alpha"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            for item in (task("T001"), task("T002", deps=["T001"], ui_required=True)):
                body = Path(tmp) / f"{item['id']}.json"
                body.write_text(json.dumps(item), encoding="utf-8")
                added = writer("add-task", "--body-file", str(body))
                self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["taskIds"] for entry in root["batches"]], [["T001"], ["T002"]])

            backend = json.loads(batch_plan_path(feature_dir, "B001").read_text(encoding="utf-8"))
            frontend = json.loads(batch_plan_path(feature_dir, "B002").read_text(encoding="utf-8"))
            self.assertEqual([entry["executionLane"] for entry in root["batches"]], ["backend", "frontend"])
            self.assertEqual(backend["executionLane"], "backend")
            self.assertEqual(frontend["executionLane"], "frontend")

    def test_plan_writer_rejects_backend_task_after_frontend_collection_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            write_plan_state(workspace)

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args, "--workspace", str(workspace), "--feature", "alpha"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            for item in (task("T001"), task("T002", deps=["T001"], ui_required=True)):
                body = Path(tmp) / f"{item['id']}.json"
                body.write_text(json.dumps(item), encoding="utf-8")
                self.assertEqual(writer("add-task", "--body-file", str(body)).returncode, 0)

            body = Path(tmp) / "T003.json"
            body.write_text(json.dumps(task("T003", deps=["T001"])), encoding="utf-8")
            added = writer("add-task", "--body-file", str(body))

            self.assertNotEqual(added.returncode, 0)
            self.assertIn("backend_task_after_frontend", added.stdout + added.stderr)

    def test_plan_writer_finalizes_only_after_complete_scenario_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                "\n".join(
                    [
                        "## ADDED Requirements",
                        "### Requirement [REQ-001]: capability",
                        "#### Scenario [SCN-001]: happy path",
                        "#### Scenario [SCN-002]: alternate path",
                    ]
                ),
                encoding="utf-8",
            )
            write_plan_state(workspace)

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args, "--workspace", str(workspace), "--feature", "alpha"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            first_body = Path(tmp) / "T001.json"
            first_body.write_text(json.dumps(task("T001")), encoding="utf-8")
            self.assertEqual(writer("add-task", "--body-file", str(first_body)).returncode, 0)

            incomplete = writer("finalize-task-set")
            self.assertNotEqual(incomplete.returncode, 0)
            self.assertIn("missing_plan_scenario_coverage", incomplete.stdout + incomplete.stderr)

            second = task("T002", deps=["T001"])
            second["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            second["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/cap/spec.md#SCN-002"]
            second_body = Path(tmp) / "T002.json"
            second_body.write_text(json.dumps(second), encoding="utf-8")
            self.assertEqual(writer("add-task", "--body-file", str(second_body)).returncode, 0)

            finalized = writer("finalize-task-set")
            self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["taskSetStatus"], "finalized")

            third_body = Path(tmp) / "T003.json"
            third_body.write_text(json.dumps(task("T003", deps=["T002"])), encoding="utf-8")
            locked = writer("add-task", "--body-file", str(third_body))
            self.assertNotEqual(locked.returncode, 0)
            self.assertIn("plan_task_set_finalized", locked.stdout + locked.stderr)

            dependency_update = writer("set-deps", "--task-id", "T002", "--dep", "T001")
            self.assertNotEqual(dependency_update.returncode, 0)
            self.assertIn("plan_task_set_finalized", dependency_update.stdout + dependency_update.stderr)

            runtime_update = writer("set-status", "--task-id", "T002", "failed")
            self.assertEqual(runtime_update.returncode, 0, runtime_update.stdout + runtime_update.stderr)

    def test_plan_writer_finalization_scans_all_spec_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                "\n".join(
                    [
                        "## ADDED Requirements",
                        "### Requirement [REQ-001]: capability",
                        "#### Scenario [SCN-001]: happy path",
                    ]
                ),
                encoding="utf-8",
            )
            (spec_dir / "supplement.md").write_text(
                "#### Scenario [SCN-002]: supplemental path",
                encoding="utf-8",
            )
            write_plan_state(workspace)
            body = Path(tmp) / "T001.json"
            body.write_text(json.dumps(task("T001")), encoding="utf-8")

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args, "--workspace", str(workspace), "--feature", "alpha"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            self.assertEqual(writer("add-task", "--body-file", str(body)).returncode, 0)

            finalized = writer("finalize-task-set")

            self.assertNotEqual(finalized.returncode, 0)
            self.assertIn("specs/cap/supplement.md#SCN-002", finalized.stdout + finalized.stderr)

    def test_bundle_rejects_mixed_execution_lane_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001"), task("T002", ui_required=True)]])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("B001.mixed_execution_lanes", errors)

    def test_bundle_rejects_backend_batch_after_frontend_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            frontend_task = task("T001", ui_required=True)
            backend_task = task("T002")
            entries = [
                batch_entry("B001", ["T001"], execution_lane="frontend"),
                batch_entry("B002", ["T002"], deps=["B001"], execution_lane="backend"),
            ]
            write_plan_json(batch_plan_path(feature_dir, "B001"), batch_plan("B001", [frontend_task], execution_lane="frontend"))
            write_plan_json(batch_plan_path(feature_dir, "B002"), batch_plan("B002", [backend_task], execution_lane="backend"))
            write_plan_json(feature_dir / "plan.json", root_plan(batches=entries, next_batch="B002"))

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertIn("backend_batch_after_frontend:B002", errors)

    def test_plan_writer_does_not_backfill_an_earlier_capability_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {"alpha": {"feature": "alpha", "checkpoint": "plan_in_progress", "stage": "Plan", "iteration": "1"}},
                    }
                ),
                encoding="utf-8",
            )

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args, "--workspace", str(workspace), "--feature", "alpha"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            tasks = [task("T001"), task("T002", deps=["T001"]), task("T003", deps=["T002"])]
            tasks[0]["specRefs"] = ["specs/a/spec.md#REQ-001", "specs/a/spec.md#SCN-001"]
            tasks[0]["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/a/spec.md#SCN-001"]
            tasks[1]["specRefs"] = ["specs/b/spec.md#REQ-001", "specs/b/spec.md#SCN-001"]
            tasks[1]["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/b/spec.md#SCN-001"]
            tasks[2]["specRefs"] = ["specs/a/spec.md#REQ-001", "specs/a/spec.md#SCN-001"]
            tasks[2]["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/a/spec.md#SCN-001"]
            for item in tasks:
                body = Path(tmp) / f"{item['id']}.json"
                body.write_text(json.dumps(item), encoding="utf-8")
                added = writer("add-task", "--body-file", str(body))
                self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["taskIds"] for entry in root["batches"]], [["T001"], ["T002"], ["T003"]])
            self.assertEqual(root["batches"][2]["deps"], ["B002"])

    def test_plan_writer_rejects_updates_that_create_forward_batch_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {"alpha": {"feature": "alpha", "checkpoint": "plan_in_progress", "stage": "Plan", "iteration": "1"}},
                    }
                ),
                encoding="utf-8",
            )

            def writer(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args, "--workspace", str(workspace), "--feature", "alpha"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertEqual(writer("init").returncode, 0)
            first = task("T001")
            first["specRefs"] = ["specs/a/spec.md#REQ-001", "specs/a/spec.md#SCN-001"]
            first["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/a/spec.md#SCN-001"]
            second = task("T002")
            second["specRefs"] = ["specs/b/spec.md#REQ-001", "specs/b/spec.md#SCN-001"]
            second["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/b/spec.md#SCN-001"]
            for item in (first, second):
                body = Path(tmp) / f"{item['id']}.json"
                body.write_text(json.dumps(item), encoding="utf-8")
                added = writer("add-task", "--body-file", str(body))
                self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

            updated = writer("set-deps", "--task-id", "T001", "--dep", "T002")

            self.assertNotEqual(updated.returncode, 0)
            self.assertIn("dependency_not_in_earlier_batch", updated.stdout + updated.stderr)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["batches"][0]["deps"], [])

    def test_failed_project_check_keeps_root_status_failed_after_batch_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            done_task = task("T001", status="done")
            write_plan_json(
                batch_plan_path(feature_dir, "B001"),
                {
                    **batch_plan("B001", [done_task]),
                    "status": "done",
                    "completedTaskCount": 1,
                    "completedAt": "2026-07-10T00:00:00Z",
                },
            )
            entry = batch_entry("B001", ["T001"])
            entry["status"] = "done"
            write_plan_json(
                feature_dir / "plan.json",
                root_plan(batches=[entry], active=None, next_batch=None),
            )

            result = record_project_check_attempt(workspace, "alpha", ["ev_0001"], success=False)

            self.assertTrue(result.ok, result.errors)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["status"], "failed")
            self.assertIsNone(root["latestProjectCheckEvidenceId"])


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
    def test_incomplete_batch_returns_next_runnable_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "artifacts"
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "alpha": {
                                "feature": "alpha",
                                "checkpoint": "code_in_progress",
                                "stage": "Code",
                                "iteration": "1",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_bundle(
                feature_dir,
                [[task("T001"), task("T002", deps=["T001"]), task("T003", deps=["T002"])]],
            )
            code = root / "code"
            code.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=code, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=code, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=code, check=True)
            (code / ".git" / "info" / "exclude").write_text(
                ".cmbdevclaw/large_tool_results/\n", encoding="utf-8"
            )
            (code / "existing.txt").write_text("already implemented\n", encoding="utf-8")
            subprocess.run(["git", "add", "existing.txt"], cwd=code, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=code, check=True, capture_output=True)

            def runner(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "task_runner.py"), *args],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            started = runner(
                "start",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--code-workspace",
                str(code),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            run_id = json.loads(started.stdout)["runId"]

            completed = runner(
                "complete",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--run-id",
                run_id,
                "--code-workspace",
                str(code),
                "--no-code-change-why",
                "behavior already exists",
                "--supporting-file",
                "existing.txt",
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["requiredAction"], "continue_active_batch")
            self.assertTrue(payload["continueCurrentBatch"])
            self.assertEqual(payload["activeBatchId"], "B001")
            self.assertEqual(payload["nextTaskId"], "T002")
            self.assertFalse(payload["stopAfterBatch"])
            self.assertIsNone(payload["batchHandoff"])

            next_started = runner(
                "start",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T002",
                "--code-workspace",
                str(code),
            )
            self.assertEqual(next_started.returncode, 0, next_started.stdout + next_started.stderr)
            next_run_id = json.loads(next_started.stdout)["runId"]
            next_completed = runner(
                "complete",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T002",
                "--run-id",
                next_run_id,
                "--code-workspace",
                str(code),
                "--no-code-change-why",
                "behavior already exists",
                "--supporting-file",
                "existing.txt",
            )

            self.assertEqual(next_completed.returncode, 0, next_completed.stdout + next_completed.stderr)
            next_payload = json.loads(next_completed.stdout)
            self.assertEqual(next_payload["requiredAction"], "continue_active_batch")
            self.assertTrue(next_payload["continueCurrentBatch"])
            self.assertEqual(next_payload["activeBatchId"], "B001")
            self.assertEqual(next_payload["nextTaskId"], "T003")
            self.assertFalse(next_payload["stopAfterBatch"])

    def test_non_final_batch_requires_new_conversation_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "artifacts"
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "alpha": {
                                "feature": "alpha",
                                "checkpoint": "code_in_progress",
                                "stage": "Code",
                                "iteration": "1",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_bundle(feature_dir, [[task("T001")], [task("T002", deps=["T001"])]] )
            code = root / "code"
            code.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=code, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=code, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=code, check=True)
            (code / ".git" / "info" / "exclude").write_text(
                ".cmbdevclaw/large_tool_results/\n", encoding="utf-8"
            )
            (code / "existing.txt").write_text("already implemented\n", encoding="utf-8")
            subprocess.run(["git", "add", "existing.txt"], cwd=code, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=code, check=True, capture_output=True)

            def runner(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "task_runner.py"), *args],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            started = runner(
                "start",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--code-workspace",
                str(code),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            run_id = json.loads(started.stdout)["runId"]
            completed = runner(
                "complete",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--run-id",
                run_id,
                "--code-workspace",
                str(code),
                "--no-code-change-why",
                "behavior already exists",
                "--supporting-file",
                "existing.txt",
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed_payload = json.loads(completed.stdout)
            self.assertTrue(completed_payload["stopAfterBatch"])
            self.assertFalse(completed_payload["continueCurrentBatch"])
            self.assertIsNone(completed_payload["nextTaskId"])
            self.assertTrue(completed_payload["requiresNewConversation"])
            self.assertEqual(completed_payload["requiredAction"], "stop_and_open_new_conversation")
            self.assertEqual(
                completed_payload["userMessage"],
                "当前批次 B001 已完成，请打开新的对话继续执行 B002。",
            )
            self.assertEqual(completed_payload["batchHandoff"]["nextBatchId"], "B002")
            self.assertTrue(completed_payload["batchHandoff"]["requiresNewConversation"])
            self.assertEqual(
                completed_payload["batchHandoff"]["instruction"],
                "当前批次 B001 已完成，请打开新的对话继续执行 B002。",
            )
            self.assertIn("task_runner.py code-session", completed_payload["batchHandoff"]["activationCommand"])
            self.assertNotIn("activate-batch", completed_payload["batchHandoff"]["activationCommand"])
            self.assertNotIn("--batch-id", completed_payload["batchHandoff"]["activationCommand"])
            self.assertNotIn("Open a new conversation", completed.stdout)
            root_plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root_plan["status"], "awaiting_next_conversation")
            self.assertIsNone(root_plan["activeBatchId"])
            self.assertEqual(root_plan["nextBatchId"], "B002")
            self.assertTrue((feature_dir / "BATCH_HANDOFF.json").is_file())

            blocked = runner(
                "start",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T002",
                "--code-workspace",
                str(code),
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("batch_handoff_requires_new_conversation:B002", blocked.stdout)

            activated = runner(
                "code-session",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
            )
            self.assertEqual(activated.returncode, 0, activated.stdout + activated.stderr)
            activated_payload = json.loads(activated.stdout)
            self.assertEqual(activated_payload["action"], "execute_active_batch")
            self.assertEqual(activated_payload["activeBatchId"], "B002")
            self.assertTrue(activated_payload["activatedFromHandoff"])
            self.assertFalse((feature_dir / "BATCH_HANDOFF.json").exists())
            activated_root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(activated_root["activeBatchId"], "B002")

    def test_recover_can_finish_run_state_after_batch_handoff_was_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "artifacts"
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {}}),
                encoding="utf-8",
            )
            write_bundle(feature_dir, [[task("T001")], [task("T002", deps=["T001"])]] )
            code = root / "code"
            code.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=code, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=code, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=code, check=True)
            (code / ".git" / "info" / "exclude").write_text(
                ".cmbdevclaw/large_tool_results/\n", encoding="utf-8"
            )
            (code / "existing.txt").write_text("already implemented\n", encoding="utf-8")
            subprocess.run(["git", "add", "existing.txt"], cwd=code, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=code, check=True, capture_output=True)

            def runner(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "task_runner.py"), *args],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            started = runner(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            run_id = json.loads(started.stdout)["runId"]
            completed = runner(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", run_id, "--code-workspace", str(code),
                "--no-code-change-why", "already implemented", "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            run_path = feature_dir / ".task-runs" / "T001" / f"{run_id}.json"
            run_state = json.loads(run_path.read_text(encoding="utf-8"))
            run_state["status"] = "evidence_written"
            run_path.write_text(json.dumps(run_state, indent=2) + "\n", encoding="utf-8")

            recovered = runner(
                "recover", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", run_id, "--code-workspace", str(code),
                "--no-code-change-why", "already implemented", "--supporting-file", "existing.txt",
            )

            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertEqual(json.loads(run_path.read_text(encoding="utf-8"))["status"], "done")
            recovered_payload = json.loads(recovered.stdout)
            self.assertEqual(recovered_payload["batchHandoff"]["nextBatchId"], "B002")
            self.assertTrue(recovered_payload["stopAfterBatch"])
            self.assertTrue(recovered_payload["requiresNewConversation"])
            self.assertEqual(recovered_payload["requiredAction"], "stop_and_open_new_conversation")
            self.assertEqual(
                recovered_payload["userMessage"],
                "当前批次 B001 已完成，请打开新的对话继续执行 B002。",
            )


if __name__ == "__main__":
    unittest.main()
