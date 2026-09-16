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
from hooks.design_contract_lock import sync_design_contract_lock  # noqa: E402
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
        "qualityGateProfiles": {},
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
        "qualityGateCommands": [],
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

    def test_batch_and_project_commands_reject_noop_validation(self) -> None:
        root = root_plan(batches=[batch_entry("B001", ["T001"])])
        root["projectValidationCommands"][0]["argv"] = ["echo", "integration"]

        errors = validate_plan_data(root, require_backend_compile=True)

        self.assertNotIn(
            "compileProfiles.backend.commands[0].validation_command_noop",
            errors,
        )
        self.assertIn("projectValidationCommands[0].validation_command_noop", errors)

    def test_backend_batch_does_not_require_a_compile_profile(self) -> None:
        root = root_plan(batches=[batch_entry("B001", ["T001"])])

        errors = validate_plan_data(root, require_backend_compile=True)

        self.assertEqual(errors, [])

    def test_compile_and_quality_commands_are_bound_to_task_set_digest(self) -> None:
        root = root_plan(batches=[batch_entry("B001", ["T001"])])
        batch = batch_plan("B001", [task("T001")])
        legacy_digest = task_set_digest(root, {"B001": batch})

        self.assertEqual(task_set_digest(root, {"B001": batch}), legacy_digest)

    def test_task_validation_policy_is_required_and_bound_to_digest(self) -> None:
        root = root_plan(batches=[batch_entry("B001", ["T001"])])
        batch = batch_plan("B001", [task("T001")])
        policy_digest = task_set_digest(root, {"B001": batch})

        root.pop("taskValidationPolicy")
        self.assertNotEqual(task_set_digest(root, {"B001": batch}), policy_digest)
        self.assertIn("taskValidationPolicy_missing", validate_plan_data(root))

    def test_finalized_plan_rejects_retired_compile_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001")]])

            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            load_plan_bundle(feature_dir)

            root["compileProfiles"] = {"backend": {"commands": []}}
            write_plan_json(root_path, root)
            with self.assertRaisesRegex(PlanJsonError, "compileProfiles_retired"):
                load_plan_bundle(feature_dir)

    def test_project_validation_rejects_batch_kinds(self) -> None:
        base = root_plan(batches=[batch_entry("B001", ["T001"])])
        base["projectValidationCommands"][0]["kind"] = "compile"
        errors = validate_plan_data(base)

        self.assertIn("projectValidationCommands[0].kind_invalid", errors)

    def test_bundle_rejects_retired_compile_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_bundle(feature_dir, [[task("T001")]])
            batch_path = batch_plan_path(feature_dir, "B001")
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["compileCommand"] = {"id": "BATCH-B001-COMPILE", "kind": "compile", "required": True}
            write_plan_json(batch_path, batch)

            with self.assertRaisesRegex(PlanJsonError, "B001.compileCommand_retired"):
                load_plan_bundle(feature_dir)
    def test_plan_writer_omits_lane_compile_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            write_plan_state(workspace)
            subprocess.run(["git", "init", "-b", "main"], cwd=workspace, check=True, capture_output=True)
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                "\n".join([
                    "## ADDED Requirements",
                    "### Requirement [REQ-001]: capability",
                    "#### Scenario [SCN-001]: happy path",
                ]),
                encoding="utf-8",
            )
            (feature_dir / "design.md").write_text(
                "\n".join([
                    "# Design",
                    "- x-auto-no-http-api: true",
                    "- x-auto-no-sql: true",
                    "| ID | Decision |",
                    "|----|----------|",
                    "| D-001 | implementation choice |",
                ]),
                encoding="utf-8",
            )
            lock_result = sync_design_contract_lock(workspace, "alpha")
            self.assertTrue(lock_result.ok, lock_result.errors)

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

            body = Path(tmp) / "T001.json"
            body.write_text(json.dumps(task("T001")), encoding="utf-8")
            group_file = Path(tmp) / "task-groups.json"
            group_file.write_text(
                json.dumps({
                    "featureId": "alpha",
                    "groups": [{
                        "id": "T001",
                        "title": "task T001",
                        "executionMode": "code",
                        "deps": [],
                        "uiRequired": False,
                        "workspaceRef": "default",
                        "specRefs": [
                            "specs/cap/spec.md#REQ-001",
                            "specs/cap/spec.md#SCN-001",
                        ],
                        "mergedScenarioRefs": [],
                        "apiIds": [],
                        "validationBoundary": "public behavior seam validated by the task command",
                    }],
                }),
                encoding="utf-8",
            )
            prepared = writer(
                "prepare-task-draft",
                "--group-file", str(group_file),
                "--code-workspace", str(workspace),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            detail = task("T001")
            detail["scope"].pop("pages", None)
            detail = {
                "goal": detail["goal"],
                "scope": detail["scope"],
                "implementationPoints": detail["implementationPoints"],
                "acceptanceCriteria": [{
                    "text": detail["acceptanceCriteria"][0]["text"],
                    "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                }],
                "nonGoals": detail["nonGoals"],
                "designRefs": detail["designRefs"],
                "dataIds": detail["dataIds"],
                "decisionIds": detail["decisionIds"],
                "validationCommands": [{
                    **{key: value for key, value in detail["validationCommands"][0].items() if key != "id"},
                    "covers": [1],
                }],
                "expectedFiles": detail["expectedFiles"],
                "blockers": detail["blockers"],
            }
            body.write_text(json.dumps(detail), encoding="utf-8")
            detailed = writer("set-draft-task-detail", "--task-id", "T001", "--body-file", str(body))
            self.assertEqual(detailed.returncode, 0, detailed.stdout + detailed.stderr)

            quality_added = writer(
                "add-quality-gate-command",
                "--lane",
                "backend",
                "--command",
                f"{sys.executable} -c \"print('backend static check')\"",
            )

            self.assertEqual(quality_added.returncode, 0, quality_added.stdout + quality_added.stderr)
            project_added = writer(
                "add-project-validation-command",
                "--command",
                f"{sys.executable} -c \"print('project integration')\"",
            )
            self.assertEqual(project_added.returncode, 0, project_added.stdout + project_added.stderr)
            finalized = writer("finalize-task-draft")
            self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch = json.loads(batch_plan_path(feature_dir, "B001").read_text(encoding="utf-8"))
            self.assertNotIn("compileProfiles", root)
            self.assertNotIn("compileCommand", batch)
            self.assertEqual(root["qualityGateProfiles"]["backend"]["commands"][0]["kind"], "static_check")
            self.assertEqual(batch["qualityGateCommands"][0]["id"], "BATCH-B001-QUALITY-001")
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

    def test_bundle_does_not_require_compile_command_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            item = task("T001")
            item["scope"].update({
                "workspaceRoots": {"default": "backend/service"},
                "paths": ["src/main/java/example"],
            })
            item["validationCommands"][0]["cwd"] = "backend/service"
            write_bundle(feature_dir, [[item]])

            _, errors = load_and_validate_plan(feature_dir / "plan.json")

            self.assertNotIn(
                "B001.compileCommand.cwd_outside_workspace_root:backend/service",
                errors,
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

    def test_plan_writer_projects_six_same_spec_tasks_to_six_single_task_batches(self) -> None:
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
            self.assertEqual([entry["id"] for entry in root["batches"]], ["B001", "B002", "B003", "B004", "B005", "B006"])
            self.assertEqual(len(first["tasks"]), 1)
            self.assertEqual(len(second["tasks"]), 1)
            self.assertIsNone(root["activeBatchId"])
            self.assertIsNone(root["nextBatchId"])

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

    def test_plan_writer_splits_frontend_tasks_with_different_routes(self) -> None:
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
            spec_driven = task("T001", ui_required=True)
            high_fidelity = task("T002", deps=["T001"], ui_required=True)
            high_fidelity["uiRefs"]["visualSourceRefs"] = ["VIS-001"]
            high_fidelity["uiRefs"]["frontendRoute"] = "absolute-html"
            for item in (spec_driven, high_fidelity):
                body = Path(tmp) / f"{item['id']}.json"
                body.write_text(json.dumps(item), encoding="utf-8")
                added = writer("add-task", "--body-file", str(body))
                self.assertEqual(added.returncode, 0, added.stdout + added.stderr)

            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["taskIds"] for entry in root["batches"]], [["T001"], ["T002"]])

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

    def test_bundle_allows_dependency_order_to_cross_execution_lanes(self) -> None:
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

        self.assertNotIn("backend_batch_after_frontend:B002", errors)

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
