import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");

function harness(testPreparation = { success: true, batchId: "B001", status: "test_required",
  utestPolicy: { stageSeconds: 600, firstExecutionSeconds: 600, commandSeconds: 300, digest: "runtime-policy" },
  engineeringContext: { brief: { facts: [{ symbol: "example.SharedApi", contentPreview: "current signature" }] } } }) {
  const prompts = [];
  const context = vm.createContext({
    feature: "orders", runId: "cw-test", artifactWorkspace: "/artifacts",
    batchWorkspaces: { B001: { workspaceRef: "default" } },
    batchTaskIds: { B001: ["T001"] },
    timeoutPerBatch: 600, setTimeout, clearTimeout,
    BATCH_RESULT_SCHEMA: {}, UTEST_STAGE_SCHEMA: {},
    CODE_STAGE_EXECUTION_BOUNDARY: "", CODE_STAGE_COMPILE_COMMAND_GUIDANCE: "",
    leasePath: "/plugin/lease.py", stagePath: "/plugin/stage.py",
    schedulerPath: "/plugin/scheduler.py", worktreeManagerPath: "/plugin/worktree_manager.py",
    routeResolverPath: "/plugin/route.py", utestCommandPath: "/plugin/utest.py",
    utestRuntimePath: "/plugin/utest_runtime.py",
    usableString: value => typeof value === "string" && value.trim().length > 0,
    batchTaskWorkspace: (_batchId, worktree) => worktree,
    unwrap: value => value, requireSuccess: value => value,
    workflowAgent: async (prompt, options) => {
      prompts.push({ prompt, options });
      if (options.label === "prepare-utest-B001") return testPreparation;
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

test("frontend implementation starts its TASK before HTML analysis and resource writes", () => {
  const { context } = harness();
  const prompt = context.implementationPrompt("B001", "/worktree", "batch", ["T001"], "default", "/worktree");
  const prepare = prompt.indexOf("先在本 agent 内完成 Route 只读准备");
  const start = prompt.indexOf("按 context 的 startArgv 执行当前 TASK 的 task_runner.py start");
  const analysis = prompt.indexOf("start 成功后才能执行 prepare_html_analysis.py");
  const parser = prompt.indexOf("仅当清单推进到转交 parser 后才读取对应 parser");
  assert.ok(prepare >= 0 && prepare < start && start < analysis && analysis < parser);
  assert.match(prompt, /确认 start 成功并保存真实 TASK runId/);
  assert.match(prompt, /Route 清单完成和 parser 读取不是 start 的前置条件/);
  assert.match(prompt, /start 失败不属于脚本降级，禁止执行分析脚本、写产物或复制资源/);
});

test("frontend repair establishes an active TASK before HTML analysis and resource writes", async () => {
  const { context, prompts } = harness();
  await context.reworkDeliveryImplementation(delivery);
  const prompt = prompts[0].prompt;
  const repair = prompt.indexOf("执行 task_runner.py start-task-repair");
  const start = prompt.indexOf("执行普通 task_runner.py start");
  const analysis = prompt.indexOf("上述 start-task-repair 或 start 成功并保存真实 runId 后，才能执行 prepare_html_analysis.py");
  assert.ok(repair >= 0 && start >= 0 && repair < analysis && start < analysis);
  assert.match(prompt, /TASK 启动失败不得走脚本降级继续写入/);
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
  const prompt = prompts.find(item => item.options.label === "stage-utest-B001").prompt;
  assert.match(prompt, /seal --commit-stage utest --commit-summary/);
  assert.match(prompt, /第一次 source_compile\/source_bug 时，先按步骤 8 的封存命令 seal/);
  assert.match(prompt, /按步骤 8 的同一封存命令 seal 后执行 record-test-failure/);
  assert.match(prompt, /不写测试通过/);
});

test("frontend UTest skips before launching any test generation or execution agent", async () => {
  const skipped = { success: true, ...delivery, status: "skipped", testStatus: "skipped", skipReason: "frontend_utest_temporarily_disabled" };
  for (const failure of [null, { failureClassification: "source_compile", commandId: "old" }]) {
    const { context, prompts } = harness(skipped);
    const result = await context.runBatchUtestAndSeal(delivery, failure);
    assert.equal(result.testStatus, "skipped");
    assert.equal(result.commitSha, "draft");
    assert.equal(result.status, "success");
    assert.equal(prompts.length, 1);
    assert.match(prompts[0].prompt, /prepare-test/);
    assert.doesNotMatch(prompts[0].prompt, /preflight|record-test-failure|seal --commit-stage/);
  }
});

test("backend UTest continues through its existing test agent", async () => {
  const { context, prompts } = harness();
  await context.runBatchUtestAndSeal(delivery);
  assert.deepEqual(prompts.map(item => item.options.label), ["prepare-utest-B001", "stage-utest-B001"]);
  assert.match(prompts[1].prompt, /example.SharedApi/);
  assert.match(prompts[1].prompt, /不要求从零探索/);
  assert.equal(prompts[1].options.timeoutMs, 600000);
});

test("missing Runtime policy fails closed instead of using a different deadline", async () => {
  const { context, prompts } = harness({ success: true, batchId: "B001", status: "test_required" });
  await assert.rejects(context.runBatchUtestAndSeal(delivery), /utest_runtime_policy_missing/);
  assert.equal(prompts.length, 1);
});

test("changing an installed Runtime after launch stops before UTest discovery", async () => {
  const { context, prompts } = harness();
  context.runtimeContractDigest = "expected-deployment";
  await assert.rejects(context.runBatchUtestAndSeal(delivery), /utest_runtime_deployment_changed/);
  assert.equal(prompts.length, 1);
});

test("UTest deadline monitor runs independently of dispatch polling and retains asset ownership", async () => {
  const { context, prompts } = harness();
  let timerCallback, completeChild;
  context.setTimeout = callback => { timerCallback = callback; return 1; };
  context.clearTimeout = () => {};
  context.workflowAgent = async (prompt, options) => {
    prompts.push({ prompt, options });
    if (options.label === "prepare-utest-B001") return { success: true, batchId: "B001", status: "test_required",
      utestPolicy: { stageSeconds: 1800, firstExecutionSeconds: 600, commandSeconds: 300, digest: "runtime-policy" } };
    if (options.label === "stage-utest-B001") return new Promise(resolve => { completeChild = resolve; });
    assert.equal(options.label, "utest-budget-B001");
    assert.match(prompt, /check-test-budget/);
    assert.match(prompt, /保留封存资产所需 lease/);
    completeChild({ success: true, testStatus: "deferred" });
    return { success: true, batchId: "B001", status: "stop_requested" };
  };
  const run = context.runBatchUtestAndSeal(delivery);
  await new Promise(resolve => setImmediate(resolve));
  timerCallback();
  const result = await run;
  assert.equal(result.testStatus, "deferred");
  assert.equal(prompts.length, 3);
});

test("invalid UTest policy output stops before a test agent can run", async () => {
  const { context, prompts } = harness({ batchId: "B001", status: "unknown" });
  await assert.rejects(context.runBatchUtestAndSeal(delivery), /utest_preparation_invalid/);
  assert.equal(prompts.length, 1);
});
