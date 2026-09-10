#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for Draft-stage engineering command configuration and validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.plan_writer import _validate_draft_engineering_commands  # noqa: E402


class EngineeringCommandValidationTest(unittest.TestCase):
    """Test _validate_draft_engineering_commands function."""

    def test_backend_workspace_requires_compile_command(self):
        """Each backend workspace should require its own compile command."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "backend-repo-a",
                    "executionMode": "code",
                }
            ],
            "compileProfiles": {},  # Missing compile command
        }

        errors = _validate_draft_engineering_commands(data)
        self.assertEqual(len(errors), 1)  # Missing compile only; E2E command is optional
        reasons = {e["reason"] for e in errors}
        self.assertIn("missing_backend_compile_command", reasons)

    def test_multiple_backend_workspaces_require_separate_commands(self):
        """Multiple backend workspaces should each have their own compile command."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "backend-a",
                    "executionMode": "code",
                },
                {
                    "id": "T002",
                    "uiRequired": False,
                    "workspaceRef": "backend-b",
                    "executionMode": "code",
                }
            ],
            "compileProfiles": {
                "backend": {
                    "commands": [
                        {"argv": ["mvn", "compile"], "repo": "backend-a"}
                        # Missing backend-b compile command
                    ]
                }
            },
            "projectValidationCommands": [
                {"kind": "integration_test", "required": True, "repo": "backend-a"},
            ],
        }

        errors = _validate_draft_engineering_commands(data)
        missing_compile = [e for e in errors if e["reason"] == "missing_backend_compile_command"]
        self.assertEqual(len(missing_compile), 1)
        self.assertIn("backend-b", missing_compile[0]["detail"])

    def test_duplicate_compile_commands_rejected(self):
        """Duplicate compile commands for same workspace should be rejected."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "default",
                    "executionMode": "code",
                }
            ],
            "compileProfiles": {
                "backend": {
                    "commands": [
                        {"argv": ["mvn", "compile"], "repo": None},
                        {"argv": ["mvn", "clean", "compile"], "repo": None},  # Duplicate
                    ]
                }
            },
            "projectValidationCommands": [
                {"kind": "integration_test", "required": True, "repo": None}
            ],
        }

        errors = _validate_draft_engineering_commands(data)
        duplicate = [e for e in errors if e["reason"] == "duplicate_backend_compile_command"]
        self.assertEqual(len(duplicate), 1)

    def test_project_e2e_command_is_optional(self):
        """Batch UTest enables finalization without a project-level command."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "backend",
                    "executionMode": "code",
                }
            ],
            "compileProfiles": {
                "backend": {"commands": [{"argv": ["mvn", "compile"], "repo": "backend"}]}
            },
            "projectValidationCommands": [
                {"kind": "integration_test", "required": False, "repo": "backend"}  # Not required
            ],
        }

        errors = _validate_draft_engineering_commands(data)
        self.assertEqual(errors, [])

    def test_external_dependency_tasks_not_checked(self):
        """External dependency tasks should not require compile commands."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "backend",
                    "executionMode": "external_dependency",
                }
            ],
            "compileProfiles": {},
            "projectValidationCommands": [],
        }

        errors = _validate_draft_engineering_commands(data)
        self.assertEqual(len(errors), 0)  # External dependencies don't need compile commands

    def test_complete_commands_pass_validation(self):
        """Should pass validation when all required commands are present."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "backend",
                    "executionMode": "code",
                },
                {
                    "id": "T002",
                    "uiRequired": True,
                    "workspaceRef": "frontend",
                    "executionMode": "code",
                }
            ],
            "compileProfiles": {
                "backend": {"commands": [{"argv": ["mvn", "compile"], "repo": "backend"}]},
                "frontend": {"commands": [{"argv": ["npm", "run", "build"], "repo": "frontend"}]},
            },
            "projectValidationCommands": [
                {"kind": "integration_test", "required": True, "repo": "backend"},
                {"kind": "integration_test", "required": True, "repo": "frontend"},
            ],
        }

        errors = _validate_draft_engineering_commands(data)
        self.assertEqual(len(errors), 0)

    def test_default_workspace_matching(self):
        """Default workspace should match commands with repo=None."""
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "uiRequired": False,
                    "workspaceRef": "default",
                    "executionMode": "code",
                }
            ],
            "compileProfiles": {
                "backend": {"commands": [{"argv": ["mvn", "compile"], "repo": None}]}
            },
            "projectValidationCommands": [
                {"kind": "integration_test", "required": True, "repo": None}
            ],
        }

        errors = _validate_draft_engineering_commands(data)
        self.assertEqual(len(errors), 0)

    def test_none_workspace_ref_uses_default_compile_profile(self):
        """An omitted Draft binding must not become the literal workspace 'None'."""
        data = {
            "tasks": [{
                "id": "T001",
                "uiRequired": False,
                "workspaceRef": None,
                "executionMode": "code",
            }],
            "compileProfiles": {
                "backend": {"commands": [{"argv": ["mvn", "compile"], "repo": None}]}
            },
        }

        self.assertEqual(_validate_draft_engineering_commands(data), [])

    def test_blank_workspace_ref_uses_default_compile_profile(self):
        """A blank Draft binding must use the default frontend workspace."""
        for workspace_ref in ("", "   "):
            with self.subTest(workspace_ref=workspace_ref):
                data = {
                    "tasks": [{
                        "id": "T001",
                        "uiRequired": True,
                        "workspaceRef": workspace_ref,
                        "executionMode": "code",
                    }],
                    "compileProfiles": {
                        "frontend": {"commands": [{"argv": ["npm", "run", "build"], "repo": None}]}
                    },
                }

                self.assertEqual(_validate_draft_engineering_commands(data), [])


if __name__ == "__main__":
    unittest.main()
