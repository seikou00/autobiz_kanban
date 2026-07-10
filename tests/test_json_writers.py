from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTODEV_HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS))

from hooks.json_writer_common import parse_postcheck_output  # noqa: E402
from hooks.stage_gate import validate_stage  # noqa: E402
from skills.autodev.hooks.artifact_check import run_postcheck  # noqa: E402


def _state_record(checkpoint: str = "plan_in_progress") -> dict:
    return {
        "feature": "alpha",
        "owner": "owner",
        "checkpoint": checkpoint,
        "stage": "Plan",
        "iteration": "1",
        "updated_at": "2026-07-08 00:00:00",
        "workflowProfile": "standard",
        "workflowDecisions": {},
        "workflowTemplate": "standard",
    }


def _workspace(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    feature_dir.mkdir(parents=True)
    (workspace / ".autobizdevops" / "state.json").write_text(
        json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {"alpha": _state_record()}}, indent=2),
        encoding="utf-8",
    )
    return workspace, feature_dir


def _write_specs(feature_dir: Path, *, second: bool = False) -> None:
    spec_dir = feature_dir / "specs" / "cap"
    spec_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "## ADDED Requirements",
        "### Requirement [REQ-001]: capability",
        "#### Scenario [SCN-001]: happy path",
    ]
    if second:
        lines.append("#### Scenario [SCN-002]: alternate path")
    (spec_dir / "spec.md").write_text("\n".join(lines), encoding="utf-8")


def _write_proposal(feature_dir: Path) -> None:
    (feature_dir / "proposal.md").write_text(
        "\n".join(
            [
                "# Proposal: cap",
                "## Why",
                "need cap",
                "## What Changes",
                "- change",
                "## Capabilities",
                "### New Capabilities",
                "- cap: capability",
                "## Impact",
                "- none",
                "## Out of Scope",
                "- none",
            ]
        ),
        encoding="utf-8",
    )


def _write_design(feature_dir: Path) -> None:
    (feature_dir / "design.md").write_text(
        "\n".join(
            [
                "# 技术设计: cap",
                "## 1. Context / 输入上下文",
                "## 2. Spec Traceability / 规格追踪",
                "| Spec | Requirement / Scenario | Design Coverage |",
                "|------|------------------------|-----------------|",
                "| specs/cap/spec.md | Requirement [REQ-001] / Scenario [SCN-001] | API-001 / DATA-001 / D-001 |",
                "| specs/cap/spec.md | Requirement [REQ-001] / Scenario [SCN-002] | API-001 / DATA-001 / D-001 |",
                "## 3. API Decisions / 接口决策",
                "- x-auto-no-http-api: true",
                "| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |",
                "|----|--------|--------------|---------|----------|--------|-------------------|--------|",
                "| API-001 | 无 | 无 | 无 | 无 | 无 | 无 | 已确认 |",
                "## 4. Data Decisions / 数据决策",
                "- x-auto-no-sql: true",
                "| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |",
                "|----|-------------|--------|--------|-----------------|----------|--------|",
                "| DATA-001 | 无 | 无 | 无 | 无 | 无 | 已确认 |",
                "## 5. Technical Design / 技术设计",
                "### Decisions",
                "| ID | Decision | Rationale | Alternatives | Status |",
                "|----|----------|-----------|--------------|--------|",
                "| D-001 | no-op | no-op | none | 已确认 |",
                "## 6. Risks / Open Questions",
                "| ID | Type | Description | Impact | Owner/Next Step |",
                "|----|------|-------------|--------|-----------------|",
                "| R-001 | 风险 | none | low | none |",
            ]
        ),
        encoding="utf-8",
    )


