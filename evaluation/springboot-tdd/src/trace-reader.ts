import { readFileSync, readdirSync, statSync } from "node:fs"
import { resolve } from "node:path"

import { asRecord } from "./codec.ts"
import { EvalError } from "./errors.ts"
import type { AgentTrace, TraceSummary, WorkflowStageRecord } from "./types.ts"

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

function stringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new EvalError("agent", `trace.${field} 必须是字符串数组`, "检查固定 CMBDevClaw trace schema。")
  }
  return value as string[]
}

function numberField(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new EvalError("agent", `trace.${field} 必须是非负数`, "检查固定 CMBDevClaw trace schema。")
  }
  return value
}

export function decodeTrace(value: unknown): AgentTrace {
  const record = asRecord(value, "trace")
  const outcome = optionalString(record.outcome)
  if (!outcome || !["success", "error", "cancelled", "unknown"].includes(outcome)) {
    throw new EvalError("agent", "trace.outcome 非法", "检查 trace 是否完整 flush。")
  }
  if (!Array.isArray(record.steps)) throw new EvalError("agent", "trace.steps 缺失", "检查 trace schema。")
  const steps = record.steps.map((raw, index) => {
    const step = asRecord(raw, `trace.steps[${index}]`)
    if (!Array.isArray(step.toolCalls)) throw new EvalError("agent", `trace.steps[${index}].toolCalls 缺失`, "检查 trace schema。")
    return {
      toolCalls: step.toolCalls.map((tool, toolIndex) => {
        const call = asRecord(tool, `trace.steps[${index}].toolCalls[${toolIndex}]`)
        return { name: optionalString(call.name) ?? "unknown" }
      })
    }
  })
  const modelCalls = Array.isArray(record.modelCalls)
    ? record.modelCalls.map((raw) => {
        const call = asRecord(raw, "trace.modelCalls[]")
        let tokenUsage: Record<string, number> | undefined
        if (call.tokenUsage !== undefined) {
          const usage = asRecord(call.tokenUsage, "trace.modelCalls[].tokenUsage")
          if (Object.values(usage).some((value) => typeof value !== "number" || !Number.isFinite(value) || value < 0)) {
            throw new EvalError("agent", "trace tokenUsage 包含非法计数", "检查固定 CMBDevClaw trace schema。")
          }
          tokenUsage = usage as Record<string, number>
        }
        return tokenUsage ? { tokenUsage } : {}
      })
    : undefined
  const trace: AgentTrace = {
    traceId: optionalString(record.traceId) ?? "",
    threadId: optionalString(record.threadId) ?? "",
    startedAt: optionalString(record.startedAt) ?? "",
    endedAt: optionalString(record.endedAt) ?? "",
    durationMs: numberField(record.durationMs, "durationMs"),
    userMessage: typeof record.userMessage === "string" ? record.userMessage : "",
    modelId: optionalString(record.modelId) ?? "",
    steps,
    totalToolCalls: numberField(record.totalToolCalls, "totalToolCalls"),
    outcome: outcome as AgentTrace["outcome"],
    usedSkills: stringArray(record.usedSkills, "usedSkills"),
    ...(modelCalls ? { modelCalls } : {}),
    ...(Array.isArray(record.nodes) ? { nodes: record.nodes } : {}),
    ...(optionalString(record.modelName) ? { modelName: optionalString(record.modelName)! } : {}),
    ...(optionalString(record.appVersion) ? { appVersion: optionalString(record.appVersion)! } : {}),
    ...(record.skillSource !== undefined ? { skillSource: stringArray(record.skillSource, "skillSource") } : {}),
    ...(optionalString(record.triggerSource) ? { triggerSource: optionalString(record.triggerSource)! } : {}),
    ...(optionalString(record.harnessProjectId) ? { harnessProjectId: optionalString(record.harnessProjectId)! } : {}),
    ...(optionalString(record.harnessFeatureSlug) ? { harnessFeatureSlug: optionalString(record.harnessFeatureSlug)! } : {}),
    ...(optionalString(record.harnessNodeName) ? { harnessNodeName: optionalString(record.harnessNodeName)! } : {}),
    ...(optionalString(record.harnessNodeStatus) ? { harnessNodeStatus: optionalString(record.harnessNodeStatus)! } : {}),
    ...(optionalString(record.harnessAdapterId) ? { harnessAdapterId: optionalString(record.harnessAdapterId)! } : {}),
    ...(optionalString(record.harnessAdapterName) ? { harnessAdapterName: optionalString(record.harnessAdapterName)! } : {}),
    ...(optionalString(record.harnessAdapterVersion) ? { harnessAdapterVersion: optionalString(record.harnessAdapterVersion)! } : {}),
    ...(record.metadata && typeof record.metadata === "object" && !Array.isArray(record.metadata)
      ? { metadata: record.metadata as Record<string, unknown> }
      : {})
  }
  if (!trace.traceId || !trace.threadId || !trace.startedAt || !trace.endedAt || !trace.modelId) {
    throw new EvalError("agent", "trace 缺少稳定标识/时间/modelId", "检查 trace 文件是否损坏或版本是否匹配。")
  }
  return trace
}

function traceFiles(root: string): string[] {
  const files: string[] = []
  const visit = (path: string): void => {
    for (const name of readdirSync(path).sort()) {
      const child = resolve(path, name)
      if (statSync(child).isDirectory()) visit(child)
      else if (name.endsWith(".jsonl")) files.push(child)
    }
  }
  visit(root)
  return files
}

