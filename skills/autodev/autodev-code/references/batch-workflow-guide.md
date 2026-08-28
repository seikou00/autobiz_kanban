# Code Batch Workflow Guide

This guide covers the fixed Code batch workflow. Read it when a Code feature
has pending Batches. The workflow control plane is repository-owned at
`workflows/code-batched-execution.workflow.js`; do not generate, validate, or
replace it with model output.

## Start

Run the launcher from the artifact workspace:

```bash
python "${pluginPath}/hooks/workflow_launcher.py" \
  --feature "${feature}" \
  --plugin-path "${pluginPath}" \
  --workspace "${artifactWorkspace}" \
  --json
```

For a single physical Git root, start the returned fixed script content only
when all of these are true. For multiple physical Git roots, require
`executionMode=repository_coordinated` and
`requiredAction=start_repository_coordinator`: run the coordinator `prepare`,
launch every returned `repositoryWorkflows` entry from its own
`workflowHostGitRoot`, wait for all child Workflows in that DAG wave, then run
the coordinator `next`. Repeat until `allMerged=true`, then invoke the
coordinator `final-verify` exactly once. The launcher copies the fixed plugin script into
`artifactWorkspace/.cmbdevclaw/workflows/` as an audit copy and returns
`workflowScriptContent` plus `workflowScriptSha256`. `workflowScriptSource`,
`workflowScript`, and `workflowScriptPath` are audit fields; do not pass those
paths to the Workflow host. `workflowArgs` is the complete argument object for
the Workflow call; do not reconstruct it.

- `useWorkflow=true`
- `canStartWorkflow=true`
- validation reason is `parallel_plan_valid` or `single_batch_workflow_valid`
- single physical root: `executionMode=fixed` and
  `requiredAction=start_fixed_workflow`
- multiple physical roots: `executionMode=repository_coordinated` and
  `requiredAction=start_repository_coordinator`

The launcher reads the top-level `plan.json.codeWorkspaces` mapping and returns
`codeWorkspaces`, `workflowHostGitRoot`, and
`executionIsolation=platform_dynamic_worktrees` for a single physical Git
root. For multiple physical roots it instead returns
`executionMode=repository_coordinated` and a repository coordinator contract.
`artifactWorkspace` is only the artifact/state directory and must never be
reused as a code workspace by guesswork. The platform creates an isolated
checkout only from the Workflow host Git root, so the host must be launched
from `workflowHostGitRoot`. A fixed Workflow can cover one Git root (multiple
logical refs to that same root are allowed). Multiple independent repositories
are launched as child Workflows by the coordinator, using the same fixed script
and one shared scheduler run. The launcher returns
`requiredAction=start_repository_coordinator`; it does not treat a multi-root
mapping as a Plan error. For an older exported Plan without this
field, pass an explicit mapping such as
`--code-workspace "RouYi=/absolute/path/to/RouYi"`; otherwise stop with
`provide_code_workspace_mapping`.

The Code-session command is `task_runner.py code-session`; there is no
`hooks/code_session.py`. Any baseline or workspace argument must be the
absolute business Git root, never a logical workspace name such as `RouYi`.

The Code Session baseline uses format v2: clean committed files are recorded
as Git blob references (`storage=git_blob`, `gitSha`) instead of copied content;
staged, unstaged, and untracked files still receive durable content objects so
rollback preserves pre-existing local changes. An active baseline from an
older format is not migrated and must be cleared before recapturing.

The Workflow tool invocation is fixed too:

```javascript
workflow({
  script: launcher.workflowScriptContent,
  args: JSON.stringify(launcher.workflowArgs)
})
```

Do not pass `scriptPath` for the artifact or plugin path. The inline content is
the unchanged repository-owned fixed script; the host persists it under the
current conversation workspace and then applies its normal workflow controls.
When resuming an existing run, pass only `resumeFromRunId` and do not resolve the
artifact path again.

