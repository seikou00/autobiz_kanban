import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");
const protocolPath = "skills/autodev/autodev-code/references/task-execution.md";
const protocol = readFileSync(new URL(`../${protocolPath}`, import.meta.url), "utf8");
const frontendProtocol = readFileSync(new URL("../skills/autodev/autodev-code/references/frontend-route.md", import.meta.url), "utf8");

function bindingFromPrompt(prompt) {
  assert.ok(prompt.includes(`先读取实现协议 "/plugin/${protocolPath}"`));
  return JSON.parse(prompt.split("\n").find(line => line.startsWith("Batch 绑定：")).slice("Batch 绑定：".length));
}

function harness() {
  const prompts = [];
  const context = vm.createContext({
    pluginPath: "/plugin", feature: "orders", runId: "cw-test", artifactWorkspace: "/artifacts",
    batchWorkspaces: { B001: { workspaceRef: "default" } },
    batchTaskIds: { B001: ["T001"] },
    timeoutPerBatch: 600, setTimeout, clearTimeout,
    BATCH_RESULT_SCHEMA: {},
    stagePath: "/plugin/stage.py",
    usableString: value => typeof value === "string" && value.trim().length > 0,
    batchTaskWorkspace: (_batchId, worktree) => worktree,
    unwrap: value => value, requireSuccess: value => value,
    workflowAgent: async (prompt, options) => {
      prompts.push({ prompt, options });
      if (options.label === "prepare-repair-B001") return { success: true, status: "ready", ...delivery };
      return { status: "success" };
    },
  });
  function load(start, end) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from);
    vm.runInContext(source.slice(from, to), context);
  }
  vm.runInContext(source.slice(0, source.indexOf("function normalizeStructuredOutput(")).replace("export const meta =", "const meta ="), context);
  load("function normalizeStructuredOutput(", "const input = unwrap(args);");
  load("function implementationPrompt(", "async function runInitialBatchLifecycle(");
  load("async function runImplementationRecoveryLifecycle(", "async function runRecoveredBatchLifecycle(");
  load("async function reworkDeliveryImplementation(", "async function recordSingleRepairResolution(");
  return { context, prompts };
}

const delivery = { batchId: "B001", worktreePath: "/worktree", branchName: "batch", commitSha: "draft" };

test("Code draft seal carries a feature summary and Code stage", () => {
  const { context } = harness();
  const prompt = context.implementationPrompt("B001", "/worktree", "batch", ["T001"], "orders", "/worktree/service");
  assert.deepEqual(bindingFromPrompt(prompt), {
    mode: "code", pluginPath: "/plugin", feature: "orders", runId: "cw-test", artifactWorkspace: "/artifacts",
    batchId: "B001", batchWorktree: "/worktree", batchBranch: "batch", taskIds: ["T001"],
    batchWorkspaceRef: "orders", taskWorkspace: "/worktree/service", timeoutPerBatch: 600,
  });
  assert.match(protocol, /seal --purpose review --commit-stage <rework 时为 rework，其余为 code> --commit-summary/);
});

test("frontend implementation starts its TASK before HTML analysis and resource writes", () => {
  assert.match(protocol, /以 .taskContract.uiRequired. 为唯一条件/);
  const todos = frontendProtocol.indexOf("--mark route-todos-created");
  const prepare = frontendProtocol.indexOf("读取原稿和必要项目资料");
  assert.ok(todos >= 0 && todos < prepare);
  const start = frontendProtocol.indexOf("只读准备完成后执行当前 TASK");
  const analysis = frontendProtocol.indexOf("启动成功后才运行 `prepare_html_analysis.py`");
  const parser = frontendProtocol.indexOf("仅当 Route 清单推进到移交 parser 时读取对应 parser");
  assert.ok(prepare >= 0 && prepare < start && start < analysis && analysis < parser);
  assert.match(frontendProtocol, /确认成功并保存真实 TASK runId/);
  assert.match(frontendProtocol, /完成 Route 清单和读取 parser 不属于启动前置条件/);
  assert.match(frontendProtocol, /启动失败时保留原始错误，禁止通过降级路径执行分析、生成产物或写入/);
});

test("frontend repair establishes an active TASK before HTML analysis and resource writes", async () => {
  const { context, prompts } = harness();
  await context.reworkDeliveryImplementation({ ...delivery, failureContext: { failedStage: "review", message: "页面缺少筛选器" } });
  const prompt = prompts.find(item => item.options.label === "rework-implement-B001").prompt;
  assert.equal(bindingFromPrompt(prompt).mode, "rework");
  assert.match(prompt, /TASK 启动成功才写入/);
  assert.match(frontendProtocol, /执行当前 TASK 的 `start` 或 `start-task-repair`，确认成功并保存真实 TASK runId/);
  assert.match(protocol, /只有当前 TASK 的 start 或 start-task-repair 成功才可写入/);
});

test("Review production repairs seal as Rework with full binding", async () => {
  for (const failedStage of ["review"]) {
    const { context, prompts } = harness();
    await context.reworkDeliveryImplementation({ ...delivery, failureContext: { failedStage, message: "筛选失效" } });
    assert.equal(prompts[0].options.label, "prepare-repair-B001");
    const prompt = prompts.find(item => item.options.label === "rework-implement-B001").prompt;
    assert.equal(bindingFromPrompt(prompt).mode, "rework");
    assert.doesNotMatch(prompt, /筛选失效/);
    assert.match(protocol, /从 prepare 的 `contextPath` 取 `repair.failure`/);
    const seal = protocol.split("\n").find(line => line.startsWith("5. 全部指定 TASK"));
    for (const binding of ["--artifact-workspace", "--feature", "--run-id", "--batch-id", "--repo", "--owner-token"]) {
      assert.ok(seal.includes(binding), binding);
    }
  }
});

test("ordinary implementation resume keeps the Code stage", async () => {
  const { context, prompts } = harness();
  await context.reworkDeliveryImplementation(delivery);
  assert.equal(bindingFromPrompt(prompts[0].prompt).mode, "resume");
});

test("an interrupted implementation receives explicit retry mode in its existing worktree", async () => {
  const { context, prompts } = harness();
  context.codeWorkspaces = { default: "/repo" };
  context.batchResults = [];
  context.runDeliveryWithImplementationRepair = async () => delivery;
  await context.runImplementationRecoveryLifecycle(delivery);
  assert.equal(prompts.length, 1);
  const binding = bindingFromPrompt(prompts[0].prompt);
  assert.equal(binding.mode, "retry");
  assert.equal(binding.batchWorktree, delivery.worktreePath);
});
