import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const finalization = source.slice(source.indexOf('async function runFinalValidation() {'),
  source.indexOf('\nif (controlProtocolVersion >= 2) {', source.indexOf('async function runFinalValidation() {')))
  + '\nreturn await runFinalValidation();';

async function finalize({ isolated = true, missingSnapshot = false,
  aggregateFailure = false, aggregateUnavailable = false, blockingErrors = [], checkpointFailure = false } = {}) {
  const commands = [];
  const context = {
    prepared: { integrationMode: isolated ? "isolated" : "direct" },
    finalScheduler: {}, lastScheduler: {}, artifactWorkspace: "/artifacts", feature: "feature", runId: "run",
    pluginPath: "/plugin", mergeTrainPath: "/plugin/merge-train", aggregatePath: "/plugin/aggregate",
    VERIFICATION_SCHEMA: {},
    phase() {}, recordUnresolved() {},
    completionReport: value => value,
    joinPath: (...parts) => parts.join("/"),
    errorText: error => String(error),
    encodeFailure: value => Buffer.from(value, "utf8").toString("base64"),
    unwrap: value => value,
    hasFailureSignal: value => value.success === false || value.passed === false,
    requireSuccess(value) {
      if (value.ok === false || value.success === false) throw new Error(value.error || "command failed");
      return value;
    },
    async workflowAgent(prompt, options) {
      commands.push({ prompt, label: options.label });
      switch (options.label) {
        case "complete-code-checkpoint":
          assert.match(prompt, /--checkpoint code_done --json/);
          return { ok: !checkpointFailure, new_checkpoint: "code_done" };
        case "aggregate-staged-evidence":
          if (aggregateUnavailable) return { ok: false, error: "report could not be written" };
          return { ok: true, passed: !aggregateFailure, policy: "report_only", blockingErrors,
            errors: aggregateFailure ? ["B003.test_batch_commit_stale", "B007.prepare_batch_commit_stale"] : [],
            integrationMode: isolated ? "isolated" : "direct", integrationWorkspaces: { default: "/integration" },
            ...(!missingSnapshot ? { workspaceSync: [], repositoryHeads: { default: "candidate" } } : {}),
            e2eStatus: "skipped", e2eSkipReason: "e2e_temporarily_disabled",
            hasDeferredIssues: false, deferredIssues: [] };
        default:
          throw new Error(`unexpected command ${options.label}`);
      }
    },
  };
  const result = await vm.runInNewContext(`(async () => { ${finalization} })()`, context);
  return { result, commands };
}

test("isolated finalization completes without preparing or requesting merge confirmation", async () => {
  const { result, commands } = await finalize();
  assert.equal(result.finalStatus, "succeeded");
  assert.equal(result.nextAction, "continue_next_stage");
  assert.ok(commands.every(command => !/sync-main/.test(command.prompt)));
  assert.deepEqual(commands.map(command => command.label), [
    "aggregate-staged-evidence",
    "complete-code-checkpoint",
  ]);
  assert.ok(commands.every(command => !command.prompt.includes('" promote ')));
});

test("legacy direct finalization preserves its successful result", async () => {
  const { result, commands } = await finalize({ isolated: false });
  assert.equal(result.finalStatus, "succeeded");
  assert.equal(commands.length, 2);
});

test("finalization aggregates delivery evidence without a test agent or skip command", async () => {
  const { result, commands } = await finalize();
  assert.equal(result.e2eSkippedReason, undefined);
  assert.deepEqual(commands.map(command => command.label), ["aggregate-staged-evidence", "complete-code-checkpoint"]);
  assert.match(commands.find(command => command.label === "aggregate-staged-evidence").prompt, /--finish-workflow/);
  assert.equal(commands.some(command => /utest|e2e/i.test(command.label)), false);
});

test("failed evidence verification completes with recorded issues and no confirmation", async () => {
  const { result, commands } = await finalize({ aggregateFailure: true });
  assert.equal(result.finalStatus, "succeeded_with_issues");
  assert.equal(result.verification.passed, false);
  assert.equal(result.verification.errors.length, 2);
  assert.equal(commands.at(-1).label, "complete-code-checkpoint");
  assert.ok(commands.every(command => !command.prompt.includes('" promote ')));
});

test("legacy direct runs report unsuccessful evidence verification as issues", async () => {
  const { result } = await finalize({ isolated: false, aggregateFailure: true });
  assert.equal(result.finalStatus, "succeeded_with_issues");
  assert.equal(result.verification.passed, false);
});

