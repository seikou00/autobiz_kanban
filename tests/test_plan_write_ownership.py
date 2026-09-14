#!/usr/bin/env python3
"""Regression coverage for Code/UTest write ownership separation."""

from __future__ import annotations

import unittest

from hooks.parallel_runtime import batch_write_set
from hooks.plan_write_ownership import task_write_paths, write_ownership_violations


class PlanWriteOwnershipTest(unittest.TestCase):
    def test_test_assets_are_not_code_batch_write_set(self) -> None:
        task = {
            "workspaceRef": "backend",
            "scope": {
                "paths": [
                    "backend:src/main/java/example/ActivityService.java",
                    "backend:src/test/java/example/ActivityServiceTest.java",
                    "tests/integration/test_activity.py",
                ],
            },
            "expectedFiles": [
                "src/main/resources/mapper/ActivityMapper.xml",
                "src/test/java/example/MarketingSchemaIntegrationTest.java",
            ],
        }

        expected = {
            "src/main/java/example/ActivityService.java",
            "src/main/resources/mapper/ActivityMapper.xml",
        }
        self.assertEqual(expected, task_write_paths(task))
        self.assertEqual(tuple(sorted(expected)), batch_write_set({"tasks": [task]}))

    def test_disjoint_controller_method_anchors_can_run_in_separate_batches(self) -> None:
        path = "src/main/java/example/ActivityController.java"
        tasks = [
            {
                "id": "T002", "workspaceRef": "backend",
                "scope": {"paths": [path]},
                "writeTargets": [{"path": path, "symbols": ["ActivityController#add"]}],
            },
            {
                "id": "T003", "workspaceRef": "backend",
                "scope": {"paths": [path]},
                "writeTargets": [{"path": path, "symbols": ["ActivityController#approve"]}],
            },
        ]
        self.assertEqual(
            [],
            write_ownership_violations(tasks, ownership_scope_by_task={"T002": "B001", "T003": "B002"}),
        )

    def test_overlapping_or_unanchored_controller_writes_remain_blocked(self) -> None:
        path = "src/main/java/example/ActivityController.java"
        tasks = [
            {
                "id": "T002", "workspaceRef": "backend",
                "scope": {"paths": [path]},
                "writeTargets": [{"path": path, "symbols": ["ActivityController#approve"]}],
            },
            {
                "id": "T003", "workspaceRef": "backend",
                "scope": {"paths": [path]},
                "writeTargets": [{"path": path, "symbols": ["ActivityController#approve"]}],
            },
        ]
        violations = write_ownership_violations(
            tasks,
            ownership_scope_by_task={"T002": "B001", "T003": "B002"},
        )
        self.assertEqual(1, len(violations))
        self.assertEqual("writeTargets", violations[0]["field"])
        self.assertIn("ownership=member_anchor", violations[0]["detail"])


if __name__ == "__main__":
    unittest.main()
