# Evidence Task Runner

## Purpose

Structured code tasks use one transactional runner so task status, validation output, Git changes, evidence artifacts, and plan references cannot drift independently.

It prevents tasks being marked done without evidence, missing logs, JSON/log content mixing, compile-only acceptance, forged changed files, and duplicate evidence after a crash.

## Artifact Boundary

All workflow artifacts are written under the feature artifact directory:

```text
${artifactWorkspace}/.autobizdevops/features/${feature}/
  plan.json
  PLAN.md
  .plan.lock
  .task-runs/<taskId>/<runId>.json
  .batch-runs/<batchId>/<runId>.json
  evidence/EVIDENCE.jsonl
  evidence/EVIDENCE.index.json
  evidence/.pending/ev_XXXX.json
  evidence/ev_XXXX.log
```

Business repositories are read for Git snapshots and used as validation working directories. The runner never writes evidence or `.task-runs` into a business repository.

## Artifact Roles

- `EVIDENCE.jsonl`: append-only evidence fact stream.
- `EVIDENCE.index.json`: stream line count, last ID, and SHA-256 integrity index.
- `ev_XXXX.log`: only captured command stdout/stderr, redacted and size-limited.
- `.task-runs/...json`: task transaction state, start/final snapshots, and evidence bindings.
- `.batch-runs/...json`: resumable batch validation attempts, workspace identity, remediation snapshots, and evidence bindings.
- `.plan.lock`: serializes every plan read-modify-write transaction so evidence bindings cannot be overwritten by a concurrent writer.
- `evidence/.pending/...json`: short-lived append transaction state, removed after JSONL/log/index commit.

JSON and log have different roles and must not contain the same JSON object. Every current task/batch/project validation record has a corresponding log, including a zero-byte file for commands with no output. Records include log SHA-256 and byte count; the gate rejects missing, changed, or cross-bound logs.

## Task Lifecycle

Before starting, every business repository must ignore the tool-owned runtime path. Use a shared `.gitignore` rule or a local `.git/info/exclude` rule:

```gitignore
.cmbdevclaw/large_tool_results/
```

The runner validates this rule but never writes either Git configuration file. Start before changing code:

```bash
python hooks/task_runner.py start --workspace "$ARTIFACT_WORKSPACE" --feature "$FEATURE" \
  --task-id T001 --code-workspace "$BUSINESS_REPO"
```

`--code-workspace` locates a repository and must match the module recorded in task `scope.workspaceRoots`. Git resolves a supplied module directory to the repository root, while start projects workspace-relative `scope.paths` to Git-root-relative `resolvedScopePaths`; a scope path that repeats the workspace prefix is rejected during Plan preflight. Task and batch validation `cwd` values remain Git-root-relative and must stay inside the declared workspace root. Plan preflight and runner start verify the command directory plus known manifests such as `pom.xml`, Gradle build files, `package.json`, `Cargo.toml`, and `go.mod`. The start response records `scopePathBase=requested_code_workspace`, `requestedCodeWorkspaces`, `workspacePrefixes`, `resolvedScopePaths`, and the resolved `repositories[].path`. It also seals the task contract, workspace projection, and initial Git snapshot in `integritySha256`; direct edits to those run fields fail with `task_run_integrity_mismatch`. Snapshots hash tracked and untracked, non-ignored file contents across the complete Git root. Complete, abort, and resume must receive the same requested module paths; changing only the module under the same Git root fails with `task_run_requested_workspace_mismatch`. Staging or unstaging a file does not change its content hash and therefore does not create a task-run delta.

Complete after implementation. Validation commands come from `plan.json`; command text, output, exit code, and changed files are not accepted from the caller:

```bash
python hooks/task_runner.py complete --workspace "$ARTIFACT_WORKSPACE" --feature "$FEATURE" \
  --task-id T001 --run-id "$RUN_ID" --code-workspace "$BUSINESS_REPO"
```

For verified existing behavior with no task-local file change:

```bash
python hooks/task_runner.py complete --workspace "$ARTIFACT_WORKSPACE" --feature "$FEATURE" \
  --task-id T001 --run-id "$RUN_ID" --code-workspace "$BUSINESS_REPO" \
  --no-code-change-why "Existing implementation already satisfies the scenario" \
  --supporting-file src/existing_implementation.py
```

This mode requires an empty snapshot diff, a real supporting file, and a required behavior/integration/E2E/static validation. Compile, typecheck, or lint alone cannot complete a no-change task.

Task `validationCommands` accept only behavior, integration, E2E, or static checks. Compile, build, typecheck, and lint belong to the lane-specific batch profile and never run once per task.

`--supporting-file` is relative to the resolved Git root; prefix it with `repoId:` for a multi-repository run. Verified-existing mode is only for behavior that existed before start. It is not a recovery mechanism for implementation files absorbed into a replacement run's baseline, and the runner rejects conflicts found in earlier aborted runs.

`start` also stores a hash of the task contract, excluding only runtime status/evidence pointers. Do not edit the active task's goal, scope, AC, validation commands, or other contract fields after start; `complete`, recovery, and `code-done` reject contract drift. Abort the run and restart after an intentional Plan correction.

