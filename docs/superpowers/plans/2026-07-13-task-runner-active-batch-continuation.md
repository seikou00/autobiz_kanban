# Task Runner Active Batch Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make successful task completion explicitly direct the Code agent to the next runnable task in the same batch.

**Architecture:** Extend `record_task_attempt` to project an additive continuation payload when its batch remains incomplete, then expose that payload from the `task_runner complete` CLI. Keep cross-batch handoff behavior unchanged and make the Code Skill branch on the new action.

**Tech Stack:** Python 3 standard library, `unittest`, JSON CLI contracts, Markdown Skill instructions.

---

### Task 1: Specify same-batch continuation at the CLI boundary

**Files:**
- Modify: `tests/test_batched_plan.py`

- [x] **Step 1: Write the failing integration test**

Create one B001 containing T001, T002, and T003. Start and complete T001 against a temporary Git repository, then assert:

```python
self.assertEqual(payload["requiredAction"], "continue_active_batch")
self.assertTrue(payload["continueCurrentBatch"])
self.assertEqual(payload["activeBatchId"], "B001")
self.assertEqual(payload["nextTaskId"], "T002")
self.assertFalse(payload["stopAfterBatch"])
self.assertIsNone(payload["batchHandoff"])
```

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_batched_plan.BatchRunnerContractTest.test_incomplete_batch_returns_next_runnable_task
```

Expected: failure because `requiredAction` is currently `None` and continuation fields are absent.

### Task 2: Implement the continuation response

**Files:**
- Modify: `hooks/plan_writer.py`
- Modify: `hooks/task_runner.py`

- [x] **Step 1: Compute the next runnable task**

After recording a successful attempt for an incomplete batch, scan that batch in plan order. Select the first non-done task whose `deps` all reference done tasks. Add this payload to the writer result:

```python
continuation = {
    "requiredAction": "continue_active_batch",
    "continueCurrentBatch": True,
    "activeBatchId": batch_id,
    "nextTaskId": next_task_id,
}
```

- [x] **Step 2: Expose the payload from `complete`**

Return the continuation fields from `_cmd_complete`, while preserving handoff precedence and returning false/null defaults when no continuation exists.

- [x] **Step 3: Run the focused integration test and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_batched_plan.BatchRunnerContractTest.test_incomplete_batch_returns_next_runnable_task
```

Expected: `OK`.

### Task 3: Harden the Agent protocol and regressions

**Files:**
- Modify: `skills/autodev/autodev-code/SKILL.md`
- Modify: `tests/test_board_config_invariants.py`

- [x] **Step 1: Add the Skill invariant test**

Require the Skill to contain `continue_active_batch`, `nextTaskId`, and an explicit prohibition on asking whether to continue while the active batch has a runnable task.

- [x] **Step 2: Verify the invariant test fails before the Skill edit**

Run:

```bash
python3 -m unittest tests.test_board_config_invariants.BoardConfigInvariantTest
```

Expected: failure identifying the missing continuation wording.

- [x] **Step 3: Update the Code Skill**

Document the mandatory same-conversation branch immediately after the `complete` command contract and before the cross-batch stop branch.

- [x] **Step 4: Run relevant regression tests**

Run:

```bash
python3 -m unittest tests.test_batched_plan tests.test_board_config_invariants
```

Expected: all tests pass with no failures or errors.

- [x] **Step 5: Inspect the final diff**

Run:

```bash
git diff --check
git diff -- hooks/plan_writer.py hooks/task_runner.py skills/autodev/autodev-code/SKILL.md tests/test_batched_plan.py tests/test_board_config_invariants.py
```

Expected: no whitespace errors and only the active-batch continuation contract changes.
