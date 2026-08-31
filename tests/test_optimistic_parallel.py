#!/usr/bin/env python3
"""Regression tests for optimistic Batch scheduling."""

from __future__ import annotations

import unittest

from hooks.parallel_runtime import _optimistic_grouping, resource_groups


class OptimisticGroupingTest(unittest.TestCase):
    def test_ignores_write_set_overlap_but_respects_limit(self) -> None:
        manifest = {
            "runtimeConfig": {"parallelSchedulingMode": "optimistic", "maxParallel": 3},
            "batches": {
                "B001": {"executionStage": "parallel", "writeSet": ["src/core.py"]},
                "B002": {"executionStage": "parallel", "writeSet": ["src/core.py"]},
                "B003": {"executionStage": "parallel", "writeSet": ["src/api.py"]},
                "B004": {"executionStage": "parallel", "writeSet": ["src/api.py"]},
            },
        }

        self.assertEqual(
            resource_groups(manifest, ["B001", "B002", "B003", "B004"]),
            [["B001", "B002", "B003"], ["B004"]],
        )

    def test_critical_stages_remain_serial(self) -> None:
        manifest = {
            "runtimeConfig": {"parallelSchedulingMode": "optimistic", "maxParallel": 4},
            "batches": {
                "B001": {"executionStage": "proto"},
                "B002": {"executionStage": "proto"},
            },
        }
        self.assertEqual(resource_groups(manifest, ["B001", "B002"]), [["B001"], ["B002"]])

    def test_default_remains_conservative(self) -> None:
        manifest = {
            "batches": {
                "B001": {"executionStage": "parallel", "writeSet": ["src/core.py"], "gitRoot": "/repo"},
                "B002": {"executionStage": "parallel", "writeSet": ["src/core.py"], "gitRoot": "/repo"},
            },
        }
        self.assertEqual(resource_groups(manifest, ["B001", "B002"]), [["B001"], ["B002"]])

    def test_grouping_rejects_non_positive_limit_before_range(self) -> None:
        with self.assertRaises(ValueError):
            _optimistic_grouping(["B001"], 0)


if __name__ == "__main__":
    unittest.main()
