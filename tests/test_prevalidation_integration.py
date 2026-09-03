#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration tests for pre-validation improvements in set-draft-task-detail.

These drive the real plan_writer.py CLI end to end (prepare-task-draft ->
set-draft-task-detail) instead of calling internal helpers directly, so they
catch wiring problems the unit tests for artifact_ref_validator /
validation_policy cannot see on their own.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def _write_design(feature_dir: Path) -> None:
    (feature_dir / "design.md").write_text(
        "\n".join(
            [
                "# 技术设计: cap",
                "## 2. Code Evidence / 代码探索证据",
                "| ID | 事实 | 位置 |",
                "|----|------|------|",
                "| EVD-01 | no-op | src/cap.py |",
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
            ]
        ),
        encoding="utf-8",
    )


def _plan_task_body(task_id: str = "T001", *, scenario: str = "SCN-001") -> dict:
    return {
        "id": task_id,
        "title": f"do {task_id}",
        "goal": "deliver behavior",
        "deps": [],
        "uiRequired": False,
        "workspaceRef": "default",
        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
        "implementationPoints": ["update behavior", "cover boundary"],
        "acceptanceCriteria": [
            {
                "id": f"AC-{task_id}-01",
                "text": "behavior is observable",
                "scenarioRefs": [f"specs/cap/spec.md#{scenario}"],
            }
        ],
        "validationBoundary": "public behavior seam validated by the task command",
        "nonGoals": ["do not change unrelated behavior"],
        "specRefs": ["specs/cap/spec.md#REQ-001", f"specs/cap/spec.md#{scenario}"],
        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
        "apiIds": ["API-001"],
        "dataIds": ["DATA-001"],
        "decisionIds": ["D-001"],
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
    }


def _draft_detail_body(task: dict) -> dict:
    scope = dict(task["scope"])
    scope.pop("pages", None)
    criteria = [
        {"text": item["text"], "scenarioRefs": list(item["scenarioRefs"])}
        for item in task["acceptanceCriteria"]
    ]
    commands = []
    for item in task["validationCommands"]:
        command = {key: value for key, value in item.items() if key not in {"id", "covers"}}
        command["covers"] = list(range(1, len(criteria) + 1))
        commands.append(command)
    return {
        "goal": task["goal"],
        "scope": scope,
        "implementationPoints": list(task["implementationPoints"]),
        "acceptanceCriteria": criteria,
        "nonGoals": list(task["nonGoals"]),
        "designRefs": list(task["designRefs"]),
        "dataIds": list(task["dataIds"]),
        "decisionIds": list(task["decisionIds"]),
        "validationCommands": commands,
        "expectedFiles": list(task.get("expectedFiles", [])),
        "blockers": list(task.get("blockers", [])),
    }


