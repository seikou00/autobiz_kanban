"""specs 阶段的机械规则必须由脚本判定，而不是由模型自证。

以下规则以前都只写在 `autodev-specs/SKILL.md` 里：

1. `Capabilities` 列进去的每一项都要有 `specs/<capability>/spec.md`，反过来
   每个 spec 文件也要能在 `Capabilities` 里找到出处。技能原来的落地方式是
   要求模型「推进 specs_done 前在回复中输出对照表 ✓」——自证状态词不构成门。
2. 每个 Requirement 至少一个 Scenario。旧校验只查「该文件至少有一个 REQ、
   至少有一个 SCN」，三个 Requirement 共用一个 Scenario 照样放行。
3. proposal 必须有 `Open Questions` 节。
4. proposal 的 New/Modified/Removed 分组要和 spec 实际用的操作段对上。
5. 标题必须能被索引器识别；Requirement/Scenario ID 外的方括号可有可无。

全都是关于文件的机械事实，因此本文件钉的是：正例放行、反例拦住、
以及缺 proposal 时不重复报错（那是 proposal_contract 的责任）。
"""

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
    placeholder_residue,
    proposal_capabilities,
    proposal_capability_groups,
    proposal_new_capability_existing,
    removed_requirements_missing_fields,
    scenarios_without_requirement,
    spec_operations_with_requirements,
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
    existing: dict[str, str] | None = None,
) -> str:
    """New 组默认带 `**Existing:** none`——那是通过态，反例各自覆写。"""
    claims = existing or {}

    def group(title: str, names: list[str]) -> str:
        lines = []
        for name in names:
            lines.append(f"- `{name}`: 说明")
            if title.startswith("New"):
                claim = claims.get(name, "none")
                if claim is not None:
                    lines.append(f"  - **Existing:** {claim}")
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

SPEC_REMOVED_REQ = """## ADDED Requirements

## MODIFIED Requirements

## REMOVED Requirements

### Requirement [REQ-001]: 旧同步入口

The system SHALL 不再提供旧同步入口。

#### Scenario [SCN-001]: 调用被拒

- **WHEN** 客户端调用旧入口
- **THEN** 系统 SHALL 返回 410
"""

# 三段标题齐全、只有 ADDED 有内容——用户裁定的 New 能力标准形态
SPEC_ALL_SECTIONS_ADDED_ONLY = (
    SPEC_ONE_REQ + "\n## MODIFIED Requirements\n\n## REMOVED Requirements\n"
)


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


