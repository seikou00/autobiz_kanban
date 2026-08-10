#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for Maven test target ambiguity detection (hooks/validation_policy.py)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.validation_policy import check_maven_test_target_ambiguity  # noqa: E402


class MavenAmbiguityTests(unittest.TestCase):
    def test_no_ambiguity_fully_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            (command_dir / "src/test/java/com/example/foo").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/foo/SimpleTest.java").write_text("")
            (command_dir / "src/test/java/com/example/bar").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/bar/SimpleTest.java").write_text("")

            command = {"argv": ["mvn", "test", "-Dtest=com.example.foo.SimpleTest"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, [])

    def test_ambiguous_simple_class_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            (command_dir / "src/test/java/com/example/foo").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/foo/SimpleTest.java").write_text("")
            (command_dir / "src/test/java/com/example/bar").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/bar/SimpleTest.java").write_text("")

            command = {"argv": ["mvn", "test", "-Dtest=SimpleTest"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, ["maven_test_selector_ambiguous"])

    def test_no_ambiguity_single_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            (command_dir / "src/test/java/com/example").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/UniqueTest.java").write_text("")

            command = {"argv": ["mvn", "test", "-Dtest=UniqueTest"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, [])

    def test_no_ambiguity_same_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            (command_dir / "src/test/java/com/example").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/TestSuite.java").write_text("")

            command = {"argv": ["mvn", "test", "-Dtest=TestSuite"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, [])

    def test_not_maven_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            command = {"argv": ["npm", "test"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, [])

    def test_maven_without_test_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            command = {"argv": ["mvn", "test"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, [])

    def test_pl_scoping_excludes_other_module(self) -> None:
        """`-pl module-a` must not be flagged ambiguous by a same-named class in module-b.

        Maven only searches the reactor selected by -pl/--projects. A class
        with the same simple name sitting in an unrelated, unselected module
        is never in scope for this command, so it must not trigger a false
        ambiguity error.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            module_a = command_dir / "module-a"
            module_b = command_dir / "module-b"
            (module_a / "src/test/java/com/example/foo").mkdir(parents=True)
            (module_a / "src/test/java/com/example/foo/SimpleTest.java").write_text("")
            (module_b / "src/test/java/com/example/bar").mkdir(parents=True)
            (module_b / "src/test/java/com/example/bar/SimpleTest.java").write_text("")

            command = {"argv": ["mvn", "-pl", "module-a", "test", "-Dtest=SimpleTest"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, [])

    def test_pl_scoping_still_flags_ambiguity_within_selected_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            module_a = command_dir / "module-a"
            (module_a / "src/test/java/com/example/foo").mkdir(parents=True)
            (module_a / "src/test/java/com/example/foo/SimpleTest.java").write_text("")
            (module_a / "src/test/java/com/example/bar").mkdir(parents=True)
            (module_a / "src/test/java/com/example/bar/SimpleTest.java").write_text("")

            command = {"argv": ["mvn", "-pl", "module-a", "test", "-Dtest=SimpleTest"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, ["maven_test_selector_ambiguous"])

    def test_coordinate_selector_falls_back_to_unrestricted_search(self) -> None:
        """`-pl :artifactId` cannot be mapped to a directory, so scoping must not
        silently drop the ambiguity check; falling back to an unscoped search is
        safer than a false negative.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            command_dir = Path(tmpdir)
            (command_dir / "src/test/java/com/example/foo").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/foo/SimpleTest.java").write_text("")
            (command_dir / "src/test/java/com/example/bar").mkdir(parents=True)
            (command_dir / "src/test/java/com/example/bar/SimpleTest.java").write_text("")

            command = {"argv": ["mvn", "-pl", ":module-a", "test", "-Dtest=SimpleTest"]}
            errors = check_maven_test_target_ambiguity(command, command_dir)
            self.assertEqual(errors, ["maven_test_selector_ambiguous"])


if __name__ == "__main__":
    unittest.main()
