#!/usr/bin/env node
import { existsSync } from "node:fs"
import { resolve } from "node:path"
import { fileURLToPath } from "node:url"

import { loadConfig } from "./config.ts"
import { EvalError, asErrorMessage } from "./errors.ts"
import { batchFingerprint } from "./fingerprint.ts"
import { buildRunMatrix } from "./matrix.ts"
import { executeBatch, reevaluateRun, runVerifierContract } from "./orchestrator.ts"
import { assertPreflight, runPreflight } from "./preflight.ts"
import { compareResults, readRunResults, writeComparison } from "./reporting.ts"
import { createPluginSnapshot } from "./snapshot.ts"
import { runAppSmoke } from "./smoke.ts"
import type { ConditionId, PluginSnapshotManifest } from "./types.ts"

const PACKAGE_ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)))
const DEFAULT_CONFIG = resolve(PACKAGE_ROOT, "config", "benchmark_config.yaml")

interface Arguments {
  command: string
  configPath: string
  conditions?: ConditionId[]
  repeats?: number[]
  reportRoot?: string
  resume: boolean
}

function parseArguments(argv: string[]): Arguments {
  const command = argv[0] ?? "help"
  let configPath = DEFAULT_CONFIG
  let conditions: ConditionId[] | undefined
  let repeats: number[] | undefined
  let reportRoot: string | undefined
  let resume = false
  for (let index = 1; index < argv.length; index += 1) {
    const value = argv[index]!
    if (value === "--resume") {
      resume = true
      continue
    }
    const next = argv[index + 1]
    if (!next) throw new EvalError("setup", `${value} 缺少值`, `为 ${value} 提供参数。`)
    if (value === "--config") configPath = resolve(next)
    else if (value === "--report-root") reportRoot = resolve(next)
    else if (value === "--condition") {
      conditions = next.split(",").map((item) => item.trim() as ConditionId)
    } else if (value === "--repeat") {
      repeats = next.split(",").map((item) => Number(item.trim()))
    } else throw new EvalError("setup", `未知参数：${value}`, "运行 help 查看用法。")
    index += 1
  }
  return {
    command,
    configPath,
    ...(conditions ? { conditions } : {}),
    ...(repeats ? { repeats } : {}),
    ...(reportRoot ? { reportRoot } : {}),
    resume
  }
}

function usage(): string {
  return [
    "CMBDevClaw SpringBoot TDD evaluation",
    "",
    "Commands:",
    "  validate     validate config and pinned assets",
    "  list         list task, conditions and run matrix",
    "  preflight    check app/model/Java/Docker readiness",
    "  dry-run      print selected runs without launching the app",
    "  snapshot     package and fingerprint the plugin",
    "  app-smoke    launch CMBDevClaw and install the plugin without an agent call",
    "  run          execute selected paid model runs",
    "  evaluate     rerun hidden verification for completed agent runs",
    "  compare      write comparison.json and comparison.md",
    "  contract     execute baseline/gold verifier contract",
    "",
    "Options: --config PATH --report-root PATH --condition control,full-chain --repeat 1,2 --resume"
  ].join("\n")
}

async function main(): Promise<void> {
  const args = parseArguments(process.argv.slice(2))
  if (args.command === "help" || args.command === "--help" || args.command === "-h") {
    console.log(usage())
    return
  }
  const config = loadConfig(args.configPath)
  if (args.reportRoot) config.reportRoot = args.reportRoot
  const plans = buildRunMatrix(config, {
    ...(args.conditions ? { conditions: args.conditions } : {}),
    ...(args.repeats ? { repeats: args.repeats } : {})
  })
  if (args.command === "validate") {
    console.log(JSON.stringify({ ok: true, benchmarkId: config.benchmarkId, tasks: 1, conditions: config.conditions.length, runs: buildRunMatrix(config).length }, null, 2))
    return
  }
  if (args.command === "list" || args.command === "dry-run") {
    console.log(JSON.stringify({
      benchmarkId: config.benchmarkId,
      task: config.task.id,
      app: `package=${config.app.version}, trace=${config.app.traceVersion}@${config.app.commit}`,
      plugin: `${config.plugin.expectedName}@${config.plugin.expectedVersion}`,
      model: config.model.id,
      runs: plans
    }, null, 2))
    return
  }
  if (args.command === "preflight") {
    const checks = await runPreflight(config)
    console.log(JSON.stringify({ ok: checks.every((check) => !check.required || check.ok), checks }, null, 2))
    if (checks.some((check) => check.required && !check.ok)) process.exitCode = 1
    return
  }
  if (args.command === "snapshot") {
    const snapshot = await createPluginSnapshot(config, resolve(config.reportRoot, "_batch"))
    console.log(JSON.stringify(snapshot, null, 2))
    return
  }
  if (args.command === "app-smoke") {
    const result = await runAppSmoke(config)
    console.log(JSON.stringify({ ok: true, ...result }, null, 2))
    return
  }
  if (args.command === "run") {
    assertPreflight(await runPreflight(config))
    const results = await executeBatch(config, plans, { resume: args.resume })
    console.log(JSON.stringify(results, null, 2))
    return
  }
  if (args.command === "evaluate") {
    const manifestPath = resolve(config.reportRoot, "_batch", "plugin-manifest.json")
    if (!existsSync(manifestPath)) throw new EvalError("setup", "缺少 batch plugin manifest", "先执行 snapshot 或 run。")
    const { readFile } = await import("node:fs/promises")
    const snapshot = JSON.parse(await readFile(manifestPath, "utf8")) as PluginSnapshotManifest
    const fingerprint = batchFingerprint(config, snapshot)
    const results = []
    for (const plan of plans) results.push(await reevaluateRun(config, plan, fingerprint))
    console.log(JSON.stringify(results, null, 2))
    return
  }
  if (args.command === "compare") {
    const report = compareResults(readRunResults(config.reportRoot), config.repeats)
    await writeComparison(config.reportRoot, report)
    console.log(JSON.stringify(report, null, 2))
    return
  }
  if (args.command === "contract") {
    const result = await runVerifierContract(config)
    console.log(JSON.stringify(result, null, 2))
    return
  }
  throw new EvalError("setup", `未知命令：${args.command}`, "运行 help 查看用法。")
}

main().catch((error) => {
  console.error(asErrorMessage(error))
  process.exitCode = error instanceof EvalError ? 2 : 1
})
