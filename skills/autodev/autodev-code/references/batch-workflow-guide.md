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
`codeWorkspaces` plus `executionIsolation=plugin_managed_git_worktrees`.
`artifactWorkspace` is only the artifact/state directory and must never be
reused as a code workspace by guesswork. Multiple independent business Git
repositories are valid. For an older exported Plan without this field, pass an
explicit mapping such as `--code-workspace "RouYi=/absolute/path/to/RouYi"`;
otherwise stop with `provide_code_workspace_mapping`.

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

The Workflow host workspace is not a business-repository contract. Each
`workspaceRef` can point to a different Git checkout; the plugin creates its
linked worktree at `<business-git-root>/.worktrees/<runId>-<batchId>`.

## Execution Contract

The fixed script runs a DAG in merge-gated waves:

1. The scheduler selects pending Batches whose dependencies are all `merged`,
   then immediately creates and records each selected Batch's linked worktree
   while the Batch is still `pending`. The Workflow starts its child agent only
   after the scheduler has returned the fixed path and branch. The first wave
   therefore contains all no-dependency Batches.
2. The current wave runs concurrently with `parallel()` up to `maxParallel`.
   Every Batch leases its pre-created plugin-managed linked worktree in its own
   business repository. This remains true when several Batches target the same
   repository: each gets its own `.worktrees/` directory, and any overlap is
   handled as a real merge conflict at the barrier.
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

Within its assigned plugin-managed worktree, a Batch agent must:

- acquire and later release the `runId + batchId` lease;
- use the scheduler-provided path and branch verbatim; it must not create a
  worktree itself or fall back to the artifact workspace or source checkout;
- pass `--workspace`, `--parallel-run-id`, and `--lease-token` to every
  `task_runner.py` command;
- pass the plugin-assigned worktree as `--code-workspace`;
- complete all assigned TASKs, then run the one allowed `batch-compile`;
- invoke `worktree_manager.py seal` to commit the successful worktree and
  persist its path, branch, and commit SHA before releasing as `ready_to_merge`.

The Batch agent must not merge, rebase, resolve conflicts, delete worktrees, or
modify a shared main checkout. It must not use platform `isolation`. The shared
Workflow owner is the only actor that invokes the merge hook.

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
  `parallel_batch_lifecycle.py cleanup`; the plugin removes only its own
  `.worktrees/<runId>-<batchId>` directories and temporary branches.
