#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.utest_assignment_router import UTestAssignmentError, build_assignments  # noqa: E402


SKILL_PATH = ROOT / "skills" / "autodev" / "autodev-utest" / "SKILL.md"


def task(task_id, workspace_ref="default", behavior="observable behavior"):
    criterion_id = "AC-{}-01".format(task_id)
    command_id = "VAL-{}-01".format(task_id)
    return {
        "id": task_id,
        "title": "Task {}".format(task_id),
        "goal": behavior,
        "implementationPoints": [
            "implement {}".format(behavior),
            "expose the public seam",
        ],
        "workspaceRef": workspace_ref,
        "validationBoundary": "public service seam",
        "nonGoals": ["unrelated pricing behavior"],
        "specRefs": [
            "specs/cap/spec.md#REQ-001",
            "specs/cap/spec.md#SCN-001",
        ],
        "acceptanceCriteria": [
            {
                "id": criterion_id,
                "text": behavior,
                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
            }
        ],
        "validationCommands": [
            {
                "id": command_id,
                "argv": ["mvn", "test-compile"],
                "cwd": ".",
                "kind": "behavior_test",
                "required": True,
                "covers": [criterion_id],
            }
        ],
        "validationTestPlan": [
            {
                "commandId": command_id,
                "assetType": "unit_test",
                "executionStage": "post_batch",
                "covers": [criterion_id],
                "testIntent": {
                    "behavior": behavior,
                    "acceptanceCriteria": [
                        {
                            "id": criterion_id,
                            "text": behavior,
                            "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                        }
                    ],
                },
            }
        ],
    }


class UTestAssignmentRouterTest(unittest.TestCase):
    def _feature(self, batches):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        feature_dir = Path(temporary.name)
        root_entries = []
        for batch_id, lane, tasks in batches:
            relative = "plans/{}/plan.json".format(batch_id)
            path = feature_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"batchId": batch_id, "executionLane": lane, "tasks": tasks}
                ),
                encoding="utf-8",
            )
            root_entries.append({"batchId": batch_id, "path": relative})
        (feature_dir / "plan.json").write_text(
            json.dumps({"batches": root_entries}), encoding="utf-8"
        )
        return feature_dir

    def test_assignment_exposes_only_routing_metadata_and_minimal_prompt(self):
        source = task("T001")
        source["status"] = "todo"
        source["evidenceIds"] = ["ev_runtime"]
        feature_dir = self._feature([("B001", "backend", [source])])

        assignment = build_assignments(feature_dir)[0]
        prompt = json.loads(
            assignment["promptContent"].split("\n", 1)[1].rsplit("\n", 1)[0]
        )

        self.assertNotIn("tasks", assignment)
        self.assertEqual(["T001"], assignment["taskIds"])
        self.assertEqual(
            {"id", "implementationPoints", "nonGoals", "validationLocations"},
            set(prompt["tasks"][0]),
        )
        for field in (
            "title",
            "goal",
            "acceptanceCriteria",
            "specRefs",
            "validationBoundary",
            "taskDigest",
            "validationCommands",
            "validationTestPlan",
        ):
            self.assertNotIn(field, assignment["promptContent"])

    def test_backend_then_frontend_preserves_batch_and_workspace_order(self):
        feature_dir = self._feature(
            [
                ("B001", "frontend", [task("T001", "web-a")]),
                ("B002", "backend", [task("T002", "api-a")]),
                ("B003", "frontend", [task("T003", "web-b")]),
                ("B004", "backend", [task("T004", "api-b")]),
            ]
        )

        assignments = build_assignments(feature_dir)

        self.assertEqual(
            ["B002", "B004", "B001", "B003"],
            [item["batchId"] for item in assignments],
        )
        self.assertEqual(
            ["api-a", "api-b", "web-a", "web-b"],
            [item["workspaceRef"] for item in assignments],
        )

    def test_same_workspace_groups_tasks_in_minimal_prompt(self):
        feature_dir = self._feature(
            [("B001", "backend", [task("T001"), task("T002")])]
        )

        assignment = build_assignments(feature_dir)[0]

        self.assertEqual(["T001", "T002"], assignment["taskIds"])
        self.assertNotIn("tasks", assignment)
        content = json.loads(
            assignment["promptContent"].split("\n", 1)[1].rsplit("\n", 1)[0]
        )
        self.assertEqual(
            task("T001")["implementationPoints"],
            content["tasks"][0]["implementationPoints"],
        )
        self.assertEqual(
            task("T001")["nonGoals"],
            content["tasks"][0]["nonGoals"],
        )
        self.assertEqual(str((feature_dir / "plans/B001/plan.json").resolve()), content["batchPlanPath"])
        self.assertEqual(
            {"id", "implementationPoints", "nonGoals", "validationLocations"},
            set(content["tasks"][0]),
        )
        self.assertNotIn("validationCommands", assignment["promptContent"])
        self.assertNotIn("acceptanceCriteria", assignment["promptContent"])
        self.assertNotIn("specRefs", assignment["promptContent"])
        self.assertNotIn("taskDigest", assignment["promptContent"])
        self.assertNotIn("validationBoundary", assignment["promptContent"])
        self.assertNotIn("mvn", assignment["promptContent"])

    def test_missing_workspace_ref_has_repair_instruction(self):
        broken = task("T001")
        broken.pop("workspaceRef")
        feature_dir = self._feature([("B001", "backend", [broken])])

        with self.assertRaises(UTestAssignmentError) as caught:
            build_assignments(feature_dir)

        self.assertIn("workspaceRef", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_validation_command_argv_is_ignored_even_when_it_is_maven_validate(self):
        source = task("T008", behavior="限时时间段、商品范围与次数限制")
        source["validationCommands"][0]["argv"] = ["mvn", "validate"]
        source["validationTestPlan"][0]["testIntent"]["behavior"] = "阶梯折扣"
        feature_dir = self._feature([("B008", "backend", [source])])

        assignment = build_assignments(feature_dir)[0]

        self.assertIn("限时时间段、商品范围与次数限制", assignment["promptContent"])
        self.assertNotIn("mvn", assignment["promptContent"])
        self.assertNotIn("阶梯折扣", assignment["promptContent"])

    def test_empty_validation_commands_defaults_to_workspace_root(self):
        source = task("T001", workspace_ref="ruoyi-vue-pro")
        source["validationCommands"] = []
        source["validationTestPlan"] = []
        feature_dir = self._feature([("B001", "backend", [source])])

        assignment = build_assignments(feature_dir)[0]
        prompt = json.loads(
            assignment["promptContent"].split("\n", 1)[1].rsplit("\n", 1)[0]
        )

        self.assertEqual(
            [{"repo": "ruoyi-vue-pro", "cwd": "."}],
            prompt["tasks"][0]["validationLocations"],
        )


class UTestWorkflowTextContractTest(unittest.TestCase):
    def test_inline_fallback_and_plan_authority_are_explicit(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("task 工具不可用时，不模拟子任务", skill)
        self.assertIn("系统约束与工程事实冲突：`contract_gap`", skill)
        self.assertIn("assignment 的 `promptContent`", skill)
        self.assertIn("`implementationPoints`", skill)
        self.assertIn("从 `validationCommands` 提取的 `validationLocations.repo/cwd`", skill)
        self.assertIn("Batch plan 的绝对路径", skill)
        self.assertIn("不得附加 plan TASK JSON", skill)
        self.assertIn("--test-file \"<RELATIVE_TEST_FILE>\"", skill)
        self.assertNotIn("--task-digest \"<TASK_DIGEST>\"", skill)
        self.assertNotIn("validationTestPlan.commandId", skill)
        self.assertNotIn("set-verdict --feature", skill)


if __name__ == "__main__":
    unittest.main()
