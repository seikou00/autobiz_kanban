import { existsSync, readFileSync } from "node:fs"
import { mkdir, readFile, rename, writeFile } from "node:fs/promises"
import { resolve } from "node:path"

import { runAgent, type AgentRunOutput } from "./agent-runner.ts"
import { sha256 } from "./codec.ts"
import { EvalError, asErrorMessage } from "./errors.ts"
import { evaluateRun, failedRun } from "./evaluator.ts"
import { batchFingerprint, resolveModelRuntimeIdentity } from "./fingerprint.ts"
import { writeJson } from "./io.ts"
import { runProcess } from "./process.ts"
import { assertSnapshotUnchanged, createPluginSnapshot } from "./snapshot.ts"
import {
  readTraces,
  summarizeTraces,
  validateControlTraces,
  validateFullChainTraces
} from "./trace-reader.ts"
import type {
  BenchmarkConfig,
  PluginSnapshotManifest,
  RunPlan,
  RunResult,
  TraceSummary,
  WorkflowStageRecord
} from "./types.ts"
import { runVerifier } from "./verifier.ts"
import {
  checkoutTaskRepository,
  prepareRunDirectories,
  runDirectories,
  type RunDirectories
} from "./workspace.ts"

export interface BatchOptions {
  resume: boolean
}

async function loadOrCreateSnapshot(
  config: BenchmarkConfig,
  resume: boolean
): Promise<PluginSnapshotManifest> {
  const batchDir = resolve(config.reportRoot, "_batch")
  const manifestPath = resolve(batchDir, "plugin-manifest.json")
  if (resume && existsSync(manifestPath)) {
    const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as PluginSnapshotManifest
    assertSnapshotUnchanged(manifest)
    return manifest
  }
  return await createPluginSnapshot(config, batchDir)
}

async function capturePatch(dirs: RunDirectories): Promise<void> {
  if (!existsSync(resolve(dirs.repo, ".git"))) return
  const diff = await runProcess(["git", "diff", "--binary", "HEAD"], {
    cwd: dirs.repo,
    timeoutMs: 60_000,
    maxOutputChars: 10_000_000
  })
  await writeFile(resolve(dirs.root, "patch.diff"), diff.stdout, "utf8")
}

function existingTraceSummary(dirs: RunDirectories, threadIds: string[]): TraceSummary | undefined {
  try {
    const traces = readTraces(dirs.traces).filter((trace) => threadIds.includes(trace.threadId))
    return traces.length > 0 ? summarizeTraces(traces) : undefined
  } catch {
    return undefined
  }
}

async function archiveIncompleteRun(plan: RunPlan): Promise<void> {
  if (!existsSync(plan.reportDir)) return
  await rename(plan.reportDir, `${plan.reportDir}.incomplete-${Date.now()}`)
}

export async function executeBatch(
  config: BenchmarkConfig,
  plans: RunPlan[],
  options: BatchOptions
): Promise<RunResult[]> {
  await mkdir(config.reportRoot, { recursive: true })
  const snapshot = await loadOrCreateSnapshot(config, options.resume)
  const fingerprint = batchFingerprint(config, snapshot)
  const modelRuntime = resolveModelRuntimeIdentity(config)
  const results: RunResult[] = []
  for (const plan of plans) {
    const resultPath = resolve(plan.reportDir, "result.json")
    if (options.resume && existsSync(resultPath)) {
      const existing = JSON.parse(readFileSync(resultPath, "utf8")) as RunResult
      if (existing.fingerprint !== fingerprint) {
        throw new EvalError("setup", `resume fingerprint 不匹配：${plan.id}`, "使用原 batch snapshot，或创建新的 report root。")
      }
      results.push(existing)
      continue
    }
    if (existsSync(plan.reportDir)) {
      if (!options.resume) throw new EvalError("setup", `run 已存在：${plan.id}`, "使用 --resume 或新的 report root。")
      await archiveIncompleteRun(plan)
    }
    const dirs = await prepareRunDirectories(plan)
    let agentOutput: AgentRunOutput = { threadIds: [], stages: [] }
    let traces: TraceSummary | undefined
    try {
      assertSnapshotUnchanged(snapshot)
      await checkoutTaskRepository(config, dirs)
      await writeJson(resolve(dirs.root, "manifest.json"), {
        schemaVersion: 1,
        benchmarkId: config.benchmarkId,
        run: plan,
        fingerprint,
        app: { commit: config.app.commit, version: config.app.version },
        plugin: {
          fingerprint: snapshot.fingerprint,
          name: snapshot.pluginName,
          version: snapshot.pluginVersion
        },
        task: { id: config.task.id, repoCommit: config.task.repoCommit },
        model: {
          id: config.model.id,
          baseUrlSha256: sha256(modelRuntime.baseUrl),
          model: modelRuntime.model,
          maxTokens: config.model.maxTokens,
          maxOutputTokens: config.model.maxOutputTokens,
          temperature: config.model.temperature
        }
      })
      agentOutput = await runAgent(config, plan, dirs, snapshot, (progress) => {
        agentOutput = progress
      })
      await writeJson(resolve(dirs.root, "agent-output.json"), agentOutput)
      await capturePatch(dirs)
      const nativeTraces = readTraces(dirs.traces).filter((trace) => agentOutput.threadIds.includes(trace.threadId))
      if (nativeTraces.length === 0) throw new EvalError("agent", "没有找到本 run 的 CMBDevClaw trace", "确认 trace root 隔离和 flush 完成。")
      if (plan.condition === "full-chain") {
        if (!agentOutput.plugin) throw new EvalError("plugin_load", "full-chain 缺少 plugin metadata", "检查安装结果。")
        validateFullChainTraces(
          nativeTraces,
          agentOutput.stages,
          agentOutput.plugin.id,
          agentOutput.plugin.version,
          config.app.version,
          config.model.id
        )
      } else validateControlTraces(nativeTraces, config.plugin.expectedName, config.app.version, config.model.id)
      traces = summarizeTraces(nativeTraces)
      await writeJson(resolve(dirs.root, "trace-summary.json"), traces)
      const verifier = await runVerifier(config, dirs.repo)
      await writeJson(resolve(dirs.verifier, "result.json"), verifier)
      const result = evaluateRun(
        config,
        plan,
        fingerprint,
        traces,
        agentOutput.stages,
        verifier,
        agentOutput.plugin?.version
      )
      await writeJson(resultPath, result)
      results.push(result)
    } catch (error) {
      await capturePatch(dirs).catch(() => undefined)
      await writeJson(resolve(dirs.root, "agent-output.json"), agentOutput).catch(() => undefined)
      traces ??= existingTraceSummary(dirs, agentOutput.threadIds)
      if (traces) await writeJson(resolve(dirs.root, "trace-summary.json"), traces).catch(() => undefined)
      const failureClass = error instanceof EvalError ? error.failureClass : "infrastructure"
      const result = failedRun(
        config,
        plan,
        fingerprint,
        failureClass,
        asErrorMessage(error),
        traces,
        agentOutput.stages
      )
      await writeJson(resultPath, result)
      results.push(result)
    }
  }
  return results
}