def _write_non_ui(feature_dir: Path) -> None:
    (feature_dir / "UI_CONTEXT.json").write_text(
        json.dumps(
            {
                "version": 1,
                "featureId": "alpha",
                "uiRequired": False,
                "decisionStatus": "locked",
                "decisionSource": "default_false",
                "confirmedAtCheckpoint": "prd_done",
                "lockedAtCheckpoint": "specs_done",
                "notApplicableReason": "纯后端",
                "pages": [],
                "interactions": [],
                "visualSources": [],
                "capabilities": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_plan(feature_dir: Path, *, include_second: bool = False) -> None:
    spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]
    if include_second:
        spec_refs.append("specs/cap/spec.md#SCN-002")
    task = {
        "id": "T001",
        "title": "do",
        "goal": "deliver behavior",
        "status": "todo",
        "deps": [],
        "uiRequired": False,
        "scope": {"modules": ["src"], "entrypoints": ["API-001"], "pages": [], "dataObjects": ["DATA-001"]},
        "implementationPoints": ["update behavior", "cover boundary"],
        "acceptanceCriteria": [{"id": "AC-T001-01", "text": "behavior is observable", "scenarioRefs": ["specs/cap/spec.md#SCN-001"]}],
        "nonGoals": ["do not change unrelated behavior"],
        "specRefs": spec_refs,
        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
        "apiIds": ["API-001"],
        "dataIds": ["DATA-001"],
        "decisionIds": ["D-001"],
        "completionPolicy": "all_required_validations_pass",
        "validationCommands": [{"id": "VAL-T001-01", "argv": ["echo", "ok"], "cwd": ".", "kind": "behavior_test", "required": True, "covers": ["AC-T001-01"]}],
        "expectedFiles": [],
        "evidenceIds": [],
        "completionEvidenceIds": [],
        "latestPassEvidenceId": None,
        "blockers": [],
    }
    (feature_dir / "plan.json").write_text(
        json.dumps(
            {
                "featureId": "alpha",
                "status": "todo",
                "activeBatchId": "B001",
                "nextBatchId": None,
                "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_topological"},
                "batches": [{
                    "id": "B001", "path": "plans/B001/plan.json", "title": "cap",
                    "specRoots": ["specs/cap/spec.md"], "deps": [], "taskIds": ["T001"], "status": "todo",
                }],
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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    batch_path = feature_dir / "plans" / "B001" / "plan.json"
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text(
        json.dumps(
            {
                "featureId": "alpha",
                "batchId": "B001",
                "title": "cap",
                "status": "todo",
                "taskCount": 1,
                "completedTaskCount": 0,
                "completionEvidenceIds": [],
                "startedAt": None,
                "completedAt": None,
                "tasks": [task],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    (feature_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")


def _read_plan_tasks(feature_dir: Path) -> list[dict]:
    batch = json.loads((feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8"))
    return batch["tasks"]


def _write_plan_tasks(feature_dir: Path, tasks: list[dict]) -> None:
    path = feature_dir / "plans" / "B001" / "plan.json"
    batch = json.loads(path.read_text(encoding="utf-8"))
    batch["tasks"] = tasks
    batch["taskCount"] = len(tasks)
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_smoke_na(feature_dir: Path) -> None:
    (feature_dir / "SMOKE_TEST_PLAN.json").write_text(
        json.dumps(
            {"version": 1, "featureId": "alpha", "flowBlocking": False, "skipReason": "无冒烟价值", "tests": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run(
    script: str,
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / script), *args],
        cwd=ROOT,
        text=True,
        input=input_text,
        capture_output=True,
        env=env,
        check=False,
    )


class JsonWriterTests(unittest.TestCase):
    def test_plan_writer_rejects_external_completion_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = _workspace(Path(tmp))
            _write_plan(workspace / ".autobizdevops" / "features" / "alpha")

            done = _run(
                "plan_writer.py", "set-status", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "done",
            )
            add = _run(
                "plan_writer.py", "add-evidence-id", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "ev_0001",
            )
            remove = _run(
                "plan_writer.py", "remove-evidence-id", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "ev_0001",
            )

            self.assertNotEqual(done.returncode, 0)
            self.assertIn("task_completion_requires_task_runner", done.stdout)
            self.assertNotEqual(add.returncode, 0)
            self.assertIn("task_evidence_binding_requires_task_runner", add.stdout)
            self.assertNotEqual(remove.returncode, 0)
            self.assertIn("task_evidence_binding_requires_task_runner", remove.stdout)

    def test_stage_gate_matches_run_postcheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_proposal(feature_dir)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_non_ui(feature_dir)
            _write_plan(feature_dir, include_second=False)
            _write_smoke_na(feature_dir)

            result = validate_stage(workspace=workspace, feature="alpha", stage="dev.plan")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code, _ = run_postcheck(ROOT, workspace, "autodev-plan", "alpha", workflow_record=_state_record())

            self.assertEqual(result.ok, code == 0)
            self.assertEqual([error["reason"] for error in result.errors or []], [])
            self.assertEqual(output.getvalue().strip(), "")

    def test_plan_structure_passes_while_stage_gate_fails_on_missing_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_proposal(feature_dir)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)
            _write_non_ui(feature_dir)
            _write_plan(feature_dir, include_second=False)
            _write_smoke_na(feature_dir)

            structure = _run("plan_writer.py", "validate", "--workspace", str(workspace), "--feature", "alpha", "--structure")
            gate = _run("stage_gate.py", "validate", "--workspace", str(workspace), "--feature", "alpha", "--stage", "dev.plan")
            stage_result = validate_stage(workspace=workspace, feature="alpha", stage="dev.plan")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code, message = run_postcheck(ROOT, workspace, "autodev-plan", "alpha", workflow_record=_state_record())
            postcheck_errors = parse_postcheck_output(output.getvalue(), fallback_message=message if code else "")

            self.assertEqual(structure.returncode, 0, structure.stdout + structure.stderr)
            self.assertNotEqual(gate.returncode, 0)
            self.assertIn("missing_plan_scenario_coverage", gate.stdout)
            self.assertFalse(stage_result.ok)
            self.assertNotEqual(code, 0)
            self.assertEqual(stage_result.errors, postcheck_errors)

    def test_stage_gate_fails_fast_without_workspace_env(self) -> None:
        env = os.environ.copy()
        env.pop("PLUGIN_WORKSPACE", None)
        env.pop("PROJECT_DIR", None)
        env.pop("FEATURE_ID", None)

        result = _run("stage_gate.py", "validate", "--stage", "dev.plan", "--feature", "alpha", env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("path_resolution_failed", result.stdout)

    def test_init_commands_refuse_existing_artifacts_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = _workspace(Path(tmp))

            first_plan = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            second_plan = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            forced_plan = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha", "--force")

            first_smoke = _run("smoke_plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            second_smoke = _run("smoke_plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            forced_smoke = _run("smoke_plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha", "--force")

            self.assertEqual(first_plan.returncode, 0, first_plan.stdout + first_plan.stderr)
            self.assertNotEqual(second_plan.returncode, 0)
            self.assertIn("artifact_already_exists", second_plan.stdout)
            self.assertEqual(forced_plan.returncode, 0, forced_plan.stdout + forced_plan.stderr)
            self.assertTrue(json.loads(forced_plan.stdout)["reset"])
            self.assertEqual(first_smoke.returncode, 0, first_smoke.stdout + first_smoke.stderr)
            self.assertNotEqual(second_smoke.returncode, 0)
            self.assertIn("artifact_already_exists", second_smoke.stdout)
            self.assertEqual(forced_smoke.returncode, 0, forced_smoke.stdout + forced_smoke.stderr)
            self.assertTrue(json.loads(forced_smoke.stdout)["reset"])

    def test_plan_writer_add_task_body_file_and_deps_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            body_file = root / "task.json"
            body_file.write_text(
                json.dumps(
                    {
                        "id": "T001",
                        "title": "body task",
                        "goal": "deliver body task",
                        "status": "done",
                        "deps": [],
                        "uiRequired": False,
                        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                        "implementationPoints": ["update behavior", "cover boundary"],
                        "acceptanceCriteria": ["behavior is observable"],
                        "nonGoals": [],
                        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": "echo ok"}],
                        "expectedFiles": [],
                        "evidenceIds": ["ev_0001"],
                        "blockers": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-file",
                str(body_file),
            )
            alias = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T002",
                "--title",
                "alias task",
                "--goal",
                "deliver alias task",
                "--deps",
                "T001",
                "--module",
                "src",
                "--implementation-point",
                "update behavior",
                "--implementation-point",
                "cover boundary",
                "--acceptance-criterion",
                "behavior is observable",
                "--spec-ref",
                "specs/cap/spec.md#REQ-001",
                "--spec-ref",
                "specs/cap/spec.md#SCN-001",
                "--design-ref",
                "design.md#D-001",
                "--decision-id",
                "D-001",
                "--validation-command",
                "echo ok",
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertEqual(body.returncode, 0, body.stdout + body.stderr)
            self.assertEqual(alias.returncode, 0, alias.stdout + alias.stderr)
            tasks = _read_plan_tasks(feature_dir)
            self.assertEqual(tasks[0]["id"], "T001")
            self.assertEqual(tasks[0]["status"], "todo")
            self.assertEqual(tasks[0]["evidenceIds"], [])
            self.assertEqual(tasks[1]["deps"], ["T001"])

    def test_plan_writer_body_file_reports_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _ = _workspace(root)
            body_file = root / "bad_task.json"
            body_file.write_text(
                json.dumps(
                    {
                        "id": "T001",
                        "goal": "missing title",
                        "status": "todo",
                        "deps": [],
                        "uiRequired": False,
                        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                        "implementationPoints": ["update behavior", "cover boundary"],
                        "acceptanceCriteria": ["behavior is observable"],
                        "nonGoals": [],
                        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": "echo ok"}],
                        "expectedFiles": [],
                        "evidenceIds": [],
                        "blockers": [],
                    }
                ),
                encoding="utf-8",
            )

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-file",
                str(body_file),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("invalid_plan_task_body", body.stdout)
            self.assertIn("title", body.stdout)

    def test_plan_writer_add_task_body_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            payload = {
                "id": "T001",
                "title": "stdin task",
                "goal": "deliver stdin task",
                "status": "done",
                "deps": [],
                "uiRequired": False,
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "nonGoals": [],
                "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
                "expectedFiles": [],
                "evidenceIds": ["ev_0001"],
                "blockers": [],
            }

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-stdin",
                input_text=json.dumps(payload, ensure_ascii=False),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertEqual(body.returncode, 0, body.stdout + body.stderr)
            tasks = _read_plan_tasks(feature_dir)
            self.assertEqual(tasks[0]["id"], "T001")
            self.assertEqual(tasks[0]["status"], "todo")
            self.assertEqual(tasks[0]["evidenceIds"], [])

    def test_plan_writer_body_stdin_rejects_conflicting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _ = _workspace(root)
            body_file = root / "task.json"
            payload = {
                "id": "T001",
                "title": "stdin task",
                "goal": "deliver stdin task",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "nonGoals": [],
                "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "validationCommands": [{"command": "echo ok"}],
            }
            body_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-file",
                str(body_file),
                "--body-stdin",
                input_text=json.dumps(payload, ensure_ascii=False),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("conflicting_task_body_sources", body.stdout)

    def test_plan_writer_body_stdin_rejects_empty_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = _workspace(Path(tmp))

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-stdin",
                input_text="",
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("empty_body_stdin", body.stdout)

    def test_plan_writer_rejects_oversized_task_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            payload = {
                "id": "T001",
                "title": "oversized task",
                "goal": "deliver too much behavior",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "nonGoals": [],
                "specRefs": [
                    "specs/cap/spec.md#REQ-001",
                    *[f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 10)],
                ],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
            }

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-stdin",
                input_text=json.dumps(payload, ensure_ascii=False),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("oversized_plan_task_must_split", body.stdout)
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["batches"], [])

    def test_plan_writer_rejects_large_task_without_split_rationale_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            payload = {
                "id": "T001",
                "title": "large task",
                "goal": "deliver many related scenarios",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "nonGoals": [],
                "specRefs": [
                    "specs/cap/spec.md#REQ-001",
                    *[f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 7)],
                ],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
            }

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-stdin",
                input_text=json.dumps(payload, ensure_ascii=False),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("missing_plan_task_split_rationale", body.stdout)
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["batches"], [])

    def test_plan_writer_add_task_cli_accepts_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            rationale = "SCN-001、SCN-003、SCN-006 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。"

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--title",
                "large grouped task",
                "--goal",
                "deliver many related scenarios",
                "--implementation-point",
                "update behavior",
                "--implementation-point",
                "cover boundary",
                "--acceptance-criterion",
                "behavior is observable",
                "--non-goal",
                "do not change unrelated behavior",
                "--spec-ref",
                "specs/cap/spec.md#REQ-001",
                *[
                    item
                    for index in range(1, 7)
                    for item in ("--spec-ref", f"specs/cap/spec.md#SCN-{index:03d}")
                ],
                "--design-ref",
                "design.md#D-001",
                "--decision-id",
                "D-001",
                "--validation-command",
                "echo ok",
                "--split-rationale",
                rationale,
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertEqual(body.returncode, 0, body.stdout + body.stderr)
            tasks = _read_plan_tasks(feature_dir)
            self.assertEqual(tasks[0]["id"], "T001")
            self.assertEqual(tasks[0]["splitRationale"], rationale)

    def test_plan_writer_counts_same_scenario_id_by_spec_path_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            payload = {
                "id": "T001",
                "title": "cross spec large task",
                "goal": "deliver many same-numbered scenarios",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "nonGoals": [],
                "specRefs": [
                    "specs/cap1/spec.md#REQ-001",
                    *[f"specs/cap{index}/spec.md#SCN-001" for index in range(1, 7)],
                ],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
            }

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-stdin",
                input_text=json.dumps(payload, ensure_ascii=False),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("missing_plan_task_split_rationale", body.stdout)
            self.assertIn("scenarios=6", body.stdout)
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["batches"], [])

    def test_plan_writer_accepts_cross_spec_path_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            payload = {
                "id": "T001",
                "title": "cross spec grouped task",
                "goal": "deliver same-numbered scenarios sharing one observable result",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "nonGoals": [],
                "specRefs": [
                    "specs/cap1/spec.md#REQ-001",
                    *[f"specs/cap{index}/spec.md#SCN-001" for index in range(1, 7)],
                ],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": "echo ok"}],
                "splitRationale": "specs/cap1/spec.md#SCN-001、specs/cap3/spec.md#SCN-001、specs/cap5/spec.md#SCN-001 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。",
            }

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = _run(
                "plan_writer.py",
                "add-task",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--body-stdin",
                input_text=json.dumps(payload, ensure_ascii=False),
            )

            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertEqual(body.returncode, 0, body.stdout + body.stderr)
            tasks = _read_plan_tasks(feature_dir)
            self.assertEqual(tasks[0]["id"], "T001")
            self.assertEqual(tasks[0]["splitRationale"], payload["splitRationale"])

    def test_code_task_context_resolves_refs_from_artifact_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir)

            result = _run("code_task_context.py", "--workspace", str(workspace), "--feature", "alpha", "--task-id", "T001")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(Path(payload["artifactFeatureDir"]).resolve(), feature_dir.resolve())
            self.assertEqual(payload["refResolution"]["specRefs"], "relative-to-artifactFeatureDir")
            self.assertTrue(all(item["found"] for item in payload["resolvedSpecRefs"]))
            self.assertTrue(all(item["found"] for item in payload["resolvedDesignRefs"]))
            self.assertIn("Scenario [SCN-001]", payload["resolvedSpecRefs"][1]["text"])
            self.assertIn("| API-001 |", payload["resolvedDesignRefs"][0]["text"])

    def test_code_task_context_fails_on_missing_ref_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir)
            tasks = _read_plan_tasks(feature_dir)
            tasks[0]["specRefs"].append("specs/cap/spec.md#SCN-999")
            _write_plan_tasks(feature_dir, tasks)

            result = _run("code_task_context.py", "--workspace", str(workspace), "--feature", "alpha", "--task-id", "T001")

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertIn("missing_ref_anchor", {error["reason"] for error in payload["errors"]})
            self.assertIn("specs/cap/spec.md#SCN-999", result.stdout)

    def test_code_task_context_fails_on_missing_ref_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir)
            tasks = _read_plan_tasks(feature_dir)
            tasks[0]["specRefs"].append("specs/missing/spec.md#SCN-001")
            _write_plan_tasks(feature_dir, tasks)

            result = _run("code_task_context.py", "--workspace", str(workspace), "--feature", "alpha", "--task-id", "T001")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing_ref_file", result.stdout)

    def test_code_task_context_rejects_absolute_and_traversal_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir)
            tasks = _read_plan_tasks(feature_dir)
            tasks[0]["specRefs"].extend(
                [
                    f"{Path(tmp).resolve() / 'outside.md'}#SCN-001",
                    "../outside.md#SCN-001",
                ]
            )
            _write_plan_tasks(feature_dir, tasks)

            result = _run("code_task_context.py", "--workspace", str(workspace), "--feature", "alpha", "--task-id", "T001")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid_artifact_ref", result.stdout)
            self.assertIn("不允许绝对路径", result.stdout)
            self.assertIn("引用路径越界", result.stdout)

    def test_code_task_context_rejects_ambiguous_short_scenario_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            (feature_dir / "specs" / "other").mkdir(parents=True)
            (feature_dir / "specs" / "other" / "spec.md").write_text(
                "\n".join(
                    [
                        "## ADDED Requirements",
                        "### Requirement [REQ-001]: other",
                        "#### Scenario [SCN-001]: same local id",
                    ]
                ),
                encoding="utf-8",
            )
            _write_design(feature_dir)
            _write_plan(feature_dir)
            tasks = _read_plan_tasks(feature_dir)
            tasks[0]["specRefs"] = ["specs/cap/spec.md#REQ-001", "#SCN-001"]
            _write_plan_tasks(feature_dir, tasks)

            result = _run("code_task_context.py", "--workspace", str(workspace), "--feature", "alpha", "--task-id", "T001")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("短引用 anchor 不唯一", result.stdout)

    def test_code_task_context_reports_task_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir)

            result = _run("code_task_context.py", "--workspace", str(workspace), "--feature", "alpha", "--task-id", "T999")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_not_found", result.stdout)

    def test_ui_context_writer_false_clears_ui_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = _workspace(Path(tmp))
            init = _run("ui_context_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha", "--ui-required")
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            locked = _run("ui_context_writer.py", "validate", "--workspace", str(workspace), "--feature", "alpha", "--locked")
            self.assertNotEqual(locked.returncode, 0)
            self.assertIn("ui_context_not_locked", locked.stdout)
            _run(
                "ui_context_writer.py",
                "add-page",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--name",
                "Page",
                "--goal",
                "Goal",
            )

            result = _run(
                "ui_context_writer.py",
                "set-ui-required",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "false",
                "--reason",
                "纯后端",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads((workspace / ".autobizdevops" / "features" / "alpha" / "UI_CONTEXT.json").read_text())
            self.assertFalse(data["uiRequired"])
            self.assertEqual(data["pages"], [])
            self.assertEqual(data["interactions"], [])
            self.assertEqual(data["visualSources"], [])
            self.assertEqual(data["capabilities"], [])

    def test_result_writers_create_expected_ids_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_plan(feature_dir, include_second=False)
            _write_non_ui(feature_dir)

            unit = _run("unit_test_result_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha", "--from-plan")
            e2e = _run(
                "e2e_result_writer.py",
                "add-case",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--spec-ref",
                "specs/cap/spec.md#SCN-001",
                "--evidence-id",
                "ev_0001",
                "--execution-mode",
                "manual",
                "--verdict",
                "PASS",
                "--step-json",
                '{"action":"open","expected":"ok"}',
            )
            review = _run(
                "review_findings_writer.py",
                "add-finding",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--spec-ref",
                "specs/cap/spec.md#SCN-001",
                "--evidence-id",
                "ev_0002",
                "--severity",
                "info",
                "--message",
                "ok",
            )
            verify = _run("verify_decision_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha", "--from-specs")

            self.assertEqual(unit.returncode, 0, unit.stdout + unit.stderr)
            self.assertEqual(e2e.returncode, 0, e2e.stdout + e2e.stderr)
            self.assertEqual(review.returncode, 0, review.stdout + review.stderr)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

            e2e_data = json.loads((feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
            review_data = json.loads((feature_dir / "REVIEW_FINDINGS.json").read_text(encoding="utf-8"))
            verify_data = json.loads((feature_dir / "VERIFY_DECISION.json").read_text(encoding="utf-8"))

            self.assertEqual(e2e_data["cases"][0]["caseId"], "E2E-alpha-001")
            self.assertEqual(review_data["findings"][0]["message"], "ok")
            self.assertEqual(verify_data["nextCheckpoint"], "needs_fix")
            self.assertEqual(verify_data["uiSummary"]["uiRequired"], False)

    def test_result_writers_reject_missing_trace_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_plan(feature_dir, include_second=False)
            _write_non_ui(feature_dir)

            unit = _run(
                "unit_test_result_writer.py",
                "add-target",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--command",
                "echo ok",
            )
            e2e = _run(
                "e2e_result_writer.py",
                "add-case",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--execution-mode",
                "manual",
                "--verdict",
                "PASS",
            )
            review = _run(
                "review_findings_writer.py",
                "add-finding",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--task-id",
                "T001",
                "--severity",
                "info",
                "--message",
                "ok",
            )

            self.assertNotEqual(unit.returncode, 0)
            self.assertIn("missing_unit_target_trace_args", unit.stdout)
            self.assertNotEqual(e2e.returncode, 0)
            self.assertIn("missing_e2e_case_args", e2e.stdout)
            self.assertNotEqual(review.returncode, 0)
            self.assertIn("missing_review_finding_args", review.stdout)


if __name__ == "__main__":
    unittest.main()
