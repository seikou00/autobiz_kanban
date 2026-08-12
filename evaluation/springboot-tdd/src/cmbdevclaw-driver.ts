import { readFile } from "node:fs/promises"

import { EvalError } from "./errors.ts"
import type { BenchmarkConfig, PluginSnapshotManifest, UserInputDecision } from "./types.ts"
import type { CmbDevClawSession } from "./cmbdevclaw-app.ts"

export interface InstalledPlugin {
  id: string
  name: string
  version: string
}

export interface HarnessBinding {
  projectId: string
  slug: string
  workspacePath: string
  templateId: string
  skippedNodes: string[]
}

export interface SelectedPluginSkill {
  name: string
  path: string
  description?: string
  metadata?: Record<string, string>
  allowedTools?: string[]
}

export interface InvokeResult {
  events: Array<{ type: string; [key: string]: unknown }>
  userInput: UserInputDecision[]
  timedOut: boolean
  error?: string
}

export interface WorkflowCompletionTarget {
  projectId: string
  slug: string
  nodeId: string
  nextSkill?: string
}

export async function installPlugin(
  session: CmbDevClawSession,
  config: BenchmarkConfig,
  snapshot: PluginSnapshotManifest
): Promise<InstalledPlugin> {
  const bytes = await readFile(snapshot.zipPath)
  const result = await session.page.evaluate(async (input: { bytes: number[]; fileName: string; version: string; expectedName: string }) => {
    const api = (window as any).api
    const install = await api.plugins.install(new Uint8Array(input.bytes).buffer, input.fileName, "local", input.version)
    if (!install.success) return { success: false, error: install.error }
    const plugins = await api.plugins.list()
    const plugin = plugins.find((item: any) => item.name === input.expectedName)
    if (!plugin) return { success: false, error: "installed plugin missing from plugins.list" }
    await api.plugins.setEnabled(plugin.id, true)
    const registry = await api.harnessBoard.registry()
    const adapter = registry.find((item: any) => item.id === plugin.id || item.name === plugin.name)
    return { success: true, plugin, adapter }
  }, {
    bytes: Array.from(bytes),
    fileName: "autobizdevops-eval.zip",
    version: snapshot.pluginVersion,
    expectedName: config.plugin.expectedName
  })
  if (!result.success || !result.plugin) {
    throw new EvalError("plugin_load", `CMBDevClaw 插件安装失败：${String(result.error ?? "unknown")}`, "检查 plugin.zip 和插件安装日志。")
  }
  if (!result.adapter?.boardCompatibility?.compatible) {
    throw new EvalError("plugin_load", `Harness adapter 不兼容：${JSON.stringify(result.adapter?.boardCompatibility ?? null)}`, "修复插件 board_config 与 CMBDevClaw API 版本。")
  }
  if (result.plugin.version !== config.plugin.expectedVersion) {
    throw new EvalError("plugin_load", `安装后的插件版本错误：${String(result.plugin.version)}`, "重新生成固定 plugin snapshot。")
  }
  return { id: result.plugin.id, name: result.plugin.name, version: result.plugin.version }
}

export async function assertPluginAbsent(session: CmbDevClawSession, expectedName: string): Promise<void> {
  const found = await session.page.evaluate(async (name: string) => {
    const api = (window as any).api
    const [plugins, skills] = await Promise.all([api.plugins.list(), api.skills.listPlugins()])
    return {
      plugin: plugins.some((item: any) => item.name === name),
      skill: skills.some((item: any) => item.pluginName === name)
    }
  }, expectedName)
  if (found.plugin || found.skill) {
    throw new EvalError("setup", "control 的隔离 app home 已出现目标插件", "删除该 run，并确认 CMB_COWORK_AGENT_HOME 隔离生效。")
  }
}

