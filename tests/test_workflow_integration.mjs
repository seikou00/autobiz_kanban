import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const finalization = source.slice(source.indexOf('async function runFinalValidation() {'),
  source.indexOf('\nif (controlProtocolVersion >= 2) {', source.indexOf('async function runFinalValidation() {')))
  + '\nreturn await runFinalValidation();';

async function finalize({ isolated = true, syncFailure = false, prepareFailure = false,
  aggregateFailure = false, aggregateUnavailable = false, blockingErrors = [] } = {}) {
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
      if (value.success === false) throw new Error(value.error || "command failed");
      return value;
    },
    async workflowAgent(prompt, options) {
      commands.push({ prompt, label: options.label });
      switch (options.label) {
        case "sync-main-into-integration":
          return { success: !syncFailure };
        case "aggregate-staged-evidence":
          if (aggregateUnavailable) return { ok: false, error: "report could not be written" };
          return { ok: true, passed: !aggregateFailure, policy: "report_only", blockingErrors,
            errors: aggregateFailure ? ["B003.test_batch_commit_stale", "B007.prepare_batch_commit_stale"] : [],
            integrationMode: isolated ? "isolated" : "direct", integrationWorkspaces: { default: "/integration" },
            e2eStatus: "skipped", e2eSkipReason: "e2e_temporarily_disabled",
            hasDeferredIssues: false, deferredIssues: [] };
        case "prepare-user-confirmed-promotion":
          return { success: !prepareFailure, mainPromotion: { status: "prepared", approvalDigest: "review-this",
            reportPath: "/artifacts/main-promotion.json", snapshot: { repositories: {
              default: { gitRoot: "/source", targetWorktreePath: "/baseline", targetBranch: "release",
                targetSha: "baseline", worktreePath: "/integration", branchName: "integration", candidateSha: "candidate" },
            } } } };
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
  assert.equal(result.verification.mainPromotion.reportPath, "/artifacts/main-promotion.json");
  assert.equal(result.verification.mainPromotion.snapshot.repositories.default.targetWorktreePath, "/baseline");
  assert.deepEqual(commands.map(command => command.label), [
    "sync-main-into-integration",
    "aggregate-staged-evidence", "prepare-user-confirmed-promotion",
  ]);
  assert.ok(commands.every(command => !command.prompt.includes('" promote ')));
});

test("legacy direct finalization preserves its successful result", async () => {
  const { result, commands } = await finalize({ isolated: false });
  assert.equal(result.finalStatus, "succeeded");
  assert.equal(commands.length, 1);
});

test("finalization registers skipped E2E inside aggregation without a separate test agent", async () => {
  const { result, commands } = await finalize();
  assert.equal(result.verification.e2eStatus, "skipped");
  assert.deepEqual(commands.map(command => command.label), ["sync-main-into-integration",
    "aggregate-staged-evidence", "prepare-user-confirmed-promotion"]);
  assert.match(commands.find(command => command.label === "aggregate-staged-evidence").prompt, /--skip-e2e/);
  assert.equal(commands.some(command => /utest|e2e/i.test(command.label)), false);
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

test("final reports retain E2E issues added after the last scheduler snapshot", () => {
  const context = vm.createContext({ manifestFromScheduler: () => ({ deferredIssues: [] }) });
  vm.runInContext(source.slice(source.indexOf("function deferredIssueDetails("),
    source.indexOf("function skippedUtestDetails(")), context);
  const issue = { kind: "e2e_failure", blocksWorkflow: false };
  assert.deepEqual(context.deferredIssueDetails({}, { deferredIssues: [issue] }), [issue]);
});
