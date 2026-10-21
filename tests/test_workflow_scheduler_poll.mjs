import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../workflows/code-batched-execution.workflow.js", import.meta.url), "utf8");

function harness() {
  const events = [], failures = [];
  const state = {
    runId: "run", status: "running", maxParallel: 2, activeWorkers: 1, observedEpochMs: 1000,
    scheduledGroups: [], readyBatches: [], allReadyBatches: [], mergeableBatches: [],
    implementationRecoveryBatches: [], stageRecoveryBatches: [], retryPendingBatches: [],
    blockedBatches: [], parallelGroups: [], allParallelGroups: [],
    batchTaskIds: {}, batchWorkspaces: {}, dispatchGrants: {},
    manifest: { batches: {}, mergeTrains: {}, deferredIssues: [] },
  };
  const context = vm.createContext({
    pluginPath: "/plugin", schedulerPath: "/plugin/hooks/parallel_batch_scheduler.py",
    artifactWorkspace: "/artifacts", feature: "feature", runId: "run",
    SCHEDULER_RESULT_SCHEMA: {}, schedulerReadInFlight: null, lastScheduler: state,
    maxParallel: 2, timeoutPerBatch: 3600, schedulerCycles: 1,
    activeDispatchGrants: new Map(), dispatchGrants: {}, retryPriorityBatchIds: new Set(),
    runnableScheduledBatchIds: () => [],
    runnableImplementationRecoveries: () => [], runnableStageRecoveries: () => [],
    runnableMergeableBatchIds: () => [], isValidBatchId: value => /^B\d+$/.test(value),
    recordUnresolved: value => assert.fail(JSON.stringify(value)), errorText: String,
    recordSchedulerFailure: (label, error) => failures.push({ label, error }),
    markSchedulerRecovered() {},
    applySchedulerState: value => { context.lastScheduler = value; return value; },
  });
  vm.runInContext(source.slice(0, source.indexOf("const input = unwrap(args);")).replace("export const meta =", "const meta ="), context);
  for (const [start, end] of [
    ["const WORKFLOW_AUTONOMY_PREFIX", "const aggregatePath"],
    ["function normalizeScheduledGroups(", 'phase("准备");'],
    ["async function readSchedulerState(", "async function deferBatchForRetry("],
    ["function takeNextRunnableLifecycle(", "async function drainRunnableLifecycles("],
  ]) {
    vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))), context);
  }
  context.waitForDispatchChange = async () => { events.push("wait"); return "poll"; };
  return { context, state, events, failures };
}

for (const capacityPoll of [false, true]) {
  for (const failure of ["empty", "wrapped-empty", "empty-error", "command-error", "failed-result"]) {
    test(`${capacityPoll ? "capacity" : "idle"} poll waits for the next interval after ${failure}`, async () => {
      const { context: c, state, events, failures } = harness();
      c.maxParallel = capacityPoll ? 1 : 2;
      const labels = [];
      c.agent = async (_, options) => {
        labels.push(options.label);
        events.push("read");
        assert.equal(c.lastScheduler, state);
        if (labels.length === 1) {
          if (failure === "empty") return null;
          if (failure === "wrapped-empty") return { value: " " };
          if (failure === "empty-error") throw new Error("Received empty response from chat model call");
          if (failure === "command-error") throw new Error("status command unavailable");
          return { success: false, error: "status failed" };
        }
        assert.equal(labels.length, 2);
        return { ...state, activeWorkers: 0, observedEpochMs: 2000 };
      };
      await c.runDispatchWorker(2, new Set(), "drain", { active: 0, ranAny: false, runningBatchIds: new Set() });
      const label = capacityPoll ? "drain-capacity-poll-2-1" : "drain-poll-2-1";
      assert.deepEqual(labels, [label, label]);
      assert.deepEqual(events, ["wait", "read", "wait", "read"]);
      assert.equal(failures.length, 1);
      assert.equal(c.lastScheduler.activeWorkers, 0);
      assert.equal(c.schedulerReadInFlight, null);
    });
  }
}

test("agent calls delegate transport recovery to the platform and preserve dispatch identity", async () => {
  for (const result of [null, { value: " " }, { batchId: "B001", status: "failed" }]) {
    const { context: c } = harness(), labels = [];
    c.activeDispatchGrants.set("B001", { generation: 2, ownerToken: "dispatch-owner" });
    c.agent = async (instruction, options) => {
      labels.push(options.label);
      assert.match(instruction, /--dispatch-generation 2 --dispatch-token "dispatch-owner"/);
      return result;
    };
    assert.equal(await c.workflowAgent("run task", { label: "task-B001" }), result);
    assert.deepEqual(labels, ["task-B001"]);
  }
});

