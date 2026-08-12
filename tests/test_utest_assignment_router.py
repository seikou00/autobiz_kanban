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

    def test_backend_only_preserves_batch_and_workspace_order(self):
        feature_dir = self._feature(
            [
                ("B001", "backend", [{"id": "T001", "workspaceRef": "api-a"}]),
                (
                    "B002",
                    "backend",
                    [
                        {"id": "T002", "workspaceRef": "api-b"},
                        {"id": "T003", "workspaceRef": "api-b"},
                    ],
                ),
            ]
        )

        assignments = build_assignments(feature_dir)

        self.assertEqual(["B001", "B002"], [item["batchId"] for item in assignments])
        self.assertEqual(["T002", "T003"], assignments[1]["taskIds"])

    def test_frontend_only_preserves_batch_order(self):
        feature_dir = self._feature(
            [
                ("B004", "frontend", [{"id": "T004", "workspaceRef": "web-a"}]),
                ("B005", "frontend", [{"id": "T005", "workspaceRef": "web-b"}]),
            ]
        )

        self.assertEqual(
            ["B004", "B005"],
            [item["batchId"] for item in build_assignments(feature_dir)],
        )

    def test_full_stack_runs_backend_then_frontend_with_stable_lane_order(self):
        feature_dir = self._feature(
            [
                ("B001", "frontend", [{"id": "T001", "workspaceRef": "web-a"}]),
                ("B002", "backend", [{"id": "T002", "workspaceRef": "api-a"}]),
                ("B003", "frontend", [{"id": "T003", "workspaceRef": "web-b"}]),
                ("B004", "backend", [{"id": "T004", "workspaceRef": "api-b"}]),
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

    def test_missing_workspace_ref_has_repair_instruction(self):
        feature_dir = self._feature(
            [("B001", "backend", [{"id": "T001"}])]
        )

        with self.assertRaises(UTestAssignmentError) as caught:
            build_assignments(feature_dir)

        self.assertIn("workspaceRef", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))


class UTestWorkflowTextContractTest(unittest.TestCase):
    def test_inline_fallback_and_constraint_conflict_are_explicit(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("task 工具不可用时，不模拟子任务", skill)
        self.assertIn("系统约束与工程事实冲突：`contract_gap`", skill)
        self.assertIn("先串行执行全部 backend assignments", skill)
        self.assertIn("再串行执行全部 frontend assignments", skill)

    def test_bootstrap_profiles_and_approved_cli_are_connected(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        profile_path = (
            ROOT
            / "skills"
            / "autodev"
            / "autodev-utest"
            / "reference"
            / "test-environment-profiles.md"
        )
        profiles = profile_path.read_text(encoding="utf-8")

        self.assertIn("--framework <spring|vue|react>", skill)
        self.assertIn("--kind setup", skill)
        self.assertIn("--kind test", skill)
        self.assertIn("网络或安装授权被拒绝时分类为 `environment`", skill)
        for marker in (
            "spring-security-test",
            "@vue/test-utils",
            "@testing-library/user-event",
            "@testing-library/jest-dom",
            "test:unit",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, profiles)


if __name__ == "__main__":
    unittest.main()
