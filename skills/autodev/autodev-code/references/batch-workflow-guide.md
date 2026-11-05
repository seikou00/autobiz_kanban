# Code Batch Workflow Guide

This guide covers the fixed Code batch workflow. Read it when a Code feature
has pending Batches. The workflow control plane is repository-owned at
`workflows/code-batched-execution.workflow.js`; do not generate, validate, or
replace it with model output.

New runs use isolated feature integration by default. Each business checkout
in Plan `codeWorkspaces` supplies its current branch and committed HEAD at run
creation. These are frozen as the baseline and final promotion target; resume
reuses the manifest even if the user later switches branches. Do not guess
`main`/`master` or create runtime configuration to select a branch; legacy
`targetBranches` no longer controls new runs. A detached HEAD or unborn branch
stops creation with an explicit error. Every physical Git repository gets an
integration branch/worktree, all Batch promotions target that integration
checkout, and final verification ends at user confirmation.
The launcher returns `review_integration_and_confirm_promotion` for a retained
or reviewable result; do not restart the Workflow or report a Plan error.
Before asking for final approval, the main agent must send a visible message
listing each repository's baseline branch/SHA, `gitRoot`, and actual
`targetWorktreePath`, followed by the integration `worktreePath`, branch/SHA,
diff, validation findings, and `mainPromotion.reportPath`. Tell the user which
directory to open to inspect baseline code and which to open for the proposed
code. A null `targetWorktreePath` means the baseline branch is not checked out;
direct the user to that branch in the repository's Git view, without switching
their checkout. Run `prepare` first if an older report lacks these locations.
Show these paths in the conversation before opening the confirmation question.
Never call the final `promote` command without the user's explicit approval of
the concrete report and its `approvalDigest`. See
[`docs/feature-integration-worktree.md`](../../../../docs/feature-integration-worktree.md)
for configuration, final confirmation commands, main synchronization, and
recovery. Existing manifests without isolated integration keep direct mode;
the older bootstrap/main descriptions below apply to those runs only.

## Start

Run the launcher in the mounted workspace of the platform Workflow tool. Keep
the artifact workspace as the `--workspace` value and pass the mounted path as
`--workflow-workspace`:

```bash
python -X utf8 "${pluginPath}/hooks/workflow_launcher.py" \
  --feature "${feature}" \
  --plugin-path "${pluginPath}" \
  --workspace "${artifactWorkspace}" \
  --workflow-workspace "$(pwd)" \
  --json
```

For one or more physical Git roots, start the returned fixed script path once
when all of these are true. Pass the complete launcher `workflowArgs` mapping
to that one Workflow. It creates a shared scheduler run and uses `parallel()`
to start one agent chain for each independently runnable Batch; each chain
provisions its own repository-native Worktree. The fixed Workflow performs
evidence aggregation after all delivery Batches are promoted, with frontend/
backend UTest and E2E explicitly recorded as not run. The
launcher keeps a Feature-scoped archival copy in
`artifactWorkspace/.cmbdevclaw/workflows/<feature>/`, then copies that fixed
script into `workflowWorkspace/.cmbdevclaw/workflows/<feature>/` for the
platform call. `workflowScriptPath` is the mounted-workspace copy and
`workflowArtifactScriptPath` is the archival copy; both match
`workflowScriptSha256`. `workflowScriptSource` identifies the immutable
source. `workflowArgs` is the complete argument object for the Workflow call;
do not reconstruct it. Each launcher invocation supplies a fresh
`workflowInvocationId`; preserve it unchanged so command retries reuse their
request IDs while separate Workflow invocations remain isolated. The launcher
checks the fixed template and verifies the published SHA before returning a
startable result. Unsupported ambient clock/random references stop publication;
report the plugin template error rather than editing a runtime copy.


- `useWorkflow=true`
- `canStartWorkflow=true`
- validation reason is `parallel_plan_valid` or `single_batch_workflow_valid`
- any number of physical roots: `executionMode=fixed` and
  `requiredAction=start_fixed_workflow`

