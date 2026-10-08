import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const wrap = (final_result, exit_code = 0) => ({
  command: "python parallel_merge_train.py",
  run_in_background: true,
  task_id: "f005fa70",
  exit_code,
  final_result,
});
const built = { success: true, status: "built", candidateSha: "candidate-sha" };
const conflicted = {
  ok: false, success: false, status: "candidate_conflicted",
  conflictContext: { candidateWorktree: "/tmp/candidate", conflictedFiles: ["src/app.js"] },
};
const promoted = { success: true, promoted: true, batchIds: ["B009"] };

function harness(responses = []) {
  const calls = [];
  const records = [];
  const context = vm.createContext({
    batchWorkspaces: { B009: { workspaceRef: "default" } },
    mergeTrainPath: "/plugin/hooks/parallel_merge_train.py",
    artifactWorkspace: "/tmp/workspace", feature: "feature", runId: "run",
    errorText: value => String(value),
    recordUnresolved: record => records.push(record),
    finalRepairResults: [], mergeResults: [],
    cleanupMergedWorktrees: async () => {},
    markBatchResolved: () => {}, markCandidateResolved: () => {},
    workflowAgent: async (_instruction, options) => {
      calls.push(options.label);
      assert.ok(responses.length, `Unexpected agent call: ${options.label}`);
      return responses.shift();
    },
  });
  function load(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `Missing workflow section: ${start}`);
    vm.runInContext(source.slice(from, to), context);
  }
  load("function normalizeStructuredOutput(", "const input = unwrap(args);");
  load("function mergedBatchIds(", "async function cleanupMergedWorktrees(");
  load("async function validateAndPromoteBatch(", "async function promoteReadyBatch(");
  load("async function attemptFinalCandidateRepair(", "async function runFinalRepairAndReport(");
  return { context, calls, records };
}

test("unwrap exposes command results through JSON, value and nested background envelopes", () => {
  const { context } = harness();
  for (const input of [
    conflicted,
    wrap(conflicted, 1),
    JSON.stringify(wrap(conflicted, 1)),
    { value: JSON.stringify(wrap(JSON.stringify(conflicted), 1)) },
    wrap(wrap(conflicted, 1)),
  ]) {
    const result = context.unwrap(input);
    assert.equal(result.status, "candidate_conflicted");
    assert.equal(result.conflictContext.candidateWorktree, "/tmp/candidate");
    assert.equal(context.hasFailureSignal(input), true);
    assert.throws(() => context.requireSuccess(input, "build"));
  }
  assert.equal(context.requireSuccess(wrap(built), "build").candidateSha, "candidate-sha");
});

test("failure checks reject malformed final results and unsuccessful command envelopes", () => {
  const { context } = harness();
  for (const input of [
    wrap({ status: "candidate_conflicted" }),
    wrap({ ok: false }), wrap({ success: false }), wrap({ passed: false }),
    wrap({ failure: { verdict: "FAIL" } }),
    wrap(built, 1), wrap(built, "1"),
    { ...wrap(built), success: false },
    { ...wrap(built), failure: { verdict: "FAIL" } },
    wrap(wrap(built, 1)),
    { command: "build-candidate", run_in_background: true, task_id: "pending" },
    { command: "build-candidate", run_in_background: true, task_id: "done", exit_code: 0 },
    wrap({}),
    ...[null, undefined, "", "invalid JSON", "null", "[]", "true", 42, []].map(value => wrap(value)),
  ]) {
    assert.throws(() => context.requireSuccess(input, "command"), JSON.stringify(input));
  }
  assert.equal(context.unwrap({ status: "passed", value: 42 }).value, 42);
});

test("wrapped conflict resolves the retained candidate before promotion", async () => {
  const { context, calls, records } = harness([
    { value: JSON.stringify(wrap(conflicted, 1)) }, wrap(built), wrap(promoted),
  ]);
  const result = await context.validateAndPromoteBatch("B009", 9);
  assert.deepEqual(calls, [
    "build-candidate-default-B009-9-1",
    "resolve-conflicted-candidate-default-B009-9",
    "promote-candidate-default-B009-9-1",
  ]);
  assert.equal(result.promoted, true);
  assert.equal(result.batchIds.join(","), "B009");
  assert.equal(records.length, 0);
});

test("direct successful command results still promote without resolution", async () => {
  const { context, calls } = harness([built, promoted]);
  assert.equal((await context.validateAndPromoteBatch("B009", 9)).promoted, true);
  assert.equal(calls.length, 2);
});

test("wrapped build failures stop before promotion", async () => {
  for (const failed of [wrap({ success: false, status: "failed" }, 1), wrap(built, 1), wrap("invalid JSON")]) {
    const { context, calls, records } = harness([failed]);
    await assert.rejects(context.validateAndPromoteBatch("B009", 9), /build candidate default failed/);
    assert.equal(calls.length, 1);
    assert.equal(records[0].status, "build_failed");
  }
});

test("failed resolution retains conflict context and never promotes", async () => {
  for (const failed of [wrap({ success: false, status: "needs_resolution" }, 1), wrap(built, 1)]) {
    const { context, calls, records } = harness([wrap(conflicted, 1), failed]);
    await assert.rejects(context.validateAndPromoteBatch("B009", 9), /immediate_candidate_resolution_failed/);
    assert.equal(calls.length, 2);
    assert.equal(records[0].status, "needs_resolution");
    assert.equal(records[0].worktreePath, "/tmp/candidate");
    assert.equal(records[0].conflictedFiles.join(","), "src/app.js");
  }
});

test("wrapped stale promotion rebuilds the candidate once", async () => {
  const { context, calls } = harness([
    wrap(built), wrap({ success: false, stale: true }, 1), wrap(built), wrap(promoted),
  ]);
  assert.equal((await context.validateAndPromoteBatch("B009", 9)).promoted, true);
  assert.deepEqual(calls, [
    "build-candidate-default-B009-9-1", "promote-candidate-default-B009-9-1",
    "build-candidate-default-B009-9-2", "promote-candidate-default-B009-9-2",
  ]);
});

test("wrapped failed promotion cannot become a successful batch attribution", async () => {
  const { context, calls } = harness([wrap(built), wrap({ success: false, error: "rejected" }, 1)]);
  await assert.rejects(context.validateAndPromoteBatch("B009", 9), /promote candidate default failed/);
  assert.equal(calls.length, 2);
});

test("final repair accepts wrapped resolution but stops on command failure", async () => {
  const record = { repositoryRef: "default", wave: 9, batchIds: ["B009"] };
  const success = harness([wrap(built), wrap(promoted)]);
  await success.context.attemptFinalCandidateRepair(record);
  assert.equal(success.context.finalRepairResults[0].status, "promoted");
  const failure = harness([wrap(built, 1)]);
  await failure.context.attemptFinalCandidateRepair(record);
  assert.equal(failure.calls.length, 1);
  assert.equal(failure.records[0].status, "needs_resolution");
});
