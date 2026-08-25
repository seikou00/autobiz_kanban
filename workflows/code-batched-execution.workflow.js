export const meta = {
  name: "code-batched-execution",
  description: "Fixed DAG execution for Code batches with merge-before-release semantics",
  whenToUse: "由 workflow_launcher.py 在存在合法待执行 Batch 时调用",
  phases: [
    { title: "准备", detail: "创建或恢复 scheduler run 并计算当前可执行 DAG 波次" },
    { title: "实现", detail: "平台为每个 Batch agent 创建原生 Git worktree，同一波次并行执行" },
    { title: "合并", detail: "每个波次完成后立即确定性合并，才释放下游依赖" },
    { title: "最终验证", detail: "所有 Batch 合并后执行最终编译门禁" }
  ]
};

const DEFAULT_MAX_PARALLEL = 4;
const MAX_SCHEDULER_WAVES = 100;
const BATCH_RESULT_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    status: { enum: ["success", "failed", "timeout"] },
    tasksCompleted: { type: "number" },
    tasksTotal: { type: "number" },
    compileStatus: { enum: ["passed", "failed", "skipped"] },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    commitSha: { type: "string" },
    errorMessage: { type: "string" }
  },
  required: ["batchId", "status", "compileStatus", "worktreePath", "branchName", "commitSha"],
  additionalProperties: false
};
const MERGE_RESULT_SCHEMA = {
  type: "object",
  properties: {
    success: { type: "boolean" },
    merged: { type: "array", items: { type: "object" } },
    failed: { type: "array", items: { type: "object" } },
    totalConflicts: { type: "number" }
  },
  required: ["success"],
  additionalProperties: false
};
const SCHEDULER_RESULT_SCHEMA = {
  type: "object",
  properties: {
    runId: { type: "string" },
    status: { type: "string" },
    scheduledGroups: { type: "array", items: { type: "array", items: { type: "string" } } },
    batchTaskIds: {
      type: "object",
      additionalProperties: { type: "array", items: { type: "string" } }
    },
    batchWorkspaces: { type: "object" }
  },
  required: ["runId", "status", "scheduledGroups"],
  additionalProperties: true
};
const VERIFICATION_SCHEMA = {
  type: "object",
  properties: {
    passed: { type: "boolean" },
    commands: { type: "array", items: { type: "object" } },
    errorMessage: { type: "string" }
  },
  required: ["passed"],
  additionalProperties: false
};
const WORKFLOW_HOST_SCHEMA = {
  type: "object",
  properties: {
    gitRoot: { type: "string" },
    branchName: { type: "string" }
  },
  required: ["gitRoot", "branchName"],
  additionalProperties: false
};

function unwrap(value) {
  if (value && typeof value === "object" && typeof value.value === "string") return unwrap(value.value);
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch (_) {
      return { raw: value };
    }
  }
  return value || {};
}

function requireSuccess(value, label) {
  const result = unwrap(value);
  if (
    !result ||
    result.ok === false ||
    result.success === false ||
    result.passed === false ||
    ["failed", "error", "timeout", "blocked", "needs_resolution"].includes(result.status)
  ) {
    throw new Error(`${label} failed: ${JSON.stringify(result)}`);
  }
  return result;
}

function usableString(value) {
  return typeof value === "string"
    && value.trim().length > 0
    && !["undefined", "null"].includes(value.trim().toLowerCase());
}

function absolutePath(value) {
  return usableString(value) && value.startsWith("/");
}

const input = unwrap(args);
const feature = input.feature;
const pluginPath = input.pluginPath;
const artifactWorkspace = input.artifactWorkspace || input.workspace;
const codeWorkspaces = input.codeWorkspaces || (input.codeWorkspace ? { default: input.codeWorkspace } : null);
const workflowHostGitRoot = input.workflowHostGitRoot;
const maxParallel = Number.isInteger(input.maxParallel) && input.maxParallel > 0
  ? input.maxParallel
  : DEFAULT_MAX_PARALLEL;
const timeoutPerBatch = Number.isInteger(input.timeoutPerBatch) && input.timeoutPerBatch > 0
  ? input.timeoutPerBatch
  : 3600;

if (
  !usableString(feature)
  || !absolutePath(pluginPath)
  || !absolutePath(artifactWorkspace)
  || !absolutePath(workflowHostGitRoot)
  || !codeWorkspaces
  || typeof codeWorkspaces !== "object"
  || Object.keys(codeWorkspaces).length === 0
) {
  throw new Error("missing_feature_plugin_path_artifact_workspace_workflow_host_or_code_workspaces");
}
if (Object.values(codeWorkspaces).some(path => !absolutePath(path))) {
  throw new Error("invalid_code_workspace_path");
}
if (new Set(Object.values(codeWorkspaces)).size !== 1) {
  throw new Error("platform_worktree_multi_repository_requires_split_workflows");
}

