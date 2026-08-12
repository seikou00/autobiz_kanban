import { readFile } from "node:fs/promises"
import { performance } from "node:perf_hooks"

import {
  assertPluginAbsent,
  appendSkillInvocation,
  createHarnessBinding,
  createThread,
  getRunDetail,
  installPlugin,
  invokeThread,
  selectPluginSkill,
  type InstalledPlugin
} from "./cmbdevclaw-driver.ts"
import { launchCmbDevClaw } from "./cmbdevclaw-app.ts"
import { EvalError } from "./errors.ts"
import {
  assertWorkflowAction,
  assertWorkflowHandoff,
  assertWorkflowProgress,
  decodeRunDetail,
  isWorkflowStageComplete
} from "./workflow.ts"
import type { BenchmarkConfig, PluginSnapshotManifest, RunPlan, WorkflowStageRecord } from "./types.ts"
import type { RunDirectories } from "./workspace.ts"

export interface AgentRunOutput {
  threadIds: string[]
  stages: WorkflowStageRecord[]
  plugin?: InstalledPlugin
}

export type AgentProgressListener = (output: AgentRunOutput) => void

const MAX_STAGE_TURNS = 3

function publishProgress(
  listener: AgentProgressListener | undefined,
  threadIds: string[],
  stages: WorkflowStageRecord[],
  plugin?: InstalledPlugin
): void {
  listener?.({
    threadIds: [...threadIds],
    stages: [...stages],
    ...(plugin ? { plugin } : {})
  })
}

