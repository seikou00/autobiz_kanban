import assert from "node:assert/strict"
import test from "node:test"

import { parseJUnitXml } from "../src/junit.ts"
import { decodeTrace, summarizeTraces, validateControlTraces, validateFullChainTraces } from "../src/trace-reader.ts"
import type { WorkflowStageRecord } from "../src/types.ts"

test("parses pass, failure, error, and skipped JUnit cases", () => {
  const cases = parseJUnitXml(`
    <testsuite>
      <testcase name="pass" time="0.1"/>
      <testcase name="fail" time="0.2"><failure message="bad &amp; wrong"/></testcase>
      <testcase name="error"><error message="boom"/></testcase>
      <testcase name="skip"><skipped/></testcase>
    </testsuite>
  `)

  assert.deepEqual(cases.map((item) => item.status), ["passed", "failed", "error", "skipped"])
  assert.equal(cases[1]?.message, "bad & wrong")
})

function nativeTrace(overrides: Record<string, unknown> = {}) {
  return decodeTrace({
    traceId: "trace-1",
    threadId: "thread-1",
    startedAt: "2026-08-12T00:00:00.000Z",
    endedAt: "2026-08-12T00:01:00.000Z",
    durationMs: 60_000,
    userMessage: "run",
    modelId: "model-1",
    appVersion: "1.4.9",
    steps: [{ toolCalls: [{ name: "read_file" }, { name: "edit_file" }] }],
    modelCalls: [{ tokenUsage: { inputTokens: 100, outputTokens: 20, totalTokens: 120 } }],
    totalToolCalls: 2,
    outcome: "success",
    usedSkills: ["autodev-specs"],
    skillSource: ["plugin:plugin-1/skills/autodev-specs"],
    triggerSource: "user",
    harnessProjectId: "project-1",
    harnessFeatureSlug: "pet-weight-tracking",
    harnessNodeName: "Dev-规格",
    harnessNodeStatus: "进行中",
    harnessAdapterId: "plugin-1",
    harnessAdapterName: "AutobizDevOps",
    harnessAdapterVersion: "1.0.83",
    metadata: { routingTrace: { finalDecision: "normal" } },
    ...overrides
  })
}

test("summarizes native traces and validates full-chain attribution", () => {
  const trace = nativeTrace()
  const stage: WorkflowStageRecord = {
    nodeId: "dev.specs",
    skill: "autodev-specs",
    threadId: "thread-1",
    beforeStatus: "pending",
    afterStatus: "done",
    startedAt: "2026-08-12T00:00:00.000Z",
    endedAt: "2026-08-12T00:01:00.000Z",
    outcome: "success",
    userInput: []
  }

  const summary = summarizeTraces([trace])
  assert.equal(summary.totalTokens, 120)
  assert.equal(summary.toolCalls, 2)
  assert.doesNotThrow(() => validateFullChainTraces([trace], [stage], "plugin-1", "1.0.83", "1.4.9", "model-1"))
})

test("control trace validation rejects target plugin attribution", () => {
  const trace = nativeTrace({ skillSource: ["plugin:target-plugin/skills/autodev-specs"] })
  assert.throws(() => validateControlTraces([trace], "target-plugin", "1.4.9", "model-1"), /control trace/)
})

test("full-chain trace validation rejects missing native routing evidence", () => {
  const trace = nativeTrace({ metadata: {} })
  const stage: WorkflowStageRecord = {
    nodeId: "dev.specs",
    skill: "autodev-specs",
    threadId: "thread-1",
    beforeStatus: "pending",
    afterStatus: "done",
    startedAt: "2026-08-12T00:00:00.000Z",
    endedAt: "2026-08-12T00:01:00.000Z",
    outcome: "success",
    userInput: []
  }
  assert.throws(
    () => validateFullChainTraces([trace], [stage], "plugin-1", "1.0.83", "1.4.9", "model-1"),
    /routingTrace/
  )
})

test("trace decoding rejects malformed token counters", () => {
  assert.throws(() => nativeTrace({ modelCalls: [{ tokenUsage: { totalTokens: "120" } }] }), /tokenUsage/)
})
