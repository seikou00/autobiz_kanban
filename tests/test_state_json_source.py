from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.artifacts import scan_artifacts  # noqa: E402
from board_core.contracts import BoardConfigError, load_board_config  # noqa: E402
from board_core.state_store import (  # noqa: E402
    STATE_SCHEMA_VERSION,
    check_or_fix_state_sync,
    load_state_json_records_result,
    render_state_md,
    state_json_content_from_records,
    write_state_records,
)
from board_core.workflow import build_workflow_shell  # noqa: E402
from hooks.update_checkpoint import prepare_checkpoint_update  # noqa: E402
from hooks.update_checkpoint import validate_plan_json_for_checkpoint  # noqa: E402
from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.plan_json import task_contract_sha256  # noqa: E402


TASK_VALIDATION_ARGV = [sys.executable, "-m", "unittest", "tests.test_plan_granularity"]
SECOND_TASK_VALIDATION_ARGV = [sys.executable, "-m", "unittest", "tests.test_validation_policy"]
BATCH_VALIDATION_ARGV = [sys.executable, "-m", "compileall", "-q", "hooks"]
PROJECT_VALIDATION_ARGV = [sys.executable, "-m", "unittest", "tests.test_json_writers"]


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / ".autobizdevops" / "features").mkdir(parents=True)
    return workspace


def plugin_env(workspace: Path, *, feature: str = "alpha") -> dict[str, str]:
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(ROOT)
    env["PLUGIN_WORKSPACE"] = str(workspace.parent)
    env["PROJECT_DIR"] = workspace.name
    env["FEATURE_ID"] = feature
    env.pop("PLUGIN_OUTPUT_DIR", None)
    return env


def env_without(workspace: Path, *keys: str) -> dict[str, str]:
    env = plugin_env(workspace)
    for key in keys:
        env.pop(key, None)
    return env


def sample_record(checkpoint: str = "discuss_in_progress") -> dict[str, str]:
    return {
        "feature": "alpha",
        "owner": "owner",
        "checkpoint": checkpoint,
        "stage": "需求澄清",
        "iteration": "1",
        "updated_at": "2026-05-25 12:00:00",
    }