test("missing audit reports and incomplete deliveries cannot prepare a promotion", async () => {
  for (const options of [{ aggregateUnavailable: true }, { aggregateFailure: true, blockingErrors: ["B001.not_merged"] }]) {
    const { result, commands } = await finalize(options);
    assert.equal(result.finalStatus, "completed_with_validation_failure");
    assert.equal(commands.at(-1).label, "aggregate-staged-evidence");
  }
});

test("an omitted Runtime publication snapshot cannot silently reuse old receipts", async () => {
  const { result, commands } = await finalize({ missingSnapshot: true });
  assert.equal(result.finalStatus, "completed_with_validation_failure");
  assert.equal(result.nextAction, "inspect_evidence_aggregate_failure");
  assert.equal(commands.length, 1);
});

test("a rejected Code checkpoint is reported without claiming the next stage is ready", async () => {
  const { result } = await finalize({ checkpointFailure: true });
  assert.equal(result.finalStatus, "completed_with_checkpoint_failure");
  assert.equal(result.nextAction, "retry_code_checkpoint");
});

test("legacy confirmation states continue into reporting while active promotion remains protected", async () => {
  const earlyReturn = source.slice(source.indexOf("const runId = prepared.runId;"), source.indexOf("let retryRecoveryInFlight"));
  for (const status of ["awaiting_user_confirmation", "promotion_prepared", "integration_retained"]) {
    const result = await vm.runInNewContext(`(async () => { ${earlyReturn}\nreturn { continued: true }; })()`, {
      feature: "feature", prepared: { runId: "same-run", status },
    });
    assert.equal(result.continued, true, status);
  }
  const result = await vm.runInNewContext(`(async () => { ${earlyReturn} })()`, {
    feature: "feature", prepared: { runId: "same-run", status: "promoting_main" },
  });
  assert.equal(result.runId, "same-run");
  assert.equal(result.finalStatus, "promoting_main");
  assert.equal(result.nextAction, "inspect_promotion_in_progress");
});

test("final reports retain E2E issues added after the last scheduler snapshot", () => {
  const context = vm.createContext({ manifestFromScheduler: () => ({ deferredIssues: [] }) });
  vm.runInContext(source.slice(source.indexOf("function deferredIssueDetails("),
    source.indexOf("function allWorkflowDeliveriesMerged(")), context);
  const issue = { kind: "e2e_failure", blocksWorkflow: false };
  assert.deepEqual(context.deferredIssueDetails({}, { deferredIssues: [issue] }), [issue]);
});

test("final publication state comes from the latest Runtime report, including cleared and stale receipts", () => {
  const old = { repositoryRef: "default", status: "synced", lastSyncedSha: "old" };
  const context = vm.createContext({
    feature: "f", runId: "run", batchResults: [], mergeResults: [], cleanupResults: [], lifecycleResults: [],
    finalRepairResults: [], schedulerFailures: [], workspaceSyncControl: { results: new Map([["default", old]]) },
    unresolvedBatchDetails: () => [], unresolvedMergeCandidateDetails: () => [], deferredIssueDetails: () => [],
    activeUnresolvedRecords: () => [], usableString: value => typeof value === "string" && value.length > 0,
  });
  vm.runInContext(source.slice(source.indexOf("function completionReport("),
    source.indexOf("async function persistUndurableBatchFailures(")), context);
  for (const [latest, pending] of [
    [[{ ...old, status: "pending", targetSha: "new" }], 1],
    [[old], 1],
    [[{ ...old, lastSyncedSha: "new" }], 0],
    [[], 0],
  ]) {
    context.workspaceSyncControl.results.set("default", { ...old, status: pending ? "synced" : "pending" });
    const result = context.completionReport({ finalStatus: "succeeded", nextAction: "continue_next_stage",
      scheduler: { workspaceSync: [old] }, verification: { workspaceSync: latest, repositoryHeads: { default: "new" } } });
    assert.equal(result.workspaceSync.length, latest.length);
    assert.equal(result.workspaceSyncPending.length, pending);
    assert.equal(result.nextAction, pending ? "resume_workspace_sync" : "continue_next_stage");
    assert.equal(result.finalStatus, pending ? "succeeded_with_issues" : "succeeded");
    if (latest.length) assert.equal(result.workspaceSync[0].status, pending ? "pending" : "synced");
  }
});