const schedulerPath = `${pluginPath}/hooks/parallel_batch_scheduler.py`;
const leasePath = `${pluginPath}/hooks/batch_lease_manager.py`;
const taskRunnerPath = `${pluginPath}/hooks/task_runner.py`;
const worktreeManagerPath = `${pluginPath}/hooks/worktree_manager.py`;
const mergerPath = `${pluginPath}/hooks/batch_merger.py`;
const finalVerifyPath = `${pluginPath}/hooks/parallel_final_verify.py`;
const codeWorkspaceArgs = Object.entries(codeWorkspaces)
  .map(([workspaceRef, path]) => `--code-workspace "${workspaceRef}=${path}"`)
  .join(" ");

phase("准备");
const workflowHost = requireSuccess(await agent(
  `执行 pwd、git rev-parse --show-toplevel 和 git branch --show-current，确认当前 Workflow 宿主工作区的 Git 根。` +
  `它必须精确等于 "${workflowHostGitRoot}"；否则返回失败，不得创建 scheduler run。` +
  `只返回 {gitRoot, branchName}。`,
  { label: "verify-workflow-host", phase: "准备", schema: WORKFLOW_HOST_SCHEMA }
), "workflow host verification");
if (workflowHost.gitRoot !== workflowHostGitRoot) {
  throw new Error(`platform_worktree_host_mismatch:expected=${workflowHostGitRoot}:actual=${workflowHost.gitRoot}`);
}

