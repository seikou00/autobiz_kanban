#!/usr/bin/env python3
"""Regression coverage for Code/UTest write ownership separation."""

from __future__ import annotations

import unittest

from hooks.parallel_runtime import batch_write_set
from hooks.plan_write_ownership import task_write_paths


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


if __name__ == "__main__":
    unittest.main()
