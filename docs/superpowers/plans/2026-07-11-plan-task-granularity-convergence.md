# Plan Task Granularity Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent repeated Plan-stage repartitioning while continuing to reject capability-sized tasks that combine independent user actions or verification boundaries.

**Architecture:** Keep the normal five-scenario target and current API/Page/UIX hard limits. Replace the unconditional eight-scenario rejection with a bounded twelve-scenario matrix exception, audited by a structured `mergedScenarioRefs` list and one complete non-compile validation command. Semantic grouping remains a Plan-stage responsibility; the writer validates only observable structural signals.

**Tech Stack:** Python standard library, JSON, argparse, unittest, Markdown skill contracts.

---

## Final Rules

| Rule | Outcome |
|---|---|
| `SCN <= 5` and existing dimension limits | Normal task; no merge metadata required. |
| `6 <= SCN <= 12` | Requires matrix exception metadata. |
| `SCN > 12` | `oversized_plan_task_must_split`; no exception. |
| `apiIds > 3`, `pageRefs > 2`, or `interactionRefs > 4` | Preserve current hard rejection. |
| More than one independent action, public seam, or verification boundary | Plan stage must split before calling the writer. |

The matrix exception is valid only when all scenarios are represented by fully-qualified, individually expanded `mergedScenarioRefs`; one required non-`compile` command covers every acceptance criterion; and `splitRationale` explains the shared request/response or state/permission matrix. `mergedScenarioRefs` is an audit field, not an additional scenario source: its normalized set must equal the task's scenario refs.

### Task 1: Add Focused Granularity Tests

**Files:**
- Create: `tests/test_plan_granularity.py`
- Modify: `tests/test_json_writers.py`

- [x] **Step 1: Write direct validator tests before changing limits**

```python
def test_matrix_exception_requires_complete_structured_scenario_refs() -> None:
    task = valid_task_with_scenarios(9)
    task["splitRationale"] = "同一查询请求和同一个响应断言组成共享同一验证闭环。"
    task["validationCommands"][0]["covers"] = all_acceptance_ids(task)

    assert reason(task) == "missing_plan_task_merged_scenario_refs"

    task["mergedScenarioRefs"] = scenario_refs(task)

    assert validate_plan_task_granularity_item(task, task_id="T001") == []
```

- [x] **Step 2: Add rejection coverage for every boundary**

Add cases for 13 scenarios with complete metadata (`oversized_plan_task_must_split`), incomplete or non-path-qualified `mergedScenarioRefs`, multiple required behavior commands, a behavior command that does not cover all ACs, and `SCN-001~SCN-009` shorthand in `specRefs`.

- [x] **Step 3: Add writer integration coverage**

In `tests/test_json_writers.py`, submit a body-file task with nine expanded scenario refs. Assert that the writer rejects missing exception metadata, accepts the complete exception, and rejects a range shorthand before writing any plan output.

- [x] **Step 4: Run tests to verify the new assertions fail**

Run: `python -m unittest tests.test_plan_granularity tests.test_json_writers.JsonWriterTests`

Expected: failure because the current writer hard-rejects more than eight scenarios and does not recognize `mergedScenarioRefs` or range shorthand.

### Task 2: Implement Bounded Matrix Exception

**Files:**
- Modify: `hooks/plan_granularity.py`
- Modify: `hooks/plan_json.py`
- Modify: `hooks/plan_writer.py`

- [x] **Step 1: Add explicit constants and structured-reference helpers**

Replace `PLAN_TASK_HARD_MAX_SCENARIOS = 8` with `PLAN_TASK_MATRIX_MAX_SCENARIOS = 12`; keep `PLAN_TASK_MAX_SCENARIOS = 5`, `PLAN_TASK_HARD_MAX_APIS = 3`, `PLAN_TASK_HARD_MAX_UI_PAGES = 2`, and `PLAN_TASK_HARD_MAX_UI_INTERACTIONS = 4` unchanged.

Add helpers that reject scenario range or concatenation shorthand, normalize only `specs/...#SCN-xxx` references, and compare the normalized `mergedScenarioRefs` set to the scenario refs extracted from `specRefs`.

- [x] **Step 2: Validate exception metadata only in the soft-to-matrix range**

For six through twelve scenarios, require:

```python
merged_refs = normalized_merged_scenario_refs(task)
if merged_refs != scenario_refs:
    return failure("invalid_plan_task_merged_scenario_refs")
if not exactly_one_complete_required_behavior_command(task):
    return failure("invalid_plan_task_matrix_validation")
if not valid_split_rationale(task, related_ids_without_scenarios):
    return failure("invalid_plan_task_split_rationale")
```

