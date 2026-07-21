# Batch Validation Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve per-TASK behavior validation and append-only evidence while running batch quality commands only when they add coverage beyond current TASK lifecycle evidence.

**Architecture:** `plan_writer.py` owns lane validation profiles and projects an immutable effective command set into every batch. `task_runner.py` adds a resumable batch run beside existing task runs; a batch cannot hand off until its latest command pass is newer than current TASK completion evidence. Batch repair changes are separate evidence and selectively trigger normal TASK revalidation runs.

**Tech Stack:** Python 3.12, `unittest`, JSON artifacts, Git content snapshots, append-only JSONL evidence.

---

## File Map

- `hooks/plan_json.py`: schema constants, batch validation/profile validation, digest coverage, runtime-field exclusions.
- `hooks/plan_writer.py`: profile CLI, batch projection/state, task completion routing, batch attempt binding, revalidation requests.
- `hooks/task_runner.py`: batch run state, command execution/retry, remediation scope mapping, TASK revalidation metadata.
- `hooks/evidence_store.py`: allow and validate batch evidence records where necessary.
- `hooks/evidence_integrity_gate.py`: validate batch references, latest-pass ordering, optional project checks, superseding TASK evidence.
- `hooks/code_exploration.py`: trust audited batch remediation changes.
- `tests/test_batched_plan.py`: schema/projection/state tests.
- `tests/test_json_writers.py`: CLI contract/profile tests.
- `tests/test_task_runner.py`: end-to-end batch run and revalidation tests.
- `tests/test_evidence_audit.py`, `tests/test_code_exploration.py`: code-done and cache regressions.
- `skills/autodev/autodev-plan/SKILL.md`, `skills/autodev/autodev-code/SKILL.md`: agent execution contract.
- `docs/evidence-task-runner.md`, `AUTOBIZDEVOPS_新手使用说明.md`: user-facing workflow description.

### Task 1: Add lane validation profiles to the plan contract

**Files:**
- Modify: `hooks/plan_json.py`
- Modify: `hooks/plan_writer.py`
- Test: `tests/test_batched_plan.py`
- Test: `tests/test_json_writers.py`

- [ ] **Step 1: Write failing schema and CLI tests**

Add tests that materialize backend and frontend batches, add one profile command per used lane, and assert the projected batch contains a stable command ID:

```python
self.assertEqual(
    backend_batch["batchValidation"]["commands"][0]["id"],
    "BATCH-B001-VAL-001",
)
self.assertEqual(backend_batch["batchValidation"]["status"], "pending")
```

Add rejection tests for a TASK command with `kind="compile"`, a used lane with no required profile command under `--initial`, and duplicate project/profile command tuples.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_batched_plan tests.test_json_writers -v
```

Expected: FAIL because `batchValidationProfiles`, `batchValidation`, and `add-batch-validation-command` do not exist and TASK compile commands are still accepted.

- [ ] **Step 3: Implement schema constants and validation**

In `hooks/plan_json.py`, define the distinct command domains and runtime status values:

```python
TASK_VALIDATION_KINDS = {"behavior_test", "integration_test", "e2e_test", "static_check"}
BATCH_VALIDATION_KINDS = {"compile", "build", "typecheck", "lint"}
PROJECT_VALIDATION_KINDS = {"integration_test", "e2e_test", "static_check"}
BATCH_VALIDATION_STATUSES = {
    "pending", "running", "failed", "revalidation_required", "passed"
}
BATCH_VALIDATION_ID_RE = re.compile(r"^BATCH-B\d{3}-VAL-\d{3}$")
```

Validate `batchValidationProfiles` on the root, validate the projected `batchValidation` object on every batch, make `projectValidationCommands` optional, and reject task commands outside `TASK_VALIDATION_KINDS`. When `require_initial_status=True`, require a non-empty profile with at least one required command for every execution lane present in `batches`.

Include the effective `batchValidation.commands` in `task_set_digest`; exclude only mutable fields (`status`, evidence IDs, active run ID) from the digest.

- [ ] **Step 4: Implement writer projection and profile CLI**

Initialize the root with `batchValidationProfiles = {}`. Add `add-batch-validation-command` with `--lane`, `--command`, `--cwd`, `--repo`, `--kind`, and `--optional`. Store profile commands without batch-specific IDs, then project them with stable IDs:

```python
effective_commands = [
    {**command, "id": f"BATCH-{batch_id}-VAL-{index:03d}"}
    for index, command in enumerate(profile.get("commands", []), start=1)
]
```

Preserve prior runtime fields when `_project_batches` rewrites a batch, but replace the command snapshot from the current profile. Add the profile contract and command order to `add-task-contract` output.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_batched_plan tests.test_json_writers -v
```

