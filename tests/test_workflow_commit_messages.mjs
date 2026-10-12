import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");

function harness() {
  const prompts = [];
  const context = vm.createContext({
    feature: "orders", runId: "cw-test", artifactWorkspace: "/artifacts",
    batchWorkspaces: { B001: { workspaceRef: "default" } },
    batchTaskIds: { B001: ["T001"] },
    timeoutPerBatch: 600, UTEST_STAGE_TIMEOUT_SECONDS: 1200,
    BATCH_RESULT_SCHEMA: {}, UTEST_STAGE_SCHEMA: {},
    CODE_STAGE_EXECUTION_BOUNDARY: "", CODE_STAGE_COMPILE_COMMAND_GUIDANCE: "",
    leasePath: "/plugin/lease.py", stagePath: "/plugin/stage.py",
    schedulerPath: "/plugin/scheduler.py", worktreeManagerPath: "/plugin/worktree_manager.py",
    routeResolverPath: "/plugin/route.py", utestCommandPath: "/plugin/utest.py",
    usableString: value => typeof value === "string" && value.trim().length > 0,
    batchTaskWorkspace: (_batchId, worktree) => worktree,
    unwrap: value => value, requireSuccess: value => value,
    workflowAgent: async (prompt, options) => {
      prompts.push({ prompt, options });
      return { status: "success" };
    },
  });
  function load(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from);
    vm.runInContext(source.slice(from, to), context);
  }
  load("function implementationPrompt(", "async function runInitialBatchLifecycle(");
  load("async function reworkDeliveryImplementation(", "async function recordSingleRepairResolution(");
  load("async function runBatchUtestAndSeal(", "async function runDeliveryReviewTestAndGate(");
  return { context, prompts };
}

const delivery = { batchId: "B001", worktreePath: "/worktree", branchName: "batch", commitSha: "draft" };

test("Code draft seal carries a feature summary and Code stage", () => {
  const { context } = harness();
  const prompt = context.implementationPrompt("B001", "/worktree", "batch", ["T001"], "default", "/worktree");
  assert.match(prompt, /seal --purpose review --commit-stage code --commit-summary/);
});

test("review and UTest production repairs seal as Rework with full binding", async () => {
  for (const failedStage of ["review", "test"]) {
    const { context, prompts } = harness();
    await context.reworkDeliveryImplementation({ ...delivery, failureContext: { failedStage, message: "筛选失效" } });
    assert.match(prompts[0].prompt, /seal --purpose review --commit-stage rework --commit-summary/);
    for (const binding of ["--artifact-workspace", "--feature", "--run-id", "--batch-id", "--repo", "--owner-token"]) {
      assert.ok(prompts[0].prompt.includes(binding), binding);
    }
  }
});

test("ordinary implementation resume keeps the Code stage", async () => {
  const { context, prompts } = harness();
  await context.reworkDeliveryImplementation(delivery);
  assert.match(prompts[0].prompt, /seal --purpose review --commit-stage code --commit-summary/);
});

test("UTest success and failure paths share the UTest seal command", async () => {
  const { context, prompts } = harness();
  await context.runBatchUtestAndSeal(delivery);
  const prompt = prompts[0].prompt;
  assert.match(prompt, /seal --commit-stage utest --commit-summary/);
  assert.match(prompt, /第一次 source_compile\/source_bug 时，先按步骤 8 的封存命令 seal/);
  assert.match(prompt, /按步骤 8 的同一封存命令 seal 后执行 record-test-failure/);
  assert.match(prompt, /不写测试通过/);
});