The launcher reads the mandatory top-level `plan.json.codeWorkspaces` mapping
and returns the complete `codeWorkspaces` mapping, optional
`workflowHostGitRoot` metadata, and `executionIsolation=native_git_worktrees`.
`artifactWorkspace` is only the artifact/state directory and must never be
reused as a code workspace by guesswork. The plugin creates linked native
checkouts from each `codeWorkspaces` binding, so the host may be launched from
the artifact directory. A fixed Workflow can cover multiple Git roots (and
multiple logical refs may share a root). It uses one shared scheduler run and
starts parallel Batch agents inside the same Workflow. A multi-root mapping is
not a Plan error. A missing or invalid mapping is a Plan error: stop and
repair the Plan. There is no CLI workspace override or legacy mapping fallback.

There is no `task_runner.py code-session` command and no
`hooks/code_session.py`. Capture the baseline only with
`rollback_stage.py --capture-code-session`; then start the fixed Workflow via
the launcher. Any baseline workspace argument must be the absolute business
Git root, never a logical workspace name such as `RouYi`.

The Code Session baseline uses format v2: clean committed files are recorded
as Git blob references (`storage=git_blob`, `gitSha`) instead of copied content;
staged, unstaged, and untracked files still receive durable content objects so
rollback preserves pre-existing local changes. An active baseline from an
older format is not migrated and must be cleared before recapturing.

The Workflow tool invocation is fixed too:

Use the top-level Workflow tool with this native parameter object (not from a wrapper workflow):

```javascript
{
  scriptPath: launcher.workflowScriptPath,
  args: launcher.workflowArgs
}
```

The launcher must materialize the mounted-workspace launch copy before this
call. Do not use the plugin source, business repository path, or inline
content. Use
`resumeFromRunId` only while the platform Workflow itself is interrupted or
non-terminal. If the platform Workflow already returned a terminal result but
the scheduler run still has `retry_pending` or stage-recovery Batches, start a
fresh platform Workflow with the same launcher `scriptPath` and `args` (no
`resumeFromRunId`). `scheduler ensure` then reuses the durable scheduler run
and reads its current state instead of replaying a completed platform journal.

Before making this call, pass the platform Workflow tool's already-mounted
workspace to the launcher as `--workflow-workspace` (or launch the command
from that directory). `launcher.workflowWorkspaceRoot` must then equal that
absolute path, and `workflowScriptRelativePath` proves the returned script is
contained below it. If the platform mounts a different workspace root, stop
with `workflow_workspace_root_mismatch` and rerun the launcher with the
correct mounted path. Do not replace the returned script with the plugin
source, a business-repository path, or inline content.

The Workflow host workspace is not a Worktree source contract. The plugin
resolves every repository from the complete `codeWorkspaces` mapping and
provisions its own native Worktree for each Batch. The artifact workspace
remains independent and only stores Feature state.

## Execution Contract

### Frontend Route Gate

Route 解析不属于 Code Session 的全局前置步骤。Batch Agent 必须先执行
`code_task_context.py`，再以返回的 `taskContract.uiRequired` 判断当前 Task：
后端 Task 跳过 Route resolver、HTML 和 Route SKILL；前端 Task 才能在同一
Agent 内完成 Route 解析、清单、parser（如适用）和 `FRONTEND_ROUTE.json`
回检。这样同一批次中的后端 Task 不会被其他 Task 的 UI 产物阻塞。

前端 TASK 的固定顺序是：Route 与 HTML / 项目资料只读准备 →
`task_runner.py start` 成功并保存真实 `runId` → HTML 分析、产物生成、
assets 处理 → parser / 页面实现 → `finish-implementation`。
Route 清单完成不是 start 的前置条件；`prepare_html_analysis.py`、
`.frontend/html-analysis` / `.frontend/html-input` 生成和资源复制都必须在
start 成功后执行。修复/恢复先由 `start-task-repair` / `resume` 建立活动 run。
start 失败不走脚本降级，保留原始错误并按既有 Batch 失败协议处理。

The fixed script starts with scheduler `ensure` and then runs a merge-gated,
dynamic-slot DAG. `ensure` creates the first durable run or reuses an active
run only after validating every sealed native delivery. A `needs_resolution`
run or a missing sealed worktree remains fail-closed; do not create a new run
to bypass it.

