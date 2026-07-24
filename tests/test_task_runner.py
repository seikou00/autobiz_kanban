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

from hooks.evidence_store import EvidenceStoreError, append_evidence  # noqa: E402
from hooks.evidence_integrity_gate import check_code_done  # noqa: E402
from hooks.plan_json import task_contract_sha256, task_set_digest  # noqa: E402
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


def _configure_frontend_task_covered(feature_dir: Path, code: Path) -> None:
    (code / "package.json").write_text(
        json.dumps({"scripts": {"build": "node --check app.js"}}) + "\n",
        encoding="utf-8",
    )
    (code / "app.js").write_text("const compiled = true;\n", encoding="utf-8")
    batch = _read_batch(feature_dir)
    batch["executionLane"] = "frontend"
    task = batch["tasks"][0]
    task["uiRequired"] = True
    task["scope"].update({
        "workspaceRoots": {"default": "."},
        "paths": ["app.js"],
        "pages": ["PAGE-001"],
    })
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
    batch["batchValidation"].update({
        "profile": "frontend",
        "mode": "task_covered",
        "coverageCommandIds": ["VAL-T001-01"],
        "commands": [],
        "activeRunId": None,
    })
    _write_batch(feature_dir, batch)
    root_path = feature_dir / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    root["batches"][0]["executionLane"] = "frontend"
    root["batchValidationProfiles"] = {"frontend": {"mode": "task_covered", "commands": []}}
    root["projectValidationCommands"] = []
    root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _configure_deferred_task_validation(feature_dir: Path) -> None:
    batch = _read_batch(feature_dir)
    for task in batch["tasks"]:
        task.update({
            "implementationEvidenceIds": [],
            "latestImplementationEvidenceId": None,
            "validationEvidenceIds": [],
            "implementationRevision": 0,
        })
    task_order = [str(task["id"]) for task in batch["tasks"]]
    batch["taskValidation"] = {
        "mode": "deferred_sequential",
        "status": "pending",
        "taskOrder": task_order,
        "completedTaskIds": [],
        "activeRunId": None,
        "lastRunId": None,
        "currentTaskId": None,
        "batchSnapshotSha256": None,
        "evidenceIds": [],
        "latestPassEvidenceByTask": {},
        "taskContractSha256ByTask": {
            str(task["id"]): task_contract_sha256(task) for task in batch["tasks"]
        },
    }
    _write_batch(feature_dir, batch)
    root_path = feature_dir / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    root["taskValidationPolicy"] = {
        "mode": "deferred_batch",
        "orchestration": "single_batch_subagent",
        "failStrategy": "fail_fast",
        "maxConcurrency": 1,
        "agentScope": "task_and_batch_validation_commands",
    }
    root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TaskRunnerTest(unittest.TestCase):
    def test_deferred_batch_handoff_requires_task_validation_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, _ = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["status"] = "done"
            _write_batch(feature_dir, batch)

            result = plan_writer_module.record_batch_validation_attempt(
                workspace,
                "alpha",
                "B001",
                ["ev_0001"],
                success=True,
                run_id="batch_run_0001",
            )

            self.assertFalse(result.ok)
            self.assertEqual(
                result.errors,
                [{"reason": "batch_validation_requires_task_validation_passed", "detail": "B001"}],
            )
            self.assertFalse((feature_dir / "BATCH_HANDOFF.json").exists())

    def test_deferred_task_validation_separates_implementation_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

            legacy_complete = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertNotEqual(legacy_complete.returncode, 0)
            self.assertIn("complete_disabled_for_deferred_validation:T001", legacy_complete.stdout)

            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            self.assertEqual(json.loads(finished.stdout)["requiredAction"], "run_batch_task_validation")
            task = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(task["status"], "implemented")
            self.assertEqual(task["implementationEvidenceIds"], ["ev_0001"])
            self.assertEqual(task["completionEvidenceIds"], [])
            self.assertEqual(_evidence(feature_dir, "ev_0001")["action"], "implementation")
            implementation_only_errors = check_code_done(feature_dir)
            self.assertTrue(implementation_only_errors)
            self.assertIn("plan_json:plan_json_status_not_done", implementation_only_errors)
            session = _run(
                "code-session", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(json.loads(session.stdout)["action"], "run_batch_task_validation")

            validation_run = _run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001",
                "--code-workspace", str(code),
            )
            self.assertEqual(validation_run.returncode, 0, validation_run.stdout + validation_run.stderr)
            validation_payload = json.loads(validation_run.stdout)
            self.assertEqual(validation_payload["currentTaskId"], "T001")
            self.assertEqual(validation_payload["requiredAction"], "spawn_batch_validation_subagent")
            self.assertEqual(validation_payload["requestedCodeWorkspaces"], [str(code.resolve())])
            self.assertEqual(
                validation_payload["validationContext"],
                {
                    "featureId": "alpha",
                    "batchId": "B001",
                    "runId": validation_payload["runId"],
                    "taskOrder": ["T001"],
                    "currentTaskId": "T001",
                    "requestedCodeWorkspaces": [str(code.resolve())],
                    "batchSnapshotSha256": validation_payload["batchSnapshotSha256"],
                    "agentScope": "task_and_batch_validation_commands",
                    "allowedRunnerCommands": ["validate-batch-task", "batch-check"],
                },
            )
            validated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_payload["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            self.assertEqual(
                json.loads(validated.stdout)["requiredAction"],
                "run_batch_check_in_validation_subagent",
            )
            task = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["validationEvidenceIds"], ["ev_0002"])
            self.assertEqual(task["completionEvidenceIds"], ["ev_0002"])
            validation_evidence = _evidence(feature_dir, "ev_0002")
            self.assertEqual(validation_evidence["validationTarget"], "batch_final_snapshot")
            self.assertEqual(validation_evidence["latestImplementationEvidenceId"], "ev_0001")

            _check_batch(workspace, code)
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            self.assertEqual(check_code_done(feature_dir), [])

    def test_missing_validation_executable_reports_environment_and_can_retry_after_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            missing_executable = Path(tmp) / "validation-tool"
            batch = _read_batch(feature_dir)
            task = batch["tasks"][0]
            task["validationCommands"][0]["argv"] = [str(missing_executable)]
            batch["taskValidation"]["taskContractSha256ByTask"] = {
                "T001": task_contract_sha256(task)
            }
            _write_batch(feature_dir, batch)

            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)

            blocked = _run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001",
                "--code-workspace", str(code),
            )
            self.assertNotEqual(blocked.returncode, 0)
            blocked_payload = json.loads(blocked.stdout)
            self.assertEqual(blocked_payload["error"], "validation_environment_unavailable:VAL-T001-01:executable_missing")
            self.assertEqual(
                blocked_payload["requiredAction"],
                "fix_validation_environment_and_retry_batch_validation",
            )
            self.assertIn("请修复验证环境后重新运行校验", blocked_payload["userMessage"])
            self.assertEqual(_read_batch(feature_dir)["taskValidation"]["status"], "ready")
            self.assertFalse((feature_dir / ".batch-task-validation-runs" / "B001").exists())

            missing_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            missing_executable.chmod(0o755)
            validation_run = _run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001",
                "--code-workspace", str(code),
            )
            self.assertEqual(validation_run.returncode, 0, validation_run.stdout + validation_run.stderr)
            payload = json.loads(validation_run.stdout)
            validated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", payload["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

    def test_maven_runner_requires_target_source_and_fresh_report_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = (Path(tmp) / "repo").resolve()
            repo.mkdir()
            (repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            source = repo / "src" / "test" / "java" / "example" / "AppTest.java"
            source.parent.mkdir(parents=True)
            source.write_text("class AppTest {}\n", encoding="utf-8")
            fake_maven = repo / "mvn"
            fake_maven.write_text(
                "#!/bin/sh\n"
                "mkdir -p target/surefire-reports\n"
                "printf '%s' '<testsuite name=\"example.AppTest\" tests=\"1\"><testcase classname=\"example.AppTest\" name=\"runs\"/></testsuite>' > target/surefire-reports/TEST-example.AppTest.xml\n",
                encoding="utf-8",
            )
            fake_maven.chmod(0o755)
            command = {
                "id": "VAL-T001-01",
                "argv": [str(fake_maven), "test", "-Dtest=example.AppTest"],
                "cwd": ".",
            }
            exit_code, output = task_runner_module._run_validation(
                command,
                {repo.name: repo},
            )
            self.assertEqual(exit_code, 0, output)

            missing_command = {
                **command,
                "id": "VAL-T001-02",
                "argv": [str(fake_maven), "test", "-Dtest=MissingTest"],
            }
            exit_code, output = task_runner_module._run_validation(
                missing_command,
                {repo.name: repo},
            )
            self.assertNotEqual(exit_code, 0)
            self.assertIn("validation_maven_test_target_missing", output)

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

    def test_runtime_environment_block_retries_same_validation_run_after_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            validation_tool = Path(tmp) / "runtime-validation-tool"
            ready_marker = Path(f"{validation_tool}.ready")
            validation_tool.write_text(
                "#!/bin/sh\n"
                f"test -f '{ready_marker}' || exit 127\n"
                "exit 0\n",
                encoding="utf-8",
            )
            validation_tool.chmod(0o755)
            _configure_deferred_task_validation(feature_dir)
            batch = _read_batch(feature_dir)
            task = batch["tasks"][0]
            task["validationCommands"][0]["argv"] = [str(validation_tool)]
            batch["taskValidation"]["taskContractSha256ByTask"] = {
                "T001": task_contract_sha256(task)
            }
            _write_batch(feature_dir, batch)

            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            self.assertEqual(_run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            ).returncode, 0)
            validation_run = _run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001",
                "--code-workspace", str(code),
            )
            self.assertEqual(validation_run.returncode, 0, validation_run.stdout + validation_run.stderr)
            run_id = json.loads(validation_run.stdout)["runId"]

            blocked = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", run_id, "--code-workspace", str(code),
            )
            self.assertNotEqual(blocked.returncode, 0)
            blocked_payload = json.loads(blocked.stdout)
            self.assertEqual(
                blocked_payload["requiredAction"],
                "fix_validation_environment_and_retry_same_run",
            )
            self.assertEqual(blocked_payload["runId"], run_id)
            self.assertEqual(_read_batch(feature_dir)["taskValidation"]["status"], "running")
            run_state = json.loads(
                (feature_dir / ".batch-task-validation-runs" / "B001" / f"{run_id}.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(run_state["status"], "running")
            self.assertEqual(run_state["evidenceIds"], [])

            ready_marker.write_text("ready\n", encoding="utf-8")
            retried = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", run_id, "--code-workspace", str(code),
            )
            self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
            self.assertEqual(json.loads(retried.stdout)["runId"], run_id)
            self.assertEqual(_read_batch(feature_dir)["taskValidation"]["status"], "passed")

    def test_deferred_validation_failure_can_retry_same_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; raise SystemExit(0 if Path('.git/info/retry-ready').exists() else 3)",
            ]
            batch["taskValidation"]["taskContractSha256ByTask"] = {
                "T001": task_contract_sha256(batch["tasks"][0])
            }
            _write_batch(feature_dir, batch)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code), "--run-id", started["runId"],
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            first_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            failed = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001", "--run-id", first_run["runId"],
                "--code-workspace", str(code),
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(json.loads(failed.stdout)["requiredAction"], "fix_task_validation")
            self.assertEqual(_read_batch(feature_dir)["taskValidation"]["status"], "failed")

            (code / ".git" / "info" / "retry-ready").write_text("ready\n", encoding="utf-8")
            retry_run = _run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertEqual(retry_run.returncode, 0, retry_run.stdout + retry_run.stderr)
            retry_payload = json.loads(retry_run.stdout)
            self.assertNotEqual(retry_payload["runId"], first_run["runId"])
            passed = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001", "--run-id", retry_payload["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            task = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(task["validationEvidenceIds"], ["ev_0002", "ev_0003"])
            self.assertEqual(task["completionEvidenceIds"], ["ev_0003"])

    def test_deferred_validation_repair_invalidates_current_batch_pointers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; raise SystemExit(0 if Path('implemented.txt').read_text().strip() == 'fixed' else 3)",
            ]
            batch["taskValidation"]["taskContractSha256ByTask"] = {
                "T001": task_contract_sha256(batch["tasks"][0])
            }
            _write_batch(feature_dir, batch)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("broken\n", encoding="utf-8")
            self.assertEqual(_run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code), "--run-id", started["runId"],
            ).returncode, 0)
            validation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            self.assertNotEqual(_run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            ).returncode, 0)

            repair = _run(
                "start-validation-repair", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            repair_payload = json.loads(repair.stdout)
            (code / "implemented.txt").write_text("fixed\n", encoding="utf-8")
            repaired = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", repair_payload["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
            task = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(task["status"], "implemented")
            self.assertEqual(task["completionEvidenceIds"], [])
            self.assertEqual(task["implementationRevision"], 2)
            self.assertEqual(_read_batch(feature_dir)["taskValidation"]["status"], "ready")

    def test_deferred_validation_freezes_batch_snapshot_until_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            started = _start(workspace, code)
            (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            validation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)

            plan_mutation = _run_plan_writer(
                "set-status", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "todo",
            )
            self.assertNotEqual(plan_mutation.returncode, 0)
            self.assertIn("task_validation_workspace_frozen", plan_mutation.stdout)
            with self.assertRaisesRegex(EvidenceStoreError, "task_validation_evidence_frozen:B001"):
                append_evidence(
                    feature_dir,
                    {
                        "checkpoint": "code_in_progress",
                        "nodeId": "dev.code",
                        "skill": "autodev-code",
                        "taskId": "T001",
                        "action": "smoke",
                    },
                )

            (code / "implemented.txt").write_text("changed during validation\n", encoding="utf-8")
            frozen = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertNotEqual(frozen.returncode, 0)
            self.assertIn("task_validation_workspace_changed", frozen.stdout)
            batch = _read_batch(feature_dir)
            self.assertEqual(batch["taskValidation"]["status"], "failed")
            self.assertEqual(batch["tasks"][0]["status"], "failed")
            self.assertEqual(batch["tasks"][0]["completionEvidenceIds"], [])

            repair = _run(
                "start-validation-repair", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            repaired_batch = _read_batch(feature_dir)
            self.assertEqual(repaired_batch["taskValidation"]["status"], "invalidated")
            self.assertEqual(repaired_batch["tasks"][0]["status"], "in_progress")

    def test_deferred_validation_allows_implemented_dependency_and_enforces_queue_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            second = json.loads(json.dumps(batch["tasks"][0]))
            second.update({"id": "T002", "title": "second behavior", "deps": ["T001"]})
            second["acceptanceCriteria"][0]["id"] = "AC-T002-01"
            second["validationCommands"][0].update({
                "id": "VAL-T002-01",
                "covers": ["AC-T002-01"],
            })
            batch["tasks"].append(second)
            batch["taskCount"] = 2
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batches"][0]["taskIds"] = ["T001", "T002"]
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _configure_deferred_task_validation(feature_dir)

            first_run = _start(workspace, code)
            (code / "first.txt").write_text("first\n", encoding="utf-8")
            first_finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", first_run["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(first_finished.returncode, 0, first_finished.stdout + first_finished.stderr)
            self.assertEqual(json.loads(first_finished.stdout)["nextTaskId"], "T002")

            second_started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(code),
            )
            self.assertEqual(second_started.returncode, 0, second_started.stdout + second_started.stderr)
            second_run = json.loads(second_started.stdout)
            (code / "second.txt").write_text("second\n", encoding="utf-8")
            second_finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--run-id", second_run["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(second_finished.returncode, 0, second_finished.stdout + second_finished.stderr)

            validation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            statuses = [task["status"] for task in _read_batch(feature_dir)["tasks"]]
            self.assertEqual(statuses, ["validating", "implemented"])
            out_of_order = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T002",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertNotEqual(out_of_order.returncode, 0)
            self.assertIn("task_validation_out_of_order", out_of_order.stdout)

            first_validated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(first_validated.returncode, 0, first_validated.stdout + first_validated.stderr)
            self.assertEqual(json.loads(first_validated.stdout)["currentTaskId"], "T002")
            self.assertEqual(
                [task["status"] for task in _read_batch(feature_dir)["tasks"]],
                ["done", "validating"],
            )
            second_validated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T002",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(second_validated.returncode, 0, second_validated.stdout + second_validated.stderr)
            self.assertEqual(_read_batch(feature_dir)["taskValidation"]["status"], "passed")

    def test_deferred_task_covered_closes_only_after_task_validation_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_frontend_task_covered(feature_dir, code)
            _configure_deferred_task_validation(feature_dir)
            started = _start(workspace, code)
            (code / "existing.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            self.assertEqual(_read_batch(feature_dir)["batchValidation"]["status"], "pending")

            validation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            validated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            batch = _read_batch(feature_dir)
            self.assertEqual(batch["taskValidation"]["status"], "passed")
            self.assertEqual(batch["batchValidation"]["status"], "passed")
            closure_id = batch["batchValidation"]["latestPassEvidenceIds"][0]
            self.assertEqual(_evidence(feature_dir, closure_id)["action"], "batch_closure")
            self.assertEqual(check_code_done(feature_dir), [])

    def test_frontend_build_can_validate_task_and_close_task_covered_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_frontend_task_covered(feature_dir, code)
            _configure_deferred_task_validation(feature_dir)
            started = _start(workspace, code)
            (code / "existing.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)

            validation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            validated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            batch = _read_batch(feature_dir)
            validation_id = batch["tasks"][0]["completionEvidenceIds"][0]
            self.assertEqual(_evidence(feature_dir, validation_id)["validation"]["assuranceLevel"], "compile")
            self.assertEqual(batch["batchValidation"]["status"], "passed")

    def test_frontend_package_script_placeholder_is_environment_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_frontend_task_covered(feature_dir, code)
            (code / "package.json").write_text(
                json.dumps({"scripts": {"build": "echo validation placeholder"}}) + "\n",
                encoding="utf-8",
            )
            _configure_deferred_task_validation(feature_dir)
            started = _start(workspace, code)
            (code / "existing.txt").write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)

            blocked = _run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(blocked.returncode, 0)
            payload = json.loads(blocked.stdout)
            self.assertEqual(
                payload["error"],
                "validation_environment_unavailable:VAL-T001-01:validation_command_placeholder",
            )
            self.assertEqual(payload["requiredAction"], "fix_validation_environment_and_retry_batch_validation")

    def test_deferred_task_covered_recovery_rechecks_snapshot_before_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_frontend_task_covered(feature_dir, code)
            _configure_deferred_task_validation(feature_dir)
            started = _start(workspace, code)
            target = code / "existing.txt"
            target.write_text("implemented\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", started["runId"],
                "--code-workspace", str(code),
            )
            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            validation_state = task_runner_module.start_batch_task_validation(
                workspace,
                "alpha",
                "B001",
                code,
            )

            with patch.object(
                task_runner_module,
                "_close_task_covered_batch",
                side_effect=RuntimeError("simulated closure interruption"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated closure interruption"):
                    task_runner_module.validate_batch_task(
                        workspace,
                        "alpha",
                        "B001",
                        "T001",
                        code,
                        validation_state["runId"],
                    )

            batch_after_interruption = _read_batch(feature_dir)
            self.assertEqual(batch_after_interruption["taskValidation"]["status"], "passed")
            self.assertEqual(batch_after_interruption["batchValidation"]["status"], "pending")
            target.write_text("changed after validation pass\n", encoding="utf-8")

            recovered = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", validation_state["runId"], "--code-workspace", str(code),
            )

            self.assertNotEqual(recovered.returncode, 0)
            payload = json.loads(recovered.stdout)
            self.assertEqual(payload["error"], "task_validation_workspace_changed_after_pass")
            self.assertEqual(payload["requiredAction"], "restore_batch_snapshot_and_retry_same_run")
            self.assertEqual(_read_batch(feature_dir)["batchValidation"]["status"], "pending")

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
            forged_command = [sys.executable, "-c", "print('forged')"]
            batch["batchValidation"]["commands"][0]["argv"] = forged_command
            root["batchValidationProfiles"]["backend"]["commands"][0]["argv"] = forged_command
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

    def test_optional_batch_failure_does_not_poison_code_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            optional_pass = {
                "id": "BATCH-B001-VAL-002",
                "argv": [sys.executable, "-c", "print('optional ok')"],
                "cwd": ".",
                "kind": "lint",
                "required": False,
            }
            batch = _read_batch(feature_dir)
            optional = {
                "id": "BATCH-B001-VAL-003",
                "argv": [sys.executable, "-c", "raise SystemExit(9)"],
                "cwd": ".",
                "kind": "lint",
                "required": False,
            }
            batch["batchValidation"]["commands"].extend([optional_pass, optional])
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"]["commands"].extend(
                [
                    {key: value for key, value in command.items() if key != "id"}
                    for command in (optional_pass, optional)
                ]
            )
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
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            payload = json.loads(checked.stdout)
            batch = _read_batch(feature_dir)
            self.assertEqual(len(batch["batchValidation"]["evidenceIds"]), 3)
            self.assertEqual(len(batch["batchValidation"]["latestPassEvidenceIds"]), 1)
            self.assertEqual(
                _evidence(feature_dir, batch["batchValidation"]["evidenceIds"][-1])["validation"]["result"],
                "fail",
            )
            self.assertEqual(payload["requiredAction"], "batch_validation_passed")
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            self.assertEqual(check_code_done(feature_dir), [])

    def test_batch_check_adopts_evidence_after_append_crash(self) -> None:
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
            real_append = task_runner_module.append_evidence

            def append_then_crash(*args: object, **kwargs: object) -> dict:
                real_append(*args, **kwargs)
                raise RuntimeError("crash after batch evidence append")

            with patch.object(task_runner_module, "append_evidence", side_effect=append_then_crash):
                with self.assertRaisesRegex(RuntimeError, "crash after batch evidence append"):
                    task_runner_module.run_batch_checks(workspace, "alpha", "B001", code)

            run_path = next((feature_dir / ".batch-runs" / "B001").glob("*.json"))
            session = task_runner_module.code_session(workspace, "alpha")
            self.assertEqual(session["action"], "run_batch_check")
            self.assertEqual(session["activeRunId"], run_path.stem)
            success, state = task_runner_module.run_batch_checks(
                workspace,
                "alpha",
                "B001",
                code,
                run_path.stem,
            )

            self.assertTrue(success)
            self.assertEqual(state["status"], "done")
            batch_records = [
                record
                for record in task_runner_module.read_records(feature_dir / "evidence" / "EVIDENCE.jsonl")
                if record.get("action") == "batch_validation"
            ]
            self.assertEqual(len(batch_records), 1)

    def test_batch_check_recovers_run_created_before_plan_binding(self) -> None:
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

            with patch.object(
                task_runner_module,
                "start_batch_validation_run",
                side_effect=RuntimeError("crash before batch plan binding"),
            ):
                with self.assertRaisesRegex(RuntimeError, "crash before batch plan binding"):
                    task_runner_module.run_batch_checks(workspace, "alpha", "B001", code)

            run_path = next((feature_dir / ".batch-runs" / "B001").glob("*.json"))
            with self.assertRaisesRegex(
                task_runner_module.TaskRunnerError,
                f"active_batch_run_exists:{run_path.stem}",
            ) as raised:
                task_runner_module.run_batch_checks(workspace, "alpha", "B001", code)
            self.assertEqual(raised.exception.details["requiredAction"], "retry_same_batch_run")
            self.assertEqual(raised.exception.details["runId"], run_path.stem)

            success, state = task_runner_module.run_batch_checks(
                workspace,
                "alpha",
                "B001",
                code,
                run_path.stem,
            )
            self.assertTrue(success)
            self.assertEqual(state["status"], "done")

    def test_batch_check_recovers_after_terminal_plan_binding_crash(self) -> None:
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
            real_bind = task_runner_module.record_batch_validation_attempt

            def bind_then_crash(*args: object, **kwargs: object) -> object:
                real_bind(*args, **kwargs)
                raise RuntimeError("crash after terminal plan binding")

            with patch.object(task_runner_module, "record_batch_validation_attempt", side_effect=bind_then_crash):
                with self.assertRaisesRegex(RuntimeError, "crash after terminal plan binding"):
                    task_runner_module.run_batch_checks(workspace, "alpha", "B001", code)

            run_path = next((feature_dir / ".batch-runs" / "B001").glob("*.json"))
            success, state = task_runner_module.run_batch_checks(
                workspace,
                "alpha",
                "B001",
                code,
                run_path.stem,
            )

            self.assertTrue(success)
            self.assertEqual(state["status"], "done")
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            self.assertEqual(check_code_done(feature_dir), [])

    def test_batch_check_recovers_interrupted_plan_bundle_commit(self) -> None:
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
            real_atomic_write = plan_writer_module.atomic_write_json
            root_writes = 0

            def interrupt_terminal_root(path: Path, data: object) -> bool:
                nonlocal root_writes
                if path == feature_dir / "plan.json":
                    root_writes += 1
                    if root_writes == 2:
                        raise RuntimeError("crash before terminal root projection")
                return real_atomic_write(path, data)

            with patch.object(
                plan_writer_module,
                "atomic_write_json",
                side_effect=interrupt_terminal_root,
            ):
                with self.assertRaisesRegex(RuntimeError, "crash before terminal root projection"):
                    task_runner_module.run_batch_checks(workspace, "alpha", "B001", code)

            transaction_path = feature_dir / ".plan-write-transaction.json"
            self.assertTrue(transaction_path.is_file())
            with self.assertRaisesRegex(ValueError, "root_status_projection_mismatch"):
                task_runner_module.load_plan_bundle(feature_dir)
            run_path = next((feature_dir / ".batch-runs" / "B001").glob("*.json"))
            self.assertEqual(json.loads(run_path.read_text(encoding="utf-8"))["status"], "evidence_written")

            success, state = task_runner_module.run_batch_checks(
                workspace,
                "alpha",
                "B001",
                code,
                run_path.stem,
            )

            self.assertTrue(success)
            self.assertEqual(state["status"], "done")
            self.assertFalse(transaction_path.exists())
            bundle = task_runner_module.load_plan_bundle(feature_dir)
            self.assertEqual(bundle.batches["B001"]["batchValidation"]["status"], "passed")

    def test_batch_check_recovers_after_revalidation_plan_binding_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            command = {
                "id": "BATCH-B001-VAL-001",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; raise SystemExit(0 if Path('existing.txt').read_text().strip() == 'fixed' else 3)",
                ],
                "cwd": ".",
                "kind": "compile",
                "required": True,
            }
            batch = _read_batch(feature_dir)
            batch["batchValidation"]["commands"] = [command]
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
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
            success, failed_state = task_runner_module.run_batch_checks(workspace, "alpha", "B001", code)
            self.assertFalse(success)
            (code / "existing.txt").write_text("fixed\n", encoding="utf-8")
            real_bind = task_runner_module.request_batch_revalidation

            def bind_then_crash(*args: object, **kwargs: object) -> object:
                real_bind(*args, **kwargs)
                raise RuntimeError("crash after revalidation plan binding")

            with patch.object(task_runner_module, "request_batch_revalidation", side_effect=bind_then_crash):
                with self.assertRaisesRegex(RuntimeError, "crash after revalidation plan binding"):
                    task_runner_module.run_batch_checks(
                        workspace,
                        "alpha",
                        "B001",
                        code,
                        failed_state["runId"],
                    )

            success, recovered = task_runner_module.run_batch_checks(
                workspace,
                "alpha",
                "B001",
                code,
                failed_state["runId"],
            )

            self.assertTrue(success)
            self.assertEqual(recovered["status"], "revalidation_required")
            current = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(current["pendingRevalidation"]["supersedesEvidenceIds"], ["ev_0001"])

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
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
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
            self.assertEqual(
                current_task["completedRevalidation"],
                {
                    "attemptType": "batch_revalidation",
                    "triggeredByBatchEvidenceIds": ["ev_0003"],
                    "supersedesEvidenceIds": ["ev_0001"],
                    "completionEvidenceIds": ["ev_0004"],
                },
            )
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
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            self.assertEqual(check_code_done(feature_dir), [])

            forged_batch = _read_batch(feature_dir)
            forged_batch["tasks"][0]["completedRevalidation"]["triggeredByBatchEvidenceIds"] = [
                "ev_9999"
            ]
            _write_batch(feature_dir, forged_batch)
            self.assertIn(
                "T001.batch_revalidation_trigger_invalid:ev_9999",
                check_code_done(feature_dir),
            )

    def test_deferred_batch_repair_revalidates_tasks_then_resumes_original_batch_run(self) -> None:
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
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
            batch["tasks"][0]["scope"]["paths"] = ["existing.txt"]
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"]["commands"] = [
                {key: value for key, value in command.items() if key != "id"}
            ]
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _configure_deferred_task_validation(feature_dir)

            implementation_run = _start(workspace, code)
            implemented = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--run-id", implementation_run["runId"],
                "--code-workspace", str(code),
                "--no-code-change-why", "existing implementation is sufficient",
                "--supporting-file", "existing.txt",
            )
            self.assertEqual(implemented.returncode, 0, implemented.stdout + implemented.stderr)
            task_validation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            initial_task_validation = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", task_validation_run["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(
                initial_task_validation.returncode,
                0,
                initial_task_validation.stdout + initial_task_validation.stderr,
            )

            failed_batch = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(failed_batch.returncode, 0)
            failed_payload = json.loads(failed_batch.stdout)
            self.assertEqual(failed_payload["requiredAction"], "fix_batch_and_retry_same_run")

            (code / "existing.txt").write_text("fixed\n", encoding="utf-8")
            repaired_batch = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
                "--run-id", failed_payload["runId"],
            )
            self.assertEqual(repaired_batch.returncode, 0, repaired_batch.stdout + repaired_batch.stderr)
            repaired_payload = json.loads(repaired_batch.stdout)
            self.assertEqual(repaired_payload["requiredAction"], "run_batch_task_validation")
            self.assertEqual(repaired_payload["affectedTaskIds"], ["T001"])
            task_after_repair = _read_batch(feature_dir)["tasks"][0]
            self.assertEqual(task_after_repair["status"], "implemented")
            self.assertEqual(task_after_repair["completionEvidenceIds"], [])
            self.assertIsInstance(task_after_repair.get("pendingRevalidation"), dict)

            revalidation_run = json.loads(_run(
                "start-batch-task-validation", "--workspace", str(workspace),
                "--feature", "alpha", "--batch-id", "B001", "--code-workspace", str(code),
            ).stdout)
            revalidated = _run(
                "validate-batch-task", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--task-id", "T001",
                "--run-id", revalidation_run["runId"], "--code-workspace", str(code),
            )
            self.assertEqual(revalidated.returncode, 0, revalidated.stdout + revalidated.stderr)
            current_task = _read_batch(feature_dir)["tasks"][0]
            self.assertNotIn("pendingRevalidation", current_task)
            self.assertEqual(
                _evidence(feature_dir, current_task["completionEvidenceIds"][0])["attemptType"],
                "batch_revalidation",
            )

            final_batch = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
                "--run-id", failed_payload["runId"],
            )
            self.assertEqual(final_batch.returncode, 0, final_batch.stdout + final_batch.stderr)
            self.assertEqual(json.loads(final_batch.stdout)["requiredAction"], "batch_validation_passed")
            project = _run(
                "project-check", "--workspace", str(workspace), "--feature", "alpha",
                "--code-workspace", str(code),
            )
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            self.assertEqual(check_code_done(feature_dir), [])

    def test_batch_repair_revalidates_same_workspace_changes_without_scope_filter(self) -> None:
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
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
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

            self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
            payload = json.loads(retried.stdout)
            self.assertEqual(payload["requiredAction"], "revalidate_affected_tasks")
            self.assertEqual(payload["affectedTaskIds"], ["T001"])

    def test_batch_repair_rejects_change_outside_requested_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "module"
            module.mkdir(parents=True)
            (module / "existing.txt").write_text("original\n", encoding="utf-8")
            _git(code, "add", "backend/module/existing.txt")
            _git(code, "commit", "-m", "add module baseline")
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
                "cwd": "backend/module",
                "kind": "compile",
                "required": True,
            }
            batch = _read_batch(feature_dir)
            _bind_workspace_contract(
                feature_dir,
                batch,
                {"default": "backend/module"},
                cwd="backend/module",
            )
            batch["tasks"][0]["scope"]["paths"] = ["src"]
            batch["batchValidation"]["commands"] = [command]
            _write_batch(feature_dir, batch)
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            root["batchValidationProfiles"]["backend"]["commands"] = [
                {key: value for key, value in command.items() if key != "id"}
            ]
            root_path.write_text(
                json.dumps(root, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            started = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(module),
                "--run-id", json.loads(started.stdout)["runId"],
                "--no-code-change-why", "existing implementation is sufficient",
                "--supporting-file", "backend/module/existing.txt",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            failed = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(module),
            )
            self.assertNotEqual(failed.returncode, 0)
            failed_payload = json.loads(failed.stdout)
            (code / "outside.txt").write_text("outside\n", encoding="utf-8")

            retried = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(module),
                "--run-id", failed_payload["runId"],
            )
            self.assertNotEqual(retried.returncode, 0)
            self.assertIn("batch_fix_outside_workspace:outside.txt", retried.stdout)

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

    def test_task_covered_batch_closes_without_running_batch_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_frontend_task_covered(feature_dir, code)
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
            self.assertEqual(payload["requiredAction"], "code_done_ready")
            self.assertFalse(payload["stopAfterBatch"])
            batch = _read_batch(feature_dir)
            self.assertEqual(batch["status"], "done")
            self.assertEqual(batch["batchValidation"]["status"], "passed")
            self.assertEqual(batch["batchValidation"]["commands"], [])
            self.assertFalse((feature_dir / ".batch-runs").exists())
            records = [
                json.loads(line)
                for line in (feature_dir / "evidence" / "EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["action"] for record in records], ["validation", "batch_closure"])
            self.assertEqual(records[-1]["coverage"]["sourceEvidenceIds"], ["ev_0001"])
            self.assertEqual(check_code_done(feature_dir), [])

            checked = _run(
                "batch-check", "--workspace", str(workspace), "--feature", "alpha",
                "--batch-id", "B001", "--code-workspace", str(code),
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("batch_check_not_required:B001:task_covered", checked.stdout)

            forged = _read_batch(feature_dir)
            forged["batchValidation"]["latestPassEvidenceIds"] = ["ev_0001"]
            _write_batch(feature_dir, forged)
            self.assertIn(
                "B001.invalid_task_covered_closure:ev_0001",
                check_code_done(feature_dir),
            )

    def test_complete_classifies_new_untracked_test_as_transient_validation_asset(self) -> None:
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

    def test_complete_records_tests_even_when_scope_paths_omit_them(self) -> None:
        for test_state in ("staged", "preexisting_untracked", "tracked"):
            with self.subTest(test_state=test_state), tempfile.TemporaryDirectory() as tmp:
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

                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                evidence = _evidence(feature_dir, "ev_0001")
                test_path = (
                    "backend/LF39.05_bccompliancemng/"
                    "src/test/java/example/application/AppTest.java"
                )
                if test_state == "staged":
                    self.assertEqual(evidence["changedFiles"], [])
                    self.assertEqual(evidence["transientValidationFiles"], [test_path])
                else:
                    self.assertEqual(evidence["changedFiles"], [test_path])

    def test_complete_keeps_tests_outside_requested_workspace_in_formal_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            module = code / "backend" / "LF39.05_bccompliancemng"
            sibling = code / "backend" / "LF39.05_other"
            module.mkdir(parents=True)
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

    def test_start_rejects_missing_maven_manifest_before_run_creation(self) -> None:
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

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("validation_manifest_missing:VAL-T001-01", started.stdout)
            self.assertEqual(list((feature_dir / ".task-runs").glob("T001/*.json")), [])

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

    def test_module_change_missing_from_scope_is_recorded_without_plan_rebuild(self) -> None:
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

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(
                _evidence(feature_dir, "ev_0001")["changedFiles"],
                [
                    "backend/LF39.05_bccompliancemng/"
                    "src/main/java/example/domain/Service.java"
                ],
            )

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

    def test_legacy_run_without_scope_base_uses_git_root_relative_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
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
            state["version"] = 1
            state.pop("integritySha256", None)
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

    def test_finish_implementation_accumulates_changes_across_aborted_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            _configure_deferred_task_validation(feature_dir)
            original = _start(workspace, code)
            (code / "first-implementation.txt").write_text("first\n", encoding="utf-8")

            aborted = _run(
                "abort", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", original["runId"], "--force-with-changes",
                "--abort-why", "scope repair before retry",
            )
            self.assertEqual(aborted.returncode, 0, aborted.stdout + aborted.stderr)

            retry = _start(workspace, code)
            (code / "second-implementation.txt").write_text("second\n", encoding="utf-8")
            finished = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", retry["runId"],
            )

            self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
            payload = json.loads(finished.stdout)
            self.assertEqual(
                payload["changedFiles"],
                ["first-implementation.txt", "second-implementation.txt"],
            )
            evidence = _evidence(feature_dir, "ev_0001")
            self.assertEqual(
                evidence["changedFiles"],
                ["first-implementation.txt", "second-implementation.txt"],
            )
            self.assertFalse(evidence["implementation"]["noCodeChange"])
            retry_state = json.loads(
                (feature_dir / ".task-runs" / "T001" / f"{retry['runId']}.json").read_text()
            )
            self.assertEqual(
                retry_state["changedFiles"],
                ["first-implementation.txt", "second-implementation.txt"],
            )

    def test_verified_existing_rejects_changes_from_prior_aborted_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            batch = _read_batch(feature_dir)
            batch["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
            batch["tasks"][0]["scope"]["paths"] = ["unrelated-planned-path"]
            _write_batch(feature_dir, batch)
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

    def test_complete_records_changes_outside_advisory_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["scope"]["workspaceRoots"] = {"default": "."}
            plan["tasks"][0]["scope"]["paths"] = ["src"]
            _write_batch(feature_dir, plan)
            started = _start(workspace, code)
            (code / "outside.txt").write_text("outside\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(_evidence(feature_dir, "ev_0001")["changedFiles"], ["outside.txt"])

    def test_complete_rejects_task_contract_changed_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            plan_path = _batch_path(feature_dir)
            plan = _read_batch(feature_dir)
            plan["tasks"][0]["validationCommands"][0]["argv"] = [
                sys.executable,
                "-c",
                "print('changed')",
            ]
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

    def test_complete_rejects_task_run_baseline_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code = _workspace(Path(tmp))
            started = _start(workspace, code)
            run_path = feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json"
            state = json.loads(run_path.read_text(encoding="utf-8"))
            state["resolvedScopePaths"] = ["forged/path"]
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            completed = _run(
                "complete", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
                "--no-code-change-why", "existing behavior is sufficient",
                "--supporting-file", "existing.txt",
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("task_run_integrity_mismatch", completed.stdout)
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
            state["version"] = 1
            state.pop("integritySha256", None)
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
            state["snapshot"] = {"forged.txt": "0" * 64}
            run_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            self.assertIn(
                f"T001.task_run_integrity_mismatch:{started['runId']}",
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
                        "kind": "integration_test",
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