export async function createHarnessBinding(
  session: CmbDevClawSession,
  config: BenchmarkConfig,
  plugin: InstalledPlugin,
  pluginWorkspace: string,
  repoPath: string,
  runId: string
): Promise<HarnessBinding> {
  const result = await session.page.evaluate(async (input: Record<string, unknown>) => {
    const api = (window as any).api
    const project = await api.harnessBoard.createProject({
      adapterId: input.pluginId,
      adapterType: "plugin",
      name: input.runId,
      projectCode: input.projectCode,
      projectFromLean: false,
      projectDir: input.projectDir,
      description: "springboot-tdd benchmark",
      systemId: "spring-petclinic",
      systemName: "Spring PetClinic",
      workspacePath: input.pluginWorkspace,
      sessionWorkspacePath: input.repoPath
    })
    const workflowConfig = await api.harnessBoard.getDynamicWorkflowConfig(project.projectId)
    const custom = workflowConfig?.templates?.find((item: any) => item.templateType === "custom")
    const standard = workflowConfig?.templates?.find((item: any) => item.id === "standard")
    const template = custom ?? standard
    if (!template) return { error: "no compatible custom/standard workflow template" }
    const templateNodes = Array.isArray(template.nodes) ? template.nodes : []
    const desiredNodes = Array.isArray(input.nodes) ? input.nodes : []
    const missingNodes = desiredNodes.filter((node: unknown) => !templateNodes.includes(node))
    if (missingNodes.length > 0) return { error: `workflow template missing nodes: ${missingNodes.join(", ")}` }
    const orderedDesired = templateNodes.filter((node: unknown) => desiredNodes.includes(node))
    if (JSON.stringify(orderedDesired) !== JSON.stringify(desiredNodes)) {
      return { error: "workflow template node order differs from benchmark config" }
    }
    const feature = await api.harnessBoard.createFeature({
      projectId: project.projectId,
      feature: input.feature,
      sessionContextInjectionSource: "plugin",
      workflowTemplate: template.id,
      ...(custom ? { workflowNodes: input.nodes } : {}),
      workflowConfig
    })
    const skippedNodes = custom ? [] : templateNodes.filter((node: unknown) => !desiredNodes.includes(node))
    for (const nodeId of skippedNodes) {
      await api.harnessBoard.skipNode({ projectId: project.projectId, slug: feature.slug, nodeId })
    }
    const detail = await api.harnessBoard.getRunDetail(project.projectId, feature.slug)
    const statuses = Object.fromEntries(detail.run.nodes.map((node: any) => [node.id, node.nodeStatus]))
    if (detail.run.currentNodeId !== desiredNodes[0]) {
      return { error: `workflow landed on ${detail.run.currentNodeId}, expected ${desiredNodes[0]}` }
    }
    if (skippedNodes.some((nodeId: string) => statuses[nodeId] !== "skipped")) {
      return { error: "standard workflow exclusion did not produce skipped node status" }
    }
    return { project, feature, templateId: template.id, skippedNodes }
  }, {
    pluginId: plugin.id,
    runId,
    projectCode: runId.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 80),
    projectDir: config.workflow.projectDir,
    pluginWorkspace,
    repoPath,
    feature: config.workflow.feature,
    nodes: config.workflow.nodes
  })
  if (result.error || !result.project || !result.feature) {
    throw new EvalError("plugin_load", `Harness Project/Feature 创建失败：${String(result.error ?? "unknown")}`, "检查 dynamic workflow 与插件初始化脚本。")
  }
  return {
    projectId: result.project.projectId,
    slug: result.feature.slug,
    workspacePath: result.feature.workspacePath,
    templateId: result.templateId,
    skippedNodes: result.skippedNodes
  }
}

export async function createThread(
  session: CmbDevClawSession,
  config: BenchmarkConfig,
  workspacePath: string,
  title: string,
  binding?: HarnessBinding
): Promise<string> {
  const threadId = await session.page.evaluate(async (input: Record<string, unknown>) => {
    const api = (window as any).api
    const metadata: Record<string, unknown> = {
      workspacePath: input.workspacePath,
      model: input.modelId,
      title: input.title
    }
    if (input.projectId && input.slug) {
      metadata.harnessFeature = { projectId: input.projectId, slug: input.slug, source: "autobizdevops" }
    }
    const thread = await api.threads.create(metadata)
    await api.workspace.set(thread.thread_id, input.workspacePath)
    return thread.thread_id
  }, {
    workspacePath,
    modelId: config.model.id,
    title,
    projectId: binding?.projectId,
    slug: binding?.slug
  })
  if (!threadId) throw new EvalError("agent", "CMBDevClaw 未返回 thread ID", "检查 threads.create IPC。")
  return threadId
}

function escapeXml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

function optionalXmlLine(tag: string, value: string | undefined): string {
  const trimmed = value?.trim()
  return trimmed ? `<${tag}>${escapeXml(trimmed)}</${tag}>\n` : ""
}

