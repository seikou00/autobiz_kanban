import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { resolve } from "node:path"
import test from "node:test"

import { expandEnvironment, loadConfig } from "../src/config.ts"
import { EvalError } from "../src/errors.ts"
import { batchFingerprint, resolveModelRuntimeIdentity } from "../src/fingerprint.ts"
import { buildRunMatrix } from "../src/matrix.ts"
import { isSupportedNodeVersion } from "../src/preflight.ts"
import type { PluginSnapshotManifest } from "../src/types.ts"

const configPath = resolve(import.meta.dirname, "..", "config", "benchmark_config.yaml")

interface RawConfigFixture {
  reportRoot: string
  task: { promptPath: string; sourcePath: string; provenancePath: string; repoCommit: string }
  app: { projectPath: string; commit: string; traceVersion: string }
  plugin: { root: string }
  verifier: {
    hiddenTestPath: string
    goldPatchPath: string
    image: string
    mavenExecutable: string
    imagePullTimeoutMs: number
  }
  conditions: Array<{ id: string; pluginEnabled: boolean }>
  workflow: { nodes: string[]; terminalCheckpoint: string }
}

function withConfigMutation(mutate: (raw: RawConfigFixture) => void, assertion: (path: string) => void): void {
  const loaded = loadConfig(configPath)
  const raw = JSON.parse(readFileSync(configPath, "utf8")) as RawConfigFixture
  raw.reportRoot = loaded.reportRoot
  raw.task.promptPath = loaded.task.promptPath
  raw.task.sourcePath = loaded.task.sourcePath
  raw.task.provenancePath = loaded.task.provenancePath
  raw.app.projectPath = loaded.app.projectPath
  raw.plugin.root = loaded.plugin.root
  raw.verifier.hiddenTestPath = loaded.verifier.hiddenTestPath
  raw.verifier.goldPatchPath = loaded.verifier.goldPatchPath
  mutate(raw)
  const root = mkdtempSync(resolve(tmpdir(), "springboot-tdd-config-"))
  const path = resolve(root, "benchmark_config.yaml")
  writeFileSync(path, `${JSON.stringify(raw, null, 2)}\n`, "utf8")
  try {
    assertion(path)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

test("loads the pinned benchmark config and builds the balanced matrix", () => {
  const config = loadConfig(configPath)
  const matrix = buildRunMatrix(config)

  assert.equal(config.app.version, "1.4.9")
  assert.equal(config.app.traceVersion, "39.8.10")
  assert.equal(config.plugin.expectedVersion, "1.0.83")
  assert.equal(config.model.temperature, 0.01)
  assert.equal(config.verifier.mavenExecutable, "/opt/maven/bin/mvn")
  assert.equal(config.verifier.imagePullTimeoutMs, 1_800_000)
  assert.equal(matrix.length, 6)
  assert.deepEqual(matrix.map((item) => item.id), [
    "springboot-tdd__control__r01",
    "springboot-tdd__control__r02",
    "springboot-tdd__control__r03",
    "springboot-tdd__full-chain__r01",
    "springboot-tdd__full-chain__r02",
    "springboot-tdd__full-chain__r03"
  ])
})

test("matrix filtering is deterministic and rejects an invalid repeat", () => {
  const config = loadConfig(configPath)
  const filtered = buildRunMatrix(config, { conditions: ["full-chain"], repeats: [2] })

  assert.deepEqual(filtered.map((item) => item.id), ["springboot-tdd__full-chain__r02"])
  assert.throws(() => buildRunMatrix(config, { repeats: [4] }), EvalError)
  assert.throws(() => buildRunMatrix(config, { repeats: [1, 1] }), /repeat 过滤包含重复值/)
})

test("environment expansion uses explicit values, fallbacks, and fails closed", () => {
  assert.equal(expandEnvironment("${EVAL_ROOT:-fallback}/app", { EVAL_ROOT: "/fixed" }), "/fixed/app")
  assert.equal(expandEnvironment("${EVAL_ROOT:-fallback}/app", {}), "fallback/app")
  assert.throws(() => expandEnvironment("${EVAL_ROOT}/app", {}), /修复：设置 EVAL_ROOT/)
})

test("accepts only the documented Node.js runtime range", () => {
  assert.equal(isSupportedNodeVersion("22.5.0"), false)
  assert.equal(isSupportedNodeVersion("22.6.0"), true)
  assert.equal(isSupportedNodeVersion("24.99.0"), true)
  assert.equal(isSupportedNodeVersion("25.0.0"), false)
})

test("fingerprint includes the resolved model runtime identity", () => {
  const config = loadConfig(configPath)
  const snapshot: PluginSnapshotManifest = {
    schemaVersion: 1,
    createdAt: "2026-08-12T00:00:00.000Z",
    zipPath: "/tmp/plugin.zip",
    zipSha256: "zip",
    pluginName: config.plugin.expectedName,
    pluginVersion: config.plugin.expectedVersion,
    gitHead: "a".repeat(40),
    gitBranch: "main",
    gitDirty: false,
    dirtyDiffSha256: "diff",
    files: [],
    fingerprint: "plugin-fingerprint"
  }
  const firstEnv = {
    [config.model.baseUrlEnv]: "https://model.example/v1",
    [config.model.modelEnv]: "model-a"
  }
  const secondEnv = { ...firstEnv, [config.model.modelEnv]: "model-b" }

  assert.equal(resolveModelRuntimeIdentity(config, firstEnv).model, "model-a")
  const firstFingerprint = batchFingerprint(config, snapshot, firstEnv)
  assert.notEqual(firstFingerprint, batchFingerprint(config, snapshot, secondEnv))
  assert.notEqual(firstFingerprint, batchFingerprint({
    ...config,
    app: { ...config.app, traceVersion: "40.0.0" }
  }, snapshot, firstEnv))
  assert.throws(() => resolveModelRuntimeIdentity(config, {}), /模型运行身份不完整/)
})

test("config validation rejects mutable or ambiguous benchmark inputs", () => {
  const mutations: Array<(raw: RawConfigFixture) => void> = [
    (raw) => { raw.task.repoCommit = "" },
    (raw) => { raw.app.commit = "abc" },
    (raw) => { raw.app.traceVersion = "1.4.9" },
    (raw) => { raw.verifier.image = "zhangyiiiiii/swe-skills-bench-jvm:latest" },
    (raw) => { raw.verifier.mavenExecutable = "mvn" },
    (raw) => { raw.task.promptPath = "/definitely/missing/springboot-tdd-task.md" },
    (raw) => { raw.conditions = [{ id: "control", pluginEnabled: false }, { id: "unknown", pluginEnabled: true }] },
    (raw) => { raw.conditions = [{ id: "control", pluginEnabled: false }, { id: "control", pluginEnabled: false }] },
    (raw) => { raw.workflow.nodes[0] = "dev.unknown" },
    (raw) => { raw.workflow.terminalCheckpoint = "code_done" }
  ]

  for (const mutate of mutations) {
    withConfigMutation(mutate, (path) => assert.throws(() => loadConfig(path, {}), EvalError))
  }
})
