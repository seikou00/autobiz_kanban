import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const finalization = source.slice(source.indexOf('phase("最终验证");'));

async function finalize({ isolated = true, syncFailure = false, prepareFailure = false } = {}) {
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
          return { success: true, worktrees: { default: "/validation" } };
        case "run-e2e-validation":
          return { success: true };
        case "aggregate-staged-evidence":
          return { passed: true, integrationMode: isolated ? "isolated" : "direct", integrationWorkspaces: { default: "/integration" } };
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
