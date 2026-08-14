"""产物契约预检的每条失败都必须回答四个问题：哪个产物、定位、问题、怎么改。

这里钉三件事：

1. **覆盖**：specs/plan 在用 validator 可能发出的每个 reason，都在
   `repair_registry.REPAIRS` 里有记录。新加一个 `fail_line` 而忘了写修复动作，
   本文件会红——这是「没有 action 的 validator 不能过」的落地方式。
2. **配对**：结构化字段随 `POST_SKILL_FAIL` 行尾的 payload 一起走，一条错误一行。
   同一个 reason 出现多次时，每条各自带自己的 target 和 action。以前 repair 是
   单独一行且只带 skill+reason，重复 reason 根本无法配对，且 stage_gate 整条丢弃。
3. **降级**：没有 payload 的旧格式行仍然照原样解析。
"""

from __future__ import annotations

import ast
import contextlib
import io
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

import repair_registry  # noqa: E402
from artifact_check import (  # noqa: E402
    HookContext,
    _validate_plan_json_traceability,
    validate_design_contract,
    validate_proposal_contract,
    validate_specs_contract,
)
from common import fail_line  # noqa: E402
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.json_writer_common import parse_postcheck_output  # noqa: E402
from hooks.plan_granularity import validate_plan_task_granularity_item  # noqa: E402


ARTIFACT_CHECK = HOOKS / "artifact_check.py"

# board_config 中 dev.specs / dev.plan 两个节点声明的 validator
IN_SCOPE_VALIDATORS = (
    "validate_proposal_contract",
    "validate_specs_contract",
    "validate_capability_spec_correspondence",
    "validate_design_contract",
    "validate_plan_json_contract",
    "validate_plan_json_initial_tasks",
    "validate_plan_task_granularity",
    "validate_plan_scenario_coverage",
    "validate_plan_ref_resolution",
)

# reason 由被调用方以数据形式返回、AST 在调用点看不到字面量的那些。
# 每一项都配一条「与源文件同步」的断言，见 DynamicReasonsInSyncTest。
DYNAMIC_REASONS = {
    # _spec_definition_index / _design_definition_index
    "duplicate_requirement_id",
    "duplicate_scenario_id",
    "duplicate_design_api_id",
    "duplicate_design_data_id",
    "duplicate_design_decision_id",
    # hooks/plan_granularity.py
    "invalid_plan_task_scenario_reference",
    "oversized_plan_task_must_split",
    "missing_plan_task_merged_scenario_refs",
    "invalid_plan_task_merged_scenario_refs",
    "missing_plan_task_split_rationale",
    "invalid_plan_task_split_rationale",
    "invalid_plan_task_matrix_validation",
    # hooks/code_task_context.py resolve_task_refs
    "invalid_artifact_ref",
    "invalid_artifact_ref_format",
    "invalid_artifact_ref_type",
    "ambiguous_ref_anchor",
    "missing_ref_file",
    "missing_ref_anchor",
    "invalid_design_contract",
    "design_api_marker_conflicts_with_definitions",
    "design_data_marker_conflicts_with_definitions",
    "plan_api_ref_forbidden_by_design_marker",
    "plan_data_ref_forbidden_by_design_marker",
}

# run_postcheck 在 validator 跑起来之前就可能失败，这些也必须带修复动作。
ENTRY_REASONS = {
    "missing_required_artifacts",
    "missing_feature_dir",
    "invalid_board_config",
    "unknown_validator",
}


