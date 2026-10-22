import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const finalization = source.slice(source.indexOf('phase("最终验证");'));

async function finalize({ isolated = true, syncFailure = false, prepareFailure = false,
  e2eFailure = false, beginFailure = false, recordingFailure = false,
  aggregateFailure = false, aggregateUnavailable = false, blockingErrors = [] } = {}) {
  const commands = [];
  const issue = { issueId: "E2E-FAILED-001", kind: "e2e_failure", blocksWorkflow: false, message: "E2E failed" };
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
      if (value.success === false) throw new Error(value.error || "command failed");
      return value;
    },
    async workflowAgent(prompt, options) {
      commands.push({ prompt, label: options.label });
      switch (options.label) {
        case "sync-main-into-integration":
          return { success: !syncFailure };
        case "begin-e2e-validation":
          return { success: !beginFailure, worktrees: { default: "/validation" } };
        case "run-e2e-validation":
          if (e2eFailure === "throw") throw new Error('环境错误 "quoted" $(echo must-not-execute)\n完整错误');
          if (e2eFailure === "recorded") return { success: true, e2eStatus: "failed", status: "deferred", issue };
          if (e2eFailure) return { success: false, error: "E2E command failed" };
          return { success: true };
        case "record-e2e-failure":
          return { success: !recordingFailure, e2eStatus: "failed", status: "deferred", issue };
        case "aggregate-staged-evidence":
          if (aggregateUnavailable) return { ok: false, error: "report could not be written" };
          return { ok: true, passed: !aggregateFailure, policy: "report_only", blockingErrors,
            errors: aggregateFailure ? ["B003.test_batch_commit_stale", "B007.prepare_batch_commit_stale"] : [],
            integrationMode: isolated ? "isolated" : "direct", integrationWorkspaces: { default: "/integration" },
            e2eStatus: e2eFailure || beginFailure ? "failed" : "passed",
            hasDeferredIssues: Boolean(e2eFailure || beginFailure), deferredIssues: e2eFailure || beginFailure ? [issue] : [] };
        case "prepare-user-confirmed-promotion":
          return { success: !prepareFailure, mainPromotion: { status: "prepared", approvalDigest: "review-this" } };
        default:
          throw new Error(`unexpected command ${options.label}`);
      }
    },
  };
  const result = await vm.runInNewContext(`(async () => { ${finalization} })()`, context);
  return { result, commands };
}

test("isolated finalization validates integration and stops at user confirmation", async () => {
  const { result, commands } = await finalize();
  assert.equal(result.finalStatus, "awaiting_user_confirmation");
  assert.equal(result.verification.mainPromotion.approvalDigest, "review-this");
  assert.deepEqual(commands.map(command => command.label), [
    "sync-main-into-integration", "begin-e2e-validation", "run-e2e-validation",
    "aggregate-staged-evidence", "prepare-user-confirmed-promotion",
  ]);
  assert.ok(commands.every(command => !command.prompt.includes('" promote ')));
});

test("legacy direct finalization preserves its successful result", async () => {
  const { result, commands } = await finalize({ isolated: false });
  assert.equal(result.finalStatus, "succeeded");
  assert.equal(commands.length, 3);
});

