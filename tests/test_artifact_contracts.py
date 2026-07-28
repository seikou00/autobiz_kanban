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
    find_template_guidance_residue,
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

PROPOSAL_RESOLVED = PROPOSAL_OK.replace(
    "## Decision Log\n无\n",
    """## Decision Log

### DEC-001: 导出文件保留 7 天

- **决定:** 导出结果在对象存储保留 7 天后自动清理
- **为什么:** 与既有报表清理策略一致，不额外引入配置项
- **否决:** 永久保留（存储成本不可控）
- **约束:** REQ-order-export-001
""",
).replace(
    "## Open Questions\n无\n",
    """## Open Questions

| ID | Question | Impact | Resolution | Decision | Status |
|----|----------|--------|------------|----------|--------|
| Q-01 | 导出文件保留多久？ | 影响验收口径与存储成本 | 保留 7 天后自动清理 | DEC-001 | 已确认 |
""",
)

PROPOSAL_LEGACY_OPEN_QUESTIONS = PROPOSAL_OK.replace(
    "## Open Questions\n无\n",
    """## Open Questions

| ID | Question | Impact | Status |
|----|----------|--------|--------|
| Q-01 | 导出文件保留多久？ | 影响验收口径 | 已确认 |
""",
)

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
- **设计依据:** API-01, D-01
- **代码证据:** EVD-001
- **验证方法:** pytest 预期结果：通过
- **状态:** 待做
- **完成记录:** 无

## Contract Coverage / 契约覆盖

