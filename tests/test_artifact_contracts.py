"""可计算契约图校验：Capability Index 双射、REQ/SCN 稳定 ID、基线/证据结构、待确认残留。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTODEV_HOOKS_DIR = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS_DIR))

from artifact_check import (  # noqa: E402
    validate_design_contract,
    validate_plan_finished_tasks,
    validate_plan_initial_tasks,
    validate_proposal_contract,
    validate_specs_contract,
)
from common import HookContext, task_count  # noqa: E402
from plan_execution_check import main as plan_check_main  # noqa: E402


PROPOSAL_OK = """# Proposal: 导出

## Why
x

## What Changes
- x

## Capability Index

| Capability ID | Capability | Operations | Spec Path | Status |
|---------------|------------|------------|-----------|--------|
| CAP-order-export | order-export | ADDED, MODIFIED | specs/order-export/spec.md | confirmed |

## Impact
- x

## Out of Scope
- x

## Decision Log
无

## Open Questions
无
"""

SPEC_OK = """# Order Export Specification

Capability-ID: CAP-order-export

## ADDED Requirements

### REQ-order-export-001: 创建导出任务

The system SHALL 创建导出任务。

#### SCN-order-export-001-01: 创建成功

- **WHEN** x
- **THEN** y

## MODIFIED Requirements

### REQ-order-export-002: 调整导出范围

修改后的完整行为。

#### SCN-order-export-002-01: 范围收窄

- **WHEN** x
- **THEN** y
"""

DESIGN_OK = """# 技术设计: 导出

## 1. Context / 输入上下文
- x

## 2. Code Evidence / 代码探索证据

| Evidence ID | Path / Symbol | Observed Fact | Verified At |
|-------------|---------------|---------------|-------------|
| EVD-001 | src/export.py::create | 当前为同步调用 | abc1234 |

## 3. Spec Traceability / 规格追踪

| Requirement | Scenarios | Decision | Design Coverage | Evidence |
|-------------|-----------|----------|-----------------|----------|
| REQ-order-export-001 | SCN-order-export-001-01 | 无 | API-01 | EVD-001 |

## 4. API Decisions / 接口决策

- **x-auto-no-http-api:** false

| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |
|----|--------|--------------|---------|----------|--------|-------------------|--------|
| API-01 | POST | /export | x | y | z | t | 已确认 |

## 5. Data Decisions / 数据决策

- **x-auto-no-sql:** true
- **说明:** 无数据变更

## 6. Technical Design / 技术设计

### Decisions
| ID | Decision | Rationale | Alternatives | Status |
|----|----------|-----------|--------------|--------|
| D-01 | x | y | z | 已确认 |

## 7. Risks / Open Questions

| ID | Type | Description | Impact | Owner/Next Step |
|----|------|-------------|--------|-----------------|
| R-01 | 风险 | x | y | z |
"""

PLAN_OK = """# 执行计划: 导出

## 任务 DAG

## 任务总览

| Task ID | 任务 | 依赖 | 覆盖契约项 | 状态 |
|---------|------|------|-----------|------|
| TASK-001 | 实现导出闭环 | 无 | REQ-order-export-001 | 待做 |

## 任务详情

### TASK-001: 实现导出闭环

- **做什么:** x
- **规格依据:** REQ-order-export-001
- **场景依据:** SCN-order-export-001-01
- **设计依据:** API-01
- **代码证据:** EVD-001
- **验证方法:** pytest 预期结果：通过
- **状态:** 待做
- **完成记录:** 无

## Contract Coverage / 契约覆盖

