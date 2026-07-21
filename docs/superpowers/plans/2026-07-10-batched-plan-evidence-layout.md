# Batched Plan And Evidence Layout Implementation Plan

> **For agentic workers:** execute test-first and stop at each batch handoff defined by the workflow itself.

**Goal:** Split feature plans into spec-derived batches of at most five tasks and make JSONL plus command logs the non-duplicated evidence artifact model.

**Architecture:** `plan_json.py` validates a root batch index and its batch plans as one bundle. `plan_writer.py` is the only multi-file plan mutator. `task_runner.py` enforces the active-batch boundary and creates handoffs. `evidence_store.py` appends artifact-version-2 JSONL records and logs without sidecars. Existing gates read the complete bundle, while Code context reads only the active batch.

**Tech Stack:** Python 3 standard library, Git CLI, unittest, existing AutoBizDevOps hooks.

### Task 1: Contract tests

**Files:**
- Create: `tests/test_batched_plan.py`
- Modify: `tests/test_plan_json_and_evidence.py`
- Modify: `tests/test_task_runner.py`

- [ ] Assert root plans cannot contain tasks and batch plans cannot exceed five tasks.
- [ ] Assert global dependency/order validation and root/batch status projections.
- [ ] Assert non-final completion creates a handoff and blocks same-conversation continuation.
- [ ] Assert new evidence creates JSONL/index/log but no sidecar, and `show` retrieves it.

### Task 2: Plan bundle and writer

**Files:**
- Modify: `hooks/plan_json.py`
- Modify: `hooks/plan_writer.py`
- Modify: `skills/autodev/autodev-plan/templates/plan.json`

- [ ] Implement root/batch schemas, bundle loading, global task lookup, topology checks, and max-five enforcement.
- [ ] Add batch creation and task assignment commands.
- [ ] Project task status into batch and root status under one plan lock.
- [ ] Reject monolithic plans with a rebuild error.

### Task 3: Runner and context boundary

**Files:**
- Modify: `hooks/task_runner.py`
- Modify: `hooks/code_task_context.py`
- Modify: `hooks/result_writer_common.py`

- [ ] Restrict task lifecycle operations to the active batch.
- [ ] Add `activate-batch` and persisted handoff generation/consumption.
- [ ] Return an explicit new-conversation stop instruction after non-final completion.
- [ ] Load only root summary plus the active batch in Code task context.

### Task 4: Evidence layout and gates

**Files:**
- Modify: `hooks/evidence_kernel.py`
- Modify: `hooks/evidence_store.py`
- Modify: `hooks/evidence_integrity_gate.py`
- Modify: `hooks/evidence_audit.py`
- Modify: `skills/autodev/hooks/artifact_check.py`

- [ ] Add artifact version 2 with no sidecar generation and retained log integrity checks.
- [ ] Add `evidence_store.py show --evidence-id`.
- [ ] Keep historical artifact-version-1 sidecar checks read-only.
- [ ] Gate code_done on all batches, all task evidence, project checks, and no unresolved handoff.

### Task 5: Workflow contracts and verification

**Files:**
- Modify: `skills/autodev/autodev-plan/SKILL.md`
- Modify: `skills/autodev/autodev-code/SKILL.md`
- Modify: `board_core/board_config.json`
- Modify: relevant tests and workflow documentation

- [ ] Document spec-capability/topological batching and the five-task limit.
- [ ] Document mandatory conversation handoff and activation command.
- [ ] Register batch plans, handoff, JSONL/index/logs as feature artifacts.
- [ ] Run focused tests, full unittest discovery, and artifact-layout audit.