An out-of-scope result with `requiredAction=fix_workspace_and_retry_same_run` means the change is outside the requested workspace: fix or ignore the reported tool output and retry `complete` with the same run ID. `requiredAction=correct_plan_scope_and_rebuild_task_baseline` means the change is inside the requested workspace but missing from the declared task scope. Preserve the patch, force-abort with an audit reason, correct the Plan through its writer, restore task files, start from the corrected contract, and reapply the patch. Do not abort just to pass a narrower workspace or to restage files. Abort rejects a changed repository by default. Intentional abandonment requires `--force-with-changes` plus `--abort-why`, and records the changed snapshot for audit.

An accidentally aborted run that has no validation evidence can resume with its original snapshot:

```bash
python hooks/task_runner.py resume --workspace "$ARTIFACT_WORKSPACE" --feature "$FEATURE" \
  --task-id T001 --run-id "$ORIGINAL_RUN_ID" --code-workspace "$BUSINESS_REPO"
```

Resume rejects contract drift, repository mismatch, evidence-bearing runs, and any competing active run in the feature.

If validation fails, fail evidence and its log are still written, while the task becomes `failed`. After interruption, use `recover` with the same arguments. Recovery can adopt evidence already appended for the same `runId` and command, so a crash between evidence append and run-state update does not duplicate validation. A crash between JSONL append and index commit is repaired from `evidence/.pending` before the next append. Use `inspect` to read run state and `abort` only before evidence reaches its terminal write phase.

## Batch Validation And Revalidation

After every task in the active batch is done, run the lane-specific compile/build/typecheck/lint profile once:

```bash
python hooks/task_runner.py batch-check --workspace "$ARTIFACT_WORKSPACE" \
  --feature "$FEATURE" --batch-id B001 --code-workspace "$BUSINESS_REPO"
```

The first call creates a batch run and publishes its active run ID before executing commands. On `fix_batch_and_retry_same_run`, fix only paths covered by the batch task scopes and retry with the returned `--run-id`. Attempts and `action=batch_validation` evidence are append-only. A validation command that modifies Git-visible files is rejected.

Each attempt persists its workspace baseline before command execution, adopts already-streamed evidence after interruption, writes `status=evidence_written` before mutating the plan, and binds the plan idempotently. A crash after evidence append, revalidation binding, or terminal binding resumes with the same run ID without duplicating completed command evidence. Required passing command evidence forms `latestPassEvidenceIds`; optional pass/fail records remain in full history but do not decide batch success.

If remediation changes task-owned files, the passing attempt requests task revalidation. Historical task evidence remains immutable, while the affected tasks lose only their current completion pointers and return to `todo`. Their next successful task evidence records `attemptType=batch_revalidation`, the triggering batch evidence IDs, and superseded completion evidence IDs; the task also retains a `completedRevalidation` runtime pointer so code-done can audit exact ownership and ordering. Ambiguous or shared scope revalidates the whole batch; an out-of-scope remediation is rejected. After task revalidation, the same batch run must pass one final batch-check before handoff.

## Multiple Repositories

Repeat `--code-workspace` for each repository. Every validation command must set `repo` to the Git root directory name when more than one repository participates. Multi-repository changed and supporting paths use `repoId:relative/path`. Duplicate root names are rejected as ambiguous.

All evidence remains in the single feature artifact directory, never in participating business repositories.

## Optional Project Check And Gate

Cross-lane or cross-batch project checks are optional and separate from both task acceptance and batch validation. Their kinds are limited to integration, E2E, and static checks, and their normalized command/workspace/repository identity must not duplicate a batch profile:

```bash
python hooks/task_runner.py project-check --workspace "$ARTIFACT_WORKSPACE" \
  --feature "$FEATURE" --code-workspace "$BUSINESS_REPO"
python hooks/evidence_integrity_gate.py code-done --feature-dir "$FEATURE_DIR"
```

`project-check` is accepted only after every batch is done and is skipped when no project commands are configured. It rejects validation commands that modify Git-visible files and, when configured, must be newer in the evidence stream than every current task and batch validation record. `code-done` always requires all task required commands and every batch profile to pass, full AC coverage, exact command equality, task/batch run consistency, current revalidation pointers, and valid log hashes; it requires a passing latest project-check only when project commands exist.

`EVIDENCE.jsonl` is the only structured evidence fact source. Use `evidence_store.py show --evidence-id ev_XXXX` to inspect one record; new evidence never creates an `ev_XXXX.json` sidecar.

## Historical Audit

```bash
python hooks/evidence_audit.py report --feature-dir "$FEATURE_DIR"
python hooks/evidence_audit.py audit --feature-dir "$FEATURE_DIR" --reset-invalid-tasks
```

The reset mode preserves evidence history while moving untrusted completed tasks to `todo`. Old plan schemas are not supported and must be rebuilt by the Plan stage. New completion evidence must be `detailVersion: 2` and originate from a persisted task run.