The Workflow host workspace is a required platform Worktree source contract:
it must be the child request's `workflowHostGitRoot`. A coordinator child must
contain exactly one physical Git root and only the returned `repositoryRefs` /
`batchIds`; it must not receive another repository's workspace mapping. The
artifact workspace remains independent and only stores Feature state.

## Execution Contract

### Frontend Route Gate

Route 解析不属于 Code Session 的全局前置步骤。Batch Agent 必须先执行
`code_task_context.py`，再以返回的 `taskContract.uiRequired` 判断当前 Task：
后端 Task 跳过 Route resolver、HTML 和 Route SKILL；前端 Task 才能在同一
Agent 内完成 Route 解析、清单、parser（如适用）和 `FRONTEND_ROUTE.json`
回检。这样同一批次中的后端 Task 不会被其他 Task 的 UI 产物阻塞。

The fixed script starts with scheduler `ensure` and then runs a DAG in
merge-gated waves. `ensure` creates the first durable run or reuses an active
run only after validating every sealed platform delivery. A `needs_resolution`
run or a missing sealed worktree remains fail-closed; do not create a new run
to bypass it.

The reuse validation also blocks a Batch marked `merged` without a non-empty
`mergeCommitSha`, a dirty source checkout, or a source HEAD that differs from
the run's recorded HEAD. In each case it returns the original `runId` with a
blocked result. A Batch is `merged` only when `batch_merger.py` has completed
the Git integration and written its merge commit; no worker-facing command may
set this status.

The fixed Workflow passes `--allow-bootstrap` to `ensure`. When the source
repository has uncommitted business changes, the scheduler automatically creates
one `autodev: bootstrap <feature> baseline` commit before platform worktrees
are provisioned. This is the only automatic source-branch commit before Batch
delivery merges; platform-owned `.cmbdevclaw/**` files are excluded from both
the dirty check and baseline commit. Direct CLI uses of `ensure` do not enable
bootstrap by default and return `parallel_code_workspace_bootstrap_required`
instead of modifying the repository.

The multi-repository coordinator uses the same controlled bootstrap policy:
`repository_workflow_coordinator.py prepare` passes `allow_bootstrap=True` to
the shared scheduler before child Workflows are launched. This keeps dirty
repositories from failing only because they entered through the coordinator;
the bootstrap still excludes platform runtime files and records one explicit
baseline commit per physical Git root.

1. The scheduler selects pending Batches whose dependencies are all `merged`.
   It does not create a directory. The Workflow starts every selected child
   with `agent(..., { isolation: "worktree" })`; the platform then creates and
   records the native linked worktree from the frozen host base. The first wave
   therefore contains all no-dependency Batches.
2. The current wave runs concurrently with `parallel()` up to `maxParallel`.
   Every Batch leases the platform-assigned linked worktree and records its
   actual path and branch in the scheduler manifest. Each isolated agent gets
   its own checkout; any overlap is handled as a real merge conflict at the
   barrier.
3. Each Batch acquires a lease, implements only its assigned TASKs, runs
   `batch-compile`, then invokes `worktree_manager.py seal` to commit and
   persist its delivery SHA. `lease release --final-status ready_to_merge`
   accepts that status only after the persisted SHA is present; an unsealed
   compile result cannot enter the merge frontier.
4. After every Batch in that wave finishes, the shared Workflow owner invokes
   `batch_merger.py --conflict-mode native-rebase`.
5. Only after that merge succeeds does the script call scheduler `resume` to
   calculate the next wave. A dependent Batch never starts from an unmerged
   upstream result.
6. The merge hook writes `mergeCommitSha` and only then marks the Batch TASKs
   `done`. A compile-passed delivery remains `implemented` / `ready_to_merge`
   until this source-branch integration succeeds.
   If Git merge succeeds but the Plan writer fails, the Batch is retained as
   `needs_resolution` with `resolution.kind=plan_state_update`; the fixed
   Workflow (and a subsequent `ensure`/`resume`) verifies the source HEAD and
   retries the idempotent Plan update before releasing downstream work.
