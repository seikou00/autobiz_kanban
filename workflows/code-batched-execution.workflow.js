export const meta = {
  name: "code-batched-execution",
  description: "Fixed DAG execution for Code batches with merge-before-release semantics",
  whenToUse: "由 workflow_launcher.py 在存在合法待执行 Batch 时调用",
  phases: [
    { title: "准备", detail: "创建 scheduler run 并计算当前可执行 DAG 波次" },
    { title: "实现", detail: "插件在各业务仓库创建 linked Git worktree，同一波次并行执行" },
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
  required: ["batchId", "status"],
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
    commands: { type: "array", items: { type: "object" } }
  },
  required: ["passed"],
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

const input = unwrap(args);
const feature = input.feature;
const pluginPath = input.pluginPath;
const artifactWorkspace = input.artifactWorkspace || input.workspace;
const codeWorkspaces = input.codeWorkspaces || (input.codeWorkspace ? { default: input.codeWorkspace } : null);
const maxParallel = Number.isInteger(input.maxParallel) && input.maxParallel > 0
  ? input.maxParallel
  : DEFAULT_MAX_PARALLEL;
const timeoutPerBatch = Number.isInteger(input.timeoutPerBatch) && input.timeoutPerBatch > 0
  ? input.timeoutPerBatch
  : 3600;

if (!feature || !pluginPath || !artifactWorkspace || !codeWorkspaces || typeof codeWorkspaces !== "object" || Object.keys(codeWorkspaces).length === 0) {
  throw new Error("missing_feature_plugin_path_artifact_workspace_or_code_workspaces");
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
const prepared = requireSuccess(await agent(
  `创建固定 Code DAG run。执行：python "${schedulerPath}" create ` +
  `--workspace "${artifactWorkspace}" --feature "${feature}" ` +
  `--max-parallel ${maxParallel} ` +
  `--timeout-seconds ${timeoutPerBatch} ${codeWorkspaceArgs}。` +
  `只返回该命令的 JSON 结果，不修改业务文件。`,
  { label: "fixed-workflow-prepare", phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
), "scheduler create");

const runId = prepared.runId;
let scheduledGroups = prepared.scheduledGroups || [];
let batchTaskIds = prepared.batchTaskIds || {};
let batchWorkspaces = prepared.batchWorkspaces || {};
const batchResults = [];
const mergeResults = [];
let schedulerWaves = 0;

while (scheduledGroups.length > 0) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    throw new Error(JSON.stringify({ error: "parallel_scheduler_wave_limit_exceeded", runId, schedulerWaves, batchResults, mergeResults }));
  }

  phase("实现");
  const batchIds = scheduledGroups.flat();
  const waveResults = await parallel(
    batchIds.map(batchId => () => {
      const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
      if (taskIds.length === 0) throw new Error(`scheduler returned no task IDs for ${batchId}`);
      const batchWorkspace = batchWorkspaces[batchId] || {};
      const batchWorkspaceRef = batchWorkspace.workspaceRef;
      const worktreePath = batchWorkspace.worktreePath;
      const branchName = batchWorkspace.branchName;
      if (!batchWorkspaceRef || !codeWorkspaces[batchWorkspaceRef] || !worktreePath || !branchName) {
        throw new Error(`scheduler did not provision a worktree for ${batchId}`);
      }
      return agent(
      `在插件管理的业务仓库 worktree 中执行 Batch ${batchId}。Feature=${feature}，runId=${runId}，` +
      `artifact workspace=${artifactWorkspace}。严格按以下固定顺序执行：\n` +
      `1. Scheduler 已在实现前为本 Batch 创建 worktree。固定路径为 "${worktreePath}"，固定分支为 "${branchName}"。先确认它是 Git worktree；禁止创建、替换或删除它，也禁止把 pwd、artifact workspace 或主业务 checkout 当作代码目录。\n` +
      `2. 执行 python "${leasePath}" acquire --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"，从 JSON 的 lease.ownerToken 保存本 Batch 的 lease token。\n` +
      `3. 用固定路径执行 python "${schedulerPath}" mark-batch --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status running --worktree-path "${worktreePath}" --branch-name "${branchName}"。所有业务代码读取、编辑与命令只能通过 execute 在该 worktreePath 内完成。\n` +
      `4. Scheduler 已提供本 Batch 的 task IDs：${JSON.stringify(taskIds)}。不要用 read_file 读取 artifact 目录；artifact workspace 不是代码目录。逐个执行这些 TASK：先执行 python "${pluginPath}/hooks/code_task_context.py" --workspace "${artifactWorkspace}" --feature "${feature}" --task-id "<task-id>" --code-workspace "${worktreePath}"，再用 task_runner.py start、完成实现后用 finish-implementation；所有 task_runner 调用必须带 --workspace "${artifactWorkspace}"、--parallel-run-id "${runId}"、步骤 2 的 lease token、--code-workspace "${worktreePath}" 和 --workspace-ref "${batchWorkspaceRef}"。不得操作其他 Batch 或任何主业务 checkout。\n` +
      `5. 全部 TASK 完成后执行 python "${taskRunnerPath}" batch-compile --workspace "${artifactWorkspace}" --feature "${feature}" --batch-id "${batchId}" --code-workspace "${worktreePath}" --parallel-run-id "${runId}" --lease-token "<lease-token>" --workspace-ref "${batchWorkspaceRef}"。编译失败则释放 lease 为 failed 并返回 failed。\n` +
      `6. 编译通过后只调用 python "${worktreeManagerPath}" --json seal --artifact-workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --repo "${codeWorkspaces[batchWorkspaceRef]}" --owner-token "<lease-token>"；从 JSON 保存 commitSha。禁止自行 git add、git commit、mark-batch ready_to_merge。\n` +
      `7. 执行 python "${leasePath}" release --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --owner-token "<lease-token>" --final-status ready_to_merge。\n` +
      `返回 {batchId, status:"success", compileStatus:"passed", worktreePath, branchName, commitSha}。` +
      `不要 merge、rebase、解决冲突、删除 worktree；不使用 platform isolation；所有命令失败立即停止。`,
      {
        label: `fixed-batch-${batchId}`,
        phase: "实现",
        schema: BATCH_RESULT_SCHEMA
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
  batchTaskIds = resumed.batchTaskIds || batchTaskIds;
  batchWorkspaces = resumed.batchWorkspaces || batchWorkspaces;
  if (!scheduledGroups.length && !["verifying", "succeeded"].includes(resumed.status)) {
    throw new Error(JSON.stringify({ error: "parallel_scheduler_stalled", runId, scheduler: resumed, batchResults, mergeResults }));
  }
}

phase("最终验证");
const verification = requireSuccess(await agent(
  `执行 python "${finalVerifyPath}" --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
  `只执行最终编译门禁；失败必须阻断 run。`,
  { label: "verify-merged-batches", phase: "最终验证", schema: VERIFICATION_SCHEMA }
), "final verification");

return { ok: true, feature, runId, batchResults, mergeResults, verification };
