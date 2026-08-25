from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

from artifact_check import (  # noqa: E402
    HookContext,
    validate_design_contract,
    validate_e2e_cases_contract,
    validate_specs_contract,
)


PRD = """# 需求正式稿

## 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| SRC-001 | 外部接口 | 支付网关 API | https://example.test/openapi | REQ-001 / SCN-001 | Specs、Plan、Code、Reviewer、E2E | 可访问 |
"""

SPEC = """# Payment Specification

## Source References / 外部资料引用

{source_rows}

## ADDED Requirements

### Requirement [REQ-001]: 提交支付

The system SHALL 按网关契约提交支付。

#### Scenario [SCN-001]: 支付成功

- **WHEN** 用户提交有效付款
- **THEN** 系统返回支付成功结果
"""

DESIGN = """# Design

## Context / 输入上下文

支付能力设计。

{source_section}

## Code Evidence

| Evidence ID | Path / Symbol | Observed Fact | Verified At |
|---|---|---|---|
| EVD-001 | src/payment.py | 已有支付适配器 | abc123 |

## Spec Traceability

| Requirement | Scenarios | Decision | Design Coverage | Evidence |
|---|---|---|---|---|
| REQ-001 | SCN-001 | 无 | API-001 | EVD-001 |

## API Decisions

x-auto-no-http-api: false

| ID | Source Refs | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |
|---|---|---|---|---|---|---|---|---|
| API-001 | {api_source} | POST | /payments | amount | payment id | gateway error | OAuth | 已确认 |

## Data Decisions

x-auto-no-sql: true

无数据结构变更。

## Technical Design

复用支付适配器并校验网关响应。

## Risks / Open Questions

无
"""


class ExternalSourceTraceabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "demo"
        self.feature_dir = self.project / ".autobizdevops" / "features" / "alpha"
        (self.feature_dir / "specs" / "payment").mkdir(parents=True)
        (self.feature_dir / "PRD.md").write_text(PRD, encoding="utf-8")

    def _write_source_context(self, targets: list[str]) -> None:
        snapshot = self.feature_dir / "sources" / "SRC-001" / "payment.md"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text("支付接口调用超时时间为 3 秒。", encoding="utf-8")
        (self.feature_dir / "source-context.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": [
                        {
                            "id": "SRC-001",
                            "name": "支付接口",
                            "path": "sources/SRC-001/payment.md",
                            "availability": "snapshot_only",
                            "readStatus": "complete",
                            "freshness": "unknown",
                            "sha256": "0" * 64,
                            "items": [
                                {
                                    "id": "SRC-001-I001",
                                    "location": "第 1 行",
                                    "original": "支付接口调用超时时间为 3 秒。",
                                    "disposition": "requirement",
                                    "requirements": [
                                        {
                                            "id": "SRC-001-R001",
                                            "text": "支付接口调用超时时间为 3 秒",
                                            "targets": targets,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _run(self, validator, *, skill: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            failures = validator(HookContext(skill=skill, slug="alpha", root=self.project))
        return failures, output.getvalue()

    def test_specs_must_preserve_every_prd_source_id(self) -> None:
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        spec_path.write_text(SPEC.format(source_rows="无"), encoding="utf-8")

        failures, output = self._run(validate_specs_contract, skill="autodev-specs")

        self.assertGreater(failures, 0)
        self.assertIn("spec_source_reference_missing", output)

        spec_path.write_text(
            SPEC.format(source_rows="| Source ID | Requirement / Scenario | Usage |\n|---|---|---|\n| SRC-001 | REQ-001 / SCN-001 | 支付网关行为约束 |"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

    def test_specs_must_consume_requirements_targeted_to_spec(self) -> None:
        self._write_source_context(["spec"])
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        source_row = "| Source ID | Requirement / Scenario | Usage |\n|---|---|---|\n| SRC-001 | REQ-001 / SCN-001 | 支付网关行为约束 |"
        spec_path.write_text(SPEC.format(source_rows=source_row), encoding="utf-8")

        failures, output = self._run(validate_specs_contract, skill="autodev-specs")

        self.assertGreater(failures, 0)
        self.assertIn("spec_source_requirement_missing", output)

        spec_path.write_text(
            SPEC.format(source_rows=source_row).replace(
                "The system SHALL 按网关契约提交支付。",
                "The system SHALL 按网关契约提交支付。来源要求：SRC-001-R001。",
            ),
            encoding="utf-8",
        )
        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

    def test_design_requires_source_coverage_and_api_link(self) -> None:
        missing_section = DESIGN.format(source_section="", api_source="无")
        (self.feature_dir / "design.md").write_text(missing_section, encoding="utf-8")

        failures, output = self._run(validate_design_contract, skill="autodev-plan")

        self.assertGreater(failures, 0)
        self.assertIn("invalid_design_missing_section", output)

        coverage = """## External Source Coverage / 外部资料覆盖

| Source ID | Related Requirement / Scenario | Design Coverage | Consumption Evidence | Status |
|---|---|---|---|---|
| SRC-001 | REQ-001 / SCN-001 | API-001 | https://example.test/openapi；POST /payments | 已消费 |"""
        empty_evidence = coverage.replace(
            "https://example.test/openapi；POST /payments",
            "无",
        )
        (self.feature_dir / "design.md").write_text(
            DESIGN.format(source_section=empty_evidence, api_source="SRC-001"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_design_contract, skill="autodev-plan")
        self.assertGreater(failures, 0)
        self.assertIn("design_source_consumption_evidence_missing", output)

        (self.feature_dir / "design.md").write_text(
            DESIGN.format(source_section=coverage, api_source="SRC-001"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_design_contract, skill="autodev-plan")
        self.assertEqual(failures, 0, output)

    def test_design_must_consume_requirements_targeted_to_design(self) -> None:
        self._write_source_context(["design"])
        coverage = """## External Source Coverage / 外部资料覆盖

| Source ID | Related Requirement / Scenario | Design Coverage | Consumption Evidence | Status |
|---|---|---|---|---|
| SRC-001 | REQ-001 / SCN-001 | API-001 | sources/SRC-001/payment.md；3 秒超时 | 已消费 |"""
        design = DESIGN.format(source_section=coverage, api_source="SRC-001")
        (self.feature_dir / "design.md").write_text(design, encoding="utf-8")

        failures, output = self._run(validate_design_contract, skill="autodev-plan")

        self.assertGreater(failures, 0)
        self.assertIn("design_source_requirement_missing", output)

        (self.feature_dir / "design.md").write_text(
            design.replace("复用支付适配器并校验网关响应。", "复用支付适配器并校验网关响应。设计依据：SRC-001-R001。"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_design_contract, skill="autodev-plan")
        self.assertEqual(failures, 0, output)

    def test_e2e_cases_must_cover_external_interface_sources(self) -> None:
        cases = """id: E2E-alpha-001
status: pending
title: 支付成功
execution_mode: api
ui_required: false
source:
  feature: alpha
  external_sources: []
  specs_contract:
    - requirement: REQ-001
      scenario: SCN-001
steps:
  - verification: api
"""
        (self.feature_dir / "E2E_TEST_CASES.yaml").write_text(cases, encoding="utf-8")
        (self.feature_dir / "e2e-run.log").write_text("{}\n", encoding="utf-8")

        failures, output = self._run(validate_e2e_cases_contract, skill="autodev-e2e")

        self.assertGreater(failures, 0)
        self.assertIn("e2e_external_source_coverage_missing", output)

        (self.feature_dir / "E2E_TEST_CASES.yaml").write_text(
            cases.replace("external_sources: []", "external_sources: [SRC-001]"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_e2e_cases_contract, skill="autodev-e2e")
        self.assertEqual(failures, 0, output)

        (self.feature_dir / "E2E_TEST_CASES.yaml").write_text(
            cases.replace("external_sources: []", "external_sources: [SRC-001, SRC-999]"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_e2e_cases_contract, skill="autodev-e2e")
        self.assertGreater(failures, 0)
        self.assertIn("e2e_external_source_unknown", output)

    def test_e2e_cases_must_consume_requirements_targeted_to_e2e(self) -> None:
        self._write_source_context(["e2e"])
        cases = """id: E2E-alpha-001
status: pending
title: 支付超时
execution_mode: api
ui_required: false
source:
  feature: alpha
  external_sources: [SRC-001]
  source_requirements: []
  specs_contract:
    - requirement: REQ-001
      scenario: SCN-001
steps:
  - verification: api
"""
        (self.feature_dir / "E2E_TEST_CASES.yaml").write_text(cases, encoding="utf-8")
        (self.feature_dir / "e2e-run.log").write_text("{}\n", encoding="utf-8")

        failures, output = self._run(validate_e2e_cases_contract, skill="autodev-e2e")

        self.assertGreater(failures, 0)
        self.assertIn("e2e_source_requirement_coverage_missing", output)

        (self.feature_dir / "E2E_TEST_CASES.yaml").write_text(
            cases.replace("source_requirements: []", "source_requirements: [SRC-001-R001]"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_e2e_cases_contract, skill="autodev-e2e")
        self.assertEqual(failures, 0, output)


if __name__ == "__main__":
    unittest.main()
