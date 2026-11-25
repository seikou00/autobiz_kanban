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

test("a consumed claim refreshes to the next generation before starting delivery", async () => {
  const c = harness(), events = [];
  let current = snapshot(3, "pending", 1000);
  c.applySchedulerState(current);
  c.dispatchCommand = async (action, job) => {
    events.push(`${action}:${job.generation}`);
    if (action === "claim" && job.generation === 3) {
      current = snapshot(4, "pending", 2000);
      return { success: true, status: "not_claimed", batchId: job.batchId, generation: 3, reasonCode: "grant_consumed" };
    }
    return { success: true, status: action === "claim" ? "claimed" : "finished", batchId: job.batchId, ownerToken: "new-owner" };
  };
  c.agent = async () => current;
  c.runInitialBatchLifecycle = async () => {
    events.push(`delivery:${c.activeDispatchGrants.get("B002").generation}`);
    current = snapshot(4, "merged", 3000);
    current.activeWorkers = 0;
    current.dispatchDiagnostics.activeBatchIds = [];
    return { batchId: "B002", status: "merged" };
  };
  c.waitForDispatchChange = async () => assert.fail("a recovered grant should proceed immediately");
  await c.runDispatchWorker(2, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set(["B001"]) });
  assert.deepEqual(events, ["claim:3", "claim:4", "delivery:4", "finish:4"]);
  assert.equal(c.unresolvedRecords.length, 0);
});

test("a rejected claim remains a rejection when the model changes success to false", async () => {
  const c = harness();
  c.agent = async () => ({ success: false, status: "not_claimed", batchId: "B001", generation: 3, reasonCode: "grant_consumed" });
  const result = await c.dispatchCommand("claim", { batchId: "B001", generation: 3 }, "", "claim-test");
  assert.equal(result.status, "not_claimed");
  assert.equal(result.reasonCode, "grant_consumed");
  assert.equal(c.activeDispatchGrants.size, 0);
});

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

