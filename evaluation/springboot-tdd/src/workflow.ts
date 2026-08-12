import { asRecord } from "./codec.ts"
import { EvalError } from "./errors.ts"
import type { WorkflowNextAction } from "./types.ts"

export interface RunDetailProjection {
  projectId: string
  slug: string
  currentNodeId: string
  currentNodeStatus: string
  nodeStatuses: Record<string, string>
  nextAction?: WorkflowNextAction
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

export function normalizeNextAction(value: unknown): WorkflowNextAction | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  const preferred = record.preferredPlugin && typeof record.preferredPlugin === "object" && !Array.isArray(record.preferredPlugin)
    ? record.preferredPlugin as Record<string, unknown>
    : undefined
  const action: WorkflowNextAction = {}
  const slashSkill = optionalString(record.slashSkill)
  const userMessage = optionalString(record.userMessage)
  const dialogTips = optionalString(record.dialogTips)
  const preferredId = optionalString(preferred?.id)
  const preferredName = optionalString(preferred?.name)
  if (slashSkill) action.slashSkill = slashSkill
  if (userMessage) action.userMessage = userMessage
  if (dialogTips) action.dialogTips = dialogTips
  if (preferredId || preferredName) {
    action.preferredPlugin = {
      ...(preferredId ? { id: preferredId } : {}),
      ...(preferredName ? { name: preferredName } : {})
    }
  }
  return Object.keys(action).length > 0 ? action : undefined
}

export function decodeRunDetail(value: unknown): RunDetailProjection {
  const root = asRecord(value, "Harness run detail")
  const project = asRecord(root.project, "Harness run detail.project")
  const run = asRecord(root.run, "Harness run detail.run")
  const workflow = asRecord(root.workflow, "Harness run detail.workflow")
  const projectId = optionalString(project.projectId)
  const slug = optionalString(run.slug)
  const currentNodeId = optionalString(run.currentNodeId)
  if (!projectId || !slug || !currentNodeId) {
    throw new EvalError("agent", "Harness run detail 缺少 projectId/slug/currentNodeId", "刷新 Feature 状态并检查插件 board 输出。")
  }
  if (!Array.isArray(run.nodes) || !Array.isArray(workflow.nodes)) {
    throw new EvalError("agent", "Harness run detail 缺少 nodes", "检查固定 CMBDevClaw trace/board schema。")
  }
  const nodeStatuses: Record<string, string> = {}
  for (const rawNode of run.nodes) {
    if (!rawNode || typeof rawNode !== "object" || Array.isArray(rawNode)) continue
    const node = rawNode as Record<string, unknown>
    const id = optionalString(node.id)
    const status = optionalString(node.nodeStatus)
    if (id && status) nodeStatuses[id] = status
  }
  const currentNodeStatus = nodeStatuses[currentNodeId]
  if (!currentNodeStatus) {
    throw new EvalError("agent", `当前节点 ${currentNodeId} 没有 nodeStatus`, "检查插件 inspect_state 输出。")
  }
  const workflowNode = workflow.nodes.find((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return false
    return optionalString((item as Record<string, unknown>).id) === currentNodeId
  }) as Record<string, unknown> | undefined
  const nodeStates = Array.isArray(workflowNode?.states) ? workflowNode.states : []
  const globalStates = Array.isArray(workflow.states) ? workflow.states : []
  const state = [...nodeStates, ...globalStates].find((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return false
    return optionalString((item as Record<string, unknown>).nodeStatus) === currentNodeStatus
  }) as Record<string, unknown> | undefined
  const nextAction = normalizeNextAction(state?.nextAction)
  return {
    projectId,
    slug,
    currentNodeId,
    currentNodeStatus,
    nodeStatuses,
    ...(nextAction ? { nextAction } : {})
  }
}

export function assertWorkflowAction(
  detail: RunDetailProjection,
  expectedNodeId: string,
  expectedSkill: string
): WorkflowNextAction {
  if (detail.currentNodeId !== expectedNodeId) {
    throw new EvalError(
      "agent",
      `Harness 当前节点不匹配：期望 ${expectedNodeId}，实际 ${detail.currentNodeId}`,
      "刷新 Feature 状态并检查 custom workflow 节点顺序。"
    )
  }
  if (detail.nextAction?.slashSkill !== expectedSkill) {
    throw new EvalError(
      "agent",
      `Harness nextAction 不匹配：期望 /${expectedSkill}，实际 /${detail.nextAction?.slashSkill ?? "(none)"}`,
      `检查 ${expectedNodeId} 的 board state 和 custom workflow。`
    )
  }
  return detail.nextAction
}

export function assertWorkflowHandoff(
  detail: RunDetailProjection,
  completedNodeId: string,
  expectedSkill: string
): WorkflowNextAction {
  if (detail.currentNodeId !== completedNodeId || detail.nodeStatuses[completedNodeId] !== "done") {
    throw new EvalError(
      "agent",
      `Harness 交接节点不匹配：期望 ${completedNodeId}/done，实际 ${detail.currentNodeId}/${detail.currentNodeStatus}`,
      "刷新 Feature 状态并检查上一节点 checkpoint。"
    )
  }
  if (detail.nextAction?.slashSkill !== expectedSkill) {
    throw new EvalError(
      "agent",
      `Harness 交接 nextAction 不匹配：期望 /${expectedSkill}，实际 /${detail.nextAction?.slashSkill ?? "(none)"}`,
      `检查 ${completedNodeId} 完成态的 board nextAction。`
    )
  }
  return detail.nextAction
}

export function assertWorkflowProgress(before: RunDetailProjection, after: RunDetailProjection): void {
  const changed = before.currentNodeId !== after.currentNodeId
    || before.currentNodeStatus !== after.currentNodeStatus
    || before.nextAction?.slashSkill !== after.nextAction?.slashSkill
  if (!changed) {
    throw new EvalError("agent", `workflow 无进展：${before.currentNodeId}/${before.currentNodeStatus}`, "查看该线程的 hook 日志和阶段产物。")
  }
}