def reachable_fail_line_reasons() -> set[str]:
    """从在用 validator 出发做函数可达性分析，收集 fail_line 的 reason 字面量。"""
    tree = ast.parse(ARTIFACT_CHECK.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    reasons: set[str] = set()
    seen: set[str] = set()

    def walk(name: str) -> None:
        if name in seen or name not in functions:
            return
        seen.add(name)
        for node in ast.walk(functions[name]):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "fail_line":
                arg = node.args[1] if len(node.args) > 1 else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    reasons.add(arg.value)
            else:
                walk(node.func.id)

    for validator in IN_SCOPE_VALIDATORS:
        walk(validator)
    return reasons


class RegistryCoverageTest(unittest.TestCase):
    def test_in_scope_validators_are_all_reachable(self) -> None:
        tree = ast.parse(ARTIFACT_CHECK.read_text(encoding="utf-8"))
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        for validator in IN_SCOPE_VALIDATORS:
            self.assertIn(validator, defined, f"validator 改名或删除了: {validator}")

    def test_every_reachable_reason_has_repair(self) -> None:
        missing = sorted(reachable_fail_line_reasons() - set(repair_registry.REPAIRS))
        self.assertEqual(
            missing,
            [],
            "以下 reason 会在 specs/plan 预检中出现但没有修复动作，"
            "请在 skills/autodev/hooks/repair_registry.py 补齐：" + ", ".join(missing),
        )

    def test_dynamic_and_entry_reasons_have_repair(self) -> None:
        missing = sorted((DYNAMIC_REASONS | ENTRY_REASONS) - set(repair_registry.REPAIRS))
        self.assertEqual(missing, [])

    def test_every_repair_uses_a_known_route(self) -> None:
        for reason, repair in repair_registry.REPAIRS.items():
            self.assertIn(repair.route, repair_registry.ROUTES, reason)

    def test_every_repair_has_nonempty_fields(self) -> None:
        for reason, repair in repair_registry.REPAIRS.items():
            self.assertTrue(repair.artifact.strip(), reason)
            self.assertTrue(repair.problem.strip(), reason)
            self.assertTrue(repair.action.strip(), reason)

    def test_plan_artifact_repairs_forbid_hand_editing(self) -> None:
        """禁令只在 plan 机器产物相关错误里出现，不靠技能正文重复枚举。"""
        for reason, repair in repair_registry.REPAIRS.items():
            if repair.artifact == "plan.json":
                self.assertIn(repair_registry.PLAN_NO_HAND_EDIT, repair.action, reason)


class DynamicReasonsInSyncTest(unittest.TestCase):
    """动态 reason 集合是手写的，必须跟着源文件走，不能悄悄漂移。"""

    def _string_literals(self, path: Path, key: str) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()

        def string_value(node):
            value = getattr(node, "value", None)
            if isinstance(value, str):
                return value
            legacy_value = getattr(node, "s", None)
            if isinstance(legacy_value, str):
                return legacy_value
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if string_value(k) == key:
                        value = string_value(v)
                        if value is not None:
                            found.add(value)
        return found

    def test_plan_granularity_reasons_are_registered(self) -> None:
        reasons = self._string_literals(ROOT / "hooks" / "plan_granularity.py", "reason")
        self.assertTrue(reasons)
        self.assertEqual(sorted(reasons - set(repair_registry.REPAIRS)), [])

    def test_task_ref_reasons_are_registered(self) -> None:
        reasons = self._string_literals(ROOT / "hooks" / "code_task_context.py", "reason")
        self.assertIn("missing_ref_anchor", reasons)
        for reason in ("invalid_artifact_ref", "missing_ref_file", "missing_ref_anchor"):
            self.assertIn(reason, repair_registry.REPAIRS)

    def test_duplicate_id_reasons_are_registered(self) -> None:
        source = ARTIFACT_CHECK.read_text(encoding="utf-8")
        for reason in (
            "duplicate_requirement_id",
            "duplicate_scenario_id",
            "duplicate_design_api_id",
            "duplicate_design_data_id",
            "duplicate_design_decision_id",
        ):
            self.assertIn(f'failures.append("{reason}")', source)
            self.assertIn(reason, repair_registry.REPAIRS)


def run_capture(func) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        failures = func()
    return failures, buffer.getvalue()


class StructuredOutputTest(unittest.TestCase):
    def _ctx(self) -> HookContext:
        return HookContext(skill="autodev-specs", slug="alpha", root=Path("/tmp"))

    def test_failure_line_carries_all_five_fields(self) -> None:
        _, output = run_capture(
            lambda: fail_line(self._ctx(), "invalid_proposal_missing_section", " section='Why'", target="Why")
        )
        errors = parse_postcheck_output(output)
        self.assertEqual(len(errors), 1)
        error = errors[0]
        for field in ("artifact", "target", "problem", "action", "route"):
            self.assertTrue(error.get(field), field)
        self.assertEqual(error["artifact"], "proposal.md")
        self.assertEqual(error["target"], "Why")
        self.assertEqual(error["route"], "fix_current")
        self.assertIn("## Why", error["action"])

    def test_same_reason_twice_keeps_its_own_target_and_action(self) -> None:
        def emit() -> int:
            ctx = self._ctx()
            return fail_line(ctx, "invalid_proposal_missing_section", " section='Why'", target="Why") + fail_line(
                ctx, "invalid_proposal_missing_section", " section='Impact'", target="Impact"
            )

        _, output = run_capture(emit)
        errors = parse_postcheck_output(output)
        self.assertEqual(len(errors), 2)
        self.assertEqual([error["target"] for error in errors], ["Why", "Impact"])
        self.assertIn("## Why", errors[0]["action"])
        self.assertIn("## Impact", errors[1]["action"])

    def test_legacy_line_without_payload_still_parses(self) -> None:
        legacy = "POST_SKILL_FAIL skill=autodev-code reason=some_legacy_reason file=a.md\n"
        errors = parse_postcheck_output(legacy)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["reason"], "some_legacy_reason")
        self.assertEqual(errors[0]["detail"], "file=a.md")
        self.assertNotIn("route", errors[0])

    def test_unregistered_reason_degrades_to_old_shape(self) -> None:
        _, output = run_capture(lambda: fail_line(self._ctx(), "brand_new_unregistered_reason", " x=1"))
        errors = parse_postcheck_output(output)
        self.assertEqual(errors[0]["reason"], "brand_new_unregistered_reason")
        self.assertNotIn("action", errors[0])

    def test_registered_failure_preserves_structured_diagnostics(self) -> None:
        diagnostics = {
            "taskId": "T013",
            "field": "splitRationale",
            "violations": [{"code": "split_rationale_missing_related_ids"}],
        }
        _, output = run_capture(
            lambda: fail_line(
                HookContext(skill="autodev-plan", slug="alpha", root=Path("/tmp")),
                "invalid_plan_task_split_rationale",
                " task=T013 detail=scenarios=8",
                target="T013",
                fields={"detail": "task=T013 detail=scenarios=8"},
                diagnostics=diagnostics,
            )
        )

        error = parse_postcheck_output(output)[0]
        self.assertEqual(error["diagnostics"], diagnostics)

    def test_pending_design_cells_route_to_the_user(self) -> None:
        ctx = HookContext(skill="autodev-plan", slug="alpha", root=Path("/tmp"))
        _, output = run_capture(lambda: fail_line(ctx, "design_has_pending_cells", " count=2", target="2 处"))
        error = parse_postcheck_output(output)[0]
        self.assertEqual(error["route"], "ask_user")
        self.assertIn("禁止自行填值", error["action"])

    def test_plan_json_code_gets_a_specific_action(self) -> None:
        ctx = HookContext(skill="autodev-plan", slug="alpha", root=Path("/tmp"))
        _, output = run_capture(
            lambda: fail_line(
                ctx,
                "invalid_plan_json",
                " detail=plan_json_status_not_initial",
                target="plan_json_status_not_initial",
            )
        )
        error = parse_postcheck_output(output)[0]
        self.assertIn("todo", error["action"])
        self.assertIn("plan.json", error["action"])

    def test_unknown_plan_json_code_falls_back_with_full_fields(self) -> None:
        ctx = HookContext(skill="autodev-plan", slug="alpha", root=Path("/tmp"))
        _, output = run_capture(
            lambda: fail_line(ctx, "invalid_plan_json", " detail=some_new_code", target="some_new_code")
        )
        error = parse_postcheck_output(output)[0]
        for field in ("artifact", "target", "problem", "action", "route"):
            self.assertTrue(error.get(field), field)


