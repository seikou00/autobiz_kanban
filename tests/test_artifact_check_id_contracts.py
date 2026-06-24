from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / "skills" / "autodev" / "hooks"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from artifact_check import (  # noqa: E402
    HookContext,
    validate_design_contract,
    validate_e2e_report_contract,
    validate_plan_finished_tasks,
    validate_specs_contract,
    validate_unit_test_report_contract,
    validate_verify_report_contract,
)


class ArtifactCheckIdContractsTest(unittest.TestCase):
    def _ctx(self, feature_dir: Path) -> HookContext:
        root = feature_dir.parent.parent.parent
        return HookContext(skill="autodev-sample", slug="alpha", root=root)

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

    def _write_design(
        self,
        feature_dir: Path,
        *,
        no_http_api: bool = True,
        no_sql: bool = True,
        include_api: bool = True,
        include_data: bool = True,
    ) -> None:
        coverage = ["D-001"]
        if include_data:
            coverage.insert(0, "DATA-001")
        if include_api:
            coverage.insert(0, "API-001")
        api_row = (
            "| API-001 | 无 | 无 | 无 | 无 | 无 | 无 | 已确认 |"
            if include_api
            else "| 无决策项 | 无 | 无 | 无 | 无 | 无 | 无 | 已确认 |"
        )
        data_row = (
            "| DATA-001 | 无 | 无 | 无 | 无 | 无 | 已确认 |"
            if include_data
            else "| 无决策项 | 无 | 无 | 无 | 无 | 无 | 已确认 |"
        )
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
                    api_row,
                    "## 4. Data Decisions / 数据决策",
                    f"- x-auto-no-sql: {str(no_sql).lower()}",
                    "| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |",
                    "|----|-------------|--------|--------|-----------------|----------|--------|",
                    data_row,
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

    def _write_plan(
        self,
        feature_dir: Path,
        *,
        spec_ref: str = "REQ-001 / #SCN-001",
        design_ref: str = "design.md#API-001 / #DATA-001 / #D-001",
    ) -> None:
        (feature_dir / "PLAN.md").write_text(
            "\n".join(
                [
                    "# 执行计划: cap",
                    "### Task [T001]: do",
                    "- **做什么:** do",
                    f"- **规格依据:** specs/cap/spec.md#{spec_ref}",
                    f"- **设计依据:** {design_ref}",
                    "- **证据依据:** ev_0001",
                    "- **验证方法:** echo ok",
                    "- **状态:** 完成",
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
            self._write_plan(feature_dir)
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
            ctx = self._ctx(feature_dir)
            self.assertEqual(validate_design_contract(ctx), 0)
            self.assertEqual(validate_plan_finished_tasks(ctx), 0)
            self.assertEqual(validate_unit_test_report_contract(ctx), 0)

    def test_design_escape_hatches_allow_absent_api_and_data_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir, include_api=False, include_data=False)
            self._write_plan(feature_dir, design_ref="design.md#D-001")
            ctx = self._ctx(feature_dir)
            self.assertEqual(validate_design_contract(ctx), 0)
            self.assertEqual(validate_plan_finished_tasks(ctx), 0)

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
            self._write_plan(feature_dir, design_ref="design.md#API-001 / #D-001")
            self.assertGreater(validate_plan_finished_tasks(self._ctx(feature_dir)), 0)

    def test_legacy_plan_format_degrades_instead_of_blocking(self) -> None:
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
            self.assertEqual(validate_plan_finished_tasks(self._ctx(feature_dir)), 0)

    def test_each_stable_task_block_requires_own_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan(feature_dir)
            with (feature_dir / "PLAN.md").open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n".join(
                        [
                            "",
                            "### Task [T002]: missing refs",
                            "- **做什么:** only mentions nothing",
                            "- **状态:** 完成",
                        ]
                    )
                )
            self.assertGreater(validate_plan_finished_tasks(self._ctx(feature_dir)), 0)

    def test_plan_refs_must_exist_in_specs_and_design(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir)
            self._write_design(feature_dir)
            self._write_plan(feature_dir, spec_ref="REQ-999 / #SCN-999")
            self.assertGreater(validate_plan_finished_tasks(self._ctx(feature_dir)), 0)

    def test_duplicate_spec_ids_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
            self._write_specs(feature_dir, duplicate=True)
            self.assertGreater(validate_specs_contract(self._ctx(feature_dir)), 0)

    def test_e2e_and_verify_reports_require_trace_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = self._feature_dir(tmp)
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


if __name__ == "__main__":
    unittest.main()