test("the status schema drops retired skipped UTest records", async () => {
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
  assert.equal(result.manifest.skippedUtests, undefined);
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

test("repeated model failures recover within 20 attempts and stop at 20 with failure reason", async () => {
  // Case 1: 19 failures followed by success on attempt 20 still merges.
  {
    const c = harness();
    let attempts = 0, generation = 1, epoch = 0;
    const state = status => {
      const value = snapshot(generation, status, ++epoch * 1000);
      value.activeWorkers = 0;
      value.dispatchDiagnostics.activeBatchIds = [];
      return value;
    };
    let current = state("pending");
    c.applySchedulerState(current);
    c.agent = async (_, options) => {
      if (options.label.endsWith("resume-retry-pending")) {
        generation += 1;
        current = state("pending");
        return { ...current, rescheduledRetryBatches: ["B002"] };
      }
      return current;
    };
    c.dispatchCommand = async (action, job) => ({ success: true,
      status: action === "claim" ? "claimed" : "finished", batchId: job.batchId, ownerToken: "owner" });
    c.runInitialBatchLifecycle = async batchId => {
      attempts += 1;
      current = state(attempts < 20 ? "retry_pending" : "merged");
      return { batchId, status: attempts < 20 ? "retry_pending" : "merged", durable: true,
        reason: attempts < 20 ? "empty_agent_output" : undefined };
    };
    c.waitForDispatchChange = async () => assert.fail("ready retries must continue dispatching");
    await c.runDispatchWorker(1, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set() });
    assert.equal(attempts, 20);
    assert.equal(c.lastScheduler.manifest.batches.B002.status, "merged");
    assert.equal(c.activeUnresolvedRecords().length, 0);
  }

  // Case 2: 20 failures stop retrying immediately and surface the failure reason.
  {
    const c = harness();
    vm.runInContext(source.slice(source.indexOf("function manifestFromScheduler("),
      source.indexOf("async function persistUndurableBatchFailures(")), c);
    c.feature = "feature";
    c.batchResults = [];
    c.mergeResults = [];
    c.cleanupResults = [];
    c.finalRepairResults = [];
    c.workspaceSyncControl = { results: new Map(), notifications: [] };
    let attempts = 0, generation = 1, epoch = 0;
    const state = status => {
      const value = snapshot(generation, status, ++epoch * 1000);
      value.activeWorkers = 0;
      value.dispatchDiagnostics.activeBatchIds = [];
      value.manifest.batches.B001 = { status: "merged", mergeCommitSha: "upstream", dependencies: [],
        worktreePath: "/worktrees/B001", branchName: "b1", commitSha: "c1", recovery: {} };
      return value;
    };
    let current = state("pending");
    c.applySchedulerState(current);
    c.agent = async (_, options) => {
      if (options.label.endsWith("resume-retry-pending")) {
        if (attempts >= 20) {
          current = state("blocked");
          current.manifest.batches.B002.error = "empty_agent_output_20";
          current.manifest.batches.B002.recovery = { status: "retry_exhausted", retryAttempts: 20, lastError: "empty_agent_output_20" };
          return { ...current, retryExhaustedBatches: ["B002"], rescheduledRetryBatches: [] };
        }
        generation += 1;
        current = state("pending");
        return { ...current, rescheduledRetryBatches: ["B002"] };
      }
      return current;
    };
    c.dispatchCommand = async (action, job) => ({ success: true,
      status: action === "claim" ? "claimed" : "finished", batchId: job.batchId, ownerToken: "owner" });
    c.runInitialBatchLifecycle = async batchId => {
      attempts += 1;
      assert.ok(attempts <= 20, "must not retry beyond 20 attempts in a single workflow launch");
      current = state("retry_pending");
      return { batchId, status: "retry_pending", durable: true, reason: `empty_agent_output_${attempts}` };
    };
    c.waitForDispatchChange = async () => assert.fail("exhausted batch must stop without idle polling");
    await c.runDispatchWorker(1, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set() });
    assert.equal(attempts, 20);
    assert.equal(c.schedulerCycles, 20);
    const report = c.completionReport({ finalStatus: "completed_with_unresolved", scheduler: c.lastScheduler });
    assert.equal(report.ok, false);
    assert.deepEqual(Array.from(report.blockedBatches), ["B002"]);
    assert.match(report.error, /empty_agent_output_20/);
  }
});

test("final recovery continues beyond three drains and returns the successful snapshot", async () => {
  const c = harness();
  let drains = 0, current = snapshot(1, "retry_pending", 1000);
  Object.assign(c, {
    controlProtocolVersion: 1, finalRepairResults: [], phase: () => {},
    persistUndurableBatchFailures: async () => {}, unresolvedMergeCandidateDetails: () => [],
    readSchedulerState: async () => current,
    recoverPendingRetries: async () => {
      current = snapshot(drains + 2, "pending", (drains + 2) * 1000);
      return { ...current, rescheduledRetryBatches: ["B002"] };
    },
    drainRunnableLifecycles: async () => {
      drains += 1;
      assert.ok(drains <= 5, "successful finalization must stop draining");
      current = snapshot(drains + 1, drains < 5 ? "retry_pending" : "merged", (drains + 2) * 1000);
      return { ranAny: true };
    },
  });
  const from = source.indexOf("async function runFinalRepairAndReport(");
  const to = source.indexOf("// A resumed run can already contain", from);
  vm.runInContext(source.slice(from, to), c);
  const result = await c.runFinalRepairAndReport();
  assert.equal(drains, 5);
  assert.equal(result.manifest.batches.B002.status, "merged");
  assert.equal(c.finalRepairResults.filter(item => item.kind === "resume_retry_pending").length, 5);
  assert.equal(c.finalRepairResults.some(item => item.status === "automatic_retry_budget_exhausted"), false);
});