| Contract Item | Type | 关联 Scenario | 覆盖任务 | 验证方法 |
|---------------|------|---------------|----------|----------|
| REQ-order-export-001 | Behavior | SCN-order-export-001-01 | TASK-001 | pytest |
| API-01 | API | - | TASK-001 | pytest |
| D-01 | Technical Decision | - | TASK-001 | pytest |
"""


class TemplateGuidanceScannerTest(unittest.TestCase):
    def test_blockquote_inside_fence_and_comparison_operator_pass(self) -> None:
        text = "# 示例\n\n````markdown\n> 围栏内引用\n````\n\n比较结果：a > b\n"
        self.assertEqual(find_template_guidance_residue(text), [])

    def test_outer_markdown_fence_fails(self) -> None:
        text = "```markdown\n# Proposal: 示例\n```\n"
        self.assertEqual(
            find_template_guidance_residue(text),
            [(1, "outer_markdown_fence")],
        )

    def test_known_wrapper_heading_fails(self) -> None:
        self.assertEqual(
            find_template_guidance_residue("# 计划模板\n"),
            [(1, "wrapper_heading")],
        )

    def test_autodev_output_templates_are_clean(self) -> None:
        templates = {
            ROOT / "skills/autodev/autodev-specs/templates/proposal.md": "# Proposal:",
            ROOT / "skills/autodev/autodev-specs/templates/spec.md": "# [Capability Name] Specification",
            ROOT / "skills/autodev/autodev-plan/templates/design.md": "# 技术设计:",
            ROOT / "skills/autodev/autodev-plan/templates/plan.md": "# 执行计划:",
        }
        for template, expected_prefix in templates.items():
            with self.subTest(template=template):
                text = template.read_text(encoding="utf-8")
                self.assertTrue(text.startswith(expected_prefix))
                self.assertEqual(find_template_guidance_residue(text), [])


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

    def test_template_guidance_fails_with_location(self) -> None:
        text = PROPOSAL_OK.replace(
            "## Capability Index",
            "## Capability Index\n\n> 本表是 capability 的唯一权威索引。",
        )
        self.write("proposal.md", text)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertGreater(failures, 0)
        self.assertIn("artifact_template_guidance_residue", output)
        self.assertIn("file='proposal.md' line=11 kind=blockquote", output)


class OpenQuestionsEvidenceTest(ContractTestBase):
    """「已确认」必须带跨文件证据：只翻 Status 不算消解。"""

    def write_feature(self, proposal: str) -> None:
        self.write("proposal.md", proposal)
        self.write("specs/order-export/spec.md", SPEC_OK)

    def assert_fails_with(self, proposal: str, reason: str) -> None:
        self.write_feature(proposal)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertGreater(failures, 0, output)
        self.assertIn(reason, output)

    def test_resolved_row_with_evidence_passes(self) -> None:
        self.write_feature(PROPOSAL_RESOLVED)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertEqual(failures, 0, output)

    def test_deleting_whole_section_fails(self) -> None:
        text = PROPOSAL_OK.replace("## Open Questions\n无\n", "")
        self.assert_fails_with(text, "invalid_proposal_missing_section")

    def test_placeholder_resolution_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace("保留 7 天后自动清理", "[用户裁定的具体结论]")
        self.assert_fails_with(text, "open_questions_resolution_missing")

    def test_resolution_restating_question_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace("保留 7 天后自动清理", "导出文件保留多久")
        self.assert_fails_with(text, "open_questions_resolution_restates_question")

    def test_missing_decision_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace("| 保留 7 天后自动清理 | DEC-001 |", "| 保留 7 天后自动清理 |  |")
        self.assert_fails_with(text, "open_questions_decision_missing")

    def test_decision_not_in_log_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace("| DEC-001 | 已确认 |", "| DEC-009 | 已确认 |")
        self.assert_fails_with(text, "open_questions_decision_not_in_log")

    def test_decision_with_placeholder_body_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace(
            "- **为什么:** 与既有报表清理策略一致，不额外引入配置项",
            "- **为什么:** [理由]",
        )
        self.assert_fails_with(text, "open_questions_decision_not_in_log")

    def test_decision_unbound_to_specs_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace(
            "- **约束:** REQ-order-export-001", "- **约束:** REQ-order-export-999"
        )
        self.assert_fails_with(text, "open_questions_decision_unbound")

    def test_status_not_resolved_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace("| DEC-001 | 已确认 |", "| DEC-001 | 讨论中 |")
        self.assert_fails_with(text, "open_questions_status_invalid")

    def test_pending_row_reports_once(self) -> None:
        text = PROPOSAL_RESOLVED.replace("| DEC-001 | 已确认 |", "| DEC-001 | 待确认 |")
        self.write_feature(text)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertEqual(failures, 1, output)
        self.assertIn("proposal_open_questions_pending", output)

    def test_legacy_table_degrades(self) -> None:
        self.write_feature(PROPOSAL_LEGACY_OPEN_QUESTIONS)
        failures, output = self.run_validator(validate_proposal_contract)
        self.assertEqual(failures, 0, output)
        self.assertIn("open_questions_legacy_degrade", output)

    def test_legacy_table_still_blocks_pending(self) -> None:
        text = PROPOSAL_LEGACY_OPEN_QUESTIONS.replace("| 影响验收口径 | 已确认 |", "| 影响验收口径 | 待确认 |")
        self.assert_fails_with(text, "proposal_open_questions_pending")

    def test_template_row_left_in_fails(self) -> None:
        text = PROPOSAL_RESOLVED.replace(
            "| Q-01 | 导出文件保留多久？ | 影响验收口径与存储成本 | 保留 7 天后自动清理 | DEC-001 | 已确认 |",
            "| Q-01 | [待确认问题；无则本节正文只写“无”] | [影响] | [用户裁定的具体结论] | DEC-001 | 已确认 |",
        )
        self.assert_fails_with(text, "open_questions_resolution_missing")


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

    def test_template_guidance_fails_with_location(self) -> None:
        self.write("proposal.md", PROPOSAL_OK)
        text = SPEC_OK.replace(
            "Capability-ID: CAP-order-export",
            "Capability-ID: CAP-order-export\n\n> 稳定 ID 规则：",
        )
        self.write("specs/order-export/spec.md", text)
        failures, output = self.run_validator(validate_specs_contract)
        self.assertGreater(failures, 0)
        self.assertIn("artifact_template_guidance_residue", output)
        self.assertIn("file='specs/order-export/spec.md' line=5 kind=blockquote", output)


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

    def test_template_guidance_fails_with_location(self) -> None:
        text = DESIGN_OK.replace(
            "# 技术设计: 导出",
            "# 技术设计: 导出\n\n> 探索得到的代码事实逐条落盘。",
        )
        self.write("design.md", text)
        failures, output = self.run_validator(validate_design_contract)
        self.assertGreater(failures, 0)
        self.assertIn("artifact_template_guidance_residue", output)
        self.assertIn("file='design.md' line=3 kind=blockquote", output)


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

    def test_template_guidance_fails_with_location(self) -> None:
        text = PLAN_OK.replace(
            "# 执行计划: 导出",
            "# 执行计划: 导出\n\n> 唯一覆盖表说明。",
        )
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_initial_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("artifact_template_guidance_residue", output)
        self.assertIn("file='PLAN.md' line=3 kind=blockquote", output)


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
        # 把 D-01 从 PLAN 中彻底抹掉：design 有该决策而 PLAN 一处不提 → 未覆盖
        text = PLAN_DONE.replace("- **设计依据:** API-01, D-01", "- **设计依据:** API-01")
        text = text.replace("| D-01 | Technical Decision | - | TASK-001 | pytest |\n", "")
        self.write("PLAN.md", text)
        self.write("design.md", DESIGN_OK)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("design_decision_uncovered", output)
        self.assertIn("D-01", output)

    def test_design_decision_covered_passes(self) -> None:
        self.write("PLAN.md", PLAN_DONE)
        self.write("design.md", DESIGN_OK)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertEqual(failures, 0, output)

    def test_template_guidance_fails_after_execution(self) -> None:
        text = PLAN_DONE.replace(
            "# 执行计划: 导出",
            "# 执行计划: 导出\n\n> Code 阶段模板说明。",
        )
        self.write("PLAN.md", text)
        failures, output = self.run_validator(validate_plan_finished_tasks)
        self.assertGreater(failures, 0)
        self.assertIn("artifact_template_guidance_residue", output)
        self.assertIn("file='PLAN.md' line=3 kind=blockquote", output)


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

    def test_uncovered_design_decision_fails(self) -> None:
        """design 的决策没被任何任务认领 → 不得放行。

        引用校验只查 PLAN→design 方向，漏了这一向的话，裁定门产出的 API/DATA/D
        可以在 PLAN 生成时被静默丢弃，而 code 只按任务「设计依据」展开。
        """
        self._write_upstream()
        plan = PLAN_OK.replace("- **设计依据:** API-01, D-01", "- **设计依据:** API-01")
        plan = plan.replace("| D-01 | Technical Decision | - | TASK-001 | pytest |\n", "")
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("uncovered_design_decision", output)
        self.assertIn("id=D-01", output)

    def test_waived_design_decision_passes(self) -> None:
        """Contract Coverage 标注「无需实现:<理由>」是合法出口。"""
        self._write_upstream()
        plan = PLAN_OK.replace("- **设计依据:** API-01, D-01", "- **设计依据:** API-01")
        plan = plan.replace(
            "| D-01 | Technical Decision | - | TASK-001 | pytest |",
            "| D-01 | Technical Decision | - | 无需实现:本轮沿用既有队列实现 | - |",
        )
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 0, output)

    def test_waiver_without_reason_fails(self) -> None:
        """「无需实现」不带理由就是免费出口，反向校验等于没加。"""
        self._write_upstream()
        plan = PLAN_OK.replace("- **设计依据:** API-01, D-01", "- **设计依据:** API-01")
        plan = plan.replace(
            "| D-01 | Technical Decision | - | TASK-001 | pytest |",
            "| D-01 | Technical Decision | - | 无需实现 | - |",
        )
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("waiver_missing_reason", output)

    def test_task_claim_outranks_waiver_wording(self) -> None:
        """模板把「TASK-002 / 无需实现:<原因>」并排摆在同一格。

        任务已认领的 ID 不该因为同格还留着豁免措辞就报 waiver_missing_reason。
        """
        self._write_upstream()
        plan = PLAN_OK.replace(
            "| D-01 | Technical Decision | - | TASK-001 | pytest |",
            "| D-01 | Technical Decision | - | TASK-001 / 无需实现:[原因] | pytest |",
        )
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 0, output)

    def test_evidence_ids_need_no_coverage(self) -> None:
        """EVD 是代码事实，不是待落地的决策，不进反向覆盖。"""
        self._write_upstream()
        plan = PLAN_OK.replace("- **代码证据:** EVD-001", "- **代码证据:** 无")
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 0, output)

    def test_structured_design_without_ids_still_checks_refs(self) -> None:
        """新格式 design.md 无 ID 行时不得整体 degrade。

        「本轮真的无 API/无 SQL」是模板鼓励的合法状态，和「legacy 无 ID 体系」
        共用跳过分支的话，PLAN 引用捏造 ID 也会放行。
        """
        self._write_upstream()
        self.write(
            "design.md",
            "# 技术设计\n\n## 4. API Decisions\n\n- **x-auto-no-http-api:** true\n"
            "\n## 5. Data Decisions\n\n- **x-auto-no-sql:** true\n",
        )
        plan = PLAN_OK.replace("- **设计依据:** API-01, D-01", "- **设计依据:** API-99")
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("missing_design_ref", output)
        self.assertNotIn("legacy_design_skip_design_ref_check", output)

    def test_legacy_design_still_degrades(self) -> None:
        """无 ID 行也无格式标记的老 design.md 保持 degrade，历史 feature 可回放。"""
        self._write_upstream()
        self.write("design.md", "# 技术设计\n\n随手写的老格式，没有 ID 体系。\n")
        plan = PLAN_OK.replace("- **设计依据:** API-01, D-01", "- **设计依据:** API-99")
        self.write("PLAN.md", plan)
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertIn("legacy_design_skip_design_ref_check", output)

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