export async function runAgent(
  config: BenchmarkConfig,
  plan: RunPlan,
  dirs: RunDirectories,
  snapshot: PluginSnapshotManifest,
  onProgress?: AgentProgressListener
): Promise<AgentRunOutput> {
  const session = await launchCmbDevClaw(config, dirs)
  const taskPrompt = await readFile(config.task.promptPath, "utf8")
  const started = performance.now()
  try {
    if (plan.condition === "control") {
      await assertPluginAbsent(session, config.plugin.expectedName)
      const threadId = await createThread(session, config, dirs.repo, `${plan.id} control`)
      publishProgress(onProgress, [threadId], [])
      const remainingMs = Math.floor(config.timeouts.totalMs - (performance.now() - started))
      if (remainingMs <= 0) throw new EvalError("timeout", "control 超过总超时", "检查 app 初始化耗时或提高 totalMs。")
      const result = await invokeThread(session, config, threadId, taskPrompt, dirs.repo, remainingMs)
      if (result.timedOut) throw new EvalError("timeout", result.error ?? "control timeout", "提高总超时或检查模型运行状态。")
      if (result.error) throw new EvalError("agent", `control agent 失败：${result.error}`, "查看 app/trace 日志。")
      await assertPluginAbsent(session, config.plugin.expectedName)
      return { threadIds: [threadId], stages: [] }
    }

    const plugin = await installPlugin(session, config, snapshot)
    const binding = await createHarnessBinding(
      session,
      config,
      plugin,
      dirs.pluginWorkspace,
      dirs.repo,
      plan.id
    )
    const stages: WorkflowStageRecord[] = []
    const threadIds: string[] = []
    publishProgress(onProgress, threadIds, stages, plugin)
    for (const [index, nodeId] of config.workflow.nodes.entries()) {
      const remainingMs = config.timeouts.totalMs - (performance.now() - started)
      if (remainingMs <= 0) {
        throw new EvalError("timeout", "full-chain 超过总超时", "检查卡住的阶段 trace 或提高 totalMs。")
      }
      const skill = config.workflow.skills[nodeId]!
      const before = decodeRunDetail(await getRunDetail(session, binding))
      const previousNode = config.workflow.nodes[index - 1]
      const action = previousNode
        ? assertWorkflowHandoff(before, previousNode, skill)
        : assertWorkflowAction(before, nodeId, skill)
      const selectedSkill = await selectPluginSkill(session, plugin, skill)
      const threadId = await createThread(session, config, dirs.repo, `${plan.id} ${nodeId}`, binding)
      threadIds.push(threadId)
      publishProgress(onProgress, threadIds, stages, plugin)
      const userMessage = index === 0
        ? `${action.userMessage ?? `请使用 /${skill} 继续推进当前 Feature。`}\n\n以下是本次唯一任务契约：\n\n${taskPrompt}`
        : action.userMessage ?? `请使用 /${skill} 继续推进当前 Feature。`
      const message = appendSkillInvocation(userMessage, selectedSkill)
      const stageStartedAt = new Date().toISOString()
      const stageStarted = performance.now()
      const userInput: WorkflowStageRecord["userInput"] = []
      const nextNode = config.workflow.nodes[index + 1]
      const nextSkill = nextNode ? config.workflow.skills[nextNode]! : undefined
      let after = before
      let outcome: WorkflowStageRecord["outcome"] = "success"
      let invokeError: string | undefined
      for (let turn = 0; turn < MAX_STAGE_TURNS; turn += 1) {
        const stageRemainingMs = config.timeouts.stageMs - (performance.now() - stageStarted)
        const totalRemainingMs = config.timeouts.totalMs - (performance.now() - started)
        if (stageRemainingMs <= 0 || totalRemainingMs <= 0) {
          outcome = "timeout"
          invokeError = `stage timeout after ${Math.floor(performance.now() - stageStarted)}ms`
          break
        }
        const turnMessage = turn === 0
          ? message
          : appendSkillInvocation(
              `当前 ${nodeId} 仍为 ${after.currentNodeStatus}，尚未满足阶段完成契约。请继续本轮 /${skill}，严格完成已读取 SKILL.md 中尚未执行的步骤，包括 stage gate、checkpoint 和完成后的 continuation guide；完成当前阶段后再结束，不要进入下一阶段。`,
              selectedSkill
            )
        const invoke = await invokeThread(
          session,
          config,
          threadId,
          turnMessage,
          dirs.repo,
          Math.max(1, Math.floor(Math.min(stageRemainingMs, totalRemainingMs))),
          {
            projectId: binding.projectId,
            slug: binding.slug,
            nodeId,
            ...(nextSkill ? { nextSkill } : {})
          }
        )
        userInput.push(...invoke.userInput)
        after = decodeRunDetail(await getRunDetail(session, binding))
        if (invoke.timedOut) {
          outcome = "timeout"
          invokeError = invoke.error
          break
        }
        if (invoke.error) {
          outcome = "error"
          invokeError = invoke.error
          break
        }
        if (isWorkflowStageComplete(after, nodeId, nextSkill)) break
      }
      const stage: WorkflowStageRecord = {
        nodeId,
        skill,
        threadId,
        beforeStatus: `${before.currentNodeId}/${before.currentNodeStatus}`,
        afterStatus: `${after.currentNodeId}/${after.currentNodeStatus}`,
        ...(after.nextAction?.slashSkill ? { nextSkill: after.nextAction.slashSkill } : {}),
        startedAt: stageStartedAt,
        endedAt: new Date().toISOString(),
        outcome,
        ...(invokeError ? { error: invokeError } : {}),
        userInput
      }
      stages.push(stage)
      publishProgress(onProgress, threadIds, stages, plugin)
      if (outcome === "timeout") throw new EvalError("timeout", `阶段 ${nodeId} 超时`, "查看该 thread 的 app/trace 日志。")
      if (outcome === "error") throw new EvalError("agent", `阶段 ${nodeId} 失败：${invokeError}`, "查看该 thread 的 app/trace 日志。")
      assertWorkflowProgress(before, after)
      if (nextNode) {
        assertWorkflowHandoff(after, nodeId, nextSkill!)
      } else if (after.nodeStatuses[nodeId] !== "done") {
        throw new EvalError("agent", `终点 ${nodeId} 未完成：${after.nodeStatuses[nodeId] ?? "unknown"}`, `达到 ${config.workflow.terminalCheckpoint} 后再结束。`)
      }
    }
    return { threadIds, stages, plugin }
  } finally {
    await session.close()
  }
}
