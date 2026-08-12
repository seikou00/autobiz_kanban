import { readFileSync, readdirSync } from "node:fs"
import { resolve } from "node:path"

import { EvalError } from "./errors.ts"
import { writeJson } from "./io.ts"
import type { ConditionId, RunResult } from "./types.ts"

interface ConditionAggregate {
  runs: number
  resolved: number
  resolvedRate: number
  scores: { build: number; regression: number; feature: number; integration: number }
  tests: { passed: number; failed: number; errors: number; skipped: number }
  usage: { tokens: number; durationMs: number; modelCalls: number; toolCalls: number }
  failureClasses: Record<string, number>
}

export interface ComparisonReport {
  schemaVersion: 1
  benchmarkId: string
  fingerprint: string
  conditions: Record<ConditionId, ConditionAggregate>
  delta: {
    resolvedRate: number
    build: number
    regression: number
    feature: number
    integration: number
    tokens: number
    durationMs: number
    modelCalls: number
    toolCalls: number
  }
}

function mean(total: number, count: number): number {
  return count === 0 ? 0 : total / count
}

function aggregate(results: RunResult[]): ConditionAggregate {
  const count = results.length
  const failures: Record<string, number> = {}
  for (const result of results) {
    if (result.failureClass) failures[result.failureClass] = (failures[result.failureClass] ?? 0) + 1
  }
  return {
    runs: count,
    resolved: results.filter((result) => result.resolved).length,
    resolvedRate: mean(results.filter((result) => result.resolved).length, count),
    scores: {
      build: mean(results.reduce((sum, item) => sum + item.scores.build, 0), count),
      regression: mean(results.reduce((sum, item) => sum + item.scores.regression, 0), count),
      feature: mean(results.reduce((sum, item) => sum + item.scores.feature, 0), count),
      integration: mean(results.reduce((sum, item) => sum + item.scores.integration, 0), count)
    },
    tests: {
      passed: results.reduce((sum, item) => sum + item.tests.passed, 0),
      failed: results.reduce((sum, item) => sum + item.tests.failed, 0),
      errors: results.reduce((sum, item) => sum + item.tests.errors, 0),
      skipped: results.reduce((sum, item) => sum + item.tests.skipped, 0)
    },
    usage: {
      tokens: mean(results.reduce((sum, item) => sum + item.usage.totalTokens, 0), count),
      durationMs: mean(results.reduce((sum, item) => sum + item.usage.durationMs, 0), count),
      modelCalls: mean(results.reduce((sum, item) => sum + item.usage.modelCalls, 0), count),
      toolCalls: mean(results.reduce((sum, item) => sum + item.usage.toolCalls, 0), count)
    },
    failureClasses: failures
  }
}

function assertBalancedMatrix(results: RunResult[], expectedRepeats?: number): void {
  if (results.some((result) => !result.completed)) {
    throw new EvalError("setup", "结果中包含未完成 run", "完成所有 run 后再比较。")
  }
  const repeatsByCondition = (["control", "full-chain"] as const).map((condition) => {
    const repeats = results.filter((result) => result.condition === condition).map((result) => result.repeat).sort((a, b) => a - b)
    if (new Set(repeats).size !== repeats.length) {
      throw new EvalError("setup", `${condition} 包含重复 repeat`, "每个 condition/repeat 只保留一个 result。")
    }
    return repeats
  })
  const [controlRepeats, fullRepeats] = repeatsByCondition
  if (JSON.stringify(controlRepeats) !== JSON.stringify(fullRepeats)) {
    throw new EvalError("setup", "control 与 full-chain 的 repeat 不平衡", "补齐相同 repeat 后再比较。")
  }
  if (expectedRepeats !== undefined) {
    const expected = Array.from({ length: expectedRepeats }, (_unused, index) => index + 1)
    if (JSON.stringify(controlRepeats) !== JSON.stringify(expected)) {
      throw new EvalError("setup", `比较必须包含两组各 ${expectedRepeats} 次完整重复`, "运行完整矩阵后再比较。")
    }
  }
}

