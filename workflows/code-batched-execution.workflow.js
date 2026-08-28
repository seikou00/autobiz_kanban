export const meta = {
  name: "code-batched-execution",
  description: "Fixed DAG execution for Code batches with merge-before-release semantics",
  whenToUse: "由 workflow_launcher.py 在存在合法待执行 Batch 时调用",
  phases: [
    { title: "准备", detail: "创建或恢复 scheduler run 并计算当前可执行 DAG 波次" },
    { title: "实现", detail: "插件为每个 Batch 创建并管理原生 Git worktree，同一波次并行执行" },
    { title: "合并", detail: "每个波次完成后立即确定性合并，才释放下游依赖" },
    { title: "最终验证", detail: "所有 Batch 合并后执行最终编译门禁" }
  ]
};

const DEFAULT_MAX_PARALLEL = 4;
const MAX_SCHEDULER_WAVES = 100;
const MAX_AUTO_MERGE_RESOLUTION_ATTEMPTS = 1;
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
    needsResolution: { type: "boolean" },
    needsPlanRecovery: { type: "boolean" },
    totalConflicts: { type: "number" },
    nextReadyBatches: { type: "array", items: { type: "string" } },
    mergeableBatches: { type: "array", items: { type: "string" } }
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
const WORKTREE_SCHEMA = {
  type: "object",
  properties: {
    success: { type: "boolean" },
    batchId: { type: "string" },
    repositoryRef: { type: "string" },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    reused: { type: "boolean" },
    error: { type: "string" }
  },
  required: ["success", "batchId", "repositoryRef"],
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
  if (!usableString(value)) return false;
  const candidate = value.trim();
  return candidate.startsWith("/")
    || /^[A-Za-z]:[\\/]/.test(candidate)
    || candidate.startsWith("\\\\");
}

function normalizePath(value) {
  const candidate = String(value || "").trim().replace(/\\/g, "/");
  const driveRoot = /^[A-Za-z]:\/$/.test(candidate);
  const unc = candidate.startsWith("//");
  const posix = !unc && candidate.startsWith("/");
  const body = candidate.replace(/^\/+/, "").replace(/\/{2,}/g, "/");
  const prefix = unc ? "//" : (posix ? "/" : "");
  if (driveRoot) return candidate.toLowerCase();
  const normalized = `${prefix}${body}`.replace(/\/$/, "").toLowerCase();
  return normalized || (posix ? "/" : (unc ? "//" : ""));
}

function samePath(left, right) {
  return normalizePath(left) === normalizePath(right);
}

function joinPath(base, ...parts) {
  return [String(base || "").replace(/[\\/]+$/, ""), ...parts]
    .map((part, index) => index === 0 ? part : String(part).replace(/^[\\/]+/, ""))
    .join("/")
    .replace(/\\/g, "/");
}

const input = unwrap(args);
const feature = input.feature;
const pluginPath = input.pluginPath;
const artifactWorkspace = input.artifactWorkspace || input.workspace;
const codeWorkspaces = input.codeWorkspaces || (input.codeWorkspace ? { default: input.codeWorkspace } : null);
const workflowHostGitRoot = input.workflowHostGitRoot;
const repositoryRefs = Array.isArray(input.repositoryRefs)
  ? input.repositoryRefs.filter(ref => usableString(ref))
  : [];
const allowedBatchIds = Array.isArray(input.batchIds)
  ? input.batchIds.filter(batchId => usableString(batchId))
  : [];
const coordinatorManaged = input.coordinatorManaged === true;
const maxParallel = Number.isInteger(input.maxParallel) && input.maxParallel > 0
  ? input.maxParallel
  : DEFAULT_MAX_PARALLEL;
const timeoutPerBatch = Number.isInteger(input.timeoutPerBatch) && input.timeoutPerBatch > 0
  ? input.timeoutPerBatch
  : 3600;
const leaseHeartbeatInterval = Math.max(30, Math.min(300, Math.floor(timeoutPerBatch / 3)));

