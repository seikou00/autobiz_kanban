import { existsSync, readFileSync } from "node:fs"
import { dirname, isAbsolute, resolve } from "node:path"

import { asBoolean, asInteger, asRecord, asString, asStringArray } from "./codec.ts"
import { EvalError } from "./errors.ts"
import type {
  AppConfig,
  BenchmarkConfig,
  ConditionConfig,
  ConditionId,
  ModelConfig,
  PluginConfig,
  TaskConfig,
  VerifierConfig,
  WorkflowConfig
} from "./types.ts"

const COMMIT_PATTERN = /^[0-9a-f]{40}$/
const IMAGE_DIGEST_PATTERN = /^[^\s@:]+(?:\/[^\s@:]+)*@sha256:[0-9a-f]{64}$/
const CONDITION_IDS = new Set<ConditionId>(["control", "full-chain"])
const EXPECTED_WORKFLOW_NODES = [
  "dev.specs",
  "dev.plan",
  "dev.code",
  "dev.review",
  "dev.utest",
  "dev.verify"
]

function parseConfigText(text: string, configPath: string): unknown {
  try {
    return JSON.parse(text)
  } catch (error) {
    throw new EvalError(
      "setup",
      `配置文件不是有效的 JSON-compatible YAML：${configPath}`,
      "保持 .yaml 扩展名，并使用 JSON 对象语法；检查逗号、引号和括号。",
      error
    )
  }
}

export function expandEnvironment(value: string, env: NodeJS.ProcessEnv): string {
  return value.replace(/\$\{([A-Z][A-Z0-9_]*)(?::-([^}]*))?\}/g, (_match, name: string, fallback?: string) => {
    const resolved = env[name]?.trim()
    if (resolved) return resolved
    if (fallback !== undefined) return fallback
    throw new EvalError("setup", `环境变量 ${name} 未配置`, `设置 ${name} 后重试。`)
  })
}

function resolveConfigPath(baseDir: string, raw: unknown, field: string, env: NodeJS.ProcessEnv): string {
  const expanded = expandEnvironment(asString(raw, field), env)
  return resolve(isAbsolute(expanded) ? expanded : resolve(baseDir, expanded))
}

function requirePath(path: string, field: string): void {
  if (!existsSync(path)) {
    throw new EvalError("setup", `${field} 不存在：${path}`, `创建该文件/目录或修正 ${field}。`)
  }
}

function parseTask(value: unknown, baseDir: string, env: NodeJS.ProcessEnv): TaskConfig {
  const record = asRecord(value, "task")
  const repoCommit = asString(record.repoCommit, "task.repoCommit")
  if (!COMMIT_PATTERN.test(repoCommit)) {
    throw new EvalError("setup", "task.repoCommit 必须是 40 位小写 Git commit", "固定 PetClinic commit。")
  }
  const task = {
    id: asString(record.id, "task.id"),
    promptPath: resolveConfigPath(baseDir, record.promptPath, "task.promptPath", env),
    sourcePath: resolveConfigPath(baseDir, record.sourcePath, "task.sourcePath", env),
    provenancePath: resolveConfigPath(baseDir, record.provenancePath, "task.provenancePath", env),
    repoUrl: asString(record.repoUrl, "task.repoUrl"),
    repoCommit
  }
  requirePath(task.promptPath, "task.promptPath")
  requirePath(task.sourcePath, "task.sourcePath")
  requirePath(task.provenancePath, "task.provenancePath")
  return task
}

function parseApp(value: unknown, baseDir: string, env: NodeJS.ProcessEnv): AppConfig {
  const record = asRecord(value, "app")
  const commit = asString(record.commit, "app.commit")
  if (!COMMIT_PATTERN.test(commit)) {
    throw new EvalError("setup", "app.commit 必须是 40 位小写 Git commit", "固定 CMBDevClaw commit。")
  }
  const projectPath = resolveConfigPath(baseDir, record.projectPath, "app.projectPath", env)
  const app = {
    projectPath,
    commit,
    version: asString(record.version, "app.version"),
    traceVersion: asString(record.traceVersion, "app.traceVersion"),
    mainEntry: resolve(projectPath, asString(record.mainEntry, "app.mainEntry")),
    electronBin: resolve(projectPath, asString(record.electronBin, "app.electronBin"))
  }
  requirePath(app.projectPath, "app.projectPath")
  requirePath(app.mainEntry, "app.mainEntry")
  requirePath(app.electronBin, "app.electronBin")
  const packagePath = resolve(projectPath, "package.json")
  requirePath(packagePath, "CMBDevClaw package.json")
  const packageRecord = asRecord(JSON.parse(readFileSync(packagePath, "utf8")), "CMBDevClaw package.json")
  if (packageRecord.version !== app.version) {
    throw new EvalError(
      "setup",
      `CMBDevClaw version 不匹配：配置 ${app.version}，实际 ${String(packageRecord.version)}`,
      "切换到固定应用版本或更新配置与评测基线。"
    )
  }
  const electronPackagePath = resolve(projectPath, "node_modules", "electron", "package.json")
  requirePath(electronPackagePath, "CMBDevClaw Electron package.json")
  const electronPackage = asRecord(JSON.parse(readFileSync(electronPackagePath, "utf8")), "CMBDevClaw Electron package.json")
  if (electronPackage.version !== app.traceVersion) {
    throw new EvalError(
      "setup",
      `CMBDevClaw trace runtime version 不匹配：配置 ${app.traceVersion}，实际 ${String(electronPackage.version)}`,
      "安装固定 CMBDevClaw 依赖，或更新 traceVersion 与评测基线。"
    )
  }
  return app
}

