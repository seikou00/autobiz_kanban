"""Specs retain identity, ownership and capability coverage, not document ceremony."""

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
    contract_id_width_errors,
    malformed_contract_headings,
    proposal_capabilities,
    scenarios_without_requirement,
    validate_capability_spec_correspondence,
    validate_proposal_contract,
    validate_specs_contract,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402


PROPOSAL_HEAD = """# Proposal: 导出

## Why

需要导出。

## What Changes

- 新增导出入口

## Capabilities
"""

PROPOSAL_TAIL = """
## Impact

- 影响模块: export

## Out of Scope

- 不做批量删除

## Decision Log

无

## Open Questions

无
"""


def proposal_text(
    new: list[str],
    modified: list[str] = (),
    removed: list[str] = (),
) -> str:
    def group(title: str, names: list[str]) -> str:
        lines = [f"- `{name}`: 说明" for name in names]
        body = "\n".join(lines) or "- 无"
        return f"\n### {title}\n\n{body}\n"

    return (
        PROPOSAL_HEAD
        + group("New Capabilities", list(new))
        + group("Modified Capabilities", list(modified))
        + group("Removed Capabilities", list(removed))
        + PROPOSAL_TAIL
    )


SPEC_ONE_REQ = """## ADDED Requirements

### Requirement [REQ-001]: 创建导出任务

The system SHALL 支持创建导出任务。

#### Scenario [SCN-001]: 创建成功

- **WHEN** 用户提交导出请求
- **THEN** 系统 SHALL 返回任务号
"""

SPEC_TWO_REQ_ONE_SCENARIO = """## ADDED Requirements

### Requirement [REQ-001]: 创建导出任务

The system SHALL 支持创建导出任务。

#### Scenario [SCN-001]: 创建成功

- **WHEN** 用户提交导出请求
- **THEN** 系统 SHALL 返回任务号

### Requirement [REQ-002]: 取消导出任务

The system SHALL 支持取消导出任务。
"""


SPEC_MODIFIED_REQ = """## ADDED Requirements

## MODIFIED Requirements

### Requirement [REQ-001]: 审批提醒频率

The system SHALL 支持配置提醒频率。

#### Scenario [SCN-001]: 改为每日

- **WHEN** 管理员设为每日
- **THEN** 系统 SHALL 每日提醒一次

## REMOVED Requirements
"""


class SpecContractValidatorTestBase(unittest.TestCase):
    def _feature(self, tmp: str) -> tuple[Path, Path]:
        project = Path(tmp).resolve() / "demo"
        project.mkdir()
        init_workspace(project)
        create_feature(project, "alpha")
        return project, project / ".autobizdevops" / "features" / "alpha"

    def _write_spec(self, feature_dir: Path, capability: str, body: str = SPEC_ONE_REQ) -> None:
        spec = feature_dir / "specs" / capability / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(body, encoding="utf-8")

    def _run(self, validator, project: Path) -> tuple[int, str]:
        ctx = HookContext(skill="autodev-specs", slug="alpha", root=project)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failures = validator(ctx)
        return failures, buffer.getvalue()


class CapabilitySpecCorrespondenceTest(SpecContractValidatorTestBase):
    def test_matching_capabilities_and_specs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"], modified=["approval-reminder"]),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            self._write_spec(feature_dir, "approval-reminder", SPEC_MODIFIED_REQ)
            failures, _ = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0)

    def test_listed_capability_without_spec_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export", "approval-reminder"]),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("proposal_capability_missing_spec", output)
            self.assertIn("approval-reminder", output)
            self.assertIn("POST_SKILL_REPAIR", output)

    def test_spec_without_listed_capability_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"]),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            self._write_spec(feature_dir, "stowaway-capability")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("spec_missing_proposal_capability", output)
            self.assertIn("stowaway-capability", output)

    def test_empty_groups_written_as_wu_are_not_capabilities(self) -> None:
        """空分组写「无」不得被当成一个叫「无」的 capability。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"]),  # Modified / Removed 两组都写「无」
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, _ = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0)

    def test_missing_proposal_does_not_double_report(self) -> None:
        """缺 proposal 是 proposal_contract 的失败，本校验不重复报。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0)
            self.assertEqual(output, "")


