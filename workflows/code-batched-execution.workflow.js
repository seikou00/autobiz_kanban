export const meta = {
  name: "code-batched-execution",
  description: "Batch DAG with isolated feature integration, E2E and user-confirmed baseline branch promotion",
  whenToUse: "由 workflow_launcher.py 在存在合法待执行 Batch 时调用",
  phases: [
    { title: "准备", detail: "创建或恢复 scheduler run 并计算当前可执行 DAG 集合" },
    { title: "Batch 阶段", detail: "编码 → review → UTest/封存；Review 可定向修复一次，UTest 失败记录后继续" },
    { title: "候选验证", detail: "Merge Train 合成并推广已完成 Review 与 UTest 记录的候选 SHA" },
    { title: "最终验证", detail: "合并后运行 B-E2E，最终只聚合既有证据、不重复执行命令" }
  ]
};

const DEFAULT_MAX_PARALLEL = 5;
const DEFAULT_BATCH_TIMEOUT_SECONDS = 4 * 60 * 60;
const MAX_SCHEDULER_CYCLES = 100;
// Idle dispatch workers keep checking the durable scheduler snapshot while
// another Batch lifecycle is still running. Completion-triggered refreshes
// remain the fast path; this bounded poll closes the gap when readiness changes
// outside the worker that currently owns the only active lifecycle.
const DISPATCH_POLL_INTERVAL_MS = 30 * 60 * 1000;
// A transport-level empty model response happens before the child can return
// its command result.  It is neither a Batch verdict nor evidence that the
// command failed, so give that same child a small, bounded retry budget.
// Periodic scheduler polls opt out and wait for their next polling interval.
// Retrying other errors would be unsafe because they may describe a genuine
// command failure after a state-changing operation.
const MAX_EMPTY_AGENT_RESPONSE_RETRIES = 2;
// The scheduler is the authority for the per-Batch retry budget (currently
// two failure admissions). Keep one extra pass to let the scheduler turn a
// legacy retry marker with a zero counter into its explicit exhausted state.
// Final repair keeps draining until that budget is consumed or no retry remains.
const MAX_FINAL_RETRY_DRAINS = 3;
// Review findings and production defects proven by UTest each receive one
// targeted repair. A deferred UTest remains explicit evidence and may proceed
// through an unverified candidate to mandatory final B-E2E.
const SINGLE_REPAIRABLE_STAGES = new Set(["review", "test"]);
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
// Scheduler snapshots cross a structured-agent boundary before this Workflow
// can consume them.  Every field used below must therefore be declared here:
// `additionalProperties` permits forward-compatible scheduler diagnostics, but
// does not cause an undeclared field to survive structured output projection.
const RECOVERY_FAILURE_CONTEXT_SCHEMA = {
  type: "object",
  properties: {
    failedStage: { type: "string" },
    failureType: { type: "string" },
    message: { type: "string" },
    testLogPath: { type: "string" },
    targetId: { type: "string" },
    commandId: { type: "string" },
    evidenceId: { type: "string" },
    testFile: { type: "string" },
    argv: { type: "array", items: { type: "string" } },
    command: { type: "string" },
    repairContextPath: { type: "string" },
    repairContextDigest: { type: "string" },
    affectedTaskIds: { type: "array", items: { type: "string" } },
    cwd: { type: "string" },
    evidenceLogPath: { type: "string" }
  },
  required: ["failedStage", "failureType", "message"],
  additionalProperties: true
};
const STAGE_RECOVERY_ENTRY_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    worktreePath: { type: ["string", "null"] },
    branchName: { type: ["string", "null"] },
    commitSha: { type: ["string", "null"] },
    recoveryKind: { const: "stage_resume" },
    // These flags are redundant with membership in stageRecoveryBatches.
    // Some structured-agent transports serialize their boolean defaults rather
    // than the scheduler's literal values, so validate their shape here and
    // canonicalize their meaning at the Workflow boundary below.
    preserveWorktree: { type: "boolean" },
    reprovision: { type: "boolean" },
    nextStage: { enum: ["prepare", "implement", "review", "test"] },
    failureContext: RECOVERY_FAILURE_CONTEXT_SCHEMA
  },
  required: [
    "batchId", "worktreePath", "branchName", "commitSha",
    "recoveryKind", "preserveWorktree", "reprovision", "nextStage"
  ],
  additionalProperties: true
};
const IMPLEMENTATION_RECOVERY_ENTRY_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    worktreePath: { type: ["string", "null"] },
    branchName: { type: ["string", "null"] },
    recoveryKind: { const: "implementation_resume" },
    resumeFromStage: { const: "implement" },
    preserveWorktree: { type: "boolean" },
    reprovision: { type: "boolean" },
    changedFileCount: { type: "number" },
    changedFileSample: { type: "array", items: { type: "string" } }
  },
  required: [
    "batchId", "worktreePath", "branchName", "recoveryKind",
    "resumeFromStage", "preserveWorktree", "reprovision"
  ],
  additionalProperties: true
};
const WORKFLOW_MANIFEST_SCHEMA = {
  type: "object",
  properties: {
    batches: {
      type: "object",
      additionalProperties: {
        type: "object",
        properties: {
          status: { type: "string" },
          dispatchGeneration: { type: "integer" },
          dispatch: { type: "object", additionalProperties: true },
          dependencies: { type: "array", items: { type: "string" } },
          error: { type: ["string", "null"] },
          worktreePath: { type: ["string", "null"] },
          branchName: { type: ["string", "null"] },
          commitSha: { type: ["string", "null"] },
          mergeCommitSha: { type: ["string", "null"] },
          recovery: {
            type: "object",
            properties: {
              status: { type: ["string", "null"] },
              retryAttempts: { type: ["number", "null"] },
              lastError: { type: ["string", "null"] },
              failureClassification: { type: ["string", "null"] },
              failureFingerprint: { type: ["string", "null"] },
              reasonCode: { type: ["string", "null"] },
              nextAction: { type: ["string", "null"] }
            },
            additionalProperties: false
          }
        },
        required: ["status", "dependencies", "worktreePath", "branchName", "commitSha", "mergeCommitSha", "recovery"],
        additionalProperties: false
      }
    },
    mergeTrains: {
      type: "object",
      additionalProperties: {
        type: "object",
        properties: {
          status: { type: "string" },
          batchIds: { type: "array", items: { type: "string" } },
          repositoryRef: { type: ["string", "null"] },
          wave: { type: ["number", "null"] },
          error: { type: ["string", "null"] },
          worktreePath: { type: ["string", "null"] },
          cleanupErrors: { type: ["array", "null"], items: { type: "string" } },
          conflictContext: {
            type: "object",
            properties: { conflictedFiles: { type: ["array", "null"], items: { type: "string" } } },
            additionalProperties: false
          }
        },
        required: ["status", "batchIds", "repositoryRef", "wave"],
        additionalProperties: false
      }
    },
    deferredIssues: {
      type: "array",
      items: {
        type: "object",
        properties: {
          issueId: { type: "string" },
          kind: { type: "string" },
          batchId: { type: "string" },
          stage: { type: "string" },
          failureType: { type: "string" },
          message: { type: "string" },
          disposition: { type: "string" },
          blocksWorkflow: { type: "boolean" },
          status: { type: "string" },
          evidenceId: { type: "string" },
          batchCommit: { type: ["string", "null"] },
          createdAt: { type: "string" }
        },
        additionalProperties: false
      }
    }
  },
  required: ["batches", "mergeTrains", "deferredIssues"],
  additionalProperties: false
};
const SCHEDULER_RESULT_SCHEMA = {
  type: "object",
  properties: {
    integrationMode: { type: "string" },
    integrationWorkspaces: { type: "object", additionalProperties: true },
    mainPromotion: { type: ["object", "null"], additionalProperties: true },
    runId: { type: "string" },
    status: { type: "string" },
    scheduledGroups: { type: "array", items: { type: "array", items: { type: "string" } } },
    readyBatches: { type: "array", items: { type: "string" } },
    allReadyBatches: { type: "array", items: { type: "string" } },
    mergeableBatches: { type: "array", items: { type: "string" } },
    implementationRecoveryBatches: {
      type: "array",
      items: IMPLEMENTATION_RECOVERY_ENTRY_SCHEMA
    },
    stageRecoveryBatches: {
      type: "array",
      items: STAGE_RECOVERY_ENTRY_SCHEMA
    },
    retryPendingBatches: { type: "array", items: { type: "string" } },
    blockedBatches: { type: "array", items: { type: "string" } },
    rescheduledRetryBatches: { type: "array", items: { type: "string" } },
    manualRetryBatches: { type: "array", items: { type: "string" } },
    parallelGroups: { type: "array", items: { type: "array", items: { type: "string" } } },
    allParallelGroups: { type: "array", items: { type: "array", items: { type: "string" } } },
    maxParallel: { type: "number" },
    activeWorkers: { type: "number" },
    observedEpochMs: { type: "number" },
    dispatchGrants: { type: "object", additionalProperties: { type: "integer", minimum: 1 } },
    dispatchDiagnostics: {
      type: "object",
      properties: { activeBatchIds: { type: "array", items: { type: "string" } } },
      additionalProperties: true
    },
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
    },
    manifest: WORKFLOW_MANIFEST_SCHEMA
  },
  required: [
    "runId", "status", "scheduledGroups",
    "readyBatches", "allReadyBatches", "mergeableBatches",
    "implementationRecoveryBatches", "stageRecoveryBatches",
    "retryPendingBatches", "blockedBatches", "dispatchGrants",
    "parallelGroups", "allParallelGroups", "maxParallel",
    "batchTaskIds", "batchWorkspaces"
  ],
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
    evidence: { type: "object" },
    repairContextPath: { type: "string" },
    repairContextDigest: { type: "string" },
    affectedTaskIds: { type: "array", items: { type: "string" } }
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
    error: { type: "string" },
    repairContextPath: { type: "string" },
    repairContextDigest: { type: "string" },
    affectedTaskIds: { type: "array", items: { type: "string" } }
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
    status: { type: "string" },
    integrationMode: { type: "string" },
    integrationWorkspaces: { type: "object", additionalProperties: true },
    mainPromotion: { type: ["object", "null"], additionalProperties: true },
    passed: { type: "boolean" },
    commands: { type: "array", items: { type: "object" } },
    errorMessage: { type: "string" },
    errors: { type: "array", items: { type: "string" } },
    hasDeferredIssues: { type: "boolean" },
    hasBlockingDeferredIssues: { type: "boolean" },
    deferredIssues: { type: "array", items: { type: "object" } },
    skippedUtests: { type: "array", items: { type: "object" } }
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
    recoveredIncomplete: { type: "boolean" },
    error: { type: "string" }
  },
  required: ["success", "batchId", "repositoryRef"],
  additionalProperties: false
};
// prepare-test is a separate control-plane agent, not a UTest execution
// result. Declare both backend policy/context and frontend skip evidence so
// structured transports do not replace this handoff with a generic success.
const UTEST_PREPARATION_SCHEMA = {
  type: "object",
  properties: {
    ok: { type: "boolean" },
    success: { type: "boolean" },
    batchId: { type: "string" },
    status: { enum: ["test_required", "skipped", "failed"] },
    stage: { type: "string" },
    runtimeContractDigest: { type: "string" },
    utestPolicy: {
      type: "object",
      properties: {
        version: { type: "string" },
        stageSeconds: { type: "integer", minimum: 1 },
        firstExecutionSeconds: { type: "integer", minimum: 1 },
        commandSeconds: { type: "integer", minimum: 1 },
        strategyChanges: { type: "integer", minimum: 0 },
        testRepairs: { type: "integer", minimum: 0 },
        sourceRepairs: { type: "integer", minimum: 0 },
        indexSeconds: { type: "integer", minimum: 1 },
        digest: { type: "string" }
      },
      required: ["stageSeconds", "firstExecutionSeconds", "commandSeconds", "digest"],
      additionalProperties: true
    },
    engineeringContext: {
      type: "object",
      properties: {
        ok: { type: "boolean" }, status: { type: "string" },
        contextPath: { type: "string" }, contextDigest: { type: "string" },
        commandCatalogPath: { type: "string" },
        allowedReads: { type: "array", items: { type: "string" } },
        summary: { type: "object", additionalProperties: true },
        brief: { type: "object", additionalProperties: true }
      },
      additionalProperties: true
    },
    testStatus: { type: "string" },
    skipReason: { type: "string" },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    commitSha: { type: "string" },
    stageEvidenceId: { type: "string" },
    reused: { type: "boolean" },
    error: { type: "string" }
  },
  required: ["success", "batchId", "status"],
  additionalProperties: true
};
const UTEST_STAGE_SCHEMA = {
  type: "object",
  properties: {
    batchId: { type: "string" },
    status: { enum: ["success", "failed", "timeout", "deferred"] },
    testStatus: { enum: ["passed", "skipped", "deferred", "source_repair_required", "timed_out"] },
    skipReason: { type: "string" },
    worktreePath: { type: "string" },
    branchName: { type: "string" },
    commitSha: { type: "string" },
    testEvidenceIds: { type: "array", items: { type: "string" } },
    stageEvidenceId: { type: "string" },
    failureType: { type: "string" },
    nextStage: { type: "string" },
    failure: { type: "object" },
    testFailure: { type: "object" },
    errorMessage: { type: "string" },
    repairContextPath: { type: "string" },
    repairContextDigest: { type: "string" },
    affectedTaskIds: { type: "array", items: { type: "string" } }
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
    return parsed.parsed ? unwrap(parsed.value) : { raw: value, unparsedStructuredOutput: true };
  }
  if (value && typeof value === "object" && (
    Object.prototype.hasOwnProperty.call(value, "final_result")
    || (value.run_in_background === true && Object.prototype.hasOwnProperty.call(value, "task_id"))
  )) {
    // Background-command agents sometimes return the task envelope rather
    // than stdout itself. Expose the command's status and candidate metadata
    // before branching, including when either layer is serialized JSON.
    const result = unwrap(value.final_result);
    if (!value.final_result || !result || typeof result !== "object" || Array.isArray(result) || Object.keys(result).length === 0) {
      return { raw: value, unparsedStructuredOutput: true };
    }
    // Do not let a successful-looking payload erase an execution failure.
    // candidate_conflicted may legitimately exit 1; its dedicated recovery
    // branch runs before requireSuccess and consumes a fresh resolved result.
    return hasDirectFailureSignal(value) || hasDirectFailureSignal(unwrap(value.failure))
      ? { ...result, commandExecutionFailure: true, commandExitCode: value.exit_code }
      : result;
  }
  return value || {};
}