test("workflow resumes after local drain/finalization failures until durable deliveries complete", async () => {
  const c = harness(), waits = [];
  let drains = 0, finalizations = 0, reads = 0;
  Object.assign(c, {
    drainRunnableLifecycles: async () => { drains += 1; },
    runFinalRepairAndReport: async () => {
      finalizations += 1;
      if (finalizations <= 4) throw new Error('transient finalization outage');
      if (finalizations <= 9) return null;
      return { manifest: { status: 'running', batches: { B001: { status: 'merged', mergeCommitSha: 'same-commit' } } } };
    },
    allWorkflowDeliveriesMerged: state => state?.manifest?.batches?.B001?.mergeCommitSha === 'same-commit',
    manifestFromScheduler: state => state?.manifest,
    applySchedulerState: state => { c.lastScheduler = state; return state; },
    waitWorkspaceSyncRetry: async seconds => waits.push(seconds),
    readSchedulerState: async () => { reads += 1; return null; },
  });
  vm.runInContext(source.slice(source.indexOf('async function runUntilDeliveriesComplete('),
    source.indexOf('// A resumed run can already contain')), c);
  const result = await c.runUntilDeliveriesComplete();
  assert.equal(result.manifest.batches.B001.mergeCommitSha, 'same-commit');
  assert.equal(drains, 10);
  assert.equal(reads, 9);
  assert.equal(waits.length, 9);
  assert.ok(waits.every(seconds => seconds > 0 && seconds <= 30));
});

test("non-retryable blocks return after independent work drains", async () => {
  for (const busy of [null, "readyBatches", "mergeableBatches", "implementationRecoveryBatches",
    "stageRecoveryBatches", "retryPendingBatches", "activeWorkers"]) {
    const state = { status: "blocked", activeWorkers: 0,
      readyBatches: [], mergeableBatches: [], implementationRecoveryBatches: [],
      stageRecoveryBatches: [], retryPendingBatches: [],
      manifest: { batches: { B001: { status: "blocked",
        recovery: { status: "blocked_non_retryable", failureClassification: "non_retryable" } } } } };
    if (busy) state[busy] = busy === "activeWorkers" ? 1 : ["B002"];
    let waits = 0;
    const c = vm.createContext({ lastScheduler: state,
      batchInvocationRetryControl: { exhausted: new Set() },
      inactiveRun: () => false,
      drainRunnableLifecycles: async () => {},
      runFinalRepairAndReport: async () => state,
      allWorkflowDeliveriesMerged: () => false,
      applySchedulerState: value => value,
      recordSchedulerFailure: (_label, error) => { throw error; },
      waitWorkspaceSyncRetry: async () => {
        waits += 1;
        assert.equal(waits, 1, "blocked runs must return without repeated polling");
        state[busy] = busy === "activeWorkers" ? 0 : [];
      },
      readSchedulerState: async () => state,
    });
    vm.runInContext(source.slice(source.indexOf('async function runUntilDeliveriesComplete('),
      source.indexOf('// A resumed run can already contain')), c);
    assert.equal(await c.runUntilDeliveriesComplete(), state);
    assert.equal(waits, busy ? 1 : 0);
  }
});

test("explicit run cancellation stops automatic recovery", async () => {
  const state = { status: "cancelled", manifest: { batches: {} } };
  const c = vm.createContext({ lastScheduler: state,
    drainRunnableLifecycles: async () => assert.fail("cancelled runs must not restart") });
  vm.runInContext(source.slice(source.indexOf("function inactiveRun("), source.indexOf("function requireSchedulerResult(")), c);
  vm.runInContext(source.slice(source.indexOf('async function runUntilDeliveriesComplete('),
    source.indexOf('// A resumed run can already contain')), c);
  assert.equal(await c.runUntilDeliveriesComplete(), state);
});

test("a durable merge supersedes in-memory batch and candidate errors", () => {
  const c = harness();
  const state = snapshot(1, "merged", 1000);
  state.manifest.batches.B001 = { status: "merged", mergeCommitSha: "upstream" };
  state.manifest.batches.B002.mergeCommitSha = "merged-by-runtime";
  c.activeUnresolvedRecords = () => [
    { kind: "batch", batchId: "B002", status: "timeout" },
    { kind: "merge_candidate", batchIds: ["B002"], repositoryRef: "default", wave: 1, status: "needs_resolution" },
  ];
  vm.runInContext(source.slice(source.indexOf("function manifestFromScheduler("),
    source.indexOf("function completionReport(")), c);
  assert.equal(c.allWorkflowDeliveriesMerged(state), true);
});