export function compareResults(results: RunResult[], expectedRepeats?: number): ComparisonReport {
  if (results.length === 0) throw new EvalError("setup", "没有可比较 result", "先执行 run/evaluate。")
  if (results.some((result) => result.schemaVersion !== 2)) {
    throw new EvalError("setup", "结果包含旧版 run schema", "先用 evaluate 重评旧 run，再执行 compare。")
  }
  const scorableFailures = new Set(["task", "agent", "timeout"])
  const invalidFailures = results.filter(
    (result) => result.failureClass && !scorableFailures.has(result.failureClass)
  )
  if (invalidFailures.length > 0) {
    throw new EvalError(
      "setup",
      `结果包含不可计分失败：${invalidFailures.map((result) => `${result.runId}=${String(result.failureClass)}`).join(", ")}`,
      "修复运行环境并仅重评这些 run，再执行 compare。"
    )
  }
  const fingerprints = new Set(results.map((result) => result.fingerprint))
  const benchmarkIds = new Set(results.map((result) => result.benchmarkId))
  if (fingerprints.size !== 1 || benchmarkIds.size !== 1) {
    throw new EvalError("setup", "结果 fingerprint/benchmarkId 不一致", "只比较同一固定 app/plugin/task/model/toolchain batch。")
  }
  const controlResults = results.filter((result) => result.condition === "control")
  const fullResults = results.filter((result) => result.condition === "full-chain")
  if (controlResults.length === 0 || fullResults.length === 0) {
    throw new EvalError("setup", "比较需要 control 与 full-chain 两组", "补齐两组结果。")
  }
  assertBalancedMatrix(results, expectedRepeats)
  const control = aggregate(controlResults)
  const full = aggregate(fullResults)
  return {
    schemaVersion: 1,
    benchmarkId: results[0]!.benchmarkId,
    fingerprint: results[0]!.fingerprint,
    conditions: { control, "full-chain": full },
    delta: {
      resolvedRate: full.resolvedRate - control.resolvedRate,
      build: full.scores.build - control.scores.build,
      regression: full.scores.regression - control.scores.regression,
      feature: full.scores.feature - control.scores.feature,
      integration: full.scores.integration - control.scores.integration,
      tokens: full.usage.tokens - control.usage.tokens,
      durationMs: full.usage.durationMs - control.usage.durationMs,
      modelCalls: full.usage.modelCalls - control.usage.modelCalls,
      toolCalls: full.usage.toolCalls - control.usage.toolCalls
    }
  }
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}

export function comparisonMarkdown(report: ComparisonReport): string {
  const rows = (["control", "full-chain"] as const).map((id) => {
    const item = report.conditions[id]
    return `| ${id} | ${item.resolved}/${item.runs} (${(item.resolvedRate * 100).toFixed(1)}%) | ${formatNumber(item.scores.build)} | ${formatNumber(item.scores.regression)} | ${formatNumber(item.scores.feature)} | ${formatNumber(item.scores.integration)} | ${formatNumber(item.usage.tokens)} | ${formatNumber(item.usage.durationMs)} | ${formatNumber(item.usage.modelCalls)} | ${formatNumber(item.usage.toolCalls)} |`
  })
  return [
    `# ${report.benchmarkId} comparison`,
    "",
    `Fingerprint: \`${report.fingerprint}\``,
    "",
    "| Condition | Resolved | Build | Regression | Feature | Integration | Avg tokens | Avg agent ms | Avg model calls | Avg tool calls |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...rows,
    "",
    "## Full-chain − control",
    "",
    `- Resolved rate: ${formatNumber(report.delta.resolvedRate)}`,
    `- Build: ${formatNumber(report.delta.build)}`,
    `- Regression: ${formatNumber(report.delta.regression)}`,
    `- Feature: ${formatNumber(report.delta.feature)}`,
    `- Integration: ${formatNumber(report.delta.integration)}`,
    `- Tokens: ${formatNumber(report.delta.tokens)}`,
    `- Agent duration ms: ${formatNumber(report.delta.durationMs)}`,
    `- Model calls: ${formatNumber(report.delta.modelCalls)}`,
    `- Tool calls: ${formatNumber(report.delta.toolCalls)}`,
    "",
    "Three repeats per condition are descriptive only; no significance claim is made.",
    ""
  ].join("\n")
}

export function readRunResults(reportRoot: string): RunResult[] {
  const results: RunResult[] = []
  for (const name of readdirSync(reportRoot).sort()) {
    const path = resolve(reportRoot, name, "result.json")
    try {
      results.push(JSON.parse(readFileSync(path, "utf8")) as RunResult)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue
      throw new EvalError("setup", `无法读取 result：${path}`, "修复或移走损坏的 result.json。", error)
    }
  }
  return results
}

export async function writeComparison(reportRoot: string, report: ComparisonReport): Promise<void> {
  await writeJson(resolve(reportRoot, "comparison.json"), report)
  const { writeFile } = await import("node:fs/promises")
  await writeFile(resolve(reportRoot, "comparison.md"), comparisonMarkdown(report), "utf8")
}
