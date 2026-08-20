export const meta = {
  name: "code-batched-execution",
  description: "Plan-aware parallel execution of independent Code batches",
  whenToUse: "由 /autodev-code 在 launcher 判定多个合法待执行 batch 时调用",
  phases: [
    { title: "准备", detail: "校验 plan、创建 run manifest、计算依赖和并发批次" },
    { title: "并行实现", detail: "每个 batch 在固定 base SHA 的独立 worktree 中执行" },
    { title: "顺序合并", detail: "按依赖拓扑和 Batch ID 顺序调用确定性合并器" },
    { title: "最终验证", detail: "合并后只执行编译门禁并回写 run 状态" }
  ]
};

const MAX_PARALLEL_BATCHES = 4;
const MAX_SCHEDULER_WAVES = 100;
const BATCH_EXECUTION_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    status: { enum: ["success", "failed", "timeout"] },
    tasksCompleted: { type: "number" },
    tasksTotal: { type: "number" },
    changedFiles: { type: "array", items: { type: "string" } },
    compileStatus: { enum: ["passed", "failed", "skipped"] },
    errorMessage: { type: "string" },
    worktreePath: { type: "string" },
    branchName: { type: "string" }
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
const VERIFICATION_SCHEMA = {
  type: "object",
  properties: {
    passed: { type: "boolean" },
    errors: { type: "array", items: { type: "string" } },
    summary: { type: "string" }
  },
  required: ["passed"],
  additionalProperties: false
};

phase("准备");
const feature = args.feature;
const pluginPath = args.pluginPath || process.env.PLUGIN_PATH;
const artifactWorkspace = args.artifactWorkspace;
const maxParallel = Number.isInteger(args.maxParallel) && args.maxParallel > 0
  ? args.maxParallel
  : MAX_PARALLEL_BATCHES;
const timeoutPerBatch = Number.isInteger(args.timeoutPerBatch) && args.timeoutPerBatch > 0
  ? args.timeoutPerBatch
  : 3600;
const codeWorkspaces = args.codeWorkspaces || (args.codeWorkspace ? { default: args.codeWorkspace } : null);
if (!feature || !pluginPath || !artifactWorkspace || !codeWorkspaces || typeof codeWorkspaces !== "object") {
  return { error: "missing_feature_plugin_path_artifact_workspace_or_code_workspaces" };
}
const codeWorkspaceArgs = Object.entries(codeWorkspaces)
  .map(([workspaceRef, path]) => `--code-workspace "${workspaceRef}=${path}"`)
  .join(" ");

// The launcher has already performed this validation.  The preparation agent
// creates the durable manifest and is required to fail closed on drift.
const preparation = await agent(
  `准备并行 Code run。Feature=${feature}，插件路径=${pluginPath}，产物 workspace=${artifactWorkspace}。` +
  `执行 python "${pluginPath}/hooks/parallel_batch_scheduler.py" create --workspace "${artifactWorkspace}" --feature "${feature}" ` +
  `--max-parallel ${maxParallel} --timeout-seconds ${timeoutPerBatch} ${codeWorkspaceArgs}。` +
  `返回 runId、readyBatches、scheduledGroups、batchWorkspaces；不要修改业务文件。`,
  { label: "parallel-run-prepare", phase: "准备", schema: { type: "object" } }
);
if (!preparation || !preparation.runId) {
  return { error: "parallel_run_prepare_failed", preparation: preparation || null };
}
const runId = preparation.runId;
let scheduledGroups = preparation.scheduledGroups || [];
const batchResults = [];
const mergeResults = [];