function parsePlugin(value: unknown, baseDir: string, env: NodeJS.ProcessEnv): PluginConfig {
  const record = asRecord(value, "plugin")
  const root = resolveConfigPath(baseDir, record.root, "plugin.root", env)
  const plugin = {
    root,
    packageScript: resolve(root, asString(record.packageScript, "plugin.packageScript")),
    expectedName: asString(record.expectedName, "plugin.expectedName"),
    expectedVersion: asString(record.expectedVersion, "plugin.expectedVersion")
  }
  requirePath(plugin.root, "plugin.root")
  requirePath(plugin.packageScript, "plugin.packageScript")
  const manifestPath = resolve(root, "plugin.json")
  requirePath(manifestPath, "plugin.json")
  const manifest = asRecord(JSON.parse(readFileSync(manifestPath, "utf8")), "plugin.json")
  if (manifest.name !== plugin.expectedName || manifest.version !== plugin.expectedVersion) {
    throw new EvalError(
      "setup",
      "plugin expectedName/expectedVersion 与 plugin.json 不一致",
      "更新 benchmark_config.yaml 或固定正确的插件快照。"
    )
  }
  return plugin
}

function parseModel(value: unknown): ModelConfig {
  const record = asRecord(value, "model")
  const temperature = record.temperature
  if (typeof temperature !== "number" || temperature <= 0 || temperature > 2) {
    throw new EvalError("setup", "model.temperature 必须在 0（不含）到 2 之间", "使用 CMBDevClaw 1.4.9 支持的 temperature。")
  }
  return {
    id: asString(record.id, "model.id"),
    displayName: asString(record.displayName, "model.displayName"),
    baseUrlEnv: asString(record.baseUrlEnv, "model.baseUrlEnv"),
    modelEnv: asString(record.modelEnv, "model.modelEnv"),
    apiKeyEnv: asString(record.apiKeyEnv, "model.apiKeyEnv"),
    maxTokens: asInteger(record.maxTokens, "model.maxTokens"),
    maxOutputTokens: asInteger(record.maxOutputTokens, "model.maxOutputTokens"),
    temperature
  }
}

function parseWorkflow(value: unknown): WorkflowConfig {
  const record = asRecord(value, "workflow")
  const nodes = asStringArray(record.nodes, "workflow.nodes")
  const skillRecord = asRecord(record.skills, "workflow.skills")
  const skills: Record<string, string> = {}
  for (const node of nodes) skills[node] = asString(skillRecord[node], `workflow.skills.${node}`)
  const unknownSkills = Object.keys(skillRecord).filter((node) => !nodes.includes(node))
  if (unknownSkills.length > 0) {
    throw new EvalError("setup", `workflow.skills 包含未知节点：${unknownSkills.join(", ")}`, "删除多余映射。")
  }
  if (JSON.stringify(nodes) !== JSON.stringify(EXPECTED_WORKFLOW_NODES)) {
    throw new EvalError(
      "setup",
      `workflow.nodes 必须是固定六节点链：${EXPECTED_WORKFLOW_NODES.join(" -> ")}`,
      "恢复 benchmark 固定 Harness 活动链。"
    )
  }
  const terminalCheckpoint = asString(record.terminalCheckpoint, "workflow.terminalCheckpoint")
  if (terminalCheckpoint !== "verify_done") {
    throw new EvalError("setup", "workflow.terminalCheckpoint 必须是 verify_done", "恢复固定终点。")
  }
  return {
    feature: asString(record.feature, "workflow.feature"),
    projectDir: asString(record.projectDir, "workflow.projectDir"),
    terminalCheckpoint,
    nodes,
    skills
  }
}

