from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import EvidenceStoreError, append_evidence  # noqa: E402
from hooks.evidence_integrity_gate import check_code_done  # noqa: E402
from hooks.plan_json import task_set_digest  # noqa: E402
from hooks import evidence_integrity_gate as evidence_integrity_gate_module  # noqa: E402
from hooks import plan_writer as plan_writer_module  # noqa: E402
from hooks import task_runner as task_runner_module  # noqa: E402



def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _configure_runtime_ignore(repo: Path) -> None:
    (repo / ".git" / "info" / "exclude").write_text(
        ".cmbdevclaw/large_tool_results/\n",
        encoding="utf-8",
    )


def _batch_path(feature_dir: Path) -> Path:
    return feature_dir / "plans" / "B001" / "plan.json"


def _read_batch(feature_dir: Path) -> dict:
    return json.loads(_batch_path(feature_dir).read_text(encoding="utf-8"))


def _write_batch(feature_dir: Path, batch: dict) -> None:
    _batch_path(feature_dir).write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _refresh_exploration_cache(feature_dir: Path, code: Path, *, task_id: str = "T001") -> None:
    from hooks.code_exploration import SCHEMA_VERSION, utc_now
    from hooks.repository_snapshot import capture_repository_snapshot

    batch = _read_batch(feature_dir)
    captured_at = utc_now()
    lane = str(batch.get("executionLane", "backend"))
    cache_path = feature_dir / "cache" / "code-exploration" / code.name / f"{lane}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = capture_repository_snapshot(code)
    cache_path.write_text(
        json.dumps(
            {
                "schemaVersion": SCHEMA_VERSION,
                "featureId": "alpha",
                "repository": {"id": code.name, "root": str(code.resolve())},
                "executionLane": lane,
                "capturedAt": captured_at,
                "capturedBatchId": str(batch.get("batchId", "B001")),
                "capturedTaskId": task_id,
                "gitSnapshot": snapshot,
                "findings": {
                    "moduleMap": [],
                    "conventions": [],
                    "integrationPoints": [],
                    "testEntrypoints": [],
                    "validationPatterns": [],
                },
                "exploredPaths": sorted(snapshot["files"]),
                "sharedPaths": [],
                "evidenceCoverage": {
                    "explainedTaskIds": [],
                    "completionEvidenceIds": [],
                    "lastExplainedBatchId": None,
                    "lastExplainedAt": captured_at,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _ensure_exploration_ready(feature_dir: Path, code: Path, *, task_id: str = "T001") -> None:
    from hooks.code_exploration import CodeExplorationError, inspect_exploration_cache
    from hooks.plan_json import load_plan_bundle

    try:
        result = inspect_exploration_cache(
            feature_dir,
            load_plan_bundle(feature_dir),
            task_id,
            code,
        )
    except CodeExplorationError:
        result = {"status": "stale"}
    if result.get("status") not in {"fresh", "fresh_with_trusted_changes"}:
        _refresh_exploration_cache(feature_dir, code, task_id=task_id)


def _bind_workspace_contract(
    feature_dir: Path,
    batch: dict,
    workspace_roots: dict[str, str],
    *,
    cwd: str,
    repo: str | None = None,
) -> None:
    for task in batch.get("tasks", []):
        task.setdefault("scope", {})["workspaceRoots"] = dict(workspace_roots)
        for command in task.get("validationCommands", []):
            command["cwd"] = cwd
            if repo is not None:
                command["repo"] = repo
    for command in batch.get("batchValidation", {}).get("commands", []):
        command["cwd"] = cwd
        if repo is not None:
            command["repo"] = repo
    root_path = feature_dir / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    lane = str(batch.get("executionLane", "backend"))
    for command in root.get("batchValidationProfiles", {}).get(lane, {}).get("commands", []):
        command["cwd"] = cwd
        if repo is not None:
            command["repo"] = repo
    root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _evidence(feature_dir: Path, evidence_id: str) -> dict:
    for line in (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("evidenceId") == evidence_id:
            return record
    raise AssertionError(f"missing evidence {evidence_id}")


def _workspace(
    root: Path,
    *,
    command_exit: int = 0,
    deps: list[str] | None = None,
    exploration_ready: bool = True,
) -> tuple[Path, Path, Path]:
    workspace = root / "artifacts"
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    feature_dir.mkdir(parents=True)
    (workspace / ".autobizdevops" / "state.json").write_text(
        json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {}}),
        encoding="utf-8",
    )
    code = root / "code"
    code.mkdir()
    _git(code, "init", "-b", "main")
    _git(code, "config", "user.email", "test@example.com")
    _git(code, "config", "user.name", "Test")
    _configure_runtime_ignore(code)
    (code / "existing.txt").write_text("existing\n", encoding="utf-8")
    _git(code, "add", "existing.txt")
    _git(code, "commit", "-m", "initial")

    task = {
        "id": "T001",
        "title": "deliver behavior",
        "goal": "deliver observable behavior",
        "status": "todo",
        "deps": deps or [],
        "uiRequired": False,
        "workspaceRef": "default",
        "scope": {"modules": [], "entrypoints": [], "pages": [], "dataObjects": []},
        "implementationPoints": ["update behavior", "cover boundary"],
        "acceptanceCriteria": [
            {
                "id": "AC-T001-01",
                "text": "behavior is observable",
                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
            }
        ],
        "validationBoundary": "public behavior seam validated by the task command",
        "nonGoals": ["do not change unrelated behavior"],
        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
        "designRefs": [],
        "apiIds": [],
        "dataIds": [],
        "decisionIds": ["D-001"],
        "completionPolicy": "all_required_validations_pass",
        "validationCommands": [
            {
                "id": "VAL-T001-01",
                "argv": [sys.executable, "-c", f"print('validation'); raise SystemExit({command_exit})"],
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "covers": ["AC-T001-01"],
            }
        ],
        "expectedFiles": [],
        "evidenceIds": [],
        "completionEvidenceIds": [],
        "latestPassEvidenceId": None,
        "blockers": [],
    }
    tasks = [task]
    if deps:
        tasks.insert(
            0,
            {
                **task,
                "id": "T000",
                "title": "dependency",
                "status": "todo",
                "deps": [],
                "acceptanceCriteria": [
                    {"id": "AC-T000-01", "text": "dependency", "scenarioRefs": ["#SCN-001"]}
                ],
                "validationCommands": [
                    {
                        "id": "VAL-T000-01",
                        "argv": [sys.executable, "-c", "raise SystemExit(0)"],
                        "cwd": ".",
                        "kind": "behavior_test",
                        "required": True,
                        "covers": ["AC-T000-01"],
                    }
                ],
            },
        )
    root_plan = {
        "featureId": "alpha",
        "status": "todo",
        "taskSetStatus": "finalized",
        "activeBatchId": "B001",
        "nextBatchId": None,
        "taskValidationPolicy": {
            "mode": "defer_to_test_stages",
            "orchestration": "inline",
            "codeGate": "batch_compile_only",
            "maxTestStageRepairAttempts": 3,
        },
        "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
        "batchValidationProfiles": {
            "backend": {
                "commands": [
                    {
                        "argv": [sys.executable, "-c", "print('batch compile')"],
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
                "title": "cap",
                "specRoots": ["specs/cap/spec.md"],
                "executionLane": "backend",
                "deps": [],
                "taskIds": [item["id"] for item in tasks],
                "status": "todo",
            }
        ],
        "projectValidationCommands": [
            {
                "id": "PROJECT-VAL-001",
                "argv": [sys.executable, "-c", "print('project compile')"],
                "cwd": ".",
                "kind": "integration_test",
                "required": True,
            }
        ],
        "projectCheckEvidenceIds": [],
        "latestProjectCheckEvidenceId": None,
    }
    (feature_dir / "plan.json").write_text(
        json.dumps(root_plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _batch_path(feature_dir).parent.mkdir(parents=True)
    _write_batch(
        feature_dir,
        {
            "featureId": "alpha",
            "batchId": "B001",
            "title": "cap",
            "executionLane": "backend",
            "status": "todo",
            "taskCount": len(tasks),
            "completedTaskCount": 0,
            "completionEvidenceIds": [],
            "batchValidation": {
                "profile": "backend",
                "status": "pending",
                "commands": [
                    {
                        "id": "BATCH-B001-VAL-001",
                        "argv": [sys.executable, "-c", "print('batch compile')"],
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
            "completedAt": None,
            "tasks": tasks,
        },
    )
    if exploration_ready:
        _refresh_exploration_cache(feature_dir, code)
    return workspace, feature_dir, code
def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "task_runner.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_plan_writer(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "plan_writer.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _start(workspace: Path, code: Path, *, refresh_exploration: bool = True) -> dict:
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    if refresh_exploration:
        _ensure_exploration_ready(feature_dir, code)
    result = _run(
        "start", "--workspace", str(workspace), "--feature", "alpha",
        "--task-id", "T001", "--code-workspace", str(code),
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)
def _configure_defer_to_test_stages(feature_dir: Path, *, always_fail: bool = False) -> None:
    batch = _read_batch(feature_dir)
    for task in batch["tasks"]:
        task.update({
            "implementationEvidenceIds": [],
            "latestImplementationEvidenceId": None,
            "validationEvidenceIds": [],
            "implementationRevision": 0,
        })
    compile_script = (
        "import sys; print('repair.txt:1: cannot find symbol', file=sys.stderr); "
        + ("raise SystemExit(1)" if always_fail else "raise SystemExit(0 if __import__('pathlib').Path('compile-fixed.txt').exists() else 1)")
    )
    batch["batchValidation"]["commands"][0]["argv"] = [sys.executable, "-c", compile_script]
    _write_batch(feature_dir, batch)

    root_path = feature_dir / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    root["taskValidationPolicy"] = {
        "mode": "defer_to_test_stages",
        "orchestration": "inline",
        "codeGate": "batch_compile_only",
        "maxTestStageRepairAttempts": 3,
    }
    root["projectValidationCommands"] = []
    root["batchValidationProfiles"]["backend"]["commands"][0]["argv"] = [
        sys.executable,
        "-c",
        compile_script,
    ]
    root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _add_second_compile_only_batch(feature_dir: Path) -> None:
    first = _read_batch(feature_dir)
    second_task = copy.deepcopy(first["tasks"][0])
    second_task.update(
        {
            "id": "T002",
            "title": "deliver next behavior",
            "status": "todo",
            "deps": ["T001"],
            "specRefs": ["specs/next/spec.md#REQ-002", "specs/next/spec.md#SCN-002"],
            "acceptanceCriteria": [
                {
                    "id": "AC-T002-01",
                    "text": "next behavior is observable",
                    "scenarioRefs": ["specs/next/spec.md#SCN-002"],
                }
            ],
            "evidenceIds": [],
            "implementationEvidenceIds": [],
            "latestImplementationEvidenceId": None,
            "validationEvidenceIds": [],
            "completionEvidenceIds": [],
            "latestPassEvidenceId": None,
            "implementationRevision": 0,
        }
    )
    second_task["validationCommands"][0].update(
        {
            "id": "VAL-T002-01",
            "covers": ["AC-T002-01"],
        }
    )
    second = copy.deepcopy(first)
    second.update(
        {
            "batchId": "B002",
            "title": "next",
            "status": "todo",
            "taskCount": 1,
            "completedTaskCount": 0,
            "completionEvidenceIds": [],
            "taskIds": ["T002"],
            "startedAt": None,
            "completedAt": None,
            "tasks": [second_task],
        }
    )
    second.pop("batchCompile", None)
    second["batchValidation"]["status"] = "pending"
    second["batchValidation"]["commands"][0]["id"] = "BATCH-B002-VAL-001"
    second["batchValidation"]["evidenceIds"] = []
    second["batchValidation"]["latestPassEvidenceIds"] = []
    second["batchValidation"]["activeRunId"] = None
    second_path = feature_dir / "plans" / "B002" / "plan.json"
    second_path.parent.mkdir(parents=True)
    second_path.write_text(
        json.dumps(second, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    root_path = feature_dir / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    root["activeBatchId"] = None
    root["nextBatchId"] = None
    root["batches"].append(
        {
            "id": "B002",
            "path": "plans/B002/plan.json",
            "title": "next",
            "specRoots": ["specs/next/spec.md"],
            "executionLane": "backend",
            "deps": ["B001"],
            "taskIds": ["T002"],
            "status": "todo",
        }
    )
    root["taskSetDigest"] = task_set_digest(root, {"B001": first, "B002": second})
    root_path.write_text(
        json.dumps(root, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class TaskRunnerTest(unittest.TestCase):
    def test_revalidate_batch_compile_reruns_a_passed_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, code = _workspace(root)
            _configure_defer_to_test_stages(feature_dir)

            compile_counter = root / "compile-count.txt"
            compile_script = (
                "from pathlib import Path; "
                f"counter = Path({str(compile_counter)!r}); "
                "counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1'); "
                "print('batch compile')"
            )
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["commands"][0]["argv"] = [
                sys.executable,
                "-c",
                compile_script,
            ]
            _write_batch(feature_dir, batch)
            root_plan_path = feature_dir / "plan.json"
            root_plan = json.loads(root_plan_path.read_text(encoding="utf-8"))
            root_plan["batchValidationProfiles"]["backend"]["commands"][0]["argv"] = [
                sys.executable,
                "-c",
                compile_script,
            ]
            root_plan_path.write_text(
                json.dumps(root_plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)

            first_compile = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertEqual(first_compile.returncode, 0, first_compile.stdout + first_compile.stderr)
            self.assertEqual(compile_counter.read_text(encoding="utf-8"), "1")

            revalidated = _run(
                "revalidate-batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertEqual(revalidated.returncode, 0, revalidated.stdout + revalidated.stderr)
            self.assertTrue(json.loads(revalidated.stdout)["wasRevalidation"])
            self.assertEqual(compile_counter.read_text(encoding="utf-8"), "2")
            revalidated_batch = _read_batch(feature_dir)
            self.assertEqual(revalidated_batch["batchCompile"]["status"], "passed")
            self.assertEqual(revalidated_batch["batchCompile"]["repairAttempts"], 0)

    def test_multiple_batches_require_parallel_workflow_instead_of_code_session_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            _add_second_compile_only_batch(feature_dir)
            session = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(session.returncode, 0, session.stdout + session.stderr)
            session_payload = json.loads(session.stdout)
            self.assertEqual(session_payload["action"], "start_parallel_batch_workflow")
            self.assertEqual(session_payload["batchIds"], ["B001", "B002"])

            direct_start = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertNotEqual(direct_start.returncode, 0)
            direct_payload = json.loads(direct_start.stdout)
            self.assertEqual(direct_payload["error"], "multi_batch_requires_parallel_workflow")
            self.assertEqual(direct_payload["requiredAction"], "start_parallel_batch_workflow")

            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["status"], "todo")
            self.assertIsNone(root["activeBatchId"])
            self.assertIsNone(root["nextBatchId"])
            self.assertFalse((feature_dir / "BATCH_HANDOFF.json").exists())

    def test_batch_compile_failure_requires_model_repair_and_new_implementation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            first_evidence = json.loads(finished.stdout)["implementationEvidenceId"]

            failed = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(failed.returncode, 0)
            failed_payload = json.loads(failed.stdout)
            self.assertEqual(failed_payload["requiredAction"], "start_batch_compile_repair")
            self.assertEqual(failed_payload["nextActor"], "model")
            self.assertEqual(failed_payload["repairAttempts"], 0)
            self.assertEqual(failed_payload["maxRepairAttempts"], 3)
            self.assertEqual(failed_payload["repairOwnerTaskIds"], ["T001"])

            direct_retry = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(direct_retry.returncode, 0)
            self.assertEqual(
                json.loads(direct_retry.stdout)["error"],
                "batch_compile_repair_required:B001",
            )
            ordinary_start = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertNotEqual(ordinary_start.returncode, 0)
            self.assertEqual(
                json.loads(ordinary_start.stdout)["requiredAction"],
                "start_batch_compile_repair",
            )

            # A model may act on the compile diagnostics before it starts the
            # formal repair run. The runner adopts that exact snapshot diff so
            # it remains evidence-bound instead of demanding a rollback.
            (code / "implemented.txt").write_text("implemented and repaired\n", encoding="utf-8")
            (code / "compile-fixed.txt").write_text("fixed by model\n", encoding="utf-8")
            repair = _run(
                "start-batch-compile-repair", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--task-id", "T001",
                "--code-workspace", str(code),
            )
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            repair_payload = json.loads(repair.stdout)
            self.assertEqual(repair_payload["repairContext"]["batchCompileRepairAttempt"], 1)
            self.assertTrue(repair_payload["repairContext"]["adoptedPreStartChanges"])
            self.assertEqual(
                repair_payload["batchCompileRepair"]["adoptedChangedFiles"],
                ["compile-fixed.txt", "implemented.txt"],
            )
            repaired = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", repair_payload["runId"],
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
            second_evidence = json.loads(repaired.stdout)["implementationEvidenceId"]
            self.assertNotEqual(second_evidence, first_evidence)
            pending_batch = _read_batch(feature_dir)
            self.assertEqual(pending_batch["batchCompile"]["status"], "pending")
            self.assertEqual(pending_batch["batchCompile"]["repairAttempts"], 1)

            passed = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            completed_batch = _read_batch(feature_dir)
            self.assertEqual(completed_batch["batchCompile"]["status"], "passed")
            self.assertEqual(completed_batch["tasks"][0]["status"], "done")
            self.assertEqual(
                completed_batch["tasks"][0]["implementationEvidenceIds"],
                [first_evidence, second_evidence],
            )
            self.assertEqual(check_code_done(feature_dir), [])

            completed_batch["batchCompile"]["implementationEvidenceByTask"]["T001"] = first_evidence
            _write_batch(feature_dir, completed_batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["taskSetDigest"] = task_set_digest(root, {"B001": completed_batch})
            root_path.write_text(
                json.dumps(root, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertIn(
                "B001.batch_compile_implementation_evidence_mismatch",
                check_code_done(feature_dir),
            )

    def test_batch_compile_repair_does_not_adopt_test_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            failed = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(failed.returncode, 0)

            test_file = code / "tests" / "generated_test.py"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("assert True\n", encoding="utf-8")
            repair = _run(
                "start-batch-compile-repair", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--task-id", "T001",
                "--code-workspace", str(code),
            )

            self.assertNotEqual(repair.returncode, 0)
            payload = json.loads(repair.stdout)
            self.assertEqual(payload["error"], "code_stage_test_changes_forbidden")
            self.assertEqual(payload["testFiles"], ["tests/generated_test.py"])
            self.assertEqual(_read_batch(feature_dir)["batchCompile"]["repairAttempts"], 0)

    def test_batch_compile_repair_adopts_changes_from_legacy_failed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            failed = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(failed.returncode, 0)
            snapshot_path = (
                feature_dir / ".task-runs" / ".batch-compile" / "B001.json"
            )
            snapshot_path.unlink()

            (code / "compile-fixed.txt").write_text("fixed before runner upgrade\n", encoding="utf-8")
            repair = _run(
                "start-batch-compile-repair", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--task-id", "T001",
                "--code-workspace", str(code),
            )

            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            payload = json.loads(repair.stdout)
            self.assertTrue(payload["repairContext"]["adoptedPreStartChanges"])
            self.assertEqual(
                payload["batchCompileRepair"]["adoptedChangedFiles"],
                ["compile-fixed.txt"],
            )

    def test_batch_compile_model_repair_is_blocked_after_three_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_defer_to_test_stages(feature_dir, always_fail=True)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)

            for attempt in range(1, 4):
                failed = _run(
                    "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                    "--batch-id", "B001", "--code-workspace", str(code),
                )
                self.assertNotEqual(failed.returncode, 0)
                repair = _run(
                    "start-batch-compile-repair", "--workspace", str(workspace),
                    "--feature", "alpha", "--batch-id", "B001", "--task-id", "T001",
                    "--code-workspace", str(code),
                )
                self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
                repair_payload = json.loads(repair.stdout)
                self.assertEqual(
                    repair_payload["repairContext"]["batchCompileRepairAttempt"],
                    attempt,
                )
                (code / "repair.txt").write_text(f"model repair {attempt}\n", encoding="utf-8")
                repaired = _run(
                    "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                    "--task-id", "T001", "--code-workspace", str(code),
                    "--run-id", repair_payload["runId"],
                )
                self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)

            final_failure = _run(
                "batch-compile", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(final_failure.returncode, 0)
            final_payload = json.loads(final_failure.stdout)
            self.assertEqual(
                final_payload["requiredAction"],
                "escalate_batch_compile_repair_exhausted",
            )
            self.assertEqual(final_payload["repairAttempts"], 3)
            self.assertFalse(final_payload["modelRepairRequired"])

            fourth_repair = _run(
                "start-batch-compile-repair", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--task-id", "T001",
                "--code-workspace", str(code),
            )
            self.assertNotEqual(fourth_repair.returncode, 0)
            self.assertEqual(
                json.loads(fourth_repair.stdout)["error"],
                "batch_compile_repair_attempts_exhausted:B001",
            )

    def test_first_task_start_requires_fresh_code_exploration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp), exploration_ready=False)

            result = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error"], "code_exploration_not_ready:code:missing")
            self.assertEqual(payload["requiredAction"], "record_code_exploration_and_retry_start")
            self.assertTrue(payload["explorationBlocked"])
            self.assertFalse(payload["implementationAllowed"])
            self.assertFalse((feature_dir / ".task-runs" / "T001").exists())

    def test_compile_only_code_stage_rejects_test_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "src" / "test").mkdir(parents=True)
            (code / "src" / "test" / "GeneratedTest.java").write_text(
                "class GeneratedTest {}\n",
                encoding="utf-8",
            )
            result = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error"], "code_stage_test_changes_forbidden")
            self.assertEqual(
                payload["requiredAction"],
                "restore_test_changes_and_continue_production_implementation",
            )
            self.assertEqual(payload["testFiles"], ["src/test/GeneratedTest.java"])
    def test_maven_runner_rejects_project_selector_from_leaf_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = (Path(tmp) / "repo").resolve()
            module = repo / "后台服务" / "零售客户经营" / "LF39.05_bccompliancemng"
            module.mkdir(parents=True)
            (module / "pom.xml").write_text("<project/>", encoding="utf-8")
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    "mvn", "test", "-Dtest=AgrCtrlSearchBasicTest",
                    "-pl", "backend/service/LF39.05_bccompliancemng",
                ],
                "cwd": "后台服务/零售客户经营/LF39.05_bccompliancemng",
            }

            with self.assertRaisesRegex(
                task_runner_module.TaskRunnerError,
                "maven_project_selector_requires_aggregator_cwd",
            ):
                task_runner_module._assert_validation_command_environment(
                    command,
                    {repo.name: repo},
                    retry_same_run=True,
                )
    def test_source_diagnostics_use_one_compile_failure_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            command = {"id": "BATCH-B001-VAL-001", "cwd": ".", "kind": "compile"}
            for relative in (
                "src/main/java/example/App.java",
                "src/test/java/example/AppTest.java",
            ):
                output = f"[ERROR] {repo / relative}:[1,1] cannot find symbol"
                with self.subTest(relative=relative):
                    self.assertEqual(
                        task_runner_module._definitive_compile_failure_category(
                            output, command, {repo.name: repo}
                        ),
                        "source_compile_failure",
                    )
            self.assertIsNone(
                task_runner_module._definitive_compile_failure_category(
                    "webpack exited with code 1", command, {repo.name: repo}
                )
            )

    def test_validation_timeout_preserves_source_compile_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            source = repo / "src" / "main" / "java" / "example" / "App.java"
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import sys,time; "
                f"print('[ERROR] {source}:[1,1] cannot find symbol', "
                "file=sys.stderr, flush=True); time.sleep(5)"
                    ),
                ],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 10,
            }
            started_at = time.monotonic()
            with patch.object(
                task_runner_module, "COMPILE_DIAGNOSTIC_DRAIN_SECONDS", 0.05
            ):
                exit_code, output = task_runner_module._run_validation(
                    command, {repo.name: repo}
                )
            self.assertEqual(exit_code, 1)
            self.assertLess(time.monotonic() - started_at, 2)
            self.assertIn("cannot find symbol", output)
            self.assertIn(
                "validation_process_stopped_after_compile_failure:source_compile_failure",
                output,
            )

    def test_validation_timeout_normalizes_test_source_compile_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            source = repo / "src" / "test" / "java" / "example" / "AppTest.java"
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import sys,time; "
                        "print('[ERROR] maven-compiler-plugin:testCompile', "
                        "file=sys.stderr, flush=True); "
                        f"print('[ERROR] {source}:[1,1] 未报告的异常错误', "
                        "file=sys.stderr, flush=True); time.sleep(5)"
                    ),
                ],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 10,
            }
            with patch.object(
                task_runner_module, "COMPILE_DIAGNOSTIC_DRAIN_SECONDS", 0.05
            ):
                exit_code, output = task_runner_module._run_validation(
                    command, {repo.name: repo}
                )
            self.assertEqual(exit_code, 1)
            self.assertIn(
                "validation_process_stopped_after_compile_failure:source_compile_failure",
                output,
            )

    def test_compile_monitor_does_not_treat_incidental_test_text_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            source = repo / "src" / "test" / "java" / "example" / "AppTest.java"
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import time; "
                        f"print('at example.AppTest.run({source}:12) expected no compilation error', "
                        "flush=True); time.sleep(0.2)"
                    ),
                ],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 3,
            }
            with patch.object(
                task_runner_module, "COMPILE_DIAGNOSTIC_DRAIN_SECONDS", 0.01
            ):
                exit_code, output = task_runner_module._run_validation(
                    command, {repo.name: repo}
                )
            self.assertEqual(exit_code, 0)
            self.assertNotIn("validation_process_stopped_after_compile_failure", output)

    def test_validation_process_uses_file_output_and_emits_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    sys.executable,
                    "-c",
                    "print('validation output captured', flush=True)",
                ],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 3,
            }
            real_popen = subprocess.Popen
            child_outputs = []

            def tracked_popen(*args, **kwargs):
                child_outputs.append(kwargs.get("stdout"))
                return real_popen(*args, **kwargs)

            progress = io.StringIO()
            with patch.object(
                task_runner_module.subprocess,
                "Popen",
                side_effect=tracked_popen,
            ):
                with patch.object(task_runner_module.sys, "stderr", progress):
                    exit_code, output = task_runner_module._run_validation(
                        command, {repo.name: repo}
                    )

            self.assertEqual(exit_code, 0)
            self.assertIn("validation output captured", output)
            self.assertEqual(len(child_outputs), 1)
            self.assertIsNot(child_outputs[0], subprocess.PIPE)
            self.assertTrue(hasattr(child_outputs[0], "fileno"))
            self.assertIn('"event":"validation_process_started"', progress.getvalue())
            self.assertIn('"event":"validation_process_finished"', progress.getvalue())

    def test_windows_batch_validation_uses_comspec_and_command_side_log_redirection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = (Path(tmp) / "repo with spaces").resolve()
            tool_dir = (Path(tmp) / "工具 with spaces").resolve()
            repo.mkdir()
            tool_dir.mkdir()
            maven = tool_dir / "mvn.cmd"
            command_shell = tool_dir / "cmd.exe"
            maven.write_text("@echo off\r\n", encoding="utf-8")
            command_shell.write_text("placeholder", encoding="utf-8")
            command = {
                "id": "VAL-T001-01",
                "argv": [str(maven), "compile"],
                "cwd": ".",
                "kind": "compile",
            }

            with patch.dict(
                task_runner_module.os.environ,
                {"COMSPEC": str(command_shell)},
                clear=False,
            ):
                launch_spec = task_runner_module._assert_validation_command_environment(
                    command,
                    {repo.name: repo},
                    retry_same_run=True,
                    platform_name="nt",
                )

            self.assertEqual(launch_spec.launch_mode, "windows_batch")
            self.assertEqual(launch_spec.resolved_executable, str(maven))
            self.assertEqual(launch_spec.command_shell, str(command_shell))
            log_path = Path(tmp) / "日志 with spaces.log"
            wrapper_content = task_runner_module._validation_windows_wrapper_content(
                launch_spec,
                log_path,
            )
            self.assertIn(
                f'call "{maven}" "compile"',
                wrapper_content,
            )
            self.assertIn(
                f'>"{log_path}" echo validation_windows_wrapper_started',
                wrapper_content,
            )
            self.assertIn(f'1>>"{log_path}" 2>&1', wrapper_content)
            wrapper_path = Path(tmp) / "包装 with spaces.cmd"
            self.assertEqual(
                task_runner_module._validation_windows_shell_command(
                    launch_spec,
                    wrapper_path,
                ),
                (
                    f'"{command_shell}" /D /S /V:OFF '
                    f'/C ""{wrapper_path}""'
                ),
            )

    def test_direct_validation_resolves_executable_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            command = {
                "id": "VAL-T001-01",
                "argv": [sys.executable, "--version"],
                "cwd": ".",
                "kind": "compile",
            }
            launch_spec = task_runner_module._assert_validation_command_environment(
                command,
                {repo.name: repo},
                retry_same_run=True,
            )
            self.assertEqual(launch_spec.launch_mode, "direct")
            self.assertIsNone(launch_spec.command_shell)
            self.assertTrue(Path(launch_spec.resolved_executable).is_absolute())
            self.assertEqual(
                task_runner_module._validation_command_argv(launch_spec),
                [launch_spec.resolved_executable, "--version"],
            )

    @unittest.skipUnless(os.name == "nt", "Windows batch launch integration")
    def test_windows_batch_compile_failure_is_captured_before_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = (Path(tmp) / "含空格仓库").resolve()
            source = repo / "src" / "main" / "java" / "example" / "App.java"
            source.parent.mkdir(parents=True)
            source.write_text("class App {}\n", encoding="utf-8")
            maven = repo / "mvn.cmd"
            maven.write_text(
                "@echo off\r\n"
                f"echo [ERROR] {source}:[1,1] must be caught or declared to be thrown 1>&2\r\n"
                "exit /b 1\r\n",
                encoding="utf-8",
            )
            command = {
                "id": "VAL-T001-01",
                "argv": [str(maven), "compile"],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 10,
            }
            started_at = time.monotonic()
            exit_code, output = task_runner_module._run_validation(
                command,
                {repo.name: repo},
            )
            self.assertEqual(exit_code, 1)
            self.assertLess(time.monotonic() - started_at, 5)
            self.assertIn("must be caught or declared to be thrown", output)
            self.assertIn(
                "validation_process_stopped_after_compile_failure:source_compile_failure",
                output,
            )

    def test_compile_ignores_fresh_maven_test_failure_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            report = repo / "target" / "surefire-reports" / "TEST-example.AppTest.xml"
            script = (
                "import time; from pathlib import Path; "
                f"p=Path({str(report)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text('<testsuite failures=\"1\"/>', encoding='utf-8'); "
                "time.sleep(1.2)"
            )
            command = {
                "id": "BATCH-B001-VAL-001",
                "argv": [sys.executable, "-c", script],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 3,
            }
            exit_code, output = task_runner_module._run_validation(
                command,
                {repo.name: repo},
            )
            self.assertEqual(exit_code, 0, output)

    def test_runtime_environment_failure_requires_strong_environment_marker(self) -> None:
        cases = {
            "The JAVA_HOME environment variable is not defined correctly":
                "java_toolchain_unavailable",
            "Could not transfer artifact a:b:jar:1 from central: Unknown host repo.example":
                "dependency_network_unavailable",
            "Failed to read artifact descriptor for a:b:jar:1: PKIX path building failed":
                "dependency_credentials_or_certificate_failure",
            "[ERROR] /repo/src/test/AppTest.java:[1,1] cannot find symbol": None,
            "Tests run: 1, Failures: 1": None,
        }
        for output, expected in cases.items():
            with self.subTest(output=output):
                self.assertEqual(
                    task_runner_module._runtime_environment_failure_category(output),
                    expected,
                )

    def test_nonzero_compile_failure_remains_code_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    sys.executable,
                    "-c",
                    "print('compile failed'); raise SystemExit(1)",
                ],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 3,
            }
            exit_code, output = task_runner_module._run_validation(
                command,
                {repo.name: repo},
            )
            self.assertEqual(exit_code, 1)
            self.assertIn("compile failed", output)

    def test_nonzero_toolchain_failure_is_returned_as_environment_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            command = {
                "id": "VAL-T001-01",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "print('The JAVA_HOME environment variable is not defined correctly'); "
                        "raise SystemExit(1)"
                    ),
                ],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 3,
            }
            with self.assertRaises(task_runner_module.TaskRunnerError) as raised:
                task_runner_module._run_validation(
                    command,
                    {repo.name: repo},
                )
            self.assertEqual(
                raised.exception.details["errorCategory"],
                "environment_failure",
            )
            self.assertEqual(
                raised.exception.details["failureCategory"],
                "java_toolchain_unavailable",
            )

    def test_validation_compile_stop_terminates_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            source = repo / "src" / "main" / "java" / "example" / "App.java"
            descendant_marker = repo / "descendant-survived.txt"
            descendant_script = (
                "import time; from pathlib import Path; time.sleep(0.8); "
                f"Path({str(descendant_marker)!r}).write_text('alive', encoding='utf-8')"
            )
            parent_script = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {descendant_script!r}]); "
                f"print('[ERROR] {source}:[1,1] cannot find symbol', flush=True); "
                "time.sleep(5)"
            )
            command = {
                "id": "VAL-T001-01",
                "argv": [sys.executable, "-c", parent_script],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 10,
            }
            with patch.object(
                task_runner_module, "COMPILE_DIAGNOSTIC_DRAIN_SECONDS", 0.05
            ):
                exit_code, output = task_runner_module._run_validation(
                    command, {repo.name: repo}
                )
            self.assertEqual(exit_code, 1)
            self.assertIn("processTreeTerminated=true", output)
            time.sleep(1)
            self.assertFalse(descendant_marker.exists())

    def test_validation_timeout_without_compile_diagnostic_remains_environment_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            command = {
                "id": "VAL-T001-01",
                "argv": [sys.executable, "-c", "import time; time.sleep(2)"],
                "cwd": ".",
                "kind": "compile",
                "timeoutSeconds": 1,
            }
            with self.assertRaises(task_runner_module.TaskRunnerError) as raised:
                task_runner_module._run_validation(command, {repo.name: repo})
            self.assertEqual(raised.exception.details["errorCategory"], "environment_failure")
            self.assertEqual(raised.exception.details["failureCategory"], "command_timeout")
            self.assertEqual(
                raised.exception.details["requiredAction"],
                "fix_compile_environment_and_retry_batch_compile",
            )

    def test_new_staged_test_is_transient_validation_file(self) -> None:
        state = {
            "scopeWorkspaces": [{"repository": "repo", "workspacePrefix": "module"}],
        }
        changes = [{
            "operation": "created",
            "path": "repo:module/src/test/java/example/AppTest.java",
            "repository": "repo",
        }]
        repositories = [{"id": "repo", "untrackedFiles": []}]
        formal, transient = task_runner_module._partition_transient_validation_changes(
            state,
            changes,
            repositories,
        )
        self.assertEqual(formal, [])
        self.assertEqual(transient, ["repo:module/src/test/java/example/AppTest.java"])
    def test_start_rejects_workspace_that_differs_from_plan_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            sibling = code / "backend" / "LF39.05_other"
            module.mkdir(parents=True)
            sibling.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            _bind_workspace_contract(
                feature_dir,
                batch,
                {"default": "backend/LF39.05_bccompliancemng"},
                cwd="backend/LF39.05_bccompliancemng",
            )
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(sibling),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("code_workspace_contract_mismatch", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_start_requires_backend_compile_or_build_batch_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            batch["batchValidation"].update({"mode": "commands", "commands": []})
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"] = {"mode": "commands", "commands": []}
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("backend_compile_command_missing", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_start_ignores_task_test_manifest_in_compile_only_code_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            module.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            _bind_workspace_contract(
                feature_dir,
                batch,
                {"default": "backend/LF39.05_bccompliancemng"},
                cwd="backend/LF39.05_bccompliancemng",
            )
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            batch["tasks"][0]["validationCommands"][0]["argv"] = [
                "mvn.cmd", "test", "-Dtest=ProtocolCtrlApplyTest", "-q"
            ]
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )

            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            self.assertEqual(len(list((feature_dir / ".task-runs").glob("T001/*.json"))), 1)
    def test_multi_repository_task_contract_is_rejected_before_scope_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, code = _workspace(root)
            second = root / "secondary"
            second.mkdir()
            _git(second, "init", "-b", "main")
            _git(second, "config", "user.email", "test@example.com")
            _git(second, "config", "user.name", "Test")
            _configure_runtime_ignore(second)
            (second / "base.txt").write_text("base\n", encoding="utf-8")
            _git(second, "add", "base.txt")
            _git(second, "commit", "-m", "initial")
            batch = _read_batch(feature_dir)
            _bind_workspace_contract(
                feature_dir,
                batch,
                {code.name: ".", second.name: "."},
                cwd=".",
                repo=code.name,
            )
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--code-workspace", str(second),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("scope.workspaceRoots_multiple_forbidden", started.stdout)

    def test_start_rejects_absolute_scope_path_before_run_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
            batch["tasks"][0]["scope"]["paths"] = ["/src/main/java/example"]
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("scope.paths_invalid:/src/main/java/example", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_scope_path_normalizer_rejects_absolute_path(self) -> None:
        for raw in (
            "",
            ".",
            "/src/main/java/example",
            "C:\\src\\main\\java\\example",
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                task_runner_module.TaskRunnerError,
                "invalid_scope_path:",
            ):
                task_runner_module._normalize_git_relative_path(
                    raw,
                    error="invalid_scope_path",
                )

    def test_scope_path_normalizer_uses_git_separators(self) -> None:
        self.assertEqual(
            task_runner_module._normalize_git_relative_path(
                "src\\main\\java\\example\\",
                error="invalid_scope_path",
            ),
            "src/main/java/example",
        )

    def test_abort_and_resume_reject_different_requested_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            sibling = code / "backend" / "LF39.05_other"
            module.mkdir(parents=True)
            sibling.mkdir(parents=True)
            original = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(original.returncode, 0, original.stdout + original.stderr)
            run_id = json.loads(original.stdout)["runId"]

            mismatched_abort = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(sibling),
                "--run-id", run_id,
            )
            self.assertNotEqual(mismatched_abort.returncode, 0)
            self.assertIn("task_run_requested_workspace_mismatch", mismatched_abort.stdout)

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", run_id,
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)
            mismatched_resume = _run(
                "resume", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(sibling),
                "--run-id", run_id,
            )
            self.assertNotEqual(mismatched_resume.returncode, 0)
            self.assertIn("task_run_requested_workspace_mismatch", mismatched_resume.stdout)
    def test_task_cannot_span_multiple_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, code = _workspace(root)
            second = root / "secondary"
            second.mkdir()
            _git(second, "init", "-b", "main")
            _git(second, "config", "user.email", "test@example.com")
            _git(second, "config", "user.name", "Test")
            _configure_runtime_ignore(second)
            (second / "base.txt").write_text("base\n", encoding="utf-8")
            _git(second, "add", "base.txt")
            _git(second, "commit", "-m", "initial")
            batch = _read_batch(feature_dir)
            _bind_workspace_contract(
                feature_dir,
                batch,
                {code.name: ".", second.name: "."},
                cwd=".",
                repo=code.name,
            )
            batch["tasks"][0]["scope"]["paths"] = [
                f"{code.name}:src/main/java/example",
                f"{second.name}:src/main/java/example",
            ]
            batch["tasks"][0]["validationCommands"][0]["repo"] = code.name
            _write_batch(feature_dir, batch)
            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--code-workspace", str(second),
            )
            self.assertNotEqual(started.returncode, 0)
            self.assertIn("scope.workspaceRoots_multiple_forbidden", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_start_rejects_two_scope_bases_as_multiple_task_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            first = code / "backend" / "first"
            second = code / "backend" / "second"
            first.mkdir(parents=True)
            second.mkdir(parents=True)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(first),
                "--code-workspace", str(second),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("task_requires_single_code_workspace", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_task_cannot_bind_modules_from_multiple_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, code = _workspace(root)
            second = root / "secondary"
            second.mkdir()
            _git(second, "init", "-b", "main")
            _git(second, "config", "user.email", "test@example.com")
            _git(second, "config", "user.name", "Test")
            _configure_runtime_ignore(second)
            (second / "base.txt").write_text("base\n", encoding="utf-8")
            _git(second, "add", "base.txt")
            _git(second, "commit", "-m", "initial")
            first_module = code / "services" / "compliance"
            second_module = second / "services" / "protocol"
            first_module.mkdir(parents=True)
            second_module.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            _bind_workspace_contract(
                feature_dir,
                batch,
                {
                    code.name: "services/compliance",
                    second.name: "services/protocol",
                },
                cwd="services/compliance",
                repo=code.name,
            )
            batch["tasks"][0]["scope"]["paths"] = [
                f"{code.name}:src/main/java/example",
                f"{second.name}:src/main/java/example",
            ]
            batch["tasks"][0]["validationCommands"][0]["repo"] = code.name
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(first_module),
                "--code-workspace", str(second_module),
            )
            self.assertNotEqual(started.returncode, 0)
            self.assertIn("scope.workspaceRoots_multiple_forbidden", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_start_rejects_unignored_runtime_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            (code / ".git" / "info" / "exclude").write_text("", encoding="utf-8")

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )

            payload = json.loads(started.stdout)
            self.assertNotEqual(started.returncode, 0)
            self.assertIn(
                "runtime_artifact_path_not_ignored:code:.cmbdevclaw/large_tool_results/",
                payload["error"],
            )
            self.assertEqual(payload["requiredAction"], "configure_git_ignore_and_retry")
            self.assertEqual(payload["resolvedGitRoots"], [str(code.resolve())])
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])
            self.assertEqual(_read_batch(feature_dir)["tasks"][0]["status"], "todo")

    def test_start_reports_requested_workspace_and_resolved_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            module = code / "bccompliancemng"
            module.mkdir()

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )

            payload = json.loads(started.stdout)
            self.assertEqual(payload["requestedCodeWorkspaces"], [str(module.resolve())])
            self.assertEqual(payload["repositories"][0]["path"], str(code.resolve()))
            self.assertEqual(payload["snapshotMode"], "git_visible_file_content_sha256")
            self.assertFalse(payload["stagingAffectsSnapshot"])

    def test_abort_classifies_new_untracked_test_without_forcing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            module.mkdir(parents=True)
            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            started_payload = json.loads(started.stdout)
            test = module / "src" / "test" / "java" / "example" / "AppTest.java"
            test.parent.mkdir(parents=True)
            test.write_text("class AppTest {}\n", encoding="utf-8")

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", started_payload["runId"],
            )

            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)
            payload = json.loads(aborted.stdout)
            test_path = (
                "backend/LF39.05_bccompliancemng/"
                "src/test/java/example/AppTest.java"
            )
            self.assertEqual(payload["transientValidationFilesAtAbort"], [test_path])
            run = json.loads(
                (
                    feature_dir / ".task-runs" / "T001" / f"{started_payload['runId']}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(run["status"], "aborted")
            self.assertEqual(run["transientValidationFilesAtAbort"], [test_path])

    def test_abort_rejects_unrecorded_changes_without_mutating_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            payload = json.loads(aborted.stdout)
            self.assertNotEqual(aborted.returncode, 0)
            self.assertEqual(
                payload["requiredAction"],
                "fix_workspace_and_retry_finish_implementation_or_force_abort",
            )
            run = json.loads(
                (
                    feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(run["status"], "started")
            self.assertEqual(_read_batch(feature_dir)["tasks"][0]["status"], "in_progress")

    def test_force_abort_requires_reason_and_records_changed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            missing_reason = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"], "--force-with-changes",
            )
            self.assertNotEqual(missing_reason.returncode, 0)
            self.assertIn("abort_with_changes_requires_reason", missing_reason.stdout)

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"], "--force-with-changes",
                "--abort-why", "abandon implementation",
            )

            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)
            run = json.loads(
                (
                    feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(run["status"], "aborted")
            self.assertEqual(run["abortWhy"], "abandon implementation")
            self.assertEqual(run["changedFilesAtAbort"], ["implemented.txt"])
            self.assertEqual(run["fileChangesAtAbort"][0]["operation"], "created")

    def test_abort_does_not_reuse_fresh_gate_after_critical_path_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            self.assertEqual(started["explorationGate"]["source"], "current_cache")
            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)

            (code / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            restarted = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )

            self.assertNotEqual(restarted.returncode, 0)
            payload = json.loads(restarted.stdout)
            self.assertEqual(payload["error"], "code_exploration_not_ready:code:stale")
            self.assertIn("pom.xml", payload["criticalHits"])
            run_paths = list((feature_dir / ".task-runs" / "T001").glob("*.json"))
            self.assertEqual(len(run_paths), 1)
    def test_code_session_rejects_missing_invalid_and_mismatched_handoff(self) -> None:
        for label, handoff_content in (("missing", None), ("invalid", "{"), ("mismatch", json.dumps({"nextBatchId": "B003"}))):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
                feature_dir.mkdir(parents=True)
                if handoff_content is not None:
                    (feature_dir / "BATCH_HANDOFF.json").write_text(handoff_content, encoding="utf-8")
                bundle = SimpleNamespace(
                    root={
                        "status": "awaiting_next_conversation",
                        "nextBatchId": "B002",
                    }
                )

                with patch.object(task_runner_module, "load_plan_bundle", return_value=bundle):
                    result = task_runner_module.code_session(workspace, "alpha")
                    self.assertEqual(result["action"], "start_parallel_batch_workflow")
    def test_task_start_rejects_multiple_requested_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, code = _workspace(root)
            second = root / "secondary"
            second.mkdir()
            _git(second, "init", "-b", "main")
            _git(second, "config", "user.email", "test@example.com")
            _git(second, "config", "user.name", "Test")
            _configure_runtime_ignore(second)
            (second / "base.txt").write_text("base\n", encoding="utf-8")
            _git(second, "add", "base.txt")
            _git(second, "commit", "-m", "initial")

            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["validationCommands"][0]["repo"] = code.name
            plan["projectValidationCommands"][0]["repo"] = code.name
            _write_batch(feature_dir, plan)
            _write_batch(feature_dir, batch)

            started_result = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--code-workspace", str(second),
            )
            self.assertNotEqual(started_result.returncode, 0)
            self.assertIn("task_requires_single_code_workspace", started_result.stdout)
            self.assertFalse((code / ".autobizdevops").exists())
            self.assertFalse((second / ".autobizdevops").exists())
            self.assertFalse((feature_dir / "evidence" / "EVIDENCE.jsonl").is_file())

    def test_start_rejects_another_active_task_run_in_same_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            second = json.loads(json.dumps(batch["tasks"][0]))
            second["id"] = "T002"
            second["status"] = "todo"
            second["acceptanceCriteria"][0]["id"] = "AC-T002-01"
            second["validationCommands"][0]["id"] = "VAL-T002-01"
            second["validationCommands"][0]["covers"] = ["AC-T002-01"]
            batch["tasks"].append(second)
            batch["taskCount"] = 2
            plan["batches"][0]["taskIds"].append("T002")
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            _write_batch(feature_dir, batch)
            _start(workspace, code)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(code),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("active_feature_task_run_exists:T001", started.stdout)
            self.assertEqual(
                json.loads(started.stdout)["requiredAction"],
                "inspect_and_retry_existing_run",
            )

    def test_resume_rejects_active_run_from_another_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            second = json.loads(json.dumps(batch["tasks"][0]))
            second["id"] = "T002"
            second["status"] = "todo"
            second["acceptanceCriteria"][0]["id"] = "AC-T002-01"
            second["validationCommands"][0]["id"] = "VAL-T002-01"
            second["validationCommands"][0]["covers"] = ["AC-T002-01"]
            batch["tasks"].append(second)
            batch["taskCount"] = 2
            plan["batches"][0]["taskIds"].append("T002")
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            _write_batch(feature_dir, batch)

            original = _start(workspace, code)
            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout)
            active = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(code),
            )
            self.assertEqual(active.returncode, 0, active.stdout)

            resumed = _run(
                "resume", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )

            self.assertNotEqual(resumed.returncode, 0)
            self.assertIn("active_feature_task_run_exists:T002", resumed.stdout)
    def test_resume_rejects_task_contract_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            original = _start(workspace, code)
            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["goal"] = "changed after the original run"
            _write_batch(feature_dir, batch)

            resumed = _run(
                "resume", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )

            self.assertNotEqual(resumed.returncode, 0)
            self.assertIn("task_set_digest_mismatch", resumed.stdout)

    def test_resume_rejects_repository_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _, code = _workspace(root)
            original = _start(workspace, code)
            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout)
            other = root / "other"
            other.mkdir()
            _git(other, "init", "-b", "main")
            _git(other, "config", "user.email", "test@example.com")
            _git(other, "config", "user.name", "Test")
            _configure_runtime_ignore(other)
            (other / "existing.txt").write_text("existing\n", encoding="utf-8")
            _git(other, "add", "existing.txt")
            _git(other, "commit", "-m", "initial")

            resumed = _run(
                "resume", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(other),
                "--run-id", original["runId"],
            )

            self.assertNotEqual(resumed.returncode, 0)
            self.assertIn("task_run_code_workspace_mismatch", resumed.stdout)

    def test_abort_can_clear_run_after_plan_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["goal"] = "corrected contract"
            _write_batch(feature_dir, plan)

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)
            self.assertFalse(json.loads(aborted.stdout)["planStatusReset"])
            updated = _read_batch(feature_dir)
            self.assertEqual(updated["tasks"][0]["status"], "in_progress")
            restarted = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertNotEqual(restarted.returncode, 0)
            self.assertIn("task_set_digest_mismatch", restarted.stdout + restarted.stderr)
    def test_start_rejects_unfinished_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp), deps=["T000"])

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("unfinished_task_dependencies:T000", started.stdout)


if __name__ == "__main__":
    unittest.main()
