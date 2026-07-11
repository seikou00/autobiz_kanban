from __future__ import annotations

import unittest

from hooks.plan_granularity import validate_plan_task_granularity_item


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
            "同一查询请求返回完整字段矩阵，并由同一个响应断言验证，"
            "拆开会复制同一验证闭环。"
        ),
    }


def _reasons(task: dict) -> list[str]:
    return [item["reason"] for item in validate_plan_task_granularity_item(task, task_id="T001")]


class PlanGranularityTests(unittest.TestCase):
    def test_matrix_exception_requires_complete_merged_scenario_refs(self) -> None:
        task = _task_with_scenarios(9)

        self.assertIn("missing_plan_task_merged_scenario_refs", _reasons(task))

        task["mergedScenarioRefs"] = task["specRefs"][1:]

        self.assertEqual(_reasons(task), [])

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

    def test_matrix_exception_rejects_more_than_twelve_scenarios(self) -> None:
        task = _task_with_scenarios(13)
        task["mergedScenarioRefs"] = task["specRefs"][1:]

        self.assertIn("oversized_plan_task_must_split", _reasons(task))

    def test_rejects_scenario_range_or_concatenation_shorthand(self) -> None:
        for anchor in ("SCN-001~SCN-009", "SCN-001, SCN-002", "SCN-001SCN-006", "SCN-001到SCN-009"):
            task = _task_with_scenarios(1)
            task["specRefs"] = ["specs/capability/spec.md#REQ-001", f"specs/capability/spec.md#{anchor}"]

            self.assertIn("invalid_plan_task_scenario_reference", _reasons(task), anchor)


if __name__ == "__main__":
    unittest.main()
