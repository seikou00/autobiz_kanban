# Batch Validation Boundary Design

## Goal

Move project-level compile validation out of individual TASK completion and run it once after each execution batch. TASK completion continues to run fast, task-specific behavior, integration, API, E2E, and local static validations. Backend and frontend batches use their own validation command profiles. The final project check remains only for cross-batch or frontend/backend integration checks.

## Current Problem

`task_runner complete` currently executes every command in a task's `validationCommands`. Plans commonly place compile, typecheck, build, or lint commands there, so the same project-level check repeats for every TASK. The existing `projectValidationCommands` run only after all batches and cannot express a batch boundary. Task evidence also captures only the task run's file delta, so a later compile repair needs a separate audit boundary.

## Design

### Validation ownership

The plan writer accepts batch validation profiles for the execution lanes present in the plan:

- `backend`: compile, build, typecheck, and lint commands for backend repositories.
- `frontend`: compile, build, typecheck, and lint commands for frontend repositories.

Every lane used by a batch must have a non-empty profile with at least one required command. An unused lane profile may be absent.

Each projected batch records the effective command snapshot for its `executionLane`. The profile and projected commands are included in the plan digest so changing a command requires rebuilding the plan contract.

The root and projected batch shapes are:

```json
{
  "batchValidationProfiles": {
    "backend": {"commands": []},
    "frontend": {"commands": []}
  }
}
```

```json
{
  "batchValidation": {
    "profile": "backend",
    "status": "pending",
    "commands": [],
    "evidenceIds": [],
    "latestPassEvidenceIds": [],
    "activeRunId": null
  }
}
```

Projected command IDs use `BATCH-B001-VAL-001` form so evidence remains unambiguous when multiple batches use the same lane profile. Valid batch validation statuses are `pending`, `running`, `failed`, `revalidation_required`, and `passed`.

Task `validationCommands` must not contain project-level command kinds (`compile`, `build`, `typecheck`, or `lint`). They continue to require behavior or integration coverage for every acceptance criterion. A compile-only task remains invalid.

`projectValidationCommands` is optional. When present, it is reserved for checks that cross batch or frontend/backend boundaries and must not duplicate a batch profile command. When it is empty, all batch validations passing is sufficient to enter the final code-done gate.

### Batch state transition

Completing the final TASK in an active batch marks that TASK done but leaves the batch active with `batchValidation.status = pending`. The runner returns `requiredAction=run_batch_check`; it must not create a handoff yet.

`batch-check` creates a batch validation run under the feature artifact directory, captures repository snapshots, and executes every command in the batch profile. It records one append-only `batch_validation` evidence record per command and binds the evidence IDs to the batch.

- All required commands pass with no stale TASK completion evidence: set `batchValidation.status = passed`, mark the batch done, then create the existing handoff or final-batch transition. If remediation changes made TASK evidence stale, use `revalidation_required` instead.
- A required command fails: set `batchValidation.status = failed`, keep the batch active, and return `fix_batch_and_retry_same_run` with the batch run ID. The agent fixes the workspace and retries the same run. A failed batch does not advance to the next conversation.
- A validation command changes Git-visible files: reject the attempt, matching the existing project-check behavior.

The batch run baseline is captured before the first batch check, after all TASK runs have completed. Files changed after that baseline are batch remediation changes. The allowed remediation scope is the union of the current batch's resolved TASK scopes. Changes outside that union return a plan-scope correction error.

### Evidence and file-change history

Evidence is append-only. Existing TASK evidence is never edited or deleted.

- A TASK run records the delta from its own `start` snapshot in its normal `validation` evidence. This remains the historical record of that TASK implementation.
- A batch run records the delta from the post-TASK batch baseline in `batch_validation` evidence. It records compile repair changes separately and does not claim TASK acceptance coverage.
- Batch records use `taskId = "__batch__"` plus an explicit `batchId`; integrity checks must recognize this non-TASK evidence owner.
- The batch's `batchValidationEvidenceIds` retain every command attempt. The latest passing attempt is the one that permits handoff.

If a batch retry makes no Git-visible source/config change, existing TASK completion evidence remains current. If a retry changes a path inside a TASK scope, the runner invalidates that TASK's current completion pointer without rewriting history:

1. Keep the old evidence in the TASK's `evidenceIds` history.
2. Clear `completionEvidenceIds` and `latestPassEvidenceId` for the affected TASK.
3. Require a new TASK run of only the task-level behavior/integration validations. This is a new append-only `validation` evidence chain, with a revalidation marker and a link to the batch evidence that superseded the previous completion.
4. Point the TASK's completion fields at the new passing evidence. A no-code-change revalidation uses the existing supporting-file requirement and records an empty file delta.

If a changed path is shared or cannot be mapped unambiguously, revalidate all TASKs in the batch. Batch validation cannot hand off while required revalidations are pending.

Revalidation evidence uses the normal TASK ID and `action = "validation"`, plus `attemptType = "batch_revalidation"`, `triggeredByBatchEvidenceIds`, and `supersedesEvidenceIds`. This keeps existing TASK completion checks while making the replacement chain explicit.

A passing batch check that introduced remediation changes transitions to `revalidation_required`, not `passed`. After every affected TASK has new completion evidence, the runner executes the same batch run's complete command set once more. Only this final pass can set the batch to `passed`. A TASK revalidation that changes files makes the prior batch pass stale and follows the same loop. A TASK is invalidated only when its latest completion evidence predates the latest mutation affecting its scope, so an already revalidated TASK is not repeatedly reopened for the same repair.

### Code session and final checks

`code-session` returns the active batch while TASKs or required revalidations remain. After the final TASK, it returns the batch-check action instead of opening the next conversation. After batch validation passes, the existing `stop_and_open_new_conversation` handoff behavior is preserved.

After all batches pass, `code-session` runs `project-check` only when `projectValidationCommands` is non-empty. `code_done` requires the latest passing project check in that case; otherwise it accepts the latest passing batch validations and all TASK completion evidence.

The final passing batch validation evidence must be newer than every current TASK completion evidence in that batch. Final project-check evidence, when configured, must be newer than all batch validation evidence and TASK completion evidence.

### Exploration-cache interaction

Batch remediation evidence is trusted evolution for the active batch. The next TASK in the same batch may use the existing deferred-cache policy. At the next batch boundary, the cache is patched or re-recorded using the accumulated batch changes, just as it is for normal TASK evidence.

## Compatibility and failure handling

The repository does not provide parallel plan versions. A finalized plan without batch validation profiles or with project-level commands still embedded in TASK validation fails the new contract with a rebuild-required error; it is not silently migrated. An active run tied to an old plan must be explicitly aborted before rebuilding the plan and starting a new run.

Batch command failures are retriable in the same batch run. Command output, exit code, snapshot identity, changed files, and retry history remain in the evidence stream and batch run state. No manual evidence append or plan status edit is allowed.

## Testing

Tests must cover:

- plan writer validation and projection of backend/frontend batch profiles;
- rejection of project-level command kinds in TASK validation;
- final TASK completion returning `run_batch_check` without handoff;
- passing batch checks completing the batch and creating handoff;
- failed batch checks retaining the active batch and retrying with the same run ID;
- rejection of validation commands that modify Git-visible files;
- batch remediation scope enforcement;
- preservation of original TASK file-change evidence and append-only revalidation evidence;
- selective revalidation for affected TASKs and conservative full-batch revalidation for ambiguous shared paths;
- multi-repository frontend/backend profiles and optional final project checks;
- evidence integrity and code-done behavior with `__batch__` records;
- exploration-cache trust within a batch and patching at the next batch boundary.
