#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速验证当前 Code 阶段的 deferred-validation 策略。"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hooks.plan_json import defer_to_test_stages_enabled


def test_modified_logic():
    """只允许 Review/UTest 接管的当前策略。"""

    current = {
        "taskValidationPolicy": {
            "mode": "defer_to_test_stages",
            "orchestration": "inline",
            "codeGate": "review_only",
        }
    }
    retired = {
        "taskValidationPolicy": {
            "mode": "defer_to_test_stages",
            "orchestration": "inline",
            "codeGate": "batch_compile_only",
        }
    }
    assert defer_to_test_stages_enabled(current)
    assert not defer_to_test_stages_enabled(retired)
    print("✅ 仅 review_only Plan 可进入 Code 阶段")
    return True


def main():
    print("=" * 60)
    print("验证 Code 阶段 Plan 策略")
    print("=" * 60)
    print()

    if test_modified_logic():
        print()
        print("=" * 60)
        print("✅ 所有验证通过！")
        print("=" * 60)
        return 0
    else:
        print()
        print("=" * 60)
        print("❌ 验证失败")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
