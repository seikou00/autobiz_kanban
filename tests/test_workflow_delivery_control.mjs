import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

async function execute(frontend = false) {
  const labels = [];
  const pools = [];
  const delivery = { batchId: "B001", worktreePath: "/worktree", branchName: "batch", commitSha: "sealed" };
  const workspace = { workspaceRef: "default", componentRoots: ["."], executionStage: "parallel",
    requestedPath: "/source", worktreePath: "/worktree", branchName: "batch" };
  let revision = 1;
  const snapshot = merged => ({
    success: true, runId: "run", status: "running", controlProtocolVersion: 2, controlRevision: revision++,
    observedEpochMs: revision * 1000, maxParallel: 3, activeWorkers: 0, integrationMode: "isolated",
    scheduledGroups: merged ? [] : [["B001"]], readyBatches: merged ? [] : ["B001"],
    allReadyBatches: merged ? [] : ["B001"], mergeableBatches: [],
    implementationRecoveryBatches: [], stageRecoveryBatches: [], retryPendingBatches: [], blockedBatches: [],
    parallelGroups: [], allParallelGroups: [], dispatchGrants: { B001: 1 },
    batchTaskIds: { B001: ["T001"] }, batchWorkspaces: { B001: workspace }, cleanupPending: [],
    manifest: { batches: { B001: { ...delivery, status: merged ? "merged" : "pending",
      dependencies: [], mergeCommitSha: merged ? "candidate" : null, recovery: {} } },
      mergeTrains: {}, deferredIssues: [], skippedUtests: [] },
  });
  const preparation = frontend
    ? { success: true, ...delivery, status: "skipped", testStatus: "skipped", skipReason: "frontend_utest_temporarily_disabled" }
    : { success: true, batchId: "B001", status: "test_required", runtimeContractDigest: "b".repeat(64),
      utestPolicy: { firstExecutionSeconds: 1800, stageSeconds: 2700, commandSeconds: 300, digest: "policy" } };
  let releaseCleanup;
  const cleanupMayFinish = new Promise(resolve => { releaseCleanup = resolve; });
  let cleanupStarted = false;
  const agent = async (prompt, options) => {
    const label = options.label;
    labels.push(label);
    if (label === "verify-engineering-deployment") return { success: true };
    if (label === "fixed-workflow-prepare") return snapshot(false);
    if (label.startsWith("dispatch-claim-")) return { success: true, batchId: "B001", status: "claimed", ownerToken: "owner", generation: 1 };
    if (label === "provision-worktree-B001") return { success: true, ...delivery, repositoryRef: "default" };
    if (label === "fixed-batch-B001") return { ...delivery, status: "success" };
    if (label === "stage-review-B001-attempt-1") return { success: true, batchId: "B001", stage: "review", status: "passed" };
    if (label === "validate-review-B001-attempt-1") return { success: true, batchId: "B001", stage: "review", status: "passed", utestPreparation: preparation };
    if (label === "stage-utest-B001") return { success: true, ...delivery, status: "success", testStatus: "passed" };
    if (label === "settle-delivery-B001-1") {
      assert.match(prompt, /--expected-commit "sealed"/);
      return { success: true, status: "merged", batchId: "B001", runId: "run", dispatchGeneration: 1,
        deliverySha: "sealed", mergeCommitSha: "candidate", dispatchFinalized: true,
        repositoryRef: "default", wave: 1, operationId: "operation", schedulerSnapshot: snapshot(true) };
    }
    if (label === "prepare-delivery-finalization") return { success: true, status: "ready", repairRequired: false, schedulerSnapshot: snapshot(true) };
    if (label === "sync-main-into-integration" || label === "begin-e2e-validation") return { success: true };
    if (label === "run-e2e-validation") {
      // Cleanup can be waiting independently while final validation advances.
      await Promise.resolve();
      assert.equal(cleanupStarted, true);
      releaseCleanup();
      return { success: true };
    }
    if (label === "aggregate-staged-evidence") return { ok: true, policy: "report_only", passed: true,
      errors: [], blockingErrors: [], integrationMode: "isolated" };
    if (label === "prepare-user-confirmed-promotion") return { success: true, mainPromotion: { approvalDigest: "approval" } };
    if (label === "drain-delivery-cleanup") {
      cleanupStarted = true;
      await cleanupMayFinish;
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

for (const frontend of [false, true]) {
  test(`complete ${frontend ? "frontend" : "backend"} workflow settles without individual control agents`, async () => {
    const { labels, result } = await execute(frontend);
    assert.equal(result.finalStatus, "awaiting_user_confirmation", JSON.stringify({ labels, result }));
    const chain = labels.filter(label => /^(stage-review|validate-review|stage-utest|settle-delivery)/.test(label));
    assert.equal(chain.length, frontend ? 3 : 4);
    assert.equal(labels.filter(label => label === "prepare-delivery-finalization").length, 1);
    assert.equal(labels.filter(label => /stage-gate|build-candidate|promote-candidate|dispatch-finish|after-batch|final-report-snapshot|recover-merged/.test(label)).length, 0);
    assert.deepEqual(result.cleanupPending, []);
    assert.equal(result.workflowMetrics.agentCalls, labels.length);
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
