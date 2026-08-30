from __future__ import annotations

import unittest

from hooks.validation_policy import (
    command_policy_errors,
    compile_only_command_errors,
    compile_only_package_script_errors,
    compile_only_package_scripts_errors,
    frontend_compile_command_matches_kind,
    package_script_name,
    package_script_policy_errors,
    maven_test_policy_errors,
    maven_test_target_sources,
    task_validation_kinds_for_lane,
)
from hooks.validation_groups import plan_validation_groups


class ValidationPolicyTest(unittest.TestCase):
    def test_path_probe_cannot_satisfy_compile(self) -> None:
        self.assertEqual(
            ["compile_command_path_probe"],
            compile_only_command_errors({"argv": ["ls", "src/views"], "cwd": "."}),
        )

    def test_compile_only_policy_supports_frontend_build_and_typecheck(self) -> None:
        self.assertEqual(
            compile_only_command_errors({"argv": ["npm", "run", "build"]}),
            [],
        )
        self.assertEqual(
            compile_only_command_errors({"argv": ["pnpm", "typecheck"]}),
            [],
        )
        self.assertEqual(
            compile_only_command_errors({"argv": ["npx", "tsc", "--noEmit"]}),
            [],
        )
        self.assertEqual(
            compile_only_command_errors({"argv": ["yarn", "build"]}),
            [],
        )
        self.assertEqual(
            compile_only_command_errors({"argv": ["bun", "run", "typecheck"]}),
            [],
        )

    def test_compile_only_policy_rejects_frontend_and_maven_tests(self) -> None:
        self.assertIn(
            "compile_command_executes_tests",
            compile_only_command_errors({"argv": ["npm", "test"]}),
        )
        self.assertIn(
            "compile_command_executes_tests",
            compile_only_command_errors({"argv": ["npx", "vitest", "run"]}),
        )
        self.assertIn(
            "compile_command_executes_tests",
            compile_only_command_errors({"argv": ["yarn", "test"]}),
        )
        self.assertIn(
            "compile_command_executes_tests",
            compile_only_command_errors({"argv": ["bun", "test"]}),
        )
        self.assertIn(
            "compile_command_not_compile_only",
            compile_only_command_errors({"argv": ["mvn", "verify"]}),
        )
        self.assertEqual(
            compile_only_command_errors({"argv": ["mvn", "clean", "compile"]}),
            [],
        )
        self.assertEqual(
            compile_only_command_errors({"argv": ["mvn", "-pl", "web", "-am", "compile"]}),
            [],
        )
        self.assertIn(
            "compile_command_not_compile_only",
            compile_only_command_errors({"argv": ["mvn", "validate", "generate-sources"]}),
        )
        self.assertIn(
            "compile_command_not_compile_only",
            compile_only_command_errors({"argv": ["gradle", "build"]}),
        )

    def test_frontend_batch_uses_compile_kind_with_build_or_typecheck_argv(self) -> None:
        for argv in (
            ["npm", "run", "build"],
            ["pnpm", "typecheck"],
            ["npx", "tsc", "--noEmit"],
        ):
            with self.subTest(argv=argv):
                command = {"argv": argv, "kind": "compile", "required": True}
                self.assertEqual(compile_only_command_errors(command), [])

    def test_compile_only_policy_rejects_test_chained_from_frontend_build_script(self) -> None:
        self.assertIn(
            "compile_package_script_executes_tests",
            compile_only_package_script_errors("vite build && vitest run"),
        )
        self.assertEqual(compile_only_package_script_errors("vite build"), [])
        self.assertIn(
            "compile_package_script_executes_tests",
            compile_only_package_scripts_errors(
                {"prebuild": "vitest run", "build": "vite build"},
                "build",
            ),
        )

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

    def test_groups_compatible_maven_tests_into_one_physical_command(self) -> None:
        groups = plan_validation_groups({"tasks": [
            {
                "id": "T001",
                "validationCommands": [{
                    "id": "VAL-T001-01",
                    "argv": ["mvn", "test", "-q", "-Dtest=FirstTest"],
                    "cwd": "service",
                    "repo": "backend",
                    "kind": "behavior_test",
                    "required": True,
                }],
            },
            {
                "id": "T002",
                "validationCommands": [{
                    "id": "VAL-T002-01",
                    "argv": ["mvn", "test", "-q", "-Dtest=SecondTest#fails"],
                    "cwd": "service",
                    "repo": "backend",
                    "kind": "behavior_test",
                    "required": True,
                }],
            },
        ]})
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["strategy"], "maven_test_aggregate")
        self.assertEqual(groups[0]["taskIds"], ["T001", "T002"])
        self.assertEqual(
            groups[0]["physicalCommand"]["argv"],
            ["mvn", "test", "-q", "-Dtest=FirstTest,SecondTest#fails"],
        )

    def test_keeps_incompatible_maven_tests_separate(self) -> None:
        groups = plan_validation_groups({"tasks": [
            {
                "id": "T001",
                "validationCommands": [{
                    "id": "VAL-T001-01",
                    "argv": ["mvn", "test", "-Dtest=FirstTest"],
                    "cwd": "service-a",
                    "kind": "behavior_test",
                    "required": True,
                }],
            },
            {
                "id": "T002",
                "validationCommands": [{
                    "id": "VAL-T002-01",
                    "argv": ["mvn", "test", "-Dtest=SecondTest"],
                    "cwd": "service-b",
                    "kind": "behavior_test",
                    "required": True,
                }],
            },
        ]})
        self.assertEqual(len(groups), 2)

    def test_deduplicates_exact_frontend_compile_commands(self) -> None:
        command = {
            "argv": ["npm", "run", "build"],
            "cwd": ".",
            "repo": "frontend",
            "kind": "build",
            "required": True,
        }
        groups = plan_validation_groups({"tasks": [
            {"id": "T001", "validationCommands": [{"id": "VAL-T001-01", **command}]},
            {"id": "T002", "validationCommands": [{"id": "VAL-T002-01", **command}]},
        ]})
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["strategy"], "exact_frontend_compile")
        self.assertEqual(len(groups[0]["logicalCommands"]), 2)


if __name__ == "__main__":
    unittest.main()
