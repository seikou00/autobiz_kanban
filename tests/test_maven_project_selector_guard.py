#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reject `-pl <path>` that re-names the directory the command already runs in.

``-pl`` / ``--projects`` selects modules from the reactor of the POM in ``cwd``.
When ``cwd`` is already the module directory that reactor contains only the
module itself, so a path selector naming the same directory can never resolve
and Maven exits non-zero with ``Could not find the selected project in the
reactor``. Selecting a submodule from an aggregator root stays valid and must
not be flagged.

Structural check only: no language assumption, no repository layout assumption.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.validation_policy import (  # noqa: E402
    command_policy_errors,
    maven_project_selector_errors,
)


REASON = "maven_project_selector_duplicates_cwd"
MODULE = "alpha/beta/service-gamma"


def _command(argv: list[str], cwd: str | None = None) -> dict[str, object]:
    command = {"argv": argv, "kind": "behavior_test", "required": True}
    if cwd is not None:
        command["cwd"] = cwd
    return command


class TestMavenProjectSelectorGuard(unittest.TestCase):
    def assertBlocked(self, command: dict[str, object], message: str) -> None:
        self.assertIn(REASON, maven_project_selector_errors(command), message)
        # The guard is only load-bearing if it reaches callers through the
        # shared entry point rather than sitting unreferenced.
        self.assertIn(REASON, command_policy_errors(command), f"{message} (经 command_policy_errors)")

    def assertAllowed(self, command: dict[str, object], message: str) -> None:
        self.assertNotIn(REASON, maven_project_selector_errors(command), message)
        self.assertNotIn(REASON, command_policy_errors(command), f"{message} (经 command_policy_errors)")

    def test_selector_equal_to_cwd_is_rejected(self):
        self.assertBlocked(
            _command(["mvn", "compile", "-pl", MODULE], cwd=MODULE),
            "cwd 已是模块目录时 -pl 同路径永远解析不到",
        )

    def test_test_command_with_selector_and_pl_is_rejected(self):
        """带 -Dtest= 的命令也要拦，不能被 maven_test_policy_errors 的提前 return 漏掉。"""
        self.assertBlocked(
            _command(["mvn", "test", "-Dtest=AlphaTest", "-pl", MODULE], cwd=MODULE),
            "TASK 级 test 命令同样适用",
        )

    def test_compile_command_without_test_selector_is_rejected(self):
        """不带 -Dtest= 时 maven_test_selectors 返回空，守卫仍须生效。"""
        self.assertBlocked(
            _command(["mvn", "compile", "-pl", MODULE], cwd=MODULE),
            "Batch 级 compile 命令没有 test selector",
        )

    def test_equals_form_is_rejected(self):
        self.assertBlocked(
            _command(["mvn", "compile", f"--projects={MODULE}"], cwd=MODULE),
            "--projects=<path> 与 -pl <path> 等价",
        )

    def test_short_equals_form_is_rejected(self):
        self.assertBlocked(
            _command(["mvn", "compile", f"-pl={MODULE}"], cwd=MODULE),
            "-pl=<path> 也要覆盖",
        )

    def test_spelling_variants_normalize_to_the_same_path(self):
        for spelling in (f"./{MODULE}", f"{MODULE}/", f"./{MODULE}/", MODULE.replace("/", "\\")):
            with self.subTest(spelling=spelling):
                self.assertBlocked(
                    _command(["mvn", "compile", "-pl", spelling], cwd=MODULE),
                    f"{spelling} 与 cwd 指向同一目录",
                )

    def test_cwd_spelling_variants_also_normalize(self):
        self.assertBlocked(
            _command(["mvn", "compile", "-pl", MODULE], cwd=f"./{MODULE}/"),
            "cwd 侧的拼写差异同样要归一",
        )

    def test_comma_list_with_one_offending_entry_is_rejected(self):
        self.assertBlocked(
            _command(["mvn", "compile", "-pl", f"other/module,{MODULE}"], cwd=MODULE),
            "逗号多值中任一命中即拦",
        )

    # --- 必须放行的合法形态 ---

    def test_submodule_from_aggregator_root_is_allowed(self):
        """cwd=Git 根时 -pl <子模块> 是 -pl 的正常用法，不得误杀。"""
        for root_spelling in (".", "./", ""):
            with self.subTest(cwd=root_spelling):
                self.assertAllowed(
                    _command(["mvn", "compile", "-pl", MODULE], cwd=root_spelling),
                    "聚合根挑子模块必须放行",
                )

    def test_selector_naming_a_different_module_is_allowed(self):
        self.assertAllowed(
            _command(["mvn", "compile", "-pl", "other/module"], cwd=MODULE),
            "指向别的模块不属于本检查",
        )

    def test_exclusion_form_is_allowed(self):
        self.assertAllowed(
            _command(["mvn", "compile", "-pl", f"!{MODULE}"], cwd=MODULE),
            "!module 是排除而非选中，语义不同",
        )

    def test_artifact_id_form_is_allowed(self):
        self.assertAllowed(
            _command(["mvn", "compile", "-pl", ":service-gamma"], cwd=MODULE),
            ":artifactId 不是路径",
        )

    def test_command_without_project_list_flag_is_allowed(self):
        self.assertAllowed(
            _command(["mvn", "test", "-Dtest=AlphaTest"], cwd=MODULE),
            "正确形态：cwd 已在模块内，不需要 -pl",
        )

    def test_non_maven_executable_is_ignored(self):
        self.assertAllowed(
            _command(["npm", "run", "build", "-pl", MODULE], cwd=MODULE),
            "-pl 只对 Maven 有这层语义",
        )

    def test_missing_cwd_is_not_guessed(self):
        self.assertAllowed(
            _command(["mvn", "compile", "-pl", MODULE]),
            "cwd 未填时不猜，交给既有的 cwd 校验处理",
        )

    def test_malformed_command_is_tolerated(self):
        for command in (None, {}, {"argv": []}, {"argv": ["mvn"]}, {"argv": "mvn compile"}):
            with self.subTest(command=command):
                self.assertEqual(maven_project_selector_errors(command), [])

    def test_flag_at_end_without_value_is_tolerated(self):
        self.assertAllowed(
            _command(["mvn", "compile", "-pl"], cwd=MODULE),
            "缺实参不应抛异常",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
