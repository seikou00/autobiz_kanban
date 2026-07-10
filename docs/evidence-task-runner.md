# Evidence Task Runner

## Purpose

Structured code tasks use one transactional runner so task status, validation output, Git changes, evidence artifacts, and plan references cannot drift independently.

It prevents tasks being marked done without evidence, missing sidecars/logs, JSON/log content mixing, compile-only acceptance, forged changed files, and duplicate evidence after a crash.

## Artifact Boundary

All workflow artifacts are written under the feature artifact directory:

```text
${artifactWorkspace}/.autobizdevops/features/${feature}/
  plan.json
  PLAN.md
  .plan.lock
  .task-runs/<taskId>/<runId>.json
  evidence/EVIDENCE.jsonl
  evidence/EVIDENCE.index.json
  evidence/.pending/ev_XXXX.json
  evidence/ev_XXXX.json
  evidence/ev_XXXX.log
```

Business repositories are read for Git snapshots and used as validation working directories. The runner never writes evidence or `.task-runs` into a business repository.

## Artifact Roles

- `EVIDENCE.jsonl`: append-only evidence fact stream.
- `EVIDENCE.index.json`: stream line count, last ID, and SHA-256 integrity index.
- `ev_XXXX.json`: structured sidecar equal to one JSONL record.
- `ev_XXXX.log`: only captured command stdout/stderr, redacted and size-limited.
- `.task-runs/...json`: transaction state, start/final snapshots, and evidence bindings.
- `.plan.lock`: serializes every plan read-modify-write transaction so evidence bindings cannot be overwritten by a concurrent writer.
- `evidence/.pending/...json`: short-lived append transaction state, removed after sidecar/index commit.

JSON and log have different roles and must not contain the same JSON object. Every current task/project validation record has a corresponding log, including a zero-byte file for commands with no output. Records include log SHA-256 and byte count; the gate rejects missing, changed, or cross-bound logs.

## Task Lifecycle

Start before changing code:

```bash
python hooks/task_runner.py start --workspace "$ARTIFACT_WORKSPACE" --feature "$FEATURE" \
  --task-id T001 --code-workspace "$BUSINESS_REPO"
```

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

`start` also stores a hash of the task contract, excluding only runtime status/evidence pointers. Do not edit the active task's goal, scope, AC, validation commands, or other contract fields after start; `complete`, recovery, and `code-done` reject contract drift. Abort the run and restart after an intentional Plan correction.

If validation fails, fail evidence and its log are still written, while the task becomes `failed`. After interruption, use `recover` with the same arguments. Recovery can adopt evidence already appended for the same `runId` and command, so a crash between evidence append and run-state update does not duplicate validation. A crash between JSONL append and sidecar/index commit is repaired from `evidence/.pending` before the next append. Use `inspect` to read run state and `abort` only before evidence reaches its terminal write phase.

## Multiple Repositories

Repeat `--code-workspace` for each repository. Every validation command must set `repo` to the Git root directory name when more than one repository participates. Multi-repository changed and supporting paths use `repoId:relative/path`. Duplicate root names are rejected as ambiguous.

All evidence remains in the single feature artifact directory, never in participating business repositories.

## Project Check And Gate

Project compile/typecheck/lint/static checks are separate from task acceptance:

```bash
python hooks/task_runner.py project-check --workspace "$ARTIFACT_WORKSPACE" \
  --feature "$FEATURE" --code-workspace "$BUSINESS_REPO"
python hooks/evidence_integrity_gate.py code-done --feature-dir "$FEATURE_DIR"
```

`project-check` is accepted only after every task is done, rejects validation commands that modify Git-visible files, and must be newer in the evidence stream than every current task completion record. `code-done` requires all task required commands to pass, full AC coverage, exact command equality, task-run/snapshot/contract consistency, valid sidecars/log hashes, and a passing latest project-check run.

## Historical Audit

```bash
python hooks/evidence_audit.py report --feature-dir "$FEATURE_DIR"
python hooks/evidence_audit.py audit --feature-dir "$FEATURE_DIR" --reset-invalid-tasks
```

The reset mode preserves evidence history while moving untrusted completed tasks to `todo`. Old plan schemas are not supported and must be rebuilt by the Plan stage. New completion evidence must be `detailVersion: 2` and originate from a persisted task run.
