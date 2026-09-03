#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse(task) hook: give specs-gate-fixer-autodev its resolved gate command."""

from __future__ import print_function

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.specs_hook_context import feature_dir_from_env, is_specs_in_progress  # noqa: E402


PREFIX = "[specs-gate-fixer-augment]"
TARGET_SUBAGENT_TYPE = "specs-gate-fixer-autodev"
APPEND_MARKER = "specs-gate-fixer-context"
PHASES = ("structure", "final")
DEFAULT_PHASE = "final"

APPEND_TEMPLATE = """

<{marker}>
Phase: {phase}
Feature 目录: {feature_dir}

## 门禁命令

```bash
python "{gate}" validate --stage dev.specs --phase {phase} --feature "{feature}"
```

`python` 不可用时改用 `python3`。每轮都跑这条命令，不要换别的入口。

## 本轮可写文件

- {feature_dir}/proposal.md
- {feature_dir}/specs/<capability>/spec.md
- {feature_dir}/SPECS_REVIEW.md
</{marker}>"""


def read_stdin_text():
    raw = sys.stdin.buffer.read()
    if not raw:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode(sys.stdin.encoding or "utf-8", errors="replace")


def as_dict(value):
    return value if isinstance(value, dict) else {}


def get_tool_input(payload):
    return as_dict(payload.get("tool_input") or payload.get("input"))


def is_target_subagent(tool_input):
    subagent_type = tool_input.get("subagent_type")
    if not isinstance(subagent_type, str):
        return False
    return subagent_type.strip().lower() == TARGET_SUBAGENT_TYPE.lower()


def resolve_phase(description):
    """Pick the phase named in the dispatch prompt; final is the stricter default."""
    lowered = description.lower()
    hits = [phase for phase in PHASES if phase in lowered]
    if len(hits) == 1:
        return hits[0]
    return DEFAULT_PHASE


def build_updated_input(payload, stream, root=None, feature_dir=None):
    tool_input = get_tool_input(payload)
    if not is_target_subagent(tool_input):
        print(
            "{} skipped; subagent_type must be {}".format(PREFIX, TARGET_SUBAGENT_TYPE),
            file=stream,
        )
        return None

    description = tool_input.get("description")
    if not isinstance(description, str) or not description.strip():
        print(
            "{} description missing; pass the gate-fixer task prompt in "
            "tool_input.description".format(PREFIX),
            file=stream,
        )
        return None

    if APPEND_MARKER in description:
        print("{} skipped; gate context already present".format(PREFIX), file=stream)
        return None

    if feature_dir is None:
        try:
            feature_dir = feature_dir_from_env()
        except ValueError as error:
            print(
                "{} {}; 派发前先确保 PLUGIN_WORKSPACE / PROJECT_DIR / FEATURE_ID "
                "已注入会话环境，否则子代理拿不到门禁命令".format(PREFIX, error),
                file=stream,
            )
            return None

    feature_dir = Path(feature_dir)
    if not is_specs_in_progress(feature_dir):
        print(
            "{} skipped; checkpoint 不是 specs_in_progress，先跑 update_checkpoint.py "
            "--checkpoint specs_in_progress".format(PREFIX),
            file=stream,
        )
        return None

    gate = Path(root or ROOT) / "hooks" / "stage_gate.py"
    phase = resolve_phase(description)
    appendix = APPEND_TEMPLATE.format(
        marker=APPEND_MARKER,
        phase=phase,
        feature_dir=feature_dir,
        feature=feature_dir.name,
        gate=gate,
    )
    print(
        "{} appended {} phase={} to {} description".format(
            PREFIX, APPEND_MARKER, phase, TARGET_SUBAGENT_TYPE
        ),
        file=stream,
    )
    return {"updatedInput": {"description": description + appendix}}


def emit_updated_input(payload, output_stream, error_stream, root=None, feature_dir=None):
    result = build_updated_input(payload, error_stream, root=root, feature_dir=feature_dir)
    if result is None:
        return False
    json.dump(result, output_stream, ensure_ascii=False, separators=(",", ":"))
    output_stream.write("\n")
    return True


def main():
    raw_input = read_stdin_text()
    if not raw_input.strip():
        print(
            "{} empty stdin; verify CMBDevClaw passes the PreToolUse payload "
            "to command hooks".format(PREFIX),
            file=sys.stderr,
        )
        return 0

    try:
        payload = json.loads(raw_input)
    except (TypeError, ValueError) as error:
        print(
            "{} invalid JSON: {}; capture the raw PreToolUse payload and update "
            "the parser".format(PREFIX, error),
            file=sys.stderr,
        )
        return 0

    if not isinstance(payload, dict):
        print(
            "{} payload is not an object; capture the raw PreToolUse payload "
            "and update the parser".format(PREFIX),
            file=sys.stderr,
        )
        return 0

    emit_updated_input(payload, sys.stdout, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
