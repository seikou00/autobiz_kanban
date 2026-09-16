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

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 状态 |
| --- | --- | --- | --- | --- | --- |
| SRC-001 | 外部接口 | 支付网关 API | https://example.test/openapi | REQ-001 / SCN-001 | 可访问 |
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

    def _write_source_context(
        self,
        first: bool,
        *,
        second: bool = False,
    ) -> None:
        """记录单位是文件：一份资料一条 source，没有阶段路由字段。"""
        sources = []
        for source_id, filename, body, include in (
            ("SRC-001", "payment.md", "支付接口调用超时时间为 3 秒。", first),
            ("SRC-002", "reconcile.md", "每日 02:00 生成对账文件。", second),
        ):
            if not include:
                continue
            snapshot = self.feature_dir / "sources" / source_id / filename
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(body, encoding="utf-8")
            sources.append({
                "id": source_id,
                "name": source_id,
                "path": "sources/%s/%s" % (source_id, filename),
                "availability": "snapshot_only",
                "readStatus": "complete",
                "freshness": "unknown",
            })
        (self.feature_dir / "source-context.json").write_text(
            json.dumps({"version": 1, "sources": sources}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _run(self, validator, *, skill: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            failures = validator(HookContext(skill=skill, slug="alpha", root=self.project))
        return failures, output.getvalue()

    def test_specs_allow_optional_source_table(self) -> None:
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        spec_path.write_text(SPEC.format(source_rows="无"), encoding="utf-8")

        failures, output = self._run(validate_specs_contract, skill="autodev-specs")

        self.assertEqual(failures, 0, output)

        spec_path.write_text(
            SPEC.format(source_rows="| Source ID | Requirement / Scenario | Usage |\n|---|---|---|\n| SRC-001 | REQ-001 / SCN-001 | 支付网关行为约束 |"),
            encoding="utf-8",
        )
        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

    def test_specs_map_spec_sources_without_copying_requirement_ids_into_body(self) -> None:
        self._write_source_context(True)
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        source_row = "| Source ID | Requirement / Scenario | Usage |\n|---|---|---|\n| SRC-001 | REQ-001 / SCN-001 | 支付网关行为约束 |"
        spec_path.write_text(SPEC.format(source_rows=source_row), encoding="utf-8")

        failures, output = self._run(validate_specs_contract, skill="autodev-specs")

        self.assertEqual(failures, 0, output)

    def test_specs_allow_inline_source_references(self) -> None:
        self._write_source_context(True)
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        source_row = "| Source ID | Requirement / Scenario | Usage |\n|---|---|---|\n| SRC-001 | REQ-001 / SCN-001 | 支付网关行为约束 |"
        spec_path.write_text(
            SPEC.format(source_rows=source_row).replace(
                "The system SHALL 按网关契约提交支付。",
                "The system SHALL 按网关契约提交支付。来源：SRC-001。",
            ),
            encoding="utf-8",
        )
        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

    def test_background_source_without_spec_requirements_needs_no_fake_mapping(self) -> None:
        self._write_source_context(False)
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        spec_path.write_text(SPEC.format(source_rows="无"), encoding="utf-8")

        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

        optional_row = (
            "| Source ID | Requirement / Scenario | Usage |\n"
            "|---|---|---|\n"
            "| SRC-001 | - | background，仅作上下文 |"
        )
        spec_path.write_text(SPEC.format(source_rows=optional_row), encoding="utf-8")
        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

    def test_spec_targeted_source_does_not_force_a_mapping_table(self) -> None:
        self._write_source_context(True)
        spec_path = self.feature_dir / "specs" / "payment" / "spec.md"
        spec_path.write_text(SPEC.format(source_rows="无"), encoding="utf-8")

        failures, output = self._run(validate_specs_contract, skill="autodev-specs")
        self.assertEqual(failures, 0, output)

if __name__ == "__main__":
    unittest.main()
