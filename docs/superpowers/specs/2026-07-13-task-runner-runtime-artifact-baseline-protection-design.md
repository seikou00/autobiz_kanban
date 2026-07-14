# Task Runner Runtime Artifact And Baseline Protection Design

## Problem

`task_runner start` resolves every supplied code workspace to its Git root and records SHA-256 hashes for every tracked or untracked, non-ignored file. This is intentional: `complete` must detect both in-scope implementation changes and unrelated changes anywhere else in the participating repositories.

Two failure modes make that safety model difficult to operate:

1. `.cmbdevclaw/large_tool_results/` is tool-owned runtime output inside the business repository. When it is not Git-ignored, changes under that path appear in the repository snapshot and fail task scope validation.
2. After a failed `complete`, aborting and starting again records the already-created implementation as the new baseline. Staging or unstaging does not help because snapshots compare file contents, not index state. A subsequent `complete` therefore sees no task-local change and encourages an invalid `--no-code-change-why` workaround.

## Goals

- Fail before task execution when known CMB DevClaw runtime output is visible to Git snapshots.
- Preserve full Git-root snapshot coverage and out-of-scope detection.
- Prevent accidental abort when a run has unrecorded repository changes.
- Allow a deliberately aborted, pre-evidence run to resume with its original snapshot.
- Make workspace resolution and snapshot semantics explicit in command output and documentation.
- Resolve module-relative `scope.paths` against the exact requested code workspace while keeping repository-wide snapshots.
- Reject `verified_existing` when an earlier aborted run proves that the current implementation was created after that earlier baseline.
- Provide an exact recovery path for existing affected runs without deleting or restaging implementation files.

## Non-Goals

- Do not limit snapshots to the requested subdirectory. A subdirectory locates its containing Git repository and defines the base for task `scope.paths`, while snapshot coverage remains repository-wide.
- Do not silently exclude `.cmbdevclaw` or accept caller-provided arbitrary snapshot exclusions.
- Do not modify a business repository's `.gitignore` or `.git/info/exclude` automatically.
- Do not infer which dirty files belong to a task when the plan has no `scope.paths`; conservative rejection is preferred.
- Do not change evidence validation, validation command execution, or project-check semantics.

## Architecture

The implementation extends the existing repository boundary in `hooks/repository_snapshot.py` and keeps task state transitions in `hooks/task_runner.py`.

### Git Ignore Contract

The runner defines one required ignored runtime path:

```text
.cmbdevclaw/large_tool_results/
```

`repository_snapshot.py` checks a synthetic child path with:

```text
git check-ignore --quiet --no-index -- .cmbdevclaw/large_tool_results/.task-runner-ignore-probe
```

The check tests ignore configuration without creating a file. `start` and `resume` run it for every resolved repository before changing plan or run state. A missing rule fails with:

```text
runtime_artifact_path_not_ignored:<repository-id>:.cmbdevclaw/large_tool_results/
```

The response also sets:

```json
{
  "requiredAction": "configure_git_ignore_and_retry",
  "resolvedGitRoot": "<absolute repository root>"
}
```

Teams should commit the narrow path to `.gitignore`. A developer may instead add it to `.git/info/exclude` when the policy must remain local. The runner never writes either file.

### Explicit Snapshot Metadata

Every new run stores:

```json
{
  "requestedCodeWorkspaces": ["<caller supplied absolute path>"],
  "snapshotMode": "git_visible_file_content_sha256",
  "stagingAffectsSnapshot": false
}
```

The existing `repositories[].path` remains the resolved Git root. CLI output therefore makes it clear that a requested module directory still maps to a repository-wide snapshot.

### Requested-Workspace Scope Resolution

For a new single-repository run, every task `scope.paths` entry is relative to the exact `--code-workspace` supplied to `start`. Git snapshots and `fileChanges[].path` remain relative to the resolved Git root. Start computes the requested workspace's Git-root-relative prefix and stores the canonical projection:

```json
{
  "scopePathBase": "requested_code_workspace",
  "requestedCodeWorkspaces": ["<absolute requested module path>"],
  "workspacePrefixes": ["path/from/git/root/to/module"],
  "declaredScopePaths": ["src/main/java/example"],
  "resolvedScopePaths": ["path/from/git/root/to/module/src/main/java/example"]
}
```

All canonical paths use Git's forward-slash form regardless of operating system. Absolute paths, `..`, empty normalized paths, and requested workspaces outside the resolved Git root are rejected before run creation.

`complete`, `abort`, and `resume` must receive the same requested workspace paths in the same repository order. Matching only the same Git root is insufficient because a different module directory would change the scope base. A mismatch fails with `task_run_requested_workspace_mismatch`.

For multiple repositories, every scope entry must use `repoId:relative/path`. The relative portion is resolved against that repository's requested workspace. An unprefixed entry fails with `scope_path_repository_prefix_required`. Supplying two different requested directories that resolve to the same Git root is rejected as `ambiguous_code_workspace_base`.

Runs created before `scopePathBase` existed retain the legacy behavior: their `scope.paths` are interpreted relative to the Git root. New runs never accept both interpretations, because dual matching would silently widen the declared scope.

Scope normalization does not repair an incomplete plan. Domain, test, resource, migration, or configuration directories that the task is expected to modify must be declared before start. Out-of-scope errors include both declared and resolved scope paths. A changed file inside the requested workspace but outside the resolved scope returns `requiredAction=correct_plan_scope_and_rebuild_task_baseline`; a change outside the requested workspace keeps `requiredAction=fix_workspace_and_retry_same_run`.

### Abort Protection

`abort` already accepts `--code-workspace`; it will now resolve and verify those repositories. Before changing state, it captures the current snapshot and compares it with the run's original snapshot.

