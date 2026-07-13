# Task Runner Active Batch Continuation Design

## Problem

`task_runner complete` only returns a `requiredAction` when completing a batch creates a cross-conversation handoff. When a task succeeds but other runnable tasks remain in the active batch, the response contains `requiredAction: null`. The Code agent can mistake that empty action for a turn boundary and ask whether it should continue.

## Decision

Every successful task completion must return an explicit next action.

- If the active batch still has unfinished tasks, return `requiredAction: continue_active_batch`, `continueCurrentBatch: true`, the current `activeBatchId`, and the first dependency-ready `nextTaskId` in batch order.
- If a non-final batch completes, preserve `requiredAction: stop_and_open_new_conversation` and the existing `batchHandoff` contract.
- If the final batch completes, do not invent another task. Return no continuation task; the next Code session remains responsible for entering project-check through `code-session`.
- Failed validation continues to return `validation_failed` and must not advertise another task.

The runner is the source of truth for the next task. It selects only an unfinished task whose dependencies are already `done`; it does not skip a blocked earlier task to violate plan order.

## Agent Contract

The Code Skill must treat `continue_active_batch` as mandatory continuation. It updates the current task to done, marks `nextTaskId` in progress, and immediately runs the next task protocol without asking the user for confirmation. Phrases such as “需要我继续吗” are forbidden while the runner reports a runnable task in the active batch.

## Compatibility

Existing handoff fields retain their meaning. New response fields are additive, so callers that only understand `stopAfterBatch` remain compatible. `continueCurrentBatch` is always false when a handoff exists or validation fails.

## Verification

An integration test will create one batch containing T001, T002, and T003. Completing T001 must return `continue_active_batch` with T002. Completing T002 must return the same action with T003. Existing cross-batch handoff tests must remain green.