| Contract Item | Type | 关联 Scenario | 覆盖任务 | 验证方法 |
|---------------|------|---------------|----------|----------|
| REQ-order-export-001 | Behavior | SCN-order-export-001-01 | TASK-001 | pytest |
"""


class ContractTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.slug = "demo-feature"
        self.ctx = HookContext(skill="test", slug=self.slug, root=self.root)
        self.ctx.feature_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, content: str) -> None:
        path = self.ctx.feature_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def run_validator(self, validator) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            failures = validator(self.ctx)
        return failures, output.getvalue()


class ProposalContractTest(ContractTestBase):
    def test_valid_proposal_passes(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        failures, _ = self.run_validator(validate_proposal_contract)
        self.assertEqual(failures, 0)

    def test_missing_capability_index_fails(self) -> None:
        text = PROPOSAL_OK.replace("Capability Index", "Capabilities Legacy")
        self.write("proposal.md", text)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertGreater(failures, 0)
        self.assertIn("invalid_proposal_missing_section", output)

    def test_placeholder_row_fails(self) -> None:
        text = PROPOSAL_OK.replace(
            "| CAP-order-export | order-export | ADDED, MODIFIED | specs/order-export/spec.md | confirmed |",
            "| CAP-[name] | [kebab-case-name] | ADDED | specs/[name]/spec.md | confirmed |",
        )
        self.write("proposal.md", text)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertGreater(failures, 0)
        self.assertIn("capability_index_placeholder_row", output)

    def test_row_path_mismatch_fails(self) -> None:
        text = PROPOSAL_OK.replace("specs/order-export/spec.md", "specs/other/spec.md")
        self.write("proposal.md", text)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertGreater(failures, 0)
        self.assertIn("capability_index_row_mismatch", output)

    def test_open_questions_pending_fails(self) -> None:
        text = PROPOSAL_OK.replace(
            "## Open Questions\n无",
            "## Open Questions\n\n| ID | Question | Impact | Status |\n|----|----------|--------|--------|\n| Q-01 | 待定问题 | 高 | 待确认 |",
        )
        self.write("proposal.md", text)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertGreater(failures, 0)
        self.assertIn("proposal_open_questions_pending", output)


class SpecsContractTest(ContractTestBase):
    def test_valid_strict_spec_passes(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        self.write("specs/order-export/spec.md", SPEC_OK)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertEqual(failures, 0, output)

    def test_capability_id_mismatch_fails(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        self.write(
            "specs/order-export/spec.md",
            SPEC_OK.replace("Capability-ID: CAP-order-export", "Capability-ID: CAP-other"),
        )
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("invalid_spec_capability_id", output)

    def test_req_capability_mismatch_fails(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        self.write(
            "specs/order-export/spec.md",
            SPEC_OK.replace("REQ-order-export-002", "REQ-other-cap-002").replace(
                "SCN-order-export-002-01", "SCN-other-cap-002-01"
            ),
        )
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("spec_req_capability_mismatch", output)

    def test_scenario_without_requirement_fails(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        self.write(
            "specs/order-export/spec.md",
            SPEC_OK.replace("SCN-order-export-002-01", "SCN-order-export-009-01"),
        )
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("scenario_without_requirement", output)

    def test_duplicate_req_across_files_fails(self) -> None:
        proposal = PROPOSAL_OK.replace(
            "| CAP-order-export | order-export | ADDED, MODIFIED | specs/order-export/spec.md | confirmed |",
            "| CAP-order-export | order-export | ADDED, MODIFIED | specs/order-export/spec.md | confirmed |\n"
            "| CAP-order-import | order-import | ADDED | specs/order-import/spec.md | confirmed |",
        )
        self.write("proposal.md", proposal)
        self.write("specs/order-export/spec.md", SPEC_OK)
        duplicate = SPEC_OK.replace("order-export", "order-import").replace(
            "REQ-order-import-001", "REQ-order-export-001"
        )
        self.write("specs/order-import/spec.md", duplicate)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("duplicate_requirement_id", output)

    def test_index_missing_spec_file_fails(self) -> None:
        proposal = PROPOSAL_OK.replace(
            "| CAP-order-export | order-export | ADDED, MODIFIED | specs/order-export/spec.md | confirmed |",
            "| CAP-order-export | order-export | ADDED, MODIFIED | specs/order-export/spec.md | confirmed |\n"
            "| CAP-ghost | ghost | ADDED | specs/ghost/spec.md | confirmed |",
        )
        self.write("proposal.md", proposal)
        self.write("specs/order-export/spec.md", SPEC_OK)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("capability_index_missing_spec", output)

    def test_spec_not_in_index_fails(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        self.write("specs/order-export/spec.md", SPEC_OK)
        extra = SPEC_OK.replace("order-export", "extra-cap")
        self.write("specs/extra-cap/spec.md", extra)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("spec_not_in_capability_index", output)

    def test_operations_mismatch_fails(self) -> None:
        self.write("proposal.md", PROPOSAL_OK.replace("ADDED, MODIFIED", "ADDED"))
        self.write("specs/order-export/spec.md", SPEC_OK)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("capability_operations_mismatch", output)

    def test_legacy_spec_without_header_uses_legacy_rules(self) -> None:
        legacy_spec = (
            "# Legacy Spec\n\n## ADDED Requirements\n\n"
            "### Requirement: 旧格式能力\n\nThe system SHALL x。\n\n"
            "#### Scenario: 旧格式场景\n\n- **WHEN** x\n- **THEN** y\n"
        )
        legacy_proposal = PROPOSAL_OK.replace("Capability Index", "Capabilities")
        self.write("proposal.md", legacy_proposal)
        self.write("specs/legacy-cap/spec.md", legacy_spec)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertEqual(failures, 0, output)


class DesignContractTest(ContractTestBase):
    def test_valid_design_passes(self) -> None:
        self.write("design.md", DESIGN_OK)
        failures, output = self.run_validator(validate_design_contract)
        self.assertEqual(failures, 0, output)

    def test_missing_evidence_fails(self) -> None:
        text = DESIGN_OK.replace("Code Evidence", "CE")
        self.write("design.md", text)
        failures, output = self.run_validator(validate_design_contract)
        self.assertGreaterEqual(failures, 1)
        self.assertIn("invalid_design_missing_section", output)

    def test_pending_cell_fails(self) -> None:
        text = DESIGN_OK.replace("| R-01 | 风险 |", "| R-01 | 读码差异 |")
        self.write("design.md", text)
        failures, output = self.run_validator(validate_design_contract)
        self.assertGreater(failures, 0)
        self.assertIn("design_has_pending_cells", output)

    def test_enum_prose_does_not_false_positive(self) -> None:
        text = DESIGN_OK.replace("| R-01 | 风险 |", "| R-01 | 风险/待确认 |")
        self.write("design.md", text)
        failures, output = self.run_validator(validate_design_contract)
        self.assertEqual(failures, 0, output)


class PlanContractTest(ContractTestBase):
    def test_valid_plan_passes(self) -> None:
        self.write("PLAN.md", PLAN_OK)
        failures, output = self.run_validator(validate_plan_initial_tasks)
        self.assertEqual(failures, 0, output)

    def test_task_id_heading_counted(self) -> None:
        self.write("PLAN.md", PLAN_OK)
        self.assertEqual(task_count(self.ctx.file("PLAN.md")), 1)

    def test_legacy_numeric_heading_counted(self) -> None:
        self.write("PLAN.md", PLAN_OK.replace("### TASK-001: 实现导出闭环", "### 1. 实现导出闭环"))
        self.assertEqual(task_count(self.ctx.file("PLAN.md")), 1)

    def test_missing_coverage_fails(self) -> None:
        text = PLAN_OK.replace("Contract Coverage / 契约覆盖", "无覆盖")
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_initial_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("missing_plan_contract_coverage", output)

    def test_pending_cell_fails(self) -> None:
        text = PLAN_OK.replace(
            "| REQ-order-export-001 | Behavior | SCN-order-export-001-01 | TASK-001 | pytest |",
            "| REQ-order-export-001 | Behavior | SCN-order-export-001-01 | 待确认 | pytest |",
        )
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_initial_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("plan_has_pending_cells", output)

    def test_missing_completion_record_field_fails(self) -> None:
        text = PLAN_OK.replace("- **完成记录:** 无\n", "")
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_initial_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("task_missing_completion_record_field", output)

    def test_prefilled_completion_record_fails(self) -> None:
        text = PLAN_OK.replace("- **完成记录:** 无", "- **完成记录:** 已通过 pytest")
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_initial_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("invalid_initial_completion_record", output)


PLAN_DONE = PLAN_OK.replace("- **状态:** 待做", "- **状态:** 完成").replace(
    "- **完成记录:** 无",
    "- **完成记录:** `pytest -q` 全部通过；改动 src/export.py；commit abc1234",
).replace("| TASK-001 | 实现导出闭环 | 无 | REQ-order-export-001 | 待做 |",
          "| TASK-001 | 实现导出闭环 | 无 | REQ-order-export-001 | 完成 |")


class PlanFinishedContractTest(ContractTestBase):
    def test_finished_with_evidence_passes(self) -> None:
        self.write("PLAN.md", PLAN_DONE)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertEqual(failures, 0, output)

    def test_finished_without_evidence_fails(self) -> None:
        text = PLAN_DONE.replace(
            "- **完成记录:** `pytest -q` 全部通过；改动 src/export.py；commit abc1234",
            "- **完成记录:** 无",
        )
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("task_missing_completion_evidence", output)

    def test_design_decision_uncovered_fails(self) -> None:
        self.write("PLAN.md", PLAN_DONE)
        # design 含 D-01 决策，但 PLAN_DONE 里没有 D-01 → 未覆盖
        self.write("design.md", DESIGN_OK)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("design_decision_uncovered", output)
        self.assertIn("D-01", output)

    def test_design_decision_covered_passes(self) -> None:
        text = PLAN_DONE.replace("- **设计依据:** API-01", "- **设计依据:** API-01, D-01")
        self.write("PLAN.md", text)
        self.write("design.md", DESIGN_OK)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertEqual(failures, 0, output)


class PlanExecutionCheckTest(ContractTestBase):
    def _write_upstream(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        self.write("specs/order-export/spec.md", SPEC_OK)
        self.write("design.md", DESIGN_OK)

    def run_check(self) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = plan_check_main([self.slug, "--workspace-root", str(self.root)])
        return code, output.getvalue()

    def test_pass(self) -> None:
        self._write_upstream()
        self.write("PLAN.md", PLAN_OK)
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertIn("verdict=PASS", output)

    def test_upstream_artifact_change_does_not_block(self) -> None:
        self._write_upstream()
        self.write("PLAN.md", PLAN_OK)
        self.write("design.md", DESIGN_OK + "\n<!-- 手改 -->\n")
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertIn("verdict=PASS", output)

    def test_missing_ref(self) -> None:
        self._write_upstream()
        plan = PLAN_OK.replace(
            "- **规格依据:** REQ-order-export-001", "- **规格依据:** REQ-order-export-999"
        )
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("missing_req_ref", output)

    def test_dependency_cycle(self) -> None:
        self._write_upstream()
        plan = PLAN_OK.replace(
            "| TASK-001 | 实现导出闭环 | 无 | REQ-order-export-001 | 待做 |",
            "| TASK-001 | 实现导出闭环 | TASK-002 | REQ-order-export-001 | 待做 |\n"
            "| TASK-002 | 后续任务 | TASK-001 | REQ-order-export-002 | 待做 |",
        )
        plan += (
            "\n### TASK-002: 后续任务\n\n- **做什么:** x\n- **规格依据:** REQ-order-export-002\n"
            "- **场景依据:** SCN-order-export-002-01\n- **设计依据:** API-01\n- **代码证据:** EVD-001\n"
            "- **验证方法:** pytest 预期结果：通过\n- **状态:** 待做\n- **完成记录:** 无\n"
        )
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("dependency_cycle", output)

    def test_legacy_plan_degrades(self) -> None:
        legacy = (
            "# 执行计划: 旧格式\n\n## 任务总览\n\n| # | 任务 | 依赖 | 状态 |\n|---|---|---|---|\n"
            "| 1 | 旧任务 | 无 | 待做 |\n\n## 任务详情\n\n### 1. 旧任务\n\n- **状态:** 待做\n"
        )
        self.write("PLAN.md", legacy)
        code, output = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("verdict=LEGACY_PLAN_DEGRADE", output)


if __name__ == "__main__":
    unittest.main()
