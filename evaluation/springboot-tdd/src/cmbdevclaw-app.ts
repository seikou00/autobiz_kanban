import { createWriteStream } from "node:fs"
import { mkdir } from "node:fs/promises"
import { createRequire } from "node:module"
import { delimiter, dirname, resolve } from "node:path"

import { pathToFileURL } from "node:url"

import { EvalError } from "./errors.ts"
import type { BenchmarkConfig } from "./types.ts"
import type { RunDirectories } from "./workspace.ts"
import { answerUserInput } from "./user-input.ts"

export interface CmbDevClawSession {
  app: any
  page: any
  close: () => Promise<void>
}

function isolatedAppEnvironment(dirs: RunDirectories): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  for (const name of ["PATH", "LANG", "LC_ALL", "SHELL", "TMPDIR", "USER", "LOGNAME"]) {
    if (process.env[name]) env[name] = process.env[name]
  }
  env.PATH = [dirname(process.execPath), env.PATH].filter(Boolean).join(delimiter)
  env.HOME = dirs.agentHome
  env.CMB_COWORK_AGENT_HOME = dirs.agentHome
  env.CMB_COWORK_TRACES_DIR = dirs.traces
  env.CMB_COWORK_TRACE_STORAGE_MODE = "plaintext"
  env.XDG_CONFIG_HOME = resolve(dirs.agentHome, "xdg-config")
  env.XDG_CACHE_HOME = resolve(dirs.agentHome, "xdg-cache")
  return env
}

async function configureRuntime(page: any, config: BenchmarkConfig, env: NodeJS.ProcessEnv): Promise<void> {
  const baseUrl = env[config.model.baseUrlEnv]?.trim()
  const model = env[config.model.modelEnv]?.trim()
  const apiKey = env[config.model.apiKeyEnv]?.trim()
  if (!baseUrl || !model || !apiKey) {
    throw new EvalError("setup", "隔离 CMBDevClaw 缺少模型配置", `设置 ${config.model.baseUrlEnv}、${config.model.modelEnv}、${config.model.apiKeyEnv}。`)
  }
  await page.evaluate(async (input: Record<string, unknown>) => {
    const api = (window as any).api
    const result = await api.models.upsertCustomConfig({
      id: input.bareId,
      name: input.displayName,
      baseUrl: input.baseUrl,
      model: input.model,
      apiKey: input.apiKey,
      maxTokens: input.maxTokens,
      maxOutputTokens: input.maxOutputTokens,
      temperature: input.temperature,
      topP: 1,
      topK: 40
    })
    const fullId = `custom:${result.id}`
    await api.models.setDefault(fullId)
    await api.sandbox.setMode("unelevated")
    await api.sandbox.setYoloMode(true)
    if (await api.sandbox.isNuxNeeded()) await api.sandbox.completeNux("unelevated")
  }, {
    bareId: config.model.id.replace(/^custom:/, ""),
    displayName: config.model.displayName,
    baseUrl,
    model,
    apiKey,
    maxTokens: config.model.maxTokens,
    maxOutputTokens: config.model.maxOutputTokens,
    temperature: config.model.temperature
  })
}

export async function launchCmbDevClaw(
  config: BenchmarkConfig,
  dirs: RunDirectories,
  env: NodeJS.ProcessEnv = process.env
): Promise<CmbDevClawSession> {
  await mkdir(dirs.app, { recursive: true })
  const appRequire = createRequire(pathToFileURL(resolve(config.app.projectPath, "package.json")))
  let electron: any
  try {
    electron = appRequire("playwright")._electron
  } catch (error) {
    throw new EvalError("app_launch", "无法从固定 CMBDevClaw 安装解析 Playwright", "在 CMBDevClaw 仓库安装固定依赖。", error)
  }
  let app: any
  try {
    app = await electron.launch({
      executablePath: config.app.electronBin,
      args: [config.app.mainEntry],
      cwd: config.app.projectPath,
      env: isolatedAppEnvironment(dirs),
      timeout: 60_000
    })
    const log = createWriteStream(resolve(dirs.app, "electron.log"), { flags: "a", mode: 0o600 })
    app.process().stdout?.pipe(log)
    app.process().stderr?.pipe(log)
    const page = await app.firstWindow()
    await page.waitForLoadState("domcontentloaded")
    await page.waitForFunction(() => Boolean((window as any).api), { timeout: 30_000 })
    await page.exposeFunction("__cmbEvalAnswerUserInput", answerUserInput)
    await configureRuntime(page, config, env)
    return {
      app,
      page,
      close: async () => {
        await page.waitForTimeout(500)
        await app.close()
        log.end()
      }
    }
  } catch (error) {
    if (app) await app.close().catch(() => undefined)
    if (error instanceof EvalError) throw error
    throw new EvalError("app_launch", `CMBDevClaw 启动失败：${String(error)}`, "确认固定 build 可由 Electron 启动。", error)
  }
}
