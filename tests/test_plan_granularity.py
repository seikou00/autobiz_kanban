from __future__ import annotations

import unittest

from hooks.plan_granularity import (
    validate_plan_task_granularity_item,
    validate_plan_task_grouping_item,
)


def _task_with_scenarios(count: int) -> dict:
    scenario_refs = [f"specs/capability/spec.md#SCN-{index:03d}" for index in range(1, count + 1)]
    acceptance_ids = [f"AC-T001-{index:02d}" for index in range(1, count + 1)]
    return {
        "id": "T001",
        "specRefs": ["specs/capability/spec.md#REQ-001", *scenario_refs],
        "apiIds": ["API-001"],
        "acceptanceCriteria": [
            {"id": acceptance_id, "scenarioRefs": [scenario_ref]}
            for acceptance_id, scenario_ref in zip(acceptance_ids, scenario_refs)
        ],
        "validationCommands": [
            {
                "id": "VAL-T001-01",
                "kind": "integration_test",
                "required": True,
                "covers": acceptance_ids,
            }
        ],
        "splitRationale": (
            "SCN-001、SCN-004、SCN-007 由同一查询请求返回完整字段矩阵，"
            "并由同一个响应断言验证，拆开会复制同一验证闭环。"
        ),
    }


def _deferred_scenario_task(count: int) -> dict:
    """A task whose grouping fields are valid but that carries no local validation."""
    task = _task_with_scenarios(count)
    scenario_ids = [f"SCN-{index:03d}" for index in range(1, count + 1)]
    task["validationCommands"] = []
    task["mergedScenarioRefs"] = task["specRefs"][1:]
    task["splitRationale"] = (
        f"{scenario_ids[0]}、{scenario_ids[2]}、{scenario_ids[-1]} 由同一外部系统回调返回，"
        "共享同一验证闭环，本地无法独立验证。"
    )
    return task


def _external_dependency_task(count: int) -> dict:
    task = _deferred_scenario_task(count)
    task["executionMode"] = "external_dependency"
    task["externalDependency"] = {
        "system": "activity-approval",
        "owner": "platform-team",
        "trackingRefs": ["design.md#D-001"],
    }
    return task


def _reasons(task: dict) -> list[str]:
    return [item["reason"] for item in validate_plan_task_granularity_item(task, task_id="T001")]


