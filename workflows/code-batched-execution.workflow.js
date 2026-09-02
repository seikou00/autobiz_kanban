export const meta = {
  name: "code-batched-execution",
  description: "Staged Batch DAG with per-Batch Review/UTest, merge train, and E2E-only finalization",
  whenToUse: "由 workflow_launcher.py 在存在合法待执行 Batch 时调用",
  phases: [
    { title: "准备", detail: "创建或恢复 scheduler run 并计算当前可执行 DAG 波次" },
    { title: "Batch 阶段", detail: "prepare → implement → review → test；仅声明静态检查时追加 quality gate，所有状态和证据持久化" },
    { title: "候选验证", detail: "Merge Train 只合成并推广已通过 Batch Review 与 UTest 的候选 SHA" },
    { title: "最终验证", detail: "合并后运行 B-E2E，最终只聚合既有证据、不重复执行命令" }
  ]
};

const DEFAULT_MAX_PARALLEL = 4;
const MAX_SCHEDULER_WAVES = 100;
// Repair review/test findings in their original Batch first.  A finding that
// persists remains blocked with its Worktree and can never enter merge train.
const MAX_DELIVERY_IMPLEMENTATION_REPAIRS = 3;
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
  required: ["runId", "status", "scheduledGroups", "batchTaskIds", "batchWorkspaces"],
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
const UTEST_STAGE_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    status: { enum: ["success", "failed", "timeout"] },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    commitSha: { type: "string" },
    testEvidenceIds: { type: "array", items: { type: "string" } },
    stageEvidenceId: { type: "string" },
    failureType: { type: "string" },
    nextStage: { type: "string" },
    failure: { type: "object" },
    errorMessage: { type: "string" }
  },
  required: ["batchId", "status", "worktreePath", "branchName", "commitSha"],
  additionalProperties: false
};
const MERGED_CLEANUP_SCHEMA = {
  type: "object",
  properties: {
    success: { type: "boolean" },
    cleanedBatchIds: { type: "array", items: { type: "string" } },
    releasedLeases: { type: "array", items: { type: "string" } },
    errors: { type: "array", items: { type: "string" } }
  },
  required: ["success", "cleanedBatchIds", "errors"],
  additionalProperties: true
};

function normalizeStructuredOutput(value) {
  let normalized = String(value || "").trim();
  // Some agents prepend their hidden reasoning even when asked for JSON only.
  // Remove complete think blocks before attempting to parse the actual result.
  normalized = normalized.replace(/\\?<think\b[^>]*>[\s\S]*?\\?<\/think>\s*/gi, "").trim();
  const fence = normalized.match(/^```(?:json)?[ \\t]*\\r?\\n([\\s\\S]*?)\\r?\\n?```[ \\t]*$/i);
  return fence ? fence[1].trim() : normalized;
}

function parseStructuredOutput(value) {
  const normalized = normalizeStructuredOutput(value);
  try {
    return { parsed: true, value: JSON.parse(normalized) };
  } catch (_) {
    // Keep JSON recovery deliberately narrow: only accept one balanced object
    // or array from surrounding prose, never arbitrary text as a success.
    for (let start = 0; start < normalized.length; start += 1) {
      if (normalized[start] !== "{" && normalized[start] !== "[") continue;
      const opening = normalized[start];
      const closing = opening === "{" ? "}" : "]";
      let depth = 0;
      let quoted = false;
      let escaped = false;
      for (let end = start; end < normalized.length; end += 1) {
        const character = normalized[end];
        if (quoted) {
          if (escaped) escaped = false;
          else if (character === "\\\\") escaped = true;
          else if (character === "\"") quoted = false;
          continue;
        }
        if (character === "\"") {
          quoted = true;
          continue;
        }
        if (character === opening) depth += 1;
        else if (character === closing) depth -= 1;
        if (depth !== 0) continue;
        try {
          return { parsed: true, value: JSON.parse(normalized.slice(start, end + 1)) };
        } catch (_) {
          break;
        }
      }
    }
    return { parsed: false };
  }
}

function unwrap(value) {
  if (value && typeof value === "object" && typeof value.value === "string") return unwrap(value.value);
  if (typeof value === "string") {
    const parsed = parseStructuredOutput(value);
    return parsed.parsed ? parsed.value : { raw: value, unparsedStructuredOutput: true };
  }
  return value || {};
}

function isFailedVerdict(value) {
  return typeof value === "string"
    && ["fail", "failed", "error", "reject", "rejected"].includes(value.trim().toLowerCase());
}

function isFailedStatus(value) {
  return typeof value === "string"
    && ["failed", "error", "timeout", "blocked", "needs_resolution"].includes(value.trim().toLowerCase());
}