export async function reevaluateRun(
  config: BenchmarkConfig,
  plan: RunPlan,
  fingerprint: string
): Promise<RunResult> {
  const dirs = runDirectories(plan)
  const agentOutput = JSON.parse(await readFile(resolve(dirs.root, "agent-output.json"), "utf8")) as AgentRunOutput
  const traces = summarizeTraces(readTraces(dirs.traces).filter((trace) => agentOutput.threadIds.includes(trace.threadId)))
  const verifier = await runVerifier(config, dirs.repo)
  await writeJson(resolve(dirs.verifier, "result.json"), verifier)
  const result = evaluateRun(config, plan, fingerprint, traces, agentOutput.stages, verifier, agentOutput.plugin?.version)
  await writeJson(resolve(dirs.root, "result.json"), result)
  return result
}

export async function runVerifierContract(config: BenchmarkConfig): Promise<{ baseline: boolean; gold: boolean; root: string }> {
  const root = resolve(config.reportRoot, `_verifier-contract-${Date.now()}`)
  const baselinePlan: RunPlan = {
    id: "contract-baseline",
    condition: "control",
    repeat: 1,
    taskId: config.task.id,
    reportDir: resolve(root, "baseline")
  }
  const goldPlan: RunPlan = { ...baselinePlan, id: "contract-gold", reportDir: resolve(root, "gold") }
  const baselineDirs = await prepareRunDirectories(baselinePlan)
  await checkoutTaskRepository(config, baselineDirs)
  const baselineResult = await runVerifier(config, baselineDirs.repo)
  await writeJson(resolve(baselineDirs.verifier, "result.json"), baselineResult)
  const goldDirs = await prepareRunDirectories(goldPlan)
  await checkoutTaskRepository(config, goldDirs)
  const apply = await runProcess(["git", "apply", "--check", config.verifier.goldPatchPath], { cwd: goldDirs.repo, timeoutMs: 30_000 })
  if (apply.exitCode !== 0) throw new EvalError("verifier", `gold patch check 失败：${apply.stderr}`, "重新生成与固定 PetClinic commit 对应的 gold.patch。")
  const applyReal = await runProcess(["git", "apply", config.verifier.goldPatchPath], { cwd: goldDirs.repo, timeoutMs: 30_000 })
  if (applyReal.exitCode !== 0) throw new EvalError("verifier", `gold patch 应用失败：${applyReal.stderr}`, "修复 gold.patch。")
  const goldResult = await runVerifier(config, goldDirs.repo)
  await writeJson(resolve(goldDirs.verifier, "result.json"), goldResult)
  const baselineOk = baselineResult.scores.build === 1
    && baselineResult.scores.regression === 1
    && baselineResult.scores.feature < 1
    && baselineResult.scores.integration < 1
  if (!baselineOk || !goldResult.resolved) {
    throw new EvalError("verifier", "baseline/gold 双向合约未通过", `查看 ${root} 下的 verifier 结果。`)
  }
  return { baseline: baselineOk, gold: goldResult.resolved, root }
}

export type { WorkflowStageRecord }
