#!/usr/bin/env python3
"""Unit tests for optimistic parallel execution."""

import pytest
from pathlib import Path
from hooks.parallel_runtime import resource_groups, _optimistic_grouping, _conservative_grouping


class TestOptimisticGrouping:
    """Test optimistic grouping strategy."""

    def test_ignores_write_set_overlap(self):
        """Optimistic mode should ignore write-set conflicts."""
        manifest = {
            "runtimeConfig": {
                "parallelSchedulingMode": "optimistic",
                "maxParallel": 3
            },
            "batches": {
                "B001": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "backend"
                },
                "B002": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],  # Same file as B001
                    "repositoryRef": "backend"
                },
                "B003": {
                    "executionStage": "parallel",
                    "writeSet": ["src/api.py"],
                    "repositoryRef": "backend"
                },
                "B004": {
                    "executionStage": "parallel",
                    "writeSet": ["src/api.py"],  # Same file as B003
                    "repositoryRef": "backend"
                }
            }
        }

        groups = resource_groups(manifest, ["B001", "B002", "B003", "B004"])

        # Should group by maxParallel=3, not by write-set
        assert len(groups) == 2
        assert len(groups[0]) == 3
        assert len(groups[1]) == 1

    def test_respects_max_parallel(self):
        """Should respect maxParallel limit."""
        batch_ids = [f"B{i:03d}" for i in range(1, 11)]
        groups = _optimistic_grouping(batch_ids, max_parallel=4)

        assert len(groups) == 3  # 10 / 4 = 2 full + 1 partial
        assert len(groups[0]) == 4
        assert len(groups[1]) == 4
        assert len(groups[2]) == 2

    def test_critical_phase_still_serial(self):
        """proto/global/integration should remain single-batch."""
        manifest = {
            "runtimeConfig": {
                "parallelSchedulingMode": "optimistic",
                "maxParallel": 4
            },
            "batches": {
                "B001": {"executionStage": "proto"},
                "B002": {"executionStage": "proto"}
            }
        }

        groups = resource_groups(manifest, ["B001", "B002"])

        # Should return single-batch groups (one per batch)
        assert len(groups) == 2
        assert groups[0] == ["B001"]
        assert groups[1] == ["B002"]


class TestConservativeGrouping:
    """Test conservative grouping strategy (existing behavior)."""

    def test_respects_write_set_overlap(self):
        """Conservative mode should serialize write-set conflicts."""
        manifest = {
            "runtimeConfig": {
                "parallelSchedulingMode": "conservative",
                "maxParallel": 4
            },
            "batches": {
                "B001": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "backend",
                    "gitRoot": "/repo"
                },
                "B002": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "backend",
                    "gitRoot": "/repo"
                }
            }
        }

        groups = resource_groups(manifest, ["B001", "B002"])

        # Should split into separate groups
        assert len(groups) >= 2

    def test_different_repos_parallel(self):
        """Different repos should be parallel even in conservative mode."""
        manifest = {
            "runtimeConfig": {
                "parallelSchedulingMode": "conservative"
            },
            "batches": {
                "B001": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "backend",
                    "gitRoot": "/repo1"
                },
                "B002": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "frontend",
                    "gitRoot": "/repo2"
                }
            }
        }

        groups = resource_groups(manifest, ["B001", "B002"])

        # Different repos should be in same group
        assert len(groups) == 1
        assert len(groups[0]) == 2


class TestDefaultBehavior:
    """Test default behavior when no config provided."""

    def test_defaults_to_conservative(self):
        """Should default to conservative mode when no config."""
        manifest = {
            "batches": {
                "B001": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "backend",
                    "gitRoot": "/repo"
                },
                "B002": {
                    "executionStage": "parallel",
                    "writeSet": ["src/core.py"],
                    "repositoryRef": "backend",
                    "gitRoot": "/repo"
                }
            }
        }

        groups = resource_groups(manifest, ["B001", "B002"])

        # Should serialize (conservative default)
        assert len(groups) >= 2


class TestMixedScenarios:
    """Test mixed scenarios combining different features."""

    def test_optimistic_with_dependencies(self):
        """Optimistic mode should still respect dependency order."""
        # This test would need scheduler integration
        # For now, just verify grouping doesn't break dependencies
        pass

    def test_empty_write_set_optimistic(self):
        """Empty write-set should work in optimistic mode."""
        manifest = {
            "runtimeConfig": {"parallelSchedulingMode": "optimistic", "maxParallel": 2},
            "batches": {
                "B001": {"executionStage": "parallel", "writeSet": []},
                "B002": {"executionStage": "parallel", "writeSet": []}
            }
        }

        groups = resource_groups(manifest, ["B001", "B002"])
        assert len(groups) == 1
        assert len(groups[0]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
