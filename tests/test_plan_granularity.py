"""Plan v2 only enforces reference integrity, not artificial task size."""

from __future__ import annotations

import unittest

from hooks.plan_granularity import validate_plan_task_granularity_item


class PlanGranularityTests(unittest.TestCase):
    def test_large_delivery_is_a_planner_decision_not_a_format_error(self) -> None:
        task = {
            "specRefs": [
                "specs/cap/spec.md#REQ-001",
                *[f"specs/cap/spec.md#SCN-{index:03d}" for index in range(1, 30)],
            ],
            "apiIds": [f"API-{index:03d}" for index in range(1, 10)],
        }
        self.assertEqual(validate_plan_task_granularity_item(task, task_id="T001"), [])

    def test_scenario_references_must_remain_individual_and_path_qualified(self) -> None:
        issues = validate_plan_task_granularity_item(
            {"specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001~SCN-003"]},
            task_id="T001",
        )
        self.assertEqual(issues[0]["reason"], "invalid_plan_task_scenario_reference")


class ScenarioReferenceDownstreamTests(unittest.TestCase):
    def test_range_reference_creates_false_scenario_coverage(self) -> None:
        """The registry evidence: ranges cannot safely count as coverage."""
        issues = validate_plan_task_granularity_item(
            {"specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001~SCN-003"]},
            task_id="T001",
        )
        self.assertEqual(issues[0]["reason"], "invalid_plan_task_scenario_reference")


if __name__ == "__main__":
    unittest.main()
