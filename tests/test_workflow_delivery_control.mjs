import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

async function execute(frontend = false, fault = null) {
  const labels = [];
  const pools = [];
  const delivery = { batchId: "B001", worktreePath: "/worktree", branchName: "batch", commitSha: "sealed" };
  const workspace = { workspaceRef: "default", componentRoots: ["."], executionStage: "parallel",
    requestedPath: "/source", worktreePath: "/worktree", branchName: "batch" };
  let revision = 1;
  let queuedOnce = false;
  let syncCompletions = 0;
  let workspaceState = fault && fault.startsWith("workspace-") ? {
    success: true, status: fault === "workspace-synced" ? "synced" : "conflicted", repositoryRef: "default",
    sourcePath: "/source", sourceBranch: "main", sourceHead: "original", lastSyncedSha: fault === "workspace-synced" ? "candidate" : null,
    targetSha: "candidate", worktreePath: "/sync-worktree", backupPath: "/sync-backup",
    contextPath: "/sync-backup/conflicts.json", jobId: "job-1", attemptId: "c".repeat(32), conflictedFiles: ["file.txt"]
  } : null;
  let reviewAdvisoryRecorded = false;
  const advisory = { issueId: "REVIEW-ADVISORY-B001-1", kind: "review_advisory", batchId: "B001", stage: "review",
    failureType: "external_uncertainty", message: "External API behavior is undocumented", disposition: "recorded_advisory",
    blocksWorkflow: false, status: "open", batchCommit: "sealed" };
  const snapshot = merged => ({
    success: true, runId: "run", status: "running", controlProtocolVersion: 2, controlRevision: revision++,
    observedEpochMs: revision * 1000, maxParallel: 3, activeWorkers: 0, integrationMode: "isolated",
    scheduledGroups: merged ? [] : [["B001"]], readyBatches: merged ? [] : ["B001"],
    allReadyBatches: merged ? [] : ["B001"], mergeableBatches: [],
    implementationRecoveryBatches: [], stageRecoveryBatches: [], retryPendingBatches: [], blockedBatches: [],
    parallelGroups: [], allParallelGroups: [], dispatchGrants: { B001: 1 },
    batchTaskIds: { B001: ["T001"] }, batchWorkspaces: { B001: workspace }, cleanupPending: [],
    ...(workspaceState && merged ? { workspaceSync: [workspaceState] } : {}),
    manifest: { batches: { B001: { ...delivery, dispatchGeneration: 1, status: merged ? "merged" : "pending",
      dependencies: [], mergeCommitSha: merged ? "candidate" : null, recovery: {} } },
      mergeTrains: {}, deferredIssues: reviewAdvisoryRecorded ? [advisory] : [], skippedUtests: [] },
  });
  let releaseCleanup;
  const cleanupMayFinish = new Promise(resolve => { releaseCleanup = resolve; });
  let cleanupStarted = false;
  const agent = async (prompt, options) => {
    const label = options.label;
    labels.push(label);
    if (label === "verify-engineering-deployment") return { success: true };
    if (label === "fixed-workflow-prepare") {
      const state = snapshot(false);
      if (fault === "sealed-recovery") {
        state.scheduledGroups = [];
        state.readyBatches = state.allReadyBatches = [];
        state.stageRecoveryBatches = [{ ...delivery, recoveryKind: "stage_resume", nextStage: "delivery",
          preserveWorktree: true, reprovision: false }];
        state.manifest.batches.B001.status = "sealed";
      }
      return state;
    }
    if (label.startsWith("dispatch-claim-")) return { success: true, batchId: "B001", status: "claimed", ownerToken: "owner", generation: 1 };
    if (label === "provision-worktree-B001") return { success: true, ...delivery, repositoryRef: "default" };
    if (label === "fixed-batch-B001") return { ...delivery, status: "success",
      commitSha: fault === "stale-sha" ? "transcribed-wrong" : delivery.commitSha };
    if (label === "stage-review-B001-attempt-1") {
      reviewAdvisoryRecorded = fault === "advisory";
      return { success: true, batchId: "B001", stage: "review", status: "passed" };
    }
    if (label === "validate-review-B001-attempt-1") {
      assert.doesNotMatch(prompt, /--skip-tests/);
      return { success: true, batchId: "B001", stage: "review", status: "passed" };
    }
    if (label === "settle-delivery-B001-1") {
      assert.equal(options.timeoutMs, 4 * 60 * 60 * 1000);
      assert.doesNotMatch(prompt, /frontend_recheck|frontend_verification/);
      assert.ok(options.schema.properties.reviewScope);
      assert.doesNotMatch(prompt, /--expected-commit\s+"/);
      assert.doesNotMatch(prompt, /--skip-tests/);
      if (fault === "queued" && !queuedOnce) {
        queuedOnce = true;
        const state = snapshot(false);
        state.scheduledGroups = [];
        state.readyBatches = state.allReadyBatches = [];
        state.mergeableBatches = ["B001"];
        state.manifest.batches.B001.status = "ready_to_candidate";
        state.manifest.batches.B001.dispatch = { status: "waiting", generation: 1 };
        return { success: true, status: "queued", batchId: "B001", runId: "run", dispatchGeneration: 1,
          deliverySha: "sealed", dispatchFinalized: false, dispatchReleased: true,
          blockingBatchIds: [], operationId: "operation", schedulerSnapshot: state };
      }
      return { success: true, status: "merged", batchId: "B001", runId: "run", dispatchGeneration: 1,
        deliverySha: "sealed", mergeCommitSha: "candidate", dispatchFinalized: true,
        repositoryRef: "default", wave: 1, operationId: "operation", schedulerSnapshot: snapshot(true),
        ...(workspaceState ? { workspaceSync: workspaceState } : {}) };
    }
    if (label.startsWith("workspace-sync-resolve-")) {
      assert.match(prompt, /仅允许编辑临时 Worktree "\/sync-worktree"/);
      assert.match(prompt, /不得修改用户目录 "\/source"/);
      assert.match(prompt, /无须人工确认/);
      return fault === "workspace-unresolved" ? { success: false, status: "needs_resolution", error: "unresolved" }
        : { success: true, status: "resolved" };
    }
    if (label.startsWith("workspace-sync-complete-")) {
      assert.match(prompt, /parallel_workspace_sync\.py" complete-job/);
      assert.match(prompt, /不移动用户分支 HEAD、不改用户暂存区、不提交代码/);
      workspaceState = { ...workspaceState, status: fault === "workspace-refresh" && syncCompletions++ === 0
        ? "refresh_required" : "synced" };
      if (workspaceState.status === "synced") workspaceState.lastSyncedSha = "candidate";
      return workspaceState;
    }
    if (label.startsWith("workspace-sync-warning-")) {
      workspaceState = { ...workspaceState, success: false, status: "failed", error: "publication failed" };
      return workspaceState;
    }
    if (label.startsWith("workspace-sync-")) {
      if (!workspaceState) return { success: true, status: "skipped", repositoryRef: "default" };
      if (workspaceState.status === "synced") return workspaceState;
      assert.match(prompt, /parallel_workspace_sync\.py" process/);
      if (fault === "workspace-agent-timeout") throw new Error("workspace sync agent timed out after merge");
      if (fault === "workspace-command-error") return { success: false, repositoryRef: "default", status: "pending", error: "Git LFS \ufffd\ufffd" };
      workspaceState = { ...workspaceState, status: "conflicted" };
      return workspaceState;
    }
    if (label === "prepare-delivery-finalization") {
      if (fault === "admission") throw new Error("finalization unavailable");
      return fault === "blocked"
        ? { success: true, status: "repair_required", repairRequired: true, batchIds: ["B001"] }
        : { success: true, status: "ready", repairRequired: false, batchIds: [],
          ...(workspaceState ? { workspaceSync: [workspaceState] } : {}) };
    }
    if (label.startsWith("final-repair-") || label === "final-report-snapshot") {
      if (fault === "snapshot") return {};
      const current = snapshot(true);
      if (fault === "blocked") {
        current.status = "blocked";
        current.blockedBatches = ["B001"];
        current.manifest.batches.B001.status = "blocked";
        current.manifest.batches.B001.mergeCommitSha = null;
        current.manifest.batches.B001.recovery = { status: "unchanged_failure", lastError: "unresolved finding" };
      }
      return current;
    }
    if (label === "aggregate-staged-evidence") {
      assert.match(prompt, /--finish-workflow/);
      assert.doesNotMatch(prompt, /--skip-e2e/);
      assert.ok(options.schema.properties.workspaceSync);
      assert.ok(options.schema.properties.repositoryHeads);
      // Cleanup can be waiting independently while final validation advances.
      await Promise.resolve();
      assert.equal(cleanupStarted, true);
      releaseCleanup();
      return { ok: true, policy: "report_only", passed: true, hasIssues: fault === "advisory",
        errors: [], blockingErrors: [], integrationMode: "isolated",
        workspaceSync: workspaceState ? [workspaceState] : [], repositoryHeads: { default: "candidate" },
        e2eStatus: "skipped", e2eSkipReason: "e2e_temporarily_disabled",
        skippedUtests: [{ batchId: "B001", executionLane: frontend ? "frontend" : "backend",
          status: "skipped", reason: "utest_temporarily_disabled" }] };
    }
    if (label === "cleanup-delivered-B001") {
      assert.match(prompt, /--budget-seconds 20/);
      if (fault === "cleanup-error") throw new Error("initial cleanup failed");
      if (fault === "cleanup-pending") return { success: true, cleanupPending: [{ jobId: "delivery", status: "retryable" }] };
      return { success: true, cleanupPending: [], cleanedJobIds: ["candidate", "delivery"] };
    }
    if (label === "complete-code-checkpoint") {
      assert.match(prompt, /update_checkpoint\.py" --feature "f" --checkpoint code_done --json/);
      assert.ok(labels.includes("aggregate-staged-evidence"));
      return { ok: true, new_checkpoint: "code_done" };
    }
    if (label === "drain-delivery-cleanup") {
      cleanupStarted = true;
      await cleanupMayFinish;
      if (fault === "cleanup-error") throw new Error("cleanup lock busy");
      if (fault === "cleanup-pending") return { success: true, cleanupPending: [{ jobId: "delivery", status: "retryable" }] };
      return { success: true, cleanupPending: [], cleanedJobIds: ["candidate", "delivery"] };
    }
    throw new Error(`unexpected control call: ${label}`);
  };
  const run = new AsyncFunction("args", "agent", "parallel", "phase", source.replace("export const meta =", "const meta ="));
  const result = await run({ feature: "f", pluginPath: "/plugin", artifactWorkspace: "/artifacts",
    codeWorkspaces: { default: "/source" }, taskCardId: "CARD-1", maxParallel: 9,
    workflowInvocationId: "a".repeat(32), runtimeContractDigest: "b".repeat(64) }, agent,
  callbacks => { pools.push(callbacks.length); return Promise.all(callbacks.map(callback => callback())); }, () => {});
  assert.equal(pools[0], 3, "the durable run's capacity overrides changed launch hints");
  return { labels, result };
}

test("nonblocking Review uncertainty survives in the completed report", async () => {
  const { labels, result } = await execute(false, "advisory");
  assert.equal(result.finalStatus, "succeeded_with_issues");
  assert.equal(result.deferredIssues.length, 1);
  assert.equal(result.deferredIssues[0].kind, "review_advisory");
  assert.equal(result.deferredIssues[0].blocksWorkflow, false);
  assert.equal(labels.some(label => label.startsWith("triage-review-")), false);
  assert.ok(labels.includes("aggregate-staged-evidence"));
});

test("settlement uses Runtime SHA even when the implementation agent transcribed it incorrectly", async () => {
  const { result } = await execute(false, "stale-sha");
  assert.equal(result.finalStatus, "succeeded");
});

test("provision contention preserves the Runtime reason when deferring a Batch", async () => {
  const busy = { success: false, batchId: "B001", error: "parallel_worktree_provision_lock_busy",
    retryable: true, retryAfterSeconds: 3, readiness: { failureKind: "run_lock_contention" } };
  let reason;
  const c = vm.createContext({
    batchWorkspaces: { B001: { workspaceRef: "default" } }, batchTaskIds: { B001: ["T001"] },
    codeWorkspaces: { default: "/source" }, artifactWorkspace: "/artifact", feature: "f", runId: "r",
    worktreeManagerPath: "/plugin/hooks/worktree_manager.py",
    workflowAgent: async (_, options) => {
      assert.equal(options.label, "provision-worktree-B001");
      for (const key of Object.keys(busy)) assert.ok(options.schema.properties[key]);
      return busy;
    },
    safelyDeferBatchForRetry: async (batchId, _path, _branch, error) => {
      reason = error;
      return { batchId, status: "retry_pending" };
    }
  });
  vm.runInContext(source.slice(0, source.indexOf("function requireSchedulerResult(")).replace("export const meta =", "const meta ="), c);
  vm.runInContext(source.slice(source.indexOf("async function runInitialBatchLifecycle("),
    source.indexOf("async function runImplementationRecoveryLifecycle(")), c);
  const result = await c.runInitialBatchLifecycle("B001");
  assert.equal(result.status, "retry_pending");
  assert.match(reason, /parallel_worktree_provision_lock_busy/);
  assert.match(reason, /"retryable":true/);
});

test("queued delivery returns to dispatch without failure or consuming its grant", async () => {
  const { labels, result } = await execute(false, "queued");
  assert.equal(result.finalStatus, "succeeded", JSON.stringify({ labels, result }));
  assert.equal(labels.filter(label => label === "dispatch-claim-B001-1").length, 2);
  assert.equal(labels.filter(label => label === "settle-delivery-B001-1").length, 2);
  assert.equal(labels.some(label => /defer-batch|dispatch-finish/.test(label)), false);
  assert.equal(labels.filter(label => label === "stage-review-B001-attempt-1").length, 1);
});

test("slow settlements queue by repository and reuse an in-flight Batch", async () => {
  const started = [];
  const complete = new Map();
  const control = { inFlight: new Map(), repositoryTails: new Map() };
  const c = vm.createContext({
    activeDispatchGrants: new Map(["B001", "B002", "B003"].map(id => [id, { generation: 1 }])),
    batchWorkspaces: { B001: { workspaceRef: "a" }, B002: { workspaceRef: "a" }, B003: { workspaceRef: "b" } },
    deliveryControl: control,
    usableString: value => typeof value === "string" && value.length > 0,
    executeDeliverySettlement: batchId => {
      started.push(batchId);
      return new Promise(resolve => complete.set(batchId, () => resolve({ batchId, status: "merged" })));
    }
  });
  vm.runInContext(source.slice(source.indexOf("async function settleReadyBatch("),
    source.indexOf("async function executeDeliverySettlement(")), c);
  const first = c.settleReadyBatch("B001");
  const duplicate = c.settleReadyBatch("B001");
  const second = c.settleReadyBatch("B002");
  const independent = c.settleReadyBatch("B003");
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(started, ["B001", "B003"]);
  complete.get("B001")();
  assert.equal(await first, await duplicate);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(started, ["B001", "B003", "B002"]);
  complete.get("B002")();
  complete.get("B003")();
  await Promise.all([second, independent]);
  assert.equal(control.inFlight.size, 0);
  assert.equal(control.repositoryTails.size, 0);
});

test("a failed settlement releases its repository queue", async () => {
  const c = vm.createContext({
    activeDispatchGrants: new Map([["B001", { generation: 1 }], ["B002", { generation: 1 }]]),
    batchWorkspaces: { B001: { workspaceRef: "a" }, B002: { workspaceRef: "a" } },
    deliveryControl: { inFlight: new Map(), repositoryTails: new Map() },
    usableString: Boolean,
    executeDeliverySettlement: async batchId => {
      if (batchId === "B001") throw new Error("interrupted");
      return { batchId, status: "merged" };
    }
  });
  vm.runInContext(source.slice(source.indexOf("async function settleReadyBatch("),
    source.indexOf("async function executeDeliverySettlement(")), c);
  const first = c.settleReadyBatch("B001");
  const second = c.settleReadyBatch("B002");
  await assert.rejects(first, /interrupted/);
  assert.equal((await second).status, "merged");
});

test("sealed delivery recovery goes straight to settlement without reimplementation or Review", async () => {
  const { labels, result } = await execute(false, "sealed-recovery");
  assert.equal(result.finalStatus, "succeeded", JSON.stringify({ labels, result }));
  assert.equal(labels.some(label => /fixed-batch|provision-worktree|stage-review|validate-review/.test(label)), false);
  assert.ok(labels.includes("settle-delivery-B001-1"));
});

for (const fault of ["workspace-synced", "workspace-conflict", "workspace-refresh", "workspace-unresolved", "workspace-command-error", "workspace-agent-timeout"]) {
  test(`${fault} delivers uncommitted workspace results without failing an already merged Batch`, async () => {
    const { labels, result } = await execute(false, fault);
    const pending = ["workspace-unresolved", "workspace-command-error", "workspace-agent-timeout"].includes(fault);
    assert.equal(result.finalStatus, pending ? "succeeded_with_issues" : "succeeded", JSON.stringify({ labels, result }));
    assert.equal(result.nextAction, pending ? "resume_workspace_sync" : "continue_next_stage");
    assert.equal(result.workspaceSync.length, 1);
    assert.equal(result.workspaceSyncPending.length, ["workspace-unresolved", "workspace-command-error", "workspace-agent-timeout"].includes(fault) ? 1 : 0);
    assert.equal(labels.some(label => /defer-batch|dispatch-finish|fixed-batch.*2/.test(label)), false);
    assert.equal(labels.filter(label => label === "settle-delivery-B001-1").length, 1);
    if (fault === "workspace-refresh") assert.equal(labels.filter(label => label.startsWith("workspace-sync-complete-")).length, 2);
  });
}

test("post-merge cleanup bypasses defer when Runtime already reported the merge", async () => {
  const c = vm.createContext({ activeDispatchGrants: new Map([["B001", { generation: 1, ownerToken: "token" }]]),
    deliveryControl: { finalized: new Map([["B001", 1]]) },
    dispatchCommand: async () => { throw new Error("must not register a retry after merge"); } });
  vm.runInContext(source.slice(source.indexOf("async function deferBatchForRetry("), source.indexOf("function mergedBatchIds(")), c);
  const result = await c.deferBatchForRetry("B001", "/worktree", "branch", "sync failed");
  assert.equal(result.status, "merged");
  assert.equal(result.durable, true);
});

test("a caller timeout keeps a live Runtime delivery queued without registering a retry", async () => {
  const control = { finalized: new Map(), waiting: new Map() };
  const c = vm.createContext({ activeDispatchGrants: new Map([["B001", { generation: 1, ownerToken: "token" }]]),
    deliveryControl: control,
    encodeFailure: String,
    dispatchCommand: async action => {
      assert.equal(action, "defer");
      return { success: true, status: "delivery_running", batchId: "B001" };
    } });
  vm.runInContext(source.slice(source.indexOf("async function deferBatchForRetry("), source.indexOf("function mergedBatchIds(")), c);
  const result = await c.deferBatchForRetry("B001", "/worktree", "branch", "caller timeout");
  assert.equal(result.status, "queued");
  assert.equal(result.durable, true);
  assert.equal(control.waiting.get("B001"), 1);
});

for (const frontend of [false, true]) {
  test(`complete ${frontend ? "frontend" : "backend"} workflow settles without individual control agents`, async () => {
    const { labels, result } = await execute(frontend);
    assert.equal(result.finalStatus, "succeeded", JSON.stringify({ labels, result }));
    assert.equal(result.verification.e2eStatus, "skipped");
    assert.equal(result.skippedUtests, undefined);
    assert.equal(labels.some(label => /utest|e2e/i.test(label)), false);
    assert.equal(result.e2eSkippedReason, undefined);
    assert.equal(labels.includes("begin-e2e-validation"), false);
    assert.equal(labels.includes("run-e2e-validation"), false);
    const chain = labels.filter(label => /^(stage-review|validate-review|stage-utest|settle-delivery)/.test(label));
    assert.equal(chain.length, 3);
    assert.equal(labels.filter(label => label === "prepare-delivery-finalization").length, 1);
    assert.equal(labels.filter(label => label === "final-repair-initial-snapshot").length, 1);
    assert.equal(labels.filter(label => /stage-gate|build-candidate|promote-candidate|dispatch-finish|after-batch|final-report-snapshot|recover-merged/.test(label)).length, 0);
    assert.deepEqual(result.cleanupPending, []);
    assert.ok(labels.indexOf("cleanup-delivered-B001") > labels.indexOf("settle-delivery-B001-1"));
    assert.ok(labels.indexOf("cleanup-delivered-B001") < labels.indexOf("prepare-delivery-finalization"));
    assert.equal(labels.includes("prepare-user-confirmed-promotion"), false);
    assert.equal(result.workflowMetrics.agentCalls, labels.length);
  });
}

for (const fault of ["cleanup-error", "cleanup-pending"]) {
  test(`${fault} is reported without reopening a merged Batch`, async () => {
    const { labels, result } = await execute(false, fault);
    assert.equal(result.finalStatus, "succeeded_with_issues");
    assert.equal(result.ok, true);
    assert.equal(result.nextAction, "continue_next_stage");
    assert.equal(result.verification.codeCheckpoint.new_checkpoint, "code_done");
    assert.equal(labels.filter(label => label === "complete-code-checkpoint").length, 1);
    assert.equal(labels.some(label => /defer-batch|dispatch-finish/.test(label)), false);
    assert.equal(labels.filter(label => label === "settle-delivery-B001-1").length, 1);
  });
}

for (const fault of ["admission", "snapshot", "blocked"]) {
  test(`finalization ${fault} failure reports unresolved work without replaying the drain or asking for confirmation`, async () => {
    const { labels, result } = await execute(false, fault);
    assert.equal(result.finalStatus, "completed_with_unresolved");
    assert.equal(result.ok, false);
    assert.ok(fault === "blocked" ? result.unresolved.batches.length : result.unresolved.schedulerFailures.length);
    assert.equal(labels.filter(label => label === "prepare-delivery-finalization").length, 1);
    assert.equal(labels.includes("prepare-user-confirmed-promotion"), false);
    assert.equal(labels.includes("run-e2e-validation"), false);
    assert.equal(labels.filter(label => label === "dispatch-claim-B001-1").length, 1);
  });
}

test("shared retry waits use bounded backoff and completion cannot be lost before wait registration", async () => {
  const timers = [];
  const c = vm.createContext({ setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout() {} });
  vm.runInContext(source.slice(0, source.indexOf("function normalizeStructuredOutput(")).replace("export const meta =", "const meta ="), c);
  vm.runInContext(source.slice(source.indexOf("function signalDispatchChange("), source.indexOf("function dispatchSnapshotEpoch(")), c);
  for (const [failures, delay] of [[1, 5000], [2, 10000], [3, 20000], [4, 30000], [8, 30000]]) {
    vm.runInContext(`schedulerControlState.available = false; schedulerControlState.failures = ${failures};`, c);
    const state = {};
    const first = c.waitForDispatchChange(state);
    assert.equal(first, c.waitForDispatchChange(state));
    assert.equal(timers.at(-1).ms, delay);
    timers.at(-1).fn();
    assert.equal(await first, "retry");
  }
  const state = {};
  c.signalDispatchChange(state);
  assert.equal(await c.waitForDispatchChange(state, 0), "signal");
});
