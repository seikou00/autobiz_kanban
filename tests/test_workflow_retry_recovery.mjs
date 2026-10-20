import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");

function snapshot(generation, status, observedEpochMs) {
  const ready = status === "pending" ? ["B002"] : [];
  const batch = (state, gen) => ({
    status: state, dispatchGeneration: gen, dependencies: [],
    worktreePath: "/worktrees/B002", branchName: "batch", commitSha: null, mergeCommitSha: null,
    recovery: { status: state === "retry_pending" ? "pending_retry" : "rescheduled", retryAttempts: 1 },
  });
  return {
    runId: "run", status: "running", maxParallel: 5, activeWorkers: 1, observedEpochMs,
    scheduledGroups: ready.length ? [ready] : [], readyBatches: ready, allReadyBatches: ready,
    mergeableBatches: [], implementationRecoveryBatches: [], stageRecoveryBatches: [],
    retryPendingBatches: status === "retry_pending" ? ["B002"] : [],
    blockedBatches: status === "blocked" ? ["B002"] : [],
    parallelGroups: ready.length ? [ready] : [], allParallelGroups: ready.length ? [ready] : [],
    dispatchGrants: { B001: 1, B002: generation }, dispatchDiagnostics: { activeBatchIds: ["B001"] },
    batchTaskIds: { B001: ["T001"], B002: ["T004"] },
    batchWorkspaces: { B001: { workspaceRef: "default" }, B002: { workspaceRef: "default" } },
    manifest: {
      batches: { B001: batch("running", 1), B002: batch(status, generation) },
      mergeTrains: {}, deferredIssues: [], skippedUtests: [],
    },
  };
}

function harness() {
  const c = vm.createContext({
    pluginPath: "/plugin", schedulerPath: "/plugin/hooks/parallel_batch_scheduler.py",
    artifactWorkspace: "/artifacts", feature: "feature", runId: "run", maxParallel: 5,
    timeoutPerBatch: 3600, workflowInvocationId: "a".repeat(32), schedulerCycles: 0,
    initialRetryRecoveryFailure: null, schedulerReadInFlight: null, lastScheduler: null,
    activeDispatchGrants: new Map(), dispatchGrants: {}, retryPriorityBatchIds: new Set(),
    quarantinedBatchIds: new Set(), quarantinedBatchGenerations: new Map(),
    unresolvedRecords: [], unresolvedRecordKeys: new Set(), lifecycleResults: [], schedulerFailures: [],
    scheduledGroups: [], mergeableBatches: [], implementationRecoveryBatches: [],
    stageRecoveryBatches: [], retryPendingBatches: [], blockedBatches: [], batchTaskIds: {}, batchWorkspaces: {},
  });
  const load = (start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, start);
    vm.runInContext(source.slice(from, to), c);
  };
  vm.runInContext(source.slice(0, source.indexOf("const input = unwrap(args);")).replace("export const meta =", "const meta ="), c);
  load("const WORKFLOW_AUTONOMY_PREFIX", "const aggregatePath");
  load("function normalizeScheduledGroups(", 'phase("准备");');
  load("let retryRecoveryInFlight", "let initialRetryRecoveryFailure");
  load("function errorText(", "async function deferBatchForRetry(");
  load("async function runLifecycleSafely(", "async function drainRunnableLifecycles(");
  return c;
}

test("a normal status snapshot of the new grant releases the previous failure quarantine", () => {
  const c = harness();
  c.applySchedulerState(snapshot(1, "retry_pending", 1000));
  assert.equal(c.quarantinedBatchIds.has("B002"), true);
  // A different worker performed resume; status does not contain rescheduledRetryBatches.
  c.applySchedulerState(snapshot(2, "pending", 2000));
  const job = c.takeNextRunnableLifecycle(new Set(["B002:1"]), new Set(["B001"]));
  assert.equal(job?.batchId, "B002");
  assert.equal(job.generation, 2);
});

test("a late failure from generation one cannot quarantine the rescheduled generation two", () => {
  const c = harness();
  c.applySchedulerState(snapshot(2, "pending", 2000));
  c.activeDispatchGrants.set("B002", { generation: 1, ownerToken: "old-owner" });
  c.observeLifecycleResult("B002", "initial", { status: "retry_pending", durable: true, reason: "old execution stopped" }, 1);
  assert.equal(c.quarantinedBatchIds.has("B002"), false);
  assert.equal(c.activeUnresolvedRecords().length, 0);
  assert.equal(c.takeNextRunnableLifecycle(new Set(["B002:1"]), new Set(["B001"]))?.generation, 2);
});

test("an old retry-pending read cannot roll back the current dispatch grant", () => {
  const c = harness();
  c.applySchedulerState(snapshot(2, "pending", 2000));
  c.applySchedulerState(snapshot(1, "retry_pending", 1000));
  assert.equal(c.dispatchGrants.B002, 2);
  assert.equal(c.lastScheduler.manifest.batches.B002.status, "pending");
  assert.equal(c.takeNextRunnableLifecycle(new Set(["B002:1"]), new Set(["B001"]))?.generation, 2);
});