A Batch rescheduled after an interrupted child keeps its existing stage
attempts and evidence, including a stage whose status is still `running`.
That status is a continuation checkpoint while the new generation is waiting
for execution: without a new dispatch or business lease, it must not occupy a
scheduler slot or exclude itself from the runnable set. A newly claimed
dispatch occupies a slot immediately, including during lease-free Review.
After updating the plugin, use the launcher to continue the same run; retain
the Worktree and manifest instead of deleting them or resetting stage evidence.
Active TASK exclusivity is scoped to `parallelRunId + batchId`; a TASK in a
different Batch does not block start. Resume closes the interrupted Batch's
old active TASK runs. An inspect result containing only an `aborted` old run
allows a fresh start for that unfinished TASK under the new dispatch grant.

The reuse validation also blocks a Batch marked `merged` without a non-empty
`mergeCommitSha`, a dirty source checkout, or a source HEAD that differs from
the run's recorded HEAD. In each case it returns the original `runId` with a
blocked result. A Batch is `merged` only when `parallel_merge_train.py` has
fast-forwarded the candidate Merge Train SHA whose Batch Review passed and
whose test omission is recorded, and written its
merge commit; no worker-facing command may
set this status.

The fixed Workflow passes `--allow-bootstrap` to `ensure`. When a source
repository has uncommitted business changes, the scheduler automatically creates
one `autodev: bootstrap <feature> baseline` commit before native worktrees
are provisioned. This is the only automatic source-branch commit before Batch
delivery merges; platform-owned `.cmbdevclaw/**` files are excluded from both
the dirty check and baseline commit. Direct CLI uses of `ensure` do not enable
bootstrap by default and return `parallel_code_workspace_bootstrap_required`
instead of modifying the repository.

The same controlled bootstrap policy applies to every repository binding in a
multi-root run. Platform runtime files are excluded and the scheduler records
one explicit baseline commit per physical Git root before Batch worktrees are
provisioned.

1. The scheduler selects pending Batches whose dependencies are all `merged`,
   whose write sets are safe with every active Batch lifecycle, and for which a
   `maxParallel` slot is free. Active includes running Review stages after
   a delivery has been sealed. The Workflow runs a fixed `maxParallel` worker
   pool. New isolated runs use control protocol v2: one `settle-delivery`
   operation gates, queues, builds and promotes a delivery, records cleanup,
   finishes its dispatch and returns a revisioned scheduler snapshot. Completion
   immediately wakes idle workers. Workers share a 60-second fallback and failed
   reads retry after 5/10/20/30 seconds, capped at 30 seconds. Capacity waits are
   at most 30 seconds and denied claims wait 15 seconds or an earlier completion.
   No worker dispatches from an unavailable or superseded snapshot.
   `settle-delivery` resolves and pins the sealed SHA inside Runtime; the
   Workflow does not transcribe `--expected-commit`. Queue/repository-lock waits
   return `queued` with an operation ID, blockers and a scheduler snapshot,
   release the dispatch slot and retain the ticket for a later claim. Waiting
   does not consume a Batch failure retry. A process execution lock lets Runtime
   recover an abandoned settler without revoking an active merge or conflict edit.
   Sealed Batches with closed stages resume at `nextStage=delivery`, without
   replaying implementation or Review. A retained candidate/reservation is reused;
   an already-merged Batch only needs its operation/dispatch receipts reconciled.
   After each merge, Runtime publishes the integrated files to the user's
   original checkout as uncommitted changes. It never moves that checkout's
   HEAD or changes its index. A detached temporary worktree merges the previous
   published integration SHA, the user's saved files and the latest integration
   SHA; local staged/unstaged/untracked files are backed up and retained.
   `workspaceSync` carries publication progress and conflict context. The Workflow
   resolves only those temporary conflict files and calls Runtime `complete`;
   it never edits the user's files directly. Interrupted writes replay from a
   per-file journal; concurrent user edits trigger a fresh merge. Publication
   failures stay separate from Batch retries and are retried at finalization.
   `review_workspace_changes` requests acceptance of uncommitted results, never
   permission to commit or promote them. Backup refs are not source-branch commits.
   A failure releases that worker to dispatch an unrelated Batch; dependents
   remain blocked until their dependency is merged.