export function formatSkillInvocation(skill: SelectedPluginSkill): string {
  const whenToUse = skill.metadata?.whenToUse ?? skill.metadata?.["when-to-use"] ?? skill.metadata?.when_to_use
  const allowedTools = skill.allowedTools?.length ? skill.allowedTools.join(", ") : undefined
  return (
    "<CMBDEVCLAW-SKILL-USE-V1>\n" +
    "<instruction>\n" +
    "用户显式选择了下面 <name> 指定的技能。请先使用 read_file 工具读取 <path> 指定的 SKILL.md 文件。读取后必须严格按照该技能说明执行本轮任务：\n" +
    "- 不要跳过任何步骤，也不要把步骤改写成泛化或概括的回答；\n" +
    "- 不要重复询问技能文档中已经明确给出的内容；\n" +
    "- 不要凭猜测代替技能中明确的指令；\n" +
    "- 技能文档中提到的相对脚本、资源、模板路径，都必须按 <path> 指定的 SKILL.md 所在目录解析；执行脚本时请使用绝对路径，或把 cwd 设置为该技能目录；\n" +
    "- 始终使用中文回答。\n" +
    "</instruction>\n" +
    `<name>${escapeXml(skill.name.trim())}</name>\n` +
    optionalXmlLine("description", skill.description) +
    optionalXmlLine("when_to_use", whenToUse) +
    optionalXmlLine("allowed_tools", allowedTools) +
    `<path>${escapeXml(skill.path.trim())}</path>\n` +
    "</CMBDEVCLAW-SKILL-USE-V1>"
  )
}

export function appendSkillInvocation(message: string, skill: SelectedPluginSkill): string {
  return `${message.trim()}\n\n${formatSkillInvocation(skill)}`
}

export async function selectPluginSkill(
  session: CmbDevClawSession,
  plugin: InstalledPlugin,
  skillName: string
): Promise<SelectedPluginSkill> {
  const raw = await session.page.evaluate(async (input: { pluginId: string; pluginName: string; skillName: string }) => {
    const skills = await (window as any).api.skills.listPlugins()
    return skills.find((item: any) => item.name === input.skillName
      && (item.pluginId === input.pluginId || item.pluginName === input.pluginName)) ?? null
  }, { pluginId: plugin.id, pluginName: plugin.name, skillName })
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new EvalError("plugin_load", `插件 Skill 不存在：${skillName}`, "检查安装包 skills 清单与 workflow.skills 映射。")
  }
  const record = raw as Record<string, unknown>
  const name = typeof record.name === "string" ? record.name.trim() : ""
  const path = typeof record.path === "string" ? record.path.trim() : ""
  if (name !== skillName || !path) {
    throw new EvalError("plugin_load", `插件 Skill 元数据非法：${skillName}`, "检查 CMBDevClaw skills.listPlugins 返回结构。")
  }
  const metadata = record.metadata && typeof record.metadata === "object" && !Array.isArray(record.metadata)
    && Object.values(record.metadata).every((value) => typeof value === "string")
    ? record.metadata as Record<string, string>
    : undefined
  const allowedTools = Array.isArray(record.allowedTools) && record.allowedTools.every((value) => typeof value === "string")
    ? record.allowedTools as string[]
    : undefined
  return {
    name,
    path,
    ...(typeof record.description === "string" && record.description.trim()
      ? { description: record.description.trim() }
      : {}),
    ...(metadata ? { metadata } : {}),
    ...(allowedTools ? { allowedTools } : {})
  }
}

