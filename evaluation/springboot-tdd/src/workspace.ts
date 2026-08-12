import { existsSync } from "node:fs"
import { mkdir } from "node:fs/promises"
import { resolve } from "node:path"

import { EvalError } from "./errors.ts"
import { runProcess } from "./process.ts"
import type { BenchmarkConfig, RunPlan } from "./types.ts"

export interface RunDirectories {
  root: string
  agentHome: string
  traces: string
  pluginWorkspace: string
  repo: string
  app: string
  verifier: string
}

export function runDirectories(plan: RunPlan): RunDirectories {
  return {
    root: plan.reportDir,
    agentHome: resolve(plan.reportDir, "agent-home"),
    traces: resolve(plan.reportDir, "traces"),
    pluginWorkspace: resolve(plan.reportDir, "plugin-workspace"),
    repo: resolve(plan.reportDir, "repo"),
    app: resolve(plan.reportDir, "app"),
    verifier: resolve(plan.reportDir, "verifier")
  }
}

export async function prepareRunDirectories(plan: RunPlan): Promise<RunDirectories> {
  const dirs = runDirectories(plan)
  if (existsSync(dirs.root)) {
    throw new EvalError("setup", `run 目录已存在：${dirs.root}`, "使用 --resume，或选择新的 report root。")
  }
  for (const path of [dirs.agentHome, dirs.traces, dirs.pluginWorkspace, dirs.app, dirs.verifier]) {
    await mkdir(path, { recursive: true, mode: 0o700 })
  }
  return dirs
}

export async function checkoutTaskRepository(config: BenchmarkConfig, dirs: RunDirectories): Promise<void> {
  const source = process.env.CMBDEVCLAW_EVAL_REPO_SOURCE?.trim() || config.task.repoUrl
  const clone = await runProcess(["git", "clone", "--no-checkout", source, dirs.repo], {
    cwd: dirs.root,
    timeoutMs: 600_000
  })
  if (clone.exitCode !== 0) {
    throw new EvalError("infrastructure", `PetClinic clone 失败：${clone.stderr.trim()}`, "检查网络或 CMBDEVCLAW_EVAL_REPO_SOURCE。")
  }
  const checkout = await runProcess(["git", "checkout", "--detach", config.task.repoCommit], {
    cwd: dirs.repo,
    timeoutMs: 120_000
  })
  if (checkout.exitCode !== 0) {
    throw new EvalError("setup", `无法 checkout 固定 PetClinic commit：${checkout.stderr.trim()}`, "确认 repo source 包含固定 commit。")
  }
  const head = await runProcess(["git", "rev-parse", "HEAD"], { cwd: dirs.repo, timeoutMs: 30_000 })
  if (head.stdout.trim() !== config.task.repoCommit) {
    throw new EvalError("setup", `PetClinic HEAD 不匹配：${head.stdout.trim()}`, "删除该 run 并重新创建 clean checkout。")
  }
}