7. Once all Batches are `merged`, `parallel_final_verify.py` runs the final
   compile gate.

Independent Batches run in parallel, including independent Batches in the same
repository. A dependency chain naturally advances one merged wave at a time,
which is the required serial behavior.

## Batch Agent Boundaries

Within its platform-assigned worktree, a Batch agent must:

- acquire and later release the `runId + batchId` lease;
- capture its actual path and branch from `pwd`, `git rev-parse --show-toplevel`
  and `git branch --show-current`; it must not create/delete a worktree or fall
  back to the artifact workspace or source checkout;
- pass `--workspace`, `--parallel-run-id`, and `--lease-token` to every
  `task_runner.py` command;
- acquire the Batch lease with the scheduler's `timeoutPerBatch` as
  `--ttl-seconds`, then run `batch_lease_manager.py heartbeat` in the
  background throughout implementation, batch compile, and sealing; renew no
  less often than every five minutes and verify both heartbeat liveness and
  `check --owner-token` before compile and before seal;
- pass the platform-assigned worktree as `--code-workspace`;
- complete all assigned TASKs, then run the one allowed `batch-compile`;
- invoke `worktree_manager.py seal` to commit the successful worktree and
  persist its path, branch, and commit SHA before releasing as `ready_to_merge`.

The Batch agent must not merge, rebase, resolve conflicts, delete worktrees, or
modify a shared main checkout. It must run with platform `isolation: "worktree"`.
The shared Workflow owner is the only actor that invokes the merge hook.
All workflow paths and the feature ID come only from launcher `workflowArgs`;
literal `undefined` or non-absolute paths are rejected before any Batch agent
is created. A Batch agent must never compensate by creating a branch or a
second workflow.

## Recovery And Failure

The manifest at `.parallel-runs/<runId>/manifest.json` is the only parallel-run
state.

- The fixed Workflow always uses `parallel_batch_scheduler.py ensure` at
  startup. It reuses an active, valid run rather than creating a second one.
  Use `parallel_batch_scheduler.py resume` only for an explicitly guided
  recovery after inspecting the retained run.
- Use `batch_lease_manager.py reclaim` for an expired lease and
  `parallel_batch_lifecycle.py monitor` to inspect a run.
- The scheduler timeout and lease TTL are distinct unless the fixed Workflow
  explicitly passes `--ttl-seconds ${timeoutPerBatch}`. Do not rely on the
  lease CLI's 15-minute default for a Batch allowed to run longer. Stop the
  heartbeat only after sealing, immediately before release; every failure
  path must stop it and release the lease as `failed`.
- A merge conflict, failed compile, plan digest change, or failed final verify
  blocks the run. Do not use `ours`, `theirs`, `git merge -s ours`,
  `--no-verify`, or direct edits in the shared checkout to bypass it.
- Successful or explicitly rolled-back terminal runs may be handed to
  `parallel_batch_lifecycle.py cleanup`; it records retained platform
  deliveries but never removes their directories or branches.
- Platform-owned `.cmbdevclaw/workflows/**` journal, state, and toolstream
  files are excluded from source-dirt checks. Do not use `git checkout -- .`
  or `git clean` to remove them: those commands can destroy user work or the
  Workflow journal needed for recovery.
- `batch_merger.py` is the integration owner for this workflow. After it has
  merged a Batch, the platform panel may still show the retained delivery for
  Diff/recovery; do not click the platform Merge action a second time. Inspect
  it, then use the platform's discard/cleanup flow when it is no longer needed.
- Never delete `.parallel-runs/<runId>`, copy files out of an orphaned
  worktree, or manually merge a Batch branch. Those operations bypass delivery
  evidence and leave the run unrecoverable.