2. The worker pool runs tasks concurrently with `parallel()` up to `maxParallel`.
   Every Batch independently runs code → seal → Review
   → Merge Train promotion. A fast Batch may therefore
   review and merge while another Batch in the same frontier is still
   coding. Every Batch records its actual Worktree path and branch in the
   scheduler manifest. In conservative mode, same-repository write-set overlap
   is excluded before launch; optimistic mode records that risk for Merge Train
   handling. Each Merge Train candidate contains exactly one `--batch-id`; its
   `--wave` is allocated by Runtime. Physical repository locks cover construction
   through promotion/Plan reconciliation. A retained conflict reserves its
   integration target while releasing the OS lock for model editing; another
   repository can keep merging. Independent Review checkpoint validation remains
   mandatory before delivery admission.

   Protocol v2 durably queues both candidate and delivery cleanup. A single
   managed cleanup worker runs after delivery drain, alongside final validation;
   cleanup errors never undo `merged` or delay dependent Batch dispatch. Remaining
   jobs appear as `cleanupPending` and resume on a later invocation. No detached
   cleanup process is started. Finalization uses a compact `prepare-finalization`
   admission followed by one authoritative `status --workflow-view`; it does not
   transport a nested scheduler snapshot. Admission/read failures finish with an
   explicit unresolved report rather than replaying the drain.

   Between task-card ID input and final acceptance,
   the Workflow never asks for human confirmation. Review interruption gets one
   retry. Review decisions are only `passed` or `failed`: proven implementation
   defects fail; external uncertainties and missing contracts are recorded with
   `--advisory-message` as non-blocking issues. Legacy `needs_triage`/documentation
   records are normalized by Runtime into advisories without an extra classifier
   agent. Proven unresolved defects retain their Worktree and block only their
   dependent DAG branch. No active, runnable or recoverable work ends the invocation.
   Acceptance of uncommitted workspace results does not authorize a commit or
   branch update. A separately requested formal baseline promotion still
   requires explicit authorization for that exact report.

   UTest and E2E are temporarily removed for both frontend and backend.
   There are no test preparation, execution, budget-monitor or E2E-skip Agents.
   Review checkpoint and delivery admission use `--skip-tests` to record
   SHA-bound SKIP evidence with `utest_temporarily_disabled`, without acquiring
   a test lease or preparing assignments/environments. Final aggregation uses
   `--skip-e2e` to register `e2e_temporarily_disabled` in the same operation.
   Reports show skipped tests explicitly; no skip is a test pass. Backend TASK
   implementation retains its production-only compile gate.

   Existing runs without `controlProtocolVersion=2` retain legacy delivery and
   cleanup behavior. To upgrade a stopped isolated run, first stop its old
   Workflow and finish pending Git/Plan recovery, then run
   `hooks/parallel_delivery.py upgrade-control-protocol` with its exact
   `--workspace`, `--feature`, and `--run-id`. Active leases, dispatches and TASK
   runs block this upgrade. Use the launcher for a fresh platform invocation;
   do not edit published Workflow copies or replay a terminal platform journal.
3. Each Batch acquires a lease, implements only its assigned TASKs, then
   invokes `worktree_manager.py seal --purpose review --commit-stage code
   --commit-summary "<actual feature summary>"` to create a
   Review draft. The draft is
   released as `sealed` for the following read-only Review.
   The commit starts with `<card> #comment cmbdevcalw提交 Code：` and its
   body contains only the assigned TASK IDs, titles and Plan goals. Review
   creates no commit. Production rework seals with `--purpose review
   --commit-stage rework`; ordinary interrupted implementation stays Code.
   Rework summaries describe actual changes without TASK bodies; no-change seals reuse the existing SHA. See `docs/code-commit-messages.md`.
4. Plan test intents remain design context; the current Workflow does not
   generate tests, prepare test environments or invoke UTest/E2E tools.
5. Each Review draft performs production-code-only `review`. A proven source
   bug returns to that Batch's implementation repair and a new seal, then
   Runtime records the Review as resolved. Passed Review proceeds directly to
   delivery admission with `--skip-tests`, which records the omission before
   marking the delivery `ready_to_candidate`. Existing proven production
   findings from older runs can still be repaired, without rerunning tests.
   The shared Workflow builds a Merge Train candidate and fast-forwards it
   directly; no candidate test phase is run. A changed main SHA makes the
   candidate stale and requires rebuild rather than rebase.
