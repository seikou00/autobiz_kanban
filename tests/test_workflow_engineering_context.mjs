import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
function harness() {
  const context = vm.createContext({
    unwrap: value => value || {}, usableString: value => typeof value === "string" && value.trim().length > 0,
    implementationFailureMessage: (value, failure) => failure.message || value.message,
  });
  vm.runInContext(source.slice(0, source.indexOf("function normalizeStructuredOutput(")).replace("export const meta =", "const meta =") +
    "\nglobalThis.schemas = { review: REVIEW_VALIDATION_SCHEMA, utest: UTEST_STAGE_SCHEMA, recovery: RECOVERY_FAILURE_CONTEXT_SCHEMA };", context);
  vm.runInContext(source.slice(source.indexOf("function implementationReworkRequired("), source.indexOf("function implementationFailureMessage(")), context);
  return context;
}

test("durable repair references survive structured transport and reach production recovery", () => {
  const context = harness();
  const references = { repairContextPath: "/artifacts/repair.json", repairContextDigest: "a".repeat(64), affectedTaskIds: ["T002"] };
  for (const [stage, schema] of [["review", context.schemas.review], ["test", context.schemas.utest]]) {
    const raw = { ...references, status: "failed", failure: { type: "implementation", message: "src/Auth.java:7 authorization mismatch" } };
    const projected = Object.fromEntries(Object.entries(raw).filter(([key]) => key in schema.properties));
    const result = context.implementationReworkRequired({ batchId: "B001", worktreePath: "/batch", branchName: "batch", commitSha: "draft" }, stage, projected);
    const recovered = JSON.parse(JSON.stringify(result.recovery.failureContext));
    for (const [key, value] of Object.entries(references)) {
      assert.deepEqual(recovered[key], value);
      assert.ok(key in context.schemas.recovery.properties);
    }
  }
});

test("test failure preserves original command coordinates alongside repair references", () => {
  const context = harness();
  const failure = { message: "failed test", argv: ["mvn", "-Dtest=AuthTest", "test"], cwd: "/batch/module", evidenceId: "ev_1", evidenceLogPath: "/artifacts/ev_1.log" };
  const result = context.implementationReworkRequired({ batchId: "B001" }, "test", { testFailure: failure, failure: { message: failure.message } });
  const baseline = JSON.parse(JSON.stringify(result.recovery.failureContext));
  for (const field of ["argv", "cwd", "evidenceId", "evidenceLogPath"]) assert.deepEqual(baseline[field], failure[field]);
});

function dispatchHarness() {
  const context = vm.createContext({
    dispatchGrants: { B001: 1 }, retryPriorityBatchIds: new Set(), activeDispatchGrants: new Map(),
    runnableScheduledBatchIds: () => ["B001"], runnableSchedulerFallbackBatchIds: () => [],
    runnableImplementationRecoveries: () => [], runnableStageRecoveries: () => [], runnableMergeableBatchIds: () => [],
    runInitialBatchLifecycle: async () => ({ batchId: "B001", status: "merged" }),
    setTimeout, clearTimeout, DISPATCH_POLL_INTERVAL_MS: 30000,
  });
  vm.runInContext(source.slice(source.indexOf("function takeNextRunnableLifecycle("), source.indexOf("async function drainRunnableLifecycles(")), context);
  return context;
}

test("same drain consumes each generation once and can dispatch a retry while another Batch is running", () => {
  const c = dispatchHarness(), claimed = new Set(), running = new Set(["B002"]);
  assert.equal(c.takeNextRunnableLifecycle(claimed, running).generation, 1);
  assert.equal(c.takeNextRunnableLifecycle(claimed, running), null);
  c.dispatchGrants = { B001: 2 };
  c.retryPriorityBatchIds.add("B001");
  assert.equal(c.takeNextRunnableLifecycle(claimed, new Set(["B001", "B002"])), null);
  assert.equal(c.takeNextRunnableLifecycle(claimed, running).generation, 2);
  assert.equal(c.takeNextRunnableLifecycle(claimed, running), null);
  c.dispatchGrants = { B001: 1 }; // stale snapshot never clears a consumed grant
  assert.equal(c.takeNextRunnableLifecycle(claimed, running), null);
});

test("local completion wakes idle workers without waiting for the fallback poll", async () => {
  const c = dispatchHarness(), state = {};
  let awake = 0;
  const waits = [c.waitForDispatchChange(state).then(() => awake++), c.waitForDispatchChange(state).then(() => awake++)];
  c.signalDispatchChange(state);
  await Promise.all(waits);
  assert.equal(awake, 2);
  assert.equal(state.waiters.size, 0);
});

test("failure diagnostics survive shell metacharacters and Unicode without executing them", () => {
  const c = dispatchHarness();
  const message = '路径错误 `touch /tmp/unwanted` $(echo bad) "quoted"\nline2';
  assert.equal(Buffer.from(c.encodeFailure(message), "base64").toString("utf8"), message);
  assert.match(c.encodeFailure(message), /^[A-Za-z0-9+/=]+$/);
});

