"""重复待创建测试目标的结构化预检。

被测函数只读 writer 自己派生的 ``validationTestPlan``，不含命名规则、
不含语言假设、不碰文件系统，因此这里全部用合成数据与中性类名。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.plan_writer import _duplicate_created_test_target_errors  # noqa: E402


def _task(task_id: str, plans: list[dict] | None) -> dict:
    task = {"id": task_id}
    if plans is not None:
        task["validationTestPlan"] = plans
    return task


def _plan(cwd: str, targets: list[dict], repo: str = "") -> dict:
    plan = {"framework": "maven", "cwd": cwd, "targets": targets}
    if repo:
        plan["repo"] = repo
    return plan


def _target(selector: str, mode: str) -> dict:
    return {"selector": selector, "mode": mode, "sourceFiles": []}


class TestDuplicateCreatedTestTarget(unittest.TestCase):
    def test_two_tasks_creating_same_class_is_rejected(self):
        data = {"tasks": [
            _task("T003", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T004", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
        ]}
        errors = _duplicate_created_test_target_errors(data)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["reason"], "duplicate_created_test_target")
        self.assertIn("AlphaTest", errors[0]["detail"])
        self.assertIn("T003,T004", errors[0]["detail"])

    def test_create_plus_reuse_is_allowed(self):
        """一个 TASK 建、后续 TASK 复用，是合法形态，不得拦。"""
        data = {"tasks": [
            _task("T003", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T004", [_plan("svc", [_target("AlphaTest", "reuse_existing")])]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_all_reuse_is_allowed(self):
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest", "reuse_existing")])]),
            _task("T002", [_plan("svc", [_target("AlphaTest", "reuse_existing")])]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_method_selectors_on_same_file_collide(self):
        """AlphaTest#a 与 AlphaTest#b 是同一个源文件。"""
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest#a", "create_in_code")])]),
            _task("T002", [_plan("svc", [_target("AlphaTest#b", "create_in_code")])]),
        ]}
        errors = _duplicate_created_test_target_errors(data)
        self.assertEqual(len(errors), 1)
        self.assertIn("AlphaTest", errors[0]["detail"])

    def test_implicit_and_explicit_default_repository_collide(self):
        """省略 repo 与显式 default 是同一个工作区。"""
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T002", [_plan("svc", [_target("AlphaTest", "create_in_code")], repo="default")]),
        ]}
        errors = _duplicate_created_test_target_errors(data)
        self.assertEqual(len(errors), 1)
        self.assertIn("at=default:svc", errors[0]["detail"])

    def test_equivalent_cwd_spellings_collide(self):
        """相同模块的路径拼写差异不能绕过待创建测试目标校验。"""
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T002", [_plan("./svc/", [_target("AlphaTest", "create_in_code")])]),
        ]}
        self.assertEqual(len(_duplicate_created_test_target_errors(data)), 1)

    def test_simple_and_fully_qualified_selector_collide(self):
        """简单类名在创建前没有包路径，不能与同名 FQCN 并存。"""
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T002", [_plan("svc", [_target("com.example.AlphaTest", "create_in_code")])]),
        ]}
        errors = _duplicate_created_test_target_errors(data)
        self.assertEqual(len(errors), 1)
        self.assertIn("T001,T002", errors[0]["detail"])

    def test_different_fully_qualified_classes_with_same_simple_name_are_allowed(self):
        """两个已知的不同包路径对应不同测试源文件。"""
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("com.alpha.AlphaTest", "create_in_code")])]),
            _task("T002", [_plan("svc", [_target("com.beta.AlphaTest", "create_in_code")])]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_same_class_name_in_different_workspace_is_allowed(self):
        """不同仓库/模块下的同名测试类是不同文件。"""
        data = {"tasks": [
            _task("T001", [_plan("mod-a", [_target("AlphaTest", "create_in_code")], repo="repoA")]),
            _task("T002", [_plan("mod-b", [_target("AlphaTest", "create_in_code")], repo="repoB")]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_same_task_repeating_target_is_not_a_conflict(self):
        """同一 TASK 内多条命令指向同一类，不算跨 TASK 冲突。"""
        data = {"tasks": [
            _task("T001", [
                _plan("svc", [_target("AlphaTest#a", "create_in_code")]),
                _plan("svc", [_target("AlphaTest#b", "create_in_code")]),
            ]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_distinct_classes_are_allowed(self):
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T002", [_plan("svc", [_target("BetaTest", "create_in_code")])]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_three_way_collision_reports_all_owners(self):
        data = {"tasks": [
            _task("T001", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T002", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
            _task("T003", [_plan("svc", [_target("AlphaTest", "create_in_code")])]),
        ]}
        errors = _duplicate_created_test_target_errors(data)
        self.assertEqual(len(errors), 1)
        self.assertIn("T001,T002,T003", errors[0]["detail"])

    def test_missing_validation_test_plan_is_tolerated(self):
        """外部依赖 TASK 与旧 Draft 没有该字段，不得报错。"""
        data = {"tasks": [_task("T001", None), _task("T002", [])]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_malformed_entries_are_skipped(self):
        data = {"tasks": [
            _task("T001", ["not-a-dict"]),
            _task("T002", [_plan("svc", ["not-a-dict", {"mode": "create_in_code"}])]),
        ]}
        self.assertEqual(_duplicate_created_test_target_errors(data), [])

    def test_empty_task_set(self):
        self.assertEqual(_duplicate_created_test_target_errors({"tasks": []}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
