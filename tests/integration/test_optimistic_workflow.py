#!/usr/bin/env python3
"""Integration-level checks for optimistic workflow configuration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hooks.parallel_merge_train import _extract_conflicted_files
from hooks.workflow_launcher import _load_runtime_config


class OptimisticWorkflowIntegrationTest(unittest.TestCase):
    def test_runtime_config_is_loaded_and_invalid_values_fall_back_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_dir = workspace / ".autobiz"
            config_dir.mkdir()
            (config_dir / "runtime_config.json").write_text(
                json.dumps(
                    {
                        "parallelSchedulingMode": "optimistic",
                        "maxParallel": 6,
                        "conflictResolution": {"maxAttempts": 3, "enableAutoResolve": True},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                _load_runtime_config(workspace),
                {
                    "parallelSchedulingMode": "optimistic",
                    "maxParallel": 6,
                    "conflictResolution": {"maxAttempts": 3, "enableAutoResolve": True},
                },
            )

            (config_dir / "runtime_config.json").write_text(
                json.dumps({"parallelSchedulingMode": "invalid", "maxParallel": 0}),
                encoding="utf-8",
            )
            self.assertEqual(_load_runtime_config(workspace)["parallelSchedulingMode"], "conservative")
            self.assertEqual(_load_runtime_config(workspace)["maxParallel"], 4)

    def test_conflict_extractor_reports_all_content_conflicts(self) -> None:
        output = """
Auto-merging src/core.py
CONFLICT (content): Merge conflict in src/core.py
Auto-merging src/api.py
CONFLICT (content): Merge conflict in src/api.py
Automatic merge failed; fix conflicts and then commit.
"""
        self.assertEqual(_extract_conflicted_files(output), ["src/core.py", "src/api.py"])


if __name__ == "__main__":
    unittest.main()