test("an in-flight old status read returns the accepted new snapshot to its caller", async () => {
  const c = harness(), old = snapshot(1, "retry_pending", 1000);
  c.applySchedulerState(old);
  let finishOldRead;
  c.agent = () => new Promise(resolve => { finishOldRead = resolve; });
  const reading = c.readSchedulerState("old-status");
  c.applySchedulerState(snapshot(2, "pending", 2000));
  finishOldRead(old);
  const result = await reading;
  assert.equal(result.dispatchGrants.B002, 2);
  assert.equal(result.retryPendingBatches.length, 0);
  assert.equal(c.quarantinedBatchIds.has("B002"), false);
});

test("the status schema preserves Runtime skipped UTest records", async () => {
  const c = harness(), current = snapshot(2, "pending", 2000);
  current.manifest.skippedUtests = [{
    batchId: "B001", executionLane: "frontend", status: "skipped",
    reason: "frontend_utest_skipped", stageEvidenceId: null,
  }];
  c.agent = async (_, options) => {
    const schema = options.schema.properties.manifest;
    const projected = Object.fromEntries(Object.entries(current.manifest).filter(([field]) => field in schema.properties));
    return { ...current, manifest: projected };
  };
  const result = await c.readSchedulerState("status-with-skipped-utests");
  assert.deepEqual(result.manifest.skippedUtests, current.manifest.skippedUtests);
});

test("single-repair errors remain errors and successful records require their full binding", async () => {
  const c = harness();
  c.stagePath = "/plugin/hooks/parallel_batch_stage.py";
  const start = source.indexOf("async function recordSingleRepairResolution(");
  vm.runInContext(source.slice(start, source.indexOf("async function blockImplementationFinding(", start)), c);
  const delivery = { batchId: "B002", worktreePath: "/worktrees/B002", branchName: "batch", commitSha: "draft" };
  const recovery = { failureContext: { failedStage: "review" } };
  const valid = { ok: true, success: true, batchId: "B002", failedStage: "review", status: "success",
    stage: "review", stageEvidenceId: "review-ev", stages: [{ stage: "review", status: "passed" }] };
  c.agent = async () => ({ ok: false, error: "repair record rejected" });
  await assert.rejects(c.recordSingleRepairResolution(recovery, delivery), /repair record rejected/);
  for (const field of ["batchId", "failedStage", "status", "stage", "stageEvidenceId", "stages"]) {
    const incomplete = { ...valid };
    delete incomplete[field];
    c.agent = async () => incomplete;
    await assert.rejects(c.recordSingleRepairResolution(recovery, delivery), /parallel_single_repair_result_incomplete/);
  }
  c.agent = async () => valid;
  assert.equal((await c.recordSingleRepairResolution(recovery, delivery)).stageEvidenceId, "review-ev");
});

test("a failure of the current generation stays quarantined until a newer grant arrives", () => {
  const c = harness();
  c.applySchedulerState(snapshot(2, "pending", 2000));
  c.observeLifecycleResult("B002", "initial", { status: "retry_pending", durable: true, reason: "current failure" }, 2);
  c.applySchedulerState(snapshot(2, "pending", 2500));
  assert.equal(c.quarantinedBatchIds.has("B002"), true);
  assert.equal(c.takeNextRunnableLifecycle(new Set(), new Set(["B001"])), null);
  c.applySchedulerState(snapshot(2, "blocked", 3000));
  assert.equal(c.quarantinedBatchIds.has("B002"), true);
  assert.equal(c.takeNextRunnableLifecycle(new Set(), new Set(["B001"])), null);
});

test("B002 reaches implementation despite a late old failure while B001 is still active", async () => {
  const c = harness(), events = [];
  let current = snapshot(1, "retry_pending", 1000);
  c.applySchedulerState(current);
  c.agent = async (_, options) => {
    if (options.label.endsWith("resume-retry-pending")) {
      events.push("resume:B002:2");
      current = snapshot(2, "pending", 2000);
      return { ...current, rescheduledRetryBatches: ["B002"] };
    }
    if (options.label.endsWith("after-retry-resume-1")) {
      // The old worker's defer result arrives after another worker resumed it.
      c.observeLifecycleResult("B002", "initial", { status: "retry_pending", durable: true, reason: "late old failure" }, 1);
    }
    return current;
  };
  c.dispatchCommand = async (action, job) => {
    events.push(`${action}:${job.batchId}:${job.generation}`);
    return { success: true, status: action === "claim" ? "claimed" : "finished", batchId: job.batchId, ownerToken: "new-owner" };
  };
  c.runInitialBatchLifecycle = async batchId => {
    assert.equal(c.lastScheduler.activeWorkers, 1);
    assert.deepEqual(Array.from(c.lastScheduler.dispatchDiagnostics.activeBatchIds), ["B001"]);
    assert.equal(c.activeDispatchGrants.get(batchId).generation, 2);
    events.push(`implement:${batchId}:T004`);
    current = snapshot(2, "merged", 3000);
    current.activeWorkers = 0;
    current.dispatchDiagnostics.activeBatchIds = [];
    return { batchId, status: "merged" };
  };
  c.waitForDispatchChange = async () => assert.fail("the ready retry must start before waiting for B001");
  await c.runDispatchWorker(2, new Set(["B002:1"]), "drain", { active: 0, ranAny: false, runningBatchIds: new Set(["B001"]) });
  assert.deepEqual(events, ["resume:B002:2", "claim:B002:2", "implement:B002:T004", "finish:B002:2"]);
});
