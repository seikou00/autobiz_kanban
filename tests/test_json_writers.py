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

from hooks.json_writer_common import parse_postcheck_output, shell_join  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    BATCH_STRATEGY,
    MAX_BATCH_TASKS,
    TASK_VALIDATION_KINDS,
    task_set_digest,
)
from hooks.parallel_validation_ownership import build_pipeline_contract  # noqa: E402
from hooks.plan_writer import _annotate_validation_test_plan  # noqa: E402
from hooks.stage_gate import validate_stage  # noqa: E402
from skills.autodev.hooks.artifact_check import run_postcheck  # noqa: E402


TEST_TASK_COMMAND = f'{sys.executable} -c "print(\'task validation\')"'
TEST_PROJECT_COMMAND = f'{sys.executable} -c "print(\'project validation\')"'


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
        "validationBoundary": "public behavior seam validated by the task command",
        "nonGoals": ["do not change unrelated behavior"],
        "specRefs": spec_refs,
        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
        "apiIds": ["API-001"],
        "dataIds": ["DATA-001"],
        "decisionIds": ["D-001"],
        "completionPolicy": "all_required_validations_pass",
        "validationCommands": [{"id": "VAL-T001-01", "argv": [sys.executable, "-m", "unittest", "test_task_behavior"], "cwd": ".", "kind": "behavior_test", "required": True, "covers": ["AC-T001-01"]}],
        "validationTestPlan": [
            {
                "commandId": "VAL-T001-01",
                "assetType": "unit_test",
                "executionStage": "post_batch",
                "covers": ["AC-T001-01"],
                "testIntent": {
                    "behavior": "behavior is observable",
                    "acceptanceCriteria": [
                        {
                            "id": "AC-T001-01",
                            "text": "behavior is observable",
                            "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        }
                    ],
                },
            }
        ],
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
                    "codeGate": "batch_compile_only",
                    "maxTestStageRepairAttempts": 3,
                },
                "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
                "batches": [{
                    "id": "B001", "path": "plans/B001/plan.json", "title": "cap",
                    "specRoots": ["specs/cap/spec.md"], "executionLane": "backend",
                    "deps": [], "taskIds": ["T001"], "status": "todo",
                }],
                "compileProfiles": {
                    "backend": {
                        "commands": [
                            {
                                "argv": [sys.executable, "-c", "print('backend compile')"],
                                "cwd": ".",
                                "kind": "compile",
                                "required": True,
                            }
                        ]
                    }
                },
                "qualityGateProfiles": {},
                "projectValidationCommands": [
                    {
                        "id": "PROJECT-VAL-001",
                        "argv": [sys.executable, "-c", "print('project validation')"],
                        "cwd": ".",
                        "kind": "integration_test",
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
        "compileCommand": {
            "id": "BATCH-B001-COMPILE",
            "argv": [sys.executable, "-c", "print('backend compile')"],
            "cwd": ".",
            "kind": "compile",
            "required": True,
        },
        "qualityGateCommands": [],
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
    batch = json.loads((feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8"))
    return batch["tasks"]


def _write_plan_tasks(feature_dir: Path, tasks: list[dict]) -> None:
    path = feature_dir / "plans" / "B001" / "plan.json"
    batch = json.loads(path.read_text(encoding="utf-8"))
    batch["tasks"] = tasks
    batch["taskCount"] = len(tasks)
    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _plan_task_body() -> dict:
    return {
        "id": "T001",
        "title": "do",
        "goal": "deliver behavior",
        "deps": [],
        "uiRequired": False,
        "workspaceRef": "default",
        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
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
        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
        "apiIds": ["API-001"],
        "dataIds": ["DATA-001"],
        "decisionIds": ["D-001"],
        "validationCommands": [
            {
                "id": "VAL-T001-01",
                "argv": [sys.executable, "-c", "print('task validation')"],
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "covers": ["AC-T001-01"],
            }
        ],
        "expectedFiles": [],
    }


def _draft_detail_body(task: dict | None = None) -> dict:
    source = task or _plan_task_body()
    scope = dict(source["scope"])
    scope.pop("pages", None)
    criteria = [
        {"text": item["text"], "scenarioRefs": list(item["scenarioRefs"])}
        for item in source["acceptanceCriteria"]
    ]
    commands = []
    for item in source["validationCommands"]:
        command = {key: value for key, value in item.items() if key not in {"id", "covers"}}
        command["covers"] = list(range(1, len(criteria) + 1))
        commands.append(command)
    return {
        "goal": source["goal"],
        "scope": scope,
        "implementationPoints": list(source["implementationPoints"]),
        "acceptanceCriteria": criteria,
        "nonGoals": list(source["nonGoals"]),
        "designRefs": list(source["designRefs"]),
        "dataIds": list(source["dataIds"]),
        "decisionIds": list(source["decisionIds"]),
        "validationCommands": commands,
        "expectedFiles": list(source.get("expectedFiles", [])),
        "blockers": list(source.get("blockers", [])),
    }


def _write_task_groups(path: Path, tasks: list[dict]) -> Path:
    groups = []
    for task in tasks:
        group = {
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
        }
        if isinstance(task.get("externalDependency"), dict):
            group["externalDependency"] = dict(task["externalDependency"])
        if task.get("splitRationale"):
            group["splitRationale"] = task["splitRationale"]
        if task.get("uiRequired") is True:
            ui_refs = task.get("uiRefs", {})
            group["uiRefs"] = {
                "pageRefs": list(ui_refs.get("pageRefs", [])),
                "interactionRefs": list(ui_refs.get("interactionRefs", [])),
                "visualSourceRefs": list(ui_refs.get("visualSourceRefs", [])),
                "frontendRoute": ui_refs.get("frontendRoute"),
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


def _code_module(root: Path, *, with_pom: bool = True) -> tuple[Path, Path]:
    repository = root / "code"
    module = repository / "backend" / "service"
    module.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
    if with_pom:
        (module / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    return repository, module


def _named_code_workspace(
    root: Path,
    name: str,
    *,
    module: str = ".",
    manifest: str | None = None,
) -> tuple[Path, Path]:
    repository = root / name
    workspace = repository if module == "." else repository / module
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
    if manifest:
        content = (
            json.dumps({
                "scripts": {
                    "build": "vite build",
                    "test": "vitest run",
                    "typecheck": "tsc --noEmit",
                }
            }) + "\n"
            if manifest == "package.json"
            else "<project/>\n"
        )
        (workspace / manifest).write_text(content, encoding="utf-8")
    return repository, workspace


class JsonWriterTests(unittest.TestCase):
    def test_shell_join_quotes_arguments_on_python_37(self) -> None:
        self.assertEqual(shell_join(["python", "hello world", "plain"]), "python 'hello world' plain")

    def test_plan_writer_binds_each_task_to_one_of_multiple_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)  # Add design.md for artifact ref validation
            _, backend_module = _named_code_workspace(
                root,
                "backend-repo",
                module="backend/service",
                manifest="pom.xml",
            )
            frontend_repo, _ = _named_code_workspace(
                root,
                "frontend-repo",
                manifest="package.json",
            )
            backend = _plan_task_body()
            backend["workspaceRef"] = "backend-repo"
            frontend = _plan_task_body()
            frontend.update({
                "id": "T002",
                "title": "frontend",
                "deps": ["T001"],
                "uiRequired": True,
                "workspaceRef": "frontend-repo",
            })
            frontend["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            frontend["acceptanceCriteria"][0].update({
                "id": "AC-T002-01",
                "scenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })
            frontend["validationCommands"][0].update({
                "id": "VAL-T002-01",
                "covers": ["AC-T002-01"],
            })
            frontend["uiRefs"] = {
                "pageRefs": ["PAGE-001"],
                "interactionRefs": ["UIX-001"],
                "visualSourceRefs": [],
                "frontendRoute": "spec-driven-ui",
            }
            group_file = _write_task_groups(root / "task-groups.json", [backend, frontend])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(backend_module),
                "--code-workspace", str(frontend_repo),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)

            backend_detail = _draft_detail_body(backend)
            backend_detail["validationCommands"][0].update({
                "argv": ["mvn", "test", "-Dtest=TargetTest"],
            })
            backend_detail["validationCommands"][0].pop("cwd", None)
            backend_path = root / "backend-detail.json"
            backend_path.write_text(json.dumps(backend_detail), encoding="utf-8")
            backend_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(backend_path),
            )
            self.assertEqual(backend_result.returncode, 0, backend_result.stdout + backend_result.stderr)
            draft = json.loads(
                (
                    feature_dir / ".tmp" / "plan_writer" / "draft"
                    / "plans" / "B001" / "plan.json"
                ).read_text(
                    encoding="utf-8"
                )
            )
            backend_task = next(task for task in draft["tasks"] if task["id"] == "T001")
            self.assertEqual(len(backend_task["validationTestPlan"]), 1)
            test_intent = backend_task["validationTestPlan"][0]
            self.assertEqual(test_intent["commandId"], "VAL-T001-01")
            self.assertEqual(test_intent["assetType"], "unit_test")
            self.assertEqual(test_intent["executionStage"], "with_code")
            self.assertNotIn("targets", test_intent)
            self.assertNotIn("create_in_code", json.dumps(test_intent))
            self.assertTrue(test_intent["testIntent"]["behavior"])

            frontend_detail = _draft_detail_body(frontend)
            frontend_detail["validationCommands"][0].update({"argv": ["npm", "test"]})
            frontend_detail["validationCommands"][0].pop("cwd", None)
            frontend_path = root / "frontend-detail.json"
            frontend_path.write_text(json.dumps(frontend_detail), encoding="utf-8")
            frontend_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T002", "--body-file", str(frontend_path),
            )
            self.assertEqual(frontend_result.returncode, 0, frontend_result.stdout + frontend_result.stderr)

            draft_dir = feature_dir / ".tmp" / "plan_writer" / "draft" / "plans"
            backend_task = json.loads((draft_dir / "B001" / "plan.json").read_text())["tasks"][0]
            frontend_task = json.loads((draft_dir / "B002" / "plan.json").read_text())["tasks"][0]
            self.assertEqual(backend_task["workspaceRef"], "backend-repo")
            self.assertEqual(backend_task["scope"]["workspaceRoots"], {"backend-repo": "backend/service"})
            self.assertEqual(backend_task["validationCommands"][0]["repo"], "backend-repo")
            self.assertEqual(backend_task["validationCommands"][0]["cwd"], "backend/service")
            self.assertEqual(frontend_task["workspaceRef"], "frontend-repo")
            self.assertEqual(frontend_task["scope"]["workspaceRoots"], {"frontend-repo": "."})
            self.assertEqual(frontend_task["validationCommands"][0]["repo"], "frontend-repo")
            self.assertEqual(frontend_task["validationCommands"][0]["cwd"], ".")

    def test_plan_writer_rejects_unknown_task_workspace_ref_before_draft_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _, backend = _named_code_workspace(root, "backend-repo")
            task = _plan_task_body()
            task["workspaceRef"] = "frontend-repo"
            group_file = _write_task_groups(root / "task-groups.json", [task])

            result = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(backend),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_group_workspace_ref_not_found", result.stdout + result.stderr)
            self.assertFalse((feature_dir / ".tmp" / "plan_writer" / "draft" / "lock.json").exists())

    def test_plan_writer_rebuild_resets_only_tasks_bound_to_changed_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)  # Add design.md for artifact ref validation
            backend_repo, _ = _named_code_workspace(root, "backend-repo")
            frontend_repo, _ = _named_code_workspace(root, "frontend-repo")
            _, replacement_frontend = _named_code_workspace(
                root / "replacement",
                "frontend-repo",
            )
            backend = _plan_task_body()
            backend["workspaceRef"] = "backend-repo"
            frontend = _plan_task_body()
            frontend.update({
                "id": "T002",
                "title": "frontend repository task",
                "deps": ["T001"],
                "workspaceRef": "frontend-repo",
            })
            frontend["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            frontend["acceptanceCriteria"][0].update({
                "id": "AC-T002-01",
                "scenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })
            frontend["validationCommands"][0].update({
                "id": "VAL-T002-01",
                "covers": ["AC-T002-01"],
            })
            group_file = _write_task_groups(root / "task-groups.json", [backend, frontend])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(backend_repo),
                "--code-workspace", str(frontend_repo),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            for task in (backend, frontend):
                detail = _draft_detail_body(task)
                detail["validationCommands"][0].pop("cwd", None)
                detail_path = root / f"{task['id']}-detail.json"
                detail_path.write_text(json.dumps(detail), encoding="utf-8")
                result = _run(
                    "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                    "--feature", "alpha", "--task-id", task["id"], "--body-file", str(detail_path),
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            rebuilt = _run(
                "plan_writer.py", "rebuild-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(backend_repo),
                "--code-workspace", str(replacement_frontend),
            )

            self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
            payload = json.loads(rebuilt.stdout)
            self.assertEqual(payload["preservedTaskIds"], ["T001"])
            self.assertEqual(payload["resetTaskIds"], ["T002"])
            self.assertEqual(payload["draft"]["readyTaskIds"], ["T001"])

    def test_plan_writer_prepare_draft_requires_code_workspace_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [_plan_task_body()])

            result = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--code-workspace", result.stderr)
            self.assertFalse((feature_dir / ".tmp" / "plan_writer" / "draft" / "lock.json").exists())

    def test_plan_writer_builds_and_finalizes_draft_batches_without_task_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)  # Add design.md for artifact ref validation
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            self.assertFalse((feature_dir / "plan.json").exists())
            draft_batch_path = feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            draft_task = json.loads(draft_batch_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(draft_task["specRefs"], task["specRefs"])
            self.assertEqual(draft_task["goal"], "")

            detail_path = Path(tmp) / "T001-detail.json"
            detail = _draft_detail_body(task)
            for command in detail["validationCommands"]:
                command.pop("cwd", None)
                command.pop("covers", None)
            detail_path.write_text(json.dumps(detail), encoding="utf-8")
            detailed = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertEqual(detailed.returncode, 0, detailed.stdout + detailed.stderr)
            draft_task = json.loads(draft_batch_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(draft_task["acceptanceCriteria"][0]["id"], "AC-T001-01")
            self.assertEqual(draft_task["validationCommands"][0]["id"], "VAL-T001-01")
            self.assertEqual(draft_task["validationCommands"][0]["covers"], ["AC-T001-01"])
            self.assertEqual(draft_task["validationCommands"][0]["cwd"], ".")

            preflight = _run(
                "plan_writer.py", "preflight-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
            finalized = _run(
                "plan_writer.py", "finalize-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["taskSetStatus"], "finalized")
            self.assertEqual(root["codeWorkspaces"], {"default": str(ROOT.resolve())})
            self.assertTrue((feature_dir / "PLAN.md").is_file())

    def test_plan_writer_projects_execution_stage_and_planning_touches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            task["executionStage"] = "integration"
            task["touches"] = ["src/shared/entry.py"]
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            group_data = json.loads(group_file.read_text(encoding="utf-8"))
            group_data["groups"][0].update({
                "executionStage": "integration",
                "touches": ["src/shared/entry.py"],
            })
            group_file.write_text(json.dumps(group_data), encoding="utf-8")

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            draft_path = feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            draft_task = json.loads(draft_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(draft_task["executionStage"], "integration")
            self.assertEqual(draft_task["scope"]["paths"], ["src/shared/entry.py"])

            detail = _draft_detail_body(task)
            for command in detail["validationCommands"]:
                command.pop("cwd", None)
                command.pop("covers", None)
            detail_path = Path(tmp) / "T001-detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")
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
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            batch = json.loads((feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["batches"][0]["executionStage"], "integration")
            self.assertEqual(batch["executionStage"], "integration")
            self.assertEqual(batch["tasks"][0]["scope"]["paths"], ["src/shared/entry.py"])
            self.assertNotIn("touches", json.dumps(root, ensure_ascii=False))
            self.assertNotIn("touches", json.dumps(batch, ensure_ascii=False))

    def test_plan_writer_does_not_add_parallel_touch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            initialized = _run(
                "plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertNotIn("parallelPolicy", plan)
            self.assertNotIn("touches", json.dumps(plan, ensure_ascii=False))

    def test_plan_writer_external_dependency_has_no_local_validation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)  # Add design.md for artifact ref validation
            task = _plan_task_body()
            task.update(
                {
                    "executionMode": "external_dependency",
                    "externalDependency": {
                        "system": "LF39.05_bczhaohuapi",
                        "owner": "zhaohu-team",
                        "trackingRefs": ["design.md#D-005"],
                    },
                    "validationCommands": [],
                }
            )
            group_file = _write_task_groups(root / "task-groups.json", [task])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            detail_path = root / "T001-detail.json"
            detail_path.write_text(json.dumps(_draft_detail_body(task)), encoding="utf-8")
            detailed = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertEqual(detailed.returncode, 0, detailed.stdout + detailed.stderr)
            preflight = _run(
                "plan_writer.py", "preflight-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
            finalized = _run(
                "plan_writer.py", "finalize-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
            formal_task = _read_plan_tasks(feature_dir)[0]
            self.assertEqual(formal_task["executionMode"], "external_dependency")
            self.assertEqual(
                formal_task["completionPolicy"],
                "external_dependency_recorded",
            )
            self.assertEqual(formal_task["validationCommands"], [])
            self.assertEqual(formal_task["validationTestPlan"], [])
            self.assertEqual(
                formal_task["externalDependency"]["trackingRefs"],
                ["design.md#D-005"],
            )
            plan_md = (feature_dir / "PLAN.md").read_text(encoding="utf-8")
            self.assertIn("执行模式: external_dependency", plan_md)
            self.assertIn("system=LF39.05_bczhaohuapi", plan_md)

    def test_plan_writer_default_never_emits_create_in_code_for_verified_existing_task(self) -> None:
        task = _plan_task_body()
        annotated = _annotate_validation_test_plan(task, [])
        self.assertEqual(annotated["validationTestPlan"][0]["commandId"], "VAL-T001-01")
        self.assertNotIn("targets", annotated["validationTestPlan"][0])
        self.assertNotIn("create_in_code", json.dumps(annotated["validationTestPlan"]))

    def test_plan_writer_prepare_draft_projects_backend_and_frontend_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir, second=True)
            backend = _plan_task_body()
            frontend = _plan_task_body()
            frontend.update({"id": "T002", "title": "frontend", "deps": ["T001"], "uiRequired": True})
            frontend["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            frontend["uiRefs"] = {
                "pageRefs": ["PAGE-001"],
                "interactionRefs": ["UIX-001"],
                "visualSourceRefs": [],
                "frontendRoute": "spec-driven-ui",
            }
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [backend, frontend])

            result = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            batches = json.loads(result.stdout)["draft"]["batches"]
            self.assertEqual([item["executionLane"] for item in batches], ["backend", "frontend"])
            self.assertEqual([item["taskIds"] for item in batches], [["T001"], ["T002"]])

    def test_plan_writer_splits_same_lane_repositories_and_routes_batch_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)
            backend_a, _ = _named_code_workspace(root, "backend-a", manifest="pom.xml")
            backend_b, _ = _named_code_workspace(root, "backend-b", manifest="pom.xml")
            first = _plan_task_body()
            first["workspaceRef"] = "backend-a"
            second = _plan_task_body()
            second.update({
                "id": "T002",
                "title": "second backend repository",
                "deps": ["T001"],
                "workspaceRef": "backend-b",
            })
            second["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            second["acceptanceCriteria"][0].update({
                "id": "AC-T002-01",
                "scenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })
            second["validationCommands"][0].update({
                "id": "VAL-T002-01",
                "covers": ["AC-T002-01"],
            })
            group_file = _write_task_groups(root / "task-groups.json", [first, second])

            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(backend_a),
                "--code-workspace", str(backend_b),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            self.assertEqual(
                [item["taskIds"] for item in json.loads(prepared.stdout)["draft"]["batches"]],
                [["T001"], ["T002"]],
            )
            for task in (first, second):
                detail = _draft_detail_body(task)
                detail["validationCommands"][0].pop("cwd", None)
                detail_path = root / f"{task['id']}-detail.json"
                detail_path.write_text(json.dumps(detail), encoding="utf-8")
                detailed = _run(
                    "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                    "--feature", "alpha", "--task-id", task["id"],
                    "--body-file", str(detail_path),
                )
                self.assertEqual(detailed.returncode, 0, detailed.stdout + detailed.stderr)
            finalized = _run(
                "plan_writer.py", "finalize-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)

            missing_repo = _run(
                "plan_writer.py", "add-compile-command", "--workspace", str(workspace),
                "--feature", "alpha", "--lane", "backend", "--command", "mvn compile -q",
                "--code-workspace", str(backend_a),
            )
            self.assertNotEqual(missing_repo.returncode, 0)
            self.assertIn("compile_command_repository_required", missing_repo.stdout)
            for index, (repository, code_workspace) in enumerate(
                (("backend-a", backend_a), ("backend-b", backend_b))
            ):
                added = _run(
                    "plan_writer.py", "add-compile-command", "--workspace", str(workspace),
                    "--feature", "alpha", "--lane", "backend", "--repo", repository,
                    "--command", "mvn compile -q",
                    "--code-workspace", str(code_workspace),
                )
                self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
                if index == 0:
                    incomplete = _run(
                        "plan_writer.py", "validate", "--workspace", str(workspace),
                        "--feature", "alpha", "--initial",
                    )
                    self.assertNotEqual(incomplete.returncode, 0)
                    self.assertIn(
                        "B002.compileCommand.required_compile_missing",
                        incomplete.stdout,
                    )

            initial = _run(
                "plan_writer.py", "validate", "--workspace", str(workspace),
                "--feature", "alpha", "--initial",
            )
            self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)

            first_batch = json.loads(
                (feature_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8")
            )
            second_batch = json.loads(
                (feature_dir / "plans" / "B002" / "plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                first_batch["compileCommand"].get("repo"),
                "backend-a",
            )
            self.assertEqual(
                second_batch["compileCommand"].get("repo"),
                "backend-b",
            )

    def test_plan_writer_draft_rejects_group_owned_detail_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)
            detail = _draft_detail_body(task)
            detail["specRefs"] = list(task["specRefs"])
            detail_path = Path(tmp) / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")

            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("draft_task_group_owned_field_forbidden", result.stdout)
            shown = _run(
                "plan_writer.py", "show-task-draft", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertEqual(json.loads(shown.stdout)["draft"]["pendingTaskIds"], ["T001"])

    def test_plan_writer_draft_rejects_writer_owned_nested_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)

            detail = _draft_detail_body(task)
            detail["acceptanceCriteria"][0]["id"] = "AC-MANUAL"
            detail_path = Path(tmp) / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")
            acceptance_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertNotEqual(acceptance_result.returncode, 0)
            self.assertIn("draft_acceptance_id_writer_owned", acceptance_result.stdout)

            detail = _draft_detail_body(task)
            detail["validationCommands"][0]["id"] = "VAL-MANUAL"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")
            validation_result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertNotEqual(validation_result.returncode, 0)
            self.assertIn("draft_validation_id_writer_owned", validation_result.stdout)

    def test_plan_writer_draft_rejects_direct_batch_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)

            batch_path = (
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            )
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            batch["tasks"][0]["title"] = "edited outside writer"
            batch_path.write_text(json.dumps(batch), encoding="utf-8")

            result = _run(
                "plan_writer.py", "show-task-draft", "--workspace", str(workspace),
                "--feature", "alpha",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_draft_digest_mismatch", result.stdout + result.stderr)

    def test_plan_writer_imports_legacy_task_directory_into_ready_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
            task = _plan_task_body()
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])

            result = _run(
                "plan_writer.py", "import-task-directory", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
                "--code-workspace", str(ROOT),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["draft"]["readyTaskIds"], ["T001"])
            self.assertFalse((feature_dir / "plan.json").exists())

    def test_plan_writer_recovers_interrupted_draft_bundle_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)
            draft_dir = feature_dir / ".tmp" / "plan_writer" / "draft"
            root = json.loads((draft_dir / "plan.json").read_text(encoding="utf-8"))
            lock = json.loads((draft_dir / "lock.json").read_text(encoding="utf-8"))
            batch = json.loads((draft_dir / "plans" / "B001" / "plan.json").read_text(encoding="utf-8"))
            transaction = {
                "version": 1,
                "featureId": "alpha",
                "root": root,
                "batchPlans": {"B001": batch},
                "lock": lock,
            }
            (draft_dir / ".draft-write-transaction.json").write_text(json.dumps(transaction), encoding="utf-8")
            (draft_dir / "lock.json").unlink()

            result = _run(
                "plan_writer.py", "show-task-draft", "--workspace", str(workspace), "--feature", "alpha",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((draft_dir / "lock.json").is_file())
            self.assertFalse((draft_dir / ".draft-write-transaction.json").exists())

    def test_plan_writer_draft_detects_group_changes_and_rebuilds_selectively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)
            first = _plan_task_body()
            second = _plan_task_body()
            second.update({"id": "T002", "title": "second", "deps": ["T001"]})
            second["specRefs"] = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-002"]
            second["acceptanceCriteria"][0].update({
                "id": "AC-T002-01", "scenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })
            second["validationCommands"][0].update({"id": "VAL-T002-01", "covers": ["AC-T002-01"]})
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [first, second])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)
            detail_path = Path(tmp) / "detail.json"
            detail_path.write_text(json.dumps(_draft_detail_body(first)), encoding="utf-8")
            self.assertEqual(_run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            ).returncode, 0)

            group_data = json.loads(group_file.read_text(encoding="utf-8"))
            group_data["groups"][1]["title"] = "changed second"
            group_file.write_text(json.dumps(group_data), encoding="utf-8")
            stale = _run(
                "plan_writer.py", "show-task-draft", "--workspace", str(workspace), "--feature", "alpha",
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("task_group_changed_after_draft_created", stale.stdout)

            rebuilt = _run(
                "plan_writer.py", "rebuild-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
            payload = json.loads(rebuilt.stdout)
            self.assertEqual(payload["preservedTaskIds"], ["T001"])
            self.assertEqual(payload["resetTaskIds"], ["T002"])

    def test_plan_writer_rebuild_repairs_legacy_draft_without_code_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [_plan_task_body()])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            lock_path = feature_dir / ".tmp" / "plan_writer" / "draft" / "lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["codeWorkspaces"] = []
            lock_path.write_text(json.dumps(lock), encoding="utf-8")

            missing = _run(
                "plan_writer.py", "rebuild-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("code_workspace_required_for_rebuild", missing.stdout)

            repaired = _run(
                "plan_writer.py", "rebuild-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
            self.assertEqual(json.loads(repaired.stdout)["resetTaskIds"], ["T001"])

    def test_plan_writer_draft_rejects_invalid_detail_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)
            detail = _draft_detail_body(task)
            detail["implementationPoints"] = [f"point {index}" for index in range(7)]
            detail["acceptanceCriteria"][0]["scenarioRefs"] = ["specs/other/spec.md#SCN-999"]
            detail_path = Path(tmp) / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")

            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("implementation_points_exceeds_limit", result.stdout)
            self.assertIn("acceptance_scenario_not_in_group", result.stdout)
            draft_batch = json.loads((
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(draft_batch["tasks"][0]["goal"], "")

    def test_plan_writer_draft_requires_non_goals_for_every_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            self.assertEqual(_run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            ).returncode, 0)
            detail = _draft_detail_body(task)
            detail["nonGoals"] = []
            detail_path = Path(tmp) / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")

            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("T001.nonGoals_missing", result.stdout)

    def test_plan_writer_group_requires_validation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            group_data = json.loads(group_file.read_text(encoding="utf-8"))
            group_data["groups"][0]["validationBoundary"] = "  "
            group_file.write_text(json.dumps(group_data), encoding="utf-8")

            result = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(ROOT),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("T001.validationBoundary_missing_or_too_short", result.stdout)
            self.assertFalse((feature_dir / "plan.json").exists())

    def test_plan_writer_group_requires_workspace_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            group_file = _write_task_groups(
                Path(tmp) / "task-groups.json",
                [_plan_task_body()],
            )
            group_data = json.loads(group_file.read_text(encoding="utf-8"))
            group_data["groups"][0].pop("workspaceRef")
            group_file.write_text(json.dumps(group_data), encoding="utf-8")

            result = _run(
                "plan_writer.py", "preflight-task-groups", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("T001.workspaceRef_invalid", result.stdout)
            self.assertFalse((feature_dir / "plan.json").exists())

    def test_plan_writer_draft_derives_workspace_root_pages_and_validation_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _, module = _code_module(root)
            task = _plan_task_body()
            task["uiRequired"] = True
            task["scope"]["pages"] = ["PAGE-001"]
            task["uiRefs"] = {
                "pageRefs": ["PAGE-001"],
                "interactionRefs": ["UIX-001"],
                "visualSourceRefs": [],
                "frontendRoute": "spec-driven-ui",
            }
            task["nonGoals"] = ["no unrelated UI changes"]
            group_file = _write_task_groups(root / "task-groups.json", [task])
            prepared = _run(
                "plan_writer.py", "prepare-task-draft", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
                "--code-workspace", str(module),
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            detail = _draft_detail_body(task)
            detail["scope"]["workspaceRoots"] = {"default": "wrong"}
            detail_path = root / "invalid-detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")
            rejected = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("draft_scope_workspace_roots_writer_owned", rejected.stdout)

            detail = _draft_detail_body(task)
            detail["validationCommands"][0].pop("cwd", None)
            detail_path = root / "detail.json"
            detail_path.write_text(json.dumps(detail), encoding="utf-8")

            result = _run(
                "plan_writer.py", "set-draft-task-detail", "--workspace", str(workspace),
                "--feature", "alpha", "--task-id", "T001", "--body-file", str(detail_path),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            draft_batch = json.loads((
                feature_dir / ".tmp" / "plan_writer" / "draft" / "plans" / "B001" / "plan.json"
            ).read_text(encoding="utf-8"))
            drafted = draft_batch["tasks"][0]
            self.assertEqual(drafted["scope"]["workspaceRoots"], {"default": "backend/service"})
            self.assertEqual(drafted["scope"]["pages"], ["PAGE-001"])
            self.assertEqual(drafted["validationBoundary"], task["validationBoundary"])
            self.assertEqual(drafted["validationCommands"][0]["cwd"], "backend/service")

    def test_plan_writer_rejects_direct_batch_contract_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            _write_design(feature_dir)
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
            _write_design(feature_dir)
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
            _write_design(feature_dir)
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

    def test_plan_writer_preflights_workspace_and_derives_batch_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _write_design(feature_dir)
            _, module = _code_module(root)
            task_dir = root / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            task["scope"].update({
                "workspaceRoots": {"default": "backend/service"},
                "paths": ["src/main/java/example"],
            })
            task["validationCommands"][0].update({
                "argv": ["mvn.cmd", "test", "-Dtest=ProtocolCtrlApplyTest", "-q"],
                "cwd": "backend/service",
            })
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(root / "task-groups.json", [task])

            missing_workspace = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )
            self.assertNotEqual(missing_workspace.returncode, 0)
            self.assertIn("code_workspace_preflight_required", missing_workspace.stdout)

            preflight = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
                "--code-workspace", str(module),
            )
            self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)

            materialized = _run(
                "plan_writer.py", "materialize-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
                "--code-workspace", str(module),
            )
            self.assertEqual(materialized.returncode, 0, materialized.stdout + materialized.stderr)
            batch_command = _run(
                "plan_writer.py", "add-compile-command", "--workspace", str(workspace),
                "--feature", "alpha", "--lane", "backend", "--command", "mvn.cmd compile -q",
                "--code-workspace", str(module),
            )
            self.assertEqual(batch_command.returncode, 0, batch_command.stdout + batch_command.stderr)
            root_plan = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(
                root_plan["compileProfiles"]["backend"]["commands"][0]["cwd"],
                "backend/service",
            )

    def test_plan_writer_rejects_project_selector_from_leaf_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _, module = _code_module(root)
            task_dir = root / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            task["scope"].update({
                "workspaceRoots": {"default": "backend/service"},
                "paths": ["src/main/java/example"],
            })
            task["validationCommands"][0].update({
                "argv": [
                    "mvn", "test", "-Dtest=ProtocolCtrlApplyTest",
                    "-pl", "backend/service/LF39.05_bccompliancemng",
                ],
                "cwd": "backend/service",
            })
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(root / "task-groups.json", [task])

            result = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
                "--code-workspace", str(module),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("maven_project_selector_requires_aggregator_cwd", result.stdout)

    def test_plan_writer_rejects_missing_validation_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)
            _, module = _code_module(root, with_pom=False)
            task_dir = root / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            task["scope"].update({
                "workspaceRoots": {"default": "backend/service"},
                "paths": ["src/main/java/example"],
            })
            task["validationCommands"][0].update({
                "argv": ["mvn.cmd", "test", "-Dtest=ProtocolCtrlApplyTest", "-q"],
                "cwd": "backend/service",
            })
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")
            group_file = _write_task_groups(root / "task-groups.json", [task])

            result = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
                "--code-workspace", str(module),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("validation_manifest_missing", result.stdout)

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

    def test_plan_writer_grouping_requires_complete_ui_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task = _plan_task_body()
            task["uiRequired"] = True
            task["uiRefs"] = {
                "pageRefs": ["PAGE-001"],
                "interactionRefs": ["UIX-001"],
                "visualSourceRefs": ["VIS-001"],
                "frontendRoute": "absolute-html",
            }
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            group_data = json.loads(group_file.read_text(encoding="utf-8"))
            del group_data["groups"][0]["uiRefs"]["visualSourceRefs"]
            del group_data["groups"][0]["uiRefs"]["frontendRoute"]
            group_file.write_text(json.dumps(group_data), encoding="utf-8")

            result = _run(
                "plan_writer.py", "preflight-task-groups", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("T001.visualSourceRefs_must_be_string_array", result.stdout)
            self.assertIn("T001.frontendRoute_missing", result.stdout)

    def test_plan_writer_grouping_returns_all_invalid_tasks_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, feature_dir = _workspace(root)
            _write_specs(feature_dir)

            def oversized_task(task_id: str, start: int) -> dict:
                task = _plan_task_body()
                task["id"] = task_id
                task["title"] = f"{task_id} oversized"
                task["specRefs"] = [
                    "specs/cap/spec.md#REQ-001",
                    *[
                        f"specs/cap/spec.md#SCN-{index:03d}"
                        for index in range(start, start + 6)
                    ],
                ]
                task["mergedScenarioRefs"] = []
                task.pop("splitRationale", None)
                return task

            group_file = _write_task_groups(
                root / "task-groups.json",
                [oversized_task("T001", 1), oversized_task("T002", 7)],
            )
            result = _run(
                "plan_writer.py",
                "preflight-task-groups",
                "--workspace",
                str(workspace),
                "--feature",
                "alpha",
                "--group-file",
                str(group_file),
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["validation"]["invalidTaskIds"], ["T001", "T002"])
            issues = payload["validation"]["issues"]
            self.assertEqual(
                {(issue["taskIds"][0], issue["reason"]) for issue in issues},
                {
                    ("T001", "missing_plan_task_merged_scenario_refs"),
                    ("T001", "missing_plan_task_split_rationale"),
                    ("T002", "missing_plan_task_merged_scenario_refs"),
                    ("T002", "missing_plan_task_split_rationale"),
                },
            )
            merged_issue = next(
                issue
                for issue in issues
                if issue["taskIds"] == ["T001"]
                and issue["reason"] == "missing_plan_task_merged_scenario_refs"
            )
            self.assertEqual(len(merged_issue["diagnostics"]["expectedRefs"]), 6)
            self.assertEqual(
                merged_issue["diagnostics"]["violations"][0]["code"],
                "merged_scenario_refs_missing",
            )

    def test_plan_writer_freezes_all_ui_refs_after_grouping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_specs(feature_dir)
            task_dir = Path(tmp) / "tasks"
            task_dir.mkdir()
            task = _plan_task_body()
            task["uiRequired"] = True
            task["scope"]["pages"] = ["PAGE-001"]
            task["uiRefs"] = {
                "pageRefs": ["PAGE-001"],
                "interactionRefs": ["UIX-001"],
                "visualSourceRefs": ["VIS-001"],
                "frontendRoute": "absolute-html",
            }
            group_file = _write_task_groups(Path(tmp) / "task-groups.json", [task])
            task["uiRefs"]["visualSourceRefs"] = ["VIS-002"]
            task["uiRefs"]["frontendRoute"] = "standard-html"
            (task_dir / "T001.json").write_text(json.dumps(task), encoding="utf-8")

            result = _run(
                "plan_writer.py", "preflight-task-set", "--workspace", str(workspace),
                "--feature", "alpha", "--group-file", str(group_file), "--task-dir", str(task_dir),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("task_group_contract_mismatch", result.stdout)
            self.assertIn("visualSourceRefs", result.stdout)
            self.assertIn("frontendRoute", result.stdout)

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
                "--feature", "alpha", "--command", TEST_PROJECT_COMMAND,
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
                    "--feature", "alpha", "--command", TEST_PROJECT_COMMAND,
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
        self.assertEqual(contract["taskTemplateStatus"], "deprecated_legacy_import_only")
        self.assertEqual(contract["taskInputExample"]["id"], "T001")
        self.assertIn("validationCommands", contract["taskInputExample"])
        self.assertIn("matrixExceptionExample", contract["taskInputExample"])
        self.assertEqual(contract["exampleOnlyTaskFields"], ["matrixExceptionExample"])
        self.assertEqual(
            contract["taskValidationPolicy"],
            {
                "mode": "defer_to_test_stages",
                "orchestration": "inline",
                "codeGate": "batch_compile_only",
                "maxTestStageRepairAttempts": 3,
                "taskCommandTiming": "test_stages_only",
                "codeCommandTiming": "after_all_batch_tasks_implemented",
                "validationTarget": "batch_final_snapshot",
                "codeStageTestExecution": "forbidden",
            },
        )
        self.assertNotIn("status", contract["taskInputExample"])
        self.assertEqual(contract["recommendedInputMode"], "draft-batch")
        self.assertEqual(
            contract["taskDetailTemplate"],
            "skills/autodev/autodev-plan/templates/task-detail-input.json",
        )
        self.assertNotIn("workspaceRoots", contract["taskDetailInputExample"]["scope"])
        self.assertTrue(contract["taskDetailInputExample"]["nonGoals"])
        self.assertNotIn("id", contract["taskDetailInputExample"]["acceptanceCriteria"][0])
        self.assertNotIn("id", contract["taskDetailInputExample"]["validationCommands"][0])
        self.assertIn("task-directory", contract["deprecatedInputModes"])
        self.assertIn("import-task-directory", contract["legacyTaskDirectoryMigration"])
        self.assertEqual(
            contract["taskGroupTemplate"],
            "skills/autodev/autodev-plan/templates/task-groups.json",
        )
        self.assertIn("groups", contract["taskGroupInputExample"])
        group_ui_example = contract["taskGroupUiRequiredExample"]
        self.assertTrue(group_ui_example["uiRequired"])
        self.assertEqual(
            list(group_ui_example["uiRefs"]),
            ["pageRefs", "interactionRefs", "visualSourceRefs", "frontendRoute"],
        )
        self.assertEqual(
            contract["exampleOnlyTaskGroupFields"],
            [
                "externalDependencyExample",
                "matrixExceptionExample",
                "uiRequiredExample",
            ],
        )
        external_example = contract["taskGroupExternalDependencyExample"]
        self.assertEqual(external_example["executionMode"], "external_dependency")
        self.assertTrue(external_example["externalDependency"]["trackingRefs"])
        group_exception = contract["taskGroupMatrixExceptionExample"]
        self.assertEqual(group_exception["mergedScenarioRefs"], group_exception["specRefs"][1:])
        self.assertIn("splitRationale", group_exception)
        self.assertIn("validationBoundary", group_exception)
        self.assertEqual(contract["validationKinds"], sorted(TASK_VALIDATION_KINDS))
        self.assertEqual(
            contract["validationKindsByLane"],
            {
                "backend": ["behavior_test", "e2e_test", "integration_test", "static_check"],
                "frontend": sorted(TASK_VALIDATION_KINDS),
            },
        )
        self.assertEqual(
            contract["validationCoverage"]["compileMayCoverAcceptanceCriteriaByLane"],
            {"backend": False, "frontend": True},
        )
        self.assertEqual(
            contract["validationCoverage"]["frontendCompileKinds"],
            ["build", "compile", "typecheck"],
        )
        self.assertEqual(contract["validationCommandPolicy"]["inlineShell"], "forbidden")
        self.assertTrue(contract["validationCommandPolicy"]["packageScriptMustExist"])
        self.assertTrue(contract["validationCommandPolicy"]["mavenTargetMustBeConcreteClass"])
        self.assertEqual(
            contract["validationTestPlanPolicy"]["targetModes"],
            [],
        )
        self.assertEqual(
            contract["validationTestPlanPolicy"]["representation"],
            "test_intent_only",
        )
        self.assertEqual(
            contract["validationTestPlanPolicy"]["createInCodeAllowed"],
            False,
        )
        self.assertEqual(contract["compileCommandKinds"], ["compile"])
        self.assertEqual(contract["qualityGateCommandKinds"], ["static_check"])
        self.assertEqual(
            contract["projectValidationCommand"]["allowedKinds"],
            ["e2e_test", "integration_test", "static_check"],
        )
        self.assertTrue(contract["projectValidationCommand"]["mustNotDuplicateBatchProfile"])
        self.assertTrue(contract["projectValidationCommand"]["requiredForParallelPipeline"])
        self.assertEqual(
            contract["projectValidationCommand"]["requiredPerWorkspaceRef"],
            "one_candidate_integration_command",
        )
        self.assertEqual(contract["projectValidationCommand"]["executionTarget"], "merge_candidate")
        self.assertTrue(contract["projectValidationCommand"]["repoRequiredWhenMultipleWorkspaces"])
        self.assertEqual(
            contract["compileCommand"],
            {
                "command": (
                    "add-compile-command --lane <backend|frontend> "
                    "[--repo <workspaceRef>] --command <command> --code-workspace <path>"
                ),
                "requiredFields": ["argv", "cwd", "kind", "required"],
                "requiredPerUsedWorkspaceInLane": "exactly_one",
                "repoRequiredWhenLaneUsesMultipleWorkspaces": True,
                "defaultCwd": "declared_workspace_root",
            },
        )
        self.assertEqual(
            contract["qualityGateCommand"]["executionStage"],
            "quality_gate_only_when_commands_present",
        )
        self.assertEqual(contract["workspaceContract"]["field"], "scope.workspaceRoots")
        self.assertEqual(contract["workspaceContract"]["taskBindingField"], "workspaceRef")
        self.assertEqual(contract["workspaceContract"]["maxWorkspaceRefsPerTask"], 1)
        self.assertFalse(contract["workspaceContract"]["crossRepositoryTaskSupported"])
        self.assertTrue(contract["workspaceContract"]["codeWorkspaceArgumentRepeatable"])
        self.assertEqual(
            contract["workspaceContract"]["source"],
            "prepare-task-draft --code-workspace",
        )
        self.assertEqual(contract["workspaceContract"]["scopePathsMode"], "advisory_change_hint")
        self.assertTrue(contract["workspaceContract"]["codeWorkspacePreflightRequired"])
        self.assertEqual(
            contract["validationEnvironmentPolicy"],
            {
                "preflightBeforeRun": True,
                "missingExecutableResult": "block_batch_compile",
                "runtimeEnvironmentResult": "block_batch_compile",
                "requiredActions": ["fix_compile_environment_and_retry_batch_compile"],
                "planOrDigestRebuildRequired": False,
            },
        )
        self.assertEqual(
            contract["fieldRules"]["workspaceRef"],
            {"required": True, "type": "repository_id", "source": "task_group"},
        )
        self.assertEqual(
            contract["fieldRules"]["validationBoundary"],
            {
                "required": True,
                "type": "non_empty_string",
                "minLength": 10,
                "source": "task_group",
            },
        )
        self.assertEqual(
            contract["fieldRules"]["nonGoals"],
            {"required": True, "minItems": 1, "items": "non_empty_string"},
        )
        self.assertEqual(contract["batchAssignment"]["strategy"], BATCH_STRATEGY)
        self.assertEqual(contract["batchAssignment"]["maxTasks"], MAX_BATCH_TASKS)
        self.assertEqual(contract["batchAssignment"]["primaryCapabilitySource"], "first_spec_ref_file")
        self.assertIn("executionLaneSource", contract["batchAssignment"])
        self.assertIn("executionLaneMapping", contract["batchAssignment"])
        self.assertIn("executionLaneOrder", contract["batchAssignment"])
        self.assertEqual(
            contract["batchAssignment"]["executionOrder"],
            "root_batch_order_then_task_order",
        )
        self.assertEqual(contract["batchAssignment"]["batchConcurrency"], 1)
        self.assertEqual(contract["batchAssignment"]["taskConcurrency"], 1)
        self.assertTrue(contract["batchAssignment"]["requiresNewConversationBetweenBatches"])
        self.assertEqual(contract["batchAssignment"]["executionLaneSource"], "uiRequired")
        self.assertEqual(
            contract["batchAssignment"]["executionLaneMapping"],
            {"uiRequired_false": "backend", "uiRequired_true": "frontend"},
        )
        self.assertEqual(contract["batchAssignment"]["executionLaneOrder"], ["backend", "frontend"])
        self.assertEqual(
            contract["batchAssignment"]["appendRule"],
                "same_primary_capability_execution_lane_and_workspace_as_immediately_preceding_batch_frontend_route_and_not_full",
        )
        self.assertFalse(contract["batchAssignment"]["manualBatchIdSupported"])
        self.assertIn("taskSetFinalization", contract)
        self.assertEqual(
            contract["taskSetFinalization"],
            {
                "groupingPreflightCommand": "preflight-task-groups --group-file <file>",
                "prepareCommand": "prepare-task-draft --group-file <file> --code-workspace <path>",
                "detailCommand": "set-draft-task-detail --task-id <id> --body-stdin",
                "preflightCommand": "preflight-task-draft",
                "command": "finalize-task-draft",
                "coverage": "all_path_qualified_spec_scenarios",
                "requiredBefore": [
                    "add-compile-command",
                    "add-project-validation-command",
                    "render-md",
                ],
            },
        )
        self.assertTrue(contract["collectingRepairs"]["atomic"])
        self.assertTrue(contract["collectingRepairs"]["preserveUnchangedTaskDetails"])
        self.assertFalse(contract["draftWorkflow"]["standaloneTaskFiles"])
        self.assertEqual(contract["draftWorkflow"]["groupLock"], "groupingDigest")
        self.assertEqual(contract["draftWorkflow"]["persistentDesignLock"], ".design-contract.lock.json")
        self.assertEqual(
            contract["draftWorkflow"]["scopeWorkspaceRootsSource"],
            "prepare-task-draft --code-workspace",
        )
        self.assertEqual(
            contract["writerOwnedDetailFields"]["scope"],
            ["pages", "workspaceRoots"],
        )
        self.assertIn("specRefs", contract["groupOwnedTaskFields"])
        self.assertIn("workspaceRef", contract["requiredTaskGroupFields"])
        self.assertIn("validationCommands", contract["requiredTaskDetailFields"])
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
                "requiredValidationByLane": {
                    "backend": "one_complete_required_behavior_command",
                    "frontend": "one_complete_required_behavior_or_matching_compile_command",
                },
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
            {
                "requiredFields": ["id", "argv", "cwd", "kind", "required"],
                "allowedKinds": ["e2e_test", "integration_test", "static_check"],
                "mustNotDuplicateBatchProfile": True,
                "requiredForParallelPipeline": True,
                "requiredPerWorkspaceRef": "one_candidate_integration_command",
                "executionTarget": "merge_candidate",
                "repoRequiredWhenMultipleWorkspaces": True,
            },
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

            result = validate_stage(workspace=workspace, feature="alpha", stage="dev.plan")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code, _ = run_postcheck(ROOT, workspace, "autodev-plan", "alpha", workflow_record=_state_record())

            self.assertEqual(result.ok, code == 0)
            self.assertEqual([error["reason"] for error in result.errors or []], [])
            self.assertEqual(output.getvalue().strip(), "")
            self.assertFalse((feature_dir / "SMOKE_TEST_PLAN.json").exists())

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
            self.assertIn("missing_ref_anchor", output.getvalue())

    def test_plan_structure_passes_while_stage_gate_fails_on_missing_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            _write_proposal(feature_dir)
            _write_specs(feature_dir, second=True)
            _write_design(feature_dir)
            _write_plan(feature_dir, include_second=False)

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
                        "validationBoundary": "public behavior seam validated by the task command",
                        "nonGoals": ["do not change unrelated behavior"],
                        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                "--non-goal",
                "do not change unrelated behavior",
                "--spec-ref",
                "specs/cap/spec.md#REQ-001",
                "--spec-ref",
                "specs/cap/spec.md#SCN-001",
                "--design-ref",
                "design.md#D-001",
                "--decision-id",
                "D-001",
                "--validation-command",
                TEST_TASK_COMMAND,
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
                        "nonGoals": ["do not change unrelated behavior"],
                        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                "title": "中文路径 stdin task",
                "goal": "deliver a UTF-8 Chinese path through stdin",
                "status": "done",
                "deps": [],
                "uiRequired": False,
                "workspaceRef": "default",
                "scope": {
                    "modules": ["中文模块"],
                    "entrypoints": [],
                    "pages": [],
                    "dataObjects": [],
                    "workspaceRoots": {"default": "."},
                    "paths": ["src/main/java/中文目录/服务.java"],
                },
                "implementationPoints": ["update behavior", "cover boundary"],
                "acceptanceCriteria": ["behavior is observable"],
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
                "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
            self.assertEqual(tasks[0]["scope"]["modules"], ["中文模块"])
            self.assertEqual(tasks[0]["scope"]["paths"], ["src/main/java/中文目录/服务.java"])

    def test_plan_writer_rejects_non_utf8_stdin_with_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = _workspace(Path(tmp))
            payload = {
                "id": "T001",
                "title": "中文路径 stdin task",
                "goal": "deliver a UTF-8 Chinese path through stdin",
                "deps": [],
                "uiRequired": False,
                "workspaceRef": "default",
                "scope": {
                    "modules": ["中文模块"],
                    "entrypoints": [],
                    "pages": [],
                    "dataObjects": [],
                    "workspaceRoots": {"default": "."},
                    "paths": ["src/main/java/中文目录/服务.java"],
                },
                "implementationPoints": ["write a path", "read a path"],
                "acceptanceCriteria": ["path is preserved"],
                "validationBoundary": "UTF-8 body input is validated before any plan write",
                "nonGoals": ["do not change behavior"],
                "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
                "expectedFiles": [],
                "evidenceIds": [],
                "blockers": [],
            }

            init = _run("plan_writer.py", "init", "--workspace", str(workspace), "--feature", "alpha")
            body = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "plan_writer.py"),
                    "add-task",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                    "--body-stdin",
                ],
                cwd=ROOT,
                input=json.dumps(payload, ensure_ascii=False).encode("gb18030"),
                capture_output=True,
                check=False,
            )

            output = body.stdout.decode("utf-8")
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            self.assertNotEqual(body.returncode, 0, output + body.stderr.decode("utf-8"))
            self.assertIn("invalid_body_stdin_encoding", output)
            self.assertIn("不是有效 UTF-8", output)
            self.assertIn("Unicode surrogate", output)
            self.assertIn("--body-file", output)
            self.assertEqual(json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))["batches"], [])

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
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
                "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
                "specRefs": [
                    "specs/cap/spec.md#REQ-001",
                    *[f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 14)],
                ],
                "designRefs": ["design.md#D-001"],
                "apiIds": [],
                "dataIds": [],
                "decisionIds": ["D-001"],
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                "validationBoundary": "query response matrix seam validated by the task command",
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
                        "argv": [sys.executable, "-c", "print('task matrix validation')"],
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
                    "validationBoundary": "scenario reference seam validated before task creation",
                    "nonGoals": ["do not change unrelated behavior"],
                    "specRefs": ["specs/cap/spec.md#REQ-001", f"specs/cap/spec.md#{anchor}"],
                    "designRefs": ["design.md#D-001"],
                    "apiIds": [],
                    "dataIds": [],
                    "decisionIds": ["D-001"],
                    "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
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
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                TEST_TASK_COMMAND,
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
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
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
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
                "validationBoundary": "public behavior seam validated by the task command",
                "nonGoals": ["do not change unrelated behavior"],
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
                "validationCommands": [{"command": TEST_TASK_COMMAND}],
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