class RequirementScenarioCoverageTest(SpecContractValidatorTestBase):
    def test_every_requirement_with_scenario_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_specs_contract, project)
            self.assertEqual(failures, 0, output)

    def test_requirement_without_own_scenario_is_blocked(self) -> None:
        """两个 Requirement 共用一个 Scenario：旧的文件级检查会放行。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write_spec(feature_dir, "order-export", SPEC_TWO_REQ_ONE_SCENARIO)
            failures, output = self._run(validate_specs_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("spec_requirement_without_scenario", output)
            self.assertIn("REQ-002", output)
            self.assertNotIn("REQ-001", output.split("requirements=")[1])


class MalformedContractHeadingTest(SpecContractValidatorTestBase):
    """索引器接受有无方括号两种写法，并拦截其余畸形标题。"""

    MALFORMED = [
        "### Requirement [REQ-002: 方括号未闭合",
        "#### Scenario SCN-002]: 多余右方括号",
        "### REQ-order-export-001: 已废除的 capability 前缀式",
        "#### SCN-order-export-001-01: 已废除的 capability 前缀式",
        "### [REQ-002]: 缺 Requirement 字样",
        "## Requirement [REQ-001]: 标题层级错",
        "### Scenario [SCN-001]: 标题层级错",
    ]

    WELL_FORMED = [
        "### Requirement [REQ-001]: 正常",
        "#### Scenario [SCN-001]: 正常",
        "### Requirement REQ-002: 无方括号",
        "#### Scenario SCN-002: 无方括号",
        "## ADDED Requirements",
        "## 稳定 ID 规范",
        "- Requirement ID 统一使用 `REQ-001`、`REQ-002`",
        "### Requirement [REQ-001]: 兼容 REQ-002 的前置",
    ]

    def test_each_malformed_shape_is_detected(self) -> None:
        for line in self.MALFORMED:
            with self.subTest(line=line):
                self.assertEqual(malformed_contract_headings(line), [line])

    def test_well_formed_and_prose_are_not_flagged(self) -> None:
        for line in self.WELL_FORMED:
            with self.subTest(line=line):
                self.assertEqual(malformed_contract_headings(line), [])

    def test_numeric_width_error_has_one_dedicated_diagnostic(self) -> None:
        line = "### Requirement [REQ-1001]: 位数过长"
        self.assertEqual(malformed_contract_headings(line), [])
        errors = contract_id_width_errors(line)
        self.assertEqual([error.current for error in errors], ["REQ-1001"])
        self.assertEqual(errors[0].suggested, "REQ-001")

    def test_spec_template_is_clean(self) -> None:
        template = ROOT / "skills/autodev/autodev-specs/templates/spec.md"
        self.assertEqual(
            malformed_contract_headings(template.read_text(encoding="utf-8")),
            [],
            "模板教的写法必须正好是索引器认的写法",
        )

    def test_one_malformed_heading_blocks_an_otherwise_valid_spec(self) -> None:
        """一个合法 REQ 就让整个文件通过——这正是旧检查删除后打开的口子。"""
        body = SPEC_ONE_REQ + "\n### Requirement [REQ-002: 畸形\n\n#### Scenario SCN-002]: 也畸形\n"
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_specs_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("spec_contract_heading_malformed", output)
            self.assertIn("REQ-002", output)


class SpecIdIntegrityTest(SpecContractValidatorTestBase):
    """ID 层面的机械事实：feature 级唯一、文档顺序递增、Scenario 有归属。"""

    def test_same_id_in_two_specs_is_blocked(self) -> None:
        """重号会让覆盖门真空满足——扁平 ID 集合分不出是哪个 capability 的。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export", "approval-reminder"]), encoding="utf-8"
            )
            self._write_spec(feature_dir, "order-export")
            self._write_spec(feature_dir, "approval-reminder")
            failures, output = self._run(validate_specs_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("duplicate_spec_id_across_specs", output)
            self.assertIn("REQ-001", output)
            self.assertIn("order-export/spec.md:REQ-001->REQ-002", output)
            self.assertIn("order-export/spec.md:SCN-001->SCN-002", output)
            self.assertIn("POST_SKILL_REPAIR", output)

    def test_four_digit_ids_only_report_width_with_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            body = SPEC_ONE_REQ.replace("REQ-001", "REQ-1001").replace(
                "SCN-001", "SCN-1001"
            )
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_specs_contract, project)
            self.assertEqual(failures, 2, output)
            fail_lines = [
                line for line in output.splitlines()
                if "POST_SKILL_FAIL" in line and "reason=spec_id_width_invalid" in line
            ]
            self.assertEqual(len(fail_lines), 2)
            self.assertNotIn("invalid_spec_missing_requirement", output)
            self.assertNotIn("invalid_spec_missing_scenario", output)
            self.assertNotIn("spec_contract_heading_malformed", output)
            self.assertNotIn("spec_placeholder_residue", output)

    def test_distinct_ids_across_specs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export", "approval-reminder"]), encoding="utf-8"
            )
            self._write_spec(feature_dir, "order-export")
            self._write_spec(
                feature_dir,
                "approval-reminder",
                SPEC_ONE_REQ.replace("REQ-001", "REQ-002").replace("SCN-001", "SCN-002"),
            )
            failures, output = self._run(validate_specs_contract, project)
            self.assertEqual(failures, 0, output)

    def test_mid_file_insertion_needs_no_renumbering(self) -> None:
        """在中间插入一个更大的编号是合法的，不再要求文档顺序递增。

        递增检查换来的只是观感，代价却是级联重编：往中间插一条 Requirement，
        它后面每一个 REQ/SCN 都得改号，所有下游引用跟着失效。唯一性由
        duplicate_* 保证，可追溯性由 ID 本身保证，顺序不承担任何契约含义。
        """
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            body = SPEC_ONE_REQ + (
                "\n### Requirement [REQ-009]: 后插入的能力\n\n"
                "The system SHALL 做另一件事。\n\n"
                "#### Scenario [SCN-009]: s\n\n- **WHEN** a\n- **THEN** b\n"
                "\n### Requirement [REQ-002]: 原本就在后面的能力\n\n"
                "The system SHALL 做第三件事。\n\n"
                "#### Scenario [SCN-002]: t\n\n- **WHEN** a\n- **THEN** b\n"
            )
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_specs_contract, project)
            self.assertEqual(failures, 0, output)

    def test_scenario_before_any_requirement_is_orphaned(self) -> None:
        self.assertEqual(
            scenarios_without_requirement(
                "#### Scenario [SCN-001]: s\n\n### Requirement [REQ-001]: a\n"
            ),
            ["SCN-001"],
        )

    def test_scenario_under_section_heading_is_orphaned(self) -> None:
        """新的 `## ` 段关闭上一个 Requirement，段标题正下方的 Scenario 无归属。"""
        text = (
            "### Requirement [REQ-001]: a\n\n#### Scenario [SCN-001]: s\n\n"
            "## MODIFIED Requirements\n\n#### Scenario [SCN-002]: 孤儿\n"
        )
        self.assertEqual(scenarios_without_requirement(text), ["SCN-002"])

    def test_owned_scenario_is_not_orphaned(self) -> None:
        self.assertEqual(scenarios_without_requirement(SPEC_ONE_REQ), [])

    def test_orphan_scenario_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            body = "## ADDED Requirements\n\n#### Scenario [SCN-009]: 无主场景\n\n" + SPEC_ONE_REQ
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_specs_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("spec_scenario_without_requirement", output)
            self.assertIn("SCN-009", output)


class ValidatorRegistrationTest(unittest.TestCase):
    def test_capability_correspondence_is_registered_on_dev_specs(self) -> None:
        """只进 VALIDATORS 不进 board_config，等于写了一段永不执行的死代码。"""
        config = json.loads(
            (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        )
        specs_node = next(
            node
            for node in config["workflow"]["nodes"]
            if node.get("id") == "dev.specs"
        )
        self.assertIn("capability_spec_correspondence", specs_node["validators"])


if __name__ == "__main__":
    unittest.main()
