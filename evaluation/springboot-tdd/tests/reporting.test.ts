import assert from "node:assert/strict"
import test from "node:test"

import { compareResults, comparisonMarkdown } from "../src/reporting.ts"
import type { ConditionId, RunResult } from "../src/types.ts"

function result(condition: ConditionId, repeat: number, resolved: boolean, tokens: number): RunResult {
  return {
    schemaVersion: 1,
    runId: `${condition}-${repeat}`,
    benchmarkId: "benchmark-1",
    taskId: "springboot-tdd",
    condition,
    repeat,
    fingerprint: "fixed-fingerprint",
    completed: true,
    resolved,
    scores: {
      build: resolved ? 1 : 0,
      regression: resolved ? 1 : 0,
      feature: resolved ? 1 : 0,
      integration: resolved ? 1 : 0
    },
    tests: { passed: resolved ? 12 : 0, failed: resolved ? 0 : 12, errors: 0, skipped: 0 },
    usage: {
      traceIds: [`trace-${condition}-${repeat}`],
      threadIds: [`thread-${condition}-${repeat}`],
      durationMs: tokens * 10,
      toolCalls: tokens / 10,
      modelCalls: 1,
      inputTokens: tokens - 10,
      outputTokens: 10,
      totalTokens: tokens,
      usedSkills: [],
      skillSource: []
    },
    traceIds: [`trace-${condition}-${repeat}`],
    stageCount: condition === "full-chain" ? 6 : 1,
    appVersion: "1.4.9"
  }
}

test("compares balanced results and reports full-chain minus control", () => {
  const report = compareResults([
    result("control", 1, false, 100),
    result("control", 2, true, 120),
    result("full-chain", 1, true, 200),
    result("full-chain", 2, true, 220)
  ])

  assert.equal(report.conditions.control.resolvedRate, 0.5)
  assert.equal(report.conditions["full-chain"].resolvedRate, 1)
  assert.equal(report.delta.resolvedRate, 0.5)
  assert.equal(report.delta.tokens, 100)
  assert.match(comparisonMarkdown(report), /Full-chain − control/)
})

test("rejects comparison across different fingerprints", () => {
  const control = result("control", 1, false, 100)
  const full = { ...result("full-chain", 1, true, 200), fingerprint: "other" }

  assert.throws(() => compareResults([control, full]), /fingerprint\/benchmarkId 不一致/)
})

test("rejects an incomplete or unbalanced final repeat matrix", () => {
  const balancedTwo = [
    result("control", 1, false, 100),
    result("control", 2, true, 120),
    result("full-chain", 1, true, 200),
    result("full-chain", 2, true, 220)
  ]
  assert.throws(() => compareResults(balancedTwo, 3), /各 3 次完整重复/)
  assert.throws(() => compareResults([
    result("control", 1, false, 100),
    result("control", 2, true, 120),
    result("full-chain", 1, true, 200)
  ]), /repeat 不平衡/)
})