test("Review needs_triage cannot launch UTest even with success true", async () => {
  const calls = [], c = vm.createContext({ batchTaskIds: { B001: ["T001"] },
    stagePath: "/plugin/hooks/parallel_batch_stage.py", artifactWorkspace: "/artifacts", feature: "f", runId: "r",
    requiresImplementationRework: () => false,
    workflowAgent: async (_, options) => { calls.push(options.label); return options.label.startsWith("stage-implement")
      ? { success: true, status: "passed" } : { success: true, batchId: "B001", stage: "review", status: "needs_triage" }; },
    runBatchUtestAndSeal: async () => { throw new Error("UTest must not run"); },
  });
  vm.runInContext(source.slice(0, source.indexOf("function normalizeStructuredOutput(")).replace("export const meta =", "const meta ="), c);
  vm.runInContext(source.slice(source.indexOf("function normalizeStructuredOutput("), source.indexOf("const input = unwrap(args);")), c);
  vm.runInContext(source.slice(source.indexOf("async function runDeliveryReviewTestAndGate("), source.indexOf("async function reworkDeliveryImplementation(")), c);
  await assert.rejects(c.runDeliveryReviewTestAndGate({ batchId: "B001", worktreePath: "/batch", branchName: "branch", commitSha: "sha" }), /review_not_passed:needs_triage/);
  assert.deepEqual(calls, ["stage-implement-B001", "stage-review-B001-attempt-1", "validate-review-B001-attempt-1"]);
});

test("Workflow dispatch works when ambient clock and random globals are unavailable", () => {
  const c = dispatchHarness();
  vm.runInContext(`
    globalThis.Date = new Proxy(function () {}, { get() { throw new Error("clock unavailable"); }, construct() { throw new Error("clock unavailable"); } });
    Math.random = () => { throw new Error("random unavailable"); };
  `, c);
  assert.equal(c.nextDispatchRequestId({}, "a".repeat(32), "drain", 1), `${"a".repeat(32)}:drain:1:1`);
  assert.equal(c.dispatchSnapshotEpoch({ observedEpochMs: 12345 }), 12345);
  c.timeoutPerBatch = 60;
  assert.equal(c.dispatchIdleExpired(1000, { observedEpochMs: 121001 }, 0, 4), true);
  assert.equal(c.dispatchIdleExpired(1000, { observedEpochMs: 1001 }, 0, 4), false);
  assert.equal(c.dispatchIdleExpired(null, {}, 5, 4), true);
});

test("request IDs replay deterministically and use separate invocation and attempt namespaces", () => {
  const c = dispatchHarness(), a = {}, replay = {};
  const first = c.nextDispatchRequestId(a, "a".repeat(32), "drain", 1);
  assert.equal(first, c.nextDispatchRequestId(replay, "a".repeat(32), "drain", 1));
  assert.notEqual(first, c.nextDispatchRequestId(a, "a".repeat(32), "drain", 1));
  assert.notEqual(first, c.nextDispatchRequestId({}, "b".repeat(32), "drain", 1));
  assert.notEqual(first, c.nextDispatchRequestId({}, "a".repeat(32), "final-repair-1", 1));
});

test("local wakes do not count as full idle polling intervals", async () => {
  const c = dispatchHarness(), state = {};
  for (let i = 0; i < 8; i++) {
    const waiting = c.waitForDispatchChange(state);
    c.signalDispatchChange(state);
    assert.equal(await waiting, "signal");
  }
});

test("fallback timer reports an elapsed poll", async () => {
  const c = dispatchHarness();
  c.DISPATCH_POLL_INTERVAL_MS = 1;
  assert.equal(await c.waitForDispatchChange({}), "poll");
});

test("a real dispatch worker completes under the restricted clock/random globals", async () => {
  const c = dispatchHarness(), executed = [], claims = [];
  vm.runInContext(`
    globalThis.Date = new Proxy(function () {}, { get() { throw new Error("clock unavailable"); } });
    Math.random = () => { throw new Error("random unavailable"); };
  `, c);
  Object.assign(c, { workflowInvocationId: "a".repeat(32), timeoutPerBatch: 60, maxParallel: 1,
    schedulerCycles: 0, MAX_SCHEDULER_CYCLES: 100,
    lastScheduler: { activeWorkers: 0, observedEpochMs: 1000, readyBatches: ["B001"] },
    isValidBatchId: value => /^B\d+$/.test(value), usableString: value => typeof value === "string" && value.length > 0,
    errorText: String, recordUnresolved: record => { throw new Error(JSON.stringify(record)); },
    runLifecycleSafely: async (batch, _, execute) => { executed.push(batch); return execute(); },
    dispatchCommand: async (action, job) => {
      if (action === "claim") claims.push(job.requestId);
      return { success: true, status: action === "claim" ? "claimed" : "finished", batchId: job.batchId, ownerToken: "token" };
    },
    refreshSchedulerForDispatch: async () => {
      c.runnableScheduledBatchIds = () => [];
      c.lastScheduler = { activeWorkers: 0, observedEpochMs: 2000, readyBatches: [] };
      return c.lastScheduler;
    },
  });
  await c.runDispatchWorker(1, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set() });
  assert.deepEqual(executed, ["B001"]);
  assert.deepEqual(claims, [`${"a".repeat(32)}:drain:1:1`]);
});
