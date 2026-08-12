import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { resolve } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import { EvalError } from "../src/errors.ts"
import type { ProcessResult } from "../src/types.ts"
import {
  assertVerifierProcessHealthy,
  prepareVerifierImage,
  runVerifier,
  verifierMavenCachePath
} from "../src/verifier.ts"

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
    () => assertVerifierProcessHealthy("build", processResult({
      exitCode: 1,
      stderr: "wget: Failed to fetch https://repo.maven.apache.org/maven2/apache-maven.tar.gz"
    })),
    (error: unknown) => error instanceof EvalError && error.failureClass === "infrastructure" && /Maven 依赖/.test(error.message)
  )
  assert.throws(
    () => assertVerifierProcessHealthy("build", processResult({ exitCode: 125, stderr: "daemon unavailable" })),
    (error: unknown) => error instanceof EvalError && error.failureClass === "infrastructure"
  )
})

test("uses fixed image Maven and one persistent cache for all verifier stages", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "springboot-tdd-verifier-"))
  const repoPath = resolve(root, "repo")
  const config = { ...loadConfig(configPath), reportRoot: resolve(root, "reports") }
  const calls: string[][] = []
  mkdirSync(repoPath, { recursive: true })
  try {
    await runVerifier(config, repoPath, async (argv) => {
      calls.push(argv)
      if (argv[1] === "image") return processResult({ argv, cwd: repoPath })
      if (argv.some((item) => item === `-Dtest=${config.verifier.testClass}`)) {
        const reportDir = resolve(repoPath, "target", "surefire-reports")
        mkdirSync(reportDir, { recursive: true })
        writeFileSync(
          resolve(reportDir, "TEST-org.springframework.samples.petclinic.owner.WeightRecordApiBenchmarkTest.xml"),
          '<testsuite><testcase name="feature_recordsValidWeight" time="0.1"/></testsuite>',
          "utf8"
        )
      }
      return processResult({ argv, cwd: repoPath })
    })

    const mavenCalls = calls.filter((argv) => argv.includes(config.verifier.mavenExecutable))
    const cacheMount = `${verifierMavenCachePath(config)}:/home/dev/.m2`
    assert.equal(mavenCalls.length, 3)
    assert.ok(mavenCalls.every((argv) => argv.includes(config.verifier.mavenExecutable)))
    assert.ok(mavenCalls.every((argv) => argv.includes(cacheMount)))
    assert.ok(mavenCalls.every((argv) => argv.includes("-U")))
    assert.ok(mavenCalls.every((argv) => !argv.includes("./mvnw")))
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
