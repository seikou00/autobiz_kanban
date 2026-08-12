import { existsSync, readFileSync } from "node:fs"
import { resolve } from "node:path"

import { EvalError } from "./errors.ts"
import { runProcess } from "./process.ts"
import type { BenchmarkConfig } from "./types.ts"

export interface PreflightCheck {
  id: string
  ok: boolean
  required: boolean
  detail: string
  fix?: string
}

export function isSupportedNodeVersion(version: string): boolean {
  const [major = 0, minor = 0] = version.split(".").map((part) => Number(part))
  return (major === 22 && minor >= 6) || major === 23 || major === 24
}

async function commandCheck(id: string, argv: string[], cwd: string, required: boolean, fix: string): Promise<PreflightCheck> {
  try {
    const result = await runProcess(argv, { cwd, timeoutMs: 30_000 })
    return {
      id,
      ok: result.exitCode === 0,
      required,
      detail: (result.stdout.trim() || result.stderr.trim()).split("\n")[0] || `exit=${String(result.exitCode)}`,
      ...(result.exitCode === 0 ? {} : { fix })
    }
  } catch (error) {
    return { id, ok: false, required, detail: String(error), fix }
  }
}

export async function runPreflight(
  config: BenchmarkConfig,
  env: NodeJS.ProcessEnv = process.env
): Promise<PreflightCheck[]> {
  const checks: PreflightCheck[] = []
  const nodeOk = isSupportedNodeVersion(process.versions.node)
  checks.push({
    id: "node",
    ok: nodeOk,
    required: true,
    detail: process.version,
    ...(nodeOk ? {} : { fix: "使用 Node.js 22.6 至 24.x。" })
  })
  const head = await commandCheck("cmbdevclaw.commit", ["git", "-C", config.app.projectPath, "rev-parse", "HEAD"], config.app.projectPath, true, "切换 CMBDevClaw 到固定 commit。")
  if (head.ok && head.detail !== config.app.commit) {
    checks.push({ id: head.id, ok: false, required: true, detail: `expected=${config.app.commit}, actual=${head.detail}`, fix: "切换 CMBDevClaw 到固定 commit。" })
  } else checks.push(head)
  const packagePath = resolve(config.app.projectPath, "package.json")
  const version = existsSync(packagePath)
    ? String((JSON.parse(readFileSync(packagePath, "utf8")) as { version?: unknown }).version ?? "")
    : "missing"
  checks.push({
    id: "cmbdevclaw.version",
    ok: version === config.app.version,
    required: true,
    detail: version,
    ...(version === config.app.version ? {} : { fix: "使用配置固定的 CMBDevClaw version。" })
  })
  const electronPackagePath = resolve(config.app.projectPath, "node_modules", "electron", "package.json")
  const electronVersion = existsSync(electronPackagePath)
    ? String((JSON.parse(readFileSync(electronPackagePath, "utf8")) as { version?: unknown }).version ?? "")
    : "missing"
  checks.push({
    id: "cmbdevclaw.traceVersion",
    ok: electronVersion === config.app.traceVersion,
    required: true,
    detail: electronVersion,
    ...(electronVersion === config.app.traceVersion
      ? {}
      : { fix: "安装固定 CMBDevClaw Electron 依赖，或更新 traceVersion 与评测基线。" })
  })
  for (const [id, path, fix] of [
    ["cmbdevclaw.main", config.app.mainEntry, "在 CMBDevClaw 仓库运行 npm run build。"],
    ["cmbdevclaw.electron", config.app.electronBin, "在 CMBDevClaw 仓库安装固定依赖。"],
    ["cmbdevclaw.playwright", resolve(config.app.projectPath, "node_modules", "playwright"), "在 CMBDevClaw 仓库安装固定依赖。"],
    ["plugin.package", config.plugin.packageScript, "恢复 package_workspace.sh。"]
  ] as const) {
    const ok = existsSync(path)
    checks.push({ id, ok, required: true, detail: path, ...(ok ? {} : { fix }) })
  }
  checks.push(await commandCheck("git", ["git", "--version"], config.plugin.root, true, "安装 Git。"))
  const java = await commandCheck("java17+", ["java", "-version"], config.plugin.root, true, "安装并启用 Java 17 或更高版本。")
  const javaMajor = Number(/(?:openjdk|java) version "(\d+)/.exec(java.detail)?.[1] ?? 0)
  if (java.ok && javaMajor < 17) {
    checks.push({ id: java.id, ok: false, required: true, detail: java.detail, fix: "将当前 JAVA_HOME 切换到 Java 17 或更高版本。" })
  } else checks.push(java)
  checks.push(await commandCheck("docker", ["docker", "info", "--format", "{{.ServerVersion}}"], config.plugin.root, true, "启动 Docker Desktop 后重试。"))
  for (const [id, envName] of [
    ["model.baseUrl", config.model.baseUrlEnv],
    ["model.model", config.model.modelEnv],
    ["model.apiKey", config.model.apiKeyEnv]
  ] as const) {
    const ok = Boolean(env[envName]?.trim())
    checks.push({ id, ok, required: true, detail: ok ? "configured" : "missing", ...(ok ? {} : { fix: `设置 ${envName}。` }) })
  }
  return checks
}

export function assertPreflight(checks: PreflightCheck[]): void {
  const failed = checks.filter((check) => check.required && !check.ok)
  if (failed.length === 0) return
  throw new EvalError(
    "setup",
    `preflight 未通过：${failed.map((check) => check.id).join(", ")}`,
    failed.map((check) => `${check.id}: ${check.fix ?? check.detail}`).join("；")
  )
}