export async function invokeThread(
  session: CmbDevClawSession,
  config: BenchmarkConfig,
  threadId: string,
  message: string,
  workspacePath: string,
  timeoutMs: number,
  completionTarget?: WorkflowCompletionTarget
): Promise<InvokeResult> {
  return await session.page.evaluate(async (input: {
    threadId: string
    message: string
    modelId: string
    workspacePath: string
    timeoutMs: number
    completionTarget?: WorkflowCompletionTarget
  }) => {
    const api = (window as any).api
    return await new Promise<InvokeResult>((resolve) => {
      const events: Array<{ type: string; [key: string]: unknown }> = []
      const decisions: UserInputDecision[] = []
      const seenRequests = new Set<string>()
      let settled = false
      let streamCleanup = (): void => undefined
      let approvalCleanup = (): void => undefined
      let completionTimer: number | undefined
      let completionStopping = false
      let completionChecking = false
      const userInputCleanup = api.userInput.onRequest(input.threadId, async (request: any) => {
        if (seenRequests.has(request.requestId)) return
        seenRequests.add(request.requestId)
        try {
          const decision = await (window as any).__cmbEvalAnswerUserInput(request)
          decisions.push(decision)
          api.userInput.sendResponse({ requestId: decision.requestId, answers: decision.answers, submittedAt: decision.submittedAt })
        } catch (error) {
          finish(false, String(error))
        }
      })
      const timer = window.setTimeout(async () => {
        await api.agent.cancel(input.threadId, { cancelWorkers: true }).catch(() => undefined)
        finish(true, `stage timeout after ${input.timeoutMs}ms`)
      }, input.timeoutMs)
      const finish = (timedOut: boolean, error?: string): void => {
        if (settled) return
        settled = true
        window.clearTimeout(timer)
        if (completionTimer !== undefined) window.clearInterval(completionTimer)
        streamCleanup()
        approvalCleanup()
        userInputCleanup()
        resolve({ events, userInput: decisions, timedOut, ...(error ? { error } : {}) })
      }
      approvalCleanup = api.sandbox.onApprovalRequest(input.threadId, async (request: any) => {
        if (request.operation !== "git_commit") return
        const requestId = request._orchestratorRequestId ?? request.id
        const toolCallId = request.tool_call?.id ?? request.id
        const worktreePath = request.suggestedGitWorktreePath ?? request.cwd
        if (!requestId || !toolCallId || worktreePath !== input.workspacePath) {
          if (requestId && toolCallId) {
            api.sandbox.sendApprovalDecision({ requestId, type: "reject", tool_call_id: toolCallId })
          }
          finish(false, "拒绝了评测 workspace 之外的 git commit 审批")
          return
        }
        const commitMessage = typeof request.suggestedCommitMessage === "string" && request.suggestedCommitMessage.trim()
          ? request.suggestedCommitMessage.trim()
          : "evaluation checkpoint"
        const filePaths = Array.isArray(request.suggestedCommitFilePaths)
          ? request.suggestedCommitFilePaths.filter((value: unknown) => typeof value === "string" && value.length > 0)
          : undefined
        try {
          const result = await api.workspace.commitWorktree(
            input.threadId,
            commitMessage,
            filePaths,
            { worktreePath }
          )
          api.sandbox.sendApprovalDecision({
            requestId,
            type: "approve",
            tool_call_id: toolCallId,
            commitResult: {
              success: result.success === true,
              ...(result.success === true ? { commitMessage } : {}),
              ...(result.error ? { error: String(result.error) } : {})
            }
          })
        } catch (error) {
          api.sandbox.sendApprovalDecision({
            requestId,
            type: "approve",
            tool_call_id: toolCallId,
            commitResult: { success: false, error: String(error) }
          })
        }
      })
      streamCleanup = api.agent.invoke(input.threadId, input.message, (event: any) => {
        events.push(event)
        if (event.type === "done") finish(false)
        if (event.type === "error") finish(false, String(event.message ?? event.error ?? "agent error"))
      }, input.modelId)
      if (input.completionTarget) {
        const target = input.completionTarget
        const stopAtCompletedHandoff = async (): Promise<void> => {
          if (settled || completionStopping || completionChecking) return
          completionChecking = true
          try {
            const detail = await api.harnessBoard.getRunDetail(target.projectId, target.slug)
            const runNode = detail?.run?.nodes?.find((node: any) => node?.id === target.nodeId)
            if (detail?.run?.currentNodeId !== target.nodeId || runNode?.nodeStatus !== "done") return
            if (target.nextSkill) {
              const workflowNode = detail?.workflow?.nodes?.find((node: any) => node?.id === target.nodeId)
              const states = [
                ...(Array.isArray(workflowNode?.states) ? workflowNode.states : []),
                ...(Array.isArray(detail?.workflow?.states) ? detail.workflow.states : [])
              ]
              const state = states.find((item: any) => item?.nodeStatus === "done")
              if (state?.nextAction?.slashSkill !== target.nextSkill) return
            }
            completionStopping = true
            await api.agent.cancel(input.threadId, { cancelWorkers: true }).catch(() => undefined)
            finish(false)
          } catch {
            // The normal post-turn Harness assertion remains authoritative.
          } finally {
            completionChecking = false
          }
        }
        completionTimer = window.setInterval(() => void stopAtCompletedHandoff(), 250)
        void stopAtCompletedHandoff()
      }
    })
  }, { threadId, message, modelId: config.model.id, workspacePath, timeoutMs, completionTarget })
}

export async function getRunDetail(session: CmbDevClawSession, binding: HarnessBinding): Promise<unknown> {
  return await session.page.evaluate(async (input: { projectId: string; slug: string }) => {
    return await (window as any).api.harnessBoard.getRunDetail(input.projectId, input.slug)
  }, binding)
}