test("failed evidence verification is retained in the report and still prepares user confirmation", async () => {
  const { result, commands } = await finalize({ aggregateFailure: true });
  assert.equal(result.finalStatus, "awaiting_user_confirmation");
  assert.equal(result.verification.passed, false);
  assert.equal(result.verification.errors.length, 2);
  assert.equal(commands.at(-1).label, "prepare-user-confirmed-promotion");
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

test("main sync conflict stops before E2E and preserves the recovery entry", async () => {
  const { result, commands } = await finalize({ syncFailure: true });
  assert.equal(result.finalStatus, "integration_sync_required");
  assert.equal(result.nextAction, "inspect_integration_sync_then_resume");
  assert.equal(commands.length, 1);
});

test("a failed preparation keeps the result waiting for confirmation", async () => {
  const { result } = await finalize({ prepareFailure: true });
  assert.equal(result.finalStatus, "awaiting_user_confirmation");
  assert.equal(result.nextAction, "sync_target_if_changed_then_prepare_promotion");
});

test("resuming a prepared promotion does not restart the development or E2E workflow", async () => {
  const earlyReturn = source.slice(source.indexOf("const runId = prepared.runId;"), source.indexOf("let retryRecoveryInFlight"));
  const result = await vm.runInNewContext(`(async () => { ${earlyReturn} })()`, {
    feature: "feature", prepared: { runId: "same-run", status: "promotion_prepared", mainPromotion: { approvalDigest: "same" } },
  });
  assert.equal(result.runId, "same-run");
  assert.equal(result.finalStatus, "promotion_prepared");
  assert.equal(result.mainPromotion.approvalDigest, "same");
});

for (const e2eFailure of [true, "throw", "recorded"]) {
  test(`E2E failure ${e2eFailure} continues to evidence aggregation and external confirmation`, async () => {
    const { result, commands } = await finalize({ e2eFailure });
    assert.equal(result.finalStatus, "awaiting_user_confirmation");
    assert.equal(result.e2e.e2eStatus, "failed");
    assert.equal(result.verification.e2eStatus, "failed");
    assert.equal(result.verification.deferredIssues[0].blocksWorkflow, false);
    assert.equal(commands.filter(c => c.label === "run-e2e-validation").length, 1);
    assert.equal(commands.filter(c => c.label === "record-e2e-failure").length, e2eFailure === "recorded" ? 0 : 1);
    assert.deepEqual(commands.slice(-2).map(c => c.label), ["aggregate-staged-evidence", "prepare-user-confirmed-promotion"]);
    assert.ok(commands.every(command => !command.prompt.includes('" promote ')));
    if (e2eFailure === "throw") {
      const prompt = commands.find(c => c.label === "record-e2e-failure").prompt;
      const encoded = prompt.match(/--metadata-base64 "([A-Za-z0-9+/=]+)"/)[1];
      const metadata = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
      assert.match(metadata.message, /\$\(echo must-not-execute\)/);
      assert.equal(metadata.executionOutcome, "execution_failed");
      assert.ok(!prompt.includes("$(echo must-not-execute)"));
    }
  });
}

test("E2E preparation failure is recorded without running tests and still prepares confirmation", async () => {
  const { result, commands } = await finalize({ beginFailure: true });
  assert.equal(result.finalStatus, "awaiting_user_confirmation");
  assert.deepEqual(commands.map(c => c.label), ["sync-main-into-integration", "begin-e2e-validation",
    "record-e2e-failure", "aggregate-staged-evidence", "prepare-user-confirmed-promotion"]);
});

test("legacy direct runs finish with issues after E2E failure", async () => {
  const { result } = await finalize({ isolated: false, e2eFailure: true });
  assert.equal(result.finalStatus, "succeeded_with_issues");
});

test("failure to persist E2E results is reported instead of claiming completion", async () => {
  const { result, commands } = await finalize({ e2eFailure: true, recordingFailure: true });
  assert.equal(result.finalStatus, "completed_with_validation_failure");
  assert.equal(result.nextAction, "record_e2e_failure_then_resume");
  assert.equal(commands.at(-1).label, "record-e2e-failure");
});

test("final reports retain E2E issues added after the last scheduler snapshot", () => {
  const context = vm.createContext({ manifestFromScheduler: () => ({ deferredIssues: [] }) });
  vm.runInContext(source.slice(source.indexOf("function deferredIssueDetails("),
    source.indexOf("function skippedUtestDetails(")), context);
  const issue = { kind: "e2e_failure", blocksWorkflow: false };
  assert.deepEqual(context.deferredIssueDetails({}, { deferredIssues: [issue] }), [issue]);
});