test("defer exhaustion releases its grant once so an independent Batch can run", async () => {
  const c = harness();
  c.maxParallel = 1;
  c.waitWorkspaceSyncRetry = async () => {};
  c.codeWorkspaces = { default: "/source" };
  c.worktreeManagerPath = "/plugin/hooks/worktree_manager.py";
  c.batchResults = [];
  c.finalRepairResults = [];
  vm.runInContext(source.slice(source.indexOf("async function deferBatchForRetry("),
    source.indexOf("function mergedBatchIds(")), c);
  vm.runInContext(source.slice(source.indexOf("async function blockImplementationFinding("),
    source.indexOf("async function resolveMergeCandidate(")), c);
  vm.runInContext(source.slice(source.indexOf("async function safelyDeferBatchForRetry("),
    source.indexOf("async function runLifecycleSafely(")), c);
  vm.runInContext(source.slice(source.indexOf("function manifestFromScheduler("),
    source.indexOf("async function attemptFinalCandidateRepair(")), c);

  c.runDeliveryReviewAndGate = async () => ({
    status: "implementation_rework_required",
    failedStage: "review",
    recovery: { batchId: "B002", worktreePath: "/worktrees/B002", branchName: "batch", commitSha: "same-sha" },
  });
  c.reworkDeliveryImplementation = async () => ({
    batchId: "B002", status: "success", worktreePath: "/worktrees/B002", branchName: "batch", commitSha: "same-sha",
  });

  let current = snapshot(1, "pending", 1000);
  current.activeWorkers = 0;
  current.dispatchDiagnostics.activeBatchIds = [];
  current.manifest.batches.B001 = { status: "pending", dependencies: [], recovery: {} };
  current.readyBatches = ["B002", "B001"];
  c.applySchedulerState(current);

  let deferCalls = 0, finishCalls = 0, independentCalls = 0;
  const runInitialBatchLifecycle = c.runInitialBatchLifecycle;
  c.runInitialBatchLifecycle = async batchId => {
    if (batchId === "B002") return runInitialBatchLifecycle(batchId);
    independentCalls += 1;
    current = snapshot(1, "retry_pending", 4000);
    current.activeWorkers = 0;
    current.dispatchDiagnostics.activeBatchIds = [];
    current.manifest.batches.B001 = { status: "merged", mergeCommitSha: "upstream", dependencies: [] };
    current.manifest.batches.B002.error = "parallel_batch_recovery:dispatch_terminated";
    return { batchId, status: "merged" };
  };
  c.agent = async (prompt, options) => {
    if (options.label === "provision-worktree-B002") {
      return { success: true, batchId: "B002", repositoryRef: "default", worktreePath: "/worktrees/B002", branchName: "batch" };
    }
    if (options.label === "fixed-batch-B002") {
      return { batchId: "B002", status: "success", worktreePath: "/worktrees/B002", branchName: "batch", commitSha: "same-sha" };
    }
    if (options.label === "dispatch-claim-B002-1") {
      current = { ...current, activeWorkers: 1, dispatchDiagnostics: { activeBatchIds: ["B002"] },
        scheduledGroups: [], observedEpochMs: 2000 };
      return { success: true, status: "claimed", batchId: "B002", generation: 1, ownerToken: "owner-1" };
    }
    if (options.label === "dispatch-claim-B001-1") {
      return { success: true, status: "claimed", batchId: "B001", generation: 1, ownerToken: "owner-2" };
    }
    if (options.label.startsWith("defer-batch-retry-")) {
      deferCalls += 1;
      throw new Error(`defer_failed_${deferCalls}`);
    }
    if (options.label === "dispatch-finish-B002-1") {
      finishCalls += 1;
      assert.match(prompt, /--owner-token "owner-1".*--outcome "failed"/);
      current = snapshot(1, "retry_pending", 3000);
      current.activeWorkers = 0;
      current.dispatchDiagnostics.activeBatchIds = [];
      current.readyBatches = ["B001"];
      current.scheduledGroups = [["B001"]];
      current.manifest.batches.B001 = { status: "pending", dependencies: [], recovery: {} };
      current.manifest.batches.B002.error = "parallel_batch_recovery:dispatch_terminated";
      return { success: true, status: "finished", batchId: "B002" };
    }
    if (options.label === "dispatch-finish-B001-1") return { success: true, status: "finished", batchId: "B001" };
    return current;
  };
  c.waitForDispatchChange = async () => assert.fail("the released grant must not hold up the independent Batch");
  c.batchWorkspaces.B002 = { workspaceRef: "default", componentRoots: ["."] };

  await c.runDispatchWorker(1, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set() });
  await c.persistUndurableBatchFailures();

  assert.equal(deferCalls, 20);
  assert.equal(finishCalls, 1);
  assert.equal(independentCalls, 1);
  assert.equal(vm.runInContext('batchInvocationRetryControl.exhausted.has("B002")', c), true);
  assert.equal(c.quarantinedBatchIds.has("B002"), true);
  const details = c.unresolvedBatchDetails(current);
  assert.equal(details.length, 1);
  assert.equal(details[0].batchId, "B002");
  assert.equal(details[0].status, "blocked");
  assert.equal(details[0].recoveryStatus, "retry_exhausted");
  assert.match(details[0].error, /defer_failed_20/);
});

