from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from hooks.validation_policy import (
    command_policy_errors,
    frontend_compile_command_matches_kind,
    package_script_name,
    package_script_policy_errors,
    maven_test_plan,
    maven_test_policy_errors,
    maven_test_target_sources,
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

    def test_package_script_policy_rejects_missing_and_noop_scripts(self) -> None:
        self.assertEqual(package_script_name({"argv": ["npm", "run", "build"]}), "build")
        self.assertEqual(package_script_policy_errors(None), ["validation_package_script_missing"])
        self.assertEqual(package_script_policy_errors("echo build ok"), ["validation_command_noop"])
        self.assertEqual(package_script_policy_errors("vite build"), [])

    def test_maven_test_plan_distinguishes_existing_and_to_be_created_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src" / "test" / "java" / "example" / "ExistingTest.java"
            source.parent.mkdir(parents=True)
            source.write_text("class ExistingTest {}\n", encoding="utf-8")
            existing = {
                "id": "VAL-T001-01",
                "argv": ["mvn", "test", "-Dtest=example.ExistingTest,NewTest"],
            }
            plan = maven_test_plan(existing, root)
            self.assertIsNotNone(plan)
            self.assertEqual(
                [target["mode"] for target in plan["targets"]],
                ["reuse_existing", "create_in_code"],
            )
            self.assertEqual(
                maven_test_target_sources(root, "example.ExistingTest"),
                [source],
            )

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