def write_minimal_trace_sources(feature_dir: Path) -> None:
    (feature_dir / "specs" / "capability").mkdir(parents=True, exist_ok=True)
    (feature_dir / "specs" / "capability" / "spec.md").write_text(
        "\n".join(
            [
                "## ADDED Requirements",
                "### Requirement [REQ-001]: capability",
                "#### Scenario [SCN-001]: happy path",
            ]
        ),
        encoding="utf-8",
    )
    (feature_dir / "design.md").write_text(
        "\n".join(
            [
                "# 技术设计: capability",
                "## 1. Context / 输入上下文",
                "## 2. Spec Traceability / 规格追踪",
                "| Spec | Requirement / Scenario | Design Coverage |",
                "|------|------------------------|-----------------|",
                "| specs/capability/spec.md | Requirement [REQ-001] / Scenario [SCN-001] | API-001 / DATA-001 / D-001 |",
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


def write_done_plan_json_and_evidence(feature_dir: Path, *, feature: str = "alpha") -> None:
    feature_dir.mkdir(parents=True, exist_ok=True)
    run_id = "run-test-state"
    batch_run_id = "run-batch-state"
    batch_command = {
        "id": "BATCH-B001-VAL-001",
        "argv": BATCH_VALIDATION_ARGV,
        "cwd": ".",
        "kind": "compile",
        "required": True,
    }
    (feature_dir / "plan.json").write_text(
        json.dumps(
            {
                "featureId": feature,
                "projectValidationCommands": [
                    {
                        "id": "PROJECT-VAL-001",
                        "argv": PROJECT_VALIDATION_ARGV,
                        "cwd": ".",
                        "kind": "integration_test",
                        "required": True,
                    }
                ],
                "projectCheckEvidenceIds": ["ev_0003"],
                "latestProjectCheckEvidenceId": "ev_0003",
                "tasks": [
                    {
                        "id": "T001",
                        "title": "Implement",
                        "goal": "deliver implementation behavior",
                        "status": "done",
                        "deps": [],
                        "scope": {
                            "modules": ["src"],
                            "entrypoints": ["API-001"],
                            "pages": [],
                            "dataObjects": ["DATA-001"],
                        },
                        "implementationPoints": ["update implementation", "cover validation path"],
                        "acceptanceCriteria": [
                            {
                                "id": "AC-T001-01",
                                "text": "validation command passes",
                                "scenarioRefs": ["specs/capability/spec.md#SCN-001"],
                            }
                        ],
                        "workspaceRef": "default",
                        "validationBoundary": "public behavior seam validated by the task command",
                        "nonGoals": ["do not change unrelated behavior"],
                        "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                        "designRefs": ["design.md#API-001", "#DATA-001", "#D-001"],
                        "apiIds": ["API-001"],
                        "dataIds": ["DATA-001"],
                        "decisionIds": ["D-001"],
                        "completionPolicy": "all_required_validations_pass",
                        "validationCommands": [
                            {
                                "id": "VAL-T001-01",
                                "argv": TASK_VALIDATION_ARGV,
                                "cwd": ".",
                                "kind": "behavior_test",
                                "required": True,
                                "covers": ["AC-T001-01"],
                            }
                        ],
                        "expectedFiles": [],
                        "evidenceIds": ["ev_0001"],
                        "completionEvidenceIds": ["ev_0001"],
                        "latestPassEvidenceId": "ev_0001",
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
    monolithic = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
    task_items = monolithic.pop("tasks")
    (feature_dir / "plan.json").write_text(
        json.dumps(
            {
                "featureId": feature,
                "status": "done",
                "taskSetStatus": "finalized",
                "activeBatchId": None,
                "nextBatchId": None,
                "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
                "batchValidationProfiles": {
                    "backend": {
                        "commands": [
                            {
                                "argv": batch_command["argv"],
                                "cwd": batch_command["cwd"],
                                "kind": batch_command["kind"],
                                "required": batch_command["required"],
                            }
                        ]
                    }
                },
                "batches": [
                    {
                        "id": "B001",
                        "path": "plans/B001/plan.json",
                        "title": "capability",
                        "specRoots": ["specs/capability/spec.md"],
                        "executionLane": "backend",
                        "deps": [],
                        "taskIds": ["T001"],
                        "status": "done",
                    }
                ],
                "projectValidationCommands": monolithic["projectValidationCommands"],
                "projectCheckEvidenceIds": monolithic["projectCheckEvidenceIds"],
                "latestProjectCheckEvidenceId": monolithic["latestProjectCheckEvidenceId"],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    batch_path = feature_dir / "plans" / "B001" / "plan.json"
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text(
        json.dumps(
            {
                "featureId": feature,
                "batchId": "B001",
                "title": "capability",
                "executionLane": "backend",
                "status": "done",
                "taskCount": 1,
                "completedTaskCount": 1,
                "completionEvidenceIds": ["ev_0001"],
                "batchValidation": {
                    "profile": "backend",
                    "status": "passed",
                    "commands": [batch_command],
                    "evidenceIds": ["ev_0002"],
                    "latestPassEvidenceIds": ["ev_0002"],
                    "activeRunId": batch_run_id,
                },
                "startedAt": "2026-07-10T00:00:00Z",
                "completedAt": "2026-07-10T00:01:00Z",
                "tasks": task_items,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    append_evidence(
        feature_dir,
        {
            "featureId": feature,
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "T001",
            "action": "validation",
            "detailVersion": 2,
            "runId": run_id,
            "completionMode": "implemented",
            "summary": "VAL-T001-01 validation pass",
            "implementation": {
                "noCodeChange": False,
                "whatChanged": ["src/example.py"],
                "why": "deliver implementation behavior",
            },
            "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
            "designRefs": ["design.md#API-001", "#DATA-001", "#D-001"],
            "changedFiles": ["src/example.py"],
            "fileChanges": [
                {
                    "path": "src/example.py",
                    "operation": "modified",
                    "kind": "source",
                    "summary": "updated example implementation",
                    "reason": "task implementation",
                }
            ],
            "supportingFiles": [],
            "checkedCriteria": ["AC-T001-01"],
            "validation": {
                "commandId": "VAL-T001-01",
                "argv": TASK_VALIDATION_ARGV,
                "command": shlex.join(TASK_VALIDATION_ARGV),
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "exitCode": 0,
                "result": "pass",
            },
        },
        output_tail="ok\n",
    )
    append_evidence(
        feature_dir,
        {
            "featureId": feature,
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "__batch__",
            "batchId": "B001",
            "action": "batch_validation",
            "detailVersion": 2,
            "runId": batch_run_id,
            "completionMode": "verified_existing",
            "summary": "BATCH-B001-VAL-001 batch validation pass",
            "implementation": {
                "noCodeChange": True,
                "whatChanged": [],
                "why": "batch check validates the completed workspace",
            },
            "specRefs": [],
            "designRefs": [],
            "changedFiles": [],
            "fileChanges": [],
            "transientValidationFiles": [],
            "supportingFiles": [],
            "checkedCriteria": ["BATCH-B001-VAL-001"],
            "validation": {
                "commandId": batch_command["id"],
                "argv": batch_command["argv"],
                "command": shlex.join(BATCH_VALIDATION_ARGV),
                "cwd": batch_command["cwd"],
                "kind": batch_command["kind"],
                "required": batch_command["required"],
                "exitCode": 0,
                "result": "pass",
            },
        },
        output_tail="batch ok\n",
    )
    append_evidence(
        feature_dir,
        {
            "featureId": feature,
            "checkpoint": "code_in_progress",
            "nodeId": "dev.code",
            "skill": "autodev-code",
            "taskId": "__project__",
            "action": "project_check",
            "detailVersion": 2,
            "runId": "run-project-state",
            "completionMode": "verified_existing",
            "summary": "PROJECT-VAL-001 project check pass",
            "implementation": {
                "noCodeChange": True,
                "whatChanged": [],
                "why": "project check validates the completed workspace",
            },
            "specRefs": [],
            "designRefs": [],
            "changedFiles": [],
            "fileChanges": [],
            "supportingFiles": [],
            "checkedCriteria": ["PROJECT-VAL-001"],
            "validation": {
                "commandId": "PROJECT-VAL-001",
                "argv": PROJECT_VALIDATION_ARGV,
                "command": shlex.join(PROJECT_VALIDATION_ARGV),
                "cwd": ".",
                "kind": "integration_test",
                "required": True,
                "exitCode": 0,
                "result": "pass",
            },
        },
        output_tail="project ok\n",
    )
    run_dir = feature_dir / ".task-runs" / "T001"
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_task = json.loads(batch_path.read_text(encoding="utf-8"))["tasks"][0]
    (run_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "version": 1,
                "runId": run_id,
                "featureId": feature,
                "taskId": "T001",
                "taskContractSha256": task_contract_sha256(plan_task),
                "status": "done",
                "success": True,
                "completionMode": "implemented",
                "changedFiles": ["src/example.py"],
                "fileChanges": [
                    {
                        "path": "src/example.py",
                        "operation": "modified",
                        "kind": "source",
                        "summary": "updated example implementation",
                        "reason": "task implementation",
                    }
                ],
                "evidenceIds": ["ev_0001"],
                "completionEvidenceIds": ["ev_0001"],
                "completedCommandEvidence": {
                    "VAL-T001-01": {
                        "evidenceId": "ev_0001",
                        "result": "pass",
                        "required": True,
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    batch_run_dir = feature_dir / ".batch-runs" / "B001"
    batch_run_dir.mkdir(parents=True, exist_ok=True)
    (batch_run_dir / f"{batch_run_id}.json").write_text(
        json.dumps(
            {
                "version": 1,
                "runId": batch_run_id,
                "featureId": feature,
                "batchId": "B001",
                "status": "done",
                "success": True,
                "attempts": [
                    {
                        "attempt": 1,
                        "evidenceIds": ["ev_0002"],
                        "result": "pass",
                        "completedAt": "2026-07-10T00:01:00Z",
                    }
                ],
                "evidenceIds": ["ev_0002"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_non_ui_context(feature_dir: Path, *, feature: str = "alpha", locked: bool = True) -> None:
    feature_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "featureId": feature,
        "uiRequired": False,
        "decisionStatus": "locked" if locked else "confirmed",
        "decisionSource": "default_false",
        "confirmedAtCheckpoint": "prd_done",
        "notApplicableReason": "纯后端能力",
        "pages": [],
        "interactions": [],
        "visualSources": [],
        "capabilities": [],
    }
    if locked:
        payload["lockedAtCheckpoint"] = "specs_done"
    (feature_dir / "UI_CONTEXT.json").write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class StateStoreTests(unittest.TestCase):
    def test_loads_v2_state_and_repairs_markdown_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            records = {"alpha": sample_record()}
            (workspace / ".autobizdevops" / "state.json").write_text(
                state_json_content_from_records(records),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(result.records["alpha"]["checkpoint"], "discuss_in_progress")
            self.assertEqual(
                (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"),
                render_state_md(records),
            )

    def test_legacy_checkpoint_map_upgrades_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps({"alpha": "discuss_done"}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)
            upgraded = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(upgraded["schemaVersion"], STATE_SCHEMA_VERSION)
            self.assertEqual(upgraded["features"]["alpha"]["checkpoint"], "discuss_done")

    def test_missing_json_migrates_from_state_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "STATE.md").write_text(
                "\n".join(
                    [
                        "# 工程状态",
                        "",
                        "| Feature | 负责人 | checkpoint | 阶段 | 迭代 | 最后更新 |",
                        "|---------|--------|-----------|------|------|---------|",
                        "| alpha | owner | discuss_done | 需求澄清 | 2 | 2026-05-25 12:00:00 |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)
            migrated = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(migrated["features"]["alpha"]["checkpoint"], "discuss_done")
            self.assertIn("自动生成", (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"))

    def test_direct_json_loader_does_not_fallback_to_state_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "STATE.md").write_text(
                "\n".join(
                    [
                        "# 工程状态",
                        "",
                        "| Feature | 负责人 | checkpoint | 阶段 | 迭代 | 最后更新 |",
                        "|---------|--------|-----------|------|------|---------|",
                        "| alpha | owner | discuss_done | 需求澄清 | 2 | 2026-05-25 12:00:00 |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = load_state_json_records_result(workspace)

            self.assertFalse(result.exists)
            self.assertEqual(result.records, {})
            self.assertFalse((workspace / ".autobizdevops" / "state.json").exists())

    def test_json_wins_over_stale_state_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})
            (workspace / ".autobizdevops" / "STATE.md").write_text("stale discuss_done\n", encoding="utf-8")

            result = check_or_fix_state_sync(workspace, fix=True)
            state_md = (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8")

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertIn("prd_done", state_md)
            self.assertNotIn("stale discuss_done", state_md)

    def test_unknown_checkpoint_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": STATE_SCHEMA_VERSION,
                        "features": {
                            "alpha": {
                                **sample_record(),
                                "checkpoint": "unknown_checkpoint",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)

            self.assertTrue(result.ok)
            self.assertIn("alpha", result.record_errors)
            self.assertIn("未知 checkpoint", "\n".join(result.record_errors["alpha"]))


class ArtifactScanTests(unittest.TestCase):
    def test_scan_file_artifact_returns_flat_status_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "PRD.md").write_text("prd", encoding="utf-8")

            artifacts = scan_artifacts(
                feature_dir,
                workspace,
                [{"id": "prd", "label": "PRD文档", "path": "PRD.md"}],
            )

            self.assertEqual(
                artifacts,
                [
                    {
                        "id": "prd",
                        "artifactLabel": "PRD文档",
                        "path": ".autobizdevops/features/alpha/PRD.md",
                        "artifactStatus": "generated",
                        "artifactStatusLabel": "已生成",
                    }
                ],
            )

    def test_scan_missing_file_artifact_keeps_expected_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)

            artifacts = scan_artifacts(
                feature_dir,
                workspace,
                [{"id": "prd", "label": "PRD文档", "path": "PRD.md"}],
            )

            self.assertEqual(
                artifacts,
                [
                    {
                        "id": "prd",
                        "artifactLabel": "PRD文档",
                        "path": ".autobizdevops/features/alpha/PRD.md",
                        "artifactStatus": "missing",
                        "artifactStatusLabel": "未生成",
                    }
                ],
            )

    def test_scan_specs_glob_returns_all_matching_md_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "specs" / "foo").mkdir(parents=True)
            (feature_dir / "specs" / "bar").mkdir(parents=True)
            (feature_dir / "specs" / "foo" / "spec.md").write_text("foo", encoding="utf-8")
            (feature_dir / "specs" / "bar" / "spec.md").write_text("bar", encoding="utf-8")
            (feature_dir / "specs" / "bar" / "notes.txt").write_text("skip", encoding="utf-8")

            artifacts = scan_artifacts(
                feature_dir,
                workspace,
                [{"id": "specs", "label": "行为规格", "path": "specs/**/*.md"}],
            )

            self.assertEqual(
                artifacts,
                [
                    {
                        "id": "specs",
                        "artifactLabel": "行为规格",
                        "paths": [
                            ".autobizdevops/features/alpha/specs/bar/spec.md",
                            ".autobizdevops/features/alpha/specs/foo/spec.md",
                        ],
                        "artifactStatus": "generated",
                        "artifactStatusLabel": "已生成",
                    }
                ],
            )

    def test_scan_code_exploration_cache_glob_returns_json_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            cache_dir = feature_dir / "cache" / "code-exploration" / "repo"
            cache_dir.mkdir(parents=True)
            (cache_dir / "backend.json").write_text("{}", encoding="utf-8")
            (cache_dir / "notes.md").write_text("skip", encoding="utf-8")

            artifacts = scan_artifacts(
                feature_dir,
                workspace,
                [
                    {
                        "id": "code_exploration_cache",
                        "label": "代码探索缓存",
                        "path": "cache/code-exploration/**/*.json",
                    }
                ],
            )

            self.assertEqual(
                artifacts,
                [
                    {
                        "id": "code_exploration_cache",
                        "artifactLabel": "代码探索缓存",
                        "paths": [
                            ".autobizdevops/features/alpha/cache/code-exploration/repo/backend.json"
                        ],
                        "artifactStatus": "generated",
                        "artifactStatusLabel": "已生成",
                    }
                ],
            )

    def test_scan_specs_glob_without_matches_returns_fallback_glob_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)

            artifacts = scan_artifacts(
                feature_dir,
                workspace,
                [{"id": "specs", "label": "行为规格", "path": "specs/**/*.md"}],
            )

            self.assertEqual(
                artifacts,
                [
                    {
                        "id": "specs",
                        "artifactLabel": "行为规格",
                        "paths": [".autobizdevops/features/alpha/specs/**/*.md"],
                        "artifactStatus": "missing",
                        "artifactStatusLabel": "未生成",
                    }
                ],
            )

    def test_scan_rejects_non_specs_glob_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)

            with self.assertRaises(ValueError):
                scan_artifacts(
                    feature_dir,
                    workspace,
                    [{"id": "logs", "path": "logs/**/*.md"}],
                )

    def test_scan_rejects_specs_glob_with_multiple_recursive_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)

            with self.assertRaises(ValueError):
                scan_artifacts(
                    feature_dir,
                    workspace,
                    [{"id": "specs", "path": "specs/**/**/*.md"}],
                )


class StateIntegrationTests(unittest.TestCase):
    def test_plan_done_sync_preserves_existing_rich_plan_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(
                    [
                        "## 任务总览",
                        "| Task ID | 任务 | 依赖 | 覆盖规格/设计项 | 状态 |",
                        "| ------- | ---- | ---- | --------------- | ---- |",
                        "| T001 | one | 无 | REQ-001/SCN-001 / D-001 | 待做 |",
                        "| T002 | two | T001 | REQ-001/SCN-001 / D-001 | 待做 |",
                        "",
                        "## 任务详情",
                        "### Task [T001]: one",
                        "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                        "- **decision_id:** D-001",
                        "- **设计依据:** design.md#D-001",
                        "- **证据依据:** ev_0001",
                        "- **验证命令:** echo one",
                        "- **状态:** 待做",
                        "",
                        "### Task [T002]: two",
                        "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                        "- **decision_id:** D-001",
                        "- **设计依据:** design.md#D-001",
                        "- **证据依据:** ev_0002",
                        "- **验证命令:** echo two",
                        "- **状态:** 待做",
                    ]
                ),
                encoding="utf-8",
            )
            rich_plan = {
                "featureId": "alpha",
                "projectValidationCommands": [
                    {
                        "id": "PROJECT-VAL-001",
                        "argv": PROJECT_VALIDATION_ARGV,
                        "cwd": ".",
                        "kind": "integration_test",
                        "required": True,
                    }
                ],
                "projectCheckEvidenceIds": [],
                "latestProjectCheckEvidenceId": None,
                "tasks": [
                    {
                        "id": "T001",
                        "title": "one",
                        "goal": "deliver one observable behavior",
                        "status": "todo",
                        "deps": [],
                        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                        "implementationPoints": ["update one behavior", "cover one boundary"],
                        "acceptanceCriteria": [
                            {
                                "id": "AC-T001-01",
                                "text": "one behavior is observable",
                                "scenarioRefs": ["#SCN-001"],
                            }
                        ],
                        "workspaceRef": "default",
                        "validationBoundary": "public behavior seam validated by the task command",
                        "nonGoals": ["do not change unrelated behavior"],
                        "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "completionPolicy": "all_required_validations_pass",
                        "validationCommands": [
                            {
                                "id": "VAL-T001-01",
                                "argv": TASK_VALIDATION_ARGV,
                                "cwd": ".",
                                "kind": "behavior_test",
                                "required": True,
                                "covers": ["AC-T001-01"],
                            }
                        ],
                        "expectedFiles": ["src/a.py"],
                        "evidenceIds": ["ev_0001"],
                        "completionEvidenceIds": [],
                        "latestPassEvidenceId": None,
                        "blockers": [],
                    },
                    {
                        "id": "T002",
                        "title": "two",
                        "goal": "deliver two observable behavior",
                        "status": "todo",
                        "deps": ["T001"],
                        "scope": {"modules": ["src"], "entrypoints": [], "pages": [], "dataObjects": []},
                        "implementationPoints": ["update two behavior", "cover two boundary"],
                        "acceptanceCriteria": [
                            {
                                "id": "AC-T002-01",
                                "text": "two behavior is observable",
                                "scenarioRefs": ["#SCN-001"],
                            }
                        ],
                        "workspaceRef": "default",
                        "validationBoundary": "public behavior seam validated by the task command",
                        "nonGoals": ["do not change unrelated behavior"],
                        "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "completionPolicy": "all_required_validations_pass",
                        "validationCommands": [
                            {
                                "id": "VAL-T002-01",
                                "argv": SECOND_TASK_VALIDATION_ARGV,
                                "cwd": ".",
                                "kind": "behavior_test",
                                "required": True,
                                "covers": ["AC-T002-01"],
                            }
                        ],
                        "expectedFiles": ["src/b.py"],
                        "evidenceIds": ["ev_0002"],
                        "completionEvidenceIds": [],
                        "latestPassEvidenceId": None,
                        "blockers": [],
                    },
                ],
            }
            tasks = rich_plan.pop("tasks")
            rich_plan.update(
                {
                    "status": "todo",
                    "taskSetStatus": "finalized",
                    "activeBatchId": "B001",
                    "nextBatchId": None,
                    "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
                    "batchValidationProfiles": {
                        "backend": {
                            "commands": [
                                {
                                    "argv": BATCH_VALIDATION_ARGV,
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
                            "title": "capability",
                            "specRoots": ["specs/capability/spec.md"],
                            "executionLane": "backend",
                            "deps": [],
                            "taskIds": ["T001", "T002"],
                            "status": "todo",
                        }
                    ],
                }
            )
            (feature_dir / "plan.json").write_text(json.dumps(rich_plan, ensure_ascii=False), encoding="utf-8")
            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            batch_path.parent.mkdir(parents=True)
            batch_path.write_text(
                json.dumps(
                    {
                        "featureId": "alpha",
                        "batchId": "B001",
                        "title": "capability",
                        "executionLane": "backend",
                        "status": "todo",
                        "taskCount": 2,
                        "completedTaskCount": 0,
                        "completionEvidenceIds": [],
                        "batchValidation": {
                            "profile": "backend",
                            "status": "pending",
                            "commands": [
                                {
                                    "id": "BATCH-B001-VAL-001",
                                    "argv": BATCH_VALIDATION_ARGV,
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
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            synced, error = validate_plan_json_for_checkpoint(workspace=workspace, feature="alpha", checkpoint="plan_done")

            self.assertTrue(synced, error)
            preserved = json.loads(batch_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["tasks"][1]["deps"], ["T001"])
            self.assertEqual(preserved["tasks"][1]["expectedFiles"], ["src/b.py"])

    def test_plan_done_validation_rejects_legacy_plan_without_task_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            legacy_plan = {
                "version": 1,
                "featureId": "alpha",
                "tasks": [
                    {
                        "id": "T001",
                        "title": "one",
                        "status": "todo",
                        "deps": [],
                        "specRefs": ["specs/capability/spec.md#REQ-001", "#SCN-001"],
                        "designRefs": ["design.md#D-001"],
                        "apiIds": [],
                        "dataIds": [],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": "echo one"}],
                        "expectedFiles": [],
                        "evidenceIds": [],
                        "blockers": [],
                    }
                ],
            }
            (feature_dir / "plan.json").write_text(json.dumps(legacy_plan, ensure_ascii=False), encoding="utf-8")

            synced, error = validate_plan_json_for_checkpoint(workspace=workspace, feature="alpha", checkpoint="plan_done")

            self.assertFalse(synced)
            self.assertIn("legacy_plan_requires_rebuild", error)

    def test_plan_done_validation_rejects_missing_plan_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(
                    [
                        "## 任务总览",
                        "| Task ID | 任务 | 依赖 | 覆盖规格/设计项 | 状态 |",
                        "| ------- | ---- | ---- | --------------- | ---- |",
                        "| T001 | one | 无 | REQ-001/SCN-001 / D-001 | 待做 |",
                        "| T002 | two | T001 | REQ-001/SCN-001 / D-001 | 待做 |",
                        "",
                        "## 任务详情",
                        "### Task [T001]: one",
                        "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                        "- **decision_id:** D-001",
                        "- **设计依据:** design.md#D-001",
                        "- **证据依据:** ev_0001",
                        "- **验证命令:** echo one",
                        "- **状态:** 待做",
                        "",
                        "### Task [T002]: two",
                        "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                        "- **decision_id:** D-001",
                        "- **设计依据:** design.md#D-001",
                        "- **证据依据:** ev_0002",
                        "- **验证命令:** echo two",
                        "- **状态:** 待做",
                    ]
                ),
                encoding="utf-8",
            )

            synced, error = validate_plan_json_for_checkpoint(workspace=workspace, feature="alpha", checkpoint="plan_done")

            self.assertFalse(synced)
            self.assertIn("缺少", error)
            self.assertFalse((feature_dir / "plan.json").exists())

    def test_update_checkpoint_cli_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
                cwd=str(feature_dir),
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state_json = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            state_md = (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8")
            self.assertEqual(state_json["features"]["alpha"]["checkpoint"], "discuss_in_progress")
            self.assertIn("discuss_in_progress", state_md)

    def test_update_existing_feature_keeps_lifecycle_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("discuss_in_progress")})
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "PRD_DISCUSS.md").write_text("discussion", encoding="utf-8")
            write_non_ui_context(feature_dir, locked=False)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--checkpoint",
                    "discuss_done",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state_json = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_json["features"]["alpha"]["checkpoint"], "discuss_done")

    def test_update_checkpoint_cli_allows_code_done_after_validation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = make_workspace(root)
            write_state_records(workspace, {"alpha": sample_record("code_in_progress")})
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            write_minimal_trace_sources(feature_dir)
            write_non_ui_context(feature_dir)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(
                    [
                        "### Task [T001]: Implement",
                        "- **状态:** 完成",
                        "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                        "- **api_id:** API-001",
                        "- **data_id:** DATA-001",
                        "- **decision_id:** D-001",
                        "- **设计依据:** design.md#API-001 / #DATA-001 / #D-001",
                        "- **证据依据:** ev_0001",
                    ]
                ),
                encoding="utf-8",
            )
            write_done_plan_json_and_evidence(feature_dir)

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--checkpoint",
                    "code_done",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state_json = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_json["features"]["alpha"]["checkpoint"], "code_done")

    def test_update_checkpoint_cli_rejects_workspace_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("不接受 --workspace/-w", result.stderr)

    def test_update_checkpoint_cli_requires_plugin_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--feature",
                    "alpha",
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env_without(workspace, "PLUGIN_WORKSPACE"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("PLUGIN_WORKSPACE 未设置", result.stderr)

    def test_update_checkpoint_cli_requires_project_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env_without(workspace, "PROJECT_DIR"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("PROJECT_DIR 未设置", result.stderr)

    def test_update_checkpoint_cli_requires_feature_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env_without(workspace, "FEATURE_ID"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("FEATURE_ID 未设置", result.stderr)

    def test_update_checkpoint_cli_rejects_feature_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--feature",
                    "beta",
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace, feature="alpha"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("--feature 与 FEATURE_ID 不一致", result.stderr)

    def test_inspect_uses_json_when_markdown_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_workspace(root / "collection")
            project.rename(root / "collection" / "proj")
            project = root / "collection" / "proj"
            write_state_records(project, {"alpha": sample_record("prd_done")})
            (project / ".autobizdevops" / "STATE.md").write_text("stale discuss_done\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "inspect_state.py"),
                    "--workspace",
                    str(root / "collection"),
                    "--project",
                    "proj",
                    "--mode",
                    "run",
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["run"]["currentNodeId"], "biz.prd")
            self.assertIn("prd_done", (project / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"))

    def test_inspect_workflow_states_include_configured_next_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_workspace(root / "collection")
            project.rename(root / "collection" / "proj")
            project = root / "collection" / "proj"
            write_state_records(project, {"alpha": sample_record("code_in_progress")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "inspect_state.py"),
                    "--workspace",
                    str(root / "collection"),
                    "--project",
                    "proj",
                    "--mode",
                    "run",
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("workflow", payload)
            self.assertNotIn("templates", payload["workflow"])
            self.assertIn("nodes", payload["run"])
            workflow_nodes = {node["id"]: node for node in payload["workflow"]["nodes"]}

            def next_action(node_id: str, state_id: str) -> dict:
                states = {state["id"]: state for state in workflow_nodes[node_id]["states"]}
                return states[state_id]["nextAction"]

            self.assertEqual(
                next_action("biz.discuss", "not_started")["slashSkill"],
                "autobiz-requirement-discuss",
            )
            self.assertEqual(next_action("biz.discuss", "done")["slashSkill"], "autobiz-prd-generate")
            self.assertEqual(next_action("dev.code", "in_progress")["slashSkill"], "autodev-code")
            self.assertEqual(next_action("dev.code", "done")["slashSkill"], "autodev-reviewer")
            self.assertEqual(next_action("ops.archive", "archived")["slashSkill"], "autoops-archive")
            self.assertEqual(
                next_action("ops.archive", "archived")["userMessage"],
                "请使用 /autoops-archive 查看当前 Feature 的归档状态。",
            )

    def test_workflow_shell_rejects_state_missing_next_action(self) -> None:
        config = load_board_config(ROOT / "board_core" / "board_config.json")
        config["workflow"]["nodes"][0]["states"][0].pop("nextAction")

        with self.assertRaisesRegex(
            BoardConfigError,
            r"biz\.discuss\.states\[0\]\.nextAction must be an object",
        ):
            build_workflow_shell(config)

    def test_inspect_run_returns_paths_for_specs_glob_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_workspace(root / "collection")
            project.rename(root / "collection" / "proj")
            project = root / "collection" / "proj"
            feature_dir = project / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "specs" / "foo").mkdir(parents=True)
            (feature_dir / "specs" / "bar").mkdir(parents=True)
            (feature_dir / "proposal.md").write_text("proposal", encoding="utf-8")
            (feature_dir / "specs" / "foo" / "spec.md").write_text("foo", encoding="utf-8")
            (feature_dir / "specs" / "bar" / "spec.md").write_text("bar", encoding="utf-8")
            write_state_records(project, {"alpha": sample_record("specs_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "inspect_state.py"),
                    "--workspace",
                    str(root / "collection"),
                    "--project",
                    "proj",
                    "--mode",
                    "run",
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            nodes = {node["id"]: node for node in payload["run"]["nodes"]}
            specs_artifact = next(
                artifact for artifact in nodes["dev.specs"]["artifacts"] if artifact["id"] == "specs"
            )
            self.assertNotIn("path", specs_artifact)
            self.assertEqual(specs_artifact["artifactLabel"], "行为规格")
            self.assertEqual(
                specs_artifact["paths"],
                [
                    ".autobizdevops/features/alpha/specs/bar/spec.md",
                    ".autobizdevops/features/alpha/specs/foo/spec.md",
                ],
            )
            self.assertEqual(specs_artifact["artifactStatus"], "generated")
            self.assertEqual(specs_artifact["artifactStatusLabel"], "已生成")

    def test_read_state_json_cli_reads_specific_feature_without_repairing_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})
            (workspace / ".autobizdevops" / "STATE.md").write_text("stale discuss_done\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "prd_done\n")
            self.assertEqual(result.stderr, "")
            self.assertEqual(
                (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"),
                "stale discuss_done\n",
            )

    def test_read_state_json_cli_reports_missing_feature_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                    "--feature",
                    "beta",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace, feature="beta"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("feature 'beta' 未在 state.json 中找到", result.stderr)

    def test_read_state_json_cli_without_feature_keeps_records_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["records"]["alpha"]["checkpoint"], "prd_done")

    def test_read_state_json_cli_accepts_legacy_project_code(self) -> None:
        # 平台过渡期兼容：只下发旧变量 PROJECT_CODE（无 PROJECT_DIR）时仍能解析工作区。
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            env = plugin_env(workspace)
            env.pop("PROJECT_DIR", None)
            env["PROJECT_CODE"] = workspace.name

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["records"]["alpha"]["checkpoint"], "prd_done")

    def test_read_state_json_cli_rejects_workspace_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("不接受 --workspace/-w", result.stderr)

    def test_read_state_json_cli_requires_plugin_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env_without(workspace, "PLUGIN_WORKSPACE"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("PLUGIN_WORKSPACE 未设置", result.stderr)

    def test_read_state_json_cli_requires_feature_id_when_feature_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env_without(workspace, "FEATURE_ID"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("FEATURE_ID 未设置", result.stderr)

    def test_read_state_json_cli_rejects_feature_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "read_state_json.py"),
                    "--feature",
                    "beta",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=plugin_env(workspace, feature="alpha"),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("--feature 与 FEATURE_ID 不一致", result.stderr)

    def test_direct_state_file_edits_are_blocked_by_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            for state_file in (".autobizdevops/state.json", ".autobizdevops/STATE.md"):
                payload = {
                    "tool_name": "write_file",
                    "cwd": str(workspace),
                    "tool_input": {
                        "file_path": state_file,
                        "content": "{}",
                    },
                }

                result = subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "state_checkpoint.py"), "state-done"],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("state.json 是主事实源", result.stderr)
                self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_code_done_does_not_assume_state_workspace_is_code_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = make_workspace(root)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            write_minimal_trace_sources(feature_dir)
            write_non_ui_context(feature_dir)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(
                    [
                        "### Task [T001]: Implement",
                        "- **状态:** 完成",
                        "- **规格依据:** specs/capability/spec.md#REQ-001 / #SCN-001",
                        "- **api_id:** API-001",
                        "- **data_id:** DATA-001",
                        "- **decision_id:** D-001",
                        "- **设计依据:** design.md#API-001 / #DATA-001 / #D-001",
                        "- **证据依据:** ev_0001",
                    ]
                ),
                encoding="utf-8",
            )
            write_done_plan_json_and_evidence(feature_dir)
            write_state_records(workspace, {"alpha": sample_record("code_in_progress")})

            result = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="code_done",
            )

            self.assertTrue(result.ok, "\n".join(result.errors))

    def test_init_workspace_and_create_feature_use_json_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            project.mkdir()

            init_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "init_workspace.py"),
                    "--mode",
                    "createProject",
                    "--workspace",
                    str(project),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            create_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "init_workspace.py"),
                    "--mode",
                    "createFeature",
                    "--workspace",
                    str(root),
                    "--project",
                    "proj",
                    "--feature",
                    "beta",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(create_result.returncode, 0, create_result.stderr)
            state_json = json.loads((project / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_json["schemaVersion"], STATE_SCHEMA_VERSION)
            self.assertEqual(state_json["features"]["beta"]["checkpoint"], "discuss_in_progress")
            self.assertIn("beta", (project / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"))

    def test_create_feature_rejects_new_custom_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            project.mkdir()

            init_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "init_workspace.py"),
                    "--mode",
                    "createProject",
                    "--workspace",
                    str(project),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            create_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "init_workspace.py"),
                    "--mode",
                    "createFeature",
                    "--workspace",
                    str(root),
                    "--project",
                    "proj",
                    "--feature",
                    "beta",
                    "--workflow-template",
                    "custom",
                    "--workflow-nodes",
                    json.dumps(["dev.specs"], ensure_ascii=False),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(create_result.returncode, 1)
            self.assertIn("workflow template 已不可用于新建 Feature: custom", create_result.stderr)
            state_json = json.loads((project / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            self.assertNotIn("beta", state_json["features"])


if __name__ == "__main__":
    unittest.main()
