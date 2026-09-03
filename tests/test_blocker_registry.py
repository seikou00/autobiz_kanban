#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阻断规则治理：没有「下游失败 + 回归测试」就不能当 blocker。"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks import blocker_registry  # noqa: E402


# 登记范围：任务粒度门禁。其余模块的规则逐批纳入。
GOVERNED_MODULES = ("hooks/plan_granularity.py",)


def _rule_severity_by_reason(path: Path) -> dict:
    """收集模块里每条 reason 字面量及其 severity（缺省为 blocker）。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    severities: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        entries = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    entries[key.value] = value.value
        reason = entries.get("reason")
        if reason:
            severities[reason] = entries.get("severity", "blocker")
    return severities


class BlockerRegistryTest(unittest.TestCase):
    def test_every_blocking_rule_is_registered(self) -> None:
        unregistered = []
        for module in GOVERNED_MODULES:
            for reason, severity in _rule_severity_by_reason(ROOT / module).items():
                if severity == "warning":
                    continue
                if reason not in blocker_registry.registered_reasons():
                    unregistered.append(f"{module}:{reason}")

        self.assertEqual(
            unregistered,
            [],
            "新增阻断规则必须在 hooks/blocker_registry.py 登记下游失败与回归测试，"
            "举不出下游失败的登记到 UNPROVEN：" + ", ".join(unregistered),
        )

    def test_registered_rules_are_not_silently_downgraded(self) -> None:
        """登记为 blocker 的规则若改成 warning，必须同时移出登记表。"""

        declared: dict = {}
        for module in GOVERNED_MODULES:
            declared.update(_rule_severity_by_reason(ROOT / module))

        downgraded = [
            reason
            for reason in blocker_registry.registered_reasons()
            if declared.get(reason) == "warning"
        ]
        self.assertEqual(downgraded, [], "已降级为 warning 的规则应移出登记表：" + ", ".join(downgraded))

    def test_every_cited_test_exists(self) -> None:
        cited = [
            (reason, entry.regression_test)
            for reason, entry in blocker_registry.BLOCKERS.items()
        ] + [
            (reason, entry.evidence)
            for reason, entry in blocker_registry.UNPROVEN.items()
        ]

        missing = []
        for reason, node in cited:
            path, _, selector = node.partition("::")
            test_name = selector.rsplit("::", 1)[-1]
            source = ROOT / path
            if not source.is_file():
                missing.append(f"{reason}: 文件不存在 {path}")
                continue
            if not re.search(rf"def {re.escape(test_name)}\(", source.read_text(encoding="utf-8")):
                missing.append(f"{reason}: {path} 中没有 {test_name}")

        self.assertEqual(missing, [], "登记表引用的回归测试必须存在：" + "; ".join(missing))

    def test_blockers_and_unproven_do_not_overlap(self) -> None:
        overlap = sorted(set(blocker_registry.BLOCKERS) & set(blocker_registry.UNPROVEN))
        self.assertEqual(overlap, [])

    def test_every_entry_answers_all_four_questions(self) -> None:
        for reason, entry in blocker_registry.BLOCKERS.items():
            with self.subTest(reason=reason):
                self.assertTrue(entry.module.strip(), reason)
                self.assertTrue(entry.downstream_failure.strip(), reason)
                self.assertTrue(entry.regression_test.strip(), reason)
        for reason, entry in blocker_registry.UNPROVEN.items():
            with self.subTest(reason=reason):
                self.assertTrue(entry.module.strip(), reason)
                self.assertTrue(entry.kept_because.strip(), reason)
                self.assertTrue(entry.evidence.strip(), reason)


if __name__ == "__main__":
    unittest.main()
