import type {
  BenchmarkConfig,
  FailureClass,
  RunPlan,
  RunResult,
  TraceSummary,
  VerifierResult,
  WorkflowStageRecord
} from "./types.ts"

export const EMPTY_TRACE_SUMMARY: TraceSummary = {
  traceIds: [],
  threadIds: [],
  durationMs: 0,
  toolCalls: 0,
  modelCalls: 0,
  inputTokens: 0,
  outputTokens: 0,
  totalTokens: 0,
  usedSkills: [],
  skillSource: []
}

function testCounts(verifier: VerifierResult): RunResult["tests"] {
  return verifier.tests.reduce<RunResult["tests"]>((counts, test) => {
    if (test.status === "passed") counts.passed += 1
    if (test.status === "failed") counts.failed += 1
    if (test.status === "error") counts.errors += 1
    if (test.status === "skipped") counts.skipped += 1
    return counts
  }, { passed: 0, failed: 0, errors: 0, skipped: 0 })
}

export function evaluateRun(
  config: BenchmarkConfig,
  plan: RunPlan,
  fingerprint: string,
  traces: TraceSummary,
  stages: WorkflowStageRecord[],
  verifier: VerifierResult,
  pluginVersion?: string
): RunResult {
  return {
    schemaVersion: 1,
    runId: plan.id,
    benchmarkId: config.benchmarkId,
    taskId: plan.taskId,
    condition: plan.condition,
    repeat: plan.repeat,
    fingerprint,
    completed: true,
    resolved: verifier.resolved,
    ...(verifier.resolved ? {} : { failureClass: "task" as const }),
    scores: verifier.scores,
    tests: testCounts(verifier),
    usage: traces,
    traceIds: traces.traceIds,
    stageCount: stages.length,
    appVersion: config.app.version,
    ...(pluginVersion ? { pluginVersion } : {})
  }
}

export function failedRun(
  config: BenchmarkConfig,
  plan: RunPlan,
  fingerprint: string,
  failureClass: FailureClass,
  error: string,
  traces: TraceSummary = EMPTY_TRACE_SUMMARY,
  stages: WorkflowStageRecord[] = []
): RunResult {
  return {
    schemaVersion: 1,
    runId: plan.id,
    benchmarkId: config.benchmarkId,
    taskId: plan.taskId,
    condition: plan.condition,
    repeat: plan.repeat,
    fingerprint,
    completed: true,
    resolved: false,
    failureClass,
    error,
    scores: { build: 0, regression: 0, feature: 0, integration: 0 },
    tests: { passed: 0, failed: 0, errors: 0, skipped: 0 },
    usage: traces,
    traceIds: traces.traceIds,
    stageCount: stages.length,
    appVersion: config.app.version
  }
}