`exactly_one_complete_required_behavior_command` selects required commands with `kind != "compile"` and requires exactly one whose `covers` set equals all acceptance criterion IDs. Do not infer user actions, request semantics, or field matrices from prose.

- [x] **Step 3: Preserve legacy behavior below and above the exception range**

Tasks with at most five scenarios continue without merge metadata. Tasks with more than twelve scenarios return `oversized_plan_task_must_split`, even if metadata is complete. Preserve the existing API/Page/UIX hard errors and their reason format.

- [x] **Step 4: Expose the conditional input contract**

Extend `add-task-contract` with:

```json
"conditionalFields": {
  "mergedScenarioRefs": {
    "when": "scenario_refs_count_is_6_to_12",
    "requiredFields": [],
    "mustEqual": "fully_qualified_scenario_refs_from_specRefs"
  }
}
```

Add a sibling `matrixException` object documenting the five-scenario target, twelve-scenario maximum, and one complete required non-compile validation command. Keep `task-input.json` unchanged because its normal example has two scenarios.

- [x] **Step 5: Run focused tests to verify green**

Run: `python -m unittest tests.test_plan_granularity tests.test_json_writers.JsonWriterTests`

Expected: all tests pass; a nine-scenario matrix task is accepted only with exact metadata, while a thirteen-scenario task is rejected.

### Task 3: Make Plan Grouping Converge

**Files:**
- Modify: `skills/autodev/autodev-plan/SKILL.md`
- Modify: `tests/test_board_config_invariants.py`

- [x] **Step 1: Replace the unconditional eight-scenario hard-limit text**

Document the final rule table. State that a six-to-twelve scenario exception requires one public seam, one complete required behavior command, complete `mergedScenarioRefs`, and a non-generic rationale. Retain the existing API/Page/UIX limits.

- [x] **Step 2: Add the convergence guard after the coverage matrix**

Add these mandatory instructions:

1. Group the matrix by `user action + public seam + automated verification boundary` before assigning task IDs.
2. Split an invalid group once along one of those three axes.
3. If the result remains one seam and one validation boundary, use the matrix exception when within twelve scenarios.
4. If it exceeds twelve scenarios or contains multiple boundaries, stop and report a specification/planning conflict; do not emit `v2`, `v3`, or temporary IDs such as `T012a`.

- [x] **Step 3: Require expanded references in the final table**

Require one full `specs/...#SCN-xxx` entry per scenario both in `specRefs` and `mergedScenarioRefs`; explicitly prohibit ranges and concatenated anchors.

- [x] **Step 4: Update invariant tests**

Assert the new threshold, `mergedScenarioRefs`, the convergence guard phrases, and prohibition of range shorthand. Remove assertions that require the obsolete hard-eight wording.

- [x] **Step 5: Run Plan skill tests**

Run: `python -m unittest tests.test_board_config_invariants`

Expected: PASS.

### Task 4: Run Regression Verification and Document Migration

**Files:**
- Modify: `docs/superpowers/specs/2026-07-10-plan-writer-task-input-contract-design.md`
- Modify: `docs/superpowers/plans/2026-07-11-plan-task-granularity-convergence.md`

- [x] **Step 1: Document the breaking plan-artifact migration policy**

Existing plans with more than five scenario refs must be regenerated through Plan before their next stage-gate validation. No compatibility mode is provided; regenerated tasks with six through twelve fully-expanded scenario refs must include `mergedScenarioRefs`.

- [x] **Step 2: Run the complete relevant suite**

Run: `python -m unittest tests.test_plan_granularity tests.test_json_writers tests.test_board_config_invariants tests.test_batched_plan tests.test_plan_json_and_evidence`

Expected: PASS.

- [x] **Step 3: Inspect final scope**

Run: `git diff --check`

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add hooks/plan_granularity.py hooks/plan_json.py hooks/plan_writer.py \
  skills/autodev/autodev-plan/SKILL.md tests/test_plan_granularity.py \
  tests/test_json_writers.py tests/test_board_config_invariants.py \
  docs/superpowers/specs/2026-07-10-plan-writer-task-input-contract-design.md \
  docs/superpowers/plans/2026-07-11-plan-task-granularity-convergence.md
git commit -m "feat: bound plan task matrix exceptions"
```