class PlanGranularityTests(unittest.TestCase):
    def test_grouping_preflight_rejects_hard_cap_without_full_task_content(self) -> None:
        task = {
            "id": "T001",
            "specRefs": [
                "specs/capability/spec.md#REQ-001",
                *[f"specs/capability/spec.md#SCN-{index:03d}" for index in range(1, 14)],
            ],
            "apiIds": [],
            "uiRequired": False,
        }

        reasons = [
            item["reason"]
            for item in validate_plan_task_grouping_item(task, task_id="T001")
        ]

        self.assertEqual(reasons, ["oversized_plan_task_must_split"])

    def test_matrix_exception_requires_complete_merged_scenario_refs(self) -> None:
        task = _task_with_scenarios(9)

        self.assertIn("missing_plan_task_merged_scenario_refs", _reasons(task))

        task["mergedScenarioRefs"] = task["specRefs"][1:]

        self.assertEqual(_reasons(task), [])

    def test_grouping_preflight_reports_all_missing_matrix_exception_fields(self) -> None:
        task = _task_with_scenarios(9)
        task["mergedScenarioRefs"] = []
        task.pop("splitRationale")

        errors = validate_plan_task_grouping_item(task, task_id="T001")

        self.assertEqual(
            [item["reason"] for item in errors],
            [
                "missing_plan_task_merged_scenario_refs",
                "missing_plan_task_split_rationale",
            ],
        )

    def test_grouping_preflight_explains_incomplete_merged_scenario_refs(self) -> None:
        task = _task_with_scenarios(9)
        task["mergedScenarioRefs"] = task["specRefs"][1:-1]
        task.pop("splitRationale")

        errors = validate_plan_task_grouping_item(task, task_id="T001")

        self.assertEqual(
            [item["reason"] for item in errors],
            [
                "invalid_plan_task_merged_scenario_refs",
                "missing_plan_task_split_rationale",
            ],
        )
        self.assertIn("missingRefs=specs/capability/spec.md#SCN-009", errors[0]["detail"])
        self.assertEqual(
            errors[0]["missingRefs"],
            ["specs/capability/spec.md#SCN-009"],
        )
        self.assertEqual(errors[0]["field"], "mergedScenarioRefs")

    def test_split_rationale_reports_every_specific_violation(self) -> None:
        task = _task_with_scenarios(8)
        task["apiIds"] = ["API-001", "API-002"]
        task["mergedScenarioRefs"] = task["specRefs"][1:]
        task["splitRationale"] = "同一模块"

        errors = validate_plan_task_grouping_item(task, task_id="T001")
        rationale_error = next(
            item for item in errors if item["reason"] == "invalid_plan_task_split_rationale"
        )
        violation_codes = {item["code"] for item in rationale_error["violations"]}

        self.assertEqual(rationale_error["taskId"], "T001")
        self.assertEqual(rationale_error["field"], "splitRationale")
        self.assertEqual(
            rationale_error["thresholds"],
            [{
                "dimension": "scenarios",
                "field": "specRefs",
                "observed": 8,
                "softLimit": 5,
                "hardLimit": 12,
            }],
        )
        self.assertEqual(
            violation_codes,
            {
                "split_rationale_too_short",
                "split_rationale_contains_banned_term",
                "split_rationale_missing_validation_boundary",
                "split_rationale_missing_related_ids",
            },
        )
        missing_ids = next(
            item
            for item in rationale_error["violations"]
            if item["code"] == "split_rationale_missing_related_ids"
        )
        self.assertEqual(missing_ids["requiredCount"], 3)
        self.assertEqual(missing_ids["actualCount"], 0)
        self.assertEqual(len(missing_ids["eligibleIds"]), 8)

    def test_matrix_exception_rejects_incomplete_merged_scenario_refs(self) -> None:
        task = _task_with_scenarios(9)
        task["mergedScenarioRefs"] = task["specRefs"][1:-1]

        self.assertIn("invalid_plan_task_merged_scenario_refs", _reasons(task))

    def test_matrix_exception_requires_one_complete_behavior_command(self) -> None:
        task = _task_with_scenarios(9)
        task["mergedScenarioRefs"] = task["specRefs"][1:]
        task["validationCommands"].append(
            {
                "id": "VAL-T001-02",
                "kind": "behavior_test",
                "required": True,
                "covers": ["AC-T001-01"],
            }
        )

        self.assertIn("invalid_plan_task_matrix_validation", _reasons(task))

    def test_matrix_validation_reports_exact_cover_mismatch(self) -> None:
        task = _task_with_scenarios(8)
        task["mergedScenarioRefs"] = task["specRefs"][1:]
        task["validationCommands"][0]["covers"] = task["validationCommands"][0]["covers"][:-1]

        errors = validate_plan_task_granularity_item(task, task_id="T001")
        matrix_error = next(
            item for item in errors if item["reason"] == "invalid_plan_task_matrix_validation"
        )
        covers_error = next(
            item
            for item in matrix_error["violations"]
            if item["code"] == "matrix_validation_covers_mismatch"
        )

        self.assertEqual(matrix_error["taskId"], "T001")
        self.assertEqual(matrix_error["field"], "validationCommands")
        self.assertEqual(covers_error["missingCovers"], ["AC-T001-08"])
        self.assertEqual(covers_error["extraCovers"], [])

    def test_matrix_exception_rejects_more_than_twelve_scenarios(self) -> None:
        task = _task_with_scenarios(13)
        task["mergedScenarioRefs"] = task["specRefs"][1:]

        self.assertEqual(_reasons(task), ["oversized_plan_task_must_split"])

    def test_matrix_exception_rejects_generic_rationale_without_scenario_ids(self) -> None:
        task = _task_with_scenarios(9)
        task["mergedScenarioRefs"] = task["specRefs"][1:]
        task["splitRationale"] = "同一查询请求返回完整字段矩阵，并由同一个响应断言验证，拆开会复制同一验证闭环。"

        self.assertIn("invalid_plan_task_split_rationale", _reasons(task))

    def test_rejects_scenario_range_or_concatenation_shorthand(self) -> None:
        for anchor in ("SCN-001~SCN-009", "SCN-001, SCN-002", "SCN-001SCN-006", "SCN-001到SCN-009"):
            task = _task_with_scenarios(1)
            task["specRefs"] = ["specs/capability/spec.md#REQ-001", f"specs/capability/spec.md#{anchor}"]

            self.assertIn("invalid_plan_task_scenario_reference", _reasons(task), anchor)


class ScenarioReferenceDownstreamTests(unittest.TestCase):
    """Evidence for the rule registry: what a non-expanded ref breaks downstream."""

    def test_range_reference_creates_false_scenario_coverage(self) -> None:
        import sys
        import tempfile
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from hooks.plan_writer import _scenario_coverage

        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp)
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            spec_dir.joinpath("spec.md").write_text(
                "\n".join([
                    "## ADDED Requirements",
                    "### Requirement REQ-001: capability",
                    "#### Scenario SCN-001: first",
                    "#### Scenario SCN-002: second",
                    "#### Scenario SCN-003: third",
                ]),
                encoding="utf-8",
            )

            expected, covered = _scenario_coverage(
                feature_dir,
                [{"id": "T001", "specRefs": ["specs/cap/spec.md#SCN-001~SCN-003"]}],
            )

        # One unexpanded ref silently marks two scenarios covered, so the coverage
        # gate would pass for scenarios no task actually plans.
        self.assertEqual(len(expected), 3)
        self.assertEqual(
            sorted(covered),
            ["specs/cap/spec.md#SCN-001", "specs/cap/spec.md#SCN-003"],
        )
        self.assertEqual(
            sorted(expected - covered),
            ["specs/cap/spec.md#SCN-002"],
        )


class ExternalDependencyGranularityTests(unittest.TestCase):
    def test_external_dependency_task_passes_without_local_validation(self) -> None:
        for count in (6, 12):
            with self.subTest(scenarios=count):
                self.assertEqual(_reasons(_external_dependency_task(count)), [])

    def test_code_task_without_local_validation_still_fails_matrix(self) -> None:
        self.assertIn(
            "invalid_plan_task_matrix_validation",
            _reasons(_deferred_scenario_task(6)),
        )

    def test_external_dependency_task_still_obeys_hard_limits(self) -> None:
        self.assertEqual(
            _reasons(_external_dependency_task(13)),
            ["oversized_plan_task_must_split"],
        )

    def test_external_dependency_task_still_obeys_split_rationale(self) -> None:
        task = _external_dependency_task(6)
        task.pop("splitRationale")

        self.assertIn("missing_plan_task_split_rationale", _reasons(task))


if __name__ == "__main__":
    unittest.main()
