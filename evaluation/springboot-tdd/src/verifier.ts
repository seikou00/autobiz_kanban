import { existsSync, readFileSync } from "node:fs"
import { copyFile, mkdir, unlink } from "node:fs/promises"
import { dirname, resolve } from "node:path"

import { EvalError } from "./errors.ts"
import { parseJUnitXml } from "./junit.ts"
import { runProcess } from "./process.ts"
import type { BenchmarkConfig, ProcessResult, TestCaseResult, VerifierResult } from "./types.ts"

const FEATURE_TESTS = [
  "feature_recordsValidWeight",
  "feature_missingWeightIsRejectedWithoutSideEffect",
  "feature_missingRecordDateIsRejectedWithoutSideEffect",
  "feature_invalidRecordDateIsRejectedWithoutSideEffect",
  "feature_zeroWeightIsRejectedWithoutSideEffect",
  "feature_negativeWeightIsRejectedWithoutSideEffect",
  "feature_unknownPetIsNotFoundWithoutSideEffect",
  "feature_petOwnedByAnotherOwnerIsNotFoundWithoutSideEffect",
  "feature_historyReturnsStableJsonFields",
  "feature_historyForPetOwnedByAnotherOwnerIsNotFound"
]

const INTEGRATION_TESTS = ["integration_recordIsPersisted", "integration_historyIsNewestFirst"]

function notRun(cwd: string, reason: string): ProcessResult {
  return {
    argv: [],
    cwd,
    exitCode: null,
    signal: null,
    stdout: "",
    stderr: reason,
    durationMs: 0,
    timedOut: false
  }
}

async function runMaven(
  config: BenchmarkConfig,
  repoPath: string,
  mavenArgs: string[]
): Promise<ProcessResult> {
  return await runProcess([
    "docker",
    "run",
    "--rm",
    "--platform",
    config.verifier.platform,
    "-v",
    `${repoPath}:/workspace/spring-petclinic`,
    "-w",
    "/workspace/spring-petclinic",
    config.verifier.image,
    "./mvnw",
    "-q",
    "-B",
    ...mavenArgs
  ], { cwd: repoPath, timeoutMs: config.verifier.timeoutMs })
}

function scoreExpected(tests: TestCaseResult[], expectedNames: string[]): number {
  const byName = new Map(tests.map((test) => [test.name, test]))
  const passed = expectedNames.filter((name) => byName.get(name)?.status === "passed").length
  return passed / expectedNames.length
}

export async function runVerifier(config: BenchmarkConfig, repoPath: string): Promise<VerifierResult> {
  const hiddenDestination = resolve(
    repoPath,
    "src/test/java/org/springframework/samples/petclinic/owner/WeightRecordApiBenchmarkTest.java"
  )
  const xmlPath = resolve(
    repoPath,
    "target/surefire-reports/TEST-org.springframework.samples.petclinic.owner.WeightRecordApiBenchmarkTest.xml"
  )
  await unlink(hiddenDestination).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT") throw error
  })
  const build = await runMaven(config, repoPath, ["-DskipTests", "compile"])
  const regression = build.exitCode === 0
    ? await runMaven(config, repoPath, ["test"])
    : notRun(repoPath, "build failed; regression not run")
  await mkdir(dirname(hiddenDestination), { recursive: true })
  await copyFile(config.verifier.hiddenTestPath, hiddenDestination)
  const previousXml = existsSync(xmlPath) ? readFileSync(xmlPath, "utf8") : undefined
  const hidden = build.exitCode === 0
    ? await runMaven(config, repoPath, [`-Dtest=${config.verifier.testClass}`, "test"])
    : notRun(repoPath, "build failed; hidden test not run")
  let tests: TestCaseResult[] = []
  if (existsSync(xmlPath)) {
    const xml = readFileSync(xmlPath, "utf8")
    if (xml === previousXml) {
      throw new EvalError("verifier", `hidden JUnit XML 未被本次运行更新：${xmlPath}`, "检查 Maven 是否在测试执行前失败。")
    }
    tests = parseJUnitXml(xml)
  }
  else if (build.exitCode === 0) {
    throw new EvalError("verifier", `hidden JUnit XML 缺失：${xmlPath}`, "检查 Maven/Surefire 输出，不能仅依赖进程退出码。")
  }
  const scores = {
    build: build.exitCode === 0 && !build.timedOut ? 1 : 0,
    regression: regression.exitCode === 0 && !regression.timedOut ? 1 : 0,
    feature: scoreExpected(tests, FEATURE_TESTS),
    integration: scoreExpected(tests, INTEGRATION_TESTS)
  }
  return {
    build,
    regression,
    hidden,
    tests,
    scores,
    resolved: Object.values(scores).every((score) => score === 1) && hidden.exitCode === 0
  }
}

export { FEATURE_TESTS, INTEGRATION_TESTS }
