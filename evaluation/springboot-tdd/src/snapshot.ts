import { lstatSync, readFileSync, readdirSync } from "node:fs"
import { mkdir } from "node:fs/promises"
import { basename, relative, resolve, sep } from "node:path"

import { canonicalJson, sha256 } from "./codec.ts"
import { EvalError } from "./errors.ts"
import { writeJson } from "./io.ts"
import { runProcess } from "./process.ts"
import type { BenchmarkConfig, PluginSnapshotFile, PluginSnapshotManifest } from "./types.ts"

const PACKAGE_DIRECTORIES = ["board_core", "hooks", "skills", "agents"]

function normalizedRelative(root: string, path: string): string {
  return relative(root, path).split(sep).join("/")
}

function shouldExclude(path: string): boolean {
  const parts = path.split(sep)
  return parts.includes("__pycache__") || path.endsWith(".pyc") || basename(path) === ".DS_Store"
}

function walkFiles(root: string, start: string, output: string[]): void {
  for (const name of readdirSync(start).sort()) {
    const path = resolve(start, name)
    if (shouldExclude(path)) continue
    const stat = lstatSync(path)
    if (stat.isSymbolicLink()) {
      throw new EvalError("setup", `插件包包含符号链接：${normalizedRelative(root, path)}`, "改为普通文件后重试。")
    }
    if (stat.isDirectory()) walkFiles(root, path, output)
    else if (stat.isFile()) output.push(path)
  }
}

export function collectPackagedFiles(pluginRoot: string): string[] {
  const paths: string[] = []
  const mcp = resolve(pluginRoot, ".mcp.json")
  if (lstatExists(mcp)) paths.push(mcp)
  for (const directory of PACKAGE_DIRECTORIES) {
    const path = resolve(pluginRoot, directory)
    if (lstatExists(path)) walkFiles(pluginRoot, path, paths)
  }
  for (const name of readdirSync(pluginRoot).sort()) {
    const path = resolve(pluginRoot, name)
    if (!lstatSync(path).isFile()) continue
    if (name.endsWith(".py") || (name.endsWith(".json") && name !== ".mcp.json")) paths.push(path)
  }
  return [...new Set(paths)].sort((left, right) => normalizedRelative(pluginRoot, left).localeCompare(normalizedRelative(pluginRoot, right)))
}

function lstatExists(path: string): boolean {
  try {
    lstatSync(path)
    return true
  } catch {
    return false
  }
}

function snapshotFiles(pluginRoot: string): PluginSnapshotFile[] {
  return collectPackagedFiles(pluginRoot).map((path) => {
    const content = readFileSync(path)
    return {
      path: normalizedRelative(pluginRoot, path),
      size: content.byteLength,
      sha256: sha256(content)
    }
  })
}

async function gitValue(pluginRoot: string, argv: string[]): Promise<string> {
  const result = await runProcess(["git", "-C", pluginRoot, ...argv], { cwd: pluginRoot, timeoutMs: 30_000 })
  if (result.exitCode !== 0) {
    throw new EvalError("setup", `无法读取插件 Git 信息：${result.stderr.trim()}`, "确认 plugin.root 是可读 Git 仓库。")
  }
  return result.stdout.trim()
}

export async function createPluginSnapshot(
  config: BenchmarkConfig,
  batchDir: string
): Promise<PluginSnapshotManifest> {
  await mkdir(batchDir, { recursive: true })
  const zipPath = resolve(batchDir, "plugin.zip")
  const packageResult = await runProcess(["bash", config.plugin.packageScript, zipPath], {
    cwd: config.plugin.root,
    timeoutMs: 300_000
  })
  if (packageResult.exitCode !== 0) {
    throw new EvalError(
      "setup",
      `插件打包失败：${packageResult.stderr.trim() || packageResult.stdout.trim()}`,
      "确认 zip 可用且 package_workspace.sh 可以执行。"
    )
  }
  const files = snapshotFiles(config.plugin.root)
  const manifestRecord = JSON.parse(readFileSync(resolve(config.plugin.root, "plugin.json"), "utf8")) as {
    name?: unknown
    version?: unknown
  }
  const gitHead = await gitValue(config.plugin.root, ["rev-parse", "HEAD"])
  const gitBranch = await gitValue(config.plugin.root, ["rev-parse", "--abbrev-ref", "HEAD"])
  const status = await gitValue(config.plugin.root, ["status", "--porcelain=v1", "--untracked-files=all"])
  const diff = await gitValue(config.plugin.root, ["diff", "--binary", "HEAD"])
  const core = {
    zipSha256: sha256(readFileSync(zipPath)),
    pluginName: String(manifestRecord.name ?? ""),
    pluginVersion: String(manifestRecord.version ?? ""),
    gitHead,
    gitBranch,
    gitDirty: status.length > 0,
    dirtyDiffSha256: sha256(`${status}\n${diff}`),
    files
  }
  const manifest: PluginSnapshotManifest = {
    schemaVersion: 1,
    createdAt: new Date().toISOString(),
    zipPath,
    ...core,
    fingerprint: sha256(canonicalJson(core))
  }
  await writeJson(resolve(batchDir, "plugin-manifest.json"), manifest)
  return manifest
}

export function assertSnapshotUnchanged(manifest: PluginSnapshotManifest): void {
  const current = sha256(readFileSync(manifest.zipPath))
  if (current !== manifest.zipSha256) {
    throw new EvalError("setup", "batch 中的 plugin.zip 已变化", "重新开始 batch，所有 run 使用同一 snapshot。")
  }
}