function hasFailureSignal(value) {
  const result = unwrap(value);
  const failure = unwrap(result.failure);
  return [result, failure].some(candidate => (
    candidate
    && (
      candidate.ok === false
      || candidate.success === false
      || candidate.passed === false
      || isFailedVerdict(candidate.verdict)
      || isFailedStatus(candidate.status)
    )
  ));
}

function requireSuccess(value, label) {
  const result = unwrap(value);
  if (!result || result.unparsedStructuredOutput === true || hasFailureSignal(result)) {
    throw new Error(`${label} failed: ${JSON.stringify(result)}`);
  }
  return result;
}

function usableString(value) {
  return typeof value === "string"
    && value.trim().length > 0
    && !["undefined", "null"].includes(value.trim().toLowerCase());
}

function requireSchedulerResult(value, label) {
  const result = requireSuccess(value, label);
  const isObject = candidate => candidate && typeof candidate === "object" && !Array.isArray(candidate);
  if (
    !usableString(result.runId)
    || !usableString(result.status)
    || !Array.isArray(result.scheduledGroups)
    || !isObject(result.batchTaskIds)
    || !isObject(result.batchWorkspaces)
  ) {
    throw new Error(JSON.stringify({
      error: "parallel_scheduler_result_invalid",
      label,
      scheduler: result,
    }));
  }
  return result;
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
const lifecyclePath = joinPath(pluginPath, "hooks/parallel_batch_lifecycle.py");
const stagePath = joinPath(pluginPath, "hooks/parallel_batch_stage.py");
const stageValidationPath = joinPath(pluginPath, "hooks/parallel_stage_validation.py");
const utestRouterPath = joinPath(pluginPath, "hooks/utest_assignment_router.py");
const utestCommandPath = joinPath(pluginPath, "hooks/run_utest_command.py");
const utestEnvironmentPath = joinPath(pluginPath, "hooks/inspect_test_environment.py");
const utestSourceBugPath = joinPath(pluginPath, "hooks/validate_utest_source_bug.py");
const mergeTrainPath = joinPath(pluginPath, "hooks/parallel_merge_train.py");
const aggregatePath = joinPath(pluginPath, "hooks/parallel_evidence_aggregate.py");
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
    scheduler.allStageRecoveryBatches,
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
const prepared = requireSchedulerResult(await agent(
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
let stageRecoveryBatches = (prepared.stageRecoveryBatches || []).filter(result => result && usableString(result.batchId) && (!allowedBatchIds.length || allowedBatchIds.includes(result.batchId)));
let batchTaskIds = prepared.batchTaskIds || {};
let batchWorkspaces = prepared.batchWorkspaces || {};
const batchResults = [];
const mergeResults = [];
const cleanupResults = [];
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

function mergedBatchIds(mergeResult) {
  return [...new Set(
    [
      ...(Array.isArray(mergeResult && mergeResult.merged) ? mergeResult.merged.map(item => item && item.batchId) : []),
      // parallel_merge_train promote-candidate returns batchIds, while older
      // merge callers returned per-Batch entries in merged.  Cleanup must use
      // only the durable promotion result, never ready-to-candidate inputs.
      ...(mergeResult && mergeResult.promoted === true && Array.isArray(mergeResult.batchIds)
        ? mergeResult.batchIds
        : []),
    ].filter(batchId => usableString(batchId))
  )];
}

async function cleanupMergedWorktrees(batchIds, label) {
  const expected = [...new Set((Array.isArray(batchIds) ? batchIds : [])
    .filter(batchId => usableString(batchId)))];
  const batchArgs = expected.map(batchId => `--batch-id "${batchId}"`).join(" ");
  const cleanup = requireSuccess(await agent(
    `清理已交付 Batch 的插件原生 Worktree。执行 python "${lifecyclePath}" cleanup-merged ` +
    `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" ${batchArgs}。` +
    `只允许清理 manifest 中 status=merged 的 Batch：释放残留 lease、删除该 Worktree 与临时分支并更新 manifest；` +
    `不得清理 failed、blocked 或 needs_resolution 的 Worktree。只返回 JSON。`,
    { label, phase: "合并", schema: MERGED_CLEANUP_SCHEMA }
  ), label);
  const cleaned = new Set(Array.isArray(cleanup.cleanedBatchIds) ? cleanup.cleanedBatchIds : []);
  const missing = expected.filter(batchId => !cleaned.has(batchId));
  if (missing.length > 0) {
    throw new Error(`merged_worktree_cleanup_incomplete:${missing.join(",")}`);
  }
  cleanupResults.push(cleanup);
  return cleanup;
}

function requiresImplementationRework(result) {
  const normalized = unwrap(result);
  const failure = unwrap(normalized.failure);
  return (
    normalized.nextStage === "implement" ||
    failure.nextStage === "implement" ||
    ((isFailedStatus(normalized.status) || isFailedVerdict(normalized.verdict)) && normalized.failureType === "implementation") ||
    ((isFailedStatus(failure.status) || isFailedVerdict(failure.verdict)) && (failure.failureType === "implementation" || failure.type === "implementation"))
  );
}

function implementationReworkRequired(batchResult, failedStage, result) {
  const normalized = unwrap(result);
  const failure = unwrap(normalized.failure);
  const failureType = failure.type || normalized.failureType || "implementation";
  const failureMessage = failure.message || normalized.message || normalized.error || "";
  if (!usableString(failureMessage)) {
    throw new Error(`implementation_rework_failure_message_missing:${batchResult.batchId}:${failedStage}`);
  }
  // Keep the finding next to the recovery coordinates.  The implementer must
  // receive this exact context, rather than inferring a newly-found defect
  // from a prior implementation evidence record.
  const failureContext = {
    failedStage,
    failureType,
    message: failureMessage,
  };
  return {
    batchId: batchResult.batchId,
    status: "implementation_rework_required",
    failedStage,
    reviewResult: normalized,
    reworkFingerprint: JSON.stringify({ failedStage, failureType, failureMessage }),
    recovery: {
      batchId: batchResult.batchId,
      worktreePath: batchResult.worktreePath,
      branchName: batchResult.branchName,
      commitSha: batchResult.commitSha,
      nextStage: "implement",
      failureContext,
    },
  };
}

function withLatestBatchDelivery(batchResult, result) {
  const normalized = unwrap(result);
  return {
    ...batchResult,
    worktreePath: usableString(normalized.worktreePath) ? normalized.worktreePath : batchResult.worktreePath,
    branchName: usableString(normalized.branchName) ? normalized.branchName : batchResult.branchName,
    commitSha: usableString(normalized.commitSha) ? normalized.commitSha : batchResult.commitSha,
  };
}

async function runBatchUtestAndSeal(batchResult) {
  const batchId = batchResult.batchId;
  const batchWorktree = batchResult.worktreePath;
  const batchBranch = batchResult.branchName;
  const batchWorkspace = batchWorkspaces[batchId] || {};
  const batchWorkspaceRef = batchWorkspace.workspaceRef;
  const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
  if (!usableString(batchWorktree) || !usableString(batchBranch) || !usableString(batchWorkspaceRef) || !taskIds.length) {
    throw new Error("utest_batch_context_missing:" + batchId);
  }
  const taskIdArgs = taskIds.map(taskId => "--task-id \"" + taskId + "\"").join(" ");
  const taskList = JSON.stringify(taskIds);
  const prompt =
    "在 Batch " + batchId + " 的原生 Git worktree \"" + batchWorktree + "\"、分支 \"" + batchBranch + "\" 内完成该 Batch 的 UTest。TASK=" + taskList + "。测试点只来自这些 TASK 的 UTEST_ASSIGNMENT/testIntent；不得读取或修改其他 Batch、主 checkout、计划 JSON 或平台产物。\n" +
    "这是 Code Review 之后的测试阶段：Review 只审业务生产代码；现在由你生成/补齐测试源码、fixture/mock/测试环境配置并运行测试。测试代码必须留在当前 Worktree，并会随本 Batch 再次封存后合并；禁止把测试拆成独立 Batch。\n" +
    "严格执行：1) cd 到该 Worktree，确认 git 顶层与分支匹配；2) 执行 python \"" + leasePath + "\" acquire --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --ttl-seconds " + timeoutPerBatch + "，保存 lease.ownerToken，并在整个 UTest、seal 期间对同一 token 保持 heartbeat；3) 执行 python \"" + stagePath + "\" start --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test；4) 执行 python \"" + utestRouterPath + "\" --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --json，且只使用其中 batchId=\"" + batchId + "\"、workspaceRef=\"" + batchWorkspaceRef + "\" 的 assignment 原文；5) 执行 python \"" + utestEnvironmentPath + "\" --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" " + taskIdArgs + " --batch-worktree \"" + batchWorktree + "\" --json。环境非 ready 时只按 UTest 协议修测试环境并重新检查，无法解决则 fail stage 为 environment。\n" +
    "6) 对每个实际 TASK 生成或补齐行为测试：覆盖 implementationPoints 与全部 AC，排除 nonGoals；使用真实工程 runner。每个测试文件落地后，必须执行 python \"" + utestCommandPath + "\" --kind test --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --task-id <真实TASK_ID> --batch-worktree \"" + batchWorktree + "\" --test-file <仓库根相对测试文件> -- <真实精确测试 argv>。不得把 Plan validationCommands 的 argv 当作测试 argv。测试自身、fixture、mock、测试配置的问题必须在本阶段修复并重跑。\n" +
    "7) 若有已执行且非零退出的测试，只能使用该次 run_utest_command JSON 返回的 taskId、commandId、targetId、taskDigest、evidenceId 执行 python \"" + utestSourceBugPath + "\" --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --task-id <返回taskId> --command-id <返回commandId> --target-id <返回targetId> --task-digest <返回taskDigest> --evidence-id <返回evidenceId>；仅该命令成功才可判为 source_bug。此时不要直接改生产代码。先用 python \"" + worktreeManagerPath + "\" --json seal --artifact-workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --repo \"" + batchWorktree + "\" --owner-token <真实token> 封存新测试资产，再执行 python \"" + stagePath + "\" fail --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test --failure-type implementation --message \"<必须包含 targetId、commandId、evidenceId、test-output.log 路径、失败断言的 expected/actual 或 stdout/stderr 根因>\"，最后以 final-status sealed 释放 lease，并返回失败 JSON。Workflow 会将该具体失败原因传给同一 Batch 的生产代码 repair，随后重新编译、封存、Review 和 UTest。\n" +
    "8) 全部 UTest 通过后，执行 python \"" + worktreeManagerPath + "\" --json seal --artifact-workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --repo \"" + batchWorktree + "\" --owner-token <真实token> 取得新的 commitSha；再执行 python \"" + stagePath + "\" complete --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test --metadata-json <包含 batchCommit、新 commitSha、testEvidenceIds、worktreePath、branchName 的对象>；最后以 final-status sealed 释放 lease。\n" +
    "成功只返回 {batchId,status:\"success\",worktreePath,branchName,commitSha,testEvidenceIds,stageEvidenceId}。source_bug 只返回 {batchId,status:\"failed\",worktreePath,branchName,commitSha,failureType:\"implementation\",nextStage:\"implement\",failure:{type:\"implementation\",message,nextStage:\"implement\"}}。环境/契约等不可自动修复问题也必须先 fail stage、释放 lease，再返回 status:\"failed\" 与 failure。不得手工 git add/commit、merge、rebase 或删除 Worktree。";
  return unwrap(await agent(
    prompt,
    { label: "stage-utest-" + batchId, phase: "Batch 阶段", schema: UTEST_STAGE_SCHEMA }
  ));
}

async function runDeliveryReviewTestAndGate(batchResult) {
  const batchId = batchResult.batchId;
  const batchWorktree = batchResult.worktreePath;
  const batchBranch = batchResult.branchName;
  const commitSha = batchResult.commitSha;
  const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
  const qualityGateRequired = (batchWorkspaces[batchId] || {}).qualityGateRequired === true;
  if (!usableString(commitSha)) throw new Error(`sealed_batch_commit_missing:${batchId}`);
  const metadata = JSON.stringify({ batchCommit: commitSha, worktreePath: batchWorktree, branchName: batchBranch });
  const stageResult = requireSuccess(await agent(
    `登记 Batch ${batchId} 已完成的准备与实现阶段。依次执行：` +
    `python "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage prepare；` +
    `python "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage prepare --metadata-json '${metadata}'；` +
    `python "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage implement；` +
    `python "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage implement --metadata-json '${metadata}'。只返回最后一个 JSON。`,
    { label: `stage-implement-${batchId}`, phase: "Batch 阶段" }
  ), `stage implement ${batchId}`);
  void stageResult;
  const review = unwrap(await agent(
    `对已封存的 Batch ${batchId} 做只读评审。代码只在原生 worktree "${batchWorktree}"，分支 "${batchBranch}"；TASK 范围仅为 ${JSON.stringify(taskIds)}。` +
    `先执行 python "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review。` +
    `只评审业务生产代码、生产配置、迁移和公开接口的实现；测试源码、fixture/mock 和测试环境由紧随其后的 UTest 阶段创建。即使 scope.paths、expectedFiles 或 writeSet 中出现测试路径，也不得因 sealed commit 缺少测试文件而判定 Review 不通过；可评估可测试性，但不得要求测试资产已存在。评审实现、接口边界、错误处理和与 TASK 验收条件的一致性；禁止修改源码、提交、合并或删除 Worktree。` +
    `通过后执行 python "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review --metadata-json '${metadata}'。` +
    `发现问题时必须先执行 python "${stagePath}" fail --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review --failure-type <implementation|documentation|needs_triage> --message "<具体问题：file:line、期望与实际行为、影响及建议修复>"，再返回该命令的 JSON。` +
    `可由当前 Batch 生产代码修复时，返回 JSON 必须同时包含 status:"failed"、verdict:"FAIL"、failureType:"implementation"、nextStage:"implement" 及 failure；Workflow 会在同一 Worktree 修复、重新编译、重新封存后再次评审。` +
    `documentation 与 needs_triage 仍按原分类阻断，保留 Worktree。只返回 JSON。`,
    { label: `stage-review-${batchId}`, phase: "Batch 阶段" }
  ));
  if (requiresImplementationRework(review)) {
    return implementationReworkRequired(batchResult, "review", review);
  }
  requireSuccess(review, `stage review ${batchId}`);
  const test = await runBatchUtestAndSeal(batchResult);
  const testedDelivery = withLatestBatchDelivery(batchResult, test);
  if (requiresImplementationRework(test)) {
    return implementationReworkRequired(testedDelivery, "test", test);
  }
  requireSuccess(test, `stage test ${batchId}`);
  batchResult = testedDelivery;
  if (qualityGateRequired) {
    requireSuccess(await agent(
      `执行 Batch ${batchId} 的静态质量门。执行 python "${stageValidationPath}" run --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage quality_gate。` +
      `该命令只运行 Plan 明确归属本 Batch 的 qualityGateCommands（lint/static check）；编译已经由 implement 阶段唯一执行，禁止重复 TASK 测试、projectValidationCommands 或 E2E。` +
      `通过后执行 python "${stagePath}" gate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。` +
      `只返回 gate JSON；只有 ready_to_candidate 才算成功。`,
      { label: `stage-quality-gate-${batchId}`, phase: "Batch 阶段" }
    ), `stage quality gate ${batchId}`);
  } else {
    requireSuccess(await agent(
      `Batch ${batchId} 未声明 qualityGateCommands，质量门不创建空步骤。直接执行 python "${stagePath}" gate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。` +
      `只返回 gate JSON；只有 ready_to_candidate 才算成功。`,
      { label: `stage-gate-${batchId}`, phase: "Batch 阶段" }
    ), `stage gate ${batchId}`);
  }
  return {
    batchId,
    status: "ready_to_candidate",
    worktreePath: batchResult.worktreePath,
    branchName: batchResult.branchName,
    commitSha: batchResult.commitSha,
  };
}

async function reworkDeliveryImplementation(recovery) {
  const batchId = recovery.batchId;
  const batchWorktree = recovery.worktreePath;
  const batchBranch = recovery.branchName;
  const batchWorkspace = batchWorkspaces[batchId] || {};
  const batchWorkspaceRef = batchWorkspace.workspaceRef;
  const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
  if (!usableString(batchWorktree) || !usableString(batchBranch) || !usableString(batchWorkspaceRef) || !taskIds.length) {
    throw new Error(`implementation_rework_context_missing:${batchId}`);
  }
  const failureContext = recovery && typeof recovery.failureContext === "object" && recovery.failureContext !== null
    ? recovery.failureContext
    : null;
  const testLogPath = failureContext && failureContext.failedStage === "test"
    ? (usableString(failureContext.testLogPath)
      ? failureContext.testLogPath
      : artifactWorkspace + "/.autobizdevops/features/" + feature + "/test-output.log")
    : null;
  const repairBrief = failureContext
    ? `本次打回的精确问题如下（必须作为修复基线，不能重新猜测原因）：${JSON.stringify(failureContext)}。` +
      (testLogPath ? `这是 UTest 打回；先读取失败原始日志 "${testLogPath}"，并用 failure message 中的 target/command/evidence 锚定问题。` : "")
    : "这是未完成 implement 的中断恢复，没有 review/test 打回上下文；按原 TASK 恢复执行。";
  return requireSuccess(await agent(
    `恢复 Batch ${batchId} 的 implement 阶段；之前的 review/test 失败已使该阶段的旧 evidence 失效。只能在既有原生 worktree "${batchWorktree}"、分支 "${batchBranch}" 内操作。${repairBrief}` +
    `依次执行：1) 用 batch_lease_manager.py acquire 获取真实 lease token（workspace="${artifactWorkspace}"、feature="${feature}"、run-id="${runId}"、batch-id="${batchId}"），随后 mark-batch 为 running；` +
    `2) 对需要修复的 TASK（仅 ${JSON.stringify(taskIds)}）读取其 latestImplementationEvidenceId，并使用 task_runner.py start-task-repair --prior-evidence-id <该真实 ID> --parallel-run-id "${runId}" --lease-token <真实 token> --code-workspace "${batchWorktree}" --workspace-ref "${batchWorkspaceRef}"；` +
    `3) 修复生产代码后，用 finish-implementation --repair-mode 和该 start 返回的真实 task run-id 记录新的 implementation evidence；` +
    `4) 用 task_runner.py batch-compile 在同一 worktree 编译，通过后用 worktree_manager.py seal 产生新的 commitSha，再以 final-status sealed 释放同一 lease。` +
    `不得创建新分支/Worktree、不得合并、不得运行非本 Batch 的验证；任何失败保留 Worktree 并以 failed 释放 lease。返回 {batchId,status:"success",compileStatus:"passed",worktreePath,branchName,commitSha}。`,
    { label: `rework-implement-${batchId}`, phase: "Batch 阶段", schema: BATCH_RESULT_SCHEMA }
  ), `implementation rework ${batchId}`);
}

async function blockImplementationFinding(delivery, staged, disposition) {
  const batchId = delivery.batchId;
  const message = "unresolved_" + staged.failedStage + "_implementation_finding:" + disposition;
  return requireSuccess(await agent(
    "Batch " + batchId + " 的 " + staged.failedStage + " 实现问题在受控修复后仍未关闭。不得将 deferred finding 放行到 merge train。执行 python \"" + schedulerPath + "\" mark-batch --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --status blocked --worktree-path \"" + delivery.worktreePath + "\" --branch-name \"" + delivery.branchName + "\" --error \"" + message + "\"。保留 Worktree 和阶段失败证据，勿修改代码、合并或删除 Worktree；只返回 JSON。",
    { label: "block-unresolved-" + staged.failedStage + "-" + batchId, phase: "Batch 阶段" }
  ), "block unresolved " + staged.failedStage + " finding " + batchId);
}

async function runDeliveryWithImplementationRepair(batchResult) {
  let delivery = batchResult;
  const seenFeedback = new Set();
  for (let attempt = 0; attempt <= MAX_DELIVERY_IMPLEMENTATION_REPAIRS;) {
    const staged = await runDeliveryReviewTestAndGate(delivery);
    if (staged.status === "ready_to_candidate") return staged;
    if (staged.status !== "implementation_rework_required") {
      throw new Error(`unexpected_delivery_stage_result:${JSON.stringify(staged)}`);
    }
    const disposition = attempt >= MAX_DELIVERY_IMPLEMENTATION_REPAIRS
      ? "repair_limit_reached"
      : seenFeedback.has(staged.reworkFingerprint)
        ? "repeated_feedback"
        : null;
    if (disposition) {
      await blockImplementationFinding(delivery, staged, disposition);
      throw new Error("delivery_implementation_repair_unresolved:" + delivery.batchId + ":" + staged.failedStage + ":" + disposition);
    }
    seenFeedback.add(staged.reworkFingerprint);
    // review/test -> implement repair -> compile/seal -> review.  The repair
    // keeps the original native Worktree and branch, so no unreviewed change
    // can bypass the delivery evidence or merge train.
    const priorCommitSha = delivery.commitSha;
    const repaired = await reworkDeliveryImplementation(staged.recovery);
    if (!usableString(repaired.commitSha) || repaired.commitSha === priorCommitSha) {
      await blockImplementationFinding(delivery, staged, "no_new_commit");
      throw new Error("delivery_implementation_repair_unresolved:" + delivery.batchId + ":" + staged.failedStage + ":no_new_commit");
    }
    delivery = repaired;
    attempt += 1;
  }
  throw new Error(`delivery_implementation_rework_loop_unreachable:${delivery.batchId}`);
}

async function continueRecoveredDelivery(recovery) {
  const delivery = recovery.nextStage === "implement"
    ? await reworkDeliveryImplementation(recovery)
    : recovery;
  return runDeliveryWithImplementationRepair(delivery);
}

function candidateGroups(batchIds) {
  const groups = {};
  for (const batchId of batchIds) {
    const ref = (batchWorkspaces[batchId] || {}).workspaceRef;
    if (!usableString(ref)) throw new Error(`scheduler did not provide repository for ${batchId}`);
    groups[ref] = groups[ref] || [];
    groups[ref].push(batchId);
  }
  return groups;
}

async function validateAndPromoteWave(batchIds, wave) {
  const groups = candidateGroups(batchIds);
  const promoted = [];
  for (const [repositoryRef, ids] of Object.entries(groups)) {
    const batchArgs = ids.map(batchId => `--batch-id "${batchId}"`).join(" ");
    // A changed main invalidates the entire candidate.  Rebuild from the
    // current head and rebuild the candidate; never rebase a previously-gated
    // candidate, because that would sever the evidence-to-SHA relationship.
    let promotion;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const builtRaw = unwrap(await agent(
        `构建 Wave ${wave} 的 Merge Train 候选（第 ${attempt} 次）。执行 python "${mergeTrainPath}" build-candidate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave} ${batchArgs}。` +
        `候选创建失败时保留 delivery Worktree 并停止，禁止 rebase 或直接合并主分支。只返回 JSON。`,
        { label: `build-candidate-${repositoryRef}-${wave}-${attempt}`, phase: "候选验证" }
      ));

      // Handle conflict resolution if needed
      let built = builtRaw;
      if (builtRaw && builtRaw.status === "candidate_conflicted") {
        log(`Wave ${wave} 检测到冲突，尝试自动解决...`);
        const resolved = unwrap(await agent(
          `尝试解决 Wave ${wave} 的合并冲突。执行 python "${mergeTrainPath}" resolve-candidate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave}。` +
          `若自动解决成功，返回 built 状态和新的 candidateSha；若需要人工介入，返回 needs_resolution 状态。只返回 JSON。`,
          { label: `resolve-conflict-${repositoryRef}-${wave}`, phase: "冲突解决" }
        ));

        if (resolved && resolved.status === "built") {
          log(`Wave ${wave} 冲突已自动解决，方法：${resolved.resolutionMethod || 'unknown'}`);
          built = resolved;
        } else if (resolved && resolved.status === "needs_resolution") {
          // Manual intervention required - throw error with context
          throw new Error(`Wave ${wave} 需要人工解决冲突: ${JSON.stringify({
            repositoryRef,
            wave,
            conflictedFiles: resolved.conflictedFiles,
            worktreePath: resolved.worktreePath,
            reason: resolved.reason || resolved.error
          })}`);
        } else {
          // Resolution failed entirely
          throw new Error(`Wave ${wave} 冲突解决失败: ${JSON.stringify(resolved)}`);
        }
      }

      // Now require success on the built result
      built = requireSuccess(built, `build candidate ${repositoryRef}`);

      const rawPromotion = unwrap(await agent(
        `推广已完成业务 Review 与 UTest 的候选 SHA ${built.candidateSha}。执行 python "${mergeTrainPath}" promote-candidate --allow-unverified --allow-stale --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave}。` +
        `Batch 已在合并前完成全部业务 Review 与 UTest；合并后唯一的可执行验证是 B-E2E。若返回 stale=true，必须停止本次推广并从当前 main 全量重建候选；禁止 rebase 或直接 merge。只返回 JSON。`,
        { label: `promote-candidate-${repositoryRef}-${wave}-${attempt}`, phase: "候选验证" }
      ));
      if (rawPromotion && rawPromotion.stale === true && attempt < 2) continue;
      promotion = requireSuccess(rawPromotion, `promote candidate ${repositoryRef}`);
      break;
    }
    promoted.push({ repositoryRef, ids, ...promotion });
  }
  return promoted;
}

// A resumed run can already contain merged deliveries from a prior interrupted
// Workflow. Clear those first so a retry never inherits occupied branches.
await cleanupMergedWorktrees([], "recover-merged-worktree-cleanup");

if (!scheduledGroups.length && !mergeableBatches.length && !stageRecoveryBatches.length && !["verifying", "succeeded"].includes(prepared.status) && !prepared.waitingForRepositories && !hasWorkOutsideScope(prepared)) {
  throw new Error(JSON.stringify({ error: "parallel_scheduler_stalled", runId, scheduler: prepared, batchResults, mergeResults }));
}

while (scheduledGroups.length > 0 || mergeableBatches.length > 0 || stageRecoveryBatches.length > 0) {
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
      `7. 编译通过后再次以同一真实 token 执行 lease check；heartbeat 继续运行。随后只调用 python "${worktreeManagerPath}" --json seal，并携带 --artifact-workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--repo "${batchWorktree}" 和 --owner-token（同一真实 token）；从 JSON 保存 commitSha。插件在此命令中提交；不要自行 git add、git commit 或把 Batch 标为可候选合并。\n` +
      `8. seal 成功后停止 heartbeat（POSIX 使用 kill，Windows 使用 Stop-Process；等待进程退出并删除 "${heartbeatPidFile}"、"${heartbeatStdoutFile}"、"${heartbeatStderrFile}"），再执行 python "${leasePath}" release，并携带 --workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--owner-token（同一真实 token）和 --final-status sealed。首次命令失败时，立即停止 heartbeat、删除这三个文件、以 failed 释放 lease（仍有效时）；随后只返回 failed。禁止检查/修改插件源码、创建 Git wrapper、尝试替代命令或继续任何 TASK。\n` +
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

  // Each candidate contains only Batch deliveries whose Review and UTest
  // evidence passed. A dependency unlocks only after that exact SHA is
  // fast-forwarded; there is no B-INT candidate execution phase.
  const recovered = await parallel(
    stageRecoveryBatches.map(result => () => continueRecoveredDelivery(result))
  );
  const gated = await parallel(
    batchResults
      .filter(result => result && result.status === "success" && currentWaveBatchIds.includes(result.batchId))
      .map(result => () => runDeliveryWithImplementationRepair(result))
  );
  const mergeIds = [...new Set([...mergeableBatches, ...gated.map(result => result.batchId), ...recovered.map(result => result.batchId)])]
    .filter(batchId => !allowedBatchIds.length || allowedBatchIds.includes(batchId));
  if (mergeIds.length > 0) {
    phase("候选验证");
    const promotions = await validateAndPromoteWave(mergeIds, schedulerWaves);
    mergeResults.push({ success: true, wave: schedulerWaves, promotions });
    const promotedBatchIds = promotions.flatMap(mergedBatchIds);
    const missingPromotionBatchIds = promotions
      .filter(promotion => promotion && promotion.promoted === true && mergedBatchIds(promotion).length === 0)
      .map(promotion => promotion.repositoryRef || "unknown");
    if (missingPromotionBatchIds.length > 0) {
      throw new Error(`promotion_batch_ids_missing:${missingPromotionBatchIds.join(",")}`);
    }
    await cleanupMergedWorktrees(promotedBatchIds, `cleanup-promoted-wave-${schedulerWaves}`);
  }

  const resumed = requireSchedulerResult(await agent(
    `执行 python "${schedulerPath}" resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" ${workspaceRefArgs}。` +
    `只返回 JSON。下游 Batch 必须仅在依赖已 merged 后才会出现在 scheduledGroups 中。`,
    { label: `schedule-wave-${schedulerWaves + 1}`, phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
  ), "scheduler resume");
  scheduledGroups = scopeGroups(resumed.scheduledGroups || []);
  mergeableBatches = (resumed.mergeableBatches || []).filter(batchId => !allowedBatchIds.length || allowedBatchIds.includes(batchId));
  stageRecoveryBatches = (resumed.stageRecoveryBatches || []).filter(result => result && usableString(result.batchId) && (!allowedBatchIds.length || allowedBatchIds.includes(result.batchId)));
  batchTaskIds = resumed.batchTaskIds || batchTaskIds;
  batchWorkspaces = resumed.batchWorkspaces || batchWorkspaces;
if (!scheduledGroups.length && !mergeableBatches.length && !stageRecoveryBatches.length && !["verifying", "succeeded"].includes(resumed.status) && !resumed.waitingForRepositories && !hasWorkOutsideScope(resumed)) {
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
    cleanupResults,
    waitingForRepositories: true,
    nextAction: "repository_coordinator_next",
  };
}

phase("最终验证");
const e2eStarted = requireSuccess(await agent(
  `所有 delivery Batch 已推广后，创建 B-E2E。执行 python "${mergeTrainPath}" begin-e2e --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
  `此命令只创建 main SHA 绑定的验证状态；它不运行 Batch compile 或 UTest。只返回 JSON。`,
  { label: "begin-e2e-validation", phase: "最终验证" }
), "begin e2e");
const e2e = requireSuccess(await agent(
  `在当前已合并 main 上执行唯一的 B-E2E 验证。Feature=${feature}，runId=${runId}。` +
  `必须只在这些插件创建的临时验证 Worktree 中操作：${JSON.stringify(e2eStarted.worktrees || {})}；不得操作主 checkout。` +
  `先收集可重现环境元数据：environment.version、environment.seedDataDigest、environment.dependencies（对象，含 DB/Redis/MQ 等实际版本或明确的 none），并将其与场景摘要一并作为 JSON metadata。` +
  `执行 Plan/Feature 定义且未被 Batch UTest 覆盖的端到端场景；如有 projectValidationCommands，它们现在唯一归属 V-E2E，须在同一临时 Worktree 中由随后命令执行。不得重复执行 Batch test、compile 或 quality gate。随后执行 Plan 唯一归属 V-E2E 的命令：python "${stageValidationPath}" run --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "V-E2E" --stage e2e_test --metadata-json '<含上述 environment 与场景摘要的 JSON>'。` +
  `通过后执行 python "${mergeTrainPath}" finish-e2e --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --passed true --metadata-json '<同一份含 environment 的 JSON>'。` +
  `失败时使用 --passed false 并记录失败摘要；失败会创建受控修复入口，禁止在 main 直接修复。只返回 JSON。`,
  { label: "run-e2e-validation", phase: "最终验证" }
), "e2e validation");
void e2eStarted;
const verification = requireSuccess(await agent(
  `执行 python "${aggregatePath}" --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
  `这是只读 evidence aggregate：禁止执行任何编译、测试或 E2E 命令。只返回 JSON。`,
  { label: "aggregate-staged-evidence", phase: "最终验证", schema: VERIFICATION_SCHEMA }
), "evidence aggregate");

return {
  ok: true,
  feature,
  runId,
  batchResults,
  mergeResults,
  cleanupResults,
  e2e,
  verification,
  finalStatus: "succeeded",
  deferredIssues: [],
};
