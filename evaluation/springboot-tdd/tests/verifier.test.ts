import assert from "node:assert/strict"
import { resolve } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import { EvalError } from "../src/errors.ts"
import type { ProcessResult } from "../src/types.ts"
import { assertVerifierProcessHealthy, prepareVerifierImage } from "../src/verifier.ts"

const configPath = resolve(import.meta.dirname, "..", "config", "benchmark_config.yaml")

function processResult(overrides: Partial<ProcessResult> = {}): ProcessResult {
  return {
    argv: ["docker"],
    cwd: "/tmp/repo",
    exitCode: 0,
    signal: null,
    stdout: "",
    stderr: "",
    durationMs: 10,
    timedOut: false,
    ...overrides
  }
}

test("pulls a missing verifier image before the Maven timeout starts", async () => {
  const config = loadConfig(configPath)
  const calls: Array<{ argv: string[]; timeoutMs: number | undefined }> = []
  const results = [
    processResult({ exitCode: 1, stderr: "No such image" }),
    processResult({ stderr: "Pull complete" })
  ]

  await prepareVerifierImage(config, config.plugin.root, async (argv, options) => {
    calls.push({ argv, timeoutMs: options.timeoutMs })
    return results.shift()!
  })

  assert.deepEqual(calls[0]?.argv.slice(0, 3), ["docker", "image", "inspect"])
  assert.deepEqual(calls[1]?.argv.slice(0, 3), ["docker", "pull", "--platform"])
  assert.equal(calls[1]?.timeoutMs, config.verifier.imagePullTimeoutMs)
})

test("does not pull an already cached verifier image", async () => {
  const config = loadConfig(configPath)
  let calls = 0

  await prepareVerifierImage(config, config.plugin.root, async () => {
    calls += 1
    return processResult()
  })

  assert.equal(calls, 1)
})

test("classifies image-pull and Maven timeouts as infrastructure failures", async () => {
  const config = loadConfig(configPath)
  const results = [
    processResult({ exitCode: 1, stderr: "No such image" }),
    processResult({ exitCode: 143, timedOut: true, durationMs: 1_802_000 })
  ]

  await assert.rejects(
    prepareVerifierImage(config, config.plugin.root, async () => results.shift()!),
    (error: unknown) => error instanceof EvalError && error.failureClass === "infrastructure" && /拉取验证镜像失败/.test(error.message)
  )

  assert.throws(
    () => assertVerifierProcessHealthy("build", processResult({ exitCode: 143, timedOut: true, durationMs: 902_143 })),
    (error: unknown) => error instanceof EvalError && error.failureClass === "infrastructure" && /build 超时/.test(error.message)
  )
  assert.doesNotThrow(() => assertVerifierProcessHealthy("build", processResult({ exitCode: 1 })))
  assert.throws(
    () => assertVerifierProcessHealthy("build", processResult({ exitCode: 125, stderr: "daemon unavailable" })),
    (error: unknown) => error instanceof EvalError && error.failureClass === "infrastructure"
  )
})
