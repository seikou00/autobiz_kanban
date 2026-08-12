import { readFileSync } from "node:fs"

import { canonicalJson, sha256 } from "./codec.ts"
import { EvalError } from "./errors.ts"
import type { BenchmarkConfig, PluginSnapshotManifest } from "./types.ts"

export interface ModelRuntimeIdentity {
  baseUrl: string
  model: string
}

export function resolveModelRuntimeIdentity(
  config: BenchmarkConfig,
  env: NodeJS.ProcessEnv = process.env
): ModelRuntimeIdentity {
  const baseUrl = env[config.model.baseUrlEnv]?.trim()
  const model = env[config.model.modelEnv]?.trim()
  if (!baseUrl || !model) {
    throw new EvalError(
      "setup",
      "模型运行身份不完整",
      `设置 ${config.model.baseUrlEnv} 和 ${config.model.modelEnv} 后重试。`
    )
  }
  return { baseUrl, model }
}

export function batchFingerprint(
  config: BenchmarkConfig,
  snapshot: PluginSnapshotManifest,
  env: NodeJS.ProcessEnv = process.env
): string {
  const modelRuntime = resolveModelRuntimeIdentity(config, env)
  return sha256(canonicalJson({
    schemaVersion: config.schemaVersion,
    benchmarkId: config.benchmarkId,
    repeats: config.repeats,
    conditions: config.conditions,
    task: {
      id: config.task.id,
      repoUrl: config.task.repoUrl,
      repoCommit: config.task.repoCommit,
      promptSha256: sha256(readFileSync(config.task.promptPath)),
      sourceSha256: sha256(readFileSync(config.task.sourcePath)),
      provenanceSha256: sha256(readFileSync(config.task.provenancePath))
    },
    app: {
      commit: config.app.commit,
      packageVersion: config.app.version,
      traceVersion: config.app.traceVersion
    },
    plugin: { fingerprint: snapshot.fingerprint, version: snapshot.pluginVersion },
    model: {
      id: config.model.id,
      runtime: { baseUrlSha256: sha256(modelRuntime.baseUrl), model: modelRuntime.model },
      maxTokens: config.model.maxTokens,
      maxOutputTokens: config.model.maxOutputTokens,
      temperature: config.model.temperature
    },
    workflow: config.workflow,
    verifier: {
      image: config.verifier.image,
      platform: config.verifier.platform,
      testClass: config.verifier.testClass,
      timeoutMs: config.verifier.timeoutMs,
      hiddenTestSha256: sha256(readFileSync(config.verifier.hiddenTestPath)),
      goldPatchSha256: sha256(readFileSync(config.verifier.goldPatchPath))
    },
    timeouts: config.timeouts
  }))
}