Expected: PASS.

Commit:

```bash
git add hooks/plan_json.py hooks/plan_writer.py tests/test_batched_plan.py tests/test_json_writers.py
git commit -m "feat: add batch validation plan contract"
```

### Task 2: Gate batch completion on validation state

**Files:**
- Modify: `hooks/plan_writer.py`
- Test: `tests/test_batched_plan.py`
- Test: `tests/test_task_runner.py`

- [ ] **Step 1: Write failing transition tests**

Add tests proving that the final TASK completion leaves `activeBatchId` set, does not create `BATCH_HANDOFF.json`, and returns:

```python
{
    "requiredAction": "run_batch_check",
    "activeBatchId": "B001",
    "batchValidationStatus": "pending",
}
```

Add a writer API test where a successful batch validation marks the batch done and only then emits the existing handoff. Add a failure test where the batch remains active and has `batchValidation.status == "failed"`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m unittest tests.test_batched_plan tests.test_task_runner.TaskRunnerTest.test_code_session_routes_active_batch_then_final_project_check -v
```

Expected: FAIL because `record_task_attempt` currently marks the batch done and creates handoff before a batch check.

- [ ] **Step 3: Implement batch-aware projection and writer APIs**

Change `_batch_status` to require both all TASKs done and `batchValidation.status == "passed"` before returning `done`:

```python
def _batch_status(tasks: list[dict[str, Any]], validation: dict[str, Any]) -> str:
    statuses = [normalize_status(task.get("status")) for task in tasks]
    if any(status == "failed" for status in statuses) or validation.get("status") == "failed":
        return "failed"
    if statuses and all(status == "done" for status in statuses):
        return "done" if validation.get("status") == "passed" else "in_progress"
    if any(status in {"in_progress", "done"} for status in statuses):
        return "in_progress"
    return "todo"
```

Add `record_batch_validation_attempt(...)` to bind evidence, update `latestPassEvidenceIds`, and perform handoff only when no revalidations are pending. Change `record_task_attempt` so the last normal/revalidation TASK returns `batchCheck` rather than `batchHandoff`.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_batched_plan tests.test_task_runner -v
```

Expected: PASS for transition tests; unrelated runner tests may require fixture profile updates completed in this task.

Commit:

```bash
git add hooks/plan_writer.py tests/test_batched_plan.py tests/test_task_runner.py
git commit -m "feat: require validation before batch completion"
```

### Task 3: Implement resumable batch-check runs

**Files:**
- Modify: `hooks/task_runner.py`
- Modify: `hooks/evidence_store.py`
- Test: `tests/test_task_runner.py`

- [ ] **Step 1: Write failing happy-path and retry tests**

Add CLI tests for:

```bash
python hooks/task_runner.py batch-check --feature alpha \
  --batch-id B001 --code-workspace /repo
```

Assert the first invocation creates `.batch-runs/B001/<runId>.json`, writes `action=batch_validation` evidence with `taskId=__batch__`, and completes the batch when commands pass. For a command that fails until a tracked source file is corrected, assert the response is `fix_batch_and_retry_same_run`, the retry uses the same run ID, and both failed/pass evidence remain in history.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m unittest tests.test_task_runner.TaskRunnerTest.test_batch_check_completes_batch tests.test_task_runner.TaskRunnerTest.test_batch_check_retries_failed_commands_in_same_run -v
```

Expected: FAIL because the `batch-check` subcommand and `.batch-runs` state do not exist.

- [ ] **Step 3: Implement batch run state and command evidence**

Add batch-run helpers parallel to task-run helpers:

```python
def _batch_run_path(feature_dir: Path, batch_id: str, run_id: str) -> Path:
    return feature_dir / ".batch-runs" / batch_id / f"{run_id}.json"
