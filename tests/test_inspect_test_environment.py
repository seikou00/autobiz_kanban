#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.inspect_test_environment import (  # noqa: E402
    TestEnvironmentError,
    inspect_environment,
    main,
)


class InspectTestEnvironmentTest(unittest.TestCase):
    def _root(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _package(self, root, data):
        (root / "package.json").write_text(json.dumps(data), encoding="utf-8")

    def test_spring_maven_ready(self):
        root = self._root()
        (root / "pom.xml").write_text(
            "<dependency><artifactId>spring-boot-starter-test</artifactId></dependency>",
            encoding="utf-8",
        )

        result = inspect_environment(root, "spring-boot-3")

        self.assertEqual("ready", result["status"])
        self.assertEqual("maven", result["runner"])
        self.assertEqual("maven", result["packageManager"])
        self.assertEqual(["pom.xml"], result["manifests"])

    def test_spring_gradle_missing_environment_returns_profile(self):
        root = self._root()
        (root / "build.gradle.kts").write_text("plugins { java }", encoding="utf-8")

        result = inspect_environment(root, "spring-boot-2")

        self.assertEqual("init_required", result["status"])
        self.assertEqual("gradle", result["runner"])
        self.assertEqual("spring-gradle-junit", result["initProfile"])

    def test_spring_maven_missing_and_gradle_ready_matrix(self):
        cases = (
            ("pom.xml", "<project/>", "maven", "init_required", "spring-maven-junit"),
            (
                "build.gradle",
                "testImplementation 'org.springframework.boot:spring-boot-starter-test'",
                "gradle",
                "ready",
                None,
            ),
        )
        for file_name, content, runner, status, profile in cases:
            with self.subTest(file_name=file_name):
                root = self._root()
                (root / file_name).write_text(content, encoding="utf-8")

                result = inspect_environment(root, "spring-boot-3")

                self.assertEqual(runner, result["runner"])
                self.assertEqual(status, result["status"])
                self.assertEqual(profile, result["initProfile"])

    def test_vue_vite_without_runner_returns_vitest_profile(self):
        root = self._root()
        self._package(
            root,
            {"dependencies": {"vue": "3", "vite": "latest"}, "packageManager": "pnpm@9"},
        )
        (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9'", encoding="utf-8")

        result = inspect_environment(root, "vue3")

        self.assertEqual("init_required", result["status"])
        self.assertEqual("pnpm", result["packageManager"])
        self.assertEqual("vue3-vite-vitest", result["initProfile"])

    def test_runner_is_detected_from_test_unit_script(self):
        root = self._root()
        self._package(
            root,
            {
                "dependencies": {"react": "latest", "vite": "latest"},
                "scripts": {"test:unit": "vitest run"},
            },
        )

        result = inspect_environment(root, "react")

        self.assertEqual("ready", result["status"])
        self.assertEqual("vitest", result["runner"])
        self.assertEqual("implicit", result["configState"])

    def test_react_vite_without_runner_returns_vitest_profile(self):
        root = self._root()
        self._package(root, {"dependencies": {"react": "latest", "vite": "latest"}})

        result = inspect_environment(root, "react")

        self.assertEqual("init_required", result["status"])
        self.assertEqual("react-vite-vitest", result["initProfile"])

    def test_each_standard_lock_selects_package_manager(self):
        cases = (
            ("package-lock.json", "npm"),
            ("yarn.lock", "yarn"),
            ("pnpm-lock.yaml", "pnpm"),
        )
        for lock_name, manager in cases:
            with self.subTest(lock_name=lock_name):
                root = self._root()
                self._package(
                    root,
                    {
                        "dependencies": {"react": "latest", "vite": "latest"},
                        "devDependencies": {"vitest": "latest"},
                    },
                )
                (root / lock_name).write_text("", encoding="utf-8")

                result = inspect_environment(root, "react")

                self.assertEqual("ready", result["status"])
                self.assertEqual(manager, result["packageManager"])

    def test_existing_jest_is_reused(self):
        root = self._root()
        self._package(
            root,
            {"dependencies": {"react": "latest"}, "devDependencies": {"jest": "latest"}},
        )
        (root / "jest.config.js").write_text("module.exports = {}", encoding="utf-8")

        result = inspect_environment(root, "react")

        self.assertEqual("ready", result["status"])
        self.assertEqual("jest", result["runner"])
        self.assertEqual("present", result["configState"])

    def test_conflicting_lock_files_are_blocking(self):
        root = self._root()
        self._package(root, {"dependencies": {"vue": "3", "vite": "latest"}})
        (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        (root / "yarn.lock").write_text("", encoding="utf-8")

        result = inspect_environment(root, "vue")

        self.assertEqual("conflict", result["status"])
        self.assertEqual("conflict", result["configState"])
        self.assertIn("修复：", result["errors"][0])

    def test_framework_manifest_conflict_is_blocking(self):
        root = self._root()
        self._package(root, {"dependencies": {"react": "latest", "vite": "latest"}})

        result = inspect_environment(root, "vue3")

        self.assertEqual("conflict", result["status"])
        self.assertIn("系统约束", result["errors"][0])

    def test_vue3_constraint_rejects_definite_vue2_manifest(self):
        for version in ("2.7.16", "^2.7.16", "~2.6.14", "npm:vue@^2.7.16"):
            with self.subTest(version=version):
                root = self._root()
                self._package(
                    root,
                    {
                        "dependencies": {"vue": version, "vite": "latest"},
                        "devDependencies": {"vitest": "latest"},
                    },
                )

                result = inspect_environment(root, "vue3")

                self.assertEqual("conflict", result["status"])
                self.assertEqual("conflict", result["configState"])
                self.assertIn("Vue2", result["errors"][0])
                self.assertIn("不要自动升级生产框架", result["errors"][0])

    def test_next_without_existing_runner_is_unsupported(self):
        root = self._root()
        self._package(root, {"dependencies": {"react": "latest", "next": "latest"}})

        result = inspect_environment(root, "react")

        self.assertEqual("unsupported", result["status"])
        self.assertEqual("unsupported", result["configState"])

    def test_unknown_framework_returns_repairable_unsupported_result(self):
        root = self._root()
        result = inspect_environment(root, "svelte")

        self.assertEqual("unsupported", result["status"])
        self.assertIn("修复：", result["errors"][0])

    def test_invalid_package_json_has_repair_instruction(self):
        root = self._root()
        (root / "package.json").write_text("{", encoding="utf-8")

        with self.assertRaises(TestEnvironmentError) as caught:
            inspect_environment(root, "react")

        self.assertIn("修复：", str(caught.exception))

    def test_cli_accepts_json_flag(self):
        root = self._root()
        (root / "pom.xml").write_text(
            "<artifactId>spring-boot-starter-test</artifactId>", encoding="utf-8"
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                ["--framework", "spring-boot-3", "--workspace", str(root), "--json"]
            )

        self.assertEqual(0, exit_code)
        self.assertEqual("ready", json.loads(stdout.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