function isFailedVerdict(value) {
  return typeof value === "string"
    && ["fail", "failed", "error", "reject", "rejected"].includes(value.trim().toLowerCase());
}

function isFailedStatus(value) {
  return typeof value === "string"
    && ["failed", "error", "timeout", "blocked", "needs_triage", "incomplete", "needs_resolution", "candidate_conflicted"].includes(value.trim().toLowerCase());
}

function hasDirectFailureSignal(candidate) {
  return Boolean(candidate && (
    candidate.ok === false
    || candidate.success === false
    || candidate.passed === false
    || candidate.commandExecutionFailure === true
    || (candidate.exit_code != null && candidate.exit_code !== 0 && candidate.exit_code !== "0")
    || isFailedVerdict(candidate.verdict)
    || isFailedStatus(candidate.status)
  ));
}

function hasFailureSignal(value) {
  const result = unwrap(value);
  const failure = unwrap(result.failure);
  return [result, failure].some(hasDirectFailureSignal);
}

function requireSuccess(value, label) {
  const result = unwrap(value);
  if (!result || result.unparsedStructuredOutput === true || hasFailureSignal(result)) {
    throw new Error(`${label} failed: ${JSON.stringify(result)}`);
  }
  return result;
}

function requireUtestPreparation(value, batchId) {
  const result = requireSuccess(value, `prepare test ${batchId}`);
  if (result.success !== true || !usableString(result.batchId)
      || !["test_required", "skipped"].includes(result.status)) {
    throw new Error(`utest_preparation_invalid:${batchId}:${JSON.stringify({
      success: result.success, batchId: result.batchId, status: result.status, fields: Object.keys(result)
    })}`);
  }
  if (result.batchId !== batchId) {
    throw new Error(`utest_preparation_batch_mismatch:${batchId}:actual=${result.batchId}`);
  }
  if (result.status === "skipped" && (result.testStatus !== "skipped"
      || ![result.skipReason, result.worktreePath, result.branchName, result.commitSha].every(usableString))) {
    throw new Error(`utest_preparation_invalid:${batchId}:skip_evidence_incomplete`);
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

function requireReviewPassed(value, batchId) {
  const result = unwrap(value);
  if (!isFinalReviewDecision(result, batchId) || result.status !== "passed" || hasFailureSignal(result)) {
    throw new Error(`review_not_passed:${result.status || "missing"}:${batchId}:${JSON.stringify(result)}`);
  }
  return result;
}

function usableString(value) {
  return typeof value === "string"
    && value.trim().length > 0
    && !["undefined", "null"].includes(value.trim().toLowerCase());
}

function requireSchedulerResult(value, label) {
  const result = unwrap(value);
  // Run status is domain state. A successful status read can report blocked
  // while independent Batches remain eligible; command failures still stop.
  requireSuccess({ ...result, status: undefined }, label);
  const isObject = candidate => candidate && typeof candidate === "object" && !Array.isArray(candidate);
  if (
    !usableString(result.runId)
    || !usableString(result.status)
    || !Array.isArray(result.scheduledGroups)
    || !Array.isArray(result.readyBatches)
    || !Array.isArray(result.allReadyBatches)
    || !Array.isArray(result.mergeableBatches)
    || !Array.isArray(result.implementationRecoveryBatches)
    || !Array.isArray(result.stageRecoveryBatches)
    || !Array.isArray(result.retryPendingBatches)
    || !Array.isArray(result.blockedBatches)
    || !Array.isArray(result.parallelGroups)
    || !Array.isArray(result.allParallelGroups)
    || !Number.isInteger(result.maxParallel)
    || result.maxParallel <= 0
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
    ...(Array.isArray(result.implementationRecoveryBatches) ? result.implementationRecoveryBatches.map(item => item && item.batchId) : []),
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
  const missingGrants = [...referencedBatchIds].filter(batchId =>
    !Number.isInteger((result.dispatchGrants || {})[batchId]) || result.dispatchGrants[batchId] < 1);
  if (missingGrants.length) throw new Error(`parallel_scheduler_dispatch_grants_incomplete:${missingGrants.join(",")}`);
  const invalidStageRecovery = result.stageRecoveryBatches.find(recovery => (
    !isObject(recovery)
    || !usableString(recovery.batchId)
    || recovery.recoveryKind !== "stage_resume"
    || typeof recovery.preserveWorktree !== "boolean"
    || typeof recovery.reprovision !== "boolean"
    || !usableString(recovery.worktreePath)
    || !usableString(recovery.branchName)
    || !usableString(recovery.commitSha)
    || !["prepare", "implement", "review", "test"].includes(recovery.nextStage)
    || (recovery.failureContext !== undefined && (
      !isObject(recovery.failureContext)
      || !usableString(recovery.failureContext.failedStage)
      || !usableString(recovery.failureContext.failureType)
      || !usableString(recovery.failureContext.message)
    ))
  ));
  if (invalidStageRecovery) {
    throw new Error(JSON.stringify({
      error: "parallel_scheduler_stage_recovery_invalid",
      label,
      runId: result.runId,
      recovery: invalidStageRecovery,
    }));
  }
  const invalidImplementationRecovery = result.implementationRecoveryBatches.find(recovery => (
    !isObject(recovery)
    || !usableString(recovery.batchId)
    || recovery.recoveryKind !== "implementation_resume"
    || recovery.resumeFromStage !== "implement"
    || typeof recovery.preserveWorktree !== "boolean"
    || typeof recovery.reprovision !== "boolean"
    || !usableString(recovery.worktreePath)
    || !usableString(recovery.branchName)
  ));
  if (invalidImplementationRecovery) {
    throw new Error(JSON.stringify({
      error: "parallel_scheduler_implementation_recovery_invalid",
      label,
      runId: result.runId,
      recovery: invalidImplementationRecovery,
    }));
  }
  // The scheduler categorizes these lists after validating ownership and
  // durable state.  Their category is the authority for Worktree semantics;
  // do not let a structured-agent boolean default turn a stage resume into a
  // reprovision request.  Coordinates and nextStage above remain mandatory.
  return {
    ...result,
    stageRecoveryBatches: result.stageRecoveryBatches.map(recovery => ({
      ...recovery,
      preserveWorktree: true,
      reprovision: false,
    })),
    implementationRecoveryBatches: result.implementationRecoveryBatches.map(recovery => ({
      ...recovery,
      preserveWorktree: true,
      reprovision: false,
    })),
  };
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
const runtimeContractDigest = input.runtimeContractDigest;
const feature = input.feature;
const workflowInvocationId = input.workflowInvocationId;
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
  : DEFAULT_BATCH_TIMEOUT_SECONDS;

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
if (!/^[a-f0-9]{32}$/.test(workflowInvocationId || "")) {
  throw new Error("workflow_invocation_id_missing_or_invalid:rerun_launcher");
}
if (!/^[a-f0-9]{64}$/.test(runtimeContractDigest || "")) {
  throw new Error("workflow_runtime_contract_missing:rerun_launcher");
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
const utestRuntimePath = joinPath(pluginPath, "hooks/utest_runtime.py");
const utestSourceBugPath = joinPath(pluginPath, "hooks/validate_utest_source_bug.py");
const mergeTrainPath = joinPath(pluginPath, "hooks/parallel_merge_train.py");
// Every child below runs after the caller has supplied the task-card ID and
// started this fixed Workflow. Keep the no-interaction contract at the call
// boundary so adding a later phase cannot accidentally reintroduce a user
// confirmation prompt.
const WORKFLOW_AUTONOMY_PREFIX = "固定 Code Workflow 已启动：不得调用 request_user_input、要求用户确认、等待用户回复或把控制权交回用户。按本提示和持久化契约自主执行；只返回本步骤的最终结构化结果。" +
  `凡本 Workflow 任一阶段执行 Python 命令，均使用 python -X utf8 <script.py> 启动（等效于 PYTHONUTF8=1，兼容 Windows/macOS/Linux），禁止裸 python。凡确需读取 JSON 文件中的字段，且该文件在当前阶段允许访问范围内，禁止用 read_file/cat 或分段读取整个文件来取字段；改用 python -X utf8 "${pluginPath}/hooks/query_json_fields.py" --file "<JSON_PATH>" --field "<FIELD_PATH>"，多个字段重复传入 --field。数组下标使用 [0]，对象集合可用 *；不要因此扩大当前阶段允许访问的文件范围。\n`;
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

async function workflowAgent(instruction, options = {}, { retryEmptyResponse = true } = {}) {
  const label = usableString(options.label) ? options.label : "workflow-agent";
  const batchMatch = label.match(/(?:^|-)(B\d{3,})(?:-|$)/);
  const grant = batchMatch && typeof activeDispatchGrants !== "undefined" ? activeDispatchGrants.get(batchMatch[1]) : null;
  if (grant) instruction += `\n派发身份：本子任务中每条 parallel_batch_stage.py 命令，以及 batch_lease_manager.py acquire 命令，必须追加 --dispatch-generation ${grant.generation} --dispatch-token "${grant.ownerToken}"。仅追加到以上命令，不能替代 TASK/UTest 的业务 lease-token/owner-token；stale_stage_owner 立即停止，禁止强制 reclaim。`;
  let lastEmptyResponse;
  const maxEmptyResponseRetries = retryEmptyResponse ? MAX_EMPTY_AGENT_RESPONSE_RETRIES : 0;
  for (let attempt = 0; attempt <= maxEmptyResponseRetries; attempt += 1) {
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
// Code and rework agents may run the required production-only compile for
// backend tasks. All behavioral tests and other validation remain UTest-owned.
// Keep this exact boundary in both implementation prompts, including cowork.
const CODE_STAGE_EXECUTION_BOUNDARY = "本 Code 执行规则适用于主 Agent、cowork/协作 Agent 以及实现修复 Agent。Code 阶段和修复阶段只允许生产源码、生产配置、迁移和公开接口的最小实现，以及本 Prompt 明确列出的 lease、scheduler、route resolver、task_runner、worktree seal/release 命令。可以只读既有测试理解契约，但在 task_runner.py finish-implementation 成功前，禁止创建、修改或删除测试源码（包括 src/test、test、tests、__tests__）、fixture/mock、测试环境或测试配置。后端 lane 的每个 TASK 在实现或修复生产代码后、finish-implementation 前，必须使用 Bash 在该 TASK 的业务 Worktree 执行项目文档/构建配置指定的 production-only compile 命令；只编译生产代码，不得连带运行测试、打包、typecheck 或 lint。记录精确命令和完整输出；失败时根据真实输出定位根因、修复生产代码并重编，直到通过；编译命令或环境无法使用时保留真实错误并返回失败，禁止假报通过或完成。前端 lane 不执行此新增编译门槛。除这项后端 compile-only 门槛外，Code/修复阶段禁止自行执行构建、打包、typecheck、lint、测试、E2E 或 TASK validation 命令，也禁止为此启动后台进程。包括但不限于 mvn/mvnw（package、verify、test）、Gradle/gradlew（build、assemble、check、test）、npm/pnpm/yarn（build、typecheck、lint、test）、npx/vite/webpack/tsc/vue-tsc/eslint、jest、vitest、pytest；Maven/Gradle 等只允许使用对应的生产 compile-only 目标，不得使用会触发测试或打包的复合目标。所有测试资产和行为验证仅可在 Review 通过后的 UTest 阶段由 UTest 协议运行；不得以“验证实现”为由绕过该阶段。\n";

const CODE_STAGE_COMPILE_COMMAND_GUIDANCE = "编译和环境查询使用前台 Bash。guard 按实际程序、目标和 TASK/Batch 绑定检查，不要求固定某行命令；支持字面量 export/环境赋值、cd、分行/分号/&&、输出重定向和 head/tail/tee，以及 PIPESTATUS 保存、echo/printf 和 exit。mvn 的 -q/-o/-D 参数不是拦截理由。使用当前 TASK 的业务模块 cwd/workdir；相对 cwd 必须由工具提供真实绝对 workspaceRoot，不能猜执行工具根目录；也可在同一次执行中使用 cd \"<模块目录>\" && ... 或 cd \"<模块目录>\" || exit \"$?\"。默认使用 PATH 中的 mvn，允许 mvn -v/--version/--help，不要求查找 mvn.exe；只有命令不存在或启动失败才检查安装。后端 TASK 的生产源码写完后，先用同一个 task_runner.py prepare-compile 登记：携带全部已有 TASK/workspace/parallel-run-id/lease-token/workspace-ref 参数、--run-id <当前 TASK 的真实 runId>、--execution-cwd \"<实际模块绝对目录>\"，在 -- 后传实际 compile-only argv；保存返回的 attemptId。prepare-compile 只冻结源码和命令，不执行编译。随后单独用 Bash 执行同一 argv，2>&1 | tee 到工作树之外已存在的临时日志目录；管道后立即保存 compile_status=(\"${PIPESTATUS[@]}\")，用 printf 输出两个退出码，最后 exit \"$((compile_status[0] != 0 ? compile_status[0] : compile_status[1]))\"。不能只追加 echo EXIT 后把工具成功当成编译通过。再独立调用 task_runner.py record-compile，携带同一 TASK/run 参数、--attempt-id <真实 attemptId>、--exit-code <实际编译退出码>、--log-exit-code <实际 tee 退出码>、--output-file \"<临时完整日志>\"；它只登记，不执行编译。record-compile 的 ok 只表示登记成功，必须读取 passed/exitCode/logExitCode；编译失败时依据实际日志修复生产代码，重新 prepare 和编译。登记失败保留输出，只重试登记，不能自动重跑编译。finish-implementation 检查当前后端 TASK/run 最新成功 Evidence 和源码快照；编译后又修改代码必须重新编译，前端不新增编译门槛。若 guard 拦截，必须展示 GUARD_DIAGNOSTICS.failures 中具体 code/reason/line/statement 和 executionCwd、taskRuns；先修复该具体原因，不得盲目删除 -o 或反复试命令。GUARD_DIAGNOSTICS.guardVersion/guardPath 用于核对 Windows 实际加载的插件；工具自身 cwd 拒绝和插件 guard 拒绝分别报告。task_runner start/start-task-repair/resume 自动登记 implement，旧 Run 的 activeStage=none 本身不禁止绑定正确的活动 TASK 纯编译。禁止 test/package/verify 等非生产编译目标、额外验证命令、动态执行和后台进程；不得切中立目录绕过 guard 或直接修改 Runtime JSON。\n";

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
  requireSuccess(await workflowAgent(
    `只执行 python -X utf8 "${joinPath(pluginPath, "hooks/engineering_context.py")}" contract --expected-digest "${runtimeContractDigest}"。原样返回 JSON；失败时停止并重新运行 launcher，不创建 Batch、不探索工程、不修改任何文件。`,
    { label: "verify-engineering-deployment", phase: "准备" }
  ), "engineering deployment");
  const prepareCommand = resumeMode === "manual"
    ? `python -X utf8 "${schedulerPath}" manual-resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${resumeRunId}"`
    : `python -X utf8 "${schedulerPath}" ensure ` +
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
    `新 run 使用集成 Worktree，禁止提交或修改源 checkout；仅旧 direct run 沿用 bootstrap 规则。` +
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
if (["awaiting_user_confirmation", "promotion_prepared", "promoting_main", "integration_retained",
     "integration_sync_required", "integration_sync_conflicted"].includes(prepared.status)) {
  return { ok: !prepared.status.startsWith("integration_sync"), feature, runId,
    finalStatus: prepared.status, integrationWorkspaces: prepared.integrationWorkspaces,
    mainPromotion: prepared.mainPromotion, nextAction: "review_integration_and_confirm_promotion" };
}

let retryRecoveryInFlight = null;
async function recoverPendingRetries(scheduler, label, phaseName = "准备") {
  if (!retryPendingBatchesOf(scheduler).length) return scheduler;
  if (retryRecoveryInFlight) return retryRecoveryInFlight;
  // A normal ensure/resume already performs this transition.  One immediate,
  // explicit retry makes the Workflow resilient to an interrupted lease
  // handoff while still letting the scheduler remain the sole state owner.
  const recovery = (async () => {
    const result = await workflowAgent(
      `执行 python -X utf8 "${schedulerPath}" resume --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
      `恢复所有 retry_pending Batch，并在返回前清理其残留 lease；必须原样返回该命令 stdout 的完整 JSON，不得自行概括或重建 batchWorkspaces。`,
      { label, phase: phaseName, schema: SCHEDULER_RESULT_SCHEMA }
    );
    return requireSchedulerResult(result, label);
  })();
  retryRecoveryInFlight = recovery;
  try {
    return await recovery;
  } finally {
    if (retryRecoveryInFlight === recovery) retryRecoveryInFlight = null;
  }
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
let implementationRecoveryBatches = (prepared.implementationRecoveryBatches || []).filter(result => result && usableString(result.batchId));
let stageRecoveryBatches = (prepared.stageRecoveryBatches || []).filter(result => result && usableString(result.batchId));
let retryPendingBatches = retryPendingBatchesOf(prepared);
const retryPriorityBatchIds = new Set(
  [
    ...(Array.isArray(prepared.rescheduledRetryBatches) ? prepared.rescheduledRetryBatches : []),
    ...(Array.isArray(prepared.manualRetryBatches) ? prepared.manualRetryBatches : []),
  ].filter(isValidBatchId)
);
let dispatchGrants = prepared.dispatchGrants || {};
const activeDispatchGrants = new Map();
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
let schedulerReadInFlight = null;
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
  dispatchGrants = scheduler.dispatchGrants || {};
  cacheSchedulerFallbackGroups(scheduler);
  scheduledGroups = normalizeScheduledGroups(scheduler.scheduledGroups || []);
  mergeableBatches = (scheduler.mergeableBatches || []).filter(isValidBatchId);
  implementationRecoveryBatches = (scheduler.implementationRecoveryBatches || []).filter(result => result && isValidBatchId(result.batchId));
  stageRecoveryBatches = (scheduler.stageRecoveryBatches || []).filter(result => result && isValidBatchId(result.batchId));
  retryPendingBatches = retryPendingBatchesOf(scheduler);
  for (const batchId of Array.isArray(scheduler.rescheduledRetryBatches) ? scheduler.rescheduledRetryBatches : []) {
    if (isValidBatchId(batchId)) retryPriorityBatchIds.add(batchId);
  }
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

async function readSchedulerState(label, phaseName = "准备", { poll = false } = {}) {
  if (schedulerReadInFlight) return schedulerReadInFlight;
  schedulerReadInFlight = (async () => {
    try {
      const state = requireSchedulerResult(await workflowAgent(
        `执行 python -X utf8 "${schedulerPath}" status --workflow-view --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
        `这是调度器输出的固定工作流字段视图；不得恢复 retry_pending、修改业务代码、创建 Worktree 或运行 TASK。只原样返回该命令 stdout 的 JSON；不得读取完整 manifest 后手工汇总、推断 scheduledGroups，或重建/省略 batchWorkspaces 的 workspaceRef。` +
        `本读取器供准备、调度、Batch、候选验证、最终修复和最终报告的所有阶段共用（包括 final-repair-initial、final-repair-before-retry、final-repair-after-drain-* 与 final-report-snapshot）。状态必须通过此 status --workflow-view 命令取得；若需单独检查视图未包含的清单字段，另用 python -X utf8 "${pluginPath}/hooks/query_json_fields.py" --file "${artifactWorkspace}/.autobizdevops/features/${feature}/.parallel-runs/${runId}/manifest.json" --field "batches.<BATCH_ID>.status" 查询，不得读取完整 manifest，也不得用查询结果重建或替换 status 命令的工作流状态。`,
        { label, phase: phaseName, schema: SCHEDULER_RESULT_SCHEMA },
        { retryEmptyResponse: !poll }
      ), label);
      const view = state.manifest;
      const missingBatches = view && view.batches && typeof view.batches === "object"
        ? Object.keys(state.batchTaskIds).filter(batchId => !view.batches[batchId])
        : Object.keys(state.batchTaskIds);
      if (!view || !view.batches || !view.mergeTrains || !Array.isArray(view.deferredIssues) || missingBatches.length) {
        throw new Error(`parallel_scheduler_manifest_view_incomplete:${missingBatches.join(",")}`);
      }
      applySchedulerState(state);
      markSchedulerRecovered();
      return state;
    } catch (error) {
      recordSchedulerFailure(label, error);
      // A periodic read can wait for the next interval. Do not retry now or
      // dispatch from a fallback snapshot merely because this poll failed.
      if (poll) return null;
      // A blank model completion did not produce a scheduler verdict. Continue
      // only from the last proven-safe fallback; malformed or real command
      // failures never authorize dispatch from stale grants.
      if (emptyAgentResponseFailure(error)) return schedulerSnapshotFallback(label, error);
      return null;
    }
  })();
  try {
    return await schedulerReadInFlight;
  } finally {
    schedulerReadInFlight = null;
  }
}

async function deferBatchForRetry(batchId, batchWorktree, batchBranch, reason) {
  const grant = activeDispatchGrants.get(batchId);
  if (!grant) throw new Error(`parallel_dispatch_grant_missing:${batchId}`);
  const finalized = await dispatchCommand("defer", { batchId, ...grant },
    `--error-b64 "${encodeFailure(reason)}"`, `defer-batch-retry-${batchId}`);
  return { batchId, status: finalized.status, reason: String(reason || "batch_execution_failed"),
    finalized, durable: finalized.status !== "stale_owner_ignored" };

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
      `清理已交付 Batch 的插件原生 Worktree。执行 python -X utf8 "${lifecyclePath}" cleanup-merged ` +
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
  for (const field of ["repairContextPath", "repairContextDigest", "affectedTaskIds"]) {
    if (normalized[field] !== undefined) failureContext[field] = normalized[field];
    else if (failure[field] !== undefined) failureContext[field] = failure[field];
  }
  if (failedStage === "test") {
    const testFailure = unwrap(normalized.testFailure || failure);
    for (const field of ["targetId", "commandId", "evidenceId", "testFile", "argv", "command", "cwd", "evidenceLogPath", "repairContextPath", "repairContextDigest", "affectedTaskIds"]) {
      if (testFailure && testFailure[field] !== undefined) {
        failureContext[field] = testFailure[field];
      }
    }
  }
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
      repairContextPath: context.repairContextPath,
      repairContextDigest: context.repairContextDigest,
      affectedTaskIds: context.affectedTaskIds,
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

async function runBatchUtestAndSeal(batchResult, retryFailure = null) {
  const batchId = batchResult.batchId;
  const batchWorktree = batchResult.worktreePath;
  const batchBranch = batchResult.branchName;
  const batchWorkspace = batchWorkspaces[batchId] || {};
  const batchWorkspaceRef = batchWorkspace.workspaceRef;
  const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
  if (!usableString(batchWorktree) || !usableString(batchBranch) || !usableString(batchWorkspaceRef) || !taskIds.length) {
    throw new Error("utest_batch_context_missing:" + batchId);
  }
  const preparation = requireUtestPreparation(await workflowAgent(
    `读取并登记 Batch ${batchId} 的 UTest 执行策略。只执行 python -X utf8 "${stagePath}" prepare-test ` +
    `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。` +
    `当前前端 UTest 暂时禁用：插件按真实 executionLane 将前端登记为 skipped 并返回原因；本步骤不执行测试、环境初始化、测试生成或编译。后端返回 test_required。` +
    `将命令 stdout 的 JSON 对象直接作为结构化返回：success、batchId、status 必须保留在顶层，不得包装到 result/data/value 或只返回成功摘要；后端完整保留 runtimeContractDigest、utestPolicy 和 engineeringContext，前端完整保留 testStatus、skipReason、worktreePath、branchName、commitSha、stageEvidenceId。禁止自行启动测试或修改源码。`,
    { label: `prepare-utest-${batchId}`, phase: "Batch 阶段", schema: UTEST_PREPARATION_SCHEMA }
  ), batchId);
  if (preparation.status === "skipped") {
    return { ...preparation, status: "success" };
  }
  if (typeof runtimeContractDigest !== "undefined" && preparation.runtimeContractDigest !== runtimeContractDigest) {
    throw new Error("utest_runtime_deployment_changed:rerun_launcher:" + batchId);
  }
  const taskIdArgs = taskIds.map(taskId => "--task-id \"" + taskId + "\"").join(" ");
  const taskList = JSON.stringify(taskIds);
  // A command gets only a fraction of the UTest wall-clock budget.  The
  // runner clamps it again using the durable `test.startedAt` deadline.
  const utestPolicy = preparation.utestPolicy;
  if (!utestPolicy || !Number.isInteger(utestPolicy.stageSeconds) || !Number.isInteger(utestPolicy.firstExecutionSeconds)
      || !Number.isInteger(utestPolicy.commandSeconds) || !usableString(utestPolicy.digest)
      || utestPolicy.stageSeconds <= 0 || utestPolicy.firstExecutionSeconds <= 0 || utestPolicy.commandSeconds <= 0) {
    throw new Error("utest_runtime_policy_missing:" + batchId);
  }
  const utestStageTimeout = Math.min(utestPolicy.stageSeconds, timeoutPerBatch);
  const utestCommandTimeout = Math.min(utestPolicy.commandSeconds, utestStageTimeout);
  const retryInstruction = retryFailure && typeof retryFailure === "object"
    ? "这是 source repair 后的受控重跑。只允许执行此前失败的精确命令，不得创建新测试、选择其他 target、换 runner 或扩大范围。若仍为 source_compile/source_bug，绝不再执行第二次 production repair；按步骤 7 以 failure-type implementation 记录 deferred UTest issue 后继续。失败基线=" + JSON.stringify(retryFailure) + "。\n"
    : "";
  const prompt =
    "在 Batch " + batchId + " 的原生 Git worktree \"" + batchWorktree + "\"、分支 \"" + batchBranch + "\" 内完成该 Batch 的 UTest。TASK=" + taskList + "。测试点只来自这些 TASK 的 UTEST_ASSIGNMENT/testIntent；不得读取或修改其他 Batch、主 checkout、计划 JSON 或平台产物，但可只读本次 runner 返回的失败日志。本阶段总预算为 " + utestStageTimeout + " 秒；到期或任一 runner 返回 executionOutcome=command_timeout/stage_deadline_exceeded 时，立即收口，禁止继续探索。\n" +
    retryInstruction +
    "先继承已验证的探索成果，只补查缺失或失效部分；自主验证要求当前断言和真实执行证据，不要求从零探索。缓存和历史 PASS 不能替代当前测试 Evidence。已准备工程上下文=" + JSON.stringify(preparation.engineeringContext) + "。先消费其 brief；完整内容按 contextPath/allowedReads 准确读取，禁止重建整个工程认知。\n" +
    "工程上下文由 utest_runtime.py prepare 的 engineeringContext 返回；先读 contextPath，再按需读 allowedReads 和 commandCatalogPath，保持 UTEST_ASSIGNMENT 原文不变。复用 Code 的实际变更、API 事实与静态隔离配方；新增查询使用同目录 engineering_context.py 的 query/resolve，携带当前 run/Batch/worktree/TASK、--stage utest 和真实 owner-token。未知符号先 locate，再 query；同问题 status=in_progress 时先做独立工作，按 retryAfterSeconds 有限重查，不重复 grep，不延长阶段预算。测试修复使用 --stage fix、原 target/argv；失败方案用 record-attempt 登记，源代码问题由 Runtime 生成修复包。命中事实不代表测试通过，source_observed 配方仍须实际验证。\n" +
    "范围护栏：除本 Batch worktree、上述 TASK 的 router 原文、engineeringContext 返回并校验身份的 contextPath/allowedReads/commandCatalogPath、本提示给出的插件命令，以及本次 runner 返回的 logPath/evidenceLogPath（仅失败后只读本 target 的命令段）外，不得访问任何路径。禁止运行 git worktree list、find、grep -R、全仓库 rg/glob，禁止按 B00*、测试目录或 .autobizdevops 目录枚举其他 Batch；不得 cd 到本 Batch worktree 外。测试文件必须是本 worktree 下、与当前 TASK assignment 对应的仓库根相对路径。已有测试资产先按 assignment 运行并复用，只有当前 TASK 的测试/fixture/mock/测试配置失败时才可修改；不得为寻找测试而扫描仓库。\n" +
    "这是 Code Review 之后的测试阶段：Review 只审业务生产代码；现在由你生成/补齐测试源码、fixture/mock/测试环境配置并运行测试。测试代码必须留在当前 Worktree，并会随本 Batch 再次封存后合并；禁止把测试拆成独立 Batch。\n" +
    "严格执行：1) cd 到该 Worktree，确认 git 顶层与分支匹配；2) 执行 python \"" + leasePath + "\" acquire --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --ttl-seconds " + timeoutPerBatch + " --lease-guard，保存 lease.ownerToken；3) 执行 python \"" + stagePath + "\" start --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test；4) 执行 router 与环境检查。若 status=environment_target_ambiguous，只能依据 TASK 与 inspector 返回的候选路径选择；选定时以 --task-id 和 --environment-target TASK_ID=ENVIRONMENT_TARGET_ID 重跑该 TASK 的环境检查，无法确定时按 environment 阻断收口，不得修改 Plan 或初始化全部候选。status 非 ready 或所选 preset 缺能力时，可在当前 test 阶段补齐已选模块的测试配置/fixture/mock/helper，以及 pom.xml 的 test scope 依赖和必要 Surefire/Failsafe 配置一次并重新检查；测试依赖写入不要求重开生产 TASK，guard 和 seal 按实际 XML 差异授权，保留生产依赖、parent/BOM、共享属性、profiles；外部依赖、JDK、权限、网络或第二次失败立即按步骤 7 收口。禁止自行运行 heartbeat、run_in_background、&、nohup 或 Start-Process。\n" +
    "5) 对每个 TASK 执行 python \"" + utestRuntimePath + "\" prepare --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --batch-worktree \"" + batchWorktree + "\" --task-id <TASK_ID> --owner-token <真实token> --stage-timeout " + utestStageTimeout + "。复用返回 preset、环境和预算；JUnit/Mockito 配合，保留既有 JUnit。缺测试依赖用相同绑定参数的 apply-pom --content-file <临时完整POM>，无需 task_runner start。执行前确有证据才 select 一次，执行后冻结策略。静态链路无法隔离记 testability_gap；mapper/H2 不替代 service 授权覆盖。禁止本地 Maven 仓库递归扫描；查类先用 lookup-class --classpath-file <当前模块已有snapshot> --class-name <完整包名>，需要源码或字节码时使用 inspect-class 和同一绑定参数；缺 snapshot 时只做一次受控 dependency:build-classpath，不扫描仓库。所有 Maven 命令（含 -v、tree/build-classpath/resolve/effective-pom、编译和测试）必须由 Bash 直接执行，禁止 Python 原生 subprocess 启动 mvn，也不得要求用户制造 mvn.exe 包装器。Maven setup 用 utest_runtime.py preflight-setup，携带与 prepare 相同的 workspace/feature/run-id/batch-id/batch-worktree/task-id/owner-token/stage-timeout 及 command-timeout，在 -- 后传精确 argv；status=prepared 时原样执行返回 bashCommand，status=cached 时复用实际结果，不重跑。Bash 实时 tee 输出并捕获命令/tee 退出码，runner 仅 --kind setup --record-only 登记。setup 不算行为测试、不重置首次真实测试期限；成功/失败缓存复用。未知或缺离线依赖不反复换版本；仅外部环境变化才 refresh-diagnostics。\n" +
    "首次命令期限为 " + utestPolicy.firstExecutionSeconds + " 秒，Runtime 的总期限/计数不因恢复或续 lease 重置。prepare/preflight 返回 blocked 后立即按步骤 7/8 记录 deferred、封存有效测试资产、释放 lease；不得继续读插件源码寻找放行路径。文件落地后执行 python \"" + utestRuntimePath + "\" preflight --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --batch-worktree \"" + batchWorktree + "\" --task-id <TASK_ID> --owner-token <真实token> --stage-timeout " + utestStageTimeout + " --command-timeout " + utestCommandTimeout + " --test-file <文件> -- <精确argv>；原样执行返回 bashCommand。生成脚本属于 Runtime：只执行，不手改；它重新校验执行绑定、限制子进程、捕获 PIPESTATUS/tee 状态并通过 record-only 登记。零/未知实际用例数不得 PASS。test_bug/source_repair 受控重跑用对应 retry-kind，绝不直接重放旧脚本。awaiting_record 仅补记原输出。\n" +
    "6) 对每个实际 TASK 生成或补齐行为测试：覆盖 Plan V2 的 testPoints、verificationIntent 与 writer 绑定的 AC；implementationPoints 只定位行为，nonGoals 不是 V2 必需输入。每个 target 初次只能执行一次真实、精确的测试 argv，并直接在 Bash 执行；不得让 Python runner 启动 Maven/Gradle/Node 测试进程。将合并输出通过 tee 写入临时文件，立即捕获测试进程退出码，再调用 python \"" + utestCommandPath + "\" --kind test --record-only --exit-code <BASH_EXIT_CODE> --output-file <临时输出文件> --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --task-id <真实TASK_ID> --batch-worktree \"" + batchWorktree + "\" --test-file <仓库根相对测试文件> -- <与 Bash 完全相同的真实测试 argv>。runner 只验证绑定、登记 Bash 的真实退出码/输出并写 Evidence/target；不得重跑测试。只有 runner 成功登记后删除临时输出文件；登记失败时保留输出文件，禁止重新执行测试。不得运行宽泛命令。若 failureClassification=test_failure 且根因位于测试资产，可修一次并只重跑同一精确命令一次。若 failureClassification=source_compile，或正常退出的失败证据经 validate_utest_source_bug 证明为 source_bug，立即停止：禁止换 runner、执行第二个测试文件或继续搜索绕过方案。\n" +
    "7) 每次失败都保留真实 runner 输出与 Evidence。先只读本次 runner 返回的 evidenceLogPath；若内容截断导致根因缺失，再只读 logPath 中本 target 的本次命令段。第一次 source_compile/source_bug 时，先按步骤 8 的封存命令 seal 新增测试资产（同样使用 --commit-stage utest 和本次测试变更摘要），再执行 python \"" + stagePath + "\" fail --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test --failure-type implementation --message \"<targetId、commandId、evidenceId、test-output.log、明确 file:line 编译根因>\"；释放 lease 后返回 testStatus:\"source_repair_required\" 和 testFailure。Workflow 会在同一 Worktree 受控修生产代码、登记一次性修复收口，并且只重跑这个原命令。若该受控重跑仍为 source_compile/source_bug，或 timeout、外部 environment 或耗尽一次测试资产修复，按步骤 8 的同一封存命令 seal 后执行 record-test-failure（failure-type implementation、environment 或 test_definition）并返回 testStatus:\"timed_out\" 或 \"deferred\"；该 issue 会随交付保留，但不阻断后续 gate、候选合并与最终 B-E2E。\n" +
    "8) 全部 UTest 通过后，执行 python \"" + worktreeManagerPath + "\" --json seal --commit-stage utest --commit-summary \"<根据本次实际测试变更填写中文说明，不写测试通过>\" --artifact-workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --repo \"" + batchWorktree + "\" --owner-token <真实token> 取得新的 commitSha；再执行 python \"" + stagePath + "\" complete --workspace \"" + artifactWorkspace + "\" --feature \"" + feature + "\" --run-id \"" + runId + "\" --batch-id \"" + batchId + "\" --stage test --metadata-json <包含 batchCommit、新 commitSha、testEvidenceIds、worktreePath、branchName 的对象>；最后以 final-status sealed 释放 lease。\n" +
    "成功只返回 {batchId,status:\"success\",testStatus:\"passed\",worktreePath,branchName,commitSha,testEvidenceIds,stageEvidenceId}。source repair 或受控 deferred 也必须返回 status:\"success\"，但必须带 testStatus 与 testFailure={message,targetId,commandId,evidenceId,testFile,argv,command}；不得手工 git add/commit、merge、rebase 或删除 Worktree。";
  // Independent deadline checks; the dispatch poll can remain thirty minutes.
  let finished = false;
  let timer;
  let wake;
  const child = workflowAgent(prompt,
    { label: "stage-utest-" + batchId, phase: "Batch 阶段", timeoutMs: utestStageTimeout * 1000, schema: UTEST_STAGE_SCHEMA }
  ).finally(() => { finished = true; clearTimeout(timer); if (wake) wake(); });
  const monitor = async () => {
    let seconds = Math.min(utestPolicy.firstExecutionSeconds, utestStageTimeout);
    while (!finished) {
      await new Promise(resolve => { wake = resolve; timer = setTimeout(resolve, seconds * 1000); });
      if (finished) return;
      const check = requireSuccess(await workflowAgent(
        `只执行 python -X utf8 "${stagePath}" check-test-budget --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。原样返回 JSON；该 Runtime 按固化期限请求停止，但保留封存资产所需 lease。不得清租约、修改代码或执行测试。`,
        { label: `utest-budget-${batchId}`, phase: "Batch 阶段" }
      ), `UTest budget ${batchId}`);
      if (check.status === "stop_requested" || check.status === "terminal") return;
      seconds = Math.max(1, Number(check.nextCheckSeconds) || 30);
    }
  };
  const results = await Promise.allSettled([child, monitor()]);
  for (const result of results) if (result.status === "rejected") throw result.reason;
  return unwrap(results[0].value);
}

async function runDeliveryReviewTestAndGate(batchResult, options = {}) {
  const reviewResolvedByRepair = options.reviewResolvedByRepair === true;
  const testResolvedByRepair = options.testResolvedByRepair === true;
  const retryTestFailure = options.retryTestFailure && typeof options.retryTestFailure === "object"
    ? options.retryTestFailure
    : null;
  const batchId = batchResult.batchId;
  const batchWorktree = batchResult.worktreePath;
  const batchBranch = batchResult.branchName;
  const commitSha = batchResult.commitSha;
  const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
  if (!usableString(commitSha)) throw new Error(`sealed_batch_commit_missing:${batchId}`);
  const metadata = JSON.stringify({ batchCommit: commitSha, worktreePath: batchWorktree, branchName: batchBranch });
  if (!reviewResolvedByRepair && !testResolvedByRepair) {
    const stageResult = requireSuccess(await workflowAgent(
      `收口 Batch ${batchId} 的 implement 阶段。task_runner 在 TASK 启动/恢复成功前已自动登记 prepare 与运行中的 implement；不要在实现完成后补写 start。执行 ` +
      `python -X utf8 "${stagePath}" finalize-implementation --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。Runtime 会校验全部 TASK 已收口并使用已封存的真实 commitSha 写阶段 Evidence；旧 Run 的阶段登记由 Runtime 校验后兼容恢复。只原样返回 JSON。`,
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
        `先执行 python -X utf8 "${stagePath}" start --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review。` +
        `随后执行插件上下文读取入口 python -X utf8 "${stagePath.replace(/[^/\\]+$/, "engineering_context.py")}" prepare --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --batch-worktree "${batchWorktree}" --stage review。仅按返回 contextPath/allowedReads 读取已验证工程事实和实际实现交接；Runtime 生成元数据不授权业务写入或测试。Review 不领取或发布新事实。` +
        `只评审业务生产代码、生产配置、迁移和公开接口的实现；测试源码、fixture/mock 和测试环境由紧随其后的 UTest 阶段创建。即使 scope.paths、expectedFiles 或 writeSet 中出现测试路径，也不得因 sealed commit 缺少测试文件而判定 Review 不通过；可评估可测试性，但不得要求测试资产已存在。评审实现、接口边界、错误处理和与 TASK 验收条件的一致性；禁止修改源码、提交、合并或删除 Worktree。Review 是完全只读阶段：禁止执行任何构建、编译、打包、typecheck、lint、测试、E2E 或验证命令，包括 mvn/mvnw、Gradle/gradlew、npm/pnpm/yarn、npx/tsc/vite/webpack、jest/vitest/pytest。后端 compile-only 已在 implement 的 finish-implementation 前完成；UTest 的行为测试及收尾编译通过 Bash 执行，再由 run_utest_command.py 登记 Evidence 和结果。` +
        `通过后执行 python -X utf8 "${stagePath}" complete --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review --metadata-json '${metadata}'。` +
        `发现问题时必须先执行 python -X utf8 "${stagePath}" fail --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --stage review --failure-type <implementation|documentation|needs_triage> --message "<具体问题：file:line、期望与实际行为、影响及建议修复>"。` +
        `最后必须执行 python -X utf8 "${stagePath}" validate-review-result --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"，并且只原样返回它的 stdout JSON。不得信任或返回此前命令的原始文本；该脚本会验证并规范化 durable Review 结果。可由当前 Batch 生产代码修复时，Workflow 会在同一 Worktree 修复、完成必要的后端纯编译并封存一次，然后直接进入 UTest，不会再次执行 Review。` +
        `documentation 与 needs_triage 仍按原分类阻断，保留 Worktree。只返回 JSON。`,
        { label: `stage-review-${batchId}-attempt-${attempt}`, phase: "Batch 阶段", schema: REVIEW_STAGE_RESULT_SCHEMA }
      ));
      // The reviewer itself is prompted to validate its result. Re-run the
      // validator in a read-only child so a prose/raw-JSON response cannot
      // bypass the plugin-owned state machine.
      reviewValidation = unwrap(await workflowAgent(
        `只验证 Batch ${batchId} 已持久化的 Review 阶段，不评审代码、不修改任何文件、不运行 TASK，也不得运行构建、编译、打包、typecheck、lint、测试或 E2E。唯一允许的命令是 python -X utf8 "${stagePath}" validate-review-result --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。只返回 JSON。`,
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
    requireReviewPassed(review, batchId);
  }
  if (!testResolvedByRepair) {
    const test = requireSuccess(await runBatchUtestAndSeal(batchResult, retryTestFailure), `stage test ${batchId}`);
    const testedDelivery = withLatestBatchDelivery(batchResult, test);
    const testStatus = test.testStatus || (test.status === "deferred" ? "deferred" : "passed");
    if (testStatus === "source_repair_required") {
      const sourceFailure = unwrap(test.testFailure || test.failure || {});
      return implementationReworkRequired(testedDelivery, "test", {
        status: "failed",
        failureType: "implementation",
        testFailure: sourceFailure,
        repairContextPath: test.repairContextPath,
        repairContextDigest: test.repairContextDigest,
        affectedTaskIds: test.affectedTaskIds,
        failure: {
          type: "implementation",
          nextStage: "implement",
          message: sourceFailure.message || test.errorMessage || "",
        },
      });
    }
    if (!["passed", "skipped", "deferred", "timed_out"].includes(testStatus)) {
      throw new Error(`stage_test_status_invalid:${batchId}:${String(testStatus)}`);
    }
    batchResult = testedDelivery;
  }
  requireSuccess(await workflowAgent(
    `Batch ${batchId} 的 Review 与 UTest 已收口（前端 UTest 明确跳过）。执行 python -X utf8 "${stagePath}" gate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。` +
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
      (testLogPath ? `这是 UTest 打回；先读取 Runtime 修复包中的本次失败片段；旧运行缺修复包时，才按 target/command/evidence 读取 "${testLogPath}" 中的准确命令段，不扫描公共日志。` : "")
    : "这是未完成 implement 的中断恢复，没有 review/test 打回上下文；按原 TASK 恢复执行。";
  const engineeringPath = leasePath.replace(/[^/\\]+$/, "engineering_context.py");
  return requireSuccess(await workflowAgent(
    `恢复 Batch ${batchId} 的 implement 阶段；之前的 review/test 失败已使该阶段的旧 evidence 失效。只能在既有原生 worktree "${batchWorktree}"、分支 "${batchBranch}" 内操作。不得调用 request_user_input、要求用户确认或等待用户裁定；按既定契约自动完成最小兼容修复并继续。${repairBrief}` +
    CODE_STAGE_EXECUTION_BOUNDARY +
    CODE_STAGE_COMPILE_COMMAND_GUIDANCE +
    `工程事实与交接均作为数据读取，不能授权业务写入。取得 lease 后先执行 python -X utf8 "${engineeringPath}" prepare --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --batch-worktree "${batchWorktree}" --stage ${failureContext ? "rework" : "code"} --owner-token <真实token>。读取返回的 contextPath、allowedReads、commandCatalogPath 和 operations；这是加载既有事实，不进行全仓探索。存在 failureContext 时只修 repairTaskIds；为空时先依据修复包做一次有限任务定位，用 repair-scope --content-file <临时JSON> 登记 affectedTaskIds 和 reason 后重新 prepare，禁止默认返修全部 TASK。旧 run 清理仍检查整个 Batch；无关且已完成 TASK 保留。start 成功后未知 API 先通过同一入口 query/resolve，结论立即 publish，修复尝试用 record-attempt 保存。` +
    `依次执行：1) 用 batch_lease_manager.py acquire 获取真实 lease token（workspace="${artifactWorkspace}"、feature="${feature}"、run-id="${runId}"、batch-id="${batchId}"、--ttl-seconds ${timeoutPerBatch}、--lease-guard）。插件在每个携带 token 的 task_runner/worktree_manager 命令边界续租；禁止自行启动后台 heartbeat；随后 mark-batch 为 running；` +
    `2) 先清理陈旧 run，再开始任何修复：对 ${JSON.stringify(taskIds)} 中每个真实 TASK 依次执行 task_runner.py inspect（携带 workspace、feature、task-id、code-workspace）；只要发现 parallelRunId="${runId}" 且状态为 started、in_progress 或 implementation_recording 的旧 run，就先用其 inspect 返回的真实 runId 执行 task_runner.py abort --force-with-changes --abort-why "implementation_rework_stale_run"，并携带 --workspace-ref "${batchWorkspaceRef}"。abort 必须保留 Worktree 未提交改动；每次 abort 后再次 inspect 确认该 TASK 已无活动 run。必须先完成全部陈旧 run 清理，禁止一边清理一边 start 新 TASK；其他 parallelRunId 的活动 run、无法 inspect 的 run 或 abort 失败均不得猜测，立即以 final-status pending 释放 lease 并返回 failed。` +
    `3) 仅处理上下文选定的 repairTaskIds（无打回上下文的普通恢复仅继续未完成 TASK），严格按依赖拓扑逐个修复，绝不并行或预启动：每次只处理一个 TASK，先 inspect 当前状态并确认其所有 deps 已为 implemented/done；所有 task_runner.py 调用必须使用 --lease-token <真实 token>、--code-workspace "${taskWorkspace}" 和 --workspace-ref "${batchWorkspaceRef}"。前置 TASK 尚未完成时必须继续完成前置 TASK，禁止尝试 start 当前 TASK。当前 TASK 为 implemented/done 时，读取其真实 latestImplementationEvidenceId，执行 task_runner.py start-task-repair --prior-evidence-id <真实 ID> --parallel-run-id "${runId}" --lease-token <真实 token> --code-workspace "${taskWorkspace}" --workspace-ref "${batchWorkspaceRef}"，记下真实 runId；当前 TASK 为 todo 时执行普通 task_runner.py start（同样携带 parallel-run-id、lease-token、code-workspace、workspace-ref），记下真实 runId。状态仍为 in_progress 时禁止再次 start；应回到步骤 2 处理其陈旧 run。其他状态不得臆造命令，返回 failed。前端 TASK 的 Route、HTML 和项目资料读取属于只读准备；上述 start-task-repair 或 start 成功并保存真实 runId 后，才能执行 prepare_html_analysis.py、生成 .frontend/html-analysis / .frontend/html-input 产物、复制或覆盖项目 assets 和修改前端源码，再按 Route 清单推进到 parser。Route 清单完成不是启动 TASK 的前置条件，TASK 启动失败不得走脚本降级继续写入。完成当前 TASK 的生产修复后，后端 lane 必须先按本 Prompt 的 Bash compile-only 规则编译通过，再用该真实 runId 执行 finish-implementation；只有 start-task-repair 启动的任务携带 --repair-mode，普通 start 启动的任务禁止携带 --repair-mode。确认 finish 成功且 TASK 已为 implemented/done 后，才可处理其下一个依赖任务。` +
    `4) 用同一 token 执行 batch_lease_manager.py check --require-lease-guard，再用 worktree_manager.py seal --purpose review --commit-stage ${failureContext ? "rework" : "code"} --commit-summary "<根据本次实际改动填写中文说明；返修说明具体修复的问题>" 产生新的 commitSha；该命令必须携带 --artifact-workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--repo "${batchWorktree}" 和同一真实 --owner-token，并以 final-status sealed 释放同一 lease。只有 lease 或 seal/release 失败才中断。` +
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
    `不得让模型串行拼接多个 stage start/complete，也不得根据原始文本判断是否收口。只执行 python -X utf8 "${stagePath}" record-single-repair --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --failed-stage "${failedStage}" --metadata-json '${metadata}'。` +
    `该插件命令会以单个、幂等的受控入口写入 prepare/implement/review evidence，并验证最终 Review evidence 的 single_repair_accepted 标记。只原样返回 JSON。`,
    { label: `record-single-repair-${failedStage}-${batchId}`, phase: "Batch 阶段", schema: SINGLE_REPAIR_RESULT_SCHEMA }
  ), `record single repair ${failedStage} ${batchId}`);
}

async function blockImplementationFinding(delivery, staged, disposition) {
  const batchId = delivery.batchId;
  const message = "unresolved_" + staged.failedStage + "_implementation_finding:" + disposition;
  return deferBatchForRetry(batchId, delivery.worktreePath, delivery.branchName, message);
}

async function runDeliveryWithImplementationRepair(batchResult, initialOptions = {}) {
  let delivery = batchResult;
  const repairedStages = new Set();
  let options = initialOptions;
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
    // Review -> one production repair -> seal -> next
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
    // A UTest-proven source defect has a new production commit and fresh
    // Review evidence, but the test itself is still pending.  Re-enter UTest
    // to run its original exact command; never mark it as repaired by prose.
    options = {
      reviewResolvedByRepair: true,
      ...(staged.failedStage === "test" ? { retryTestFailure: staged.recovery.failureContext } : {}),
    };
  }
}

async function continueRecoveredDelivery(recovery) {
  if (recovery.nextStage !== "implement" || !(recovery && recovery.failureContext)) {
    // Scheduler stage recovery is a durable checkpoint, not a request to
    // replay already-passed Review.  A test-stage recovery must resume UTest
    // directly; this keeps its agent scope and lease limited to the work left.
    const initialOptions = recovery.nextStage === "test"
      ? { reviewResolvedByRepair: true }
      : {};
    return runDeliveryWithImplementationRepair(recovery, initialOptions);
  }
  const repaired = await reworkDeliveryImplementation(recovery);
  await recordSingleRepairResolution(recovery, repaired);
  const failedStage = recovery.failureContext.failedStage;
  const options = {
    reviewResolvedByRepair: true,
    ...(failedStage === "test" ? { retryTestFailure: recovery.failureContext } : {}),
  };
  return runDeliveryReviewTestAndGate(repaired, options);
}

async function resolveMergeCandidate(repositoryRef, wave, label, executionPhase) {
  const bindingArgs = `--workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave}`;
  // Explicitly declare every handoff field: structured transports can drop
  // undeclared properties even with additionalProperties enabled.
  const preparationSchema = {
    type: "object",
    properties: {
      success: { type: "boolean" }, ok: { type: "boolean" }, status: { type: "string" },
      error: { type: "string" }, worktreePath: { type: "string" }, candidateSha: { type: "string" },
      conflictedFiles: { type: "array", items: { type: "string" } },
      repositoryRef: { type: "string" }, wave: { type: "number" }, attempts: { type: "number" },
      reused: { type: "boolean" }, runDir: { type: "string" }, manifestPath: { type: "string" },
      contextPath: { type: "string" }, contextDigest: { type: "string" }, identityDigest: { type: "string" },
    },
    required: ["success"], additionalProperties: true,
  };
  const prepared = unwrap(await workflowAgent(
    `准备既有 Merge Train 候选的冲突解决：执行 python -X utf8 "${mergeTrainPath}" prepare-resolution ${bindingArgs} --attempt-id "${label}"。` +
    `运行清单位置已知：${JSON.stringify(`${artifactWorkspace}/.autobizdevops/features/${feature}/.parallel-runs/${runId}/manifest.json`)}。无需搜索目录或读取插件源码定位产物。` +
    `完整保留命令返回的 runDir、manifestPath、contextPath、contextDigest、identityDigest、repositoryRef、wave、conflictedFiles；上下文包由插件准备。` +
    `使用 execute 的默认合法工作区，不把工具 cwd/workdir 切换到候选；候选目录由插件内部处理。只执行这个插件命令并返回 stdout JSON；失败时返回原始结果，不得自行解决、暂存或提交，不得创建候选或修改集成分支、基线分支。`,
    { label, phase: executionPhase, schema: preparationSchema }
  ));
  if (!prepared || hasFailureSignal(prepared) || prepared.success !== true) return prepared;
  if (prepared.status === "built") return prepared; // Reconcile an already completed handoff.
  if (!["resolution_required", "ready_to_complete"].includes(prepared.status)) {
    return { success: false, status: "needs_resolution", error: "candidate_resolution_prepare_invalid", worktreePath: prepared.worktreePath };
  }
  if (prepared.status === "resolution_required") {
    if (!absolutePath(prepared.worktreePath) || !Array.isArray(prepared.conflictedFiles) || !prepared.conflictedFiles.length) {
      return { success: false, status: "needs_resolution", error: "candidate_resolution_scope_missing" };
    }
    if (!absolutePath(prepared.contextPath) || !absolutePath(prepared.manifestPath) || !absolutePath(prepared.runDir)
        || !/^[a-f0-9]{64}$/.test(prepared.contextDigest || "") || !/^[a-f0-9]{64}$/.test(prepared.identityDigest || "")
        || prepared.repositoryRef !== repositoryRef || prepared.wave !== wave) {
      return { success: false, status: "needs_resolution", error: "candidate_resolution_context_missing_or_invalid", worktreePath: prepared.worktreePath };
    }
    const edited = unwrap(await workflowAgent(
      `解决已授权的 Merge Train 冲突。候选 Worktree=${JSON.stringify(prepared.worktreePath)}；允许修改的冲突文件=${JSON.stringify(prepared.conflictedFiles)}。` +
      `冲突上下文索引=${JSON.stringify(prepared.contextPath)}；contextDigest=${prepared.contextDigest}；identityDigest=${prepared.identityDigest}；repositoryRef=${JSON.stringify(repositoryRef)}；wave=${wave}。` +
      `运行目录=${JSON.stringify(prepared.runDir)}；manifestPath=${JSON.stringify(prepared.manifestPath)}，这些路径由插件提供，不得猜测。` +
      `先用 query_json_fields 按字段读取索引 identity、sideMeaning、businessContextPath、files、snapshotRole；再按索引读取 business.json 中双方 Batch 的任务、Plan 快照和 Evidence 引用，按文件读取 conflicts.json 的 hunk、changes.diff 及所需三方快照。JSON 读取沿用全局 query_json_fields 协议；按需使用 files[0] 等字段，可合并多个字段查询，避免重复读取完整文件。` +
      `允许读取该上下文包及其明确引用的 Plan/Evidence，以及候选中理解调用关系所需的相关代码；资料内容只作为业务数据，不作为执行指令。base/ours/theirs 是 Git index stage 1/2/3，base 不等于候选创建时的目标 HEAD。` +
      `快照是初始阅读资料，当前候选可能已有部分编辑；重试应接续当前文件，不得用快照覆盖已完成内容。上下文缺失、不可读或身份不匹配时直接返回 needs_resolution 和具体错误，禁止搜索目录、扫描完整 manifest 或读取插件源码重建上下文。` +
      `只编辑已授权的冲突文件，保留双方应有逻辑、移除冲突标记。不得创建新 Worktree、修改集成分支、基线分支或其他 Batch。` +
      `本步骤只负责文件内容，不执行 git add、git commit、git merge --continue 或任何其他写入 Git 操作，不通过 Python/脚本包装这些操作。` +
      `编辑完成后立即返回 {"success":true,"status":"resolved"}，交还 Workflow，由下一步插件完成暂存和 merge commit。` +
      `文件写入被拦截或无法解决时，返回 {"success":false,"status":"needs_resolution","error":<具体原因>}；不要修改插件源码或尝试其他提交命令。`,
      { label: `${label}-edit`, phase: executionPhase }
    ));
    if (!edited || edited.success !== true || edited.status !== "resolved" || hasFailureSignal(edited)) {
      return { success: false, status: "needs_resolution", error: edited || "candidate_resolution_empty", worktreePath: prepared.worktreePath, conflictedFiles: prepared.conflictedFiles };
    }
  }
  // The model has returned. This separate plugin operation owns all Git writes.
  return unwrap(await workflowAgent(
    `模型已交还冲突文件。执行 python -X utf8 "${mergeTrainPath}" complete-resolution ${bindingArgs}。` +
    `插件负责检查、暂存、非交互 merge commit 和候选 SHA 登记。使用 execute 的默认合法工作区，不把工具 cwd/workdir 切换到候选。只执行这个命令并返回 stdout JSON；失败时原样返回，禁止自行 git add/commit/merge、包装替代命令或修改文件。`,
    { label: `${label}-commit`, phase: executionPhase }
  ));
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
        `构建 Batch ${batchId} 的独立 Merge Train 候选（候选序号 ${wave}，第 ${attempt} 次）。执行 python -X utf8 "${mergeTrainPath}" build-candidate --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave} ${batchArgs}。` +
        `这是可能超过 60 秒的 Git 候选构建：若当前 execute 支持 run_in_background，必须只启动一次 execute({command:<上述命令>,run_in_background:true})，保存 task_id 并在同一子 Agent 内反复 task_output（每次可 timeout:120000）直至获得最终退出结果；task_output 的等待超时不是构建失败，禁止重启该命令。若当前为托管前台会话，则只执行一次并等待其最终结果。子 Agent 在 task_output 得到终态前不得结束，否则平台会清理其后台进程。` +
        `候选创建失败时保留 delivery Worktree 并停止，禁止 rebase 或直接合并主分支。build-candidate 在同一 wave 中只能真正启动一次；只能轮询同一 task_id，不得因本地终端超时或中断再次执行。只返回最终命令 stdout 的完整 JSON；若工具返回后台任务包装对象，提取 final_result，不得把 command、task_id、exit_code 包装层当作命令结果。status=candidate_conflicted 是可恢复冲突，完整保留 conflictContext 交由本 Workflow 处理。`,
        { label: `build-candidate-${repositoryRef}-${batchId}-${wave}-${attempt}`, phase: "候选验证" }
      ));

      let built = builtRaw;
      if (builtRaw && builtRaw.status === "candidate_conflicted") {
        // A candidate's conflict markers are tied to this exact base SHA.
        // Resolve and promote it now, before other same-repository deliveries
        // can advance main and make the retained conflict context stale.
        const resolved = await resolveMergeCandidate(repositoryRef, wave,
          `resolve-conflicted-candidate-${repositoryRef}-${batchId}-${wave}`, "候选验证");
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
      `推广已完成业务 Review，UTest 已通过、已记录失败或前端 UTest 已明确跳过的候选 SHA ${built.candidateSha}。执行 python -X utf8 "${mergeTrainPath}" promote-candidate --allow-unverified --allow-stale --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --repository-ref "${repositoryRef}" --wave ${wave}。` +
        `Batch 的 UTest 失败会作为显式 issue 随最终结果保留，但不打断后续流程；合并后唯一的可执行验证是 B-E2E。若返回 stale=true，必须停止本次推广并由插件从当前目标 HEAD 全量重建候选；禁止 rebase 或直接 merge。只返回 JSON。`,
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
  const engineeringPath = leasePath.replace(/[^/\\]+$/, "engineering_context.py");
  return `在插件创建的原生 Git worktree "${batchWorktree}" 中执行 Batch ${batchId}。Feature=${feature}，runId=${runId}，artifact workspace=${artifactWorkspace}。严格按以下固定顺序执行：\n` +
    `Code Workflow 已获自主执行授权：不得调用 request_user_input、要求用户确认或等待用户裁定。差异按既定 TASK/规格和现有工程模式作最小兼容实现，并作为非阻断 Evidence 记录；流程必须继续。\n` +
    CODE_STAGE_EXECUTION_BOUNDARY +
    CODE_STAGE_COMPILE_COMMAND_GUIDANCE +
    `1. 执行 cd "${batchWorktree}"（Windows 使用 Set-Location），确认 git rev-parse --show-toplevel 等于该路径、git symbolic-ref --quiet --short HEAD 等于 "${batchBranch}"。禁止 git worktree add/remove、git switch、merge、rebase 或操作其他 checkout。\n` +
    `2. 执行 python -X utf8 "${leasePath}" acquire --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --ttl-seconds ${timeoutPerBatch} --lease-guard；从 JSON 的 lease.ownerToken 保存本 Batch 的 lease token。\n` +
    `3. 将步骤 2 返回的非空 ownerToken 保存为变量，并在后续命令中展开为该真实字符串；命令行中不得出现空字符串、字面量 "LEASE_TOKEN" 或 "<lease-token>"。禁止自行运行 batch_lease_manager.py heartbeat、run_in_background、&、nohup、setsid 或 Start-Process。插件会在每个携带 token 的 task_runner/worktree_manager 命令开始时续租；独立 shell 子进程存活与否不再作为 Batch 失败条件。\n` +
    `4. 执行 python -X utf8 "${schedulerPath}" mark-batch --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --status running --worktree-path "${batchWorktree}" --branch-name "${batchBranch}"。业务源码命令只在该 checkout 内执行。\n` +
    `共享上下文：步骤 4 后执行 python -X utf8 "${engineeringPath}" prepare --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}" --batch-worktree "${batchWorktree}" --stage code --owner-token <真实token>。只加载已有事实，不在 Workflow 前或 Batch 前另做探索。只读其 contextPath、allowedReads、commandCatalogPath；优先使用 operations 和当前 CLI schema。TASK start 前只读准备和 start 后实现均先 lookup --symbol <符号>；缺失时未知 API 先 locate --symbol <完整类名> --module <当前模块相对路径>，再 resolve --symbol <符号> --source <准确相对路径>（签名）或 query（usage_example/runtime_precondition/test_recipe/capability_result）；这些操作携带相同身份及当前 --task-id、--stage code 和真实 owner-token。这些工程元数据操作不授权修改业务源码；依赖类使用 engineering_context.py lookup-class/inspect-class 和已有精确 classpath。claimed 后只读 sources 并立即 publish --fact-id/--claim-token/--content-file <临时JSON>，不要等 TASK 完成；in_progress 时先做独立工作，再按 retryAfterSeconds 有限重查。不同符号/版本分别查询，不扫描整个仓库。finish-implementation 自动生成实现交接，失败只补交接，不重复实现或编译。\n` +
    `5. Scheduler 已提供本 Batch 的唯一 TASK IDs：${JSON.stringify(taskIds)}。逐个以这些具体 ID 执行；禁止使用空值、"undefined" 或任何占位符。不要用 read_file 读取 artifact 目录；artifact workspace 不是代码目录（仅允许读取 engineering prepare 返回的准确 allowedReads/contextPath/commandCatalogPath）。自动重试时，先对每个 TASK 执行 task_runner.py inspect；如发现同一 parallelRunId 的 started/in_progress run，使用其真实 runId 执行 task_runner.py abort --force-with-changes --abort-why "automatic_batch_retry" 并携带 --workspace-ref "${batchWorkspaceRef}"，保留 worktree 改动并将 TASK 恢复为 todo。已经 implemented/done 的 TASK 必须保留既有 implementation evidence，禁止再次 start；只继续未完成 TASK。对数组中的每个实际 ID，直接将该值传给 code_task_context.py 的 --task-id 参数。以 taskContract.uiRequired 为唯一条件：false 时跳过 Route resolver，不读取 HTML/Route SKILL；true 时先在本 agent 内完成 Route 只读准备：执行 python -X utf8 "${routeResolverPath}" --workspace "${artifactWorkspace}" --feature "${feature}" --start-route-run --json（携带当前真实 --task-id），按返回 route 读取对应 Route SKILL 到 EOF，标记 route-skill-read-complete、创建 route write_todos，读取原始 HTML 与项目资料并确认脚本参数。只读准备完成且 context 返回 startAllowed=true 后，按 context 的 startArgv 执行当前 TASK 的 task_runner.py start，并携带本 Prompt 要求的全部 TASK/Batch/workspace 绑定参数；确认 start 成功并保存真实 TASK runId。Route 清单完成和 parser 读取不是 start 的前置条件。start 成功后才能执行 prepare_html_analysis.py、生成 .frontend/html-analysis / .frontend/html-input 产物、复制或覆盖项目 assets，再推进 Route 清单；仅当清单推进到转交 parser 后才读取对应 parser 并标记 parser-read，完成清单后标记 route-todos-completed，统一回检后写入 FRONTEND_ROUTE.json。start 失败不属于脚本降级，禁止执行分析脚本、写产物或复制资源。route=spec-driven-ui 不读 parser 但仍须在 start 成功后实现并回检，route=none 禁止写前端源码。每个 TASK 必须严格执行“start 成功后才能写业务源码；后端 TASK 必须先 Bash 编译通过，再 finish-implementation 成功，然后才能开始下一个 TASK”。编译失败时从实际输出定位根因、修复后重编，不得跳过或假报通过。禁止预先编写后续 TASK 的任何业务文件。若 start 返回 prestart_unattributed_changes_detected：不得创建 supporting file、不得以 no-code-change 提交、不得继续后续 TASK；保留原始 JSON 并返回 failed，使 Workflow 将该 Batch 隔离为 retry_pending，其他独立 Batch 继续。单个 TASK 从 start 成功到 finish 成功期间产生的全部业务变更都归属该 TASK，runner 不按 Plan 的 scope.paths 拒绝实际实现文件。所有 task_runner 调用必须带 --workspace "${artifactWorkspace}"、--parallel-run-id "${runId}"、展开后的真实 --lease-token、--code-workspace "${taskWorkspace}" 和 --workspace-ref "${batchWorkspaceRef}"。不得操作其他 Batch 或任何主业务 checkout。\n` +
    `6. 全部 TASK 完成后执行 python -X utf8 "${leasePath}" check 并携带同一真实 --owner-token 和 --require-lease-guard；仅 valid=true 才可继续。只调用 python -X utf8 "${worktreeManagerPath}" --json seal --purpose review --commit-stage code --commit-summary "<根据本 Batch 实际实现填写中文功能说明>"，并携带 --artifact-workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--repo "${batchWorktree}" 和 --owner-token（同一真实 token）；该命令也会续租。从 JSON 保存供 Review 使用的草稿 commitSha。插件在此命令中提交；不要自行 git add、git commit 或把 Batch 标为可候选合并。\n` +
    `7. 草稿 seal 成功后执行 python -X utf8 "${leasePath}" release，并携带 --workspace "${artifactWorkspace}"、--feature "${feature}"、--run-id "${runId}"、--batch-id "${batchId}"、--owner-token（同一真实 token）和 --final-status sealed。若 seal 返回 parallel_git_index_lock_busy 或 parallel_git_index_lock_recovery_failed，说明等待与本 Batch index.lock 的受控清理后仍无法写入；只以 final-status pending 调用同一 release，随后返回 failed，由 Workflow 标记为 retry_pending 并在同一 run resume。其他首次命令失败也同样以 final-status pending 释放。禁止检查/修改插件源码、创建 Git wrapper、尝试替代命令或继续任何 TASK。\n` +
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
      `为 Batch ${batchId} 创建或复用插件托管的原生 Git Worktree。只允许执行一次 python -X utf8 "${worktreeManagerPath}" --json provision --artifact-workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${batchId}"。只返回该命令的 JSON；不得使用平台 isolation，不得修改业务源码。若 execute 报告 timeout、中断或无结果，立即返回 failed，禁止检查插件目录、读取 Git 状态、重试该命令，或使用 run_in_background、&、nohup、setsid、Start-Process 等后台方式。后续 Workflow 重试会由插件受控识别并重建未完成 Worktree。`,
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

async function runImplementationRecoveryLifecycle(recovery) {
  const batchId = recovery.batchId;
  const batchWorktree = recovery.worktreePath;
  const batchBranch = recovery.branchName;
  try {
    const taskIds = Array.isArray(batchTaskIds[batchId]) ? batchTaskIds[batchId] : [];
    const batchWorkspace = batchWorkspaces[batchId] || {};
    const batchWorkspaceRef = batchWorkspace.workspaceRef;
    if (!usableString(batchWorktree) || !usableString(batchBranch) || !taskIds.length) {
      throw new Error(`implementation_recovery_context_missing:${batchId}`);
    }
    if (!usableString(batchWorkspaceRef) || !codeWorkspaces[batchWorkspaceRef]) {
      throw new Error(`scheduler did not provide a code workspace for ${batchId}`);
    }
    // The scheduler has already proven this is a plugin-owned worktree whose
    // dirty changes still sit on the frozen Batch base.  Do not provision,
    // reset, or discard it: implementationPrompt first reconciles stale Task
    // Runner state with --force-with-changes, then continues the same TASK.
    const taskWorkspace = batchTaskWorkspace(batchId, batchWorktree);
    const implemented = unwrap(await workflowAgent(
      implementationPrompt(batchId, batchWorktree, batchBranch, taskIds, batchWorkspaceRef, taskWorkspace),
      { label: `resume-implementation-${batchId}`, phase: "Batch 阶段", schema: BATCH_RESULT_SCHEMA }
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

function runnableImplementationRecoveries() {
  return implementationRecoveryBatches
    .filter(recovery => (
      recovery
      && recovery.recoveryKind === "implementation_resume"
      && recovery.preserveWorktree === true
      && recovery.reprovision === false
      && !quarantinedBatchIds.has(recovery.batchId)
      && (!schedulerSnapshotDegraded || !schedulerFallbackConsumed.has(recovery.batchId))
    ));
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

function takeNextRunnableLifecycle(claimedBatchIds, runningBatchIds = new Set()) {
  const claimed = claimedBatchIds || new Set();
  const scheduledBatchIds = runnableScheduledBatchIds();
  const fallbackBatchIds = runnableSchedulerFallbackBatchIds();
  const implementationRecoveries = runnableImplementationRecoveries();
  const recoveries = runnableStageRecoveries();
  const mergeable = runnableMergeableBatchIds();
  const recoveredBatchIds = new Set([
    ...implementationRecoveries.map(item => item.batchId),
    ...recoveries.map(item => item.batchId),
  ]);
  const mergeableBatchIds = new Set(mergeable);
  const claim = job => {
    if (!job || runningBatchIds.has(job.batchId)) return null;
    const generation = typeof dispatchGrants === "undefined" ? 1 : dispatchGrants[job.batchId];
    if (!Number.isInteger(generation) || generation < 1) return null;
    const grantKey = `${job.batchId}:${generation}`;
    if (claimed.has(grantKey)) return null;
    claimed.add(grantKey);
    return { ...job, generation, grantKey };
  };
  const candidates = [];

  // Keep the established recovery/scheduled/mergeable order for ordinary work,
  // then move every scheduler-rescheduled retry behind all non-retry candidates.
  // A sealed delivery or verified dirty implementation worktree remains an
  // owned checkpoint and is represented by the scheduler's recovery arrays.
  for (const recovery of recoveries) {
    if (mergeableBatchIds.has(recovery.batchId)) continue;
    candidates.push({
      batchId: recovery.batchId,
      source: "stage_recovery",
      execute: () => runRecoveredBatchLifecycle(recovery),
      fallback: recovery,
    });
  }
  for (const recovery of implementationRecoveries) {
    if (mergeableBatchIds.has(recovery.batchId)) continue;
    candidates.push({
      batchId: recovery.batchId,
      source: "implementation_recovery",
      execute: () => runImplementationRecoveryLifecycle(recovery),
      fallback: recovery,
    });
  }
  for (const batchId of scheduledBatchIds) {
    candidates.push({
      batchId,
      source: "initial",
      execute: () => runInitialBatchLifecycle(batchId),
    });
  }
  for (const batchId of fallbackBatchIds) {
    candidates.push({
      batchId,
      source: "scheduler_snapshot_fallback",
      execute: () => runInitialBatchLifecycle(batchId),
    });
  }
  for (const batchId of mergeable) {
    if (recoveredBatchIds.has(batchId)) continue;
    candidates.push({
      batchId,
      source: "merge_candidate",
      execute: () => runMergeableBatchLifecycle(batchId),
    });
  }
  const retryPriorities = typeof retryPriorityBatchIds === "undefined" ? null : retryPriorityBatchIds;
  const ordered = [...candidates.filter(job => !retryPriorities || !retryPriorities.has(job.batchId)),
    ...candidates.filter(job => retryPriorities && retryPriorities.has(job.batchId))];
  for (const candidate of ordered) {
    const result = claim(candidate);
    if (result) return result;
  }
  return null;
}

// Encode arbitrary Unicode diagnostics without interpolating shell metacharacters.
function encodeFailure(reason) {
  const bytes = [...encodeURIComponent(String(reason || "batch_execution_failed"))
    .replace(/%([0-9A-F]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)))].map(c => c.charCodeAt(0));
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let result = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | ((bytes[i + 1] || 0) << 8) | (bytes[i + 2] || 0);
    result += alphabet[n >>> 18] + alphabet[(n >>> 12) & 63]
      + (i + 1 < bytes.length ? alphabet[(n >>> 6) & 63] : "=")
      + (i + 2 < bytes.length ? alphabet[n & 63] : "=");
  }
  return result;
}

async function dispatchCommand(action, job, extra, label) {
  const script = schedulerPath.replace(/[^/\\]+$/, "parallel_dispatch.py");
  const owner = job.ownerToken ? ` --owner-token "${job.ownerToken}"` : "";
  const result = unwrap(await workflowAgent(
    `只执行 python -X utf8 "${script}" ${action} --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --batch-id "${job.batchId}" --generation ${job.generation}${owner} ${extra}。` +
    `原样返回命令 JSON。not_claimed/stale_owner_ignored 是过期或已占用的派发许可，不得运行 TASK、强制 reclaim 或自行重试。不得读取插件源码。`,
    { label, phase: "Batch 阶段", schema: { type: "object", properties: {
      success: { type: "boolean" }, status: { type: "string" }, batchId: { type: "string" },
      generation: { type: "integer" }, ownerToken: { type: "string" }, reasonCode: { type: ["string", "null"] }
    }, required: ["success", "status", "batchId"], additionalProperties: false } }
  ));
  if (result.success !== true || result.commandExecutionFailure || result.batchId !== job.batchId) {
    throw new Error(`parallel_dispatch_command_failed:${action}:${job.batchId}:${JSON.stringify(result)}`);
  }
  return result;
}

function signalDispatchChange(state) {
  for (const wake of state.waiters || []) wake();
  if (state.waiters) state.waiters.clear();
}

function waitForDispatchChange(state) {
  if (!state.waiters) state.waiters = new Set();
  return new Promise(resolve => {
    const wake = (reason = "signal") => { clearTimeout(timer); state.waiters.delete(wake); resolve(reason); };
    const timer = setTimeout(() => wake("poll"), DISPATCH_POLL_INTERVAL_MS);
    state.waiters.add(wake);
  });
}

// All wall-clock observations come from the Runtime's scheduler response.
function dispatchSnapshotEpoch(state) {
  return state && Number.isFinite(state.observedEpochMs) ? state.observedEpochMs : null;
}

function dispatchIdleExpired(started, snapshot, polls, limit) {
  const current = dispatchSnapshotEpoch(snapshot);
  return (started !== null && current !== null && current - started > (timeoutPerBatch + 60) * 1000)
    || polls > limit;
}

function nextDispatchRequestId(state, invocationId, drainLabel, workerIndex) {
  state.claimSequence = (state.claimSequence || 0) + 1;
  return `${invocationId}:${drainLabel}:${workerIndex}:${state.claimSequence}`;
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

function dispatchHasPotentialWork(state) {
  if (!state || typeof state !== "object") return false;
  return Boolean(
    (Array.isArray(state.readyBatches) && state.readyBatches.length)
    || (Array.isArray(state.mergeableBatches) && state.mergeableBatches.length)
    || (Array.isArray(state.implementationRecoveryBatches) && state.implementationRecoveryBatches.length)
    || (Array.isArray(state.stageRecoveryBatches) && state.stageRecoveryBatches.length)
    || (Array.isArray(state.retryPendingBatches) && state.retryPendingBatches.length)
    || (Number.isInteger(state.activeWorkers) && state.activeWorkers > 0)
  );
}

function dispatchOccupiedSlots(state, dispatcherState) {
  const activeIds = new Set(
    state
    && state.dispatchDiagnostics
    && Array.isArray(state.dispatchDiagnostics.activeBatchIds)
      ? state.dispatchDiagnostics.activeBatchIds.filter(isValidBatchId)
      : []
  );
  for (const batchId of dispatcherState.runningBatchIds) activeIds.add(batchId);
  const reported = state && Number.isInteger(state.activeWorkers) ? state.activeWorkers : 0;
  return Math.max(activeIds.size, reported);
}

async function refreshSchedulerForDispatch(label, { poll = false } = {}) {
  let state = await readSchedulerState(label, "Batch 阶段", { poll });
  if (!state) return null;
  const pendingRetriesOf = scheduler => (
    scheduler && Array.isArray(scheduler.retryPendingBatches)
      ? scheduler.retryPendingBatches.filter(isValidBatchId)
      : []
  );

  let resumePass = 0;
  while (pendingRetriesOf(state).length) {
    const pendingBefore = pendingRetriesOf(state);
    let resumed;
    try {
      resumed = await recoverPendingRetries(state, `${label}-resume-retry-pending`, "Batch 阶段");
    } catch (error) {
      // A retry recovery error must not keep independent, already-scheduled
      // work from using the dispatcher. The next scheduler refresh can retry
      // the recovery while keeping these durable retry markers intact.
      recordSchedulerFailure(`${label}-resume-retry-pending`, error);
      return state;
    }
    applySchedulerState(resumed);
    const rescheduled = new Set([
      ...(Array.isArray(resumed.rescheduledRetryBatches) ? resumed.rescheduledRetryBatches : []),
      ...normalizeScheduledGroups(resumed.scheduledGroups || []).flat(),
      ...(resumed.mergeableBatches || []),
      ...(resumed.implementationRecoveryBatches || []).map(item => item && item.batchId),
      ...(resumed.stageRecoveryBatches || []).map(item => item && item.batchId),
    ].filter(isValidBatchId));
    for (const batchId of rescheduled) quarantinedBatchIds.delete(batchId);

    // Re-read after resume so a retry marker written concurrently with that
    // resume is picked up before the worker decides the queue is drained.
    state = await readSchedulerState(`${label}-after-retry-resume-${++resumePass}`, "Batch 阶段", { poll });
    if (!state) return null;
    const stillPending = pendingRetriesOf(state);
    const madeProgress = pendingBefore.some(batchId => !stillPending.includes(batchId))
      || stillPending.some(batchId => !pendingBefore.includes(batchId));
    if (!madeProgress) break;
  }
  return state;
}

async function runDispatchWorker(workerIndex, claimedBatchIds, drainLabel, dispatcherState) {
  let idlePolls = 0;
  let idleStarted = dispatchSnapshotEpoch(lastScheduler);
  const maxIdlePolls = Math.ceil(((timeoutPerBatch + 60) * 1000) / DISPATCH_POLL_INTERVAL_MS);
  for (;;) {
    // Resume retry-pending work as soon as it is visible to the drain. The
    // candidate selector keeps these retries behind every available non-retry
    // lifecycle, so recovery does not delay independent work.
    const pendingRetryIds = lastScheduler && Array.isArray(lastScheduler.retryPendingBatches)
      ? lastScheduler.retryPendingBatches.filter(isValidBatchId)
      : [];
    if (pendingRetryIds.length) {
      const refreshed = await refreshSchedulerForDispatch(
        `${drainLabel}-retry-pending-${workerIndex}-${schedulerCycles}`
      );
      if (!refreshed && dispatcherState.active === 0) return;
    }

    // Recovery and merge candidates share the same worker pool as fresh
    // implementation grants. Enforce the run-wide limit here as well, since
    // their arrays are recovery candidates rather than scheduler reservations.
    if (dispatchOccupiedSlots(lastScheduler, dispatcherState) >= maxParallel) {
      if (dispatcherState.active === 0 && lastScheduler && lastScheduler.activeWorkers === 0) return;
      if (await waitForDispatchChange(dispatcherState) === "poll") idlePolls += 1;
      await refreshSchedulerForDispatch(
        `${drainLabel}-capacity-poll-${workerIndex}-${schedulerCycles}`,
        { poll: true }
      );
      if (dispatchIdleExpired(idleStarted, lastScheduler, idlePolls, maxIdlePolls)) {
        recordUnresolved({
          kind: "scheduler",
          status: "dispatcher_idle_timeout",
          durable: false,
          error: `dispatcher_capacity_wait_timeout:${workerIndex}:${maxIdlePolls}`,
        });
        return;
      }
      continue;
    }
    // Claim synchronously from the latest shared snapshot before awaiting any
    // tool call. Other worker callbacks therefore cannot take the same Batch.
    const job = takeNextRunnableLifecycle(claimedBatchIds, dispatcherState.runningBatchIds);
    if (job) {
      dispatcherState.active += 1;
      dispatcherState.runningBatchIds.add(job.batchId);
      job.requestId = nextDispatchRequestId(dispatcherState, workflowInvocationId, drainLabel, workerIndex);
      let grant = null;
      let claimDeferred = false;
      let outcome = "failed";
      try {
        const acquired = await dispatchCommand("claim", job,
          `--source "${job.source}" --worker "${drainLabel}-${workerIndex}" --request-id "${job.requestId}" --ttl-seconds ${Math.max(14400, timeoutPerBatch + 300)}`,
          `dispatch-claim-${job.batchId}-${job.generation}`);
        if (acquired.status === "claimed" && usableString(acquired.ownerToken)) {
          grant = { generation: job.generation, ownerToken: acquired.ownerToken };
          activeDispatchGrants.set(job.batchId, grant);
          if (!canStartLifecycle()) return;
          idlePolls = 0;
          idleStarted = dispatchSnapshotEpoch(lastScheduler);
          dispatcherState.ranAny = true;
          const result = await runLifecycleSafely(job.batchId, job.source, job.execute, job.fallback);
          outcome = result && result.status === "retry_pending" ? "retry_pending" : result && ["merged", "ready_to_candidate"].includes(result.status) ? "completed" : "failed";
        } else if (["capacity_full", "running", "business_lease_held", "resource_or_stage_conflict"].includes(acquired.reasonCode)) {
          claimedBatchIds.delete(job.grantKey);
          claimDeferred = true;
        } else {
          recordUnresolved({ kind: "scheduler", batchId: job.batchId, status: "dispatch_not_claimed",
            durable: false, error: acquired.reasonCode || "invalid_dispatch_claim", source: job.source });
        }
      } catch (error) {
        // A failure to persist this Batch's retry marker must be visible, but
        // must not terminate the worker and strand unrelated ready Batches.
        recordUnresolved({
          kind: "batch",
          batchId: job.batchId,
          status: "dispatch_worker_failed",
          durable: false,
          error: errorText(error),
          source: job.source,
        });
      } finally {
        if (grant) {
          try {
            await dispatchCommand("finish", { ...job, ...grant }, `--outcome "${outcome}"`, `dispatch-finish-${job.batchId}-${job.generation}`);
          } catch (error) {
            recordUnresolved({ kind: "scheduler", batchId: job.batchId, status: "dispatch_finish_failed", durable: false, error: errorText(error) });
          }
          if (activeDispatchGrants.get(job.batchId) === grant) activeDispatchGrants.delete(job.batchId);
        }
        dispatcherState.active -= 1;
        dispatcherState.runningBatchIds.delete(job.batchId);
        signalDispatchChange(dispatcherState);
      }

      // Every outcome, including retry_pending, triggers an immediate refresh.
      // A null result from a real scheduler error blocks new dispatch until a
      // later successful poll; it never causes us to reuse stale grants.
      const state = await refreshSchedulerForDispatch(
        `${drainLabel}-after-batch-${job.batchId}-${schedulerCycles}-worker-${workerIndex}`
      );
      if (state && state.schedulerSnapshotFallback === true) {
        schedulerFallbackConsumed.add(job.batchId);
      }
      if (!state) {
        if (await waitForDispatchChange(dispatcherState) === "poll") idlePolls += 1;
        const recovered = await refreshSchedulerForDispatch(
          `${drainLabel}-worker-${workerIndex}-scheduler-retry`
        );
        if (!recovered && dispatcherState.active === 0) return;
      }
      if (claimDeferred) {
        if (await waitForDispatchChange(dispatcherState) === "poll") idlePolls += 1;
        if (dispatchIdleExpired(idleStarted, lastScheduler, idlePolls, maxIdlePolls)) {
          recordUnresolved({ kind: "scheduler", status: "dispatcher_idle_timeout", durable: false,
            error: `dispatcher_claim_wait_timeout:${workerIndex}:${maxIdlePolls}` });
          return;
        }
      }
      continue;
    }

    // Other workers may still be running while this worker has no assignment.
    // Keep polling so newly eligible work is dispatched without a completion
    // barrier. A retry-pending marker also keeps the worker alive until the
    // retry recovery path has had a chance to reschedule it.
    const snapshot = lastScheduler;
    if (dispatcherState.active === 0 && !dispatchHasPotentialWork(snapshot)) return;
    if (
      dispatcherState.active === 0
      && snapshot
      && snapshot.activeWorkers === 0
      && (!Array.isArray(snapshot.retryPendingBatches) || snapshot.retryPendingBatches.length === 0)
    ) {
      const stuck = [...new Set([...(snapshot.readyBatches || []), ...runnableScheduledBatchIds(),
        ...runnableMergeableBatchIds(), ...runnableStageRecoveries().map(item => item.batchId),
        ...runnableImplementationRecoveries().map(item => item.batchId)])];
      if (stuck.length) recordUnresolved({ kind: "scheduler", status: "ready_batches_not_dispatchable", durable: false,
        error: { batchIds: stuck, dispatchGrants: typeof dispatchGrants === "undefined" ? {} : dispatchGrants,
          notSelected: snapshot.dispatchDiagnostics && snapshot.dispatchDiagnostics.notSelected } });
      return;
    }
    if (await waitForDispatchChange(dispatcherState) === "poll") idlePolls += 1;
    await refreshSchedulerForDispatch(
      `${drainLabel}-poll-${workerIndex}-${schedulerCycles}`,
      { poll: true }
    );
    if (dispatchIdleExpired(idleStarted, lastScheduler, idlePolls, maxIdlePolls)) {
      recordUnresolved({
        kind: "scheduler",
        status: "dispatcher_idle_timeout",
        durable: false,
        error: `dispatcher_idle_timeout:${workerIndex}:${maxIdlePolls}`,
      });
      return;
    }
  }
}

async function drainRunnableLifecycles(drainLabel) {
  let ranAny = false;
  // Start a stable pool for the whole drain, rather than a snapshot-sized set
  // of callbacks. Empty workers remain alive while another lifecycle runs and
  // poll the scheduler, so a later-ready Batch can use a free slot promptly.
  // Each worker handles one Batch at a time and immediately asks for another.
  const claimedBatchIds = new Set();
  const dispatcherState = { active: 0, ranAny: false, runningBatchIds: new Set() };
  const poolSize = Math.max(1, maxParallel);
  phase("Batch 阶段");
  try {
    await parallel(Array.from({ length: poolSize }, (_, index) => async () => {
      await runDispatchWorker(index + 1, claimedBatchIds, drainLabel, dispatcherState);
    }));
  } catch (error) {
    recordUnresolved({ kind: "scheduler", status: "parallel_execution_failed", durable: false, error });
  }
  ranAny = dispatcherState.ranAny || schedulerCycles > 0;
  const snapshot = lastScheduler;
  return {
    ranAny,
    reason: snapshot && snapshot.activeWorkers > 0
      ? "other_active_workers"
      : snapshot && Array.isArray(snapshot.readyBatches) && snapshot.readyBatches.length
        ? "ready_batches_not_dispatchable"
        : "no_runnable_independent_batches",
  };
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

function skippedUtestDetails(scheduler = lastScheduler, verification = null) {
  const manifest = manifestFromScheduler(scheduler);
  if (manifest && Array.isArray(manifest.skippedUtests)) return manifest.skippedUtests;
  return verification && Array.isArray(verification.skippedUtests) ? verification.skippedUtests : [];
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
    ok: ["succeeded", "succeeded_with_issues", "awaiting_user_confirmation"].includes(finalStatus),
    feature,
    runId,
    batchResults,
    mergeResults,
    cleanupResults,
    lifecycleResults,
    e2e,
    verification,
    integrationWorkspaces: (verification && verification.integrationWorkspaces) || (scheduler && scheduler.integrationWorkspaces),
    mainPromotion: (verification && verification.mainPromotion) || (scheduler && scheduler.mainPromotion),
    finalStatus,
    retryPendingBatches: batches.filter(batch => batch.status === "retry_pending").map(batch => batch.batchId),
    blockedBatches: batches.filter(batch => ["blocked", "needs_resolution"].includes(batch.status)).map(batch => batch.batchId),
    deferredIssues,
    e2eSkippedReason,
    skippedUtests: skippedUtestDetails(scheduler, verification),
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
  // A new drain is not a new retry budget or dispatch generation.
  for (const batch of unresolvedBatchDetails()) {
    if (batch.status !== "blocked" || batch.recoveryStatus !== "retry_exhausted") continue;
    finalRepairResults.push({ kind: "retry_budget_exhausted", batchId: batch.batchId,
      status: "blocked", durable: true, error: batch.error });
  }
}

async function attemptFinalCandidateRepair(record) {
  if (!usableString(record.repositoryRef) || !Number.isFinite(Number(record.wave))) return;
  const batchIds = Array.isArray(record.batchIds) && record.batchIds.length
    ? record.batchIds
    : (record.batchId ? [record.batchId] : []);
  try {
    const resolved = await resolveMergeCandidate(record.repositoryRef, record.wave,
      `final-repair-candidate-${record.repositoryRef}-${record.wave}`, "最终修复与报告");
    if (!resolved || resolved.status !== "built" || resolved.success === false || hasFailureSignal(resolved)) {
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
      `推广已在最终修复阶段恢复的候选。执行 python -X utf8 "${mergeTrainPath}" promote-candidate --allow-unverified --allow-stale ` +
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
          ...(repairState.implementationRecoveryBatches || []).map(item => item && item.batchId),
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

// Drain every currently runnable branch, including retry-pending Batches.
// Retry candidates stay behind all available non-retry work so independent
// branches and their newly-ready downstream waves get dispatched first.
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
if (prepared.integrationMode === "isolated") {
  try {
    requireSuccess(await workflowAgent(
      `执行 python -X utf8 "${joinPath(pluginPath, "hooks/parallel_integration.py")}" sync-main --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
      `只同步目标主分支提交到集成 Worktree，不得修改主分支；若存在冲突，原样返回插件状态和 conflicts，保留集成 Worktree 供用户检查。只返回 JSON。`,
      { label: "sync-main-into-integration", phase: "最终验证" }
    ), "integration main sync");
  } catch (error) {
    return completionReport({ finalStatus: "integration_sync_required", scheduler: finalScheduler || lastScheduler,
      verification: { error: errorText(error) }, nextAction: "inspect_integration_sync_then_resume" });
  }
}
let e2eStarted;
let e2e;
try {
  e2eStarted = requireSuccess(await workflowAgent(
    `所有 delivery Batch 已推广后，创建 B-E2E。执行 python -X utf8 "${mergeTrainPath}" begin-e2e --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
    `此命令创建当前集成 SHA 绑定的验证状态（旧 direct run 绑定原基线分支）；它不运行 Batch UTest。只返回 JSON。`,
    { label: "begin-e2e-validation", phase: "最终验证" }
  ), "begin e2e");
  e2e = requireSuccess(await workflowAgent(
    `在全部 Batch 已合入的最终集成提交上执行唯一的 B-E2E 验证。Feature=${feature}，runId=${runId}。` +
    `必须只在这些插件创建的临时验证 Worktree 中操作：${JSON.stringify(e2eStarted.worktrees || {})}；不得操作主 checkout。` +
    `先收集可重现环境元数据：environment.version、environment.seedDataDigest、environment.dependencies（对象，含 DB/Redis/MQ 等实际版本或明确的 none），并将其与场景摘要一并作为 JSON metadata。` +
    `执行 Plan/Feature 定义且未被 Batch UTest 覆盖的端到端场景，不得重复执行 Batch test。` +
    `通过后执行 python -X utf8 "${mergeTrainPath}" finish-e2e --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}" --passed true --metadata-json '<含上述 environment 与场景摘要的 JSON>'。` +
    `失败时使用 --passed false 并记录失败摘要；失败会创建受控修复入口，禁止在基线分支直接修复。只返回 JSON。`,
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
    `执行 python -X utf8 "${aggregatePath}" --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
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

if (verification.integrationMode === "isolated") {
  try {
    const promotion = requireSuccess(await workflowAgent(
      `执行 python -X utf8 "${joinPath(pluginPath, "hooks/parallel_integration.py")}" prepare --workspace "${artifactWorkspace}" --feature "${feature}" --run-id "${runId}"。` +
      `只准备可审阅的最终合并报告和 approvalDigest，不得执行 promote、修改主分支或替用户确认。完整返回 JSON。`,
      { label: "prepare-user-confirmed-promotion", phase: "最终验证" }
    ), "prepare main promotion");
    verification.mainPromotion = promotion.mainPromotion;
    return completionReport({ finalStatus: "awaiting_user_confirmation", scheduler: finalScheduler || lastScheduler,
      e2e, verification, nextAction: "review_integration_and_confirm_promotion" });
  } catch (error) {
    verification.promotionPreparationError = errorText(error);
    return completionReport({ finalStatus: "awaiting_user_confirmation", scheduler: finalScheduler || lastScheduler,
      e2e, verification, nextAction: "sync_target_if_changed_then_prepare_promotion" });
  }
}

return completionReport({
  finalStatus: verification.hasDeferredIssues ? "succeeded_with_issues" : "succeeded",
  scheduler: finalScheduler || lastScheduler,
  e2e,
  verification,
  nextAction: verification.hasDeferredIssues ? "review_deferred_issues" : "completed",
});