```

The saved state contains `batchId`, `runId`, `status`, `repositories`, `baselineRepositories`, `attempts`, `evidenceIds`, and the effective command digest. On retry, reject a changed plan command digest or mismatched requested workspaces. Execute all commands for each attempt, require snapshots to stay unchanged during command execution, and append records with:

```python
{
    "taskId": "__batch__",
    "batchId": batch_id,
    "action": "batch_validation",
    "runId": run_id,
    "changedFiles": changed_files,
    "fileChanges": file_changes,
    "validation": validation_payload,
}
```

Expose `batch-check` with optional `--run-id`; omit it on the first attempt and require the returned run ID for retries.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_task_runner -v
```

Expected: PASS.

Commit:

```bash
git add hooks/task_runner.py hooks/evidence_store.py tests/test_task_runner.py
git commit -m "feat: add resumable batch validation runner"
```

### Task 4: Preserve TASK evidence and revalidate affected scopes

**Files:**
- Modify: `hooks/plan_json.py`
- Modify: `hooks/plan_writer.py`
- Modify: `hooks/task_runner.py`
- Test: `tests/test_task_runner.py`

- [ ] **Step 1: Write failing remediation tests**

Create a two-TASK batch fixture with disjoint scopes. Fail batch compile, modify one TASK's scoped source file, then pass batch compile. Assert:

```python
self.assertEqual(batch["batchValidation"]["status"], "revalidation_required")
self.assertEqual(batch["tasks"][0]["pendingRevalidation"]["attemptType"], "batch_revalidation")
self.assertNotIn("pendingRevalidation", batch["tasks"][1])
self.assertEqual(batch["tasks"][0]["evidenceIds"], original_evidence_ids)
self.assertEqual(batch["tasks"][0]["completionEvidenceIds"], [])
```

Add tests where one path matches multiple TASK scopes and all TASKs are reopened, where a path matches no TASK scope and batch-check returns `batch_fix_out_of_scope`, and where the final batch pass must be newer than the new TASK completion evidence.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m unittest tests.test_task_runner.TaskRunnerTest.test_batch_repair_revalidates_affected_task tests.test_task_runner.TaskRunnerTest.test_ambiguous_batch_repair_revalidates_entire_batch tests.test_task_runner.TaskRunnerTest.test_batch_repair_rejects_out_of_scope_change -v
```

Expected: FAIL because remediation changes are not mapped to TASK scopes and completion pointers are not superseded.

- [ ] **Step 3: Implement scope mapping and revalidation metadata**

Use each TASK's resolved scope against the batch run's requested workspaces. If every changed path maps to exactly one TASK, reopen the unique affected set. If any path maps to multiple TASKs, reopen all TASKs. If any path maps to none, reject the retry.

Store runtime-only metadata excluded from `task_contract_sha256`:

```python
task["pendingRevalidation"] = {
    "attemptType": "batch_revalidation",
    "triggeredByBatchEvidenceIds": list(batch_evidence_ids),
    "supersedesEvidenceIds": list(task["completionEvidenceIds"]),
}
```

Keep `evidenceIds`, clear only current completion pointers, and set the TASK to `todo`. `start` copies this metadata into the new run. `_record_for_command` writes it to new validation evidence; successful binding removes `pendingRevalidation` and makes only the new pass evidence current.

After revalidation, return `run_batch_check`. The same batch run executes all commands again and only a pass newer than every current TASK completion can mark the batch passed.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_task_runner tests.test_batched_plan -v
```

Expected: PASS.

Commit:

```bash
git add hooks/plan_json.py hooks/plan_writer.py hooks/task_runner.py tests/test_task_runner.py tests/test_batched_plan.py
git commit -m "feat: revalidate tasks after batch remediation"
```

### Task 5: Enforce batch evidence at code-done and trust remediation changes

**Files:**
- Modify: `hooks/evidence_integrity_gate.py`
- Modify: `hooks/code_exploration.py`
- Test: `tests/test_evidence_audit.py`
- Test: `tests/test_code_exploration.py`

- [ ] **Step 1: Write failing integrity and cache tests**

