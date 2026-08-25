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

Start the returned fixed script content only when all of these are true. The
launcher copies the fixed plugin script into
`artifactWorkspace/.cmbdevclaw/workflows/` as an audit copy and returns
`workflowScriptContent` plus `workflowScriptSha256`. `workflowScriptSource`,
`workflowScript`, and `workflowScriptPath` are audit fields; do not pass those
paths to the Workflow host. `workflowArgs` is the complete argument object for
the Workflow call; do not reconstruct it.

- `useWorkflow=true`
- `executionMode=fixed`
- `canStartWorkflow=true`
- `requiredAction=start_fixed_workflow`
- validation reason is `parallel_plan_valid` or `single_batch_workflow_valid`

The launcher reads the top-level `plan.json.codeWorkspaces` mapping and returns
`codeWorkspaces`, `workflowHostGitRoot`, and
`executionIsolation=platform_dynamic_worktrees`.
`artifactWorkspace` is only the artifact/state directory and must never be
reused as a code workspace by guesswork. The platform creates an isolated
checkout only from the Workflow host Git root, so the host must be launched
from `workflowHostGitRoot`. A fixed Workflow can cover one Git root (multiple
logical refs to that same root are allowed). Multiple independent repositories
must be launched as separate Workflows; the launcher stops with
`launch_workflow_per_code_repository`. For an older exported Plan without this
field, pass an explicit mapping such as
`--code-workspace "RouYi=/absolute/path/to/RouYi"`; otherwise stop with
`provide_code_workspace_mapping`.

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
it must be the launcher's `workflowHostGitRoot`. The artifact workspace remains
independent and only stores Feature state.

## Execution Contract

The fixed script runs a DAG in merge-gated waves:

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
   `batch-compile`, commits its worktree, records `ready_to_merge`, and releases
   its lease.
4. After every Batch in that wave finishes, the shared Workflow owner invokes
   `batch_merger.py --conflict-mode native-rebase`.
5. Only after that merge succeeds does the script call scheduler `resume` to
   calculate the next wave. A dependent Batch never starts from an unmerged
   upstream result.
6. Once all Batches are `merged`, `parallel_final_verify.py` runs the final
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
- pass the platform-assigned worktree as `--code-workspace`;
- complete all assigned TASKs, then run the one allowed `batch-compile`;
- invoke `worktree_manager.py seal` to commit the successful worktree and
  persist its path, branch, and commit SHA before releasing as `ready_to_merge`.

The Batch agent must not merge, rebase, resolve conflicts, delete worktrees, or
modify a shared main checkout. It must run with platform `isolation: "worktree"`.
The shared Workflow owner is the only actor that invokes the merge hook.

## Recovery And Failure

The manifest at `.parallel-runs/<runId>/manifest.json` is the only parallel-run
state.

- Use `parallel_batch_scheduler.py resume` only after a successful merge or to
  resume a retained run.
- Use `batch_lease_manager.py reclaim` for an expired lease and
  `parallel_batch_lifecycle.py monitor` to inspect a run.
- A merge conflict, failed compile, plan digest change, or failed final verify
  blocks the run. Do not use `ours`, `theirs`, `git merge -s ours`,
  `--no-verify`, or direct edits in the shared checkout to bypass it.
- Successful or explicitly rolled-back terminal runs may be handed to
  `parallel_batch_lifecycle.py cleanup`; it records retained platform
  deliveries but never removes their directories or branches.
- `batch_merger.py` is the integration owner for this workflow. After it has
  merged a Batch, the platform panel may still show the retained delivery for
  Diff/recovery; do not click the platform Merge action a second time. Inspect
  it, then use the platform's discard/cleanup flow when it is no longer needed.
