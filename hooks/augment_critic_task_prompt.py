#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse(task): give critic-autodev findings a stable audit envelope."""

from __future__ import print_function

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.specs_hook_context import feature_dir_from_env, is_specs_in_progress


TARGET = "critic-autodev"
MARKER = "<autodev-review-audit>"


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _tool_input(payload):
    return _as_dict(payload.get("tool_input") or payload.get("input"))


def _target(tool_input):
    value = tool_input.get("subagent_type")
    return isinstance(value, str) and value.strip().lower() == TARGET


def _run_id():
    return "RV-{}-{}".format(
        datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8]
    )


def build_updated_input(payload, run_id=None):
    tool_input = _tool_input(payload)
    if not _target(tool_input):
        return None
    description = tool_input.get("description")
    if not isinstance(description, str) or not description.strip() or MARKER in description:
        return None
    run_id = run_id or _run_id()
    appendix = """

{marker}
Review run ID: {run_id}

## 本阶段的审查边界

dev.specs 审的是行为契约本身，只判五项：需求覆盖是否完整、实现范围是否越界、操作分类（New/Modified/Removed）与代码事实是否一致、上游资料引用是否齐全、待确认项是否已消解。

不判定实现是否存在，也不比对当前代码状态。specs 描述的是「系统应该表现为什么行为」，不是「现在代码有什么」——「后端尚未实现该接口」「前端 API 无对应 Controller」「状态枚举与现有代码不一致」这类观察不是本阶段的 finding，除非它证伪的是 proposal 中 `**Existing:**` 的存量断言。同理，只对本阶段产物（proposal.md、specs/**/spec.md）提 finding，其他阶段的产物不在审查范围内。

## 结论输出

在原有审查输出末尾追加且只追加一个以下 fenced block。即使没有 finding 也输出空数组。每条 finding 的 id 使用 `{run_id}-F001`、`{run_id}-F002` 递增；severity 保留 Critical / Major / Minor 原词；evidence 写可定位的文件、ID 或原文。

```autodev-review-findings
{{"reviewRunId":"{run_id}","findings":[{{"id":"{run_id}-F001","severity":"Critical","claim":"...","evidence":"path:line"}}]}}
```
</autodev-review-audit>
""".format(marker=MARKER, run_id=run_id)
    return {"updatedInput": {"description": description + appendix}}


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    try:
        if not is_specs_in_progress(feature_dir_from_env()):
            return 0
    except ValueError:
        return 0
    result = build_updated_input(payload)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
