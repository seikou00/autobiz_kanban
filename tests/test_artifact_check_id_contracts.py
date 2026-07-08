from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import json
import subprocess
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "skills" / "autodev" / "hooks"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from artifact_check import (  # noqa: E402
    HookContext,
    validate_code_done_gate,
    validate_e2e_result_json,
    validate_design_contract,
    validate_e2e_report_contract,
    validate_fix_request_json,
    validate_plan_json_contract,
    validate_plan_json_initial_tasks,
    validate_plan_finished_tasks,
    validate_plan_scenario_coverage,
    validate_plan_task_detail_schema,
    validate_plan_ui_projection,
    validate_plan_task_granularity,
    validate_review_findings_json,
    validate_smoke_result_json,
    validate_smoke_test_plan_json,
    validate_specs_contract,
    validate_unit_test_result_json,
    validate_unit_test_report_contract,
    validate_ui_context_json,
    validate_verify_decision_json,
    validate_verify_report_contract,
)
from board_core.contracts import BoardConfigError  # noqa: E402
from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.plan_json import write_plan_json  # noqa: E402
from hooks.ui_context import validate_ui_context_data  # noqa: E402


class ArtifactCheckIdContractsTest(unittest.TestCase):
    def _ctx(self, feature_dir: Path) -> HookContext:
        root = feature_dir.parent.parent.parent
        return HookContext(skill="autodev-sample", slug="alpha", root=root)

    def _plan_ctx(self, feature_dir: Path) -> HookContext:
        root = feature_dir.parent.parent.parent
        return HookContext(
            skill="autodev-sample",
            slug="alpha",
            root=root,
            required_inputs=("plan.json",),
        )

    def _required_output_ctx(self, feature_dir: Path, artifact: str) -> HookContext:
        root = feature_dir.parent.parent.parent
        return HookContext(
            skill="autodev-sample",
            slug="alpha",
            root=root,
            required_outputs=(artifact,),
        )

    def _ui_required_ctx(self, feature_dir: Path, *artifacts: str) -> HookContext:
        root = feature_dir.parent.parent.parent
        return HookContext(
            skill="autodev-sample",
            slug="alpha",
            root=root,
            required_inputs=("UI_CONTEXT.json",),
            required_outputs=artifacts,
        )

    def _feature_dir(self, tmp: str) -> Path:
        feature_dir = Path(tmp) / ".autobizdevops" / "features" / "alpha"
        feature_dir.mkdir(parents=True, exist_ok=True)
        return feature_dir

    def _write_specs(self, feature_dir: Path, *, duplicate: bool = False) -> None:
        (feature_dir / "specs" / "cap").mkdir(parents=True, exist_ok=True)
        lines = [
            "## ADDED Requirements",
            "### Requirement [REQ-001]: capability",
            "#### Scenario [SCN-001]: happy path",
        ]
        if duplicate:
            lines.extend(
                [
                    "### Requirement [REQ-001]: duplicate",
                    "#### Scenario [SCN-001]: duplicate",
                ]
            )
        (feature_dir / "specs" / "cap" / "spec.md").write_text("\n".join(lines), encoding="utf-8")

    def _write_plan_json_and_evidence(
        self,
        feature_dir: Path,
        *,
        ui_task: bool = False,
        e2e_evidence: bool = False,
    ) -> None:
        spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]
        write_plan_json(
            feature_dir / "plan.json",
            {
                "version": 1,
                "taskDetailVersion": 1,
                "featureId": "alpha",
                "tasks": [
                    {
                        "id": "T001",
                        "title": "do",
                        "goal": "deliver observable behavior",
                        "status": "done",
                        "deps": [],
                        "scope": {
                            "modules": ["src"],
                            "entrypoints": ["POST /api/alpha"],
                            "pages": ["PAGE-001"] if ui_task else [],
                            "dataObjects": ["DATA-001"],
                        },
                        "implementationPoints": ["update the behavior", "cover the boundary"],
                        "acceptanceCriteria": ["the behavior is observable"],
                        "nonGoals": ["do not change unrelated behavior"],
                        **(
                            {
                                "uiRequired": True,
                                "uiRefs": {
                                    "pageRefs": ["PAGE-001"],
                                    "interactionRefs": ["UIX-001"],
                                    "visualSourceRefs": ["VIS-001"],
                                    "frontendRoute": "absolute-html",
                                },
                            }
                            if ui_task
                            else {}
                        ),
                        "specRefs": spec_refs,
                        "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
                        "apiIds": ["API-001"],
                        "dataIds": ["DATA-001"],
                        "decisionIds": ["D-001"],
                        "validationCommands": [{"command": "echo ok"}],
                        "expectedFiles": [],
                        "evidenceIds": ["ev_0001"],
                        "blockers": [],
                    }
                ],
            },
        )
        node_id = "dev.e2e" if e2e_evidence else "dev.code"
        skill = "autodev-e2e" if e2e_evidence else "autodev-code"
        checkpoint = "e2e_in_progress" if e2e_evidence else "code_in_progress"
        append_evidence(
            feature_dir,
            {
                "featureId": "alpha",
                "checkpoint": checkpoint,
                "nodeId": node_id,
                "skill": skill,
                "taskId": "T001",
                "action": "validation",
                "specRefs": spec_refs,
                "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
                "changedFiles": ["src/foo.py"],
                "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
            },
        )

    def _write_plan_json(
        self,
        feature_dir: Path,
        *,
        status: str = "done",
        spec_refs: list[str] | None = None,
        design_refs: list[str] | None = None,
        api_ids: list[str] | None = None,
        data_ids: list[str] | None = None,
        decision_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        blockers: list[str] | None = None,
        extra_task_fields: dict | None = None,
    ) -> None:
        task = {
            "id": "T001",
            "title": "do",
            "goal": "deliver observable behavior",
            "status": status,
            "deps": [],
            "scope": {
                "modules": ["src"],
                "entrypoints": ["POST /api/alpha"] if api_ids else [],
                "pages": [],
                "dataObjects": ["DATA-001"] if data_ids else [],
            },
            "implementationPoints": ["update the behavior", "cover the boundary"],
            "acceptanceCriteria": ["the behavior is observable"],
            "nonGoals": ["do not change unrelated behavior"] if api_ids else [],
            "specRefs": spec_refs or ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
            "designRefs": design_refs or ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
            "apiIds": [] if api_ids is None else api_ids,
            "dataIds": [] if data_ids is None else data_ids,
            "decisionIds": ["D-001"] if decision_ids is None else decision_ids,
            "validationCommands": [{"command": "echo ok"}],
            "expectedFiles": [],
            "evidenceIds": ["ev_0001"] if evidence_ids is None else evidence_ids,
            "blockers": [] if blockers is None else blockers,
        }
        if extra_task_fields:
            task.update(extra_task_fields)
        write_plan_json(
            feature_dir / "plan.json",
            {
                "version": 1,
                "taskDetailVersion": 1,
                "featureId": "alpha",
                "tasks": [task],
            },
        )

    def _write_smoke_plan(
        self,
        feature_dir: Path,
        *,
        tests: list[dict] | None = None,
        flow_blocking: bool = False,
    ) -> None:
        normalized_tests: list[dict] | None = None
        if tests is not None:
            normalized_tests = []
            for raw_test in tests:
                item = dict(raw_test)
                item.setdefault(
                    "seam",
                    {
                        "type": "api",
                        "entrypoint": "GET /health",
                        "observable": "HTTP 200 response",
                    },
                )
                item.setdefault(
                    "verticalSlice",
                    {
                        "trigger": "call the public smoke endpoint",
                        "expectedOutcome": "the endpoint returns a successful response",
                    },
                )
                item.setdefault(
                    "mockPolicy",
                    {
                        "externalOnly": True,
                        "allowedMocks": [],
                    },
                )
                normalized_tests.append(item)
        self._write_json(
            feature_dir,
            "SMOKE_TEST_PLAN.json",
            {
                "version": 1,
                "featureId": "alpha",
                "flowBlocking": flow_blocking,
                "skipReason": "" if tests else "no smoke needed",
                "tests": normalized_tests if normalized_tests is not None else [],
            },
        )

    def _write_ui_context(self, feature_dir: Path, *, ui_required: bool = True) -> None:
        self._write_json(
            feature_dir,
            "UI_CONTEXT.json",
            {
                "version": 1,
                "featureId": "alpha",
                "uiRequired": ui_required,
                "decisionStatus": "locked",
                "decisionSource": "user_confirmed" if ui_required else "default_false",
                "confirmedAtCheckpoint": "prd_done",
                "lockedAtCheckpoint": "specs_done",
                "notApplicableReason": "" if ui_required else "纯后端能力",
                "pages": [
                    {"pageId": "PAGE-001", "name": "页面", "goal": "展示能力", "states": ["success"]}
                ] if ui_required else [],
                "interactions": [
                    {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "点击提交"}
                ] if ui_required else [],
                "visualSources": [
                    {
                        "sourceId": "VIS-001",
                        "type": "high_fidelity_html",
                        "path": ".autobizdevops/features/alpha/frontend-html/page.html",
                        "route": "absolute-html",
                        "required": True,
                    }
                ] if ui_required else [],
                "capabilities": [
                    {
                        "capabilityId": "alpha-ui",
                        "uiRequired": True,
                        "pageRefs": ["PAGE-001"],
                        "interactionRefs": ["UIX-001"],
                        "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                    }
                ] if ui_required else [],
            },
        )

    def _write_json(self, feature_dir: Path, name: str, payload: dict) -> None:
        (feature_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _init_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, text=True, capture_output=True)

    def _git_ignore_path(self, root: Path, path: str) -> None:
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{path}\n")

    def _write_design(
        self,
        feature_dir: Path,
        *,
        no_http_api: bool = True,
        no_sql: bool = True,
        include_api: bool = True,
        include_data: bool = True,
        api_ids: list[str] | None = None,
        data_ids: list[str] | None = None,
        decision_ids: list[str] | None = None,
    ) -> None:
        api_ids = api_ids or (["API-001"] if include_api else [])
        data_ids = data_ids or (["DATA-001"] if include_data else [])
        decision_ids = decision_ids or ["D-001"]
        coverage = [*api_ids, *data_ids, *decision_ids]
        api_rows = (
            [f"| {api_id} | 无 | 无 | 无 | 无 | 无 | 无 | 已确认 |" for api_id in api_ids]
            if api_ids
            else ["| 无决策项 | 无 | 无 | 无 | 无 | 无 | 无 | 已确认 |"]
        )
        data_rows = (
            [f"| {data_id} | 无 | 无 | 无 | 无 | 无 | 已确认 |" for data_id in data_ids]
            if data_ids
            else ["| 无决策项 | 无 | 无 | 无 | 无 | 无 | 已确认 |"]
        )
        decision_rows = [
            f"| {decision_id} | no-op | no-op | none | 已确认 |"
            for decision_id in decision_ids
        ]
        (feature_dir / "design.md").write_text(
            "\n".join(
                [
                    "# 技术设计: cap",
                    "## 1. Context / 输入上下文",
                    "## 2. Spec Traceability / 规格追踪",
                    "| Spec | Requirement / Scenario | Design Coverage |",
                    "|------|------------------------|-----------------|",
                    f"| specs/cap/spec.md | Requirement [REQ-001] / Scenario [SCN-001] | {' / '.join(coverage)} |",
                    "## 3. API Decisions / 接口决策",
                    f"- x-auto-no-http-api: {str(no_http_api).lower()}",
                    "| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |",
                    "|----|--------|--------------|---------|----------|--------|-------------------|--------|",
                    *api_rows,
                    "## 4. Data Decisions / 数据决策",
                    f"- x-auto-no-sql: {str(no_sql).lower()}",
                    "| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |",
                    "|----|-------------|--------|--------|-----------------|----------|--------|",
                    *data_rows,
                    "## 5. Technical Design / 技术设计",
                    "### Decisions",
                    "| ID | Decision | Rationale | Alternatives | Status |",
                    "|----|----------|-----------|--------------|--------|",
                    *decision_rows,
                    "## 6. Risks / Open Questions",
                    "| ID | Type | Description | Impact | Owner/Next Step |",
                    "|----|------|-------------|--------|-----------------|",
                    "| R-001 | 风险 | none | low | none |",
                ]
            ),
            encoding="utf-8",
        )

    def _write_plan(
        self,
        feature_dir: Path,
        *,
        spec_ref: str = "REQ-001 / #SCN-001",
        design_ref: str = "design.md#API-001 / #DATA-001 / #D-001",
        api_id: str = "API-001",
        data_id: str = "DATA-001",
        decision_id: str = "D-001",
        use_structured_ids: bool = False,
    ) -> None:
        task_lines = [
            "### Task [T001]: do",
            "- **做什么:** do",
            f"- **规格依据:** specs/cap/spec.md#{spec_ref}",
        ]
        if use_structured_ids:
            task_lines.extend(
                [
                    f"- **api_id:** {api_id}",
                    f"- **data_id:** {data_id}",
                    f"- **decision_id:** {decision_id}",
                    f"- **设计依据:** {design_ref}",
                ]
            )
        else:
            task_lines.extend(
                [
                    f"- **设计依据:** {design_ref}",
                ]
            )
        task_lines.extend(
            [
                "- **证据依据:** ev_0001",
                "- **验证方法:** echo ok",
                "- **状态:** 完成",
            ]
        )
        (feature_dir / "PLAN.md").write_text(
            "\n".join(
                [
                    "# 执行计划: cap",
                    *task_lines,
                    "## Specs 行为覆盖",
                    "| Spec Requirement / Scenario | 覆盖任务 | 验证方法 |",
                    "| --------------------------- | -------- | -------- |",
                    "| REQ-001 / SCN-001 | T001 | echo ok |",
                ]
            ),
            encoding="utf-8",
        )

    def test_specs_contract_requires_stable_requirement_and_scenario_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            (feature_dir / "specs" / "cap").mkdir(parents=True, exist_ok=True)
            (feature_dir / "specs" / "cap" / "spec.md").write_text(
                "\n".join(
                    [
                        "## ADDED Requirements",
                        "### Requirement: legacy",
                        "#### Scenario: legacy",
                    ]
                ),
                encoding="utf-8",
            )
            ctx = self._ctx(feature_dir)
            self.assertGreater(validate_specs_contract(ctx), 0)

    def test_plan_design_and_unit_test_reports_require_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            (feature_dir / "UNIT_TEST_REPORT.md").write_text(
                "\n".join(
                    [
                        "# Unit Test Report",
                        "- **Feature:** alpha",
                        "- **Mode:** auto",
                        "- **Generated At:** 2026-06-24 00:00:00",
                        "- **Verdict:** PASS",
                        "- **Test Log:** test-output.log",
                        "## Test Plan",
                        "| ID | Source | Behavior | Test Target | Priority | Status |",
                        "|----|--------|----------|-------------|----------|--------|",
                        "| UT-001 | specs/cap/spec.md#REQ-001 / #SCN-001 | cap | FooTest#ok | P0 | planned |",
                        "## Execution Summary",
                        "## Coverage Matrix",
                        "| Source | Requirement | Test | Result | Evidence |",
                        "|--------|-------------|------|--------|----------|",
                        "| specs/cap/spec.md#REQ-001 / #SCN-001 | cap | FooTest#ok | PASS | ev_0001 |",
                        "## Failure Analysis",
                        "## Fix Attempts",
                        "| ID | Classification | Files Changed | Hypothesis | Command | Result |",
                        "|----|----------------|---------------|------------|---------|--------|",
                        "| T001 | source_bug | src/foo.py | fix | echo ok | pass |",
                        "## Commands",
                        "## Handoff",
                    ]
                ),
                encoding="utf-8",
            )
            (feature_dir / "test-output.log").write_text("2026-06-24 ok\n", encoding="utf-8")
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )
            ctx = self._ctx(feature_dir)
            self.assertEqual(validate_design_contract(ctx), 0)
            self.assertEqual(validate_plan_finished_tasks(self._plan_ctx(feature_dir)), 0)
            self.assertEqual(validate_unit_test_report_contract(ctx), 0)

    def test_plan_accepts_structured_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json(feature_dir)
            ctx = self._plan_ctx(feature_dir)
            self.assertEqual(validate_plan_finished_tasks(ctx), 0)

    def test_plan_accepts_multi_value_structured_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(
                feature_dir,
                no_http_api=False,
                no_sql=False,
                api_ids=["API-001", "API-002"],
                data_ids=["DATA-001", "DATA-002"],
                decision_ids=["D-001", "D-002"],
            )
            self._write_plan_json(
                feature_dir,
                design_refs=["design.md#API-001", "design.md#API-002", "design.md#DATA-001", "design.md#DATA-002", "design.md#D-001", "design.md#D-002"],
                api_ids=["API-001", "API-002"],
                data_ids=["DATA-001", "DATA-002"],
                decision_ids=["D-001", "D-002"],
            )
            ctx = self._plan_ctx(feature_dir)
            self.assertEqual(validate_plan_finished_tasks(ctx), 0)

    def test_ui_context_json_accepts_locked_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_ui_context(feature_dir)

            self.assertEqual(validate_ui_context_json(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_ui_context_json_rejects_unlocked_required_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_json(
                feature_dir,
                "UI_CONTEXT.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "uiRequired": False,
                    "decisionStatus": "confirmed",
                    "decisionSource": "default_false",
                    "confirmedAtCheckpoint": "prd_done",
                    "notApplicableReason": "纯后端能力",
                    "pages": [],
                    "interactions": [],
                    "visualSources": [],
                    "capabilities": [],
                },
            )

            self.assertGreater(validate_ui_context_json(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_ui_context_capability_spec_refs_optional_before_locked(self) -> None:
        data = {
            "version": 1,
            "featureId": "alpha",
            "uiRequired": True,
            "decisionStatus": "confirmed",
            "decisionSource": "user_confirmed",
            "confirmedAtCheckpoint": "prd_done",
            "notApplicableReason": "",
            "pages": [
                {"pageId": "PAGE-001", "name": "页面", "goal": "展示能力"}
            ],
            "interactions": [
                {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "点击提交"}
            ],
            "visualSources": [],
            "capabilities": [
                {
                    "capabilityId": "alpha-ui",
                    "uiRequired": True,
                    "pageRefs": ["PAGE-001"],
                    "interactionRefs": ["UIX-001"],
                }
            ],
        }

        self.assertEqual(validate_ui_context_data(data, feature_id="alpha", require_confirmed=True), [])
        self.assertIn(
            "capabilities[0].specRefs_must_be_string_array",
            validate_ui_context_data(data, feature_id="alpha", require_locked=True),
        )

    def test_ui_context_locked_ui_requires_capability_scenario_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            payload = {
                "version": 1,
                "featureId": "alpha",
                "uiRequired": True,
                "decisionStatus": "locked",
                "decisionSource": "user_confirmed",
                "confirmedAtCheckpoint": "prd_done",
                "lockedAtCheckpoint": "specs_done",
                "notApplicableReason": "",
                "pages": [
                    {"pageId": "PAGE-001", "name": "页面", "goal": "展示能力"}
                ],
                "interactions": [
                    {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "点击提交"}
                ],
                "visualSources": [],
                "capabilities": [],
            }
            self.assertIn(
                "ui_context_locked_without_ui_capability",
                validate_ui_context_data(payload, feature_id="alpha", require_locked=True),
            )
            self._write_json(
                feature_dir,
                "UI_CONTEXT.json",
                payload,
            )

            self.assertGreater(validate_ui_context_json(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_plan_ui_projection_accepts_task_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "ui",
                            "status": "todo",
                            "deps": [],
                            "uiRequired": True,
                            "uiRefs": {
                                "pageRefs": ["PAGE-001"],
                                "interactionRefs": ["UIX-001"],
                                "visualSourceRefs": ["VIS-001"],
                                "frontendRoute": "absolute-html",
                            },
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
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
            )

            self.assertEqual(validate_plan_ui_projection(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_plan_ui_projection_rejects_visual_route_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "ui",
                            "status": "todo",
                            "deps": [],
                            "uiRequired": True,
                            "uiRefs": {
                                "pageRefs": ["PAGE-001"],
                                "interactionRefs": ["UIX-001"],
                                "visualSourceRefs": ["VIS-001"],
                                "frontendRoute": "standard-html",
                            },
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
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
            )

            self.assertGreater(validate_plan_ui_projection(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_plan_ui_projection_accepts_spec_driven_without_visual_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            data = json.loads((feature_dir / "UI_CONTEXT.json").read_text(encoding="utf-8"))
            data["visualSources"] = []
            self._write_json(feature_dir, "UI_CONTEXT.json", data)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "ui",
                            "status": "todo",
                            "deps": [],
                            "uiRequired": True,
                            "uiRefs": {
                                "pageRefs": ["PAGE-001"],
                                "interactionRefs": ["UIX-001"],
                                "visualSourceRefs": [],
                                "frontendRoute": "spec-driven-ui",
                            },
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
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
            )

            self.assertEqual(validate_plan_ui_projection(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_plan_ui_projection_rejects_ui_task_when_feature_not_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir, ui_required=False)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "bad ui",
                            "status": "todo",
                            "deps": [],
                            "uiRequired": True,
                            "uiRefs": {"pageRefs": ["PAGE-001"], "interactionRefs": [], "visualSourceRefs": [], "frontendRoute": "spec-driven-ui"},
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
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
            )

            self.assertGreater(validate_plan_ui_projection(self._required_output_ctx(feature_dir, "UI_CONTEXT.json")), 0)

    def test_plan_ui_projection_rejects_ui_refs_on_non_ui_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "backend",
                            "status": "todo",
                            "deps": [],
                            "uiRequired": False,
                            "uiRefs": {
                                "pageRefs": ["PAGE-001"],
                                "interactionRefs": ["UIX-001"],
                                "visualSourceRefs": [],
                                "frontendRoute": "spec-driven-ui",
                            },
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
                            "apiIds": ["API-001"],
                            "dataIds": ["DATA-001"],
                            "decisionIds": ["D-001"],
                            "validationCommands": [{"command": "echo ok"}],
                            "expectedFiles": [],
                            "evidenceIds": [],
                            "blockers": [],
                        },
                        {
                            "id": "T002",
                            "title": "ui",
                            "status": "todo",
                            "deps": [],
                            "uiRequired": True,
                            "uiRefs": {
                                "pageRefs": ["PAGE-001"],
                                "interactionRefs": ["UIX-001"],
                                "visualSourceRefs": [],
                                "frontendRoute": "spec-driven-ui",
                            },
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
                            "apiIds": ["API-001"],
                            "dataIds": ["DATA-001"],
                            "decisionIds": ["D-001"],
                            "validationCommands": [{"command": "echo ok"}],
                            "expectedFiles": [],
                            "evidenceIds": [],
                            "blockers": [],
                        },
                    ],
                },
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                failures = validate_plan_ui_projection(self._required_output_ctx(feature_dir, "UI_CONTEXT.json"))

            self.assertGreater(failures, 0)
            self.assertIn("plan_ui_refs_for_non_ui_task", output.getvalue())

    def test_plan_accepts_data_none_even_if_summary_mentions_data_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir, include_data=False, no_sql=True)
            self._write_plan_json(
                feature_dir,
                design_refs=["design.md#API-001", "design.md#D-001"],
                data_ids=[],
            )
            ctx = self._plan_ctx(feature_dir)
            self.assertEqual(validate_plan_finished_tasks(ctx), 0)

    def test_plan_json_contract_degrades_when_plan_not_in_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)

            self.assertEqual(validate_plan_json_contract(self._ctx(feature_dir)), 0)

            (feature_dir / "PLAN.md").write_text("# stale human plan\n", encoding="utf-8")
            self.assertGreater(validate_plan_json_contract(self._ctx(feature_dir)), 0)

    def test_design_escape_hatches_allow_absent_api_and_data_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir, include_api=False, include_data=False)
            self._write_plan_json(
                feature_dir,
                design_refs=["design.md#D-001"],
                api_ids=[],
                data_ids=[],
            )
            ctx = self._ctx(feature_dir)
            self.assertEqual(validate_design_contract(ctx), 0)
            self.assertEqual(validate_plan_finished_tasks(self._plan_ctx(feature_dir)), 0)

    def test_design_requires_api_and_data_ids_when_escape_hatches_are_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(
                feature_dir,
                no_http_api=False,
                no_sql=False,
                include_api=False,
                include_data=False,
            )
            self.assertGreater(validate_design_contract(self._ctx(feature_dir)), 0)

    def test_plan_escape_hatches_still_reject_unknown_design_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir, include_api=False, include_data=False)
            self._write_plan_json(
                feature_dir,
                design_refs=["design.md#API-001", "design.md#D-001"],
                api_ids=[],
                data_ids=[],
            )
            self.assertGreater(validate_plan_finished_tasks(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_without_api_or_data_refs_is_allowed_when_other_task_covers_design(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir, no_http_api=False, no_sql=False)
            spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "cover api and data",
                            "status": "todo",
                            "deps": [],
                            "specRefs": spec_refs,
                            "designRefs": ["design.md#API-001", "design.md#DATA-001", "design.md#D-001"],
                            "apiIds": ["API-001"],
                            "dataIds": ["DATA-001"],
                            "decisionIds": ["D-001"],
                            "validationCommands": [{"command": "echo ok"}],
                            "expectedFiles": [],
                            "evidenceIds": [],
                            "blockers": [],
                        },
                        {
                            "id": "T002",
                            "title": "no api or data work",
                            "status": "todo",
                            "deps": ["T001"],
                            "specRefs": spec_refs,
                            "designRefs": ["design.md#D-001"],
                            "apiIds": [],
                            "dataIds": [],
                            "decisionIds": ["D-001"],
                            "validationCommands": [{"command": "echo ok"}],
                            "expectedFiles": [],
                            "evidenceIds": [],
                            "blockers": [],
                        },
                    ],
                },
            )

            self.assertEqual(validate_plan_json_contract(self._plan_ctx(feature_dir)), 0)

    def test_plan_requires_design_api_and_data_decisions_to_be_covered_somewhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir, no_http_api=False, no_sql=False)
            self._write_plan_json(
                feature_dir,
                status="todo",
                design_refs=["design.md#D-001"],
                api_ids=[],
                data_ids=[],
                evidence_ids=[],
            )

            self.assertGreater(validate_plan_json_contract(self._plan_ctx(feature_dir)), 0)

    def test_plan_markdown_without_plan_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(
                    [
                        "# 执行计划: cap",
                        "### 1. 实现",
                        "- **做什么:** do",
                        "- **状态:** 完成",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertGreater(validate_plan_finished_tasks(self._ctx(feature_dir)), 0)

    def test_each_stable_task_block_requires_own_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json(feature_dir, spec_refs=[], design_refs=[], decision_ids=[])
            self.assertGreater(validate_plan_finished_tasks(self._plan_ctx(feature_dir)), 0)

    def test_plan_refs_must_exist_in_specs_and_design(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json(
                feature_dir,
                spec_refs=["specs/cap/spec.md#REQ-999", "specs/cap/spec.md#SCN-999"],
            )
            self.assertGreater(validate_plan_finished_tasks(self._plan_ctx(feature_dir)), 0)

    def test_duplicate_spec_ids_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir, duplicate=True)
            self.assertGreater(validate_specs_contract(self._ctx(feature_dir)), 0)

    def test_e2e_and_verify_reports_require_trace_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            (feature_dir / "E2E_TEST_CASES.yaml").write_text(
                "\n".join(
                    [
                        "id: E2E-alpha-001",
                        "status: pending",
                        "execution_mode: mixed",
                        "ui_required: true",
                        "source:",
                        "  specs_contract:",
                        "    - spec: specs/cap/spec.md",
                        "      requirement: Requirement [REQ-001]: cap",
                        "      scenario: Scenario [SCN-001]: happy path",
                    ]
                ),
                encoding="utf-8",
            )
            (feature_dir / "E2E_REPORT.md").write_text(
                "E2E-alpha-001 PASS specs/cap/spec.md#REQ-001 / #SCN-001\n",
                encoding="utf-8",
            )
            (feature_dir / "e2e-run.log").write_text("ok\n", encoding="utf-8")
            self._write_json(
                feature_dir,
                "E2E_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "cases": [
                        {
                            "caseId": "E2E-alpha-001",
                            "taskId": "T001",
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "uiRequired": True,
                            "executionMode": "mixed",
                            "steps": [],
                            "verdict": "PASS",
                        }
                    ],
                },
            )
            (feature_dir / "VERIFY_REPORT.md").write_text(
                "\n".join(
                    [
                        "# 验证报告",
                        "## 验证总览",
                        "| # | Specs Requirement / Scenario | 裁定 | 证据来源 |",
                        "|---|------------------------------|------|----------|",
                        "| 1 | specs/cap/spec.md#REQ-001 / #SCN-001 | 通过 | UNIT_TEST_REPORT + E2E_REPORT |",
                        "## Specs / Design Contract 验证",
                        "| # | Contract Item | 裁定 | 证据来源 |",
                        "|---|---------------|------|----------|",
                        "| 1 | specs/cap/spec.md#REQ-001 / #SCN-001 | 通过 | e2e-run.log |",
                        "## 结论",
                        "- 分支决策: verify_done",
                    ]
                ),
                encoding="utf-8",
            )
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                },
            )
            ctx = self._ctx(feature_dir)
            self.assertEqual(validate_e2e_report_contract(ctx), 0)
            self.assertEqual(validate_verify_report_contract(ctx), 0)

    def test_e2e_report_rejects_missing_trace_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            (feature_dir / "E2E_TEST_CASES.yaml").write_text(
                "id: E2E-alpha-001\nexecution_mode: mixed\nui_required: true\n",
                encoding="utf-8",
            )
            (feature_dir / "E2E_REPORT.md").write_text("E2E-alpha-001 PASS\n", encoding="utf-8")
            (feature_dir / "e2e-run.log").write_text("ok\n", encoding="utf-8")
            self.assertGreater(validate_e2e_report_contract(self._ctx(feature_dir)), 0)

    def test_e2e_result_rejects_fail_summary_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "E2E_RESULT.json",
                {
                    "version": 1,
                    "verdict": "FAIL",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "fail"}
                    ],
                    "cases": [
                        {
                            "caseId": "E2E-alpha-001",
                            "taskId": "T001",
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "uiRequired": True,
                            "executionMode": "manual",
                            "steps": [],
                            "verdict": "FAIL",
                        }
                    ],
                },
            )

            self.assertGreater(
                validate_e2e_result_json(self._required_output_ctx(feature_dir, "E2E_RESULT.json")),
                0,
            )

    def test_json_sidecars_validate_trace_refs_and_scenario_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]

            self._write_json(
                feature_dir,
                "REVIEW_FINDINGS.json",
                {
                    "version": 1,
                    "verdict": "PASS_WITH_WARNINGS",
                    "findings": [
                        {
                            "id": "R001",
                            "taskId": "T001",
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "severity": "high",
                            "message": "covered",
                            "suggestedCheckpoint": "code_in_progress",
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                            "coverage": {"lines": 1},
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "E2E_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "cases": [
                        {
                            "caseId": "E2E-alpha-001",
                            "taskId": "T001",
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "uiRequired": True,
                            "executionMode": "manual",
                            "steps": [{"action": "open", "expected": "ok", "result": "PASS"}],
                            "verdict": "PASS",
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "FIX_REQUEST.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "sourceCheckpoint": "verify_in_progress",
                    "sourceNodeId": "dev.verify",
                    "suggestedCheckpoint": "code_in_progress",
                    "rootCause": "implementation_bug",
                    "blockingReason": "fix",
                    "humanActionRequired": False,
                    "failedSpecRefs": spec_refs,
                    "failedEvidenceIds": ["ev_0001"],
                    "failedDesignRefs": ["design.md#D-001"],
                    "createdAt": "2026-06-24T00:00:00Z",
                },
            )

            ctx = self._ctx(feature_dir)
            self.assertEqual(validate_review_findings_json(ctx), 0)
            self.assertEqual(validate_unit_test_result_json(ctx), 0)
            self.assertEqual(validate_e2e_result_json(ctx), 0)
            self.assertEqual(validate_verify_decision_json(ctx), 0)
            self.assertEqual(validate_fix_request_json(ctx), 0)

    def test_review_unit_and_e2e_results_validate_ui_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=True)
            spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]

            self._write_json(
                feature_dir,
                "REVIEW_FINDINGS.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "findings": [
                        {
                            "id": "R001",
                            "taskId": "T001",
                            "uiRequired": True,
                            "pageRefs": ["PAGE-001"],
                            "interactionRefs": ["UIX-001"],
                            "visualSourceRefs": ["VIS-001"],
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "severity": "info",
                            "message": "ui covered",
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "uiRequired": True,
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "E2E_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "cases": [
                        {
                            "caseId": "E2E-alpha-001",
                            "taskId": "T001",
                            "uiRequired": True,
                            "pageRefs": ["PAGE-001"],
                            "interactionRefs": ["UIX-001"],
                            "visualSourceRefs": ["VIS-001"],
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "executionMode": "manual",
                            "steps": [{"action": "open", "expected": "ok", "result": "PASS"}],
                            "verdict": "PASS",
                        }
                    ],
                },
            )

            ctx = self._ui_required_ctx(feature_dir, "REVIEW_FINDINGS.json", "UNIT_TEST_RESULT.json", "E2E_RESULT.json")
            self.assertEqual(validate_review_findings_json(ctx), 0)
            self.assertEqual(validate_unit_test_result_json(ctx), 0)
            self.assertEqual(validate_e2e_result_json(ctx), 0)

    def test_ui_projection_rejects_missing_ui_fields_for_ui_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=True)
            spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]

            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "E2E_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "cases": [
                        {
                            "caseId": "E2E-alpha-001",
                            "taskId": "T001",
                            "uiRequired": True,
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "executionMode": "manual",
                            "steps": [],
                            "verdict": "PASS",
                        }
                    ],
                },
            )

            ctx = self._ui_required_ctx(feature_dir, "UNIT_TEST_RESULT.json", "E2E_RESULT.json")
            self.assertGreater(validate_unit_test_result_json(ctx), 0)
            self.assertGreater(validate_e2e_result_json(ctx), 0)

    def test_review_findings_reject_missing_ui_refs_for_ui_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True)
            self._write_json(
                feature_dir,
                "REVIEW_FINDINGS.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "findings": [
                        {
                            "id": "R001",
                            "taskId": "T001",
                            "uiRequired": True,
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "severity": "info",
                            "message": "ui finding missing refs",
                        }
                    ],
                },
            )

            self.assertGreater(
                validate_review_findings_json(self._ui_required_ctx(feature_dir, "REVIEW_FINDINGS.json")),
                0,
            )

    def test_verify_decision_validates_ui_summary_and_applicability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=True)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {
                            "scenarioRef": "SCN-001",
                            "evidenceIds": ["ev_0001"],
                            "verdict": "pass",
                            "uiApplicability": "required",
                        }
                    ],
                    "uiSummary": {
                        "uiRequired": True,
                        "passedUiScenarioRefs": ["SCN-001"],
                        "failedUiScenarioRefs": [],
                        "manualUiScenarioRefs": [],
                        "missingUiScenarioRefs": [],
                        "notApplicableScenarioRefs": [],
                    },
                },
            )

            self.assertEqual(
                validate_verify_decision_json(self._ui_required_ctx(feature_dir, "VERIFY_DECISION.json")),
                0,
            )

    def test_verify_decision_accepts_non_ui_feature_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir, ui_required=False)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {
                            "scenarioRef": "SCN-001",
                            "evidenceIds": ["ev_0001"],
                            "verdict": "pass",
                            "uiApplicability": "not_applicable",
                        }
                    ],
                    "uiSummary": {
                        "uiRequired": False,
                        "passedUiScenarioRefs": [],
                        "failedUiScenarioRefs": [],
                        "manualUiScenarioRefs": [],
                        "missingUiScenarioRefs": [],
                        "notApplicableScenarioRefs": ["SCN-001"],
                    },
                },
            )

            self.assertEqual(
                validate_verify_decision_json(self._ui_required_ctx(feature_dir, "VERIFY_DECISION.json")),
                0,
            )

    def test_verify_decision_rejects_missing_ui_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=True)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                },
            )

            self.assertGreater(
                validate_verify_decision_json(self._ui_required_ctx(feature_dir, "VERIFY_DECISION.json")),
                0,
            )

    def test_verify_decision_allows_failed_ui_scenario_with_required_applicability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=True)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "fail",
                    "passedScenarioRefs": [],
                    "failedScenarioRefs": ["SCN-001"],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "needs_fix",
                    "scenarioCoverage": [
                        {
                            "scenarioRef": "SCN-001",
                            "evidenceIds": ["ev_0001"],
                            "verdict": "fail",
                            "uiApplicability": "required",
                        }
                    ],
                    "uiSummary": {
                        "uiRequired": True,
                        "passedUiScenarioRefs": [],
                        "failedUiScenarioRefs": ["SCN-001"],
                        "manualUiScenarioRefs": [],
                        "missingUiScenarioRefs": [],
                        "notApplicableScenarioRefs": [],
                    },
                },
            )

            self.assertEqual(
                validate_verify_decision_json(self._ui_required_ctx(feature_dir, "VERIFY_DECISION.json")),
                0,
            )

    def test_verify_decision_allows_non_ui_scenario_as_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            with (feature_dir / "specs" / "cap" / "spec.md").open("a", encoding="utf-8") as handle:
                handle.write("\n### Requirement [REQ-002]: backend\n#### Scenario [SCN-002]: backend path\n")
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=True)
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "specRefs": ["specs/cap/spec.md#REQ-002", "specs/cap/spec.md#SCN-002"],
                    "designRefs": ["design.md#D-001"],
                    "changedFiles": ["src/foo.py"],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
            )
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001", "SCN-002"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001", "ev_0002"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {
                            "scenarioRef": "SCN-001",
                            "evidenceIds": ["ev_0001"],
                            "verdict": "pass",
                            "uiApplicability": "required",
                        },
                        {
                            "scenarioRef": "SCN-002",
                            "evidenceIds": ["ev_0002"],
                            "verdict": "pass",
                            "uiApplicability": "not_applicable",
                        },
                    ],
                    "uiSummary": {
                        "uiRequired": True,
                        "passedUiScenarioRefs": ["SCN-001"],
                        "failedUiScenarioRefs": [],
                        "manualUiScenarioRefs": [],
                        "missingUiScenarioRefs": [],
                        "notApplicableScenarioRefs": ["SCN-002"],
                    },
                },
            )

            self.assertEqual(
                validate_verify_decision_json(self._ui_required_ctx(feature_dir, "VERIFY_DECISION.json")),
                0,
            )

    def test_verify_ui_pass_requires_e2e_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True, e2e_evidence=False)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {
                            "scenarioRef": "SCN-001",
                            "evidenceIds": ["ev_0001"],
                            "verdict": "pass",
                            "uiApplicability": "required",
                        }
                    ],
                    "uiSummary": {
                        "uiRequired": True,
                        "passedUiScenarioRefs": ["SCN-001"],
                        "failedUiScenarioRefs": [],
                        "manualUiScenarioRefs": [],
                        "missingUiScenarioRefs": [],
                        "notApplicableScenarioRefs": [],
                    },
                },
            )

            self.assertGreater(
                validate_verify_decision_json(self._ui_required_ctx(feature_dir, "VERIFY_DECISION.json")),
                0,
            )

    def test_fix_request_validates_failed_ui_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_ui_context(feature_dir)
            self._write_plan_json_and_evidence(feature_dir, ui_task=True)
            self._write_json(
                feature_dir,
                "FIX_REQUEST.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "sourceCheckpoint": "verify_in_progress",
                    "sourceNodeId": "dev.verify",
                    "suggestedCheckpoint": "code_in_progress",
                    "rootCause": "implementation_bug",
                    "blockingReason": "fix",
                    "humanActionRequired": False,
                    "failedSpecRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                    "failedEvidenceIds": ["ev_0001"],
                    "failedDesignRefs": ["design.md#D-001"],
                    "failedUiRefs": {
                        "pageRefs": ["PAGE-001"],
                        "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": ["VIS-001"],
                    },
                    "createdAt": "2026-06-24T00:00:00Z",
                },
            )
            self.assertEqual(validate_fix_request_json(self._ui_required_ctx(feature_dir, "FIX_REQUEST.json")), 0)

            payload = json.loads((feature_dir / "FIX_REQUEST.json").read_text(encoding="utf-8"))
            payload["failedUiRefs"]["pageRefs"] = ["PAGE-999"]
            self._write_json(feature_dir, "FIX_REQUEST.json", payload)
            self.assertGreater(validate_fix_request_json(self._ui_required_ctx(feature_dir, "FIX_REQUEST.json")), 0)

    def test_verify_decision_requires_all_defined_scenario_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            with (feature_dir / "specs" / "cap" / "spec.md").open("a", encoding="utf-8") as handle:
                handle.write("\n### Requirement [REQ-002]: another\n#### Scenario [SCN-002]: uncovered\n")
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                },
            )

            self.assertGreater(validate_verify_decision_json(self._ctx(feature_dir)), 0)

    def test_verify_decision_allows_explicit_missing_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            with (feature_dir / "specs" / "cap" / "spec.md").open("a", encoding="utf-8") as handle:
                handle.write("\n### Requirement [REQ-002]: another\n#### Scenario [SCN-002]: uncovered\n")
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "fail",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": ["SCN-002"],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "needs_fix",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"},
                        {"scenarioRef": "SCN-002", "evidenceIds": [], "verdict": "missing"},
                    ],
                },
            )

            self.assertEqual(validate_verify_decision_json(self._ctx(feature_dir)), 0)

    def test_verify_decision_rejects_pass_rows_without_covering_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": [], "verdict": "pass"}
                    ],
                },
            )

            self.assertGreater(validate_verify_decision_json(self._ctx(feature_dir)), 0)

    def test_verify_decision_rejects_verdict_checkpoint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "fail",
                    "passedScenarioRefs": [],
                    "failedScenarioRefs": ["SCN-001"],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "fail"}
                    ],
                },
            )

            self.assertGreater(validate_verify_decision_json(self._ctx(feature_dir)), 0)

    def test_verify_decision_rejects_scenario_matrix_decision_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "VERIFY_DECISION.json",
                {
                    "version": 1,
                    "verdict": "pass",
                    "passedScenarioRefs": ["SCN-001"],
                    "failedScenarioRefs": [],
                    "manualVerificationRefs": [],
                    "missingScenarioRefs": [],
                    "evidenceIds": ["ev_0001"],
                    "nextCheckpoint": "verify_done",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "fail"}
                    ],
                },
            )

            self.assertGreater(validate_verify_decision_json(self._ctx(feature_dir)), 0)

    def test_unit_and_e2e_results_require_all_defined_scenario_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            with (feature_dir / "specs" / "cap" / "spec.md").open("a", encoding="utf-8") as handle:
                handle.write("\n### Requirement [REQ-002]: another\n#### Scenario [SCN-002]: uncovered\n")
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "E2E_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "cases": [
                        {
                            "caseId": "E2E-alpha-001",
                            "taskId": "T001",
                            "specRefs": spec_refs,
                            "evidenceIds": ["ev_0001"],
                            "uiRequired": True,
                            "executionMode": "manual",
                            "steps": [],
                            "verdict": "PASS",
                        }
                    ],
                },
            )

            ctx = self._ctx(feature_dir)
            self.assertGreater(validate_unit_test_result_json(ctx), 0)
            self.assertGreater(validate_e2e_result_json(ctx), 0)

    def test_json_sidecars_reject_parallel_scenario_ref_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "scenarioRef": "SCN-999",
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )

            self.assertGreater(validate_unit_test_result_json(self._ctx(feature_dir)), 0)

    def test_json_sidecars_require_evidence_stream_for_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "do",
                            "status": "done",
                            "deps": [],
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
                    ],
                },
            )
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "PASS",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "result": "PASS",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )

            self.assertGreater(validate_unit_test_result_json(self._ctx(feature_dir)), 0)

    def test_unit_test_result_rejects_fail_summary_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "UNIT_TEST_RESULT.json",
                {
                    "version": 1,
                    "verdict": "FAIL",
                    "scenarioCoverage": [
                        {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "fail"}
                    ],
                    "targets": [
                        {
                            "targetId": "UT-001",
                            "taskId": "T001",
                            "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                            "evidenceIds": ["ev_0001"],
                            "result": "FAIL",
                            "command": "pytest tests/test_foo.py",
                        }
                    ],
                },
            )

            self.assertGreater(
                validate_unit_test_result_json(self._required_output_ctx(feature_dir, "UNIT_TEST_RESULT.json")),
                0,
            )

    def test_plan_json_initial_tasks_accepts_initial_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(feature_dir, status="todo")

            self.assertEqual(validate_plan_json_initial_tasks(self._ctx(feature_dir)), 0)

    def test_plan_json_initial_tasks_rejects_non_initial_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(feature_dir, status="done")

            self.assertGreater(validate_plan_json_initial_tasks(self._ctx(feature_dir)), 0)

    def test_plan_task_granularity_accepts_small_task_without_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(feature_dir, status="todo")

            self.assertEqual(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_giant_task_without_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 8)
            ]
            self._write_plan_json(feature_dir, status="todo", spec_refs=spec_refs)

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_accepts_specific_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 8)
            ]
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=spec_refs,
                extra_task_fields={
                    "splitRationale": "SCN-001、SCN-004、SCN-007 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。"
                },
            )

            self.assertEqual(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_accepts_english_validation_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 8)
            ]
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=spec_refs,
                extra_task_fields={
                    "splitRationale": "SCN-001, SCN-004, and SCN-007 cannot be validated independently because they share the same validation loop and same response assertion."
                },
            )

            self.assertEqual(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_hard_scenario_cap_even_with_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 10)
            ]
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=spec_refs,
                extra_task_fields={
                    "splitRationale": "SCN-001、SCN-004、SCN-007 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。"
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_page_only_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 8)
            ]
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=spec_refs,
                extra_task_fields={
                    "splitRationale": "覆盖 SCN-001、SCN-004、SCN-007，但它们都是同一页面的不同交互元素和不同组成部分，属于同一页面闭环，不可独立拆分。"
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_sparse_split_rationale_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 8)
            ]
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=spec_refs,
                extra_task_fields={
                    "splitRationale": "SCN-001 代表本任务主提交链路，其余场景也会在同一个验证闭环里一起覆盖。"
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_vague_split_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            spec_refs = ["specs/cap/spec.md#REQ-001"] + [
                f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 8)
            ]
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=spec_refs,
                extra_task_fields={"splitRationale": "同一模块一起实现比较方便"},
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_multi_page_ui_without_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001"],
                extra_task_fields={
                    "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001", "PAGE-002"],
                        "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": [],
                        "frontendRoute": "spec-driven-ui",
                    },
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/alpha"],
                        "pages": ["PAGE-001", "PAGE-002"],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated pages"],
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_hard_page_cap_even_with_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001"],
                extra_task_fields={
                    "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001", "PAGE-002", "PAGE-003"],
                        "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": [],
                        "frontendRoute": "spec-driven-ui",
                    },
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/alpha"],
                        "pages": ["PAGE-001", "PAGE-002", "PAGE-003"],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated UI pages"],
                    "splitRationale": "PAGE-001 与 PAGE-002 由同一次提交动作触发，PAGE-003 共享同一验证闭环。",
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_many_apis_without_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001", "API-002", "API-003"],
                extra_task_fields={
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/one", "POST /api/two", "POST /api/three"],
                        "pages": [],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated APIs"],
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_hard_api_cap_even_with_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001", "API-002", "API-003", "API-004"],
                extra_task_fields={
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/one", "POST /api/two", "POST /api/three", "POST /api/four"],
                        "pages": [],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated APIs"],
                    "splitRationale": "API-001 与 API-002 由同一次提交动作触发，API-003 与 API-004 共享同一验证闭环。"
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_sparse_api_split_rationale_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001", "API-002", "API-003"],
                extra_task_fields={
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/one", "POST /api/two", "POST /api/three"],
                        "pages": [],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated APIs"],
                    "splitRationale": "API-001 是本任务的主入口，另外两个接口只作为同一提交结果的辅助入口一起验证。",
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_many_interactions_without_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001"],
                extra_task_fields={
                    "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001"],
                        "interactionRefs": ["UIX-001", "UIX-002", "UIX-003", "UIX-004"],
                        "visualSourceRefs": [],
                        "frontendRoute": "spec-driven-ui",
                    },
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/alpha"],
                        "pages": ["PAGE-001"],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated UI interactions"],
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_granularity_rejects_hard_interaction_cap_even_with_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_plan_json(
                feature_dir,
                status="todo",
                api_ids=["API-001"],
                extra_task_fields={
                    "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001"],
                        "interactionRefs": ["UIX-001", "UIX-002", "UIX-003", "UIX-004", "UIX-005"],
                        "visualSourceRefs": [],
                        "frontendRoute": "spec-driven-ui",
                    },
                    "scope": {
                        "modules": ["src"],
                        "entrypoints": ["POST /api/alpha"],
                        "pages": ["PAGE-001"],
                        "dataObjects": [],
                    },
                    "nonGoals": ["do not implement unrelated UI interactions"],
                    "splitRationale": "UIX-001、UIX-003、UIX-005 由同一次提交动作触发，并共享同一验证闭环。"
                },
            )

            self.assertGreater(validate_plan_task_granularity(self._plan_ctx(feature_dir)), 0)

    def test_plan_scenario_coverage_accepts_all_defined_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=[
                    "specs/cap/spec.md#REQ-001",
                    "specs/cap/spec.md#SCN-001",
                ],
            )

            self.assertEqual(validate_plan_scenario_coverage(self._plan_ctx(feature_dir)), 0)

    def test_plan_scenario_coverage_rejects_missing_defined_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            (feature_dir / "specs" / "cap").mkdir(parents=True, exist_ok=True)
            (feature_dir / "specs" / "cap" / "spec.md").write_text(
                "\n".join(
                    [
                        "## ADDED Requirements",
                        "### Requirement [REQ-001]: capability",
                        "#### Scenario [SCN-001]: happy path",
                        "#### Scenario [SCN-002]: edge path",
                    ]
                ),
                encoding="utf-8",
            )
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=[
                    "specs/cap/spec.md#REQ-001",
                    "specs/cap/spec.md#SCN-001",
                ],
            )

            self.assertGreater(validate_plan_scenario_coverage(self._plan_ctx(feature_dir)), 0)

    def test_plan_scenario_coverage_is_path_aware_for_repeated_local_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            for cap in ("one", "two"):
                (feature_dir / "specs" / cap).mkdir(parents=True, exist_ok=True)
                (feature_dir / "specs" / cap / "spec.md").write_text(
                    "\n".join(
                        [
                            "## ADDED Requirements",
                            "### Requirement [REQ-001]: capability",
                            "#### Scenario [SCN-001]: happy path",
                        ]
                    ),
                    encoding="utf-8",
                )
            self._write_plan_json(
                feature_dir,
                status="todo",
                spec_refs=[
                    "specs/one/spec.md#REQ-001",
                    "specs/one/spec.md#SCN-001",
                ],
            )

            self.assertGreater(validate_plan_scenario_coverage(self._plan_ctx(feature_dir)), 0)

    def test_plan_task_detail_schema_rejects_legacy_plan_in_code_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "legacy",
                            "status": "done",
                            "deps": [],
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
                    ],
                },
            )

            self.assertGreater(validate_plan_task_detail_schema(self._plan_ctx(feature_dir)), 0)

    def test_plan_json_success_skips_stale_plan_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(
                    [
                        "# 执行计划: stale",
                        "### Task [T001]: stale",
                        "- **状态:** 待做",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(validate_plan_finished_tasks(self._ctx(feature_dir)), 0)

    def test_code_done_gate_validator_requires_pass_evidence_for_each_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            write_plan_json(
                feature_dir / "plan.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "tasks": [
                        {
                            "id": "T001",
                            "title": "done without evidence",
                            "status": "done",
                            "deps": [],
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
                    ],
                },
            )
            ctx = HookContext(
                skill="autodev-code",
                slug="alpha",
                root=feature_dir.parent.parent.parent,
                required_outputs=("evidence/EVIDENCE.jsonl",),
            )

            self.assertGreater(validate_code_done_gate(ctx), 0)

    def test_code_done_gate_degrades_plan_check_when_plan_not_in_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["src/foo.py"],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
            )
            ctx = HookContext(
                skill="autodev-code",
                slug="alpha",
                root=feature_dir.parent.parent.parent,
                required_outputs=("evidence/EVIDENCE.jsonl",),
            )

            self.assertEqual(validate_code_done_gate(ctx), 0)

    def test_smoke_test_plan_accepts_missing_source_path_during_plan_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                        "preconditions": [],
                        "timeoutSeconds": 60,
                    }
                ],
            )

            self.assertEqual(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_missing_tdd_seam_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "seam": None,
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_missing_vertical_slice_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "verticalSlice": None,
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_internal_mock_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "mockPolicy": {"externalOnly": False, "allowedMocks": ["internal service"]},
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_multi_scenario_smoke_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            with (feature_dir / "specs" / "cap" / "spec.md").open("a", encoding="utf-8") as handle:
                handle.write("\n### Requirement [REQ-002]: another\n#### Scenario [SCN-002]: second path\n")
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001", "specs/cap/spec.md#SCN-002"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_blocking_or_untracked_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                flow_blocking=True,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": "tmp/cap_smoke.py",
                        "command": "python tmp/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_unknown_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T999",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_test_plan_rejects_unknown_scenario_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="todo", evidence_ids=[])
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-999"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )

            self.assertGreater(validate_smoke_test_plan_json(self._required_output_ctx(feature_dir, "SMOKE_TEST_PLAN.json")), 0)

    def test_smoke_result_degrades_when_no_plan_and_no_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)

            self.assertEqual(validate_smoke_result_json(self._ctx(feature_dir)), 0)

    def test_smoke_result_allows_failed_verdict_and_requires_smoke_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="done", evidence_ids=["ev_0001"])
            source = feature_dir.parent.parent.parent / "tests" / "smoke" / "cap_smoke.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("raise SystemExit(1)\n", encoding="utf-8")
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": "tests/smoke/cap_smoke.py",
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )
            appended = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["tests/smoke/cap_smoke.py"],
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/cap_smoke.py", "exitCode": 1, "result": "fail"},
                },
                output_tail="boom",
            )
            self._write_json(
                feature_dir,
                "SMOKE_RESULT.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "flowBlocking": False,
                    "verdict": "FAIL",
                    "results": [
                        {
                            "testId": "SMK-001",
                            "taskId": "T001",
                            "command": "python tests/smoke/cap_smoke.py",
                            "exitCode": 1,
                            "result": "fail",
                            "evidenceId": appended["evidenceId"],
                            "outputTailPath": appended["smoke"]["outputTailPath"],
                            "failureSummary": "boom",
                        }
                    ],
                },
            )

            self.assertEqual(validate_smoke_result_json(self._required_output_ctx(feature_dir, "SMOKE_RESULT.json")), 0)

    def test_smoke_result_requires_generated_source_to_be_git_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="done", evidence_ids=["ev_0001"])
            source_path = "tests/smoke/cap_smoke.py"
            source = root / source_path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("raise SystemExit(1)\n", encoding="utf-8")
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": source_path,
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )
            appended = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": [source_path],
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/cap_smoke.py", "exitCode": 1, "result": "fail"},
                },
                output_tail="boom",
            )
            self._write_json(
                feature_dir,
                "SMOKE_RESULT.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "flowBlocking": False,
                    "verdict": "FAIL",
                    "results": [
                        {
                            "testId": "SMK-001",
                            "taskId": "T001",
                            "command": "python tests/smoke/cap_smoke.py",
                            "exitCode": 1,
                            "result": "fail",
                            "evidenceId": appended["evidenceId"],
                            "outputTailPath": appended["smoke"]["outputTailPath"],
                            "failureSummary": "boom",
                        }
                    ],
                },
            )

            self.assertGreater(validate_smoke_result_json(self._required_output_ctx(feature_dir, "SMOKE_RESULT.json")), 0)

            self._git_ignore_path(root, source_path)
            self.assertEqual(validate_smoke_result_json(self._required_output_ctx(feature_dir, "SMOKE_RESULT.json")), 0)

    def test_smoke_result_rejects_tracked_generated_source_even_when_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git_repo(root)
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="done", evidence_ids=["ev_0001"])
            source_path = "tests/smoke/cap_smoke.py"
            source = root / source_path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("raise SystemExit(1)\n", encoding="utf-8")
            subprocess.run(["git", "add", source_path], cwd=root, check=True, text=True, capture_output=True)
            self._git_ignore_path(root, source_path)
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "cap smoke",
                        "smokeType": "api",
                        "sourcePath": source_path,
                        "command": "python tests/smoke/cap_smoke.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )
            appended = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": [source_path],
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/cap_smoke.py", "exitCode": 1, "result": "fail"},
                },
                output_tail="boom",
            )
            self._write_json(
                feature_dir,
                "SMOKE_RESULT.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "flowBlocking": False,
                    "verdict": "FAIL",
                    "results": [
                        {
                            "testId": "SMK-001",
                            "taskId": "T001",
                            "command": "python tests/smoke/cap_smoke.py",
                            "exitCode": 1,
                            "result": "fail",
                            "evidenceId": appended["evidenceId"],
                            "outputTailPath": appended["smoke"]["outputTailPath"],
                            "failureSummary": "boom",
                        }
                    ],
                },
            )

            self.assertGreater(validate_smoke_result_json(self._required_output_ctx(feature_dir, "SMOKE_RESULT.json")), 0)

    def test_smoke_result_rejects_missing_planned_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="done", evidence_ids=["ev_0001"])
            smoke_dir = feature_dir.parent.parent.parent / "tests" / "smoke"
            smoke_dir.mkdir(parents=True, exist_ok=True)
            (smoke_dir / "one.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            (smoke_dir / "two.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "one",
                        "smokeType": "cli",
                        "sourcePath": "tests/smoke/one.py",
                        "command": "python tests/smoke/one.py",
                        "expectedSignals": ["exit 0"],
                    },
                    {
                        "id": "SMK-002",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "two",
                        "smokeType": "cli",
                        "sourcePath": "tests/smoke/two.py",
                        "command": "python tests/smoke/two.py",
                        "expectedSignals": ["exit 0"],
                    },
                ],
            )
            appended = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["tests/smoke/one.py"],
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/one.py", "exitCode": 0, "result": "pass"},
                },
            )
            self._write_json(
                feature_dir,
                "SMOKE_RESULT.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "flowBlocking": False,
                    "verdict": "PASS",
                    "results": [
                        {
                            "testId": "SMK-001",
                            "taskId": "T001",
                            "command": "python tests/smoke/one.py",
                            "exitCode": 0,
                            "result": "pass",
                            "evidenceId": appended["evidenceId"],
                        }
                    ],
                },
            )

            self.assertGreater(validate_smoke_result_json(self._ctx(feature_dir)), 0)

    def test_smoke_result_rejects_summary_verdict_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="done", evidence_ids=["ev_0001"])
            smoke_dir = feature_dir.parent.parent.parent / "tests" / "smoke"
            smoke_dir.mkdir(parents=True, exist_ok=True)
            (smoke_dir / "one.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            self._write_smoke_plan(
                feature_dir,
                tests=[
                    {
                        "id": "SMK-001",
                        "taskId": "T001",
                        "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        "title": "one",
                        "smokeType": "cli",
                        "sourcePath": "tests/smoke/one.py",
                        "command": "python tests/smoke/one.py",
                        "expectedSignals": ["exit 0"],
                    }
                ],
            )
            appended = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["tests/smoke/one.py"],
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/one.py", "exitCode": 0, "result": "pass"},
                },
            )
            self._write_json(
                feature_dir,
                "SMOKE_RESULT.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "flowBlocking": False,
                    "verdict": "FAIL",
                    "results": [
                        {
                            "testId": "SMK-001",
                            "taskId": "T001",
                            "command": "python tests/smoke/one.py",
                            "exitCode": 0,
                            "result": "pass",
                            "evidenceId": appended["evidenceId"],
                        }
                    ],
                },
            )

            self.assertGreater(validate_smoke_result_json(self._ctx(feature_dir)), 0)

    def test_code_done_gate_does_not_accept_smoke_evidence_as_validation_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_plan_json(feature_dir, status="done", evidence_ids=["ev_0001"])
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "smoke",
                    "specRefs": ["specs/cap/spec.md#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["tests/smoke/cap_smoke.py"],
                    "validation": {"command": "python tests/smoke/cap_smoke.py", "exitCode": 0, "result": "pass"},
                    "smoke": {"testId": "SMK-001", "command": "python tests/smoke/cap_smoke.py", "exitCode": 0, "result": "pass"},
                },
            )
            ctx = HookContext(
                skill="autodev-code",
                slug="alpha",
                root=feature_dir.parent.parent.parent,
                required_inputs=("plan.json",),
                required_outputs=("evidence/EVIDENCE.jsonl",),
            )

            self.assertGreater(validate_code_done_gate(ctx), 0)

    def test_fix_request_rejects_unknown_design_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "FIX_REQUEST.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "sourceCheckpoint": "verify_in_progress",
                    "sourceNodeId": "dev.verify",
                    "suggestedCheckpoint": "code_in_progress",
                    "rootCause": "implementation_bug",
                    "blockingReason": "fix",
                    "humanActionRequired": False,
                    "failedSpecRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                    "failedEvidenceIds": ["ev_0001"],
                    "failedDesignRefs": ["design.md#D-999"],
                    "createdAt": "2026-06-24T00:00:00Z",
                },
            )

            self.assertGreater(validate_fix_request_json(self._ctx(feature_dir)), 0)

    def test_fix_request_record_contract_error_falls_back_to_repo_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan_json_and_evidence(feature_dir)
            self._write_json(
                feature_dir,
                "FIX_REQUEST.json",
                {
                    "version": 1,
                    "featureId": "alpha",
                    "sourceCheckpoint": "verify_in_progress",
                    "sourceNodeId": "dev.verify",
                    "suggestedCheckpoint": "verify_done",
                    "rootCause": "implementation_bug",
                    "blockingReason": "fix",
                    "humanActionRequired": False,
                    "failedSpecRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                    "failedEvidenceIds": ["ev_0001"],
                    "failedDesignRefs": ["design.md#D-001"],
                    "createdAt": "2026-06-24T00:00:00Z",
                },
            )
            state_dir = feature_dir.parent.parent
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "state.json").write_text(
                json.dumps({"features": {"alpha": {"checkpoint": "needs_fix"}}}),
                encoding="utf-8",
            )
            with mock.patch("artifact_check.load_record_workflow_contracts", side_effect=BoardConfigError("bad record")):
                self.assertGreater(validate_fix_request_json(self._ctx(feature_dir)), 0)


if __name__ == "__main__":
    unittest.main()
