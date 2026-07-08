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
    (feature_dir / "plan.json").write_text(
        json.dumps(
            {
                "version": 1,
                "taskDetailVersion": 1,
                "featureId": "alpha",
                "tasks": [
                    {
                        "id": "T001",
                        "title": "do",
                        "goal": "deliver behavior",
                        "status": "todo",
                        "deps": [],
                        "uiRequired": False,
                        "scope": {
                            "modules": ["src"],
                            "entrypoints": ["API-001"],
                            "pages": [],
                            "dataObjects": ["DATA-001"],
                        },
                        "implementationPoints": ["update behavior", "cover boundary"],
                        "acceptanceCriteria": ["behavior is observable"],
                        "nonGoals": ["do not change unrelated behavior"],
                        "specRefs": spec_refs,
                        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
                        "apiIds": ["API-001"],
                        "dataIds": ["DATA-001"],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": "echo ok"}],
                        "expectedFiles": [],
                        "evidenceIds": [],
                        "blockers": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (feature_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")


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


def _run(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class JsonWriterTests(unittest.TestCase):
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
