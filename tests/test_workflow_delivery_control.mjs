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
      assert.match(prompt, /--skip-tests/);
      return { success: true, batchId: "B001", stage: "review", status: "passed" };
    }
    if (label === "settle-delivery-B001-1") {
      assert.doesNotMatch(prompt, /--expected-commit\s+"/);
      assert.match(prompt, /--skip-tests/);
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
        repositoryRef: "default", wave: 1, operationId: "operation", schedulerSnapshot: snapshot(true) };
    }
    if (label === "prepare-delivery-finalization") {
      if (fault === "admission") throw new Error("finalization unavailable");
      return fault === "blocked"
        ? { success: true, status: "repair_required", repairRequired: true, batchIds: ["B001"] }
        : { success: true, status: "ready", repairRequired: false, batchIds: [] };
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
    if (label === "sync-main-into-integration") return { success: true };
    if (label === "aggregate-staged-evidence") {
      assert.match(prompt, /--skip-e2e/);
      // Cleanup can be waiting independently while final validation advances.
      await Promise.resolve();
      assert.equal(cleanupStarted, true);
      releaseCleanup();
      return { ok: true, policy: "report_only", passed: true,
        errors: [], blockingErrors: [], integrationMode: "isolated",
        e2eStatus: "skipped", e2eSkipReason: "e2e_temporarily_disabled",
        skippedUtests: [{ batchId: "B001", executionLane: frontend ? "frontend" : "backend",
          status: "skipped", reason: "utest_temporarily_disabled" }] };
    }
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

test("nonblocking Review uncertainty survives through final baseline confirmation", async () => {
  const { labels, result } = await execute(false, "advisory");
  assert.equal(result.finalStatus, "awaiting_user_confirmation");
  assert.equal(result.deferredIssues.length, 1);
  assert.equal(result.deferredIssues[0].kind, "review_advisory");
  assert.equal(result.deferredIssues[0].blocksWorkflow, false);
  assert.equal(labels.some(label => label.startsWith("triage-review-")), false);
  assert.ok(labels.includes("aggregate-staged-evidence"));
});

test("settlement uses Runtime SHA even when the implementation agent transcribed it incorrectly", async () => {
  const { result } = await execute(false, "stale-sha");
  assert.equal(result.finalStatus, "awaiting_user_confirmation");
});

test("queued delivery returns to dispatch without failure or consuming its grant", async () => {
  const { labels, result } = await execute(false, "queued");
  assert.equal(result.finalStatus, "awaiting_user_confirmation", JSON.stringify({ labels, result }));
  assert.equal(labels.filter(label => label === "dispatch-claim-B001-1").length, 2);
  assert.equal(labels.filter(label => label === "settle-delivery-B001-1").length, 2);
  assert.equal(labels.some(label => /defer-batch|dispatch-finish/.test(label)), false);
  assert.equal(labels.filter(label => label === "stage-review-B001-attempt-1").length, 1);
});

test("sealed delivery recovery goes straight to settlement without reimplementation or Review", async () => {
  const { labels, result } = await execute(false, "sealed-recovery");
  assert.equal(result.finalStatus, "awaiting_user_confirmation", JSON.stringify({ labels, result }));
  assert.equal(labels.some(label => /fixed-batch|provision-worktree|stage-review|validate-review/.test(label)), false);
  assert.ok(labels.includes("settle-delivery-B001-1"));
});

for (const frontend of [false, true]) {
  test(`complete ${frontend ? "frontend" : "backend"} workflow settles without individual control agents`, async () => {
    const { labels, result } = await execute(frontend);
    assert.equal(result.finalStatus, "awaiting_user_confirmation", JSON.stringify({ labels, result }));
    assert.equal(result.verification.e2eStatus, "skipped");
    assert.equal(result.skippedUtests[0].reason, "utest_temporarily_disabled");
    assert.equal(labels.some(label => /utest|e2e/i.test(label)), false);
    assert.equal(result.e2eSkippedReason, "e2e_temporarily_disabled");
    assert.equal(labels.includes("begin-e2e-validation"), false);
    assert.equal(labels.includes("run-e2e-validation"), false);
    const chain = labels.filter(label => /^(stage-review|validate-review|stage-utest|settle-delivery)/.test(label));
    assert.equal(chain.length, 3);
    assert.equal(labels.filter(label => label === "prepare-delivery-finalization").length, 1);
    assert.equal(labels.filter(label => label === "final-repair-initial-snapshot").length, 1);
    assert.equal(labels.filter(label => /stage-gate|build-candidate|promote-candidate|dispatch-finish|after-batch|final-report-snapshot|recover-merged/.test(label)).length, 0);
    assert.deepEqual(result.cleanupPending, []);
    assert.equal(result.workflowMetrics.agentCalls, labels.length);
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