phase("并行实现");
let schedulerWaves = 0;
while (scheduledGroups.length) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    return { error: "parallel_scheduler_wave_limit_exceeded", runId, schedulerWaves, batchResults, mergeResults };
  }
  // Each ready Batch owns an isolated worktree. Dependency readiness and the
  // global maxParallel setting are the only execution constraints; conflicts
  // are detected later by the repository-local merge step.
  const executions = scheduledGroups.map(([batchId]) => ({ batchId, runId }));
  const waveResults = await pipeline(executions, async execution => {
    return agent(
      `执行唯一 Batch ${execution.batchId}。Feature=${feature}，runId=${execution.runId}，插件路径=${pluginPath}，artifact workspace=${artifactWorkspace}。` +
      `从 manifest.batchWorkspaces 读取该 batch 的 workspaceRef、组件根目录和业务仓库，禁止选择其他仓库。\n` +
      `先获取 lease，再创建并行 worktree。在 worktree 中执行本 batch 的 task_runner start、finish-implementation、batch-compile，` +
      `所有调用携带 --parallel-run-id ${execution.runId} 和 lease token。\n` +
      `batch-compile 成功会自动回写 ready_to_merge；随后调用 "${pluginPath}/hooks/worktree_manager.py" seal 提交该 worktree。` +
      `失败时调用 "${pluginPath}/hooks/parallel_batch_scheduler.py" mark-batch failed；最后用 "${pluginPath}/hooks/batch_lease_manager.py" release --final-status ready_to_merge 释放 lease。` +
      `不要修改主工作区、不要解决冲突、不要删除 worktree。`,
      { label: `batch-${execution.batchId}`, phase: "并行实现", schema: BATCH_EXECUTION_SCHEMA }
    );
  });
  batchResults.push(...waveResults);
  const failed = waveResults.filter(item => !item || item.status !== "success");
  if (failed.length) {
    return { error: "batch_execution_failed", runId, failed, batchResults };
  }

  phase("顺序合并");
  const mergeResult = await agent(
    `只执行确定性合并，不进行人工改写。执行 python "${pluginPath}/hooks/batch_merger.py" --workspace "${artifactWorkspace}" ` +
    `--feature "${feature}" --run-id "${runId}"。合并器必须从 manifest 对每个 batch 选择绑定的 Git 根。` +
    `主工作区变化、planDigest 漂移或 Git 冲突时立即停止；禁止 --ours、--theirs 和手动编辑冲突文件。`,
    { label: "merge-batches", phase: "顺序合并", schema: MERGE_RESULT_SCHEMA }
  );
  if (!mergeResult || !mergeResult.success) {
    if (!mergeResult?.needsResolution) {
      return { error: "merge_failed", runId, mergeResult: mergeResult || null, batchResults };
    }
    const resolutionTargets = (mergeResult.failed || []).filter(item => item && item.needsResolution);
    const resolutionResults = await pipeline(resolutionTargets, async item => agent(
      `自动处理 Code 合并冲突。Feature=${feature}，runId=${runId}，Batch=${item.batchId}。` +
      `读取 manifest 中 resolution.worktreePath，在该 Worktree 执行 git status、git diff、git diff --cc，` +
      `同时阅读该 Batch 与冲突来源 Batch 的 goal、touches、implementation Evidence 和提交 diff。` +
      `只解决 Git 标记的冲突文件及实现所必需的适配；禁止使用 git checkout --ours/--theirs、git merge -s ours、--no-verify、删除一侧变更或直接改主工作区。` +
      `按两个 Batch 的业务目标保留兼容行为，解决后运行该 Batch 的 required compile 命令。` +
      `然后执行 python "${pluginPath}/hooks/parallel_conflict_resolver.py" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${item.batchId}"，` +
      `返回冲突文件、解决理由、验证输出摘要和 resolutionCommitSha。`,
      { label: `resolve-conflict-${item.batchId}`, phase: "顺序合并", schema: { type: "object" } }
    ));
    const mergedResolutions = [];
    for (const item of resolutionTargets) {
      const resolved = resolutionResults.find(result => result?.batchId === item.batchId);
      if (!resolved || resolved.status === "failed") {
        return { error: "conflict_resolution_failed", runId, mergeResult, resolutionResults, batchResults };
      }
      const mergedResolution = await agent(
        `执行 python "${pluginPath}/hooks/parallel_conflict_resolver.py" merge --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${item.batchId}"。` +
        `只允许把已完成并已验证的 resolution 分支合并到对应 repositoryRef 的 Git 根。`,
        { label: `merge-resolution-${item.batchId}`, phase: "顺序合并", schema: MERGE_RESULT_SCHEMA }
      );
      if (!mergedResolution || !mergedResolution.success) {
        return { error: "resolution_merge_failed", runId, mergeResult, mergedResolution, batchResults };
      }
      mergedResolutions.push(mergedResolution);
    }
    mergeResults.push({ ...mergeResult, success: true, resolved: mergedResolutions });
  } else {
    mergeResults.push(mergeResult);
  }

  const resumed = await agent(
    `执行 python "${pluginPath}/hooks/parallel_batch_scheduler.py" resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"，返回 scheduledGroups 和 status。`,
    { label: "schedule-next-wave", phase: "准备", schema: { type: "object" } }
  );
  scheduledGroups = resumed?.scheduledGroups || [];
  if (!scheduledGroups.length && !["verifying", "succeeded"].includes(resumed?.status)) {
    return { error: "parallel_scheduler_stalled", runId, scheduler: resumed || null, batchResults, mergeResults };
  }
}

phase("最终验证");
const verification = await agent(
  `执行 python "${pluginPath}/hooks/parallel_final_verify.py" --workspace "${artifactWorkspace}" --feature "${feature}" ` +
  `--run-id "${runId}"。它会在 manifest 绑定的每个仓库中执行对应组件的编译门禁；不运行 UTest，失败必须阻断 run。`,
  { label: "verify-merged", phase: "最终验证", schema: VERIFICATION_SCHEMA }
);
if (!verification || !verification.passed) {
  return { error: "verification_failed", runId, verification: verification || null, mergeResults, batchResults };
}

return { ok: true, feature, runId, batchResults, mergeResults, verification };
