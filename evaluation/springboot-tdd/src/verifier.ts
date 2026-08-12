import { existsSync, readFileSync } from "node:fs"
import { copyFile, mkdir, unlink } from "node:fs/promises"
import { dirname, resolve } from "node:path"

import { EvalError } from "./errors.ts"
import { parseJUnitXml } from "./junit.ts"
import { runProcess } from "./process.ts"
import type { BenchmarkConfig, ProcessResult, TestCaseResult, VerifierResult } from "./types.ts"

type ProcessRunner = typeof runProcess

const DOCKER_INFRASTRUCTURE_EXIT_CODES = new Set([125, 126, 127, 137, 143])

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
  mavenArgs: string[],
  processRunner: ProcessRunner
): Promise<ProcessResult> {
  return await processRunner([
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

function processDetail(result: ProcessResult): string {
  return (result.stderr.trim() || result.stdout.trim() || `exit=${String(result.exitCode)}`).slice(-2_000)
}

export function assertVerifierProcessHealthy(stage: string, result: ProcessResult): void {
  if (result.timedOut) {
    throw new EvalError(
      "infrastructure",
      `验证器 ${stage} 超时（${result.durationMs}ms）`,
      "检查 Docker 资源和 Maven 依赖状态；确认后仅重评该 run。",
      result
    )
  }
  if (result.signal !== null || result.exitCode === null || DOCKER_INFRASTRUCTURE_EXIT_CODES.has(result.exitCode)) {
    throw new EvalError(
      "infrastructure",
      `验证器 ${stage} 的 Docker 进程异常终止：${processDetail(result)}`,
      "检查 Docker Desktop、容器资源和固定验证镜像后重试。",
      result
    )
  }
}

export async function prepareVerifierImage(
  config: BenchmarkConfig,
  cwd: string,
  processRunner: ProcessRunner = runProcess
): Promise<void> {
  const inspect = await processRunner(["docker", "image", "inspect", config.verifier.image], {
    cwd,
    timeoutMs: 30_000
  })
  if (inspect.timedOut || inspect.signal !== null || inspect.exitCode === null) {
    throw new EvalError(
      "infrastructure",
      `检查验证镜像失败：${processDetail(inspect)}`,
      "启动 Docker Desktop 并检查本地镜像状态后重试。",
      inspect
    )
  }
  if (inspect.exitCode === 0) return

  const pull = await processRunner([
    "docker",
    "pull",
    "--platform",
    config.verifier.platform,
    config.verifier.image
  ], { cwd, timeoutMs: config.verifier.imagePullTimeoutMs })
  if (pull.timedOut || pull.exitCode !== 0 || pull.signal !== null) {
    throw new EvalError(
      "infrastructure",
      `拉取验证镜像失败：${processDetail(pull)}`,
      "检查 Docker Hub 网络；镜像完整拉取后仅重评受影响的 run。",
      pull
    )
  }
}

function scoreExpected(tests: TestCaseResult[], expectedNames: string[]): number {
  const byName = new Map(tests.map((test) => [test.name, test]))
  const passed = expectedNames.filter((name) => byName.get(name)?.status === "passed").length
  return passed / expectedNames.length
}

export async function runVerifier(
  config: BenchmarkConfig,
  repoPath: string,
  processRunner: ProcessRunner = runProcess
): Promise<VerifierResult> {
  await prepareVerifierImage(config, repoPath, processRunner)
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
  const build = await runMaven(config, repoPath, ["-DskipTests", "compile"], processRunner)
  assertVerifierProcessHealthy("build", build)
  const regression = build.exitCode === 0
    ? await runMaven(config, repoPath, ["test"], processRunner)
    : notRun(repoPath, "build failed; regression not run")
  if (build.exitCode === 0) assertVerifierProcessHealthy("regression", regression)
  await mkdir(dirname(hiddenDestination), { recursive: true })
  await copyFile(config.verifier.hiddenTestPath, hiddenDestination)
  const previousXml = existsSync(xmlPath) ? readFileSync(xmlPath, "utf8") : undefined
  const hidden = build.exitCode === 0
    ? await runMaven(config, repoPath, [`-Dtest=${config.verifier.testClass}`, "test"], processRunner)
    : notRun(repoPath, "build failed; hidden test not run")
  if (build.exitCode === 0) assertVerifierProcessHealthy("hidden tests", hidden)
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
