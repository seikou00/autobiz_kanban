#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse(task) hook: constrain post-implementation UTest assignments."""

from __future__ import print_function

import json
import sys


PREFIX = "[utest-task-prompt-augment]"
TARGET_SUBAGENT_TYPE = "test-engineer-autodev"
APPEND_MARKER = "autodev-utest-assignment"
APPEND_INSTRUCTION = """

<{marker}>

### Autodev UTest assignment contract

- This is post-implementation testing: `post_implementation=true`, `tdd_rebuild=false`. Preserve existing production implementation; do not apply the role's TDD deletion rule.
- Read `<AGENTS_INSTRUCTIONS>`, then resolve the assignment's `<SCOPE>`, `<SYSTEM>`, and `<UNIT>` references through tools before editing. `<SCOPE>` owns the deploy-unit and repository boundary; system/unit documents own framework constraints.
- Treat `executionLane` and `workspaceRef` from the Batch/task assignment as authoritative. Work only in the assigned workspace and do not combine backend and frontend lanes.
- Test content and boundary come from the plan fields carried in the assignment: assert `testIntent.behavior` plus the `acceptanceCriteria` named in `covers`, stay inside `validationBoundary`, and run the `validationCommands` entry each `commandId` points at. Read a spec or design anchor only when those fields leave the assertion undetermined; do not re-derive targets from specs and do not substitute self-authored commands. A missing or dangling `validationTestPlan` entry is a blocking `contract_gap`.
- Resolve `framework` only from actually opened `<SYSTEM>`/`<UNIT>` documents. Resolve `runner` and package manager only from real manifests, lock files, and test config. A conflict is a blocking `contract_gap`; when the constraint is absent, fall back to repository facts and add a warning.
- You may edit only tests, fixtures, mocks, test helpers, test-environment configuration, dependency manifests, and the matching lock file. Do not edit production source.
- Route pure functions to `unit`, hooks/composables to `logic`, stores to `state`, components to `component`, and router/page/API adapters to `integration`. Real-browser, multi-page, or real-network coverage is an E2E handoff, not an implementation target.
- Classify every unresolved failure as exactly one of `test_bug`, `source_bug`, `contract_gap`, `environment`, `flaky`, or `unknown`. Repair `test_bug` within the write boundary. Return a `source_fix_request` for `source_bug`; do not patch production source.
- Run tests and retain fresh command results. Existing Jest/Vitest runners must be reused. Missing supported test environments may be initialized only after the environment profile is reported by the inspector.

Return exactly these top-level fields in the final structured result: `status`, `assignment`, `constraint_files`, `lane`, `framework`, `runner`, `environment_initialization`, `test_targets`, `command_results`, `evidence_ids`, `failure_classification`, `source_fix_request`, `e2e_handoff`, `warnings`.

</{marker}>""".format(marker=APPEND_MARKER)


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


def build_updated_input(payload, stream):
    tool_input = get_tool_input(payload)
    if not is_target_subagent(tool_input):
        print(
            "{} skipped; subagent_type must be {}".format(
                PREFIX, TARGET_SUBAGENT_TYPE
            ),
            file=stream,
        )
        return None

    description = tool_input.get("description")
    if not isinstance(description, str) or not description.strip():
        print(
            "{} description missing; pass the UTest assignment in "
            "tool_input.description".format(PREFIX),
            file=stream,
        )
        return None

    if APPEND_MARKER in description:
        print(
            "{} skipped; UTest assignment contract already present".format(PREFIX),
            file=stream,
        )
        return None

    updated_description = description + APPEND_INSTRUCTION
    print(
        "{} appended {} to {} description".format(
            PREFIX, APPEND_MARKER, TARGET_SUBAGENT_TYPE
        ),
        file=stream,
    )
    return {"updatedInput": {"description": updated_description}}


def emit_updated_input(payload, output_stream, error_stream):
    result = build_updated_input(payload, error_stream)
    if result is None:
        return False
    json.dump(result, output_stream, ensure_ascii=False, separators=(",", ":"))
    output_stream.write("\n")
    return True


def main():
    raw_input = read_stdin_text()
    if not raw_input.strip():
        print(
            "{} empty stdin; verify CMBDevClaw passes the PreToolUse task "
            "payload to command hooks".format(PREFIX),
            file=sys.stderr,
        )
        return 0

    try:
        payload = json.loads(raw_input)
    except (TypeError, ValueError) as error:
        print(
            "{} invalid JSON: {}. Repair: capture the raw PreToolUse payload "
            "and update the parser".format(PREFIX, error),
            file=sys.stderr,
        )
        return 0

    if not isinstance(payload, dict):
        print(
            "{} payload is not an object. Repair: pass one JSON object from "
            "PreToolUse".format(PREFIX),
            file=sys.stderr,
        )
        return 0

    emit_updated_input(payload, sys.stdout, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