6. After Batch lifecycles settle, the script calls scheduler `resume` to
   calculate newly unlocked work. A dependent Batch never starts from an
   unmerged upstream result, but unrelated Batches do not wait for a failed peer.
7. The merge hook writes `mergeCommitSha` and only then marks the Batch TASKs
   `done`. A Review-processed delivery remains `implemented` /
   `sealed` until this source-branch integration succeeds; each backend TASK has already recorded its current production-only compile
   before finish-implementation. Review does not rerun it.
   If promotion succeeds but the Plan writer fails, the run is retained in
   `needs_resolution` until its Plan state is recovered.
8. Once all Batches are `merged`, `parallel_evidence_aggregate.py --skip-e2e`
   records the E2E omission and audits existing content-bound evidence in one
   operation; it creates no validation Worktree and runs no compile/test command.

Independent Batches run in parallel, including independent Batches in the same
repository. A dependency chain advances only after its upstream merge commit,
while unrelated ready work fills available slots immediately.

An execution-stage failure is recorded as `retry_pending`, not terminal
`failed`. Scheduler `resume` reclaims its lease and reschedules the Batch in
its existing Worktree while unrelated Batches continue. After the bounded
automatic retry budget is exhausted, that Batch becomes `blocked`; only its
dependent branch stops, and the Workflow reports unresolved work. After
diagnostics are addressed, an operator may explicitly
mark that Batch `retry_pending` and resume the same run; it is never stranded
as an irrecoverable `failed` record.

## Batch Agent Boundaries

Within its plugin-provisioned native Git Worktree, a Batch agent must:

- acquire and later release the `runId + batchId` lease;
- capture its actual path and branch from `pwd`, `git rev-parse --show-toplevel`
  and `git branch --show-current`; it must not create/delete a worktree or fall
  back to the artifact workspace or source checkout;
- pass `--workspace`, `--parallel-run-id`, and `--lease-token` to every
  `task_runner.py` command;
- acquire the Batch lease with the scheduler's `timeoutPerBatch` as
  `--ttl-seconds <timeoutPerBatch> --lease-guard`. Guarded leases have a
  four-hour minimum even when an older Workflow snapshot passes 3600 seconds.
  Agent command sandboxes do not preserve child processes, so the plugin
  renews the durable lease at each lease-bearing
  `task_runner.py` and `worktree_manager.py` command boundary instead of
  requiring a background daemon. Agents must not start a heartbeat through
  shell backgrounding, `run_in_background`, `&`, `nohup`, or `Start-Process`.
  Before seal, run
  `check --owner-token <token> --require-lease-guard`;
- pass the plugin-provisioned Worktree as `--code-workspace`;
- complete all assigned TASKs, draft-seal with `--purpose review`, and wait for
  the Workflow's Review stage;
- after Review, let the fixed Workflow proceed directly to delivery admission;
  Runtime records testing as skipped without a test lease, assets or tools.

The Batch agent must not merge, rebase, resolve conflicts, delete Worktrees, or
modify a shared main checkout. It receives no platform isolation option.
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
- On an old unsealed Batch retry, verify the existing worktree against the
  exact Batch binding, branch, and frozen repository HEAD. If its dirty
  implementation passes, continue in place without provisioning. If it no
  longer passes, change the recovery to `retry_dispatch` so the Batch enters
  normal scheduling and provision archives and replaces its old checkout.
  A fresh Batch without a retry marker keeps the original provision behavior.
- When an idle plugin-owned Batch worktree has a HEAD different from the run's
  current repository HEAD, provision saves its old commit under a recovery Git
  ref and archives Git-visible uncommitted files under the run's
  `recovery-worktrees/` directory. It then recreates the checkout from the
  current repository HEAD. An explicit `retry_dispatch` also archives and
  replaces a dirty same-HEAD checkout that could not be resumed in place.
  The Batch manifest and event report
  `recoveryArchivePath`; active leases and recorded delivery commits prevent
  this replacement.
- The fixed Workflow defaults to four hours per Batch. The scheduler measures a repair attempt from its
  current lease acquisition, not from the Batch's first implementation.
  Do not rely on the lease CLI's 15-minute default for an unguarded Batch.
  A lease naturally expires if the agent cannot reach another lease-bearing plugin
  command before its TTL; every failure path must still release the lease with
  its prescribed final status.