export function readTraces(root: string): AgentTrace[] {
  const traces: AgentTrace[] = []
  const ids = new Set<string>()
  for (const path of traceFiles(root)) {
    const lines = readFileSync(path, "utf8").split(/\r?\n/).filter((line) => line.trim())
    for (const [index, line] of lines.entries()) {
      let raw: unknown
      try {
        raw = JSON.parse(line)
      } catch (error) {
        throw new EvalError("agent", `trace JSONL 损坏：${path}:${index + 1}`, "重新运行该 run；不得忽略损坏行。", error)
      }
      const trace = decodeTrace(raw)
      if (ids.has(trace.traceId)) throw new EvalError("agent", `traceId 重复：${trace.traceId}`, "隔离 trace root 后重跑。")
      ids.add(trace.traceId)
      traces.push(trace)
    }
  }
  return traces.sort((left, right) => left.startedAt.localeCompare(right.startedAt))
}

export function summarizeTraces(traces: AgentTrace[]): TraceSummary {
  const summary: TraceSummary = {
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
  const threadIds = new Set<string>()
  const skills = new Set<string>()
  const sources = new Set<string>()
  for (const trace of traces.filter((item) => item.triggerSource !== "internal_notification")) {
    summary.traceIds.push(trace.traceId)
    threadIds.add(trace.threadId)
    summary.durationMs += trace.durationMs
    summary.toolCalls += trace.totalToolCalls
    summary.modelCalls += trace.modelCalls?.length ?? 0
    for (const call of trace.modelCalls ?? []) {
      summary.inputTokens += call.tokenUsage?.inputTokens ?? 0
      summary.outputTokens += call.tokenUsage?.outputTokens ?? 0
      summary.totalTokens += call.tokenUsage?.totalTokens ?? 0
    }
    for (const skill of trace.usedSkills) skills.add(skill)
    for (const source of trace.skillSource ?? []) sources.add(source)
  }
  summary.threadIds = [...threadIds].sort()
  summary.usedSkills = [...skills].sort()
  summary.skillSource = [...sources].sort()
  return summary
}

function traceUsesSkill(trace: AgentTrace, skill: string): boolean {
  return trace.usedSkills.some((value) => value === skill || value.startsWith(`${skill}-`))
}

export function validateFullChainTraces(
  traces: AgentTrace[],
  stages: WorkflowStageRecord[],
  pluginId: string,
  pluginVersion: string,
  appVersion: string,
  modelId: string
): void {
  for (const stage of stages) {
    const matches = traces.filter((trace) => trace.threadId === stage.threadId && trace.triggerSource !== "internal_notification")
    if (matches.length === 0) throw new EvalError("agent", `阶段 ${stage.nodeId} 没有 native trace`, "等待 trace flush 后重跑。")
    if (!matches.some((trace) => traceUsesSkill(trace, stage.skill))) {
      throw new EvalError("agent", `阶段 ${stage.nodeId} 未归因到 ${stage.skill}`, "确认 nextAction 通过 CMBDevClaw 插件 Skill 执行。")
    }
    for (const trace of matches) {
      if (trace.appVersion !== appVersion) throw new EvalError("agent", `trace appVersion 错误：${String(trace.appVersion)}`, "使用固定 CMBDevClaw build。")
      if (trace.modelId !== modelId) throw new EvalError("agent", `trace modelId 错误：${trace.modelId}`, "所有 run 使用固定模型配置。")
      if (trace.harnessProjectId === undefined || trace.harnessFeatureSlug === undefined) {
        throw new EvalError("agent", `阶段 ${stage.nodeId} 缺少 Harness 归因`, "使用绑定 Feature 的项目会话。")
      }
      if (!trace.harnessNodeName || !trace.harnessNodeStatus) {
        throw new EvalError("agent", `阶段 ${stage.nodeId} 缺少 Harness 节点归因`, "确认项目会话绑定当前 Feature 节点。")
      }
      if (trace.harnessAdapterId !== pluginId || trace.harnessAdapterVersion !== pluginVersion) {
        throw new EvalError("agent", `阶段 ${stage.nodeId} 的 Harness adapter 版本不匹配`, "使用本 batch 安装的固定插件。")
      }
      if (!(trace.skillSource ?? []).some((source) => source.startsWith(`plugin:${pluginId}/`))) {
        throw new EvalError("agent", `阶段 ${stage.nodeId} 的 Skill 来源不是目标插件`, "检查安装后的 plugin ID 与 Skill source。")
      }
      const routingTrace = trace.metadata?.routingTrace
      if (!routingTrace || typeof routingTrace !== "object" || Array.isArray(routingTrace)) {
        throw new EvalError("agent", `阶段 ${stage.nodeId} 缺少 routingTrace`, "确认固定 CMBDevClaw 路由证据完整落盘。")
      }
    }
  }
}

export function validateControlTraces(
  traces: AgentTrace[],
  pluginIdOrName: string,
  appVersion: string,
  modelId: string
): void {
  for (const trace of traces) {
    if (trace.appVersion !== appVersion) throw new EvalError("agent", `control trace appVersion 错误：${String(trace.appVersion)}`, "使用固定 CMBDevClaw build。")
    if (trace.modelId !== modelId) throw new EvalError("agent", `control trace modelId 错误：${trace.modelId}`, "所有 run 使用固定模型配置。")
    if ((trace.skillSource ?? []).some((source) => source.includes(pluginIdOrName))) {
      throw new EvalError("agent", "control trace 出现目标插件 Skill 来源", "删除被污染的 app home 后重跑。")
    }
  }
}
