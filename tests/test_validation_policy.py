from __future__ import annotations

import unittest

from hooks.validation_policy import (
    command_policy_errors,
    frontend_compile_command_matches_kind,
    maven_test_policy_errors,
    task_validation_kinds_for_lane,
)


class ValidationPolicyTest(unittest.TestCase):


    def test_rejects_direct_noop_placeholder_and_inline_shell(self) -> None:
        self.assertEqual(
            command_policy_errors({"argv": ["echo", "ok"]}),
            ["validation_command_noop"],
        )
        self.assertIn(
            "validation_command_placeholder",
            command_policy_errors({"argv": ["tool", "validation placeholder"]}),
        )
        self.assertIn(
            "validation_command_inline_shell_forbidden",
            command_policy_errors({"argv": ["bash", "-c", "run-tests"]}),
        )

    def test_allows_repository_script_without_inline_shell(self) -> None:
        self.assertEqual(command_policy_errors({"argv": ["bash", "scripts/validate.sh"]}), [])

    def test_frontend_compile_commands_must_match_kind(self) -> None:
        self.assertTrue(frontend_compile_command_matches_kind({
            "argv": ["npm", "run", "build"],
            "kind": "build",
        }))
        self.assertTrue(frontend_compile_command_matches_kind({
            "argv": ["npx", "tsc", "--noEmit"],
            "kind": "typecheck",
        }))
        self.assertFalse(frontend_compile_command_matches_kind({
            "argv": ["npm", "run", "typecheck"],
            "kind": "build",
        }))

    def test_validation_kinds_are_lane_specific(self) -> None:
        self.assertNotIn("build", task_validation_kinds_for_lane("backend"))
        self.assertIn("build", task_validation_kinds_for_lane("frontend"))


    def test_maven_target_policy_rejects_skip_and_non_concrete_selectors(self) -> None:
        command = {
            "argv": [
                "mvn",
                "test",
                "-Dtest=ExampleTest.java",
                "-DskipTests=true",
                "-Dsurefire.failIfNoSpecifiedTests=false",
            ]
        }
        errors = maven_test_policy_errors(command)
        self.assertIn("maven_test_execution_skipped", errors)
        self.assertIn("maven_test_zero_match_allowed", errors)
        self.assertIn("maven_test_selector_must_name_class", errors)


if __name__ == "__main__":
    unittest.main()