phase("准备");
const prepared = requireSuccess(await agent(
  `确保固定 Code DAG run。执行：python "${schedulerPath}" ensure ` +
  `--workspace "${artifactWorkspace}" --feature "${feature}" ` +
  `--max-parallel ${maxParallel} ` +
  `--timeout-seconds ${timeoutPerBatch} --allow-bootstrap ${codeWorkspaceArgs}。` +
  `已有可恢复 run 时必须返回其原 runId，不得创建第二个 run。` +
  `必要时允许 scheduler 创建 autodev baseline 提交以供平台创建 worktree；不得修改业务文件内容，` +
  `且不得把 .cmbdevclaw 平台运行文件纳入提交。只返回该命令的 JSON 结果。`,
  { label: "fixed-workflow-prepare", phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
), "scheduler ensure");

const runId = prepared.runId;
let scheduledGroups = prepared.scheduledGroups || [];
let mergeableBatches = prepared.mergeableBatches || [];
let batchTaskIds = prepared.batchTaskIds || {};
let batchWorkspaces = prepared.batchWorkspaces || {};
const batchResults = [];
const mergeResults = [];
let schedulerWaves = 0;

if (!scheduledGroups.length && !mergeableBatches.length && !["verifying", "succeeded"].includes(prepared.status)) {
  throw new Error(JSON.stringify({ error: "parallel_scheduler_stalled", runId, scheduler: prepared, batchResults, mergeResults }));
}

while (scheduledGroups.length > 0 || mergeableBatches.length > 0) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    throw new Error(JSON.stringify({ error: "parallel_scheduler_wave_limit_exceeded", runId, schedulerWaves, batchResults, mergeResults }));
  }

  if (scheduledGroups.length > 0) {
    phase("实现");
    const batchIds = scheduledGroups.flat();
    const waveResults = await parallel(
      batchIds.map(batchId => () => {
      const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
      if (taskIds.length === 0) throw new Error(`scheduler returned no task IDs for ${batchId}`);
      const batchWorkspace = batchWorkspaces[batchId] || {};
      const batchWorkspaceRef = batchWorkspace.workspaceRef;
      if (!batchWorkspaceRef || !codeWorkspaces[batchWorkspaceRef]) {
        throw new Error(`scheduler did not provide a code workspace for ${batchId}`);
      }
      return agent(
      `在平台分配的原生业务仓库 worktree 中执行 Batch ${batchId}。Feature=${feature}，runId=${runId}，` +
      `artifact workspace=${artifactWorkspace}。严格按以下固定顺序执行：\n` +
      `1. 本 agent 已通过平台 isolation: "worktree" 获得独立 checkout。执行 pwd、git rev-parse --show-toplevel、git branch --show-current，保存 Git 根为 batchWorktree、分支为 batchBranch。batchWorktree 必须不等于主业务 checkout "${codeWorkspaces[batchWorkspaceRef]}"；后续 scheduler 会验证它与该主仓库共享 Git worktree registry。禁止 git worktree add/remove、git switch、merge、rebase 或操作其他 checkout。\n` +
      `2. 执行 python "${leasePath}" acquire --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"，从 JSON 的 lease.ownerToken 保存本 Batch 的 lease token。\n` +
      `3. 用刚才采集的 batchWorktree 和 batchBranch 执行 python "${schedulerPath}" mark-batch --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status running --worktree-path "<batchWorktree>" --branch-name "<batchBranch>"。业务源码命令只在当前分配 checkout 内执行。\n` +
      `4. Scheduler 已提供本 Batch 的 task IDs：${JSON.stringify(taskIds)}。不要用 read_file 读取 artifact 目录；artifact workspace 不是代码目录。逐个执行这些 TASK：先执行 python "${pluginPath}/hooks/code_task_context.py" --workspace "${artifactWorkspace}" --feature "${feature}" --task-id "<task-id>" --code-workspace "<batchWorktree>"，再用 task_runner.py start、完成实现后用 finish-implementation；所有 task_runner 调用必须带 --workspace "${artifactWorkspace}"、--parallel-run-id "${runId}"、步骤 2 的 lease token、--code-workspace "<batchWorktree>" 和 --workspace-ref "${batchWorkspaceRef}"。不得操作其他 Batch 或任何主业务 checkout。\n` +
      `5. 全部 TASK 完成后执行 python "${taskRunnerPath}" batch-compile --workspace "${artifactWorkspace}" --feature "${feature}" --batch-id "${batchId}" --code-workspace "<batchWorktree>" --parallel-run-id "${runId}" --lease-token "<lease-token>" --workspace-ref "${batchWorkspaceRef}"。编译失败则释放 lease 为 failed 并返回 failed。\n` +
      `6. 编译通过后只调用 python "${worktreeManagerPath}" --json seal --artifact-workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --repo "<batchWorktree>" --owner-token "<lease-token>"；从 JSON 保存 commitSha。插件在此命令中提交；不要自行 git add、git commit 或 mark-batch ready_to_merge。\n` +
      `7. 执行 python "${leasePath}" release --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --owner-token "<lease-token>" --final-status ready_to_merge。\n` +
      `返回 {batchId, status:"success", compileStatus:"passed", worktreePath:batchWorktree, branchName:batchBranch, commitSha}。` +
      `不得创建任何 workflow、手工创建分支、使用 undefined 路径或 feature、手工 git add/commit；不要 merge、rebase、解决冲突、删除 worktree。任何命令失败立即返回 failed，不得以部分结果继续。`,
      {
        label: `fixed-batch-${batchId}`,
        phase: "实现",
        schema: BATCH_RESULT_SCHEMA,
        isolation: "worktree"
      }
      );
      })
    );
    const normalizedWaveResults = waveResults.map(unwrap);
    batchResults.push(...normalizedWaveResults);
    const failed = normalizedWaveResults.filter(result => !result || result.status !== "success" || result.compileStatus !== "passed");
    if (failed.length > 0) {
      throw new Error(JSON.stringify({ error: "batch_execution_failed", runId, failed, batchResults, mergeResults }));
    }
  }

  // This is the release barrier: dependent Batches cannot appear in the next
  // scheduler result until every completed Batch in this wave is merged.
  phase("合并");
  const mergeResult = requireSuccess(await agent(
    `合并刚完成的固定 DAG 波次。执行 python "${mergerPath}" --workspace "${artifactWorkspace}" ` +
    `--feature "${feature}" --run-id "${runId}" --conflict-mode native-rebase。` +
    `只允许 shared workflow owner 执行此命令；不要修改业务代码。若返回冲突或 needsResolution，立即失败并保留 manifest。`,
    { label: `merge-wave-${schedulerWaves}`, phase: "合并", schema: MERGE_RESULT_SCHEMA }
  ), "merge completed wave");
  if (!mergeResult.success) {
    throw new Error(JSON.stringify({ error: "merge_failed", runId, mergeResult, batchResults, mergeResults }));
  }
  mergeResults.push(mergeResult);

  const resumed = requireSuccess(await agent(
    `执行 python "${schedulerPath}" resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
    `只返回 JSON。下游 Batch 必须仅在依赖已 merged 后才会出现在 scheduledGroups 中。`,
    { label: `schedule-wave-${schedulerWaves + 1}`, phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
  ), "scheduler resume");
  scheduledGroups = resumed.scheduledGroups || [];
  mergeableBatches = resumed.mergeableBatches || [];
  batchTaskIds = resumed.batchTaskIds || batchTaskIds;
  batchWorkspaces = resumed.batchWorkspaces || batchWorkspaces;
  if (!scheduledGroups.length && !mergeableBatches.length && !["verifying", "succeeded"].includes(resumed.status)) {
    throw new Error(JSON.stringify({ error: "parallel_scheduler_stalled", runId, scheduler: resumed, batchResults, mergeResults }));
  }
}

phase("最终验证");
const verification = requireSuccess(await agent(
  `执行 python "${finalVerifyPath}" --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
  `只执行最终编译门禁；失败必须阻断 run，并将 hook 返回的 error 原样写入 errorMessage。`,
  { label: "verify-merged-batches", phase: "最终验证", schema: VERIFICATION_SCHEMA }
), "final verification");

return { ok: true, feature, runId, batchResults, mergeResults, verification };
