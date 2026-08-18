#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屏蔽 plan.json digest 校验的脚本

用法:
    python scripts/disable_digest_check.py          # 屏蔽校验
    python scripts/disable_digest_check.py restore  # 恢复校验
"""

import sys
from pathlib import Path

PLAN_JSON_FILE = Path(__file__).parent.parent / "hooks" / "plan_json.py"
BACKUP_FILE = PLAN_JSON_FILE.with_suffix(".py.digest_backup")

# 需要注释掉的代码行（1906-1907行）
ORIGINAL_LINES = [
    "    if root.get(\"taskSetDigest\") is not None and root.get(\"taskSetDigest\") != task_set_digest(root, batch_data):\n",
    "        errors.append(\"task_set_digest_mismatch\")\n",
]

DISABLED_LINES = [
    "    # DISABLED: digest check\n",
    "    # if root.get(\"taskSetDigest\") is not None and root.get(\"taskSetDigest\") != task_set_digest(root, batch_data):\n",
    "    #     errors.append(\"task_set_digest_mismatch\")\n",
]


def disable_digest_check():
    """屏蔽 digest 校验"""
    if not PLAN_JSON_FILE.exists():
        print(f"错误：找不到文件 {PLAN_JSON_FILE}", file=sys.stderr)
        return 1

    content = PLAN_JSON_FILE.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    # 创建备份
    if not BACKUP_FILE.exists():
        BACKUP_FILE.write_text(content, encoding="utf-8")
        print(f"✓ 已创建备份: {BACKUP_FILE}")

    # 查找并替换目标行
    modified = False
    i = 0
    while i < len(lines):
        # 检查是否匹配原始代码
        if (i + 1 < len(lines) and
            lines[i].strip() and
            "taskSetDigest" in lines[i] and
            "task_set_digest_mismatch" in lines[i + 1]):

            # 替换为注释掉的版本
            indent = len(lines[i]) - len(lines[i].lstrip())
            lines[i] = " " * indent + "# DISABLED: digest check\n"
            lines[i + 1] = " " * indent + "# " + lines[i + 1].lstrip()
            if "errors.append" in lines[i + 1]:
                indent_inner = len(lines[i + 2]) - len(lines[i + 2].lstrip()) if i + 2 < len(lines) else indent + 4
                lines.insert(i + 1, " " * indent + "# if root.get(\"taskSetDigest\") is not None and root.get(\"taskSetDigest\") != task_set_digest(root, batch_data):\n")
            modified = True
            print(f"✓ 已在第 {i + 1} 行屏蔽 digest 校验")
            break
        i += 1

    if not modified:
        # 尝试查找已经被注释的行
        for i, line in enumerate(lines):
            if "DISABLED: digest check" in line:
                print(f"⚠ digest 校验已经被屏蔽（第 {i + 1} 行）")
                return 0

        print("错误：未找到 digest 校验代码", file=sys.stderr)
        return 1

    # 写回文件
    PLAN_JSON_FILE.write_text("".join(lines), encoding="utf-8")
    print(f"✓ 已更新文件: {PLAN_JSON_FILE}")
    print("\n✅ digest 校验已成功屏蔽")
    return 0


def restore_digest_check():
    """恢复 digest 校验"""
    if not BACKUP_FILE.exists():
        print("错误：找不到备份文件", file=sys.stderr)
        return 1

    backup_content = BACKUP_FILE.read_text(encoding="utf-8")
    PLAN_JSON_FILE.write_text(backup_content, encoding="utf-8")
    BACKUP_FILE.unlink()

    print(f"✓ 已从备份恢复: {PLAN_JSON_FILE}")
    print("✅ digest 校验已恢复")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        return restore_digest_check()
    else:
        return disable_digest_check()


if __name__ == "__main__":
    sys.exit(main())