Add code-done tests that reject a missing batch pass, a batch pass older than current TASK completion evidence, a mismatched batch command, and an unresolved `pendingRevalidation`. Add a passing case with empty `projectValidationCommands`. Add an exploration test where `batch_validation.changedFiles` are included in trusted evolution.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m unittest tests.test_evidence_audit tests.test_code_exploration -v
```

Expected: FAIL because integrity accepts only `validation`/`project_check` and cache trust reads only TASK completion evidence.

- [ ] **Step 3: Implement batch completion checks and cache trust**

Allow `taskId="__batch__"` only when `action="batch_validation"` and `batchId` names a real batch. Validate every required effective command against the latest successful batch run, including exact `argv`, `cwd`, `kind`, `required`, and `repo` equality. Require the latest batch evidence number to exceed the latest current TASK completion evidence number.

Change project completion logic to return no error when `projectValidationCommands == []`; otherwise preserve the latest-project-check ordering rule and compare it against both TASK and batch evidence.

Extend `collect_trusted_evolution` so successful, plan-bound batch remediation records contribute their `changedFiles` only when their batch/run identity and evidence references validate.

- [ ] **Step 4: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_evidence_audit tests.test_code_exploration -v
```

Expected: PASS.

Commit:

```bash
git add hooks/evidence_integrity_gate.py hooks/code_exploration.py tests/test_evidence_audit.py tests/test_code_exploration.py
git commit -m "feat: enforce batch validation evidence"
```

### Task 6: Update agent protocols and run full verification

**Files:**
- Modify: `skills/autodev/autodev-plan/SKILL.md`
- Modify: `skills/autodev/autodev-code/SKILL.md`
- Modify: `docs/evidence-task-runner.md`
- Modify: `AUTOBIZDEVOPS_新手使用说明.md`
- Test: `tests/test_skill_artifact_drift.py`
- Test: `tests/test_inspect_skill_contract_plain.py`

- [ ] **Step 1: Write failing protocol assertions**

Add skill contract tests that require `add-batch-validation-command`, `run_batch_check`, `fix_batch_and_retry_same_run`, `batch_revalidation`, and the prohibition on project-level TASK command kinds.

- [ ] **Step 2: Run protocol tests and verify RED**

Run:

```bash
python -m unittest tests.test_skill_artifact_drift tests.test_inspect_skill_contract_plain -v
```

Expected: FAIL because the skills still instruct TASK completion or final project-check to own compile commands.

- [ ] **Step 3: Update Plan and Code instructions**

Document this exact execution order:

```text
task complete -> continue active batch
last task complete -> batch-check
batch-check fail -> fix and retry same batch run
batch repair changed source -> revalidate affected TASKs
revalidation complete -> final batch-check
final batch-check pass -> handoff or code-done/project-check
```

State that task commands are behavior-focused, batch profiles own compile/build/typecheck/lint, original evidence is immutable, and project checks are optional cross-boundary checks.

- [ ] **Step 4: Run protocol and complete regression suites**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: PASS with zero failures and errors.

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 5: Commit documentation and protocol updates**

```bash
git add skills/autodev/autodev-plan/SKILL.md skills/autodev/autodev-code/SKILL.md docs/evidence-task-runner.md AUTOBIZDEVOPS_新手使用说明.md tests/test_skill_artifact_drift.py tests/test_inspect_skill_contract_plain.py
git commit -m "docs: move compile validation to batch boundary"
```

## Review Hardening Addendum

- Persist batch attempts as `validation_running -> evidence_written -> plan bound`, adopt streamed command evidence on retry, and make terminal and revalidation plan binding idempotent.
- Publish `activeRunId` before the first command so `code-session` can recover an interrupted run.
- Keep all optional command evidence in history, but bind only required passing evidence as the current batch pass pointer.
- Restrict optional project checks to `integration_test`, `e2e_test`, and `static_check`; reject commands duplicating a batch profile by `argv + cwd + repo`.
- Reject finalized plans missing `batchValidationProfiles` or projected `batchValidation` with `batch_validation_contract_requires_rebuild`.
- Preserve `completedRevalidation` runtime linkage and verify trigger, superseded, current completion, ownership, and evidence ordering at code-done.
- Support `task_covered` profiles for same-workspace targeted Maven lifecycle validation. Final TASK completion writes `batch_closure` evidence and skips redundant batch compilation; `commands` mode retains the resumable batch-check path.
- Reject bare Maven task tests, disguised batch-owned commands, and manual batch command execution in the Code protocol.