if (
  !usableString(feature)
  || !absolutePath(pluginPath)
  || !absolutePath(artifactWorkspace)
  || !codeWorkspaces
  || typeof codeWorkspaces !== "object"
  || Object.keys(codeWorkspaces).length === 0
) {
  throw new Error("missing_feature_plugin_path_artifact_workspace_or_code_workspaces");
}
if (Object.values(codeWorkspaces).some(path => !absolutePath(path))) {
  throw new Error("invalid_code_workspace_path");
}
// The workflow host can be an artifact directory. Native worktrees are
// provisioned by the plugin from the repository paths in codeWorkspaces.
// Keep workflowHostGitRoot as optional metadata for older callers only.
if (usableString(workflowHostGitRoot) && !absolutePath(workflowHostGitRoot)) {
  throw new Error("invalid_workflow_repository_root");
}
if (coordinatorManaged && (!repositoryRefs.length || !allowedBatchIds.length)) {
  throw new Error("repository_coordinator_scope_required");
}
if (coordinatorManaged && Object.keys(codeWorkspaces).some(ref => !repositoryRefs.includes(ref))) {
  throw new Error("repository_coordinator_workspace_ref_scope_mismatch");
}

const schedulerPath = joinPath(pluginPath, "hooks/parallel_batch_scheduler.py");
const leasePath = joinPath(pluginPath, "hooks/batch_lease_manager.py");
const taskRunnerPath = joinPath(pluginPath, "hooks/task_runner.py");
const routeResolverPath = joinPath(pluginPath, "hooks/resolve_frontend_html_route.py");
const worktreeManagerPath = joinPath(pluginPath, "hooks/worktree_manager.py");
const mergerPath = joinPath(pluginPath, "hooks/batch_merger.py");
const finalVerifyPath = joinPath(pluginPath, "hooks/parallel_final_verify.py");
const codeWorkspaceArgs = Object.entries(codeWorkspaces)
  .map(([workspaceRef, path]) => `--code-workspace "${workspaceRef}=${path}"`)
  .join(" ");
const workspaceRefArgs = repositoryRefs
  .map(workspaceRef => `--workspace-ref "${workspaceRef}"`)
  .join(" ");

function scopeGroups(groups) {
  const allowed = new Set(allowedBatchIds);
  return (Array.isArray(groups) ? groups : [])
    .map(group => (Array.isArray(group) ? group.filter(batchId => !allowed.size || allowed.has(batchId)) : []))
    .filter(group => group.length > 0);
}

function hasWorkOutsideScope(scheduler) {
  if (!allowedBatchIds.length || !scheduler || typeof scheduler !== "object") return false;
  const allowed = new Set(allowedBatchIds);
  const candidates = [
    scheduler.scheduledGroups,
    scheduler.allReadyBatches,
    scheduler.allMergeableBatches,
    scheduler.allParallelGroups,
  ];
  return candidates.some(value => {
    const ids = Array.isArray(value)
      ? value.flatMap(item => Array.isArray(item) ? item : [item])
      : [];
    return ids.some(batchId => usableString(batchId) && !allowed.has(batchId));
  });
}