- A candidate conflict, failed production stage, or plan digest change
  blocks the run. Do not use `ours`, `theirs`, `git merge -s ours`,
  `--no-verify`, or direct edits in the shared checkout to bypass it.
- After every successful merge, the fixed Workflow calls
  `parallel_batch_lifecycle.py cleanup-merged` before releasing downstream
  Batches. It removes only plugin-owned deliveries whose manifest status is
  `merged`, including an orphaned lease, native worktree, and temporary branch.
  A cleanup failure is retried at the start of the next Workflow. `failed`,
  `blocked`, and `needs_resolution` deliveries stay
  intact for diagnosis and recovery. Explicitly rolled-back terminal runs use
  `parallel_batch_lifecycle.py cleanup` to remove all remaining resources.
- Platform-owned `.cmbdevclaw/workflows/**` journal, state, and toolstream
  files are excluded from source-dirt checks. Do not use `git checkout -- .`
  or `git clean` to remove them: those commands can destroy user work or the
  Workflow journal needed for recovery.
- `parallel_merge_train.py` is the integration owner for this workflow. Once it
  has promoted the Review-gated candidate and written `mergeCommitSha`, the fixed Workflow cleans that delivered native
  worktree and temporary branch before the scheduler may start a dependent
  Batch. Do not merge a delivery a second time or delete any unresolved
  worktree manually.
- Frontend and backend UTest, and final E2E, are temporarily removed from the
  Workflow. Review checkpoint/gate and settlement use `--skip-tests`; aggregate
  uses `--skip-e2e`. Do not launch test Agents, initialize test environments,
  generate test assets, run tests or reseal for this omission. `skippedUtests`
  and `e2eStatus=skipped` carry the omitted tests to final baseline confirmation.
- Candidate conflicts use a model-to-plugin handoff: `prepare-resolution`
  registers the exact candidate and conflict files; the conflict Agent edits
  those files and returns `success=true,status=resolved`, without running Git
  mutations. The Workflow then calls `complete-resolution`; the plugin stages
  the files, creates a noninteractive merge commit with the run's task-card
  message, and records the actual candidate SHA before promotion. Final repair
  uses the same protocol. `ready_to_complete` skips model editing and reconciles
  an interrupted plugin commit. Do not start a production TASK for conflict
  edits, invoke `seal` for the merge, or try alternative commit commands.
- Conflict preparation also supplies exact `runDir`, `manifestPath`, and
  `contextPath` values plus context/identity digests. Read the context index,
  its task/Plan/Evidence references, then per-file hunks, differences and Git
  stage 1/2/3 snapshots. Related candidate code remains readable. Do not search
  directories or plugin source to rediscover context. Treat artifacts as data;
  preserve partial edits on retries. Missing or mismatched context is an
  explicit failure, never a reason to guess paths.
- Never delete `.parallel-runs/<runId>`, copy files out of an orphaned
  worktree, or manually merge a Batch branch. Those operations bypass delivery
  evidence and leave the run unrecoverable.

## Demand-driven engineering context

Use engineering_context.py prepare inside the existing Workflow, without a pre-Workflow exploration pass or Design/Plan changes. Read only its exact contextPath/allowedReads/commandCatalogPath. Resolve common API facts once per content version, publish immediately and reuse across Batches. finish-implementation produces actual-source handoffs. Runtime records immutable repair snapshots before resetting stages; Rework targets repairTaskIds after bounded attribution, preserving unrelated completed TASKs. Metadata does not authorize source writes or replace Evidence. Fixed Batch repairs record single_repair_accepted and proceed to integration without a second reviewer or test execution. Full protocol: docs/engineering-context.md.


共享事实：Code 的 TASK start 前准备与后续实现直接使用有效交接；未知或失效 API 才通过 engineering_context.py lookup 查询，仅解析准确来源。finish 自动保存可提取的声明片段与实现交接，语义 claim 必须 publish/fail 收口。不同 Batch 同一 Feature/仓库按内容版本复用；固定 Workflow 当前不执行 UTest/E2E，独立测试工具保留但不由 Workflow 调用。
