#!/usr/bin/env python3
"""Regression coverage for the retired Draft batch-compile contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.plan_writer import _validate_draft_engineering_commands  # noqa: E402


class EngineeringCommandValidationTest(unittest.TestCase):
    def test_code_tasks_do_not_require_compile_commands(self) -> None:
        data = {
            "tasks": [
                {"id": "T001", "workspaceRef": "backend-a", "executionMode": "code"},
                {"id": "T002", "workspaceRef": "frontend-b", "executionMode": "code"},
            ],
            "qualityGateProfiles": {},
        }

        self.assertEqual(_validate_draft_engineering_commands(data), [])

    def test_draft_validation_has_no_cross_task_compile_requirement(self) -> None:
        data = {
            "tasks": [{"id": "T001", "workspaceRef": "default", "executionMode": "code"}],
        }

        self.assertEqual(_validate_draft_engineering_commands(data), [])


if __name__ == "__main__":
    unittest.main()