phase("准备");
const prepared = requireSuccess(await agent(
  `确保固定 Code DAG run。执行：python "${schedulerPath}" ensure ` +
  `--workspace "${artifactWorkspace}" --feature "${feature}" ` +
  `--max-parallel ${maxParallel} ` +
  `--timeout-seconds ${timeoutPerBatch} --allow-bootstrap ${codeWorkspaceArgs} ${workspaceRefArgs}。` +
  `已有可恢复 run 时必须返回其原 runId，不得创建第二个 run。` +
  `必要时允许 scheduler 创建 autodev baseline 提交；不得修改业务文件内容，` +
  `且不得把 .cmbdevclaw 平台运行文件纳入提交。只返回该命令的 JSON 结果。`,
  { label: "fixed-workflow-prepare", phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
), "scheduler ensure");

const runId = prepared.runId;
let scheduledGroups = scopeGroups(prepared.scheduledGroups || []);
let mergeableBatches = (prepared.mergeableBatches || []).filter(batchId => !allowedBatchIds.length || allowedBatchIds.includes(batchId));
let batchTaskIds = prepared.batchTaskIds || {};
let batchWorkspaces = prepared.batchWorkspaces || {};
const batchResults = [];
const mergeResults = [];
let schedulerWaves = 0;

async function failBatchAndReclaimLease(batchId, batchWorktree, batchBranch, reason) {
  return agent(
    `收尾失败的 Batch ${batchId}，不得重试实现或研究环境。原因：${JSON.stringify(reason)}。` +
    `先执行 python "${leasePath}" reclaim --workspace "${artifactWorkspace}" --feature "${feature}" ` +
    `--run-id "${runId}" --batch-id "${batchId}" --force；再执行 python "${schedulerPath}" mark-batch ` +
    `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status failed ` +
    `--worktree-path "${batchWorktree}" --branch-name "${batchBranch}"。` +
    `只清理租约和更新调度状态，保留插件原生 worktree 供排查；不要创建 workflow、修改业务代码、包装 Git 或继续执行 TASK。只返回 JSON。`,
    { label: `cleanup-failed-batch-${batchId}`, phase: "实现" }
  );
}

if (!scheduledGroups.length && !mergeableBatches.length && !["verifying", "succeeded"].includes(prepared.status) && !prepared.waitingForRepositories && !hasWorkOutsideScope(prepared)) {
  throw new Error(JSON.stringify({ error: "parallel_scheduler_stalled", runId, scheduler: prepared, batchResults, mergeResults }));
}

while (scheduledGroups.length > 0 || mergeableBatches.length > 0) {
  schedulerWaves += 1;
  if (schedulerWaves > MAX_SCHEDULER_WAVES) {
    throw new Error(JSON.stringify({ error: "parallel_scheduler_wave_limit_exceeded", runId, schedulerWaves, batchResults, mergeResults }));
  }

  let currentWaveBatchIds = [];
  if (scheduledGroups.length > 0) {
    phase("实现");
    const batchIds = scheduledGroups.flat();
    currentWaveBatchIds = batchIds;
    const provisionResults = await parallel(
      batchIds.map(batchId => () => {
        return agent(
          `为 Batch ${batchId} 创建或复用插件托管的原生 Git Worktree。执行 python "${worktreeManagerPath}" --json provision ` +
          `--artifact-workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。` +
          `只返回 JSON；不得使用平台 isolation，不得修改业务源码。`,
          { label: `provision-worktree-${batchId}`, phase: "实现", schema: WORKTREE_SCHEMA }
        );
      })
    );
    const normalizedProvisionResults = provisionResults.map(unwrap);
    const provisioningFailures = normalizedProvisionResults.filter(result => !result || result.success !== true);
    if (provisioningFailures.length > 0) {
      throw new Error(JSON.stringify({ error: "native_worktree_provision_failed", runId, provisioningFailures, batchResults, mergeResults }));
    }
    const provisionedByBatch = Object.fromEntries(normalizedProvisionResults.map(result => [result.batchId, result]));
    let waveResults;
    try {
      waveResults = await parallel(
        batchIds.map(batchId => () => {
      const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
      if (taskIds.length === 0) throw new Error(`scheduler returned no task IDs for ${batchId}`);
      const batchWorkspace = batchWorkspaces[batchId] || {};
      const batchWorkspaceRef = batchWorkspace.workspaceRef;
      if (!batchWorkspaceRef || !codeWorkspaces[batchWorkspaceRef]) {
        throw new Error(`scheduler did not provide a code workspace for ${batchId}`);
      }
      const provisioned = provisionedByBatch[batchId];
      if (!provisioned || !provisioned.worktreePath || !provisioned.branchName) {
        throw new Error(`plugin did not provide native worktree for ${batchId}`);
      }
      const batchWorktree = provisioned.worktreePath;
      const batchBranch = provisioned.branchName;
      const heartbeatDirectory = joinPath(
        artifactWorkspace,
        ".autobizdevops",
        "features",
        feature,
        ".parallel-runs",
        runId,
        "leases"
      );
      const heartbeatPidFile = joinPath(heartbeatDirectory, `${batchId}.heartbeat.pid`);
      const heartbeatStdoutFile = joinPath(heartbeatDirectory, `${batchId}.heartbeat.out.log`);
      const heartbeatStderrFile = joinPath(heartbeatDirectory, `${batchId}.heartbeat.err.log`);
      return agent(
      `在插件创建的原生 Git worktree "${batchWorktree}" 中执行 Batch ${batchId}。Feature=${feature}，runId=${runId}，` +
      `artifact workspace=${artifactWorkspace}。严格按以下固定顺序执行：\n` +
      `1. 执行 cd "${batchWorktree}"（Windows 使用 Set-Location），确认 git rev-parse --show-toplevel 等于该路径、git symbolic-ref --quiet --short HEAD 等于 "${batchBranch}"。禁止 git worktree add/remove、git switch、merge、rebase 或操作其他 checkout。\n` +
      `2. 执行 python "${leasePath}" acquire --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --ttl-seconds ${timeoutPerBatch}，从 JSON 的 lease.ownerToken 保存本 Batch 的 lease token。\n` +
      `3. 将步骤 2 返回的非空 ownerToken 保存为变量，并在后续命令中展开为该真实字符串；命令行中不得出现空字符串、字面量 "LEASE_TOKEN" 或 "<lease-token>"。立即启动 heartbeat：参数必须包含展开后的 --owner-token、--ttl-seconds ${timeoutPerBatch}、--interval-seconds ${leaseHeartbeatInterval}、--max-seconds ${timeoutPerBatch}、--pid-file "${heartbeatPidFile}"。POSIX 把标准输出/错误分别写入 "${heartbeatStdoutFile}" / "${heartbeatStderrFile}" 后台运行；Windows PowerShell 使用 Start-Process、-RedirectStandardOutput "${heartbeatStdoutFile}"、-RedirectStandardError "${heartbeatStderrFile}"、-PassThru 并记录其 Id。后续实现、编译和 seal 全程保持 heartbeat 运行；heartbeat 退出、PID 不存活或日志出现错误都必须让本 Batch 失败。\n` +
      `4. 执行 python "${schedulerPath}" mark-batch --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status running --worktree-path "${batchWorktree}" --branch-name "${batchBranch}"。业务源码命令只在该 checkout 内执行。\n` +
      `5. Scheduler 已提供本 Batch 的唯一 TASK IDs：${JSON.stringify(taskIds)}。逐个以这些具体 ID 执行；禁止使用空值、"undefined" 或任何占位符。不要用 read_file 读取 artifact 目录；artifact workspace 不是代码目录。对数组中的每个实际 ID，直接将该值传给 code_task_context.py 的 --task-id 参数（例如数组为 ["T001"] 时必须传 --task-id "T001"）。以 taskContract.uiRequired 为唯一条件：false 时跳过 Route resolver，不读取 HTML/Route SKILL；true 时必须在本 agent 内、写前端源码前执行 python "${routeResolverPath}" --workspace "${artifactWorkspace}" --feature "${feature}" --start-route-run --json，并按返回 route 读取对应 Route SKILL 到 EOF，标记 route-skill-read-complete、创建 route write_todos；仅当 Route SKILL 清单推进到转交 parser 后才读取对应 parser 并标记 parser-read，完成清单后标记 route-todos-completed，统一回检后写入 FRONTEND_ROUTE.json。route=spec-driven-ui 不读 parser 但仍须回检，route=none 禁止写前端源码。若同一 agent 后续处理同一 Route 的前端 task，复用已完成且仍匹配的 routeRunId，不得让后端 task 触发 resolver。随后用 task_runner.py start、完成实现后用 finish-implementation；所有 task_runner 调用必须带 --workspace "${artifactWorkspace}"、--parallel-run-id "${runId}"、展开后的真实 lease token、--code-workspace "${batchWorktree}" 和 --workspace-ref "${batchWorkspaceRef}"。不得操作其他 Batch 或任何主业务 checkout。\n` +
      `6. 全部 TASK 完成后执行 python "${leasePath}" check；其 --workspace、--feature、--run-id、--batch-id 取本 Batch 的上述固定值，--owner-token 必须是步骤 2 返回并保存的真实 token。仅 valid=true 才可继续。heartbeat 保持运行，然后执行 python "${taskRunnerPath}" batch-compile，并携带 --workspace "${artifactWorkspace}"、--feature "${feature}"、--batch-id "${batchId}"、--code-workspace "${batchWorktree}"、--parallel-run-id "${runId}"、--lease-token（同一真实 token）和 --workspace-ref "${batchWorkspaceRef}"。\n` +
      `7. 编译通过后再次以同一真实 token 执行 lease check；heartbeat 继续运行。随后只调用 python "${worktreeManagerPath}" --json seal，并携带 --artifact-workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--repo "${batchWorktree}" 和 --owner-token（同一真实 token）；从 JSON 保存 commitSha。插件在此命令中提交；不要自行 git add、git commit 或 mark-batch ready_to_merge。\n` +
      `8. seal 成功后停止 heartbeat（POSIX 使用 kill，Windows 使用 Stop-Process；等待进程退出并删除 "${heartbeatPidFile}"、"${heartbeatStdoutFile}"、"${heartbeatStderrFile}"），再执行 python "${leasePath}" release，并携带 --workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--owner-token（同一真实 token）和 --final-status ready_to_merge。首次命令失败时，立即停止 heartbeat、删除这三个文件、以 failed 释放 lease（仍有效时）；随后只返回 failed。禁止检查/修改插件源码、创建 Git wrapper、尝试替代命令或继续任何 TASK。\n` +
      `返回 {batchId, status:"success", compileStatus:"passed", worktreePath:batchWorktree, branchName:batchBranch, commitSha}。` +
      `不得创建任何 workflow、手工创建分支、使用 undefined 路径或 feature、手工 git add/commit；不要 merge、rebase、解决冲突、删除 worktree。任何命令失败立即返回 failed，不得以部分结果继续。`,
      {
        label: `fixed-batch-${batchId}`,
        phase: "实现",
        schema: BATCH_RESULT_SCHEMA,
      }
      );
        })
      );
    } catch (error) {
      await parallel(
        batchIds.map(batchId => {
          const provisioned = provisionedByBatch[batchId];
          return () => failBatchAndReclaimLease(
            batchId,
            provisioned.worktreePath,
            provisioned.branchName,
            String(error)
          );
        })
      );
      throw error;
    }
    const normalizedWaveResults = waveResults.map(unwrap);
    batchResults.push(...normalizedWaveResults);
    const failedEntries = normalizedWaveResults
      .map((result, index) => ({ result, batchId: batchIds[index] }))
      .filter(({ result }) => !result || result.status !== "success" || result.compileStatus !== "passed");
    const failed = failedEntries.map(({ result }) => result);
    if (failedEntries.length > 0) {
      await parallel(
        failedEntries.map(({ result, batchId }) => {
          const provisioned = provisionedByBatch[batchId];
          return () => failBatchAndReclaimLease(
            batchId,
            provisioned.worktreePath,
            provisioned.branchName,
            result && (result.errorMessage || result.raw || result.status)
          );
        })
      );
      throw new Error(JSON.stringify({ error: "batch_execution_failed", runId, failed, batchResults, mergeResults }));
    }
  }

  // This is the release barrier: dependent Batches cannot appear in the next
  // scheduler result until every completed Batch in this wave is merged.
  phase("合并");
  const mergeIds = [...new Set([...mergeableBatches, ...currentWaveBatchIds])]
    .filter(batchId => !allowedBatchIds.length || allowedBatchIds.includes(batchId));
  const mergeIdArgs = mergeIds.map(batchId => `--batch-id "${batchId}"`).join(" ");
  let mergeResult = unwrap(await agent(
    `合并刚完成的固定 DAG 波次。执行 python "${mergerPath}" --workspace "${artifactWorkspace}" ` +
    `--feature "${feature}" --run-id "${runId}" ${mergeIdArgs} --conflict-mode native-rebase。` +
    `只允许 shared workflow owner 执行此命令；不要修改业务代码。返回 JSON；若返回 needsResolution=true，必须保留 manifest 与插件原生 worktree，禁止自行标记成功。`,
    { label: `merge-wave-${schedulerWaves}`, phase: "合并", schema: MERGE_RESULT_SCHEMA }
  ));
  mergeResults.push(mergeResult);

  if (!mergeResult.success && mergeResult.needsResolution) {
    if (MAX_AUTO_MERGE_RESOLUTION_ATTEMPTS < 1) {
      throw new Error(JSON.stringify({ error: "merge_resolution_disabled", runId, mergeResult, batchResults, mergeResults }));
    }
    const failedDelivery = Array.isArray(mergeResult.failed)
      ? mergeResult.failed.find(item => item && item.needsResolution && item.resolution)
      : null;
    const resolution = failedDelivery && failedDelivery.resolution;
    const conflictBatchId = failedDelivery && failedDelivery.batchId;
    if (!conflictBatchId || !resolution || !resolution.worktreePath || !resolution.branchName || !resolution.targetSha) {
      throw new Error(JSON.stringify({ error: "merge_resolution_contract_invalid", runId, mergeResult, batchResults, mergeResults }));
    }
    phase("合并");
    const resolved = requireSuccess(await agent(
      `自动收口 Batch ${conflictBatchId} 的真实 Git 冲突。只能操作插件保留的原生 worktree "${resolution.worktreePath}"，` +
      `分支必须是 "${resolution.branchName}"，目标提交必须是 "${resolution.targetSha}"。先 cd 到该 worktree，确认 git 根和分支；` +
      `执行 git rebase "${resolution.targetSha}"，按冲突文件逐个进行语义合并，保留双方不冲突的改动并补齐必要的接口/配置兼容。` +
      `禁止 git checkout --ours/--theirs、git restore、git reset --hard、git rebase --skip、git merge --abort、任何 --no-verify，` +
      `禁止修改主业务 checkout、删除 worktree 或丢弃任一侧改动。仅对已解决文件执行 git add，使用 GIT_EDITOR=true git rebase --continue。` +
      `完成后必须确认 worktree clean、git merge-base --is-ancestor "${resolution.targetSha}" HEAD 成功，并执行验证命令：` +
      `${JSON.stringify(resolution.validationCommands || [])}。只返回 {success:true,batchId,worktreePath,branchName,commitSha}。任何失败返回 success:false。`,
      {
        label: `resolve-merge-conflict-${conflictBatchId}`,
        phase: "合并",
        schema: {
          type: "object",
          properties: {
            success: { type: "boolean" },
            batchId: { type: "string" },
            worktreePath: { type: "string" },
            branchName: { type: "string" },
            commitSha: { type: "string" },
            errorMessage: { type: "string" }
          },
          required: ["success"],
          additionalProperties: false
        }
      }
    ), "merge conflict resolution");
    const registered = requireSuccess(await agent(
      `确认冲突解决提交并恢复合并屏障。执行 python "${mergerPath}" resolve --workspace "${artifactWorkspace}" ` +
      `--feature "${feature}" --run-id "${runId}" --batch-id "${conflictBatchId}"。只返回 JSON；` +
      `只有 worktree clean、分支已基于 targetSha 且无残余冲突时才允许 success。`,
      { label: `register-merge-resolution-${conflictBatchId}`, phase: "合并", schema: { type: "object", properties: { success: { type: "boolean" }, batchId: { type: "string" }, commitSha: { type: "string" }, error: { type: "string" } }, required: ["success"], additionalProperties: false } }
    ), "register merge resolution");
    if (!resolved.success || !registered.success) {
      throw new Error(JSON.stringify({ error: "merge_resolution_failed", runId, resolved, registered, batchResults, mergeResults }));
    }
    mergeResult = requireSuccess(await agent(
      `重试刚完成冲突收口的 Batch。执行 python "${mergerPath}" --workspace "${artifactWorkspace}" ` +
      `--feature "${feature}" --run-id "${runId}" --batch-id "${conflictBatchId}" --conflict-mode native-rebase。只返回 JSON。`,
      { label: `merge-resolved-wave-${schedulerWaves}`, phase: "合并", schema: MERGE_RESULT_SCHEMA }
    ), "merge resolved wave");
    mergeResults.push(mergeResult);
  }
  if (!mergeResult.success && mergeResult.failed?.some(item => item && item.needsPlanRecovery)) {
    const planFailure = mergeResult.failed.find(item => item && item.needsPlanRecovery);
    const recoveryBatchId = planFailure && planFailure.batchId;
    if (!recoveryBatchId) {
      throw new Error(JSON.stringify({ error: "plan_recovery_contract_invalid", runId, mergeResult, batchResults, mergeResults }));
    }
    const recovered = requireSuccess(await agent(
      `Git 已完成合并但 Plan 状态更新失败。执行 python "${mergerPath}" recover-plan --workspace "${artifactWorkspace}" ` +
      `--feature "${feature}" --run-id "${runId}" --batch-id "${recoveryBatchId}"，仅恢复 Plan 元数据，禁止修改业务代码。只返回 JSON。`,
      { label: `recover-plan-state-${recoveryBatchId}`, phase: "合并", schema: { type: "object", properties: { success: { type: "boolean" }, batchId: { type: "string" }, commitSha: { type: "string" }, error: { type: "string" } }, required: ["success"], additionalProperties: false } }
    ), "recover plan state");
    if (!recovered.success) {
      throw new Error(JSON.stringify({ error: "plan_state_recovery_failed", runId, recovered, batchResults, mergeResults }));
    }
    mergeResult = {
      success: true,
      merged: [{ batchId: recoveryBatchId, commitSha: recovered.commitSha }],
      failed: [],
      totalConflicts: 0,
      recoveredPlanState: true,
    };
    mergeResults.push(mergeResult);
  }
  if (!mergeResult.success) {
    throw new Error(JSON.stringify({ error: "merge_failed", runId, mergeResult, batchResults, mergeResults }));
  }

  const resumed = requireSuccess(await agent(
    `执行 python "${schedulerPath}" resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" ${workspaceRefArgs}。` +
    `只返回 JSON。下游 Batch 必须仅在依赖已 merged 后才会出现在 scheduledGroups 中。`,
    { label: `schedule-wave-${schedulerWaves + 1}`, phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
  ), "scheduler resume");
  scheduledGroups = scopeGroups(resumed.scheduledGroups || []);
  mergeableBatches = (resumed.mergeableBatches || []).filter(batchId => !allowedBatchIds.length || allowedBatchIds.includes(batchId));
  batchTaskIds = resumed.batchTaskIds || batchTaskIds;
  batchWorkspaces = resumed.batchWorkspaces || batchWorkspaces;
  if (!scheduledGroups.length && !mergeableBatches.length && !["verifying", "succeeded"].includes(resumed.status) && !resumed.waitingForRepositories && !hasWorkOutsideScope(resumed)) {
    throw new Error(JSON.stringify({ error: "parallel_scheduler_stalled", runId, scheduler: resumed, batchResults, mergeResults }));
  }
}

if (coordinatorManaged) {
  return {
    ok: true,
    feature,
    runId,
    batchResults,
    mergeResults,
    waitingForRepositories: true,
    nextAction: "repository_coordinator_next",
  };
}

phase("最终验证");
const verification = requireSuccess(await agent(
  `执行 python "${finalVerifyPath}" --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
  `只执行最终编译门禁；失败必须阻断 run，并将 hook 返回的 error 原样写入 errorMessage。`,
  { label: "verify-merged-batches", phase: "最终验证", schema: VERIFICATION_SCHEMA }
), "final verification");

return { ok: true, feature, runId, batchResults, mergeResults, verification };
