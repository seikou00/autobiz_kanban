from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if str(ROOT := Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.evidence_integrity_gate import check_code_done  # noqa: E402
from hooks.plan_json import task_set_digest  # noqa: E402
from hooks import evidence_integrity_gate as evidence_integrity_gate_module  # noqa: E402
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


def _evidence(feature_dir: Path, evidence_id: str) -> dict:
    for line in (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("evidenceId") == evidence_id:
            return record
    raise AssertionError(f"missing evidence {evidence_id}")


def _workspace(root: Path, *, command_exit: int = 0, deps: list[str] | None = None) -> tuple[Path, Path, Path]:
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
        "scope": {"modules": [], "entrypoints": [], "pages": [], "dataObjects": []},
        "implementationPoints": ["update behavior", "cover boundary"],
        "acceptanceCriteria": [
            {
                "id": "AC-T001-01",
                "text": "behavior is observable",
                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
            }
        ],
        "nonGoals": [],
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
                        "argv": ["echo", "ok"],
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
                "kind": "compile",
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
    return workspace, feature_dir, code
def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "task_runner.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _start(workspace: Path, code: Path) -> dict:
    result = _run(
        "start", "--workspace", str(workspace), "--feature", "alpha",
        "--task-id", "T001", "--code-workspace", str(code),
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def _check_batch(workspace: Path, code: Path) -> dict:
    result = _run(
        "batch-check", "--workspace", str(workspace), "--feature", "alpha",
        "--batch-id", "B001", "--code-workspace", str(code),
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


class TaskRunnerTest(unittest.TestCase):
    def test_code_done_rejects_missing_batch_validation_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["latestPassEvidenceIds"] = []
            _write_batch(feature_dir, batch)

            self.assertIn(
                "B001.missing_batch_validation_pass:BATCH-B001-VAL-001",
                check_code_done(feature_dir),
            )

    def test_code_done_rejects_batch_validation_command_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["commands"][0]["argv"] = ["echo", "forged"]
            root["batchValidationProfiles"]["backend"]["commands"][0]["argv"] = ["echo", "forged"]
            root["taskSetDigest"] = task_set_digest(root, {"B001": batch})
            _write_batch(feature_dir, batch)
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            self.assertIn(
                "B001.batch_validation_command_mismatch:BATCH-B001-VAL-001:argv",
                check_code_done(feature_dir),
            )

    def test_code_done_accepts_empty_project_validation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["projectValidationCommands"] = []
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)

            session = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )

            self.assertEqual(session.returncode, 0, session.stdout + session.stderr)
            self.assertEqual(json.loads(session.stdout)["action"], "code_done_ready")
            self.assertEqual(check_code_done(feature_dir), [])

    def test_ambiguous_batch_repair_revalidates_entire_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "code"
            repo.mkdir()
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test")
            repositories = task_runner_module._resolve_repositories([repo])
            batch = {
                "tasks": [
                    {"id": "T001", "scope": {"paths": ["shared.txt"]}},
                    {"id": "T002", "scope": {"paths": ["shared.txt"]}},
                    {"id": "T003", "scope": {"paths": ["other.txt"]}},
                ]
            }

            affected = task_runner_module._affected_tasks_for_batch_changes(
                batch,
                [
                    {
                        "path": "shared.txt",
                        "operation": "modified",
                        "kind": "code",
                        "summary": "shared file changed",
                    }
                ],
                [repo],
                repositories,
            )

            self.assertEqual(affected, ["T001", "T002", "T003"])

    def test_batch_check_completes_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            checked = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )

            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            payload = json.loads(checked.stdout)
            self.assertEqual(payload["requiredAction"], "batch_validation_passed")
            self.assertEqual(payload["status"], "done")
            run_path = feature_dir / ".batch-runs" / "B001" / f"{payload['runId']}.json"
            self.assertTrue(run_path.is_file())
            evidence = _evidence(feature_dir, payload["evidenceIds"][0])
            self.assertEqual(evidence["action"], "batch_validation")
            self.assertEqual(evidence["taskId"], "__batch__")
            self.assertEqual(evidence["batchId"], "B001")
            self.assertEqual(_read_batch(feature_dir)["batchValidation"]["status"], "passed")
            session = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(json.loads(session.stdout)["action"], "run_project_check")

    def test_batch_check_retries_failed_commands_in_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            command = {
                "id": "BATCH-B001-VAL-001",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "raise SystemExit(0 if Path('existing.txt').read_text().strip() == 'fixed' else 3)"
                    ),
                ],
                "cwd": ".",
                "kind": "compile",
                "required": True,
            }
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["commands"] = [command]
            batch["tasks"][0]["scope"]["paths"] = ["existing.txt"]
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"]["commands"] = [
                {key: value for key, value in command.items() if key != "id"}
            ]
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            failed = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertTrue(failed.stdout, failed.stderr)
            failed_payload = json.loads(failed.stdout)
            self.assertEqual(failed_payload["requiredAction"], "fix_batch_and_retry_same_run")
            self.assertEqual(_evidence(feature_dir, failed_payload["evidenceIds"][0])["validation"]["result"], "fail")

            (code / "existing.txt").write_text("fixed\n", encoding="utf-8")
            repaired = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
                "--run-id", failed_payload["runId"],
            )

            self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
            repaired_payload = json.loads(repaired.stdout)
            self.assertEqual(repaired_payload["runId"], failed_payload["runId"])
            self.assertEqual(repaired_payload["requiredAction"], "revalidate_affected_tasks")
            self.assertEqual(repaired_payload["affectedTaskIds"], ["T001"])
            task_after_repair = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(task_after_repair["evidenceIds"], ["ev_0001"])
            self.assertEqual(task_after_repair["completionEvidenceIds"], [])
            self.assertEqual(
                task_after_repair["pendingRevalidation"]["supersedesEvidenceIds"],
                ["ev_0001"],
            )

            revalidation = _start(workspace, code)
            revalidated = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", revalidation["runId"],
                "--no-code-change-why", "batch repair is implemented and needs behavior revalidation",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(revalidated.returncode, 0, revalidated.stdout + revalidated.stderr)
            current_task = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(current_task["evidenceIds"], ["ev_0001", "ev_0004"])
            self.assertEqual(current_task["completionEvidenceIds"], ["ev_0004"])
            self.assertNotIn("pendingRevalidation", current_task)
            revalidation_evidence = _evidence(feature_dir, "ev_0004")
            self.assertEqual(revalidation_evidence["attemptType"], "batch_revalidation")
            self.assertEqual(revalidation_evidence["supersedesEvidenceIds"], ["ev_0001"])

            passed = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
                "--run-id", failed_payload["runId"],
            )
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            passed_payload = json.loads(passed.stdout)
            self.assertEqual(passed_payload["requiredAction"], "batch_validation_passed")
            state = json.loads(
                (feature_dir / ".batch-runs" / "B001" / f"{passed_payload['runId']}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["status"], "done")
            self.assertEqual(len(state["attempts"]), 3)
            self.assertEqual(len(state["evidenceIds"]), 3)
            self.assertEqual(_evidence(feature_dir, state["evidenceIds"][-1])["validation"]["result"], "pass")

    def test_batch_repair_rejects_out_of_scope_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            command = {
                "id": "BATCH-B001-VAL-001",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "raise SystemExit(0 if Path('existing.txt').read_text().strip() == 'fixed' else 3)"
                    ),
                ],
                "cwd": ".",
                "kind": "compile",
                "required": True,
            }
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["commands"] = [command]
            batch["tasks"][0]["scope"]["paths"] = ["src"]
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"]["commands"] = [
                {key: value for key, value in command.items() if key != "id"}
            ]
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            failed = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            failed_payload = json.loads(failed.stdout)
            (code / "existing.txt").write_text("fixed\n", encoding="utf-8")

            retried = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
                "--run-id", failed_payload["runId"],
            )

            self.assertNotEqual(retried.returncode, 0)
            self.assertIn("batch_fix_out_of_scope:existing.txt", retried.stdout)

    def test_batch_check_rejects_validation_that_mutates_git_visible_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            command = {
                "id": "BATCH-B001-VAL-001",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('generated.txt').write_text('generated')",
                ],
                "cwd": ".",
                "kind": "compile",
                "required": True,
            }
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["commands"] = [command]
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"]["commands"] = [
                {key: value for key, value in command.items() if key != "id"}
            ]
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            checked = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )

            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("batch_validation_modified_workspace:BATCH-B001-VAL-001", checked.stdout)
            records = [
                json.loads(line)
                for line in (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["action"] for record in records], ["validation"])

    def test_final_task_completion_waits_for_batch_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["requiredAction"], "run_batch_check")
            self.assertEqual(payload["activeBatchId"], "B001")
            self.assertFalse(payload["stopAfterBatch"])
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            self.assertEqual(batch["tasks"][0]["status"], "done")
            self.assertEqual(batch["status"], "in_progress")
            self.assertEqual(batch["batchValidation"]["status"], "pending")
            self.assertEqual(root["activeBatchId"], "B001")
            self.assertFalse((feature_dir / "BATCH_HANDOFF.json").exists())
            session = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(session.returncode, 0, session.stdout + session.stderr)
            self.assertEqual(json.loads(session.stdout)["action"], "run_batch_check")

    def test_complete_classifies_new_untracked_test_as_transient_validation_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            module.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["paths"] = [
                "src/main/java/example/application"
            ]
            _write_batch(feature_dir, batch)
            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

            source = module / "src" / "main" / "java" / "example" / "application" / "App.java"
            source.parent.mkdir(parents=True)
            source.write_text("class App {}\n", encoding="utf-8")
            test = module / "src" / "test" / "java" / "example" / "application" / "AppTest.java"
            test.parent.mkdir(parents=True)
            test.write_text("class AppTest {}\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", json.loads(started.stdout)["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            source_path = (
                "backend/LF39.05_bccompliancemng/"
                "src/main/java/example/application/App.java"
            )
            test_path = (
                "backend/LF39.05_bccompliancemng/"
                "src/test/java/example/application/AppTest.java"
            )
            self.assertEqual(payload["transientValidationFiles"], [test_path])
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(evidence["changedFiles"], [source_path])
            self.assertEqual(evidence["transientValidationFiles"], [test_path])
            run_path = (
                feature_dir
                / ".task-runs"
                / "T001"
                / f"{json.loads(started.stdout)['runId']}.json"
            )
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["transientValidationFiles"] = []
            run_path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
            self.assertIn(
                "T001.task_run_transient_validation_files_mismatch:ev_0001",
                evidence_integrity_gate_module._check_task_run_state(
                    feature_dir,
                    _read_batch(feature_dir)["tasks"][0],
                    [evidence],
                ),
            )

    def test_complete_keeps_staged_existing_and_tracked_tests_in_formal_scope(self) -> None:
        for test_state in ("staged", "preexisting_untracked", "tracked"):
            with self.subTest(test_state=test_state), tempfile.TemporaryDirectory() as tmp:
                workspace, feature_dir, code = _workspace(Path(tmp))
                module = code / "backend" / "LF39.05_bccompliancemng"
                module.mkdir(parents=True)
                batch = _read_batch(feature_dir)
                batch["tasks"][0]["scope"]["paths"] = [
                    "src/main/java/example/application"
                ]
                _write_batch(feature_dir, batch)
                test = (
                    module
                    / "src"
                    / "test"
                    / "java"
                    / "example"
                    / "application"
                    / "AppTest.java"
                )
                if test_state in {"preexisting_untracked", "tracked"}:
                    test.parent.mkdir(parents=True)
                    test.write_text("class AppTest {}\n", encoding="utf-8")
                if test_state == "tracked":
                    _git(code, "add", test.relative_to(code).as_posix())
                    _git(code, "commit", "-m", "add test baseline")

                started = _run(
                    "start", "--workspace", str(workspace), "--feature", "alpha",
                    "--task-id", "T001", "--code-workspace", str(module),
                )
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                test.parent.mkdir(parents=True, exist_ok=True)
                test.write_text(f"class AppTest {{ /* {test_state} */ }}\n", encoding="utf-8")
                if test_state == "staged":
                    _git(code, "add", test.relative_to(code).as_posix())

                completed = _run(
                    "complete", "--workspace", str(workspace), "--feature", "alpha",
                    "--task-id", "T001", "--code-workspace", str(module),
                    "--run-id", json.loads(started.stdout)["runId"],
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("out_of_scope_changes_detected", completed.stdout)
                self.assertIn("src/test/java/example/application/AppTest.java", completed.stdout)

    def test_complete_keeps_tests_outside_requested_workspace_in_formal_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            sibling = code / "backend" / "LF39.05_other"
            module.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            _write_batch(feature_dir, batch)
            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            source = module / "src" / "main" / "java" / "example" / "App.java"
            source.parent.mkdir(parents=True)
            source.write_text("class App {}\n", encoding="utf-8")
            test = sibling / "src" / "test" / "java" / "example" / "AppTest.java"
            test.parent.mkdir(parents=True)
            test.write_text("class AppTest {}\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", json.loads(started.stdout)["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("out_of_scope_changes_detected", completed.stdout)
            self.assertIn("backend/LF39.05_other/src/test", completed.stdout)

    def test_module_relative_scope_matches_git_root_relative_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            module.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            source = module / "src" / "main" / "java" / "example" / "ProtocolCtrl.java"
            source.parent.mkdir(parents=True)
            source.write_text("class ProtocolCtrl {}\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", json.loads(started.stdout)["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(
                _evidence(feature_dir, "ev_0001")["changedFiles"],
                ["backend/LF39.05_bccompliancemng/src/main/java/example/ProtocolCtrl.java"],
            )

    def test_complete_rejects_different_requested_workspace_under_same_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            sibling = code / "backend" / "LF39.05_other"
            module.mkdir(parents=True)
            sibling.mkdir(parents=True)
            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(sibling),
                "--run-id", json.loads(started.stdout)["runId"],
                "--no-code-change-why", "existing implementation",
                "--supporting-file", "existing.txt",
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("task_run_requested_workspace_mismatch", completed.stdout)

    def test_module_change_missing_from_scope_requires_plan_correction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            module.mkdir(parents=True)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["paths"] = [
                "src/main/java/example/adapter/protocolctrl"
            ]
            _write_batch(feature_dir, batch)
            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout)
            domain = module / "src" / "main" / "java" / "example" / "domain" / "Service.java"
            domain.parent.mkdir(parents=True)
            domain.write_text("interface Service {}\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", json.loads(started.stdout)["runId"],
            )

            payload = json.loads(completed.stdout)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                payload["requiredAction"],
                "correct_plan_scope_and_rebuild_task_baseline",
            )
            self.assertEqual(
                payload["declaredScopePaths"],
                ["src/main/java/example/adapter/protocolctrl"],
            )
            self.assertEqual(
                payload["resolvedScopePaths"],
                [
                    "backend/LF39.05_bccompliancemng/"
                    "src/main/java/example/adapter/protocolctrl"
                ],
            )

    def test_multi_repository_scope_requires_repository_prefix(self) -> None:
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
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            _write_batch(feature_dir, batch)

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--code-workspace", str(second),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("scope_path_repository_prefix_required", started.stdout)

    def test_start_rejects_absolute_scope_path_before_run_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
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

    def test_legacy_run_without_scope_base_uses_git_root_relative_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["paths"] = ["src/main/java/example"]
            _write_batch(feature_dir, batch)
            started = _start(workspace, code)
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            for field in (
                "scopePathBase",
                "scopeWorkspaces",
                "workspacePrefixes",
                "declaredScopePaths",
                "resolvedScopePaths",
            ):
                state.pop(field, None)
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            source = code / "src" / "main" / "java" / "example" / "Legacy.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Legacy {}\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_multi_repository_prefixed_scope_matches_changed_repository(self) -> None:
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
            batch["tasks"][0]["scope"]["paths"] = [
                f"{code.name}:src/main/java/example",
                f"{second.name}:src/main/java/example",
            ]
            batch["tasks"][0]["validationCommands"][0]["repo"] = code.name
            _write_batch(feature_dir, batch)
            source = second / "src" / "main" / "java" / "example" / "Service.java"

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--code-workspace", str(second),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            source.parent.mkdir(parents=True)
            source.write_text("interface Service {}\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--code-workspace", str(second),
                "--run-id", json.loads(started.stdout)["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(
                _evidence(feature_dir, "ev_0001")["changedFiles"],
                ["secondary:src/main/java/example/Service.java"],
            )

    def test_start_rejects_two_scope_bases_for_same_git_root(self) -> None:
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
            self.assertIn("ambiguous_code_workspace_base", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

    def test_multi_repository_scope_resolves_each_requested_module(self) -> None:
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
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            payload = json.loads(started.stdout)
            self.assertEqual(
                payload["resolvedScopePaths"],
                [
                    "code:services/compliance/src/main/java/example",
                    "secondary:services/protocol/src/main/java/example",
                ],
            )
            source = second_module / "src" / "main" / "java" / "example" / "Service.java"
            source.parent.mkdir(parents=True)
            source.write_text("interface Service {}\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(first_module),
                "--code-workspace", str(second_module),
                "--run-id", payload["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

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
                "fix_workspace_and_retry_complete_or_force_abort",
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

    def test_resume_reuses_original_snapshot_and_completes_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            original = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            force_aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"], "--force-with-changes",
                "--abort-why", "preserve implementation for resume",
            )
            self.assertEqual(force_aborted.returncode, 0, force_aborted.stdout)

            replacement = _start(workspace, code)
            clean_abort = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", replacement["runId"],
            )
            self.assertEqual(clean_abort.returncode, 0, clean_abort.stdout)

            resumed = _run(
                "resume", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            resumed_payload = json.loads(resumed.stdout)
            self.assertEqual(resumed_payload["status"], "started")
            self.assertEqual(resumed_payload["resumeCount"], 1)
            self.assertEqual(resumed_payload["snapshot"], original["snapshot"])

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(_evidence(feature_dir, "ev_0001")["changedFiles"], ["implemented.txt"])

    def test_verified_existing_rejects_changes_from_prior_aborted_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            original = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"], "--force-with-changes",
                "--abort-why", "preserve implementation for diagnosis",
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout)

            replacement = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", replacement["runId"],
                "--no-code-change-why", "existing implementation satisfies the contract",
                "--supporting-file", "implemented.txt",
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                f"verified_existing_conflicts_with_prior_run_changes:{original['runId']}:implemented.txt",
                completed.stdout,
            )

    def test_staging_existing_file_does_not_create_snapshot_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            started = _start(workspace, code)

            _git(code, "add", "implemented.txt")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("no_code_change_requires_reason_and_supporting_files", completed.stdout)

    def test_code_session_holds_task_run_lock_across_handoff_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "BATCH_HANDOFF.json").write_text(
                json.dumps({"nextBatchId": "B002"}),
                encoding="utf-8",
            )
            awaiting_bundle = SimpleNamespace(
                root={
                    "status": "awaiting_next_conversation",
                    "activeBatchId": None,
                    "nextBatchId": "B002",
                }
            )
            active_bundle = SimpleNamespace(
                root={
                    "status": "in_progress",
                    "activeBatchId": "B002",
                    "nextBatchId": None,
                    "executionLane": "backend",
                    "batches": [{"id": "B002", "executionLane": "backend", "taskIds": ["T002"]}],
                },
                batches={
                    "B002": {
                        "tasks": [{"id": "T002", "status": "todo"}],
                        "batchValidation": {"status": "pending"},
                    }
                },
            )
            lock_held = False
            bundles = iter([awaiting_bundle, active_bundle])

            @contextmanager
            def observed_lock(_feature_dir: Path):
                nonlocal lock_held
                self.assertFalse(lock_held)
                lock_held = True
                try:
                    yield
                finally:
                    lock_held = False

            def guarded_load(*_args, **_kwargs):
                self.assertTrue(lock_held)
                return next(bundles)

            def guarded_activate(*_args, **_kwargs):
                self.assertTrue(lock_held)
                return {}

            with (
                patch.object(task_runner_module, "_task_run_lock", side_effect=observed_lock),
                patch.object(
                    task_runner_module,
                    "load_plan_bundle",
                    side_effect=guarded_load,
                ),
                patch.object(
                    task_runner_module,
                    "_activate_batch_unlocked",
                    side_effect=guarded_activate,
                ),
            ):
                result = task_runner_module.code_session(workspace, "alpha")

            self.assertEqual(result["action"], "execute_active_batch")
            self.assertEqual(result["activeBatchId"], "B002")
            self.assertTrue(result["activatedFromHandoff"])

    def test_code_session_rejects_missing_invalid_and_mismatched_handoff(self) -> None:
        cases = [
            ("missing", None, "batch_handoff_missing:B002"),
            ("invalid", "{", "batch_handoff_invalid:B002"),
            ("mismatch", json.dumps({"nextBatchId": "B003"}), "batch_handoff_mismatch:B002"),
        ]
        for label, handoff_content, expected_error in cases:
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
                    with self.assertRaisesRegex(task_runner_module.TaskRunnerError, expected_error):
                        task_runner_module.code_session(workspace, "alpha")

    def test_code_session_routes_active_batch_then_final_project_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))

            active = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(active.returncode, 0, active.stdout + active.stderr)
            active_payload = json.loads(active.stdout)
            self.assertEqual(active_payload["action"], "execute_active_batch")
            self.assertEqual(active_payload["activeBatchId"], "B001")
            self.assertEqual(active_payload["executionLane"], "backend")
            self.assertFalse(active_payload["activatedFromHandoff"])

            started = _start(workspace, code)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)

            project_required = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(project_required.returncode, 0, project_required.stdout + project_required.stderr)
            self.assertEqual(json.loads(project_required.stdout)["action"], "run_project_check")

            project_check = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project_check.returncode, 0, project_check.stdout + project_check.stderr)
            ready = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
            self.assertEqual(json.loads(ready.stdout)["action"], "code_done_ready")

    def test_multiple_repositories_are_snapshotted_but_artifacts_stay_in_feature_dir(self) -> None:
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
            self.assertEqual(started_result.returncode, 0, started_result.stdout + started_result.stderr)
            started = json.loads(started_result.stdout)
            (second / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code), "--code-workspace", str(second),
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(evidence["changedFiles"], ["secondary:implemented.txt"])
            self.assertEqual(evidence["fileChanges"][0]["repository"], "secondary")
            self.assertFalse((code / ".autobizdevops").exists())
            self.assertFalse((second / ".autobizdevops").exists())
            self.assertTrue((feature_dir / "evidence" / "EVIDENCE.jsonl").is_file())

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

    def test_resume_rejects_run_with_completed_command_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            original = _start(workspace, code)
            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout)
            run_path = feature_dir / ".task-runs" / "T001" / f"{original['runId']}.json"
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["completedCommandEvidence"] = {
                "VAL-T001-01": {
                    "evidenceId": "ev_0001",
                    "result": "pass",
                    "required": True,
                }
            }
            run_path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")

            resumed = _run(
                "resume", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"],
            )

            self.assertNotEqual(resumed.returncode, 0)
            self.assertIn("task_run_cannot_resume_with_evidence", resumed.stdout)

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

    def test_project_check_records_non_task_evidence_and_binds_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            _check_batch(workspace, code)

            checked = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )

            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["projectCheckEvidenceIds"], ["ev_0003"])
            self.assertEqual(plan["latestProjectCheckEvidenceId"], "ev_0003")
            evidence = _evidence(feature_dir, "ev_0003")
            self.assertEqual(evidence["action"], "project_check")
            self.assertEqual(evidence["taskId"], "__project__")
            self.assertEqual(evidence["validation"]["commandId"], "PROJECT-VAL-001")
            self.assertEqual(evidence["supportingFiles"], [])

    def test_project_check_rejects_unfinished_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))

            checked = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )

            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("project_check_requires_all_tasks_done:T001", checked.stdout)
            self.assertFalse((feature_dir / "evidence" / "EVIDENCE.jsonl").exists())

    def test_project_check_rejects_git_visible_workspace_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["projectValidationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('project-generated.txt').write_text('generated')",
            ]
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

            checked = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )

            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("project_validation_modified_workspace:PROJECT-VAL-001", checked.stdout)
            records = [
                json.loads(line)
                for line in (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["action"] for record in records], ["validation", "batch_validation"])

    def test_complete_detects_renamed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "existing.txt").rename(code / "renamed.txt")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(evidence["changedFiles"], ["existing.txt", "renamed.txt"])
            self.assertEqual(evidence["fileChanges"][0]["operation"], "renamed")
            self.assertEqual(evidence["fileChanges"][0]["fromPath"], "existing.txt")
            self.assertEqual(evidence["fileChanges"][0]["path"], "renamed.txt")

    def test_complete_rejects_changes_outside_declared_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["scope"]["paths"] = ["src"]
            _write_batch(feature_dir, plan)
            started = _start(workspace, code)
            (code / "outside.txt").write_text("outside\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("out_of_scope_changes_detected:outside.txt", completed.stdout)
            self.assertEqual(
                json.loads(completed.stdout)["requiredAction"],
                "fix_workspace_and_retry_same_run",
            )

    def test_complete_rejects_task_contract_changed_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["validationCommands"][0]["argv"] = ["echo", "changed"]
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("task_set_digest_mismatch", completed.stdout)
            self.assertFalse((feature_dir / "evidence" / "EVIDENCE.jsonl").exists())

    def test_complete_runs_all_required_validation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["validationCommands"].append(
                {
                    "id": "VAL-T001-02",
                    "argv": [sys.executable, "-c", "print('second validation')"],
                    "cwd": ".",
                    "kind": "behavior_test",
                    "required": True,
                    "covers": ["AC-T001-01"],
                }
            )
            _write_batch(feature_dir, plan)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            plan = _read_batch(feature_dir)
            task = plan["tasks"][0]
            self.assertEqual(task["evidenceIds"], ["ev_0001", "ev_0002"])
            self.assertEqual(task["completionEvidenceIds"], ["ev_0001", "ev_0002"])
            self.assertEqual(task["latestPassEvidenceId"], "ev_0002")

    def test_recover_binds_evidence_written_run_without_rerunning_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "evidence_written",
                    "success": True,
                    "completionMode": "verified_existing",
                    "changedFiles": [],
                    "fileChanges": [],
                    "evidenceIds": ["ev_0001"],
                    "completionEvidenceIds": ["ev_0001"],
                }
            )
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "detailVersion": 2,
                    "runId": started["runId"],
                    "completionMode": "verified_existing",
                    "summary": "recovered validation",
                    "implementation": {"noCodeChange": True, "whatChanged": [], "why": "existing"},
                    "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                    "designRefs": [],
                    "changedFiles": [],
                    "fileChanges": [],
                    "supportingFiles": ["existing.txt"],
                    "checkedCriteria": ["AC-T001-01"],
                    "validation": {
                        "commandId": "VAL-T001-01",
                        "argv": [sys.executable, "-c", "print('validation')"],
                        "command": "validation",
                        "cwd": ".",
                        "kind": "behavior_test",
                        "required": True,
                        "exitCode": 0,
                        "result": "pass",
                    },
                },
                output_tail="validation\n",
            )

            recovered = _run(
                "recover", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            plan = _read_batch(feature_dir)
            self.assertEqual(plan["tasks"][0]["status"], "done")
            records = (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)

    def test_recover_accepts_legacy_run_state_without_batch_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "evidence_written",
                    "success": True,
                    "completionMode": "verified_existing",
                    "changedFiles": [],
                    "fileChanges": [],
                    "evidenceIds": ["ev_0001"],
                    "completionEvidenceIds": ["ev_0001"],
                }
            )
            state.pop("batchId", None)
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "detailVersion": 2,
                    "runId": started["runId"],
                    "completionMode": "verified_existing",
                    "summary": "legacy recovered validation",
                    "implementation": {"noCodeChange": True, "whatChanged": [], "why": "existing"},
                    "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                    "designRefs": [],
                    "changedFiles": [],
                    "fileChanges": [],
                    "supportingFiles": ["existing.txt"],
                    "checkedCriteria": ["AC-T001-01"],
                    "validation": {
                        "commandId": "VAL-T001-01",
                        "argv": [sys.executable, "-c", "print('validation')"],
                        "command": "validation",
                        "cwd": ".",
                        "kind": "behavior_test",
                        "required": True,
                        "exitCode": 0,
                        "result": "pass",
                    },
                },
                output_tail="validation\n",
            )

            recovered = _run(
                "recover", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            self.assertEqual(json.loads(run_path.read_text(encoding="utf-8"))["status"], "done")

    def test_recover_rejects_missing_evidence_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "evidence_written",
                    "success": True,
                    "completionMode": "verified_existing",
                    "evidenceIds": ["ev_9999"],
                    "completionEvidenceIds": ["ev_9999"],
                }
            )
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            recovered = _run(
                "recover", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(recovered.returncode, 0)
            self.assertIn("task_run_evidence_missing:ev_9999", recovered.stdout)

    def test_recover_resumes_after_one_of_multiple_commands_was_evidenced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["validationCommands"].append(
                {
                    "id": "VAL-T001-02",
                    "argv": [sys.executable, "-c", "print('second validation')"],
                    "cwd": ".",
                    "kind": "behavior_test",
                    "required": True,
                    "covers": ["AC-T001-01"],
                }
            )
            _write_batch(feature_dir, plan)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "detailVersion": 2,
                    "runId": started["runId"],
                    "completionMode": "implemented",
                    "summary": "first validation",
                    "implementation": {"noCodeChange": False, "whatChanged": ["implemented.txt"], "why": "task"},
                    "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["implemented.txt"],
                    "fileChanges": [
                        {
                            "path": "implemented.txt",
                            "operation": "created",
                            "kind": "docs",
                            "summary": "Task execution created implemented.txt",
                            "reason": "Detected from the task run Git snapshot",
                        }
                    ],
                    "supportingFiles": [],
                    "checkedCriteria": ["AC-T001-01"],
                    "validation": {
                        "commandId": "VAL-T001-01",
                        "argv": plan["tasks"][0]["validationCommands"][0]["argv"],
                        "command": "first",
                        "cwd": ".",
                        "kind": "behavior_test",
                        "required": True,
                        "exitCode": 0,
                        "result": "pass",
                    },
                },
                output_tail="first validation\n",
            )
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            state.update(
                {
                    "status": "validation_running",
                    "completionMode": "implemented",
                    "changedFiles": ["implemented.txt"],
                    "fileChanges": [
                        {
                            "path": "implemented.txt",
                            "operation": "created",
                            "kind": "docs",
                            "summary": "Task execution created implemented.txt",
                            "reason": "Detected from the task run Git snapshot",
                        }
                    ],
                    "completedCommandEvidence": {
                        "VAL-T001-01": {"evidenceId": "ev_0001", "result": "pass", "required": True}
                    },
                    "evidenceIds": ["ev_0001"],
                    "completionEvidenceIds": ["ev_0001"],
                }
            )
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            recovered = _run(
                "recover", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            records = (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 2)
            second = json.loads(records[1])
            self.assertEqual(second["validation"]["commandId"], "VAL-T001-02")

    def test_recover_adopts_streamed_evidence_missing_from_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            file_changes = [
                {
                    "path": "implemented.txt",
                    "operation": "created",
                    "kind": "docs",
                    "summary": "Task execution created implemented.txt",
                    "reason": "Detected from the task run Git snapshot",
                }
            ]
            state.update(
                {
                    "status": "validation_running",
                    "completionMode": "implemented",
                    "changedFiles": ["implemented.txt"],
                    "fileChanges": file_changes,
                }
            )
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            command = _read_batch(feature_dir)["tasks"][0]["validationCommands"][0]
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "detailVersion": 2,
                    "runId": started["runId"],
                    "completionMode": "implemented",
                    "summary": "first validation",
                    "implementation": {"noCodeChange": False, "whatChanged": ["implemented.txt"], "why": "task"},
                    "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["implemented.txt"],
                    "fileChanges": file_changes,
                    "supportingFiles": [],
                    "checkedCriteria": ["AC-T001-01"],
                    "validation": {
                        "commandId": "VAL-T001-01",
                        "argv": command["argv"],
                        "command": "first",
                        "cwd": ".",
                        "kind": "behavior_test",
                        "required": True,
                        "exitCode": 0,
                        "result": "pass",
                    },
                },
                output_tail="first validation\n",
            )

            recovered = _run(
                "recover", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
            records = (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)
            recovered_state = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered_state["completedCommandEvidence"]["VAL-T001-01"]["evidenceId"], "ev_0001")

    def test_complete_records_changed_files_and_atomically_completes_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["completionMode"], "implemented")
            plan = _read_batch(feature_dir)
            task = plan["tasks"][0]
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["evidenceIds"], ["ev_0001"])
            self.assertEqual(task["completionEvidenceIds"], ["ev_0001"])
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(evidence["changedFiles"], ["implemented.txt"])
            self.assertEqual(evidence["completionMode"], "implemented")
            self.assertIn("validation", (feature_dir / "evidence" / "ev_0001.log").read_text(encoding="utf-8"))

            _check_batch(workspace, code)

            project_check = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project_check.returncode, 0, project_check.stdout + project_check.stderr)
            self.assertEqual(check_code_done(feature_dir), [])

    def test_code_done_rejects_task_run_snapshot_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)
            project_check = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project_check.returncode, 0, project_check.stdout + project_check.stderr)

            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            state["changedFiles"] = ["forged.txt"]
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            self.assertIn(
                "T001.task_run_changed_files_mismatch:ev_0001",
                check_code_done(feature_dir),
            )

    def test_code_done_rejects_task_contract_changed_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)
            project_check = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project_check.returncode, 0, project_check.stdout + project_check.stderr)
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["goal"] = "changed after completion"
            _write_batch(feature_dir, plan)

            self.assertIn("plan_json:task_set_digest_mismatch", check_code_done(feature_dir))

    def test_code_done_rejects_project_check_older_than_task_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            project_evidence = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "__project__",
                    "action": "project_check",
                    "detailVersion": 2,
                    "runId": "run-project-before-task",
                    "completionMode": "verified_existing",
                    "summary": "project check before task completion",
                    "implementation": {
                        "noCodeChange": True,
                        "whatChanged": [],
                        "why": "project check",
                    },
                    "specRefs": [],
                    "designRefs": [],
                    "changedFiles": [],
                    "fileChanges": [],
                    "supportingFiles": ["."],
                    "checkedCriteria": ["PROJECT-VAL-001"],
                    "validation": {
                        "commandId": "PROJECT-VAL-001",
                        "argv": [sys.executable, "-c", "print('project compile')"],
                        "command": "project compile",
                        "cwd": ".",
                        "kind": "compile",
                        "required": True,
                        "exitCode": 0,
                        "result": "pass",
                    },
                },
                output_tail="project compile\n",
            )
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            _check_batch(workspace, code)
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["projectCheckEvidenceIds"] = [project_evidence["evidenceId"]]
            plan["latestProjectCheckEvidenceId"] = project_evidence["evidenceId"]
            plan["status"] = "done"
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

            self.assertIn(
                "project_check_older_than_task_completion:ev_0001",
                check_code_done(feature_dir),
            )

    def test_complete_supports_verified_existing_with_no_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing implementation satisfies the contract",
                "--supporting-file", "existing.txt",
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(evidence["completionMode"], "verified_existing")
            self.assertEqual(evidence["changedFiles"], [])
            self.assertTrue(evidence["implementation"]["noCodeChange"])
            self.assertEqual(evidence["supportingFiles"], ["existing.txt"])

    def test_no_change_completion_requires_reason_and_supporting_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _, code = _workspace(Path(tmp))
            started = _start(workspace, code)

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("no_code_change_requires_reason_and_supporting_files", completed.stdout)

    def test_validation_failure_is_evidenced_but_task_is_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp), command_exit=3)
            started = _start(workspace, code)

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing implementation was checked",
                "--supporting-file", "existing.txt",
            )

            self.assertNotEqual(completed.returncode, 0)
            plan = _read_batch(feature_dir)
            self.assertEqual(plan["tasks"][0]["status"], "failed")
            self.assertEqual(plan["tasks"][0]["evidenceIds"], ["ev_0001"])
            self.assertEqual(plan["tasks"][0]["completionEvidenceIds"], [])
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(evidence["validation"]["result"], "fail")

    def test_failed_task_can_retry_without_losing_evidence_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; raise SystemExit(0 if Path('retry-ready').exists() else 3)",
            ]
            _write_batch(feature_dir, batch)
            first_run = _start(workspace, code)
            failed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", first_run["runId"],
                "--no-code-change-why", "existing implementation was checked",
                "--supporting-file", "existing.txt",
            )
            self.assertNotEqual(failed.returncode, 0)

            (code / "retry-ready").write_text("ready\n", encoding="utf-8")
            second_run = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", second_run["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            plan = _read_batch(feature_dir)
            task = plan["tasks"][0]
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["evidenceIds"], ["ev_0001", "ev_0002"])
            self.assertEqual(task["completionEvidenceIds"], ["ev_0002"])
            self.assertEqual(task["latestPassEvidenceId"], "ev_0002")
            records = [
                json.loads(line)
                for line in (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["validation"]["result"] for record in records], ["fail", "pass"])

    def test_complete_rejects_validation_that_mutates_git_visible_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('generated.txt').write_text('generated')",
            ]
            _write_batch(feature_dir, plan)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("validation_modified_workspace:VAL-T001-01", completed.stdout)
            self.assertFalse((feature_dir / "evidence" / "EVIDENCE.jsonl").exists())

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