PROPOSAL_MISSING_DECISION_LOG = """# Proposal: 导出

## Why

需要导出。

## What Changes

- 新增导出入口

## Capabilities

### New Capabilities

- `order-export`: 说明

### Modified Capabilities

- 无

### Removed Capabilities

- 无

## Impact

- 影响模块: export

## Out of Scope

- 不做批量删除

## Open Questions

无
"""

SPEC_MALFORMED_HEADING = """## ADDED Requirements

### Requirement REQ-001: 缺方括号

The system SHALL 支持导出。

#### Scenario [SCN-001]: 创建成功

- **WHEN** 用户提交
- **THEN** 系统 SHALL 返回任务号
"""

SPEC_REQ_WITHOUT_SCENARIO = """## ADDED Requirements

### Requirement [REQ-001]: 创建导出任务

The system SHALL 支持创建导出任务。

#### Scenario [SCN-001]: 创建成功

- **WHEN** 用户提交
- **THEN** 系统 SHALL 返回任务号

### Requirement [REQ-002]: 查询导出任务

The system SHALL 支持查询。
"""

DESIGN_WITH_PENDING = """# Design

## Context / 输入上下文

现状说明。

## Code Evidence

src/a.py:1

## Spec Traceability

| Requirement | Decision |
|---|---|
| REQ-001 | 无 |

## API Decisions

x-auto-no-http-api: true

## Data Decisions

x-auto-no-sql: true

## Technical Design

方案说明。

## Risks / Open Questions

| 项 | 状态 |
|---|---|
| 鉴权口径 | 待确认 |
"""