def _write_task_groups(path: Path, tasks: list[dict]) -> Path:
    groups = []
    for task in tasks:
        groups.append({
            "id": task["id"],
            "title": task["title"],
            "executionMode": task.get("executionMode", "code"),
            "deps": list(task.get("deps", [])),
            "uiRequired": task.get("uiRequired") is True,
            "workspaceRef": task.get("workspaceRef", "default"),
            "specRefs": list(task.get("specRefs", [])),
            "mergedScenarioRefs": list(task.get("mergedScenarioRefs", [])),
            "apiIds": list(task.get("apiIds", [])),
            "validationBoundary": task.get(
                "validationBoundary",
                "public behavior seam validated by one executable command",
            ),
        })
    path.write_text(
        json.dumps({"featureId": "alpha", "groups": groups}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class PrevalidationIntegrationTests(unittest.TestCase):
    """Drive plan_writer.py's real CLI surface, not internal helpers."""

    def _finalize_single_task(self, root: Path) -> tuple[Path, Path, dict]:
        workspace, feature_dir = _workspace(root)
        _write_specs(feature_dir)
        _write_design(feature_dir)
        task = _plan_task_body()
        group_file = _write_task_groups(root / "task-groups.json", [task])
        prepared = _run(
            "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
            "--feature", "alpha", "--group-file", str(group_file),
            "--code-workspace", str(ROOT),
        )
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        detail_path = root / "detail.json"
        detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
        detailed = _run(
            "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
            "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
        )
        self.assertEqual(detailed.returncode, 0, detailed.stdout + detailed.stderr)
        finalized = _run(
            "plan_writer.py", "finalize-task-draft", "--workspace", str(workspace),
            "--feature", "alpha",
        )
        self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
        return workspace, feature_dir, task

    def test_group_rejects_unknown_api_as_plan_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            task["apiIds"] = ["API-999"]
            group_file = _write_task_groups(root / "task-groups.json", [task])
            result = _run(
                "plan_writer.py", "preflight-task-groups", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            issue = next(item for item in payload["errors"] if item["field"] == "apiIds[0]")
            self.assertEqual(issue["repairTarget"], "task_group")
            self.assertFalse(issue["designMutationAllowed"])
            self.assertEqual(issue["currentValue"], "API-999")

    def test_prepare_cannot_remint_design_lock_after_tmp_draft_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])
            first = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            shutil.rmtree(feature_dir / ".tmp" / "plan_writer")
            with (feature_dir / "design.md").open("a", encoding="utf-8") as handle:
                handle.write("\n<!-- revised without confirmation -->\n")
            second = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("confirmed_design_changed_without_reconfirmation", second.stdout)
            confirmed = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT), "--design-revision-confirmed",
                "--reason", "Design revision confirmed in plan gate",
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stdout + confirmed.stderr)

    def test_detail_rejects_unknown_data_and_decision_but_allows_empty_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            detail = _draft_detail_body(task)
            detail["dataIds"] = ["DATA-999"]
            detail["decisionIds"] = ["D-999"]
            invalid_path = root / "invalid-detail.json"
            invalid_path.write_text(json.dumps(detail), encoding="utf-8")
            invalid = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(invalid_path),
            )
            self.assertNotEqual(invalid.returncode, 0)
            invalid_payload = json.loads(invalid.stdout)
            reasons = {item["reason"] for item in invalid_payload["errors"]}
            self.assertIn("unknown_plan_json_data_ref", reasons)
            self.assertIn("unknown_plan_json_decision_ref", reasons)

            detail["dataIds"] = []
            detail["decisionIds"] = []
            valid_path = root / "valid-detail.json"
            valid_path.write_text(json.dumps(detail), encoding="utf-8")
            valid = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(valid_path),
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

    def test_set_draft_task_detail_rejects_malformed_design_ref_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            detail = _draft_detail_body(task)
            detail["designRefs"] = ["design.md - API-001"]  # malformed: missing '#'
            detail_path = root / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")

            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid_artifact_ref_format", result.stdout)
            # Draft batch must not have been written with the bad task.
            draft_batch_path = (
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            )
            draft_task = json.loads(draft_batch_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(draft_task["goal"], "")

    def test_set_draft_task_detail_rejects_dangling_design_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            detail = _draft_detail_body(task)
            detail["designRefs"] = ["design.md#API-999"]  # anchor not present in design.md
            detail_path = root / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")

            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing_ref_anchor", result.stdout)

    def test_set_draft_task_detail_allows_shared_test_selector_as_intent(self) -> None:
        """Test-stage ownership is deferred, so Plan may preserve shared selectors as intent."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)

            module = root / "code" / "backend" / "service"
            module.mkdir(parents=True)
            (module / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main"], cwd=root / "code", check=True, capture_output=True)

            first = _plan_task_body("T001")
            first["validationCommands"] = [{
                "id": "VAL-T001-01",
                "argv": ["mvn", "test", "-Dtest=DuplicateTest"],
                "cwd": "backend/service",
                "kind": "behavior_test",
                "required": True,
                "covers": ["AC-T001-01"],
            }]
            second = _plan_task_body("T002", scenario="SCN-002")
            second["deps"] = ["T001"]
            second["validationCommands"] = [{
                "id": "VAL-T002-01",
                "argv": ["mvn", "test", "-Dtest=DuplicateTest"],
                "cwd": "backend/service",
                "kind": "behavior_test",
                "required": True,
                "covers": ["AC-T002-01"],
            }]
            group_file = _write_task_groups(root / "task-groups.json", [first, second])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(module),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            first_detail_path = root / "T001-detail.json"
            first_detail_path.write_text(json.dumps(_draft_detail_body(first)), encoding="utf-8")
            first_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(first_detail_path),
            )
            self.assertEqual(first_result.returncode, 0, first_result.stdout + first_result.stderr)

            second_detail_path = root / "T002-detail.json"
            second_detail_path.write_text(json.dumps(_draft_detail_body(second)), encoding="utf-8")
            second_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T002", "--body-file", str(second_detail_path),
            )

            self.assertEqual(second_result.returncode, 0, second_result.stdout + second_result.stderr)

            draft_batch_path = (
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            )
            draft_tasks = json.loads(draft_batch_path.read_text(encoding="utf-8"))["tasks"]
            for draft_task in draft_tasks:
                self.assertEqual(draft_task["validationCommands"][0]["argv"][-1], "-Dtest=DuplicateTest")
                self.assertEqual(len(draft_task["validationTestPlan"]), 1)
                self.assertNotIn("targets", draft_task["validationTestPlan"][0])
                self.assertIn("testIntent", draft_task["validationTestPlan"][0])

    def test_set_draft_task_detail_accepts_valid_refs_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            detail_path = root / "detail.json"
            detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["draft"]["readyTaskIds"], ["T001"])

    def test_preflight_reports_task_and_single_task_repair_updates_only_that_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            detail_path = root / "detail.json"
            detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
            set_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertEqual(set_result.returncode, 0, set_result.stdout + set_result.stderr)

            design_path = feature_dir / "design.md"
            design_path.write_text(
                design_path.read_text(encoding="utf-8").replace("D-001", "D-002"),
                encoding="utf-8",
            )
            preflight = _run(
                "plan_writer.py", "preflight-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertNotEqual(preflight.returncode, 0)
            report = json.loads(preflight.stdout)["validation"]
            self.assertEqual(report["invalidTaskIds"], [])
            issue = next(
                item for item in report["issues"]
                if item["reason"] == "confirmed_design_changed_after_draft_created"
            )
            self.assertEqual(issue["repairTarget"], "design_revision")
            self.assertFalse(issue["designMutationAllowed"])

    def test_batch_repair_is_atomic_when_one_task_patch_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)
            first = _plan_task_body("T001")
            second = _plan_task_body("T002", scenario="SCN-002")
            second["deps"] = ["T001"]
            group_file = _write_task_groups(root / "task-groups.json", [first, second])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            for task in (first, second):
                detail_path = root / f"{task['id']}.json"
                detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
                result = _run(
                    "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                    "--feature", "alpha", "--task-id", task["id"], "--body-file", str(detail_path),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            repairs = {
                "repairs": [
                    {"taskId": "T001", "patch": {"goal": "repaired first goal"}},
                    {"taskId": "T002", "patch": {"designRefs": ["design.md#D-999"]}},
                ]
            }
            repair_path = root / "repairs.json"
            repair_path.write_text(json.dumps(repairs), encoding="utf-8")
            repaired = _run(
                "plan_writer.py", "repair-draft-tasks", "--workspace", str(workspace),
                "--feature", "alpha", "--body-file", str(repair_path),
            )
            self.assertNotEqual(repaired.returncode, 0)
            payload = json.loads(repaired.stdout)
            self.assertEqual(payload["validation"]["invalidTaskIds"], ["T002"])

            draft_batch_path = (
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            )
            tasks = json.loads(draft_batch_path.read_text(encoding="utf-8"))["tasks"]
            first_after = next(item for item in tasks if item["id"] == "T001")
            self.assertEqual(first_after["goal"], first["goal"])

    def test_batch_repair_commits_multiple_tasks_in_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)
            first = _plan_task_body("T001")
            second = _plan_task_body("T002", scenario="SCN-002")
            second["deps"] = ["T001"]
            group_file = _write_task_groups(root / "task-groups.json", [first, second])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            for task in (first, second):
                detail_path = root / f"{task['id']}.json"
                detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
                result = _run(
                    "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                    "--feature", "alpha", "--task-id", task["id"], "--body-file", str(detail_path),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            repairs = {
                "repairs": [
                    {"taskId": "T001", "patch": {"goal": "repaired first goal"}},
                    {"taskId": "T002", "patch": {"goal": "repaired second goal"}},
                ]
            }
            repaired = _run(
                "plan_writer.py", "repair-draft-tasks", "--workspace", str(workspace),
                "--feature", "alpha", "--body-json", json.dumps(repairs),
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
            payload = json.loads(repaired.stdout)
            self.assertEqual(payload["repairedTaskIds"], ["T001", "T002"])
            self.assertTrue(payload["repairComplete"])
            shown = _run(
                "plan_writer.py", "show-task-draft", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)

    def test_task_repair_rejects_group_owned_field_with_task_group_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(root / "task-groups.json", [task])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            detail_path = root / "detail.json"
            detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
            set_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertEqual(set_result.returncode, 0, set_result.stdout + set_result.stderr)
            repaired = _run(
                "plan_writer.py", "repair-draft-task", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-json",
                json.dumps({"specRefs": ["specs/cap/spec.md#REQ-001"]}),
            )
            self.assertNotEqual(repaired.returncode, 0)
            issue = json.loads(repaired.stdout)["validation"]["issues"][0]
            self.assertEqual(issue["taskIds"], ["T001"])
            self.assertEqual(issue["repairTarget"], "task_group")

    def test_finalized_plan_repair_recomputes_root_and_task_contract_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, _ = self._finalize_single_task(root)
            design_path = feature_dir / "design.md"
            design_path.write_text(
                design_path.read_text(encoding="utf-8").replace("D-001", "D-002"),
                encoding="utf-8",
            )

            diagnosis = _run(
                "plan_writer.py", "diagnose-plan-repair", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(diagnosis.returncode, 0, diagnosis.stdout + diagnosis.stderr)
            diagnosis_payload = json.loads(diagnosis.stdout)["diagnosis"]
            self.assertEqual(diagnosis_payload["recommendedCommand"], "design_revision_required")
            self.assertFalse(diagnosis_payload["executionStarted"])

            reopened = _run(
                "plan_writer.py", "reopen-finalized-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--reason", "update design decision reference",
            )
            self.assertNotEqual(reopened.returncode, 0)
            self.assertIn("design_revision", reopened.stdout)

    def test_corrupt_formal_bundle_does_not_block_draft_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, task = self._finalize_single_task(root)
            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            formal_batch = json.loads(batch_path.read_text(encoding="utf-8"))
            formal_batch["tasks"][0]["goal"] = "edited outside writer"
            batch_path.write_text(json.dumps(formal_batch), encoding="utf-8")

            strict_show = _run(
                "plan_writer.py", "show", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertNotEqual(strict_show.returncode, 0)
            self.assertIn("task_set_digest_mismatch", strict_show.stdout)
            diagnosis = _run(
                "plan_writer.py", "diagnose-plan-repair", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(diagnosis.returncode, 0, diagnosis.stdout + diagnosis.stderr)
            self.assertEqual(
                json.loads(diagnosis.stdout)["diagnosis"]["artifactState"],
                "finalized_corrupt",
            )
            reopened = _run(
                "plan_writer.py", "reopen-finalized-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--reason", "restore writer-owned formal bundle",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
            restored = _run(
                "plan_writer.py", "finalize-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--force",
            )
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            restored_batch = json.loads(batch_path.read_text(encoding="utf-8"))
            self.assertEqual(restored_batch["tasks"][0]["goal"], task["goal"])
            strict_show = _run(
                "plan_writer.py", "show", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(strict_show.returncode, 0, strict_show.stdout + strict_show.stderr)

    def test_reopen_finalized_draft_rejects_started_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, _ = self._finalize_single_task(root)
            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            formal_batch = json.loads(batch_path.read_text(encoding="utf-8"))
            formal_batch["status"] = "in_progress"
            formal_batch["tasks"][0]["status"] = "in_progress"
            batch_path.write_text(json.dumps(formal_batch), encoding="utf-8")

            diagnosis = _run(
                "plan_writer.py", "diagnose-plan-repair", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(diagnosis.returncode, 0, diagnosis.stdout + diagnosis.stderr)
            payload = json.loads(diagnosis.stdout)["diagnosis"]
            self.assertTrue(payload["executionStarted"])
            self.assertEqual(payload["recommendedCommand"], "plan_revision_required")
            reopened = _run(
                "plan_writer.py", "reopen-finalized-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--reason", "must be rejected",
            )
            self.assertNotEqual(reopened.returncode, 0)
            self.assertIn("finalized_plan_reopen_forbidden", reopened.stdout)

    def test_reopened_draft_repair_freezes_if_execution_starts_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir, _ = self._finalize_single_task(root)
            reopened = _run(
                "plan_writer.py", "reopen-finalized-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--reason", "prepare a local correction",
            )
            self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)

            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            formal_batch = json.loads(batch_path.read_text(encoding="utf-8"))
            formal_batch["status"] = "in_progress"
            formal_batch["tasks"][0]["status"] = "in_progress"
            batch_path.write_text(json.dumps(formal_batch), encoding="utf-8")

            repaired = _run(
                "plan_writer.py", "repair-draft-task", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-json",
                json.dumps({"goal": "must not be persisted"}),
            )
            self.assertNotEqual(repaired.returncode, 0)
            self.assertIn("plan_execution_workspace_frozen", repaired.stdout)
            self.assertIn("repairTarget", repaired.stdout)

            draft_batch_path = (
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            )
            draft_task = json.loads(draft_batch_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertNotEqual(draft_task["goal"], "must not be persisted")


if __name__ == "__main__":
    unittest.main()
