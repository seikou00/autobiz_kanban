import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");

function harness() {
  const events = [], failures = [];
  const state = {
    runId: "run", status: "running", maxParallel: 2, activeWorkers: 1, observedEpochMs: 1000,
    scheduledGroups: [], readyBatches: [], allReadyBatches: [], mergeableBatches: [],
    implementationRecoveryBatches: [], stageRecoveryBatches: [], retryPendingBatches: [],
    blockedBatches: [], parallelGroups: [], allParallelGroups: [],
    batchTaskIds: {}, batchWorkspaces: {}, dispatchGrants: {},
    manifest: { batches: {}, mergeTrains: {}, deferredIssues: [] },
  };
  const context = vm.createContext({
    pluginPath: "/plugin", schedulerPath: "/plugin/hooks/parallel_batch_scheduler.py",
    artifactWorkspace: "/artifacts", feature: "feature", runId: "run",
    SCHEDULER_RESULT_SCHEMA: {}, schedulerReadInFlight: null, lastScheduler: state,
    maxParallel: 2, timeoutPerBatch: 3600, schedulerCycles: 1,
    activeDispatchGrants: new Map(), dispatchGrants: {}, retryPriorityBatchIds: new Set(),
    runnableScheduledBatchIds: () => [], runnableSchedulerFallbackBatchIds: () => [],
    runnableImplementationRecoveries: () => [], runnableStageRecoveries: () => [],
    runnableMergeableBatchIds: () => [], isValidBatchId: value => /^B\d+$/.test(value),
    recordUnresolved: value => assert.fail(JSON.stringify(value)), errorText: String,
    recordSchedulerFailure: (label, error) => failures.push({ label, error }),
    markSchedulerRecovered() {},
    applySchedulerState: value => { context.lastScheduler = value; },
    schedulerSnapshotFallback: () => assert.fail("failed polls must wait instead of dispatching a fallback"),
  });
  vm.runInContext(source.slice(0, source.indexOf("const input = unwrap(args);")).replace("export const meta =", "const meta ="), context);
  for (const [start, end] of [
    ["const WORKFLOW_AUTONOMY_PREFIX", "const aggregatePath"],
    ["function normalizeScheduledGroups(", 'phase("准备");'],
    ["async function readSchedulerState(", "async function deferBatchForRetry("],
    ["function takeNextRunnableLifecycle(", "async function drainRunnableLifecycles("],
  ]) {
    vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))), context);
  }
  context.waitForDispatchChange = async () => { events.push("wait"); return "poll"; };
  return { context, state, events, failures };
}

for (const capacityPoll of [false, true]) {
  for (const failure of ["empty", "wrapped-empty", "empty-error", "command-error", "failed-result"]) {
    test(`${capacityPoll ? "capacity" : "idle"} poll waits for the next interval after ${failure}`, async () => {
      const { context: c, state, events, failures } = harness();
      c.maxParallel = capacityPoll ? 1 : 2;
      const labels = [];
      c.agent = async (_, options) => {
        labels.push(options.label);
        events.push("read");
        assert.equal(c.lastScheduler, state);
        if (labels.length === 1) {
          if (failure === "empty") return null;
          if (failure === "wrapped-empty") return { value: " " };
          if (failure === "empty-error") throw new Error("Received empty response from chat model call");
          if (failure === "command-error") throw new Error("status command unavailable");
          return { success: false, error: "status failed" };
        }
        assert.equal(labels.length, 2);
        return { ...state, activeWorkers: 0, observedEpochMs: 2000 };
      };
      await c.runDispatchWorker(2, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set() });
      const label = capacityPoll ? "drain-capacity-poll-2-1" : "drain-poll-2-1";
      assert.deepEqual(labels, [label, label]);
      assert.deepEqual(events, ["wait", "read", "wait", "read"]);
      assert.equal(failures.length, 1);
      assert.equal(c.lastScheduler.activeWorkers, 0);
      assert.equal(c.schedulerReadInFlight, null);
    });
  }
}

test("non-poll agent calls retain their bounded empty response retries", async () => {
  const { context: c } = harness(), labels = [];
  c.agent = async (_, options) => {
    labels.push(options.label);
    return labels.length < 3 ? null : { success: true };
  };
  assert.equal((await c.workflowAgent("run task", { label: "task-B001" })).success, true);
  assert.deepEqual(labels, ["task-B001", "task-B001-empty-retry-1", "task-B001-empty-retry-2"]);
});