class RepresentativeSpecsFailuresTest(unittest.TestCase):
    """代表性 specs 失败：缺章节、畸形 ID、REQ 缺 Scenario、capability 不对应。"""

    def _feature(self, tmp: str) -> tuple[Path, Path]:
        project = Path(tmp).resolve() / "demo"
        project.mkdir()
        init_workspace(project)
        create_feature(project, "alpha")
        return project, project / ".autobizdevops" / "features" / "alpha"

    def _errors(self, validator, project: Path, skill: str = "autodev-specs") -> list[dict]:
        ctx = HookContext(skill=skill, slug="alpha", root=project)
        _, output = run_capture(lambda: validator(ctx))
        return parse_postcheck_output(output)

    def _assert_actionable(self, errors: list[dict], reason: str) -> dict:
        matches = [error for error in errors if error["reason"] == reason]
        self.assertTrue(matches, f"没有报出 {reason}：{errors}")
        error = matches[0]
        for field in ("artifact", "target", "problem", "action", "route"):
            self.assertTrue(error.get(field), f"{reason} 缺 {field}")
        return error

    def test_missing_proposal_section_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "proposal.md").write_text(PROPOSAL_MISSING_DECISION_LOG, encoding="utf-8")
            errors = self._errors(validate_proposal_contract, project)
            error = self._assert_actionable(errors, "invalid_proposal_missing_section")
            self.assertEqual(error["target"], "Decision Log")

    def test_malformed_contract_heading_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            spec = feature_dir / "specs" / "order-export" / "spec.md"
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text(SPEC_MALFORMED_HEADING, encoding="utf-8")
            errors = self._errors(validate_specs_contract, project)
            error = self._assert_actionable(errors, "spec_contract_heading_malformed")
            self.assertIn("order-export", error["target"])
            self.assertIn("[REQ-NNN]", error["action"])

    def test_requirement_without_scenario_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            spec = feature_dir / "specs" / "order-export" / "spec.md"
            spec.parent.mkdir(parents=True, exist_ok=True)
            spec.write_text(SPEC_REQ_WITHOUT_SCENARIO, encoding="utf-8")
            errors = self._errors(validate_specs_contract, project)
            error = self._assert_actionable(errors, "spec_requirement_without_scenario")
            self.assertIn("Scenario", error["action"])

    def test_design_pending_cells_ask_the_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            (feature_dir / "design.md").write_text(DESIGN_WITH_PENDING, encoding="utf-8")
            errors = self._errors(validate_design_contract, project, skill="autodev-plan")
            error = self._assert_actionable(errors, "design_has_pending_cells")
            self.assertEqual(error["route"], "ask_user")