class CapabilityOperationConsistencyTest(SpecContractValidatorTestBase):
    """proposal 的 New/Modified/Removed 分组必须和 spec 实际用的操作段对上。

    判据是「该段下有没有 Requirement」而不是「该段标题在不在」：每个 spec 都带
    齐三段以保持文件形状统一，用不到的留空。规则有意不对称——声明的分组总是
    要求对应操作段有内容，但只有 New 额外禁止其他段有内容（全新能力没有存量
    需求可改可删）；Modified 的 spec 顺手加一条 ADDED 是常规写法，禁掉会误伤。
    """

    def test_new_capability_with_empty_other_sections_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"]), encoding="utf-8"
            )
            self._write_spec(feature_dir, "order-export", SPEC_ALL_SECTIONS_ADDED_ONLY)
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0, output)

    def test_removed_capability_uses_removed_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text([], removed=["legacy-sync"]), encoding="utf-8"
            )
            self._write_spec(feature_dir, "legacy-sync", SPEC_REMOVED_REQ)
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0, output)

    def test_modified_capability_may_also_add_requirements(self) -> None:
        """Modified 的 spec 里出现 ADDED 是常规写法，不该报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text([], modified=["approval-reminder"]), encoding="utf-8"
            )
            body = SPEC_MODIFIED_REQ.replace(
                "## ADDED Requirements\n",
                "## ADDED Requirements\n\n### Requirement [REQ-002]: 新增静默时段\n\n"
                "The system SHALL 支持静默时段。\n\n"
                "#### Scenario [SCN-002]: 静默时段不提醒\n\n"
                "- **WHEN** 处于静默时段\n- **THEN** 系统 SHALL 不发提醒\n",
            )
            self._write_spec(feature_dir, "approval-reminder", body)
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0, output)

    def test_declared_group_without_matching_section_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text([], modified=["approval-reminder"]), encoding="utf-8"
            )
            # 声明 Modified，却只写了 ADDED 段
            self._write_spec(feature_dir, "approval-reminder", SPEC_ALL_SECTIONS_ADDED_ONLY)
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("capability_operation_missing", output)
            self.assertIn("expected=MODIFIED", output)
            self.assertIn("POST_SKILL_REPAIR", output)

    def test_new_capability_with_filled_modified_section_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"]), encoding="utf-8"
            )
            body = SPEC_ONE_REQ + (
                "\n## MODIFIED Requirements\n\n"
                "### Requirement [REQ-002]: 改了存量\n\n"
                "The system SHALL 改动已有行为。\n\n"
                "#### Scenario [SCN-002]: s\n\n- **WHEN** a\n- **THEN** b\n"
            )
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("capability_operation_contradicts_new", output)
            self.assertIn("MODIFIED", output)

    def test_empty_section_headings_do_not_count_as_content(self) -> None:
        """标题存在但段内无 Requirement 时，不算该能力用了这个操作。"""
        self.assertEqual(
            spec_operations_with_requirements(SPEC_ALL_SECTIONS_ADDED_ONLY), {"ADDED"}
        )
        self.assertEqual(spec_operations_with_requirements(SPEC_MODIFIED_REQ), {"MODIFIED"})
        self.assertEqual(spec_operations_with_requirements(SPEC_REMOVED_REQ), {"REMOVED"})

    def test_capability_group_parsing(self) -> None:
        text = proposal_text(["order-export"], modified=["billing"], removed=["legacy-sync"])
        self.assertEqual(
            proposal_capability_groups(text),
            {"order-export": "New", "billing": "Modified", "legacy-sync": "Removed"},
        )

    def test_capability_names_match_group_keys(self) -> None:
        """两个解析函数不得漂移——分组表的键集合就是名字集合。"""
        text = proposal_text(["order-export"], modified=["billing"], removed=["legacy-sync"])
        self.assertEqual(set(proposal_capability_groups(text)), proposal_capabilities(text))

    def test_spec_template_keeps_all_three_operation_sections(self) -> None:
        """模板保持三段式，用不到的段由作者留空——校验器数的是段下的 Requirement。

        规则文本本身钉在 SKILL.md（见下一条），不在这里重复断言：模板里的
        措辞可以被精简，段结构不能变。
        """
        template = (ROOT / "skills/autodev/autodev-specs/templates/spec.md").read_text(
            encoding="utf-8"
        )
        for operation in ("ADDED", "MODIFIED", "REMOVED"):
            with self.subTest(operation=operation):
                self.assertIn(f"## {operation} Requirements", template)

    def test_specs_skill_teaches_the_group_to_operation_rule(self) -> None:
        """规则与校验器不得分叉：SKILL 教的写法必须正是校验器放行的写法。

        SKILL 只写产物形态，不点校验器名字——validator 是脚本的实现细节，
        写进技能只会让模型去猜脚本内部逻辑。
        """
        skill = (ROOT / "skills/autodev/autodev-specs/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("capability_spec_correspondence", skill)
        self.assertIn("ADDED Requirements", skill)
        self.assertIn("不得有 Requirement", skill)
        self.assertIn("段下不写 Requirement", skill)


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


class RemovedRequirementFieldsTest(SpecContractValidatorTestBase):
    """移除必须说明原因与迁移方式，否则下游只能猜旧入口该怎么办。"""

    HEAD = "## REMOVED Requirements\n\n### Requirement [REQ-003]: 旧导出入口\n\n"

    def test_both_fields_present_pass(self) -> None:
        text = self.HEAD + "**Reason:** 已被 /v2/export 取代\n**Migration:** 调用方改用 /v2/export\n"
        self.assertEqual(removed_requirements_missing_fields(text), [])

    def test_missing_migration_is_reported(self) -> None:
        text = self.HEAD + "**Reason:** 已被 /v2/export 取代\n"
        self.assertEqual(removed_requirements_missing_fields(text), ["REQ-003:Migration"])

    def test_placeholder_value_counts_as_missing(self) -> None:
        text = self.HEAD + "**Reason:** [移除原因]\n**Migration:** [迁移方式]\n"
        self.assertEqual(
            removed_requirements_missing_fields(text),
            ["REQ-003:Reason", "REQ-003:Migration"],
        )

    def test_added_only_spec_has_nothing_to_report(self) -> None:
        self.assertEqual(removed_requirements_missing_fields(SPEC_ONE_REQ), [])

    def test_missing_field_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            body = SPEC_ONE_REQ + (
                "\n## REMOVED Requirements\n\n### Requirement [REQ-003]: 旧入口\n\n"
                "**Reason:** 已被取代\n\n"
                "#### Scenario [SCN-003]: 旧入口被调用\n\n- **WHEN** a\n- **THEN** 410\n"
            )
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_specs_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("removed_requirement_missing_field", output)
            self.assertIn("REQ-003:Migration", output)


class PlaceholderResidueTest(SpecContractValidatorTestBase):
    """模板槽位留在产物里就是没写完；具体 ID 和 Markdown 链接不是槽位。"""

    def test_id_syntax_is_not_a_placeholder(self) -> None:
        self.assertEqual(placeholder_residue(SPEC_ONE_REQ), [])

    def test_markdown_link_is_not_a_placeholder(self) -> None:
        self.assertEqual(placeholder_residue("见 [设计文档](design.md) 第三节\n"), [])

    def test_checkbox_is_not_a_placeholder(self) -> None:
        self.assertEqual(placeholder_residue("- [ ] 待办\n- [x] 已做\n"), [])

    def test_template_slot_is_reported(self) -> None:
        self.assertEqual(
            placeholder_residue("### Requirement [REQ-001]: [能力名]\n"), ["[能力名]"]
        )

    def test_id_template_slots_are_reported(self) -> None:
        self.assertEqual(
            placeholder_residue(
                "### Requirement [REQ-NNN]: 标题\n映射：REQ-NNN / SCN-NNN\n"
            ),
            ["REQ-NNN", "REQ-NNN", "SCN-NNN"],
        )

    def test_tbd_words_are_reported(self) -> None:
        self.assertEqual(placeholder_residue("错误码 TBD\n"), ["TBD"])
        self.assertEqual(placeholder_residue("字段语义待补充\n"), ["待补充"])

    def test_residue_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            body = SPEC_ONE_REQ.replace("创建导出任务", "[能力名]")
            self._write_spec(feature_dir, "order-export", body)
            failures, output = self._run(validate_specs_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("spec_placeholder_residue", output)


class ProposalOpenQuestionsTest(SpecContractValidatorTestBase):
    def test_proposal_with_all_sections_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"]), encoding="utf-8"
            )
            failures, output = self._run(validate_proposal_contract, project)
            self.assertEqual(failures, 0, output)

    def test_proposal_without_open_questions_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            text = proposal_text(["order-export"]).replace("## Open Questions\n\n无\n", "")
            (feature_dir / "proposal.md").write_text(text, encoding="utf-8")
            failures, output = self._run(validate_proposal_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("invalid_proposal_missing_section", output)
            self.assertIn("Open Questions", output)

    def test_section_name_in_prose_does_not_satisfy_the_gate(self) -> None:
        """删掉整节、正文里提一句名字，不构成免检出口。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            text = proposal_text(["order-export"]).replace(
                "## Open Questions\n\n无\n",
                "本轮没有 Open Questions 需要处理。\n",
            )
            (feature_dir / "proposal.md").write_text(text, encoding="utf-8")
            failures, output = self._run(validate_proposal_contract, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("Open Questions", output)


class NewCapabilityExistingEvidenceTest(SpecContractValidatorTestBase):
    """New 组每项必须写下一句可证伪的存量断言。

    `ADDED` 是零成本默认值：没搜索过也能一路写成新增，且 spec 是照着这个声明写
    的，所以 `capability_spec_correspondence` 的自洽性检查完美通过——两边一起
    错，就没有一个检查会响。这里钉的不是「断言为真」（脚本证明不了），而是
    「断言必须被写下来」，从而给回检一个能逐条核对的靶子。
    """

    def test_none_claim_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"], existing={"order-export": "none"}),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertEqual(failures, 0, output)

    def test_missing_existing_line_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"], existing={"order-export": None}),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("new_capability_existing_evidence_missing", output)

    def test_placeholder_does_not_count_as_a_claim(self) -> None:
        """模板槽位原样留着，等同于没写。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(
                    ["order-export"],
                    existing={"order-export": "[none 或 现有入口的 `相对路径#符号`]"},
                ),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("new_capability_existing_evidence_missing", output)

    def test_declaring_a_path_while_staying_in_new_is_blocked(self) -> None:
        """写了存量路径就不是新增；这正是 trace 里 12 个 capability 全标 ADDED 的反面。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(
                    ["order-export"],
                    existing={"order-export": "src/adapter/ExportController.java#query"},
                ),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("new_capability_declares_existing_code", output)
            self.assertIn("ExportController", output)

    def test_near_miss_wording_is_not_in_the_negative_closed_set(self) -> None:
        """闭集只认 none / 无；放宽了这个字段就退回成自由散文。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(
                proposal_text(["order-export"], existing={"order-export": "暂未发现相关代码"}),
                encoding="utf-8",
            )
            self._write_spec(feature_dir, "order-export")
            failures, output = self._run(validate_capability_spec_correspondence, project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("new_capability_declares_existing_code", output)

    def test_modified_group_is_not_asked_for_the_claim(self) -> None:
        """断言只为拦住「默认新增」；Modified 组本来就承认存量，不必重复声明。"""
        self.assertEqual(
            proposal_new_capability_existing(
                proposal_text(["order-export"], modified=["approval-reminder"])
            ),
            {"order-export": "none"},
        )

    def test_empty_new_group_written_as_wu_claims_nothing(self) -> None:
        self.assertEqual(proposal_new_capability_existing(proposal_text([])), {})


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
