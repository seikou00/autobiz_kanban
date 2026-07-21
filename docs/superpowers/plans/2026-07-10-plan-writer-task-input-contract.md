# Plan Writer Single Task Input Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three Plan JSON templates with one writer-compatible task input template.

**Architecture:** Keep the persisted root/batch plan split unchanged and treat both as writer-owned output. Expose the remaining input, UI condition, and project-validation shape through `add-task-contract`.

**Tech Stack:** Python standard library, argparse, JSON, unittest, Markdown skill contracts.

---

### Task 1: Write Contract Regression Tests

**Files:**
- Modify: `tests/test_json_writers.py`
- Modify: `tests/test_board_config_invariants.py`

- [x] **Step 1: Assert a single template contract**

Require `task-input.json`, UI conditional fields, project validation command fields, generated artifact ownership, and the absence of writer-owned input fields.

- [x] **Step 2: Run the focused tests and observe the missing-template and old-contract failures**

Run: `python -m unittest tests.test_json_writers.JsonWriterTests.test_plan_writer_add_task_contract_is_machine_readable_without_workspace tests.test_json_writers.JsonWriterTests.test_plan_writer_task_template_supports_chinese_body_file_and_creates_first_batch`

Expected: failures because `task-input.json` and the expanded contract do not yet exist.

### Task 2: Converge Templates and Writer Contract

**Files:**
- Create: `skills/autodev/autodev-plan/templates/task-input.json`
- Delete: `skills/autodev/autodev-plan/templates/task.json`
- Delete: `skills/autodev/autodev-plan/templates/plan.json`
- Delete: `skills/autodev/autodev-plan/templates/batch-plan.json`
- Modify: `hooks/plan_writer.py`

- [x] **Step 1: Replace the static templates with one input example**

Retain caller-provided task fields and omit `status`, `evidenceIds`, `completionEvidenceIds`, `latestPassEvidenceId`, and `completionPolicy`.

- [x] **Step 2: Extend `add-task-contract`**

Return the input example path, conditional `uiRefs` requirements, project validation command fields, and generated root/batch artifact ownership.

### Task 3: Align the Plan Skill

**Files:**
- Modify: `skills/autodev/autodev-plan/SKILL.md`

- [x] **Step 1: Replace three-template instructions with the single input-template workflow**

Declare root and batch plan JSON writer-owned, describe conditional `uiRefs`, and retain the validation and batch constraints.

### Task 4: Verify

**Files:**
- Test: `tests/test_json_writers.py`
- Test: `tests/test_board_config_invariants.py`

- [x] **Step 1: Run focused regression tests**

Run the writer and Plan skill invariant tests, then run the relevant batch and plan validation suites.

- [x] **Step 2: Inspect the final diff**

Run `git diff --check` and confirm no static root or batch JSON template remains.
