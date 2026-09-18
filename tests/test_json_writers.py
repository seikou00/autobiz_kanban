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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTODEV_HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS))

from hooks.json_writer_common import shell_join  # noqa: E402
from hooks.design_contract_lock import sync_design_contract_lock  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    task_set_digest,
)
from hooks.parallel_validation_ownership import build_pipeline_contract  # noqa: E402
from hooks.stage_gate import validate_stage  # noqa: E402
from skills.autodev.hooks.artifact_check import run_postcheck  # noqa: E402


TEST_TASK_COMMAND = f'{sys.executable} -c "print(\'task validation\')"'


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
    _write_design(feature_dir)
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
                "## 2. Code Evidence / 代码探索证据",
                "| ID | 事实 | 位置 |",
                "|----|------|------|",
                "| EVD-01 | no-op | src/cap.py |",
                "## 3. Spec Traceability / 规格追踪",
                "| Spec | Requirement / Scenario | Design Coverage |",
                "|------|------------------------|-----------------|",
                "| specs/cap/spec.md | Requirement [REQ-001] / Scenario [SCN-001] | API-001 / DATA-001 / D-001 |",
                "| specs/cap/spec.md | Requirement [REQ-001] / Scenario [SCN-002] | API-001 / DATA-001 / D-001 |",
                "## 3. API Decisions / 接口决策",
                "- x-auto-no-http-api: false",
                "| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |",
                "|----|--------|--------------|---------|----------|--------|-------------------|--------|",
                "| API-001 | 无 | 无 | 无 | 无 | 无 | 无 | 已确认 |",
                "## 4. Data Decisions / 数据决策",
                "- x-auto-no-sql: false",
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
    result = sync_design_contract_lock(feature_dir.parents[2], feature_dir.name)
    if not result.ok:
        raise AssertionError(result.errors)


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
        "workspaceRef": "default",
        "scope": {"modules": ["src"], "entrypoints": ["API-001"], "pages": [], "dataObjects": ["DATA-001"]},
        "implementationPoints": ["update behavior", "cover boundary"],
        "acceptanceCriteria": [{"id": "AC-T001-01", "text": "behavior is observable", "scenarioRefs": ["specs/cap/spec.md#SCN-001"]}],
        "validationBoundary": "public behavior seam",
        "nonGoals": ["do not change unrelated behavior"],
        "specRefs": spec_refs,
        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
        "apiIds": ["API-001"],
        "dataIds": ["DATA-001"],
        "decisionIds": ["D-001"],
        "completionPolicy": "all_required_validations_pass",
        "expectedFiles": [],
        "evidenceIds": [],
        "completionEvidenceIds": [],
        "latestPassEvidenceId": None,
        "blockers": [],
    }
    root = {
                "featureId": "alpha",
                "status": "todo",
                "taskSetStatus": "finalized",
                "activeBatchId": "B001",
                "nextBatchId": None,
                "taskValidationPolicy": {
                    "mode": "defer_to_test_stages",
                    "orchestration": "inline",
                    "codeGate": "review_only",
                    "maxTestStageRepairAttempts": 3,
                },
                "batchPolicy": {"maxTasks": 3, "strategy": "minimal_closed_delivery_v2"},
                "batches": [{
                    "id": "B001", "path": "plans/B001/plan.json", "title": "cap",
                    "specRoots": ["specs/cap/spec.md"], "executionLane": "backend",
                    "deps": [], "taskIds": ["T001"], "deliveryKind": "single_task", "status": "todo",
                }],
            }
    batch = {
        "featureId": "alpha",
        "batchId": "B001",
        "title": "cap",
        "executionLane": "backend",
        "status": "todo",
        "taskCount": 1,
        "completedTaskCount": 0,
        "completionEvidenceIds": [],
        "deliveryKind": "single_task",
        "startedAt": None,
        "completedAt": None,
        "tasks": [task],
    }
    # A finalized parallel plan carries the deterministic staged-pipeline
    # projection.  Keep this common fixture representative of a plan which is
    # eligible to leave the Plan stage; tests which exercise invalid pipeline
    # contracts mutate it explicitly.
    root["parallelBatchPipeline"] = build_pipeline_contract(root, {"B001": batch})
    root["taskSetDigest"] = task_set_digest(root, {"B001": batch})
    (feature_dir / "plan.json").write_text(
        json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    batch_path = feature_dir / "plans" / "B001" / "plan.json"
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text(
        json.dumps(batch, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (feature_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")


def _read_plan_tasks(feature_dir: Path) -> list[dict]:
    root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
    return [
        task
        for entry in root.get("batches", [])
        for task in json.loads((feature_dir / entry["path"]).read_text(encoding="utf-8")).get("tasks", [])
    ]


def _write_plan_tasks(feature_dir: Path, tasks: list[dict]) -> None:
    path = feature_dir / "plans" / "B001" / "plan.json"
    batch = json.loads(path.read_text(encoding="utf-8"))
    batch["tasks"] = tasks
    batch["taskCount"] = len(tasks)
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    def test_shell_join_quotes_arguments_on_python_37(self) -> None:
        self.assertEqual(shell_join(["python", "hello world", "plain"]), "python 'hello world' plain")


    def test_stage_gate_matches_run_postcheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_proposal(feature_dir)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir, include_second=False)
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
                        "notApplicableReason": "纯后端能力",
                        "pages": [],
                        "interactions": [],
                        "visualSources": [],
                        "capabilities": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            # Plan must consume the Design lock and not re-run the upstream
            # design.md validator.
            (feature_dir / "design.md").write_text("not a Design contract\n", encoding="utf-8")

            result = validate_stage(workspace=workspace, feature="alpha", stage="dev.plan")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code, _ = run_postcheck(ROOT, workspace, "autodev-plan", "alpha", workflow_record=_state_record())

            self.assertEqual(result.ok, code == 0)
            self.assertEqual([error["reason"] for error in result.errors or []], [])
            self.assertEqual(output.getvalue().strip(), "")
            self.assertFalse((feature_dir / "SMOKE_TEST_PLAN.json").exists())

    def test_stage_gate_ignores_invalid_checkpoint_from_other_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = _workspace(Path(tmp))
            state_path = workspace / ".autobizdevops" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["features"]["broken-other"] = {
                "feature": "broken-other",
                "checkpoint": "missing_checkpoint",
                "workflowTemplate": "standard",
            }
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

            with mock.patch("hooks.stage_gate.run_postcheck", return_value=(0, "ok")) as postcheck:
                result = validate_stage(workspace=workspace, feature="alpha", stage="dev.plan")

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.errors, [])
            postcheck.assert_called_once()

    def test_stage_gate_rejects_invalid_checkpoint_from_current_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = _workspace(Path(tmp))
            state_path = workspace / ".autobizdevops" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["features"]["alpha"]["checkpoint"] = "missing_checkpoint"
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

            with mock.patch("hooks.stage_gate.run_postcheck") as postcheck:
                result = validate_stage(workspace=workspace, feature="alpha", stage="dev.plan")

            self.assertFalse(result.ok)
            self.assertEqual(result.errors[0]["reason"], "invalid_state_json")
            self.assertIn("Feature 'alpha'", result.errors[0]["detail"])
            postcheck.assert_not_called()

    def test_postcheck_rejects_unknown_design_ref_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_proposal(feature_dir)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _write_plan(feature_dir)

            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["tasks"][0]["designRefs"] = [
                "design.md#API-777",
                "design.md#DATA-001",
                "design.md#D-001",
            ]
            batch_path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            root["taskSetDigest"] = task_set_digest(root, {"B001": batch})
            (feature_dir / "plan.json").write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code, _ = run_postcheck(ROOT, workspace, "autodev-plan", "alpha", workflow_record=_state_record())
            self.assertNotEqual(code, 0)
            self.assertIn("unknown_plan_json_api_ref", output.getvalue())


    def test_stage_gate_fails_fast_without_workspace_env(self) -> None:
        env = os.environ.copy()
        env.pop("PLUGIN_WORKSPACE", None)
        env.pop("PROJECT_DIR", None)
        env.pop("FEATURE_ID", None)

        result = _run("stage_gate.py", "validate", "--stage", "dev.plan", "--feature", "alpha", env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("path_resolution_failed", result.stdout)


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


    def test_result_writers_create_expected_ids_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_plan(feature_dir, include_second=False)

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
                "--priority",
                "P0",
                "--ui-required",
                "true",
                "--execution-mode",
                "browser",
                "--step-json",
                '{"action":"open","expected":"ok","verification":{"type":"ui","details":"visible"}}',
            )
            verify = _run("verify_decision_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha", "--from-specs")

            self.assertEqual(unit.returncode, 0, unit.stdout + unit.stderr)
            self.assertEqual(e2e.returncode, 0, e2e.stdout + e2e.stderr)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

            e2e_data = json.loads((feature_dir / "E2E_RESULT.json").read_text(encoding="utf-8"))
            verify_data = json.loads((feature_dir / "VERIFY_DECISION.json").read_text(encoding="utf-8"))

            self.assertEqual(e2e_data["cases"][0]["caseId"], "E2E-alpha-001")
            self.assertEqual(verify_data["nextCheckpoint"], "needs_fix")
            self.assertIn("uiSummary", verify_data)

    def test_result_writers_reject_missing_trace_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_plan(feature_dir, include_second=False)

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
                TEST_TASK_COMMAND,
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
                "browser",
            )
            self.assertNotEqual(unit.returncode, 0)
            self.assertIn("missing_unit_target_trace_args", unit.stdout)
            self.assertNotEqual(e2e.returncode, 0)
            self.assertIn("required", e2e.stderr)


if __name__ == "__main__":
    unittest.main()