class RepresentativePlanFailuresTest(unittest.TestCase):
    """代表性 plan 失败：未知 SCN、未知设计 ID、任务过大。"""

    def _feature(self, tmp: str) -> tuple[Path, Path]:
        project = Path(tmp).resolve() / "demo"
        project.mkdir()
        init_workspace(project)
        create_feature(project, "alpha")
        feature_dir = project / ".autobizdevops" / "features" / "alpha"
        spec = feature_dir / "specs" / "order-export" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(
            "## ADDED Requirements\n\n"
            "### Requirement [REQ-001]: 导出\n\nThe system SHALL 导出。\n\n"
            "#### Scenario [SCN-001]: 成功\n\n- **WHEN** a\n- **THEN** b\n",
            encoding="utf-8",
        )
        (feature_dir / "design.md").write_text(
            "# Design\n\n## API Decisions\n\nx-auto-no-http-api: true\n\n"
            "## Data Decisions\n\nx-auto-no-sql: true\n\n"
            "## Technical Design\n\n### D-001: 决策\n\n说明。\n",
            encoding="utf-8",
        )
        return project, feature_dir

    def test_unknown_scenario_and_design_refs_are_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp)
            ctx = HookContext(skill="autodev-plan", slug="alpha", root=project)
            data = {
                "tasks": [
                    {
                        "id": "T001",
                        "specRefs": [
                            "specs/order-export/spec.md#REQ-001",
                            "specs/order-export/spec.md#SCN-404",
                        ],
                        "designRefs": ["design.md#D-404"],
                        "decisionIds": ["D-404"],
                    }
                ]
            }
            _, output = run_capture(lambda: _validate_plan_json_traceability(ctx, data))
            errors = parse_postcheck_output(output)
            by_reason = {error["reason"]: error for error in errors}

            unknown_scn = by_reason.get("unknown_plan_json_scenario_ref")
            self.assertIsNotNone(unknown_scn, errors)
            self.assertIn("SCN-404", unknown_scn["target"])
            self.assertIn("T001", unknown_scn["target"])
            # 上游确实缺定义时的出口必须写在动作里
            self.assertIn("dev.specs", unknown_scn["action"])

            unknown_decision = by_reason.get("unknown_plan_json_decision_ref")
            self.assertIsNotNone(unknown_decision, errors)
            self.assertEqual(unknown_decision["artifact"], "design.md")
            self.assertEqual(unknown_decision["route"], "fix_current")

    def test_oversized_task_is_actionable(self) -> None:
        task = {
            "id": "T001",
            "specRefs": [f"specs/order-export/spec.md#SCN-{index:03d}" for index in range(1, 20)],
        }
        item_errors = validate_plan_task_granularity_item(task, task_id="T001")
        reasons = {error["reason"] for error in item_errors}
        self.assertTrue(reasons, "粒度校验没有报错，测试样本失效")
        ctx = HookContext(skill="autodev-plan", slug="alpha", root=Path("/tmp"))

        def emit() -> int:
            failures = 0
            for error in item_errors:
                failures += fail_line(
                    ctx,
                    error["reason"],
                    " " + error.get("detail", ""),
                    target="T001",
                    fields={"detail": error.get("detail", "")},
                    diagnostics=error,
                )
            return failures

        _, output = run_capture(emit)
        for error in parse_postcheck_output(output):
            for field in ("artifact", "target", "problem", "action", "route"):
                self.assertTrue(error.get(field), f"{error['reason']} 缺 {field}")
            self.assertEqual(error["target"], "T001")
            self.assertTrue(error.get("diagnostics", {}).get("violations"))


class SkillWordingTest(unittest.TestCase):
    SPECS_SKILL = ROOT / "skills" / "autodev" / "autodev-specs" / "SKILL.md"
    PLAN_SKILL = ROOT / "skills" / "autodev" / "autodev-plan" / "SKILL.md"
    REVIEW_PROTOCOL = ROOT / "skills" / "references" / "review-protocol.md"

    def test_machine_precheck_is_named_as_such(self) -> None:
        for path in (self.SPECS_SKILL, self.PLAN_SKILL, self.REVIEW_PROTOCOL):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("集中校验", text, f"{path.name} 仍在用含糊的「集中校验」")
            self.assertIn("产物契约预检（机器校验）", text, path.name)

    def test_both_stages_use_stage_gate(self) -> None:
        for path, stage in ((self.SPECS_SKILL, "dev.specs"), (self.PLAN_SKILL, "dev.plan")):
            text = path.read_text(encoding="utf-8")
            self.assertTrue("stage_gate.py" in text, f"{path.name} 未使用统一入口 stage_gate.py")
            self.assertTrue(f"validate --stage {stage}" in text, f"{path.name} 缺 --stage {stage}")
            self.assertFalse("artifact_check.py" in text, f"{path.name} 仍在直接调 artifact_check.py")

    def test_skills_do_not_enumerate_error_codes(self) -> None:
        """错误码和脚本内部规则留在脚本里，技能只描述处理流程。"""
        for path in (self.SPECS_SKILL, self.PLAN_SKILL):
            text = path.read_text(encoding="utf-8")
            for reason in ("missing_plan_json", "invalid_proposal_missing_section", "spec_requirement_without_scenario"):
                self.assertNotIn(reason, text, f"{path.name} 不应枚举错误码 {reason}")

    def test_route_values_are_mapped_to_review_categories(self) -> None:
        text = self.REVIEW_PROTOCOL.read_text(encoding="utf-8")
        for route in repair_registry.ROUTES:
            self.assertIn(route, text, f"review-protocol 缺 route 映射：{route}")


if __name__ == "__main__":
    unittest.main()
