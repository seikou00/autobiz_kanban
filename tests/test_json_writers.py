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
from hooks.plan_json import (  # noqa: E402
    BATCH_STRATEGY,
    BATCH_VALIDATION_KINDS,
    MAX_BATCH_TASKS,
    TASK_VALIDATION_KINDS,
    task_set_digest,
)
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
    root = {
                "featureId": "alpha",
                "status": "todo",
                "taskSetStatus": "finalized",
                "activeBatchId": "B001",
                "nextBatchId": None,
                "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
                "batches": [{
                    "id": "B001", "path": "plans/B001/plan.json", "title": "cap",
                    "specRoots": ["specs/cap/spec.md"], "executionLane": "backend",
                    "deps": [], "taskIds": ["T001"], "status": "todo",
                }],
                "batchValidationProfiles": {
                    "backend": {
                        "commands": [
                            {
                                "argv": ["echo", "compile"],
                                "cwd": ".",
                                "kind": "compile",
                                "required": True,
                            }
                        ]
                    }
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
    batch = {
        "featureId": "alpha",
        "batchId": "B001",
        "title": "cap",
        "executionLane": "backend",
        "status": "todo",
        "taskCount": 1,
        "completedTaskCount": 0,
        "completionEvidenceIds": [],
        "batchValidation": {
            "profile": "backend",
            "status": "pending",
            "commands": [
                {
                    "id": "BATCH-B001-VAL-001",
                    "argv": ["echo", "compile"],
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
        "tasks": [task],
    }
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


def _plan_task_body() -> dict:
    return {
        "id": "T001",
        "title": "do",
        "goal": "deliver behavior",
        "deps": [],
        "uiRequired": False,
        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
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
        "designRefs": ["design.md#D-001"],
        "apiIds": [],
        "dataIds": [],
        "decisionIds": ["D-001"],
        "validationCommands": [
            {
                "id": "VAL-T001-01",
                "argv": ["echo", "ok"],
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "covers": ["AC-T001-01"],
            }
        ],
        "expectedFiles": [],
    }


def _write_task_groups(path: Path, tasks: list[dict]) -> Path:
    groups = []
    for task in tasks:
        group = {
            "id": task["id"],
            "title": task["title"],
            "deps": list(task.get("deps", [])),
            "uiRequired": task.get("uiRequired") is True,
            "specRefs": list(task.get("specRefs", [])),
            "mergedScenarioRefs": list(task.get("mergedScenarioRefs", [])),
            "apiIds": list(task.get("apiIds", [])),
            "validationBoundary": "public behavior seam validated by one executable command",
        }
        if task.get("splitRationale"):
            group["splitRationale"] = task["splitRationale"]
        if task.get("uiRequired") is True:
            ui_refs = task.get("uiRefs", {})
            group["uiRefs"] = {
                "pageRefs": list(ui_refs.get("pageRefs", [])),
                "interactionRefs": list(ui_refs.get("interactionRefs", [])),
            }
        groups.append(group)
    path.write_text(
        json.dumps({"featureId": "alpha", "groups": groups}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


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
    def test_plan_writer_rejects_direct_batch_contract_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "materialize-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            ).returncode, 0)

            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["tasks"][0]["title"] = "edited outside writer"
            batch_path.write_text(json.dumps(batch), encoding="utf-8")

            result = _run(
                "plan_writer.py", "show", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_set_digest_mismatch", result.stdout + result.stderr)

    def test_plan_writer_replaces_and_removes_collecting_tasks_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir, second=True)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            first = _plan_task_body()
            second = _plan_task_body()
            second.update({"id": "T002", "title": "second", "deps": ["T001"]})
            second["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            second["acceptanceCriteria"][0].update({
                "id": "AC-T002-01",
                "scenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })
            second["validationCommands"][0].update({"id": "VAL-T002-01", "covers": ["AC-T002-01"]})
            for task in (first, second):
                path = task_dir / f"{task['id']}.json"
                path.write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [first, second])

            self.assertEqual(_run(
                "plan_writer.py", "materialize-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            ).returncode, 0)
            finalized_replace = _run(
                "plan_writer.py", "replace-task", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--body-file", str(task_dir / "T001.json"),
            )
            self.assertNotEqual(finalized_replace.returncode, 0)
            self.assertIn("plan_task_set_finalized", finalized_replace.stdout + finalized_replace.stderr)

            collecting_workspace, collecting_feature = _workspace(Path(tmp) / "collecting")
            _write_specs(collecting_feature, second=True)
            self.assertEqual(_run(
                "plan_writer.py", "init", "--workspace", str(collecting_workspace), "--feature", "alpha",
            ).returncode, 0)
            for task in (first, second):
                self.assertEqual(_run(
                    "plan_writer.py", "add-task", "--workspace", str(collecting_workspace), "--feature", "alpha",
                    "--body-file", str(task_dir / f"{task['id']}.json"),
                ).returncode, 0)
            blocked = _run(
                "plan_writer.py", "remove-task", "--workspace", str(collecting_workspace), "--feature", "alpha",
                "--task-id", "T001",
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("task_has_dependents", blocked.stdout + blocked.stderr)

            replacement = dict(second)
            replacement["deps"] = []
            replacement_path = task_dir / "T002-replacement.json"
            replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
            self.assertEqual(_run(
                "plan_writer.py", "replace-task", "--workspace", str(collecting_workspace), "--feature", "alpha",
                "--task-id", "T002", "--body-file", str(replacement_path),
            ).returncode, 0)
            self.assertEqual(_run(
                "plan_writer.py", "remove-task", "--workspace", str(collecting_workspace), "--feature", "alpha",
                "--task-id", "T001",
            ).returncode, 0)
            self.assertEqual(_read_plan_tasks(collecting_feature)[0]["id"], "T002")

    def test_plan_writer_preflights_all_lanes_before_materializing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir, second=True)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            backend = _plan_task_body()
            frontend = _plan_task_body()
            frontend.update({"id": "T002", "title": "frontend", "uiRequired": True, "deps": ["T001"]})
            frontend["scope"] = {
                "modules": ["ui"], "entrypoints": ["route"], "pages": ["PAGE-001"],
                "dataObjects": [], "paths": [],
            }
            frontend["uiRefs"] = {
                "pageRefs": ["PAGE-001"], "interactionRefs": ["UIX-001"],
                "visualSourceRefs": [], "frontendRoute": "spec-driven-ui",
            }
            frontend["nonGoals"] = ["no unrelated UI changes"]
            frontend["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            frontend["acceptanceCriteria"][0].update({
                "id": "AC-T002-01", "scenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })
            frontend["validationCommands"][0].update({"id": "VAL-T002-01", "covers": ["AC-T002-01"]})
            for task in (backend, frontend):
                (task_dir / f"{task['id']}.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [backend, frontend])

            grouping_preflight = _run(
                "plan_writer.py", "preflight-task-groups", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )
            self.assertEqual(
                grouping_preflight.returncode,
                0,
                grouping_preflight.stdout + grouping_preflight.stderr,
            )

            preflight = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )
            self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
            self.assertFalse((feature_dir / "plan.json").exists())
            lanes = [item["executionLane"] for item in json.loads(preflight.stdout)["preflight"]["batches"]]
            self.assertEqual(lanes, ["backend", "frontend"])

            materialized = _run(
                "plan_writer.py", "materialize-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )
            self.assertEqual(materialized.returncode, 0, materialized.stdout + materialized.stderr)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["taskSetStatus"], "finalized")
            self.assertEqual([item["executionLane"] for item in root["batches"]], ["backend", "frontend"])

    def test_plan_writer_reports_required_split_before_task_content_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            task["specRefs"] = [
                "specs/cap/spec.md#REQ-001",
                *[f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 14)],
            ]
            del task["goal"]
            task["implementationPoints"] = [f"point {index}" for index in range(1, 8)]
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])

            result = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("oversized_plan_task_must_split", result.stdout)
            self.assertNotIn("invalid_plan_task_body", result.stdout)
            self.assertNotIn("implementationPoints_too_many", result.stdout)

    def test_plan_writer_rejects_task_changes_after_grouping_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            task["title"] = "changed after grouping"
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")

            result = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_group_contract_mismatch", result.stdout)
            self.assertIn("fields=title", result.stdout)

    def test_plan_writer_preflight_returns_missing_coverage_to_matrix_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir, second=True)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])

            result = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing_plan_scenario_coverage", result.stdout + result.stderr)
            self.assertIn("return_to_scenario_matrix", result.stdout + result.stderr)
            self.assertFalse((feature_dir / "plan.json").exists())
            self.assertFalse((feature_dir / "plans").exists())

    def test_plan_writer_revalidates_granularity_after_spec_ref_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            scenario_lines = [f"#### Scenario [SCN-{index:03d}]: path {index}" for index in range(1, 14)]
            (spec_dir / "spec.md").write_text(
                "\n".join(["## ADDED Requirements", "### Requirement [REQ-001]: capability", *scenario_lines]),
                encoding="utf-8",
            )
            body = _plan_task_body()
            first_refs = [f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 6)]
            body["specRefs"] = ["specs/cap/spec.md#REQ-001", *first_refs]
            body["acceptanceCriteria"][0]["scenarioRefs"] = first_refs
            body_file = Path(tmp) / "T001.json"
            body_file.write_text(json.dumps(body), encoding="utf-8")

            self.assertEqual(
                _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha").returncode,
                0,
            )
            self.assertEqual(
                _run(
                    "plan_writer.py", "add-task", "--workspace", str(workspace), "--feature", "alpha",
                    "--body-file", str(body_file),
                ).returncode,
                0,
            )

            extra_refs = [f"specs/cap/spec.md#SCN-{index:03d}" for index in range(6, 14)]
            mutated = _run(
                "plan_writer.py", "add-spec-ref", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", *extra_refs,
            )

            self.assertNotEqual(mutated.returncode, 0)
            self.assertIn("oversized_plan_task_must_split", mutated.stdout + mutated.stderr)
            batch = json.loads((feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(batch["tasks"][0]["specRefs"], ["specs/cap/spec.md#REQ-001", *first_refs])

    def test_downstream_plan_artifacts_require_finalized_task_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            body_file = Path(tmp) / "T001.json"
            body_file.write_text(json.dumps(_plan_task_body()), encoding="utf-8")

            self.assertEqual(
                _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha").returncode,
                0,
            )
            self.assertEqual(
                _run(
                    "plan_writer.py", "add-task", "--workspace", str(workspace), "--feature", "alpha",
                    "--body-file", str(body_file),
                ).returncode,
                0,
            )

            project = _run(
                "plan_writer.py", "add-project-validation-command", "--workspace", str(workspace),
                "--feature", "alpha", "--command", "echo compile",
            )
            rendered = _run(
                "plan_writer.py", "render-md", "--workspace", str(workspace), "--feature", "alpha"
            )
            smoke = _run(
                "smoke_plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha",
                "--skip-reason", "no smoke needed",
            )

            for result in (project, rendered, smoke):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("plan_task_set_not_finalized", result.stdout + result.stderr)

            finalized = _run(
                "plan_writer.py", "finalize-task-set", "--workspace", str(workspace), "--feature", "alpha"
            )
            self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
            self.assertEqual(
                _run(
                    "plan_writer.py", "add-project-validation-command", "--workspace", str(workspace),
                    "--feature", "alpha", "--command", "echo compile",
                ).returncode,
                0,
            )
            self.assertEqual(
                _run("plan_writer.py", "render-md", "--workspace", str(workspace), "--feature", "alpha").returncode,
                0,
            )
            self.assertEqual(
                _run(
                    "smoke_plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha",
                    "--skip-reason", "no smoke needed",
                ).returncode,
                0,
            )

    def test_plan_writer_add_task_contract_is_machine_readable_without_workspace(self) -> None:
        env = os.environ.copy()
        env.pop("PLUGIN_WORKSPACE", None)
        env.pop("PROJECT_DIR", None)
        env.pop("FEATURE_ID", None)

        result = _run("plan_writer.py", "add-task-contract", env=env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        contract = payload["contract"]
        self.assertEqual(contract["taskTemplate"], "skills/autodev/autodev-plan/templates/task-input.json")
        self.assertEqual(contract["taskInputExample"]["id"], "T001")
        self.assertIn("validationCommands", contract["taskInputExample"])
        self.assertIn("matrixExceptionExample", contract["taskInputExample"])
        self.assertEqual(contract["exampleOnlyTaskFields"], ["matrixExceptionExample"])
        self.assertNotIn("status", contract["taskInputExample"])
        self.assertEqual(contract["recommendedInputMode"], "task-directory")
        self.assertEqual(
            contract["taskGroupTemplate"],
            "skills/autodev/autodev-plan/templates/task-groups.json",
        )
        self.assertIn("groups", contract["taskGroupInputExample"])
        group_exception = contract["taskGroupMatrixExceptionExample"]
        self.assertEqual(group_exception["mergedScenarioRefs"], group_exception["specRefs"][1:])
        self.assertIn("splitRationale", group_exception)
        self.assertIn("validationBoundary", group_exception)
        self.assertEqual(contract["validationKinds"], sorted(TASK_VALIDATION_KINDS))
        self.assertEqual(contract["batchValidationKinds"], sorted(BATCH_VALIDATION_KINDS))
        self.assertEqual(
            contract["batchValidationCommand"],
            {
                "command": "add-batch-validation-command --lane <backend|frontend> --command <command>",
                "requiredFields": ["argv", "cwd", "kind", "required"],
                "requiredPerUsedLane": True,
            },
        )
        self.assertEqual(contract["batchAssignment"]["strategy"], BATCH_STRATEGY)
        self.assertEqual(contract["batchAssignment"]["maxTasks"], MAX_BATCH_TASKS)
        self.assertEqual(contract["batchAssignment"]["primaryCapabilitySource"], "first_spec_ref_file")
        self.assertIn("executionLaneSource", contract["batchAssignment"])
        self.assertIn("executionLaneMapping", contract["batchAssignment"])
        self.assertIn("executionLaneOrder", contract["batchAssignment"])
        self.assertEqual(contract["batchAssignment"]["executionLaneSource"], "uiRequired")
        self.assertEqual(
            contract["batchAssignment"]["executionLaneMapping"],
            {"uiRequired_false": "backend", "uiRequired_true": "frontend"},
        )
        self.assertEqual(contract["batchAssignment"]["executionLaneOrder"], ["backend", "frontend"])
        self.assertEqual(
            contract["batchAssignment"]["appendRule"],
            "same_primary_capability_and_execution_lane_as_immediately_preceding_batch_and_not_full",
        )
        self.assertFalse(contract["batchAssignment"]["manualBatchIdSupported"])
        self.assertIn("taskSetFinalization", contract)
        self.assertEqual(
            contract["taskSetFinalization"],
            {
                "groupingPreflightCommand": "preflight-task-groups --group-file <file>",
                "command": "materialize-task-set --group-file <file> --task-dir <directory>",
                "preflightCommand": "preflight-task-set --group-file <file> --task-dir <directory>",
                "coverage": "all_path_qualified_spec_scenarios",
                "requiredBefore": [
                    "add-batch-validation-command",
                    "add-project-validation-command",
                    "render-md",
                    "smoke_plan_writer.init",
                ],
            },
        )
        self.assertTrue(contract["collectingRepairs"]["atomic"])
        self.assertEqual(contract["formalArtifacts"]["integrityField"], "taskSetDigest")
        self.assertFalse(contract["formalArtifacts"]["directEditingSupported"])
        self.assertIn("--batch-id", contract["forbiddenArguments"])
        self.assertIn("validationCommands", contract["requiredTaskFields"])
        self.assertEqual(contract["validationCoverage"]["rule"], "required_commands_cover_all_acceptance_criteria")
        self.assertEqual(
            contract["conditionalFields"]["uiRefs"],
            {
                "when": "uiRequired_is_true",
                "requiredFields": ["pageRefs", "interactionRefs", "visualSourceRefs", "frontendRoute"],
            },
        )
        self.assertEqual(
            contract["conditionalFields"]["mergedScenarioRefs"],
            {
                "when": "scenario_refs_count_is_6_to_12",
                "requiredFields": [],
                "mustEqual": "fully_qualified_scenario_refs_from_specRefs",
            },
        )
        self.assertEqual(
            contract["matrixException"],
            {
                "normalScenarioMaximum": 5,
                "scenarioMaximum": 12,
                "requiredValidation": "one_complete_required_non_compile_behavior_command",
            },
        )
        self.assertEqual(len(contract["matrixExceptionExample"]["mergedScenarioRefs"]), 6)
        self.assertEqual(
            contract["matrixExceptionExample"]["mergedScenarioRefs"],
            contract["matrixExceptionExample"]["specRefs"][1:],
        )
        self.assertEqual(
            contract["matrixExceptionExample"]["validationCommands"][0]["covers"],
            ["AC-T001-01"],
        )
        self.assertEqual(
            contract["projectValidationCommand"],
            {"requiredFields": ["id", "argv", "cwd", "kind", "required"]},
        )
        self.assertEqual(
            contract["writerOwnedGeneratedArtifacts"],
            {
                "rootPlan": "plan.json",
                "batchPlans": "plans/Bxxx/plan.json",
            },
        )

    def test_plan_writer_task_template_supports_chinese_body_file_and_creates_first_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            feature = "代发准入测试"
            feature_dir = workspace / ".autobizdevops" / "features" / feature
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {feature: {**_state_record(), "feature": feature}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            task = json.loads(
                (ROOT / "skills/autodev/autodev-plan/templates/task-input.json").read_text(encoding="utf-8")
            )
            for writer_owned_field in (
                "status",
                "evidenceIds",
                "completionEvidenceIds",
                "latestPassEvidenceId",
                "completionPolicy",
            ):
                self.assertNotIn(writer_owned_field, task)
            task.update(
                {
                    "id": "T001",
                    "title": "代发协议管控菜单与路由配置",
                    "goal": "用户可以进入代发协议管控页面",
                    "scope": {
                        "modules": ["bcpccomplianceui"],
                        "entrypoints": ["/protocol-control-apply-report"],
                        "pages": [],
                        "dataObjects": [],
                        "paths": [],
                    },
                    "implementationPoints": ["注册页面路由", "验证菜单入口可达"],
                    "acceptanceCriteria": [
                        {
                            "id": "AC-T001-01",
                            "text": "目标用户可以进入代发协议管控页面",
                            "scenarioRefs": ["specs/protocol-control-menu/spec.md#SCN-001"],
                        }
                    ],
                    "nonGoals": ["不实现申请提交"],
                    "specRefs": [
                        "specs/protocol-control-menu/spec.md#REQ-001",
                        "specs/protocol-control-menu/spec.md#SCN-001",
                    ],
                    "designRefs": ["design.md#D-001"],
                    "decisionIds": ["D-001"],
                    "validationCommands": [
                        {
                            "id": "VAL-T001-01",
                            "argv": ["npm", "test", "--", "protocol-control"],
                            "cwd": "bcpccomplianceui",
                            "kind": "behavior_test",
                            "required": True,
                            "covers": ["AC-T001-01"],
                        }
                    ],
                }
            )
            body_file = feature_dir / ".tmp" / "plan_writer" / "tasks" / "T001.json"
            body_file.parent.mkdir(parents=True)
            body_file.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

            initialized = _run(
                "plan_writer.py", "init", "--workspace", str(workspace), "--feature", feature
            )
            added = _run(
                "plan_writer.py", "add-task", "--workspace", str(workspace), "--feature", feature,
                "--body-file", str(body_file),
            )

            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            root_plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch_plan = json.loads((feature_dir / "plans/B001/plan.json").read_text(encoding="utf-8"))
            self.assertNotIn("tasks", root_plan)
            self.assertEqual(root_plan["activeBatchId"], "B001")
            self.assertEqual(root_plan["batches"][0]["taskIds"], ["T001"])
            self.assertEqual(batch_plan["tasks"][0]["title"], "代发协议管控菜单与路由配置")

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

            plan_path = workspace / ".autobizdevops" / "features" / "alpha" / "plan.json"
            finalized_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            finalized_plan["taskSetStatus"] = "finalized"
            plan_path.write_text(json.dumps(finalized_plan), encoding="utf-8")

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
                    *[f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 14)],
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

    def test_plan_writer_accepts_matrix_exception_body_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            scenario_refs = [f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 10)]
            acceptance_ids = [f"AC-T001-{index:02d}" for index in range(1, 10)]
            payload = {
                "id": "T001",
                "title": "matrix query task",
                "goal": "deliver one query response matrix",
                "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                "implementationPoints": ["update query", "cover response matrix"],
                "acceptanceCriteria": [
                    {"id": acceptance_id, "text": "matrix value is observable", "scenarioRefs": [scenario_ref]}
                    for acceptance_id, scenario_ref in zip(acceptance_ids, scenario_refs)
                ],
                "nonGoals": ["do not add another query seam"],
                "specRefs": ["specs/cap/spec.md#REQ-001", *scenario_refs],
                "mergedScenarioRefs": scenario_refs,
                "designRefs": ["design.md#D-001"],
                "apiIds": ["API-001"],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [
                    {
                        "id": "VAL-T001-01",
                        "argv": ["echo", "ok"],
                        "cwd": ".",
                        "kind": "integration_test",
                        "required": True,
                        "covers": acceptance_ids,
                    }
                ],
                "splitRationale": (
                    "SCN-001、SCN-004、SCN-009 由同一查询请求返回字段矩阵，"
                    "并由同一个响应断言验证，拆开会复制同一验证闭环。"
                ),
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
            self.assertEqual(_read_plan_tasks(feature_dir)[0]["mergedScenarioRefs"], scenario_refs)

    def test_plan_writer_rejects_scenario_range_or_concatenation_shorthand_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for index, anchor in enumerate(("SCN-001~SCN-009", "SCN-001, SCN-002", "SCN-001SCN-006", "SCN-001到SCN-009"), start=1):
                workspace, feature_dir = _workspace(Path(tmp) / str(index))
                payload = {
                    "id": "T001",
                    "title": "range shorthand task",
                    "goal": "reject ambiguous scenario coverage",
                    "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                    "implementationPoints": ["validate references", "reject shorthand"],
                    "acceptanceCriteria": ["reference validation is observable"],
                    "nonGoals": [],
                    "specRefs": ["specs/cap/spec.md#REQ-001", f"specs/cap/spec.md#{anchor}"],
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
                self.assertNotEqual(body.returncode, 0, anchor)
                self.assertIn("invalid_plan_task_scenario_reference", body.stdout, anchor)
                self.assertEqual(json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))["batches"], [], anchor)

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
                "mergedScenarioRefs": [
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

    def test_plan_writer_add_task_cli_rejects_matrix_exception_without_structured_refs(self) -> None:
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
            self.assertNotEqual(body.returncode, 0)
            self.assertIn("missing_plan_task_merged_scenario_refs", body.stdout)
            self.assertEqual(json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))["batches"], [])

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
                "mergedScenarioRefs": [
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
                "mergedScenarioRefs": [
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