When changes exist, normal abort fails without mutating run or plan state:

```text
task_run_has_unrecorded_changes:<comma-separated paths>
```

The JSON response includes:

```json
{
  "requiredAction": "fix_workspace_and_retry_complete_or_force_abort",
  "changedFiles": ["..."],
  "resolvedGitRoots": ["..."]
}
```

An intentional abandonment requires both `--force-with-changes` and a non-empty `--abort-why`. The aborted state retains `abortSnapshot`, `fileChangesAtAbort`, `changedFilesAtAbort`, and `abortWhy`. This preserves enough history to detect a later invalid no-change claim.

Runs with no repository changes retain the current abort behavior and do not require a reason.

### Resume State Transition

A new command resumes an aborted run without replacing its original snapshot:

```text
task_runner.py resume ... --run-id <original-run-id> --code-workspace <repo>
```

Resume succeeds only when:

- the run status is `aborted`;
- no other run is active for the feature;
- no validation evidence was written by the run;
- task ID, batch, task contract, and resolved repositories still match;
- required runtime paths are Git-ignored.

It sets the task back to `in_progress`, sets the run to `started`, increments `resumeCount`, records `resumedAt`, and preserves the original `snapshot` and `repositories[].snapshot` fields. Abort audit fields remain for diagnostics.

Terminal or evidence-bearing runs cannot resume. A plan contract change requires a new task run after the implementation changes have been removed or separately preserved.

### Prior-Baseline Guard For No-Change Completion

When the current run has an empty snapshot diff and requests `verified_existing`, the runner inspects earlier aborted runs for the same task. If an earlier run has `changedFilesAtAbort`, or comparing its original snapshot with the current snapshot finds changes, the no-change claim fails with:

```text
verified_existing_conflicts_with_prior_run_changes:<run-id>:<comma-separated paths>
```

Only paths inside `scope.paths` are considered when scope paths exist. Without scope paths, any prior repository change is considered conflicting. This guard does not affect a genuine no-change task with no prior task-run delta.

## State Flow

```text
start
  -> runtime ignore preflight
  -> started with original snapshot
  -> complete
       -> out-of-scope: fix workspace and retry same run
       -> validation/evidence: existing terminal flow

started
  -> abort with no changes: aborted
  -> abort with changes: rejected, run remains started
  -> force abort with reason: aborted with audit snapshot

aborted without evidence
  -> resume
  -> started with the original snapshot
```

## Existing T001 Recovery

For the reported Windows workspace:

1. Add `.cmbdevclaw/large_tool_results/` to `D:\code\LF39.05_BCWplus_cust\.git\info\exclude`, or commit the same rule in the repository `.gitignore` before creating another run.
2. Abort the newer run whose baseline already contains the implementation. Its snapshot diff should be empty, so normal abort is allowed.
3. Resume the earliest aborted T001 run that was started before implementation files were created.
4. Retry `complete` with that original run ID. Do not unstage/restage files and do not use `--no-code-change-why`.
5. If the original run cannot resume because its task contract changed or it already owns evidence, preserve the patch, restore only T001-scoped files to their original state, start a new run, reapply the patch, and complete normally.

## Error Handling

- Git command failures remain `git_snapshot_failed` or receive the existing repository-resolution error.
- Ignore preflight failures occur before run creation or plan mutation.
- Abort guard failures do not mutate the run JSON or task status.
- Resume performs all checks before mutating either the run or plan. If the plan status update fails, the run remains aborted.
- Structured CLI errors expose `requiredAction`, `changedFiles`, and resolved roots as separate JSON fields rather than embedding all guidance in the error string.

## Testing

### Repository Snapshot Unit Tests

- An ignored `.cmbdevclaw/large_tool_results/` probe passes.
- An unignored probe is returned as a policy violation.
- The check works when the directory does not exist.
- Other untracked files remain snapshot-visible.

### Task Runner Integration Tests

- `start` rejects an unignored runtime path before creating run state.
- `start` reports requested module paths and resolved Git roots.
- A module-relative scope matches Git-root-relative changed paths after canonical resolution.
- A missing domain, test, or resources scope remains out of scope after prefix resolution.
- Complete, abort, and resume reject a different requested module under the same Git root.
- Windows separators normalize to Git forward-slash paths.
- Multi-repository scope paths require `repoId:` and resolve against the matching requested workspace.
- Legacy run states without scope metadata retain Git-root-relative matching.
- Staging and unstaging unchanged file content never creates a snapshot delta.
- `abort` succeeds when the workspace matches the original snapshot.
- `abort` rejects changed workspaces and leaves run/task state unchanged.
- forced abort requires a reason and records the abort snapshot and changes.
- `resume` reuses the original snapshot and allows normal completion of existing implementation changes.
- `resume` rejects active competing runs, contract drift, repository mismatch, and evidence-bearing runs.
- no-change completion rejects task changes inherited from an earlier aborted run.
- genuine verified-existing completion without prior changes still passes.
- multi-repository ignore checks and changed paths retain repository prefixes.

### Regression Tests

- Run `tests.test_code_exploration.RepositorySnapshotTest`.
- Run the complete `tests.test_task_runner.TaskRunnerTest` suite.
- Run the full Python test suite.

## Documentation

Update `docs/evidence-task-runner.md` and `skills/autodev/autodev-code/SKILL.md` to state:

- configure runtime ignores before `start`;
- `--code-workspace` resolves to a Git root;
- snapshots compare content and are unaffected by staging;
- retry the same run after a recoverable workspace error;
- use `resume` for an accidentally aborted pre-evidence run;
- never use verified-existing mode to compensate for a lost implementation baseline.