test("agent transport errors propagate without workflow retries", async () => {
  const { context: c } = harness(), labels = [];
  const failure = new Error("Received empty response from chat model call");
  c.agent = async (_, options) => { labels.push(options.label); throw failure; };
  await assert.rejects(c.workflowAgent("run task", { label: "task-B001" }), error => error === failure);
  assert.deepEqual(labels, ["task-B001"]);
});

test("child phases limit managed background execution to plugin Python scripts", async () => {
  const { context: c } = harness();
  for (const label of ["fixed-workflow-prepare", "provision-worktree-B001", "fixed-batch-B001",
    "stage-review-B001-attempt-1", "stage-utest-B001", "build-candidate-default-B001-1-1",
    "promote-candidate-default-B001-1-1", "cleanup-merged-B001", "run-e2e-validation"]) {
    const options = { label, timeoutMs: 300000, schema: {} };
    const result = { success: true };
    c.agent = async (instruction, received) => {
      assert.equal(received, options);
      assert.match(instruction, /仅本 Workflow 提示中明确调用的插件 Python 脚本/);
      assert.match(instruction, /execute\(\{command:<原来的完整 Python 脚本命令>,run_in_background:true\}\)/);
      assert.match(instruction, /task_output\(\{task_id:<同一 ID>,timeout:60000\}\)/);
      assert.match(instruction, /每次脚本调用只启动一次/);
      assert.match(instruction, /轮询等待超时或任务仍在运行只继续等待同一任务/);
      assert.match(instruction, /lease release 和子 Agent 返回均须等待脚本终态/);
      assert.match(instruction, /原阶段预算、Runtime deadline 和显式子 Agent 超时继续生效/);
      assert.match(instruction, /不适用于业务命令、Bash 编译\/测试\/环境查询、Git 命令或 Runtime 返回的 bashCommand/);
      assert.match(instruction, /禁止把它们合并进插件脚本的后台调用/);
      assert.match(instruction, /原始阶段指令$/);
      return result;
    };
    assert.equal(await c.workflowAgent("原始阶段指令", options), result);
  }
});

test("Bash compilation keeps its foreground execution and exit-code recording", () => {
  const { context: c } = harness();
  vm.runInContext(source.slice(source.indexOf("const CODE_STAGE_COMPILE_COMMAND_GUIDANCE ="),
    source.indexOf("function taskWorkspacePath(")), c);
  const guidance = vm.runInContext("CODE_STAGE_COMPILE_COMMAND_GUIDANCE", c);
  assert.match(guidance, /^编译和环境查询使用前台 Bash。/);
  assert.match(guidance, /PIPESTATUS/);
  assert.match(guidance, /record-compile/);
  assert.match(guidance, /动态执行和后台进程/);
  assert.doesNotMatch(guidance, /run_in_background|task_output/);
});

function initialLifecycleHarness() {
  const { context: c } = harness();
  Object.assign(c, {
    batchWorkspaces: { B001: { workspaceRef: "default" } },
    batchTaskIds: { B001: ["T001"] }, codeWorkspaces: { default: "/source" }, batchResults: [],
    worktreeManagerPath: "/plugin/hooks/worktree_manager.py",
    batchTaskWorkspace: (_id, worktree) => worktree,
    implementationPrompt: () => "implement current task",
    runDeliveryWithImplementationRepair: async result => result,
    safelyDeferBatchForRetry: async batchId => ({ batchId, status: "retry_pending" }),
  });
  vm.runInContext(source.slice(source.indexOf("async function runInitialBatchLifecycle("),
    source.indexOf("async function runImplementationRecoveryLifecycle(")), c);
  return c;
}

test("provision uses managed background execution with its five-minute budget", async () => {
  const c = initialLifecycleHarness(), prompts = [];
  c.agent = async (instruction, options) => {
    prompts.push({ instruction, options });
    return options.label.startsWith("provision-")
      ? { success: true, batchId: "B001", repositoryRef: "default", worktreePath: "/worktree", branchName: "batch" }
      : { batchId: "B001", status: "success" };
  };
  assert.equal((await c.runInitialBatchLifecycle("B001")).status, "success");
  assert.deepEqual(prompts.map(({ options }) => options.label), ["provision-worktree-B001", "fixed-batch-B001"]);
  assert.equal(prompts[0].options.timeoutMs, 300000);
  assert.match(prompts[0].instruction, /本 provision 步骤的总预算仍为 5 分钟/);
  assert.doesNotMatch(prompts[0].instruction, /等待前台命令完成|禁止[^。]*run_in_background/);
});

test("an unfinished background task cannot advance provision to implementation", async () => {
  const c = initialLifecycleHarness(), calls = [];
  c.agent = async (_, options) => {
    calls.push(options.label);
    return { run_in_background: true, task_id: "still-running" };
  };
  assert.equal((await c.runInitialBatchLifecycle("B001")).status, "retry_pending");
  assert.deepEqual(calls, ["provision-worktree-B001"]);
  assert.equal(c.batchResults.length, 0);
});
