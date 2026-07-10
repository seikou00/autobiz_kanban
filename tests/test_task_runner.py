from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if str(ROOT := Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.evidence_integrity_gate import check_code_done  # noqa: E402



def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


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
    (feature_dir / "plan.json").write_text(
        json.dumps(
            {
                "featureId": "alpha",
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
                "tasks": tasks,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
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


class TaskRunnerTest(unittest.TestCase):
    def test_multiple_repositories_are_snapshotted_but_artifacts_stay_in_feature_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, code = _workspace(root)
            second = root / "secondary"
            second.mkdir()
            _git(second, "init", "-b", "main")
            _git(second, "config", "user.email", "test@example.com")
            _git(second, "config", "user.name", "Test")
            (second / "base.txt").write_text("base\n", encoding="utf-8")
            _git(second, "add", "base.txt")
            _git(second, "commit", "-m", "initial")

            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["validationCommands"][0]["repo"] = code.name
            plan["projectValidationCommands"][0]["repo"] = code.name
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

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
            evidence = json.loads((feature_dir / "evidence" / "ev_0001.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["changedFiles"], ["secondary:implemented.txt"])
            self.assertEqual(evidence["fileChanges"][0]["repository"], "secondary")
            self.assertFalse((code / ".autobizdevops").exists())
            self.assertFalse((second / ".autobizdevops").exists())
            self.assertTrue((feature_dir / "evidence" / "EVIDENCE.jsonl").is_file())

    def test_start_rejects_another_active_task_run_in_same_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _start(workspace, code)
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            second = json.loads(json.dumps(plan["tasks"][0]))
            second["id"] = "T002"
            second["status"] = "todo"
            second["acceptanceCriteria"][0]["id"] = "AC-T002-01"
            second["validationCommands"][0]["id"] = "VAL-T002-01"
            second["validationCommands"][0]["covers"] = ["AC-T002-01"]
            plan["tasks"].append(second)
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(code),
            )

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("active_feature_task_run_exists:T001", started.stdout)

    def test_abort_can_clear_run_after_plan_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["goal"] = "corrected contract"
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)
            updated = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["tasks"][0]["status"], "todo")
            restarted = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)

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

            checked = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )

            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["projectCheckEvidenceIds"], ["ev_0002"])
            self.assertEqual(plan["latestProjectCheckEvidenceId"], "ev_0002")
            evidence = json.loads((feature_dir / "evidence" / "ev_0002.json").read_text(encoding="utf-8"))
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
            records = (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)

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
            evidence = json.loads((feature_dir / "evidence" / "ev_0001.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["changedFiles"], ["existing.txt", "renamed.txt"])
            self.assertEqual(evidence["fileChanges"][0]["operation"], "renamed")
            self.assertEqual(evidence["fileChanges"][0]["fromPath"], "existing.txt")
            self.assertEqual(evidence["fileChanges"][0]["path"], "renamed.txt")

    def test_complete_rejects_changes_outside_declared_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["scope"]["paths"] = ["src"]
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            started = _start(workspace, code)
            (code / "outside.txt").write_text("outside\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("out_of_scope_changes_detected:outside.txt", completed.stdout)

    def test_complete_rejects_task_contract_changed_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["validationCommands"][0]["argv"] = ["echo", "changed"]
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("task_contract_changed_after_start:T001", completed.stdout)
            self.assertFalse((feature_dir / "evidence" / "EVIDENCE.jsonl").exists())

    def test_complete_runs_all_required_validation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
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
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
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
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["tasks"][0]["status"], "done")
            records = (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)

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
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
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
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
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
            command = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))["tasks"][0]["validationCommands"][0]
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
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            task = plan["tasks"][0]
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["evidenceIds"], ["ev_0001"])
            self.assertEqual(task["completionEvidenceIds"], ["ev_0001"])
            evidence = json.loads((feature_dir / "evidence" / "ev_0001.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["changedFiles"], ["implemented.txt"])
            self.assertEqual(evidence["completionMode"], "implemented")
            self.assertIn("validation", (feature_dir / "evidence" / "ev_0001.log").read_text(encoding="utf-8"))

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
            project_check = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project_check.returncode, 0, project_check.stdout + project_check.stderr)
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["goal"] = "changed after completion"
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

            self.assertIn(
                f"T001.task_run_contract_mismatch:{started['runId']}",
                check_code_done(feature_dir),
            )

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
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["projectCheckEvidenceIds"] = [project_evidence["evidenceId"]]
            plan["latestProjectCheckEvidenceId"] = project_evidence["evidenceId"]
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
            evidence = json.loads((feature_dir / "evidence" / "ev_0001.json").read_text(encoding="utf-8"))
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
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["tasks"][0]["status"], "failed")
            self.assertEqual(plan["tasks"][0]["evidenceIds"], ["ev_0001"])
            self.assertEqual(plan["tasks"][0]["completionEvidenceIds"], [])
            evidence = json.loads((feature_dir / "evidence" / "ev_0001.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["validation"]["result"], "fail")

    def test_failed_task_can_retry_without_losing_evidence_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp), command_exit=3)
            first_run = _start(workspace, code)
            failed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", first_run["runId"],
                "--no-code-change-why", "existing implementation was checked",
                "--supporting-file", "existing.txt",
            )
            self.assertNotEqual(failed.returncode, 0)

            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "print('validation pass')",
            ]
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            second_run = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", second_run["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
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
            plan_path = feature_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('generated.txt').write_text('generated')",
            ]
            plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
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