function parseVerifier(value: unknown, baseDir: string, env: NodeJS.ProcessEnv): VerifierConfig {
  const record = asRecord(value, "verifier")
  const image = asString(record.image, "verifier.image")
  if (!IMAGE_DIGEST_PATTERN.test(image)) {
    throw new EvalError("setup", "verifier.image 必须使用 @sha256 digest", "固定不可变 JVM image digest。")
  }
  const verifier = {
    image,
    platform: asString(record.platform, "verifier.platform"),
    mavenExecutable: asString(record.mavenExecutable, "verifier.mavenExecutable"),
    hiddenTestPath: resolveConfigPath(baseDir, record.hiddenTestPath, "verifier.hiddenTestPath", env),
    goldPatchPath: resolveConfigPath(baseDir, record.goldPatchPath, "verifier.goldPatchPath", env),
    testClass: asString(record.testClass, "verifier.testClass"),
    imagePullTimeoutMs: asInteger(record.imagePullTimeoutMs, "verifier.imagePullTimeoutMs", 1_000),
    timeoutMs: asInteger(record.timeoutMs, "verifier.timeoutMs", 1_000)
  }
  if (!verifier.mavenExecutable.startsWith("/")) {
    throw new EvalError("setup", "verifier.mavenExecutable 必须是容器内绝对路径", "使用固定镜像中的 Maven 绝对路径。")
  }
  requirePath(verifier.hiddenTestPath, "verifier.hiddenTestPath")
  requirePath(verifier.goldPatchPath, "verifier.goldPatchPath")
  return verifier
}

function parseConditions(value: unknown): ConditionConfig[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new EvalError("setup", "conditions 必须是非空数组", "配置 control 和 full-chain。")
  }
  const seen = new Set<string>()
  const conditions = value.map((item, index) => {
    const record = asRecord(item, `conditions[${index}]`)
    const id = asString(record.id, `conditions[${index}].id`)
    if (!CONDITION_IDS.has(id as ConditionId)) {
      throw new EvalError("setup", `未知 condition：${id}`, "只使用 control 或 full-chain。")
    }
    if (seen.has(id)) throw new EvalError("setup", `condition ID 重复：${id}`, "删除重复 condition。")
    seen.add(id)
    return { id: id as ConditionId, pluginEnabled: asBoolean(record.pluginEnabled, `${id}.pluginEnabled`) }
  })
  if (!seen.has("control") || !seen.has("full-chain")) {
    throw new EvalError("setup", "conditions 必须同时包含 control 与 full-chain", "补齐两组条件。")
  }
  if (conditions.find((item) => item.id === "control")?.pluginEnabled !== false) {
    throw new EvalError("setup", "control.pluginEnabled 必须为 false", "关闭 control 的插件加载。")
  }
  if (conditions.find((item) => item.id === "full-chain")?.pluginEnabled !== true) {
    throw new EvalError("setup", "full-chain.pluginEnabled 必须为 true", "启用 full-chain 的插件加载。")
  }
  return conditions
}

export function loadConfig(configPath: string, env: NodeJS.ProcessEnv = process.env): BenchmarkConfig {
  const absolutePath = resolve(configPath)
  requirePath(absolutePath, "config")
  const baseDir = dirname(absolutePath)
  const root = asRecord(parseConfigText(readFileSync(absolutePath, "utf8"), absolutePath), "config")
  const schemaVersion = asInteger(root.schemaVersion, "schemaVersion")
  if (schemaVersion !== 1) {
    throw new EvalError("setup", `不支持 schemaVersion=${schemaVersion}`, "将 schemaVersion 设为 1。")
  }
  const timeouts = asRecord(root.timeouts, "timeouts")
  return {
    schemaVersion,
    benchmarkId: asString(root.benchmarkId, "benchmarkId"),
    repeats: asInteger(root.repeats, "repeats"),
    reportRoot: resolveConfigPath(baseDir, root.reportRoot, "reportRoot", env),
    task: parseTask(root.task, baseDir, env),
    app: parseApp(root.app, baseDir, env),
    plugin: parsePlugin(root.plugin, baseDir, env),
    model: parseModel(root.model),
    workflow: parseWorkflow(root.workflow),
    verifier: parseVerifier(root.verifier, baseDir, env),
    conditions: parseConditions(root.conditions),
    timeouts: {
      stageMs: asInteger(timeouts.stageMs, "timeouts.stageMs", 1_000),
      totalMs: asInteger(timeouts.totalMs, "timeouts.totalMs", 1_000)
    }
  }
}
