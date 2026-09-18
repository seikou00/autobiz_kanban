export const meta = {
  name: "code-batched-execution",
  description: "Staged Batch DAG with per-Batch Review/UTest, merge train, and E2E-only finalization",
  whenToUse: "由 workflow_launcher.py 在存在合法待执行 Batch 时调用",
  phases: [
    { title: "准备", detail: "创建或恢复 scheduler run 并计算当前可执行 DAG 集合" },
    { title: "Batch 阶段", detail: "编码 → review → UTest/封存；Review 可定向修复一次，UTest 失败记录后继续" },
    { title: "候选验证", detail: "Merge Train 合成并推广已完成 Review 与 UTest 记录的候选 SHA" },
    { title: "最终验证", detail: "合并后运行 B-E2E，最终只聚合既有证据、不重复执行命令" }
  ]
};

const DEFAULT_MAX_PARALLEL = 4;
const MAX_SCHEDULER_CYCLES = 100;
// A transport-level empty model response happens before the child can return
// its command result.  It is neither a Batch verdict nor evidence that the
// command failed, so give that same child a small, bounded retry budget.
// Retrying other errors would be unsafe because they may describe a genuine
// command failure after a state-changing operation.
const MAX_EMPTY_AGENT_RESPONSE_RETRIES = 2;
// The scheduler is the authority for the per-Batch retry budget (currently
// two failure admissions).  Keep one extra pass to let the scheduler turn a
// legacy retry marker with a zero counter into its explicit exhausted state.
// The Workflow must keep draining until that budget is consumed or no retry
// remains; otherwise a retry created by the first final-repair drain is
// incorrectly left for a manual resume.
const MAX_FINAL_RETRY_DRAINS = 3;
// Review findings can receive one targeted implementation repair. Batch UTest
// failures are durable non-blocking evidence: record them and continue to the
// Merge Train so independent Batch work is never interrupted.
const SINGLE_REPAIRABLE_STAGES = new Set(["review"]);
const BATCH_RESULT_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    status: { enum: ["success", "failed", "timeout"] },
    tasksCompleted: { type: "number" },
    tasksTotal: { type: "number" },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    commitSha: { type: "string" },
    errorMessage: { type: "string" }
  },
  required: ["batchId", "status", "worktreePath", "branchName", "commitSha"],
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
    batchWorkspaces: {
      type: "object",
      additionalProperties: {
        type: "object",
        properties: {
          workspaceRef: { type: "string" },
          componentRoots: { type: "array", items: { type: "string" } },
          executionStage: { type: "string" },
          requestedPath: { type: ["string", "null"] },
          worktreePath: { type: ["string", "null"] },
          branchName: { type: ["string", "null"] }
        },
        required: ["workspaceRef", "componentRoots", "executionStage", "requestedPath", "worktreePath", "branchName"],
        additionalProperties: true
      }
    }
  },
  required: ["runId", "status", "scheduledGroups", "batchTaskIds", "batchWorkspaces"],
  additionalProperties: true
};
// Review can return either `parallel_batch_stage.py complete` evidence or a
// `fail` transition. Keep that response structured so a failed Review cannot
// lose the durable finding while passing through the agent boundary.
const REVIEW_STAGE_RESULT_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    stage: { type: "string" },
    status: { enum: ["passed", "failed", "needs_triage"] },
    nextStage: { type: "string" },
    failureType: { type: "string" },
    failure: { type: "object" },
    evidenceId: { type: "string" },
    evidence: { type: "object" }
  },
  required: ["status"],
  additionalProperties: true
};
// A reviewer may produce prose or lose its final tool output after it has
// already changed the durable stage state.  The verifier always reads the
// plugin-owned stage record and normalizes that state before the Workflow
// decides whether to repair, continue, or ask the reviewer to finish it.
const REVIEW_VALIDATION_SCHEMA = {
  type: "object",
  properties: {
    success: { type: "boolean" },
    batchId: { type: "string" },
    stage: { const: "review" },
    status: { enum: ["passed", "failed", "needs_triage", "incomplete"] },
    nextStage: { type: "string" },
    failure: { type: "object" },
    evidenceId: { type: "string" },
    durableState: { type: "string" },
    error: { type: "string" }
  },
  required: ["success", "batchId", "stage", "status"],
  additionalProperties: true
};
const SINGLE_REPAIR_RESULT_SCHEMA = {
  type: "object",
  properties: {
    ok: { const: true },
    success: { const: true },
    batchId: { type: "string" },
    failedStage: { type: "string" },
    status: { const: "success" },
    stage: { type: "string" },
    stageEvidenceId: { type: "string" },
    stages: { type: "array", items: { type: "object" } }
  },
  required: ["ok", "success", "batchId", "failedStage", "status", "stage", "stages"],
  additionalProperties: false
};
const VERIFICATION_SCHEMA = {
  type: "object",
  properties: {
    passed: { type: "boolean" },
    commands: { type: "array", items: { type: "object" } },
    errorMessage: { type: "string" },
    errors: { type: "array", items: { type: "string" } },
    hasDeferredIssues: { type: "boolean" },
    hasBlockingDeferredIssues: { type: "boolean" },
    deferredIssues: { type: "array", items: { type: "object" } }
  },
  required: ["passed"],
  additionalProperties: true
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
    status: { enum: ["success", "failed", "timeout", "deferred"] },
    testStatus: { enum: ["passed", "deferred"] },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    commitSha: { type: "string" },
    testEvidenceIds: { type: "array", items: { type: "string" } },
    stageEvidenceId: { type: "string" },
    failureType: { type: "string" },
    nextStage: { type: "string" },
    failure: { type: "object" },
    testFailure: { type: "object" },
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

function isFinalReviewDecision(value, batchId) {
  const result = unwrap(value);
  return Boolean(
    result
    && result.batchId === batchId
    && result.stage === "review"
    && ["passed", "failed", "needs_triage"].includes(result.status)
  );
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
  // The Workflow keeps this mapping as shared state across parallel Batch
  // chains. A scheduler child must return the command's complete stdout, not
  // a hand-written summary that drops workspaceRef and poisons later work.
  const referencedBatchIds = new Set([
    ...Object.keys(result.batchTaskIds),
    ...normalizeScheduledGroups(result.scheduledGroups).flat(),
    ...(Array.isArray(result.stageRecoveryBatches) ? result.stageRecoveryBatches.map(item => item && item.batchId) : []),
    ...(Array.isArray(result.mergeableBatches) ? result.mergeableBatches : []),
  ].filter(usableString));
  const incompleteWorkspaces = [...referencedBatchIds].filter(batchId => {
    const workspace = result.batchWorkspaces[batchId];
    return !isObject(workspace) || !usableString(workspace.workspaceRef);
  });
  if (incompleteWorkspaces.length) {
    throw new Error(JSON.stringify({
      error: "parallel_scheduler_workspace_mapping_incomplete",
      label,
      batchIds: incompleteWorkspaces,
      runId: result.runId,
      status: result.status,
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
const taskCardId = input.taskCardId;
const resumeMode = input.resumeMode === "manual" ? "manual" : "automatic";
const resumeRunId = input.resumeRunId;
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
  || !codeWorkspaces
  || typeof codeWorkspaces !== "object"
  || Object.keys(codeWorkspaces).length === 0
) {
  throw new Error("missing_feature_plugin_path_artifact_workspace_or_code_workspaces");
}
if (Object.values(codeWorkspaces).some(path => !absolutePath(path))) {
  throw new Error("invalid_code_workspace_path");
}
if (!usableString(taskCardId) || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(taskCardId.trim())) {
  throw new Error("invalid_task_card_id");
}
// The workflow host can be an artifact directory. Native worktrees are
// provisioned by the plugin from the repository paths in codeWorkspaces.
// Keep workflowHostGitRoot as optional metadata for older callers only.
if (usableString(workflowHostGitRoot) && !absolutePath(workflowHostGitRoot)) {
  throw new Error("invalid_workflow_repository_root");
}
const schedulerPath = joinPath(pluginPath, "hooks/parallel_batch_scheduler.py");
const leasePath = joinPath(pluginPath, "hooks/batch_lease_manager.py");
const taskRunnerPath = joinPath(pluginPath, "hooks/task_runner.py");
const routeResolverPath = joinPath(pluginPath, "hooks/resolve_frontend_html_route.py");
const worktreeManagerPath = joinPath(pluginPath, "hooks/worktree_manager.py");
const lifecyclePath = joinPath(pluginPath, "hooks/parallel_batch_lifecycle.py");
const stagePath = joinPath(pluginPath, "hooks/parallel_batch_stage.py");
const utestRouterPath = joinPath(pluginPath, "hooks/utest_assignment_router.py");
const utestCommandPath = joinPath(pluginPath, "hooks/run_utest_command.py");
const utestEnvironmentPath = joinPath(pluginPath, "hooks/inspect_test_environment.py");
const utestSourceBugPath = joinPath(pluginPath, "hooks/validate_utest_source_bug.py");
const mergeTrainPath = joinPath(pluginPath, "hooks/parallel_merge_train.py");
// Every child below runs after the caller has supplied the task-card ID and
// started this fixed Workflow. Keep the no-interaction contract at the call
// boundary so adding a later phase cannot accidentally reintroduce a user
// confirmation prompt.
const WORKFLOW_AUTONOMY_PREFIX = "固定 Code Workflow 已启动：不得调用 request_user_input、要求用户确认、等待用户回复或把控制权交回用户。按本提示和持久化契约自主执行；只返回本步骤的最终结构化结果。\n";
function emptyAgentResponse(value) {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim().length === 0;
  return Boolean(
    value
    && typeof value === "object"
    && typeof value.value === "string"
    && value.value.trim().length === 0
  );
}

function emptyAgentResponseFailure(error) {
  const message = error instanceof Error ? error.message : String(error || "");
  return /(?:received\s+)?empty\s+response\s+from\s+(?:chat\s+)?model(?:\s+call)?|workflow_agent_empty_response/i.test(message);
}

async function workflowAgent(instruction, options = {}) {
  const label = usableString(options.label) ? options.label : "workflow-agent";
  let lastEmptyResponse;
  for (let attempt = 0; attempt <= MAX_EMPTY_AGENT_RESPONSE_RETRIES; attempt += 1) {
    const retryOptions = attempt === 0
      ? options
      : { ...options, label: `${label}-empty-retry-${attempt}` };
    try {
      const response = await agent(WORKFLOW_AUTONOMY_PREFIX + instruction, retryOptions);
      if (!emptyAgentResponse(response)) return response;
      lastEmptyResponse = new Error(`workflow_agent_empty_response:${label}:attempt=${attempt + 1}`);
    } catch (error) {
      if (!emptyAgentResponseFailure(error)) throw error;
      lastEmptyResponse = error;
    }
  }
  throw new Error(
    `workflow_agent_empty_response_exhausted:${label}:${errorText(lastEmptyResponse)}`
  );
}
const aggregatePath = joinPath(pluginPath, "hooks/parallel_evidence_aggregate.py");
const codeWorkspaceArgs = Object.entries(codeWorkspaces)
  .map(([workspaceRef, path]) => `--code-workspace "${workspaceRef}=${path}"`)
  .join(" ");
// Tests are intentionally owned by the later UTest stage.  This boundary is
// injected into both initial implementation and implementation rework prompts;
// task_runner.py independently rejects violating file changes at completion.
const CODE_STAGE_TEST_BOUNDARY = "Code 阶段和修复阶段只允许生产源码、生产配置、迁移和公开接口的最小实现。可以只读既有测试理解契约，但在 task_runner.py finish-implementation 成功前，禁止创建、修改或删除测试源码（包括 src/test、test、tests、__tests__）、fixture/mock、测试环境或测试配置；禁止执行任何测试命令（包括 mvn test、Gradle test、npm test、pnpm test、yarn test、jest、vitest、pytest）及 TASK validation commands。所有测试资产和测试命令仅可在 Review 通过后的 UTest 阶段执行。\n";

function taskWorkspacePath(batchId, batchWorktree, componentRoots) {
  if (!usableString(batchWorktree)) {
    throw new Error(`batch_worktree_missing:${batchId}`);
  }
  const roots = Array.isArray(componentRoots)
    ? [...new Set(componentRoots.filter(usableString).map(root => root.trim()))]
    : [];
  if (roots.length !== 1) {
    throw new Error(`batch_component_root_invalid:${batchId}:${JSON.stringify(componentRoots)}`);
  }
  const root = roots[0].replace(/\\/g, "/").replace(/^\.\//, "").replace(/\/+$/, "") || ".";
  if (root === ".") return batchWorktree;
  if (
    absolutePath(root)
    || root.split("/").some(part => !part || part === "." || part === "..")
  ) {
    throw new Error(`batch_component_root_invalid:${batchId}:${roots[0]}`);
  }
  return joinPath(batchWorktree, root);
}

function batchTaskWorkspace(batchId, batchWorktree) {
  const batchWorkspace = batchWorkspaces[batchId] || {};
  return taskWorkspacePath(batchId, batchWorktree, batchWorkspace.componentRoots);
}

function normalizeScheduledGroups(groups) {
  return (Array.isArray(groups) ? groups : [])
    .map(group => (Array.isArray(group) ? group.filter(usableString) : []))
    .filter(group => group.length > 0);
}

function retryPendingBatchesOf(scheduler) {
  const retryPending = scheduler && Array.isArray(scheduler.retryPendingBatches)
    ? scheduler.retryPendingBatches
    : [];
  return retryPending.filter(usableString);
}

phase("准备");
let prepared;
try {
  const prepareCommand = resumeMode === "manual"
    ? `python "${schedulerPath}" manual-resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${resumeRunId}"`
    : `python "${schedulerPath}" ensure ` +
      `--workspace "${artifactWorkspace}" --feature "${feature}" ` +
      `--task-card-id "${taskCardId.trim()}" ` +
      `--max-parallel ${maxParallel} ` +
      `--timeout-seconds ${timeoutPerBatch} --allow-bootstrap ${codeWorkspaceArgs}`;
  if (resumeMode === "manual" && !usableString(resumeRunId)) {
    throw new Error("manual_resume_run_id_missing");
  }
  prepared = requireSchedulerResult(await workflowAgent(
    `确保固定 Code DAG run。执行：${prepareCommand}。` +
    (resumeMode === "manual"
      ? `这是用户请求的人工恢复：只重排 scheduler 已识别的 retryable Batch，保留既有 Worktree、证据和 runId；不得恢复 needs_resolution、冲突或取消的 Batch。`
      : `已有可恢复 run 时必须返回其原 runId，不得创建第二个 run。`) +
    `必要时允许 scheduler 创建 autodev baseline 提交；不得修改业务文件内容，` +
    `且不得把 .cmbdevclaw 平台运行文件纳入提交。必须原样返回该命令 stdout 的完整 JSON；不得读取 manifest 后自行概括、重建或省略 batchWorkspaces 的 workspaceRef。`,
    { label: "fixed-workflow-prepare", phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
  ), "scheduler ensure");
} catch (error) {
  // There is no run identifier to recover when the control plane cannot be
  // initialized.  Return an observable terminal result instead of exposing a
  // raw Agent exception to the caller.
  return {
    ok: false,
    feature,
    runId: null,
    finalStatus: "scheduler_prepare_failed",
    e2eSkippedReason: "scheduler_run_not_initialized",
    unresolved: {
      batches: [],
      mergeCandidates: [],
      deferredIssues: [],
      schedulerFailures: [{ phase: "prepare", error: String(error) }],
    },
    nextAction: "retry_scheduler_prepare",
  };
}

const runId = prepared.runId;

async function recoverPendingRetries(scheduler, label) {
  if (!retryPendingBatchesOf(scheduler).length) return scheduler;
  // A normal ensure/resume already performs this transition.  One immediate,
  // explicit retry makes the Workflow resilient to an interrupted lease
  // handoff while still letting the scheduler remain the sole state owner.
  return requireSchedulerResult(await workflowAgent(
    `执行 python "${schedulerPath}" resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
    `恢复所有 retry_pending Batch，并在返回前清理其残留 lease；必须原样返回该命令 stdout 的完整 JSON，不得自行概括或重建 batchWorkspaces。`,
    { label, phase: "准备", schema: SCHEDULER_RESULT_SCHEMA }
  ), label);
}

let initialRetryRecoveryFailure = null;
try {
  prepared = await recoverPendingRetries(prepared, "recover-pending-retries-after-ensure");
} catch (error) {
  // An interrupted control-plane model call must not prevent the already
  // schedulable, independent portion of this durable run from draining. The
  // final repair boundary will make one more recovery attempt and report the
  // original failure if it remains unavailable.
  initialRetryRecoveryFailure = error;
}
let scheduledGroups = normalizeScheduledGroups(prepared.scheduledGroups || []);
let mergeableBatches = (prepared.mergeableBatches || []).filter(usableString);
let stageRecoveryBatches = (prepared.stageRecoveryBatches || []).filter(result => result && usableString(result.batchId));
let retryPendingBatches = retryPendingBatchesOf(prepared);
let batchTaskIds = prepared.batchTaskIds || {};
let batchWorkspaces = prepared.batchWorkspaces || {};
const batchResults = [];
const mergeResults = [];
const cleanupResults = [];
const lifecycleResults = [];
const unresolvedRecords = [];
const unresolvedRecordKeys = new Set();
const quarantinedBatchIds = new Set();
const schedulerFailures = [];
const finalRepairResults = [];
let schedulerCycles = 0;
let mergeSequence = 0;
let blockedBatches = (prepared.blockedBatches || []).filter(usableString);
let lastScheduler = prepared;
// Keep only the scheduler's last proven-safe pending waves.  This is used
// solely when a later *read-only* scheduler child exhausts empty-response
// retries.  It lets unrelated work continue without inventing a new DAG or
// bypassing the scheduler's conservative write-set grouping.
let schedulerSnapshotDegraded = false;
let schedulerFallbackGroups = [];
const schedulerFallbackConsumed = new Set();

function errorText(value) {
  if (value instanceof Error) return value.message || String(value);
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function isValidBatchId(batchId) {
  return usableString(batchId);
}

function activeUnresolvedRecords() {
  return unresolvedRecords.filter(record => record && record.resolved !== true);
}

function recordUnresolved(record) {
  const normalized = {
    kind: "batch",
    status: "unresolved",
    durable: false,
    ...record,
  };
  if (normalized.error !== undefined) normalized.error = errorText(normalized.error);
  const key = [
    normalized.kind,
    normalized.batchId || "",
    normalized.repositoryRef || "",
    normalized.wave || "",
    normalized.status || "",
    normalized.error || "",
  ].join("|");
  if (!unresolvedRecordKeys.has(key)) {
    unresolvedRecordKeys.add(key);
    unresolvedRecords.push(normalized);
  }
  if (
    isValidBatchId(normalized.batchId)
    && !["cleanup", "deferred_issue", "validation", "scheduler"].includes(normalized.kind)
  ) {
    quarantinedBatchIds.add(normalized.batchId);
  }
  return normalized;
}

function markBatchResolved(batchId) {
  if (!usableString(batchId)) return;
  quarantinedBatchIds.delete(batchId);
  for (const record of unresolvedRecords) {
    if (record && record.batchId === batchId && record.kind !== "cleanup") {
      record.resolved = true;
      record.resolvedAt = "workflow_recovered";
    }
  }
}

function markCandidateResolved(repositoryRef, wave) {
  for (const record of unresolvedRecords) {
    if (
      record
      && record.kind === "merge_candidate"
      && record.repositoryRef === repositoryRef
      && Number(record.wave) === Number(wave)
    ) {
      record.resolved = true;
      record.resolvedAt = "workflow_recovered";
    }
  }
}

function observeLifecycleResult(batchId, source, value) {
  const result = unwrap(value);
  const status = result && typeof result === "object" ? result.status : undefined;
  const observation = {
    batchId,
    source,
    status: usableString(status) ? status : "unknown",
    durable: result && result.durable === true,
    error: result && (result.errorMessage || result.reason || result.cleanupError || result.error),
  };
  lifecycleResults.push(observation);
  if (status === "merged") {
    markBatchResolved(batchId);
    return result;
  }
  if (
    !result
    || result.unparsedStructuredOutput === true
    || ["retry_pending", "failed", "timeout", "blocked", "needs_resolution"].includes(status)
    || hasFailureSignal(result)
  ) {
    recordUnresolved({
      kind: "batch",
      batchId,
      status: observation.status,
      durable: observation.durable,
      error: observation.error || result,
      source,
      worktreePath: result && result.worktreePath,
      branchName: result && result.branchName,
    });
  }
  return result;
}

function recordSchedulerFailure(phaseName, error) {
  const failure = { phase: phaseName, error: errorText(error) };
  schedulerFailures.push(failure);
  recordUnresolved({ kind: "scheduler", status: "unavailable", durable: false, ...failure });
  return failure;
}

if (initialRetryRecoveryFailure) {
  recordSchedulerFailure("recover-pending-retries-after-ensure", initialRetryRecoveryFailure);
}

function markSchedulerRecovered() {
  schedulerSnapshotDegraded = false;
  schedulerFallbackConsumed.clear();
  for (const record of unresolvedRecords) {
    if (record && record.kind === "scheduler" && record.resolved !== true) {
      record.resolved = true;
      record.resolvedAt = "scheduler_snapshot_recovered";
    }
  }
}

function nextSchedulerFallbackWave(groups, consumed, quarantined) {
  for (const group of normalizeScheduledGroups(groups)) {
    const remaining = group.filter(batchId => (
      isValidBatchId(batchId)
      && !consumed.has(batchId)
      && !quarantined.has(batchId)
    ));
    if (remaining.length) return remaining;
  }
  return [];
}

function boundedSchedulerFallbackGroups(groups, capacity) {
  const width = Number.isInteger(capacity) && capacity > 0 ? capacity : 1;
  return normalizeScheduledGroups(groups).flatMap(group => {
    const waves = [];
    for (let index = 0; index < group.length; index += width) {
      waves.push(group.slice(index, index + width));
    }
    return waves;
  });
}

function cacheSchedulerFallbackGroups(scheduler) {
  const ready = new Set([
    ...(Array.isArray(scheduler && scheduler.readyBatches) ? scheduler.readyBatches : []),
    ...(Array.isArray(scheduler && scheduler.allReadyBatches) ? scheduler.allReadyBatches : []),
  ].filter(isValidBatchId));
  const rawGroups = Array.isArray(scheduler && scheduler.parallelGroups)
    ? scheduler.parallelGroups
    : scheduler && scheduler.allParallelGroups;
  const groups = normalizeScheduledGroups(rawGroups)
    .map(group => group.filter(batchId => ready.has(batchId)))
    .filter(group => group.length);
  if (!groups.length) return;
  // `parallelGroups` expresses conflict-safe compatibility, while the actual
  // dispatch also observes maxParallel. Preserve both constraints before this
  // snapshot can be used as a degraded-mode queue.
  const capacity = Number.isInteger(scheduler && scheduler.maxParallel) && scheduler.maxParallel > 0
    ? scheduler.maxParallel
    : maxParallel;
  schedulerFallbackGroups = boundedSchedulerFallbackGroups(groups, capacity);
  schedulerFallbackConsumed.clear();
}

function schedulerSnapshotFallback(label, error) {
  schedulerSnapshotDegraded = true;
  return {
    ...(lastScheduler && typeof lastScheduler === "object" ? lastScheduler : {}),
    // `scheduledGroups` is a point-in-time dispatch grant. Replaying it after
    // a read failure could re-run a just-finished Batch, so expose only the
    // cached safe waves through `runnableSchedulerFallbackBatchIds` below.
    scheduledGroups: [],
    schedulerSnapshotFallback: true,
    schedulerSnapshotFailure: {
      label,
      error: errorText(error),
    },
  };
}

function applySchedulerState(scheduler) {
  if (!scheduler || typeof scheduler !== "object") return;
  lastScheduler = scheduler;
  cacheSchedulerFallbackGroups(scheduler);
  scheduledGroups = normalizeScheduledGroups(scheduler.scheduledGroups || []);
  mergeableBatches = (scheduler.mergeableBatches || []).filter(isValidBatchId);
  stageRecoveryBatches = (scheduler.stageRecoveryBatches || []).filter(result => result && isValidBatchId(result.batchId));
  retryPendingBatches = retryPendingBatchesOf(scheduler);
  // Bindings are immutable within a run. Merge a validated snapshot so a
  // scoped future response cannot erase coordinates used by an in-flight
  // Review repair or a newly-unblocked Batch.
  batchTaskIds = { ...batchTaskIds, ...(scheduler.batchTaskIds || {}) };
  batchWorkspaces = { ...batchWorkspaces, ...(scheduler.batchWorkspaces || {}) };
  blockedBatches = (scheduler.blockedBatches || []).filter(isValidBatchId);
  for (const batchId of retryPendingBatches) {
    recordUnresolved({ kind: "batch", batchId, status: "retry_pending", durable: true, source: "scheduler" });
  }
  for (const batchId of blockedBatches) {
    recordUnresolved({ kind: "batch", batchId, status: "blocked", durable: true, source: "scheduler" });
  }
}

async function readSchedulerState(label, phaseName = "准备") {
  try {
    const state = requireSchedulerResult(await workflowAgent(
      `执行 python "${schedulerPath}" status --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
      `这是只读调度快照；不得恢复 retry_pending、修改业务代码、创建 Worktree 或运行 TASK。必须只原样返回该命令 stdout 的完整 JSON。不得读取 manifest 后手工汇总、推断 scheduledGroups，或重建/省略 batchWorkspaces 的 workspaceRef。`,
      { label, phase: phaseName, schema: SCHEDULER_RESULT_SCHEMA }
    ), label);
    applySchedulerState(state);
    markSchedulerRecovered();
    return state;
  } catch (error) {
    recordSchedulerFailure(label, error);
    // A blank model completion did not produce a scheduler verdict.  Continue
    // only from a previously returned scheduler grouping; malformed or real
    // command failures remain a stop condition so the workflow cannot guess
    // about leases, dependencies, or conflicts.
    if (emptyAgentResponseFailure(error)) return schedulerSnapshotFallback(label, error);
    return null;
  }
}

async function deferBatchForRetry(batchId, batchWorktree, batchBranch, reason) {
  const locationArgs = usableString(batchWorktree) && usableString(batchBranch)
    ? ` --worktree-path "${batchWorktree}" --branch-name "${batchBranch}"`
    : "";
  const finalized = requireSuccess(await workflowAgent(
    `将失败的 Batch ${batchId} 标记为可恢复重试，不得打断其他无依赖 Batch。原因：${JSON.stringify(reason)}。` +
    `先执行 python "${leasePath}" reclaim --workspace "${artifactWorkspace}" --feature "${feature}" ` +
    `--run-id "${runId}" --batch-id "${batchId}" --force；再执行 python "${schedulerPath}" mark-batch ` +
    `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status retry_pending ` +
    `--error ${JSON.stringify(String(reason || "batch_execution_failed"))}${locationArgs}。` +
    `只清理租约和更新调度状态，保留插件原生 worktree、草稿与阶段证据供自动恢复；不要创建 workflow、修改业务代码、包装 Git 或继续执行 TASK。只返回 JSON。`,
    { label: `defer-batch-retry-${batchId}`, phase: "Batch 阶段" }
  ), `defer batch retry ${batchId}`);
  return {
    batchId,
    status: "retry_pending",
    reason: String(reason || "batch_execution_failed"),
    finalized,
    // The scheduler mark command completed successfully.  A later status
    // snapshot still verifies this claim before the final report is emitted.
    durable: true,
  };
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

function promotionWrapperBatchIds(promotion) {
  // Some fixed-workflow runtimes preserve `promote-candidate`'s success at
  // the outer level but wrap its per-repository result as
  // `{ promotions: [{ repositoryRef, ids }] }`.  Those `ids` are trustworthy
  // only with an explicit successful outer promotion; never read them from a
  // failed/ambiguous wrapper or a ready-to-candidate input.
  if (
    !promotion
    || promotion.success !== true
    || hasFailureSignal(promotion)
    || !Array.isArray(promotion.promotions)
  ) return [];
  return [...new Set(promotion.promotions.flatMap(item => {
    const direct = mergedBatchIds(item);
    if (direct.length > 0) return direct;
    return Array.isArray(item && item.ids)
      ? item.ids.filter(batchId => usableString(batchId))
      : [];
  }))];
}

function canonicalPromotionResult(rawPromotion, repositoryRef, candidateBatchIds) {
  const promotion = unwrap(rawPromotion);
  const reportedBatchIds = [...new Set([
    ...mergedBatchIds(promotion),
    ...promotionWrapperBatchIds(promotion),
  ])];
  const expectedBatchIds = [...new Set((Array.isArray(candidateBatchIds) ? candidateBatchIds : [])
    .filter(batchId => usableString(batchId)))];
  // `promote-candidate` has already completed successfully at this point.  A
  // few older plugin/runtime copies returned only `{ success: true }` after
  // the fast-forward and lost `batchIds` while serializing the response.  The
  // candidate was built from this exact, immutable batch set, so use it solely
  // as attribution for that successful promotion.  Do not apply this fallback
  // to a failed or ambiguous command result.
  const inferredBatchIds = reportedBatchIds.length === 0
    && promotion
    && promotion.success === true
    && !hasFailureSignal(promotion)
    ? expectedBatchIds
    : [];
  return {
    ...promotion,
    repositoryRef,
    promoted: (promotion && promotion.promoted === true) || reportedBatchIds.length > 0 || inferredBatchIds.length > 0,
    batchIds: reportedBatchIds.length > 0 ? reportedBatchIds : inferredBatchIds,
    promotionResultIncomplete: inferredBatchIds.length > 0,
  };
}

async function cleanupMergedWorktrees(batchIds, label) {
  const expected = [...new Set((Array.isArray(batchIds) ? batchIds : [])
    .filter(batchId => usableString(batchId)))];
  const batchArgs = expected.map(batchId => `--batch-id "${batchId}"`).join(" ");
  try {
    const cleanup = requireSuccess(await workflowAgent(
      `清理已交付 Batch 的插件原生 Worktree。执行 python "${lifecyclePath}" cleanup-merged ` +
      `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" ${batchArgs}。` +
      `只允许清理 manifest 中 status=merged 的 Batch：释放残留 lease、删除该 Worktree 与临时分支并更新 manifest；` +
      `不得清理 failed、blocked 或 needs_resolution 的 Worktree。只返回 JSON。`,
      { label, phase: "合并", schema: MERGED_CLEANUP_SCHEMA }
    ), label);
    const cleaned = new Set(Array.isArray(cleanup.cleanedBatchIds) ? cleanup.cleanedBatchIds : []);
    const missing = expected.filter(batchId => !cleaned.has(batchId));
    if (missing.length > 0) {
      const incomplete = {
        success: false,
        cleanedBatchIds: Array.from(cleaned),
        errors: [`merged_worktree_cleanup_incomplete:${missing.join(",")}`],
      };
      cleanupResults.push(incomplete);
      for (const batchId of missing) {
        recordUnresolved({
          kind: "cleanup",
          batchId,
          status: "cleanup_pending",
          durable: false,
          error: incomplete.errors[0],
        });
      }
      return incomplete;
    }
    cleanupResults.push(cleanup);
    return cleanup;
  } catch (error) {
    const failure = {
      success: false,
      cleanedBatchIds: [],
      errors: [errorText(error)],
    };
    cleanupResults.push(failure);
    for (const batchId of expected) {
      recordUnresolved({
        kind: "cleanup",
        batchId,
        status: "cleanup_pending",
        durable: false,
        error,
      });
    }
    return failure;
  }
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
  const failureMessage = implementationFailureMessage(normalized, failure);
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

function implementationFailureMessage(normalized, failure) {
  const direct = failure.message || normalized.message || normalized.error || "";
  if (usableString(direct)) return direct;

  // Some reviewers return the finding as structured review fields rather than
  // the stage command's `failure.message`. Preserve every available detail as
  // the repair brief; never invent a generic message for an empty finding.
  const labels = {
    file: "file",
    lines: "lines",
    expected: "expected",
    actual: "actual",
    impact: "impact",
    suggestedFix: "suggestedFix",
  };
  const details = Object.entries(labels)
    .map(([field, label]) => [label, failure[field]])
    .filter(([, value]) => usableString(value))
    .map(([label, value]) => `${label}: ${value}`);
  return details.length ? details.join("\\n") : "";
}

async function recoverImplementationRework(batchResult, failedStage, result) {
  try {
    return implementationReworkRequired(batchResult, failedStage, result);
  } catch (error) {
    const expected = `implementation_rework_failure_message_missing:${batchResult.batchId}:${failedStage}`;
    if (!error || error.message !== expected) throw error;

    // The stage command persists its failure before the agent returns. If an
    // agent response omitted that field, recover the exact, durable finding
    // rather than marking the Batch retry_pending or asking Review again.
    const scheduler = await readSchedulerState(
      `recover-${failedStage}-finding-${batchResult.batchId}`,
      "Batch 阶段"
    );
    const recovery = (scheduler && Array.isArray(scheduler.stageRecoveryBatches)
      ? scheduler.stageRecoveryBatches
      : []
    ).find(item => item && item.batchId === batchResult.batchId);
    const context = recovery && recovery.failureContext;
    if (
      !context
      || context.failedStage !== failedStage
      || !usableString(context.message)
    ) {
      throw error;
    }
    const normalized = unwrap(result);
    const failure = unwrap(normalized.failure);
    return implementationReworkRequired(batchResult, failedStage, {
      ...normalized,
      status: normalized.status || "failed",
      failureType: context.failureType || normalized.failureType || "implementation",
      nextStage: "implement",
      failure: {
        ...failure,
        type: context.failureType || failure.type || "implementation",
        message: context.message,
        nextStage: "implement",
      },
    });
  }
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
    "严格执行：1) cd 到该 Worktree，确认 git 顶层与分支匹配；2) 执行 python \"" + leasePath + "\" acquire --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --ttl-seconds " + timeoutPerBatch + " --lease-guard，保存 lease.ownerToken。插件在每个携带 token 的 task_runner/worktree_manager 命令边界续租；禁止自行运行 heartbeat、run_in_background、&、nohup 或 Start-Process；3) 执行 python \"" + stagePath + "\" start --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test；4) 执行 python \"" + utestRouterPath + "\" --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --json，且只使用其中 batchId=\"" + batchId + "\"、workspaceRef=\"" + batchWorkspaceRef + "\" 的 assignment 原文；5) 执行 python \"" + utestEnvironmentPath + "\" --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" " + taskIdArgs + " --batch-worktree \"" + batchWorktree + "\" --json。环境非 ready 时只按 UTest 协议修测试环境并重新检查；仍无法解决时按步骤 7 以 environment 记录失败并继续。\n" +
    "6) 对每个实际 TASK 生成或补齐行为测试：覆盖 implementationPoints、testPoints 与全部 AC，排除 nonGoals；使用真实工程 runner。每个测试文件落地后，必须执行 python \"" + utestCommandPath + "\" --kind test --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --task-id <真实TASK_ID> --batch-worktree \"" + batchWorktree + "\" --test-file <仓库根相对测试文件> -- <真实精确测试 argv>。Plan V2 不提供测试 argv；UTest 必须根据实际测试资产和 runner 生成命令。测试自身、fixture、mock、测试配置的问题必须在本阶段修复并重跑。\n" +
    "7) 若任一 UTest 最终仍失败，必须保留本次真实 runner 输出与 run_utest_command Evidence；source_bug 仍可用 validate_utest_source_bug 做分类，但不得在此阶段修复生产代码，也不得执行 stage fail。每次 seal 前都先执行携带 --owner-token <真实token> --require-lease-guard 的 lease check。先用 python \"" + worktreeManagerPath + "\" --json seal --artifact-workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --repo \"" + batchWorktree + "\" --owner-token <真实token> 封存新增测试资产；随后执行 python \"" + stagePath + "\" record-test-failure --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --failure-type <implementation|test_definition|documentation|environment|needs_triage> --message \"<必须包含 targetId、commandId、evidenceId、test-output.log 路径、失败断言的 expected/actual 或 stdout/stderr 根因>\" --metadata-json '<包含 batchCommit、新 commitSha、testEvidenceIds、worktreePath、branchName 的对象>'，其中 batchCommit 必须等于刚 seal 返回的新 commitSha。最后以 final-status sealed 释放 lease。该命令会把失败记录成非阻断 issue，Workflow 继续后续流程。\n" +
    "8) 全部 UTest 通过后，执行 python \"" + worktreeManagerPath + "\" --json seal --artifact-workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --repo \"" + batchWorktree + "\" --owner-token <真实token> 取得新的 commitSha；再执行 python \"" + stagePath + "\" complete --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test --metadata-json <包含 batchCommit、新 commitSha、testEvidenceIds、worktreePath、branchName 的对象>；最后以 final-status sealed 释放 lease。\n" +
    "成功只返回 {batchId,status:\"success\",testStatus:\"passed\",worktreePath,branchName,commitSha,testEvidenceIds,stageEvidenceId}。记录失败后也返回 status:\"success\"，但必须返回 testStatus:\"deferred\" 与 testFailure；不得返回 failed/timeout 以中断其他 Batch。只有无法写入失败 Evidence 或无法安全释放 lease 时才返回 failed；若能释放 lease，必须使用 final-status pending，禁止 final-status failed，由 Workflow 标记为 retry_pending。不得手工 git add/commit、merge、rebase 或删除 Worktree。";
  return unwrap(await workflowAgent(
    prompt,
    { label: "stage-utest-" + batchId, phase: "Batch 阶段", schema: UTEST_STAGE_SCHEMA }
  ));
}

async function runDeliveryReviewTestAndGate(batchResult, options = {}) {
  const reviewResolvedByRepair = options.reviewResolvedByRepair === true;
  const testResolvedByRepair = options.testResolvedByRepair === true;
  const batchId = batchResult.batchId;
  const batchWorktree = batchResult.worktreePath;
  const batchBranch = batchResult.branchName;
  const commitSha = batchResult.commitSha;
  const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
  if (!usableString(commitSha)) throw new Error(`sealed_batch_commit_missing:${batchId}`);
  const metadata = JSON.stringify({ batchCommit: commitSha, worktreePath: batchWorktree, branchName: batchBranch });
  if (!reviewResolvedByRepair && !testResolvedByRepair) {
    const stageResult = requireSuccess(await workflowAgent(
      `登记 Batch ${batchId} 已完成的准备与实现阶段。依次执行：` +
      `python "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage prepare；` +
      `python "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage prepare --metadata-json '${metadata}'；` +
      `python "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage implement；` +
      `python "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage implement --metadata-json '${metadata}'。只返回最后一个 JSON。`,
      { label: `stage-implement-${batchId}`, phase: "Batch 阶段" }
    ), `stage implement ${batchId}`);
    void stageResult;
  }
  if (!reviewResolvedByRepair) {
    let review;
    let reviewValidation;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const retryContext = attempt === 1
        ? ""
        : "上一次 Review 子 Agent 没有形成可验证的终态。仅修复 Review 阶段的命令执行/返回，先读取当前 durable stage 状态；不得修改业务代码。";
      const reviewRaw = unwrap(await workflowAgent(
        `对已草稿封存的 Batch ${batchId} 做只读评审。Review execution mode=fixed_code_workflow；代码只在原生 worktree "${batchWorktree}"，分支 "${batchBranch}"；TASK 范围仅为 ${JSON.stringify(taskIds)}。不得调用 request_user_input、要求用户确认或等待用户裁定；只返回可执行的阶段结果。${retryContext}` +
        `先执行 python "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review。` +
        `只评审业务生产代码、生产配置、迁移和公开接口的实现；测试源码、fixture/mock 和测试环境由紧随其后的 UTest 阶段创建。即使 scope.paths、expectedFiles 或 writeSet 中出现测试路径，也不得因 sealed commit 缺少测试文件而判定 Review 不通过；可评估可测试性，但不得要求测试资产已存在。评审实现、接口边界、错误处理和与 TASK 验收条件的一致性；禁止修改源码、提交、合并或删除 Worktree。` +
        `通过后执行 python "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review --metadata-json '${metadata}'。` +
        `发现问题时必须先执行 python "${stagePath}" fail --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review --failure-type <implementation|documentation|needs_triage> --message "<具体问题：file:line、期望与实际行为、影响及建议修复>"。` +
        `最后必须执行 python "${stagePath}" validate-review-result --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"，并且只原样返回它的 stdout JSON。不得信任或返回此前命令的原始文本；该脚本会验证并规范化 durable Review 结果。可由当前 Batch 生产代码修复时，Workflow 会在同一 Worktree 修复、跳过编译记录并封存一次，然后直接进入 UTest，不会再次执行 Review。` +
        `documentation 与 needs_triage 仍按原分类阻断，保留 Worktree。只返回 JSON。`,
        { label: `stage-review-${batchId}-attempt-${attempt}`, phase: "Batch 阶段", schema: REVIEW_STAGE_RESULT_SCHEMA }
      ));
      // The reviewer itself is prompted to validate its result. Re-run the
      // validator in a read-only child so a prose/raw-JSON response cannot
      // bypass the plugin-owned state machine.
      reviewValidation = unwrap(await workflowAgent(
        `只验证 Batch ${batchId} 已持久化的 Review 阶段，不评审代码、不修改任何文件、不运行 TASK。执行 python "${stagePath}" validate-review-result --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。只返回 JSON。`,
        { label: `validate-review-${batchId}-attempt-${attempt}`, phase: "Batch 阶段", schema: REVIEW_VALIDATION_SCHEMA }
      ));
      if (isFinalReviewDecision(reviewValidation, batchId)) {
        review = reviewValidation;
        break;
      }
      // Preserve the raw reviewer output only as diagnostic context. The next
      // attempt is driven entirely by the durable validator result.
      review = reviewRaw;
    }
    if (!isFinalReviewDecision(review, batchId)) {
      throw new Error(`review_stage_not_finalized:${batchId}:${JSON.stringify(reviewValidation || review)}`);
    }
    if (requiresImplementationRework(review)) {
      return recoverImplementationRework(batchResult, "review", review);
    }
    requireSuccess(review, `stage review ${batchId}`);
  }
  if (!testResolvedByRepair) {
    const test = requireSuccess(await runBatchUtestAndSeal(batchResult), `stage test ${batchId}`);
    const testedDelivery = withLatestBatchDelivery(batchResult, test);
    const testStatus = test.testStatus || (test.status === "deferred" ? "deferred" : "passed");
    if (!["passed", "deferred"].includes(testStatus)) {
      throw new Error(`stage_test_status_invalid:${batchId}:${String(testStatus)}`);
    }
    batchResult = testedDelivery;
  }
  requireSuccess(await workflowAgent(
    `Batch ${batchId} 的 Review 与 UTest 已收口。执行 python "${stagePath}" gate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。` +
    `只返回 gate JSON；只有 ready_to_candidate 才算成功。`,
    { label: `stage-gate-${batchId}`, phase: "Batch 阶段" }
  ), `stage gate ${batchId}`);
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
  const taskWorkspace = batchTaskWorkspace(batchId, batchWorktree);
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
  return requireSuccess(await workflowAgent(
    `恢复 Batch ${batchId} 的 implement 阶段；之前的 review/test 失败已使该阶段的旧 evidence 失效。只能在既有原生 worktree "${batchWorktree}"、分支 "${batchBranch}" 内操作。不得调用 request_user_input、要求用户确认或等待用户裁定；按既定契约自动完成最小兼容修复并继续。${repairBrief}` +
    CODE_STAGE_TEST_BOUNDARY +
    `依次执行：1) 用 batch_lease_manager.py acquire 获取真实 lease token（workspace="${artifactWorkspace}"、feature="${feature}"、run-id="${runId}"、batch-id="${batchId}"、--ttl-seconds ${timeoutPerBatch}、--lease-guard）。插件在每个携带 token 的 task_runner/worktree_manager 命令边界续租；禁止自行启动后台 heartbeat；随后 mark-batch 为 running；` +
    `2) 先清理陈旧 run，再开始任何修复：对 ${JSON.stringify(taskIds)} 中每个真实 TASK 依次执行 task_runner.py inspect（携带 workspace、feature、task-id、code-workspace）；只要发现 parallelRunId="${runId}" 且状态为 started、in_progress 或 implementation_recording 的旧 run，就先用其 inspect 返回的真实 runId 执行 task_runner.py abort --force-with-changes --abort-why "implementation_rework_stale_run"，并携带 --workspace-ref "${batchWorkspaceRef}"。abort 必须保留 Worktree 未提交改动；每次 abort 后再次 inspect 确认该 TASK 已无活动 run。必须先完成全部陈旧 run 清理，禁止一边清理一边 start 新 TASK；其他 parallelRunId 的活动 run、无法 inspect 的 run 或 abort 失败均不得猜测，立即以 final-status pending 释放 lease 并返回 failed。` +
    `3) 严格按依赖拓扑逐个修复，绝不并行或预启动：每次只处理一个 TASK，先 inspect 当前状态并确认其所有 deps 已为 implemented/done；所有 task_runner.py 调用必须使用 --lease-token <真实 token>、--code-workspace "${taskWorkspace}" 和 --workspace-ref "${batchWorkspaceRef}"。前置 TASK 尚未完成时必须继续完成前置 TASK，禁止尝试 start 当前 TASK。当前 TASK 为 implemented/done 时，读取其真实 latestImplementationEvidenceId，执行 task_runner.py start-task-repair --prior-evidence-id <真实 ID> --parallel-run-id "${runId}" --lease-token <真实 token> --code-workspace "${taskWorkspace}" --workspace-ref "${batchWorkspaceRef}"，记下真实 runId；当前 TASK 为 todo 时执行普通 task_runner.py start（同样携带 parallel-run-id、lease-token、code-workspace、workspace-ref），记下真实 runId。状态仍为 in_progress 时禁止再次 start；应回到步骤 2 处理其陈旧 run。其他状态不得臆造命令，返回 failed。完成当前 TASK 的生产修复后，必须立刻用该真实 runId 执行 finish-implementation：只有 start-task-repair 启动的任务携带 --repair-mode，普通 start 启动的任务禁止携带 --repair-mode。确认 finish 成功且 TASK 已为 implemented/done 后，才可处理其下一个依赖任务。` +
    `4) 用同一 token 执行 batch_lease_manager.py check --require-lease-guard，再用 worktree_manager.py seal 产生新的 commitSha，并以 final-status sealed 释放同一 lease。只有 lease 或 seal/release 失败才中断。` +
    `不得创建新分支/Worktree、不得合并、不得运行非本 Batch 的验证；其他命令失败保留 Worktree 并以 final-status pending 释放 lease，让 Workflow 标记为 retry_pending。返回 {batchId,status:"success",worktreePath,branchName,commitSha}。`,
    { label: `rework-implement-${batchId}`, phase: "Batch 阶段", schema: BATCH_RESULT_SCHEMA }
  ), `implementation rework ${batchId}`);
}

async function recordSingleRepairResolution(recovery, repaired) {
  const batchId = repaired.batchId;
  const failedStage = recovery && recovery.failureContext && recovery.failureContext.failedStage;
  if (!SINGLE_REPAIRABLE_STAGES.has(failedStage)) {
    throw new Error(`single_repair_stage_invalid:${batchId}:${String(failedStage)}`);
  }
  const metadata = JSON.stringify({
    batchCommit: repaired.commitSha,
    worktreePath: repaired.worktreePath,
    branchName: repaired.branchName,
    repairedFromStage: failedStage,
    repairDisposition: "single_repair_accepted",
  });
  return requireSuccess(await workflowAgent(
    `Batch ${batchId} 的 ${failedStage} 已按一次性修复策略完成生产代码修复和封存。` +
    `不得让模型串行拼接多个 stage start/complete，也不得根据原始文本判断是否收口。只执行 python "${stagePath}" record-single-repair --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --failed-stage "${failedStage}" --metadata-json '${metadata}'。` +
    `该插件命令会以单个、幂等的受控入口写入 prepare/implement/review evidence，并验证最终 Review evidence 的 single_repair_accepted 标记。只原样返回 JSON。`,
    { label: `record-single-repair-${failedStage}-${batchId}`, phase: "Batch 阶段", schema: SINGLE_REPAIR_RESULT_SCHEMA }
  ), `record single repair ${failedStage} ${batchId}`);
}

async function blockImplementationFinding(delivery, staged, disposition) {
  const batchId = delivery.batchId;
  const message = "unresolved_" + staged.failedStage + "_implementation_finding:" + disposition;
  return deferBatchForRetry(batchId, delivery.worktreePath, delivery.branchName, message);
}

async function runDeliveryWithImplementationRepair(batchResult) {
  let delivery = batchResult;
  const repairedStages = new Set();
  let options = {};
  for (;;) {
    const staged = await runDeliveryReviewTestAndGate(delivery, options);
    if (staged.status === "ready_to_candidate") return staged;
    if (staged.status !== "implementation_rework_required") {
      throw new Error(`unexpected_delivery_stage_result:${JSON.stringify(staged)}`);
    }
    if (!SINGLE_REPAIRABLE_STAGES.has(staged.failedStage) || repairedStages.has(staged.failedStage)) {
      await blockImplementationFinding(delivery, staged, "single_repair_already_used");
      return { batchId: delivery.batchId, status: "retry_pending", failedStage: staged.failedStage };
    }
    repairedStages.add(staged.failedStage);
    // Review/test -> one production repair -> actual compile/seal -> next
    // stage.  A repaired stage is recorded explicitly and never rerun in this
    // first-version policy.
    const priorCommitSha = delivery.commitSha;
    const repaired = await reworkDeliveryImplementation(staged.recovery);
    if (!usableString(repaired.commitSha) || repaired.commitSha === priorCommitSha) {
      await blockImplementationFinding(delivery, staged, "no_new_commit");
      return { batchId: delivery.batchId, status: "retry_pending", failedStage: staged.failedStage };
    }
    delivery = repaired;
    await recordSingleRepairResolution(staged.recovery, repaired);
    options = staged.failedStage === "review"
      ? { reviewResolvedByRepair: true }
      : { reviewResolvedByRepair: true, testResolvedByRepair: true };
  }
}

async function continueRecoveredDelivery(recovery) {
  if (recovery.nextStage !== "implement" || !(recovery && recovery.failureContext)) {
    return runDeliveryWithImplementationRepair(recovery);
  }
  const repaired = await reworkDeliveryImplementation(recovery);
  await recordSingleRepairResolution(recovery, repaired);
  const failedStage = recovery.failureContext.failedStage;
  const options = failedStage === "review"
    ? { reviewResolvedByRepair: true }
    : { reviewResolvedByRepair: true, testResolvedByRepair: true };
  return runDeliveryReviewTestAndGate(repaired, options);
}

async function validateAndPromoteBatch(batchId, candidateSequence) {
  const repositoryRef = (batchWorkspaces[batchId] || {}).workspaceRef;
  if (!usableString(repositoryRef)) throw new Error(`scheduler did not provide repository for ${batchId}`);
  const ids = [batchId];
  const batchArgs = `--batch-id "${batchId}"`;
  // `parallel_merge_train.py` uses --wave as a durable candidate-record key.
  // It is a monotonically increasing candidate sequence here, never a set of
  // Batch deliveries to merge together.
  const wave = candidateSequence;
    // A changed main invalidates the entire candidate.  Rebuild from the
    // current head and rebuild the candidate; never rebase a previously-gated
    // candidate, because that would sever the evidence-to-SHA relationship.
    let promotion;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const builtRaw = unwrap(await workflowAgent(
        `构建 Batch ${batchId} 的独立 Merge Train 候选（候选序号 ${wave}，第 ${attempt} 次）。执行 python "${mergeTrainPath}" build-candidate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave} ${batchArgs}。` +
        `这是可能超过 60 秒的 Git 候选构建：若当前 execute 支持 run_in_background，必须只启动一次 execute({command:<上述命令>,run_in_background:true})，保存 task_id 并在同一子 Agent 内反复 task_output（每次可 timeout:120000）直至获得最终退出结果；task_output 的等待超时不是构建失败，禁止重启该命令。若当前为托管前台会话，则只执行一次并等待其最终结果。子 Agent 在 task_output 得到终态前不得结束，否则平台会清理其后台进程。` +
        `候选创建失败时保留 delivery Worktree 并停止，禁止 rebase 或直接合并主分支。build-candidate 在同一 wave 中只能真正启动一次；只能轮询同一 task_id，不得因本地终端超时或中断再次执行。只返回最终命令 JSON。`,
        { label: `build-candidate-${repositoryRef}-${batchId}-${wave}-${attempt}`, phase: "候选验证" }
      ));

      let built = builtRaw;
      if (builtRaw && builtRaw.status === "candidate_conflicted") {
        // A candidate's conflict markers are tied to this exact base SHA.
        // Resolve and promote it now, before other same-repository deliveries
        // can advance main and make the retained conflict context stale.
        const resolved = unwrap(await workflowAgent(
          `立即修复刚产生的 Merge Train 冲突候选。执行 python "${mergeTrainPath}" resolve-candidate ` +
          `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave}。` +
          `只能处理该既有候选 Worktree；成功时返回 status="built"，随后本 Workflow 会立即推广。不得创建新候选、修改 main 或继续其他 Batch。只返回 JSON。`,
          { label: `resolve-conflicted-candidate-${repositoryRef}-${batchId}-${wave}`, phase: "候选验证" }
        ));
        if (!resolved || resolved.status !== "built" || resolved.success === false || hasFailureSignal(resolved)) {
          recordUnresolved({
            kind: "merge_candidate",
            repositoryRef,
            wave,
            batchIds: ids,
            batchId,
            status: "needs_resolution",
            durable: true,
            error: resolved || builtRaw,
            worktreePath: (resolved && resolved.worktreePath) || builtRaw.worktreePath || (builtRaw.conflictContext && builtRaw.conflictContext.candidateWorktree),
            conflictedFiles: (resolved && resolved.conflictedFiles) || builtRaw.conflictedFiles || (builtRaw.conflictContext && builtRaw.conflictContext.conflictedFiles),
          });
          throw new Error(`immediate_candidate_resolution_failed:${repositoryRef}:${wave}:${errorText(resolved || builtRaw)}`);
        }
        built = resolved;
      }

      // Now require success on the built result
      try {
        built = requireSuccess(built, `build candidate ${repositoryRef}`);
      } catch (error) {
        recordUnresolved({
          kind: "merge_candidate",
          repositoryRef,
          wave,
          batchIds: ids,
          batchId,
          status: "build_failed",
          durable: false,
          error,
        });
        throw error;
      }

      const rawPromotion = unwrap(await workflowAgent(
      `推广已完成业务 Review 且 UTest 已通过或已记录失败的候选 SHA ${built.candidateSha}。执行 python "${mergeTrainPath}" promote-candidate --allow-unverified --allow-stale --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave}。` +
        `Batch 的 UTest 失败会作为显式 issue 随最终结果保留，但不打断后续流程；合并后唯一的可执行验证是 B-E2E。若返回 stale=true，必须停止本次推广并从当前 main 全量重建候选；禁止 rebase 或直接 merge。只返回 JSON。`,
        { label: `promote-candidate-${repositoryRef}-${batchId}-${wave}-${attempt}`, phase: "候选验证" }
      ));
      if (rawPromotion && rawPromotion.stale === true && attempt < 2) continue;
      if (rawPromotion && (rawPromotion.stale === true || rawPromotion.needsPlanRecovery === true || hasFailureSignal(rawPromotion))) {
        recordUnresolved({
          kind: "merge_candidate",
          repositoryRef,
          wave,
          batchIds: ids,
          batchId,
          status: rawPromotion.needsPlanRecovery ? "needs_plan_recovery" : rawPromotion.stale ? "stale" : "promotion_failed",
          durable: rawPromotion.needsPlanRecovery === true,
          error: rawPromotion.errors || rawPromotion.error || rawPromotion,
          worktreePath: rawPromotion.worktreePath,
        });
      }
      try {
        const successfulPromotion = requireSuccess(rawPromotion, `promote candidate ${repositoryRef}`);
        promotion = canonicalPromotionResult(successfulPromotion, repositoryRef, ids);
      } catch (error) {
        recordUnresolved({
          kind: "merge_candidate",
          repositoryRef,
          wave,
          batchIds: ids,
          batchId,
          status: "promotion_failed",
          durable: false,
          error,
        });
        throw error;
      }
      break;
    }
  return promotion;
}

async function promoteReadyBatch(batchId) {
  const promotionWave = ++mergeSequence;
  phase("候选验证");
  try {
    const promotions = [await validateAndPromoteBatch(batchId, promotionWave)];
    const promotedBatchIds = promotions.flatMap(mergedBatchIds);
    const missingPromotionBatchIds = promotions
      .filter(promotion => promotion && promotion.promoted === true && mergedBatchIds(promotion).length === 0)
      .map(promotion => promotion.repositoryRef || "unknown");
    if (missingPromotionBatchIds.length > 0) {
      throw new Error(`promotion_batch_ids_missing:${missingPromotionBatchIds.join(",")}`);
    }
    if (!promotedBatchIds.includes(batchId)) {
      throw new Error(`promotion_batch_not_merged:${batchId}`);
    }
    await cleanupMergedWorktrees(promotedBatchIds, `cleanup-promoted-batch-${batchId}-${promotionWave}`);
    markBatchResolved(batchId);
    // Do not publish a success result until its durable batch attribution and
    // cleanup checks have passed.  This prevents one promotion attempt from
    // appearing as both successful and failed in the final workflow report.
    mergeResults.push({ success: true, batchId, wave: promotionWave, promotions });
    return { batchId, status: "merged", wave: promotionWave };
  } catch (error) {
    mergeResults.push({ success: false, batchId, wave: promotionWave, error: errorText(error) });
    recordUnresolved({
      kind: "merge_candidate",
      batchId,
      status: "promotion_failed",
      durable: false,
      error,
      wave: promotionWave,
      repositoryRef: (batchWorkspaces[batchId] || {}).workspaceRef,
    });
    throw error;
  }
}

async function safelyDeferBatchForRetry(batchId, batchWorktree, batchBranch, reason) {
  try {
    return await deferBatchForRetry(batchId, batchWorktree, batchBranch, reason);
  } catch (cleanupError) {
    // A retry marker must never turn one Batch's cleanup issue into a global
    // failure.  The next scheduler resume can still inspect the retained
    // Worktree/lease and surface the exact cleanup error.
    return {
      batchId,
      status: "retry_pending",
      reason: String(reason || "batch_execution_failed"),
      cleanupError: String(cleanupError),
      // Do not claim that a model-side cleanup call changed the manifest.  The
      // retained worktree/lease is intentionally left for scheduler recovery.
      durable: false,
    };
  }
}

function implementationPrompt(batchId, batchWorktree, batchBranch, taskIds, batchWorkspaceRef, taskWorkspace) {
  return `在插件创建的原生 Git worktree "${batchWorktree}" 中执行 Batch ${batchId}。Feature=${feature}，runId=${runId}，artifact workspace=${artifactWorkspace}。严格按以下固定顺序执行：\n` +
    `Code Workflow 已获自主执行授权：不得调用 request_user_input、要求用户确认或等待用户裁定。差异按既定 TASK/规格和现有工程模式作最小兼容实现，并作为非阻断 Evidence 记录；流程必须继续。\n` +
    CODE_STAGE_TEST_BOUNDARY +
    `1. 执行 cd "${batchWorktree}"（Windows 使用 Set-Location），确认 git rev-parse --show-toplevel 等于该路径、git symbolic-ref --quiet --short HEAD 等于 "${batchBranch}"。禁止 git worktree add/remove、git switch、merge、rebase 或操作其他 checkout。\n` +
    `2. 执行 python "${leasePath}" acquire --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --ttl-seconds ${timeoutPerBatch} --lease-guard；从 JSON 的 lease.ownerToken 保存本 Batch 的 lease token。\n` +
    `3. 将步骤 2 返回的非空 ownerToken 保存为变量，并在后续命令中展开为该真实字符串；命令行中不得出现空字符串、字面量 "LEASE_TOKEN" 或 "<lease-token>"。禁止自行运行 batch_lease_manager.py heartbeat、run_in_background、&、nohup、setsid 或 Start-Process。插件会在每个携带 token 的 task_runner/worktree_manager 命令开始时续租；独立 shell 子进程存活与否不再作为 Batch 失败条件。\n` +
    `4. 执行 python "${schedulerPath}" mark-batch --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status running --worktree-path "${batchWorktree}" --branch-name "${batchBranch}"。业务源码命令只在该 checkout 内执行。\n` +
    `5. Scheduler 已提供本 Batch 的唯一 TASK IDs：${JSON.stringify(taskIds)}。逐个以这些具体 ID 执行；禁止使用空值、"undefined" 或任何占位符。不要用 read_file 读取 artifact 目录；artifact workspace 不是代码目录。自动重试时，先对每个 TASK 执行 task_runner.py inspect；如发现同一 parallelRunId 的 started/in_progress run，使用其真实 runId 执行 task_runner.py abort --force-with-changes --abort-why "automatic_batch_retry" 并携带 --workspace-ref "${batchWorkspaceRef}"，保留 worktree 改动并将 TASK 恢复为 todo。已经 implemented/done 的 TASK 必须保留既有 implementation evidence，禁止再次 start；只继续未完成 TASK。对数组中的每个实际 ID，直接将该值传给 code_task_context.py 的 --task-id 参数。以 taskContract.uiRequired 为唯一条件：false 时跳过 Route resolver，不读取 HTML/Route SKILL；true 时必须在本 agent 内、写前端源码前执行 python "${routeResolverPath}" --workspace "${artifactWorkspace}" --feature "${feature}" --start-route-run --json，并按返回 route 读取对应 Route SKILL 到 EOF，标记 route-skill-read-complete、创建 route write_todos；仅当 Route SKILL 清单推进到转交 parser 后才读取对应 parser 并标记 parser-read，完成清单后标记 route-todos-completed，统一回检后写入 FRONTEND_ROUTE.json。route=spec-driven-ui 不读 parser 但仍须回检，route=none 禁止写前端源码。每个 TASK 必须严格执行“start 成功后才可写业务源码；紧接着 finish-implementation 成功后才可开始下一个 TASK”。禁止预先编写后续 TASK 的任何业务文件。若 start 返回 prestart_unattributed_changes_detected：不得创建 supporting file、不得以 no-code-change 提交、不得继续后续 TASK；保留原始 JSON 并返回 failed，使 Workflow 将该 Batch 隔离为 retry_pending，其他独立 Batch 继续。单个 TASK 从 start 成功到 finish 成功期间产生的全部业务变更都归属该 TASK，runner 不按 Plan 的 scope.paths 拒绝实际实现文件。所有 task_runner 调用必须带 --workspace "${artifactWorkspace}"、--parallel-run-id "${runId}"、展开后的真实 --lease-token、--code-workspace "${taskWorkspace}" 和 --workspace-ref "${batchWorkspaceRef}"。不得操作其他 Batch 或任何主业务 checkout。\n` +
    `6. 全部 TASK 完成后执行 python "${leasePath}" check 并携带同一真实 --owner-token 和 --require-lease-guard；仅 valid=true 才可继续。只调用 python "${worktreeManagerPath}" --json seal --purpose review，并携带 --artifact-workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--repo "${batchWorktree}" 和 --owner-token（同一真实 token）；该命令也会续租。从 JSON 保存供 Review 使用的草稿 commitSha。插件在此命令中提交；不要自行 git add、git commit 或把 Batch 标为可候选合并。\n` +
    `7. 草稿 seal 成功后执行 python "${leasePath}" release，并携带 --workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--owner-token（同一真实 token）和 --final-status sealed。若 seal 返回 parallel_git_index_lock_busy 或 parallel_git_index_lock_recovery_failed，说明等待与本 Batch index.lock 的受控清理后仍无法写入；只以 final-status pending 调用同一 release，随后返回 failed，由 Workflow 标记为 retry_pending 并在同一 run resume。其他首次命令失败也同样以 final-status pending 释放。禁止检查/修改插件源码、创建 Git wrapper、尝试替代命令或继续任何 TASK。\n` +
    `返回 {batchId, status:"success", worktreePath:batchWorktree, branchName:batchBranch, commitSha}。不得创建任何 workflow、手工创建分支、使用 undefined 路径或 feature、手工 git add/commit；不要 merge、rebase、解决冲突、删除 worktree。任何命令失败立即返回 failed，不得以部分结果继续。`;
}

async function runInitialBatchLifecycle(batchId) {
  let batchWorktree = (batchWorkspaces[batchId] || {}).worktreePath;
  let batchBranch = (batchWorkspaces[batchId] || {}).branchName;
  try {
    const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
    const batchWorkspace = batchWorkspaces[batchId] || {};
    const batchWorkspaceRef = batchWorkspace.workspaceRef;
    if (!taskIds.length) throw new Error(`scheduler returned no task IDs for ${batchId}`);
    if (!usableString(batchWorkspaceRef) || !codeWorkspaces[batchWorkspaceRef]) {
      throw new Error(`scheduler did not provide a code workspace for ${batchId}`);
    }
    const provisioned = requireSuccess(await workflowAgent(
      `为 Batch ${batchId} 创建或复用插件托管的原生 Git Worktree。执行 python "${worktreeManagerPath}" --json provision --artifact-workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。只返回 JSON；不得使用平台 isolation，不得修改业务源码。`,
      { label: `provision-worktree-${batchId}`, phase: "Batch 阶段", schema: WORKTREE_SCHEMA }
    ), `provision worktree ${batchId}`);
    batchWorktree = provisioned.worktreePath;
    batchBranch = provisioned.branchName;
    if (!usableString(batchWorktree) || !usableString(batchBranch)) {
      throw new Error(`plugin did not provide native worktree for ${batchId}`);
    }
    const taskWorkspace = batchTaskWorkspace(batchId, batchWorktree);
    const implemented = unwrap(await workflowAgent(
      implementationPrompt(batchId, batchWorktree, batchBranch, taskIds, batchWorkspaceRef, taskWorkspace),
      { label: `fixed-batch-${batchId}`, phase: "Batch 阶段", schema: BATCH_RESULT_SCHEMA }
    ));
    batchResults.push(implemented);
    if (!implemented || implemented.status !== "success") {
      return safelyDeferBatchForRetry(
        batchId,
        batchWorktree,
        batchBranch,
        implemented && (implemented.errorMessage || implemented.raw || implemented.status)
      );
    }
    const delivery = await runDeliveryWithImplementationRepair(implemented);
    if (delivery.status === "ready_to_candidate") return promoteReadyBatch(batchId);
    return delivery;
  } catch (error) {
    return safelyDeferBatchForRetry(batchId, batchWorktree, batchBranch, String(error));
  }
}

async function runRecoveredBatchLifecycle(recovery) {
  const batchId = recovery.batchId;
  try {
    const delivery = await continueRecoveredDelivery(recovery);
    if (delivery.status === "ready_to_candidate") return promoteReadyBatch(batchId);
    return delivery;
  } catch (error) {
    return safelyDeferBatchForRetry(batchId, recovery.worktreePath, recovery.branchName, String(error));
  }
}

async function runMergeableBatchLifecycle(batchId) {
  const existing = batchWorkspaces[batchId] || {};
  try {
    return await promoteReadyBatch(batchId);
  } catch (error) {
    return safelyDeferBatchForRetry(batchId, existing.worktreePath, existing.branchName, String(error));
  }
}

async function runLifecycleSafely(batchId, source, execute, fallback = {}) {
  try {
    const result = await execute();
    return observeLifecycleResult(batchId, source, result);
  } catch (error) {
    // `parallel()` must only receive resolving jobs.  If an unexpected Agent
    // exception escaped a lifecycle helper, retain the worktree and turn it
    // into the same retry contract used by expected Batch failures.
    const deferred = await safelyDeferBatchForRetry(
      batchId,
      fallback.worktreePath || (batchWorkspaces[batchId] || {}).worktreePath,
      fallback.branchName || (batchWorkspaces[batchId] || {}).branchName,
      errorText(error)
    );
    return observeLifecycleResult(batchId, source, deferred);
  }
}

function runnableScheduledBatchIds() {
  return normalizeScheduledGroups(scheduledGroups)
    .flat()
    .filter(batchId => (
      !quarantinedBatchIds.has(batchId)
      && (!schedulerSnapshotDegraded || !schedulerFallbackConsumed.has(batchId))
    ));
}

function runnableSchedulerFallbackBatchIds() {
  if (!schedulerSnapshotDegraded) return [];
  return nextSchedulerFallbackWave(
    schedulerFallbackGroups,
    schedulerFallbackConsumed,
    quarantinedBatchIds,
  );
}

function runnableStageRecoveries() {
  return stageRecoveryBatches
    .filter(recovery => (
      recovery
      && !quarantinedBatchIds.has(recovery.batchId)
      && (!schedulerSnapshotDegraded || !schedulerFallbackConsumed.has(recovery.batchId))
    ));
}

function runnableMergeableBatchIds() {
  return mergeableBatches.filter(batchId => (
    !quarantinedBatchIds.has(batchId)
    && (!schedulerSnapshotDegraded || !schedulerFallbackConsumed.has(batchId))
  ));
}

function takeNextRunnableLifecycle(claimedBatchIds) {
  const claimed = claimedBatchIds || new Set();
  const scheduledBatchIds = runnableScheduledBatchIds();
  const fallbackBatchIds = runnableSchedulerFallbackBatchIds();
  const recoveries = runnableStageRecoveries();
  const mergeable = runnableMergeableBatchIds();
  const recoveredBatchIds = new Set(recoveries.map(item => item.batchId));
  const mergeableBatchIds = new Set(mergeable);
  const claim = job => {
    if (!job || claimed.has(job.batchId)) return null;
    claimed.add(job.batchId);
    return job;
  };

  for (const batchId of scheduledBatchIds) {
    const job = claim({
      batchId,
      source: "initial",
      execute: () => runInitialBatchLifecycle(batchId),
    });
    if (job) return job;
  }
  for (const batchId of fallbackBatchIds) {
    const job = claim({
      batchId,
      source: "scheduler_snapshot_fallback",
      execute: () => runInitialBatchLifecycle(batchId),
    });
    if (job) return job;
  }
  for (const recovery of recoveries) {
    if (mergeableBatchIds.has(recovery.batchId)) continue;
    const job = claim({
      batchId: recovery.batchId,
      source: "stage_recovery",
      execute: () => runRecoveredBatchLifecycle(recovery),
      fallback: recovery,
    });
    if (job) return job;
  }
  for (const batchId of mergeable) {
    if (recoveredBatchIds.has(batchId)) continue;
    const job = claim({
      batchId,
      source: "merge_candidate",
      execute: () => runMergeableBatchLifecycle(batchId),
    });
    if (job) return job;
  }
  return null;
}

function canStartLifecycle() {
  schedulerCycles += 1;
  if (schedulerCycles <= MAX_SCHEDULER_CYCLES) return true;
  recordUnresolved({
    kind: "scheduler",
    status: "cycle_limit_exceeded",
    durable: false,
    error: `parallel_scheduler_cycle_limit_exceeded:${schedulerCycles}`,
  });
  return false;
}

async function runLifecycleChain(initialJob, claimedBatchIds, drainLabel) {
  let job = initialJob;
  let result = null;
  while (job) {
    if (!canStartLifecycle()) return result;
    result = await runLifecycleSafely(job.batchId, job.source, job.execute, job.fallback);
    // Whether this Batch merged or failed into retry_pending, its execution
    // slot is now free. Refresh immediately so an independent pending Batch
    // can use it; only a real merge releases this Batch's own dependents.
    // Do not wait for unrelated jobs passed to the same `parallel()` call.
    if (!result) return result;
    const state = await readSchedulerState(
      `${drainLabel}-after-batch-${job.batchId}-${schedulerCycles}`,
      "Batch 阶段"
    );
    if (!state) return result;
    if (state.schedulerSnapshotFallback === true) {
      // This Batch has completed a lifecycle attempt. Do not replay it from a
      // stale scheduler grant while walking the cached, conservative waves.
      schedulerFallbackConsumed.add(job.batchId);
    }
    job = takeNextRunnableLifecycle(claimedBatchIds);
  }
  return result;
}

async function drainRunnableLifecycles(drainLabel) {
  let ranAny = false;
  for (;;) {
    // Claim this snapshot in-memory before launching.  The scheduler's
    // selection is read-only until a worker acquires its lease, so this avoids
    // two concurrently completed chains starting the same pending Batch.
    const claimedBatchIds = new Set();
    const lifecycleJobs = [];
    for (;;) {
      const job = takeNextRunnableLifecycle(claimedBatchIds);
      if (!job) break;
      lifecycleJobs.push(() => runLifecycleChain(job, claimedBatchIds, drainLabel));
    }
    if (!lifecycleJobs.length) {
      return { ranAny, reason: "no_runnable_independent_batches" };
    }

    // A failed Batch is quarantined locally and its slot is immediately reused
    // by another eligible Batch. Successful merges use the same eager path,
    // so independent peers never form a completion barrier.
    phase("Batch 阶段");
    try {
      await parallel(lifecycleJobs);
    } catch (error) {
      // This is defensive: each job above catches its own failures.  Keep an
      // unexpected parallel-engine failure observable rather than aborting.
      recordUnresolved({ kind: "scheduler", status: "parallel_execution_failed", durable: false, error });
    }
    ranAny = true;

    // `status` schedules currently-independent work but deliberately leaves
    // retry_pending records untouched.  Retries are deferred to the explicit
    // final repair phase so a flaky Batch cannot starve peer branches.
    const state = await readSchedulerState(`${drainLabel}-schedule-cycle-${schedulerCycles}`);
    if (!state) return { ranAny, reason: "scheduler_snapshot_unavailable" };
  }
}

function manifestFromScheduler(scheduler = lastScheduler) {
  return scheduler && scheduler.manifest && typeof scheduler.manifest === "object"
    ? scheduler.manifest
    : null;
}

function scopedManifestBatches(manifest) {
  const batches = manifest && manifest.batches && typeof manifest.batches === "object"
    ? manifest.batches
    : {};
  return Object.entries(batches).filter(([batchId]) => isValidBatchId(batchId));
}

function unresolvedBatchDetails(scheduler = lastScheduler) {
  const manifest = manifestFromScheduler(scheduler);
  const byId = new Map();
  const scopedBatches = scopedManifestBatches(manifest);
  const batchMap = manifest && manifest.batches && typeof manifest.batches === "object" ? manifest.batches : {};
  for (const [batchId, batch] of scopedBatches) {
    if (!batch || batch.status === "merged") continue;
    const dependencies = Array.isArray(batch.dependencies) ? batch.dependencies : [];
    const blockedBy = dependencies.filter(dependency => {
      const upstream = batchMap[dependency];
      return !upstream || upstream.status !== "merged";
    });
    const blocks = scopedBatches
      .filter(([candidateId, candidate]) => candidateId !== batchId && candidate && Array.isArray(candidate.dependencies) && candidate.dependencies.includes(batchId) && candidate.status !== "merged")
      .map(([candidateId]) => candidateId);
    const recovery = batch.recovery && typeof batch.recovery === "object" ? batch.recovery : {};
    byId.set(batchId, {
      batchId,
      status: batch.status || "unknown",
      error: batch.error || recovery.lastError,
      retryAttempts: Number.isInteger(recovery.retryAttempts) ? recovery.retryAttempts : 0,
      worktreePath: batch.worktreePath,
      branchName: batch.branchName,
      commitSha: batch.commitSha,
      dependencies,
      blockedBy,
      blocks,
      recoveryStatus: recovery.status,
    });
  }
  for (const record of activeUnresolvedRecords()) {
    if (!isValidBatchId(record.batchId) || ["cleanup", "deferred_issue", "validation", "scheduler"].includes(record.kind)) continue;
    const existing = byId.get(record.batchId) || { batchId: record.batchId, dependencies: [], blockedBy: [], blocks: [] };
    if (existing.status === "merged") continue;
    byId.set(record.batchId, {
      ...existing,
      status: existing.status || record.status || "unresolved",
      error: existing.error || record.error,
      worktreePath: existing.worktreePath || record.worktreePath,
      branchName: existing.branchName || record.branchName,
      durable: record.durable === true,
    });
  }
  return [...byId.values()].sort((left, right) => left.batchId.localeCompare(right.batchId));
}

function unresolvedMergeCandidateDetails(scheduler = lastScheduler) {
  const manifest = manifestFromScheduler(scheduler);
  const trains = manifest && manifest.mergeTrains && typeof manifest.mergeTrains === "object"
    ? manifest.mergeTrains
    : {};
  const byKey = new Map();
  const add = candidate => {
    const key = `${candidate.repositoryRef || "unknown"}|${candidate.wave || "unknown"}`;
    const existing = byKey.get(key) || {};
    byKey.set(key, { ...existing, ...candidate });
  };
  for (const [trainId, train] of Object.entries(trains)) {
    if (!train || !["candidate_conflicted", "needs_resolution", "failed", "stale", "built"].includes(train.status)) continue;
    const trainBatchIds = Array.isArray(train.batchIds) ? train.batchIds : [];
    // A later retry can promote the same deliveries through a fresh Wave.
    // Retain the older candidate for audit, but do not make that historical
    // record look unfinished once every delivery it references has a durable
    // merge commit.
    if (
      trainBatchIds.length > 0
      && trainBatchIds.every(batchId => {
        const batch = manifest && manifest.batches && manifest.batches[batchId];
        return batch && batch.status === "merged" && usableString(batch.mergeCommitSha);
      })
    ) continue;
    const ids = trainBatchIds.filter(isValidBatchId);
    add({
      trainId,
      repositoryRef: train.repositoryRef,
      wave: train.wave,
      status: train.status,
      error: train.error,
      batchIds: ids,
      worktreePath: train.worktreePath,
      conflictedFiles: train.conflictContext && train.conflictContext.conflictedFiles,
      cleanupErrors: train.cleanupErrors,
    });
  }
  for (const record of activeUnresolvedRecords()) {
    if (record.kind !== "merge_candidate") continue;
    add({
      repositoryRef: record.repositoryRef,
      wave: record.wave,
      status: record.status,
      error: record.error,
      batchIds: record.batchIds || (record.batchId ? [record.batchId] : []),
      worktreePath: record.worktreePath,
      conflictedFiles: record.conflictedFiles,
      durable: record.durable === true,
    });
  }
  return [...byKey.values()]
    .filter(candidate => candidate.status !== "promoted")
    .sort((left, right) => `${left.repositoryRef}|${left.wave}`.localeCompare(`${right.repositoryRef}|${right.wave}`));
}

function deferredIssueDetails(scheduler = lastScheduler, verification = null) {
  const manifest = manifestFromScheduler(scheduler);
  if (manifest && Array.isArray(manifest.deferredIssues)) return manifest.deferredIssues;
  return verification && Array.isArray(verification.deferredIssues) ? verification.deferredIssues : [];
}

function allWorkflowDeliveriesMerged(scheduler = lastScheduler) {
  const manifest = manifestFromScheduler(scheduler);
  const batches = scopedManifestBatches(manifest);
  return batches.length > 0
    && batches.every(([, batch]) => batch && batch.status === "merged" && usableString(batch.mergeCommitSha))
    && unresolvedBatchDetails(scheduler).length === 0
    && unresolvedMergeCandidateDetails(scheduler).length === 0;
}

function completionReport({
  finalStatus,
  scheduler = lastScheduler,
  e2e = null,
  verification = null,
  e2eSkippedReason = null,
  nextAction = null,
}) {
  const batches = unresolvedBatchDetails(scheduler);
  const mergeCandidates = unresolvedMergeCandidateDetails(scheduler);
  const deferredIssues = deferredIssueDetails(scheduler, verification);
  const cleanup = activeUnresolvedRecords().filter(record => record.kind === "cleanup");
  const activeSchedulerFailures = activeUnresolvedRecords().filter(record => record.kind === "scheduler");
  return {
    ok: ["succeeded", "succeeded_with_issues"].includes(finalStatus),
    feature,
    runId,
    batchResults,
    mergeResults,
    cleanupResults,
    lifecycleResults,
    e2e,
    verification,
    finalStatus,
    retryPendingBatches: batches.filter(batch => batch.status === "retry_pending").map(batch => batch.batchId),
    blockedBatches: batches.filter(batch => ["blocked", "needs_resolution"].includes(batch.status)).map(batch => batch.batchId),
    deferredIssues,
    e2eSkippedReason,
    finalRepair: {
      attempted: true,
      results: finalRepairResults,
    },
    unresolved: {
      batches,
      mergeCandidates,
      deferredIssues,
      cleanup,
      schedulerFailures: activeSchedulerFailures,
      records: activeUnresolvedRecords(),
    },
    schedulerFailures,
    nextAction: nextAction || (batches.length || mergeCandidates.length
      ? "inspect_unresolved_batches_then_resume"
      : "inspect_final_validation_failure"),
  };
}

async function persistUndurableBatchFailures() {
  const active = activeUnresolvedRecords();
  const durableBatchIds = new Set(active
    .filter(record => record.kind === "batch" && record.durable === true && isValidBatchId(record.batchId))
    .map(record => record.batchId));
  for (const record of active) {
    if (
      record.durable === true
      || !isValidBatchId(record.batchId)
      || !["batch", "merge_candidate"].includes(record.kind)
      || durableBatchIds.has(record.batchId)
    ) continue;
    const result = await safelyDeferBatchForRetry(
      record.batchId,
      record.worktreePath || (batchWorkspaces[record.batchId] || {}).worktreePath,
      record.branchName || (batchWorkspaces[record.batchId] || {}).branchName,
      record.error || record.status
    );
    finalRepairResults.push({
      kind: "persist_retry_marker",
      batchId: record.batchId,
      durable: result.durable === true,
      status: result.status,
      error: result.cleanupError,
    });
    if (result.durable === true) {
      record.durable = true;
      durableBatchIds.add(record.batchId);
    } else {
      record.finalRepairError = result.cleanupError || result.reason;
    }
  }
}

async function queueRetryExhaustedBatchRepairs() {
  // A retry-exhausted Batch is deliberately quarantined during the normal
  // drain.  Once every independent branch has had its chance to finish, give
  // it one fresh, durable repair admission.  `mark-batch retry_pending` owns
  // the retry counter reset for this explicit final-repair boundary.
  for (const batch of unresolvedBatchDetails()) {
    if (batch.status !== "blocked" || batch.recoveryStatus !== "retry_exhausted") continue;
    const result = await safelyDeferBatchForRetry(
      batch.batchId,
      batch.worktreePath || (batchWorkspaces[batch.batchId] || {}).worktreePath,
      batch.branchName || (batchWorkspaces[batch.batchId] || {}).branchName,
      batch.error || "retry_exhausted_final_repair"
    );
    finalRepairResults.push({
      kind: "requeue_retry_exhausted_batch",
      batchId: batch.batchId,
      status: result.status,
      durable: result.durable === true,
      error: result.cleanupError,
    });
    if (result.durable === true) {
      quarantinedBatchIds.delete(batch.batchId);
      for (const record of unresolvedRecords) {
        if (record && record.batchId === batch.batchId && record.resolved !== true) {
          record.resolved = true;
          record.resolvedAt = "queued_for_final_repair";
        }
      }
    }
  }
}

async function attemptFinalCandidateRepair(record) {
  if (!usableString(record.repositoryRef) || !Number.isFinite(Number(record.wave))) return;
  const batchIds = Array.isArray(record.batchIds) && record.batchIds.length
    ? record.batchIds
    : (record.batchId ? [record.batchId] : []);
  try {
    const resolved = unwrap(await workflowAgent(
      `所有独立 Batch 已排空。对保留的 Merge Train 候选作最后一次受控自动恢复：执行 python "${mergeTrainPath}" resolve-candidate ` +
      `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${record.repositoryRef}" --wave ${record.wave}。` +
      `只可处理该既有候选；不得创建新 Worktree、修改 main 或继续其他 Batch。无法自动解决时保留候选和冲突上下文。只返回 JSON。`,
      { label: `final-repair-candidate-${record.repositoryRef}-${record.wave}`, phase: "最终修复与报告" }
    ));
    if (!resolved || resolved.status !== "built" || resolved.success === false) {
      finalRepairResults.push({
        kind: "resolve_candidate",
        repositoryRef: record.repositoryRef,
        wave: record.wave,
        status: resolved && resolved.status || "failed",
        error: resolved && (resolved.error || resolved.reason),
      });
      recordUnresolved({
        kind: "merge_candidate",
        repositoryRef: record.repositoryRef,
        wave: record.wave,
        batchIds,
        batchId: batchIds.length === 1 ? batchIds[0] : undefined,
        status: "needs_resolution",
        durable: true,
        error: resolved,
        worktreePath: resolved && resolved.worktreePath,
        conflictedFiles: resolved && resolved.conflictedFiles,
      });
      return;
    }
    const promotion = requireSuccess(await workflowAgent(
      `推广已在最终修复阶段恢复的候选。执行 python "${mergeTrainPath}" promote-candidate --allow-unverified --allow-stale ` +
      `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${record.repositoryRef}" --wave ${record.wave}。` +
      `仅推广这个已恢复候选；若 stale 或失败，保留其状态供最终报告。只返回 JSON。`,
      { label: `final-promote-candidate-${record.repositoryRef}-${record.wave}`, phase: "最终修复与报告" }
    ), `final promote candidate ${record.repositoryRef}`);
    const promotedBatchIds = mergedBatchIds(promotion);
    if (!promotedBatchIds.length) {
      throw new Error(`final_promotion_batch_ids_missing:${record.repositoryRef}:${record.wave}`);
    }
    await cleanupMergedWorktrees(promotedBatchIds, `final-cleanup-candidate-${record.repositoryRef}-${record.wave}`);
    for (const batchId of promotedBatchIds) markBatchResolved(batchId);
    markCandidateResolved(record.repositoryRef, record.wave);
    mergeResults.push({ success: true, batchIds: promotedBatchIds, wave: record.wave, repositoryRef: record.repositoryRef, finalRepair: true });
    finalRepairResults.push({
      kind: "resolve_candidate",
      repositoryRef: record.repositoryRef,
      wave: record.wave,
      status: "promoted",
      batchIds: promotedBatchIds,
    });
  } catch (error) {
    finalRepairResults.push({
      kind: "resolve_candidate",
      repositoryRef: record.repositoryRef,
      wave: record.wave,
      status: "failed",
      error: errorText(error),
    });
    recordUnresolved({
      kind: "merge_candidate",
      repositoryRef: record.repositoryRef,
      wave: record.wave,
      batchIds,
      batchId: batchIds.length === 1 ? batchIds[0] : undefined,
      status: "final_repair_failed",
      durable: false,
      error,
    });
  }
}

async function runFinalRepairAndReport() {
  phase("最终修复与报告");
  await readSchedulerState("final-repair-initial-snapshot", "最终修复与报告");
  await persistUndurableBatchFailures();
  await queueRetryExhaustedBatchRepairs();

  // Candidate conflicts are retried only after independent work has drained.
  // One attempt per candidate prevents a permanently conflicted candidate from
  // monopolizing the run.
  const attemptedCandidates = new Set();
  for (const candidate of unresolvedMergeCandidateDetails()) {
    if (!["candidate_conflicted", "needs_resolution", "resolve_failed"].includes(candidate.status)) continue;
    const key = `${candidate.repositoryRef}|${candidate.wave}`;
    if (attemptedCandidates.has(key)) continue;
    attemptedCandidates.add(key);
    await attemptFinalCandidateRepair(candidate);
  }

  let repairState = await readSchedulerState("final-repair-before-retry", "最终修复与报告");
  let retryDrain = 0;
  while (repairState) {
    const retryPending = retryPendingBatchesOf(repairState);
    if (retryPending.length) {
      if (retryDrain >= MAX_FINAL_RETRY_DRAINS) {
        finalRepairResults.push({
          kind: "resume_retry_pending",
          status: "automatic_retry_budget_exhausted",
          retryDrain,
          retryPendingBatchIds: retryPending,
        });
        break;
      }
      retryDrain += 1;
      try {
        repairState = await recoverPendingRetries(repairState, `final-repair-resume-retry-pending-${retryDrain}`);
        applySchedulerState(repairState);
        const rescheduled = new Set([
          ...(Array.isArray(repairState.rescheduledRetryBatches) ? repairState.rescheduledRetryBatches : []),
          ...normalizeScheduledGroups(repairState.scheduledGroups || []).flat(),
          ...(repairState.mergeableBatches || []),
          ...(repairState.stageRecoveryBatches || []).map(item => item && item.batchId),
        ].filter(isValidBatchId));
        for (const batchId of rescheduled) quarantinedBatchIds.delete(batchId);
        finalRepairResults.push({
          kind: "resume_retry_pending",
          status: repairState.status,
          retryDrain,
          rescheduledBatchIds: [...rescheduled],
        });
      } catch (error) {
        recordSchedulerFailure(`final-repair-resume-retry-pending-${retryDrain}`, error);
        finalRepairResults.push({ kind: "resume_retry_pending", retryDrain, status: "failed", error: errorText(error) });
        break;
      }
    }

    // Run once even when there was no retry at entry: a candidate repaired
    // above may have made a dependent Batch runnable. A retry can likewise
    // uncover another transient failure while recovered work is draining.
    const drain = await drainRunnableLifecycles(`final-repair-${retryDrain || "initial"}`);
    finalRepairResults.push({ kind: "drain_recovered_batches", retryDrain, ...drain });
    repairState = await readSchedulerState(`final-repair-after-drain-${retryDrain}`, "最终修复与报告");
    if (!repairState || !retryPendingBatchesOf(repairState).length) break;
  }
  return readSchedulerState("final-report-snapshot", "最终修复与报告");
}

// A resumed run can already contain merged deliveries from a prior interrupted
// Workflow. Cleanup is non-blocking: a temporary-worktree error never turns a
// durable delivery back into a failed Batch.
await cleanupMergedWorktrees([], "recover-merged-worktree-cleanup");
applySchedulerState(prepared);

// First drain every currently runnable branch without automatically resuming
// failed work.  This lets siblings and their downstream waves finish even when
// one model call, worktree, or merge candidate is unhealthy.
await drainRunnableLifecycles("drain");

const finalScheduler = await runFinalRepairAndReport();
if (finalScheduler) applySchedulerState(finalScheduler);

if (!allWorkflowDeliveriesMerged(finalScheduler)) {
  const snapshotAvailable = Boolean(manifestFromScheduler(finalScheduler));
  return completionReport({
    finalStatus: "completed_with_unresolved",
    scheduler: finalScheduler || lastScheduler,
    e2eSkippedReason: snapshotAvailable ? "delivery_dag_incomplete" : "scheduler_snapshot_unavailable",
    nextAction: "inspect_unresolved_batches_then_resume",
  });
}

phase("最终验证");
let e2eStarted;
let e2e;
try {
  e2eStarted = requireSuccess(await workflowAgent(
    `所有 delivery Batch 已推广后，创建 B-E2E。执行 python "${mergeTrainPath}" begin-e2e --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
    `此命令只创建 main SHA 绑定的验证状态；它不运行 Batch UTest。只返回 JSON。`,
    { label: "begin-e2e-validation", phase: "最终验证" }
  ), "begin e2e");
  e2e = requireSuccess(await workflowAgent(
    `在当前已合并 main 上执行唯一的 B-E2E 验证。Feature=${feature}，runId=${runId}。` +
    `必须只在这些插件创建的临时验证 Worktree 中操作：${JSON.stringify(e2eStarted.worktrees || {})}；不得操作主 checkout。` +
    `先收集可重现环境元数据：environment.version、environment.seedDataDigest、environment.dependencies（对象，含 DB/Redis/MQ 等实际版本或明确的 none），并将其与场景摘要一并作为 JSON metadata。` +
    `执行 Plan/Feature 定义且未被 Batch UTest 覆盖的端到端场景，不得重复执行 Batch test。` +
    `通过后执行 python "${mergeTrainPath}" finish-e2e --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --passed true --metadata-json '<含上述 environment 与场景摘要的 JSON>'。` +
    `失败时使用 --passed false 并记录失败摘要；失败会创建受控修复入口，禁止在 main 直接修复。只返回 JSON。`,
    { label: "run-e2e-validation", phase: "最终验证" }
  ), "e2e validation");
} catch (error) {
  recordUnresolved({ kind: "validation", status: "e2e_failed", durable: false, error });
  return completionReport({
    finalStatus: "completed_with_validation_failure",
    scheduler: finalScheduler || lastScheduler,
    e2e: e2e || { error: errorText(error) },
    e2eSkippedReason: "e2e_execution_failed",
    nextAction: "inspect_e2e_failure_and_use_controlled_repair",
  });
}
void e2eStarted;

let verification;
try {
  verification = unwrap(await workflowAgent(
    `执行 python "${aggregatePath}" --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
    `这是只读 evidence aggregate：禁止执行任何编译、测试或 E2E 命令。只返回 JSON。`,
    { label: "aggregate-staged-evidence", phase: "最终验证", schema: VERIFICATION_SCHEMA }
  ));
  if (!verification || verification.unparsedStructuredOutput === true || hasFailureSignal(verification)) {
    recordUnresolved({ kind: "validation", status: "evidence_aggregate_failed", durable: false, error: verification });
    return completionReport({
      finalStatus: "completed_with_validation_failure",
      scheduler: finalScheduler || lastScheduler,
      e2e,
      verification,
      nextAction: "inspect_evidence_aggregate_failure",
    });
  }
} catch (error) {
  recordUnresolved({ kind: "validation", status: "evidence_aggregate_failed", durable: false, error });
  return completionReport({
    finalStatus: "completed_with_validation_failure",
    scheduler: finalScheduler || lastScheduler,
    e2e,
    verification: { error: errorText(error) },
    nextAction: "inspect_evidence_aggregate_failure",
  });
}

return completionReport({
  finalStatus: verification.hasDeferredIssues ? "succeeded_with_issues" : "succeeded",
  scheduler: finalScheduler || lastScheduler,
  e2e,
  verification,
  nextAction: verification.hasDeferredIssues ? "review_deferred_issues" : "completed",
});
