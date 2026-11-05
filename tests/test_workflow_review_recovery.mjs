import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const delivery = { batchId: "B001", worktreePath: "/tree", branchName: "batch", commitSha: "sealed" };
const passed = { success: true, batchId: "B001", stage: "review", status: "passed" };
const advisory = { issueId: "REVIEW-ADVISORY-B001-1", type: "external_uncertainty", message: "external API behavior is undocumented" };

function harness() {
  const c = vm.createContext({
    stagePath: "/plugin/hooks/parallel_batch_stage.py", pluginPath: "/plugin",
    artifactWorkspace: "/artifacts", feature: "f", runId: "run", controlProtocolVersion: 2,
    batchTaskIds: { B001: ["T001"] }, batchWorkspaces: { B001: { workspaceRef: "default" } },
    errorText: error => error.message || String(error),
  });
  vm.runInContext(source.slice(0, source.indexOf("const input = unwrap(args);")).replace("export const meta =", "const meta ="), c);
  const load = (from, until) => vm.runInContext(source.slice(source.indexOf(from), source.indexOf(until, source.indexOf(from))), c);
  load("function requiresImplementationRework(", "async function runDeliveryReviewAndGate(");
  load("async function runDeliveryReviewAndGate(", "async function reworkDeliveryImplementation(");
  return { c, load };
}

test("Review transport interruption gets one retry driven by its durable checkpoint", async () => {
  const { c } = harness(), labels = [];
  c.workflowAgent = async (_, { label }) => {
    labels.push(label);
    if (label === "stage-review-B001-attempt-1") throw new Error("interrupted");
    if (label === "validate-review-B001-attempt-1") return { ...passed, status: "incomplete", success: false };
    return passed;
  };
  assert.equal((await c.runDeliveryReviewAndGate(delivery)).status, "ready_to_candidate");
  assert.equal(labels.filter(label => label.startsWith("stage-review-")).length, 2);
});

test("reviewer prose cannot replace a missing durable decision", async () => {
  const { c } = harness();
  c.workflowAgent = async (_, { label }) => label.startsWith("stage-review-") ? passed : { status: "incomplete" };
  await assert.rejects(c.runDeliveryReviewAndGate(delivery), /review_stage_not_finalized/);
});

test("Review with external uncertainties proceeds to delivery without a test or classification agent", async () => {
  const { c } = harness(), labels = [];
  c.workflowAgent = async (prompt, { label }) => {
    labels.push(label);
    if (label.startsWith("stage-review-")) {
      assert.match(prompt, /--decision <passed\|failed>/);
      assert.match(prompt, /--advisory-message/);
    }
    return { ...passed, advisories: [advisory] };
  };
  assert.equal((await c.runDeliveryReviewAndGate(delivery)).status, "ready_to_candidate");
  assert.deepEqual(labels, ["stage-review-B001-attempt-1", "validate-review-B001-attempt-1"]);
});

test("proven implementation defects still fail Review even alongside external advisories", async () => {
  const { c } = harness();
  const failure = { type: "implementation", message: "source.py:1 missing null check", nextStage: "implement" };
  c.workflowAgent = async () => ({ ...passed, status: "failed", nextStage: "implement", failure, advisories: [advisory] });
  const result = await c.runDeliveryReviewAndGate(delivery);
  assert.equal(result.status, "implementation_rework_required");
  assert.equal(result.recovery.failureContext.message, failure.message);
});

test("Review schemas expose binary decisions and retain advisory details through projection", () => {
  const { c } = harness();
  vm.runInContext("globalThis.reviewSchemas = [REVIEW_STAGE_RESULT_SCHEMA, REVIEW_VALIDATION_SCHEMA];", c);
  for (const schema of c.reviewSchemas) {
    assert.equal(schema.properties.status.enum.includes("needs_triage"), false);
    const declared = schema.properties.advisories.items.properties;
    const projected = Object.fromEntries(Object.entries(advisory).filter(([key]) => key in declared));
    assert.deepEqual(projected, advisory);
  }
});

test("Runtime-proven sealed repair skips task restart even when the commit is already current", async () => {
  const { c, load } = harness(), labels = [];
  c.leasePath = "/plugin/hooks/batch_lease_manager.py";
  c.batchTaskWorkspace = () => "/tree";
  load("async function reworkDeliveryImplementation(", "async function recordSingleRepairResolution(");
  c.workflowAgent = async (_, { label }) => {
    labels.push(label);
    return { success: true, status: "reused", ...delivery };
  };
  const recovery = { ...delivery, failureContext: { failedStage: "review", failureType: "implementation", message: "recorded finding" } };
  const result = await c.reworkDeliveryImplementation(recovery);
  assert.equal(result.repairReused, true);
  assert.equal(result.commitSha, delivery.commitSha);
  assert.deepEqual(labels, ["prepare-repair-B001"]);

  load("async function runDeliveryWithImplementationRepair(", "async function continueRecoveredDelivery(");
  let first = true;
  c.runDeliveryReviewAndGate = async () => {
    if (!first) return { ...delivery, status: "ready_to_candidate" };
    first = false;
    return { status: "implementation_rework_required", failedStage: "review", recovery };
  };
  c.recordSingleRepairResolution = async () => {};
  assert.equal((await c.runDeliveryWithImplementationRepair(delivery)).status, "ready_to_candidate");
});

test("an implementer cannot claim Runtime repair reuse through its own response", async () => {
  const { c, load } = harness();
  c.leasePath = "/plugin/hooks/batch_lease_manager.py";
  c.batchTaskWorkspace = () => "/tree";
  c.CODE_STAGE_EXECUTION_BOUNDARY = "";
  c.CODE_STAGE_COMPILE_COMMAND_GUIDANCE = "";
  c.timeoutPerBatch = 60;
  load("async function reworkDeliveryImplementation(", "async function recordSingleRepairResolution(");
  c.workflowAgent = async (_, { label }) => label.startsWith("prepare-repair-")
    ? { success: true, status: "ready", ...delivery }
    : { success: true, status: "success", ...delivery, repairReused: true };
  const result = await c.reworkDeliveryImplementation({ ...delivery, failureContext: { failedStage: "review", message: "finding" } });
  assert.equal(result.repairReused, false);
});
