from __future__ import annotations

import sys
import tempfile
import unittest
import json
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
    validate_review_findings_json,
    validate_specs_contract,
    validate_unit_test_result_json,
    validate_unit_test_report_contract,
    validate_verify_decision_json,
    validate_verify_report_contract,
)
from board_core.contracts import BoardConfigError  # noqa: E402
from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.plan_json import write_plan_json  # noqa: E402


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

    def _write_plan_json_and_evidence(self, feature_dir: Path) -> None:
        spec_refs = ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"]
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
        append_evidence(
            feature_dir,
            {
                "featureId": "alpha",
                "checkpoint": "code_in_progress",
                "nodeId": "dev.code",
                "skill": "autodev-code",
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
    ) -> None:
        write_plan_json(
            feature_dir / "plan.json",
            {
                "version": 1,
                "featureId": "alpha",
                "tasks": [
                    {
                        "id": "T001",
                        "title": "do",
                        "status": status,
                        "deps": [],
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
                ],
            },
        )

    def _write_json(self, feature_dir: Path, name: str, payload: dict) -> None:
        (feature_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

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