test("final repair shares the 20-attempt resume budget across dispatch and outer passes", async () => {
  const c = harness();
  let resumeCalls = 0, waits = 0;
  let current = snapshot(1, "retry_pending", 1000);
  current.activeWorkers = 0;
  current.dispatchDiagnostics.activeBatchIds = [];
  current.manifest.batches.B001 = { status: "merged", mergeCommitSha: "upstream", dependencies: [],
    worktreePath: "/worktrees/B001", branchName: "b1", commitSha: "c1", recovery: {} };
  c.applySchedulerState(current);

  Object.assign(c, {
    controlProtocolVersion: 1, finalRepairResults: [], phase: () => {},
    persistUndurableBatchFailures: async () => {}, unresolvedMergeCandidateDetails: () => [],
    allWorkflowDeliveriesMerged: () => false,
    readSchedulerState: async () => current,
    drainRunnableLifecycles: async () => ({ ranAny: false }),
    waitWorkspaceSyncRetry: async () => { waits += 1; },
    agent: async (_prompt, options) => {
      if (options.label.includes("resume-retry-pending")) {
        resumeCalls += 1;
        return current;
      }
      return current;
    },
  });
  vm.runInContext(source.slice(source.indexOf("function manifestFromScheduler("),
    source.indexOf("function completionReport(")), c);
  vm.runInContext(source.slice(source.indexOf("async function runFinalRepairAndReport("),
    source.indexOf("// A resumed run can already contain")), c);

  // Use another Batch in the dispatch phase to verify that the budget follows the invocation.
  const repairState = current;
  current = { ...current, retryPendingBatches: ["B001"] };
  for (let i = 1; i <= 5; i += 1) {
    await c.recoverPendingRetries(current, `dispatch-resume-retry-pending-${i}`);
  }
  assert.equal(resumeCalls, 5);
  current = repairState;

  // Outer loop + final repair must consume only the remaining 15 attempts (total = 20, never 21+)
  // and must exit without resetting on subsequent outer passes.
  await c.runUntilDeliveriesComplete();
  assert.equal(resumeCalls, 20);
  assert.equal(waits, 0);
  assert.equal(vm.runInContext('batchInvocationRetryControl.exhausted.has("B002")', c), true);
  assert.equal(c.finalRepairResults.some(item => item.status === "automatic_retry_budget_exhausted"), true);

  // Calling runFinalRepairAndReport again in the same invocation makes 0 additional resume calls.
  await c.runFinalRepairAndReport();
  assert.equal(resumeCalls, 20);
});
