import { existsSync, readFileSync } from "node:fs"
import { copyFile, mkdir, unlink } from "node:fs/promises"
import { dirname, resolve } from "node:path"

import { EvalError } from "./errors.ts"
import { parseJUnitXml } from "./junit.ts"
import { runProcess } from "./process.ts"
import type { BenchmarkConfig, ProcessResult, TestCaseResult, VerifierResult } from "./types.ts"

type ProcessRunner = typeof runProcess

const DOCKER_INFRASTRUCTURE_EXIT_CODES = new Set([125, 126, 127, 137, 143])
const NETWORK_FAILURE_PATTERNS = [
  /wget: Failed to fetch/i,
  /UnknownHostException/i,
  /ConnectException/i,
  /SocketTimeoutException/i,
  /Connection (?:reset|refused|timed out)/i,
  /Network is unreachable/i,
  /Temporary failure in name resolution/i,
  /Name or service not known/i,
  /PKIX path building failed/i,
  /status code: (?:429|502|503|504)\b/i
]

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
  const cachePath = verifierMavenCachePath(config)
  await mkdir(cachePath, { recursive: true })
  return await processRunner([
    "docker",
    "run",
    "--rm",
    "--platform",
    config.verifier.platform,
    "-v",
    `${repoPath}:/workspace/spring-petclinic`,
    "-v",
    `${cachePath}:/home/dev/.m2`,
    "-w",
    "/workspace/spring-petclinic",
    config.verifier.image,
    config.verifier.mavenExecutable,
    "-q",
    "-B",
    ...mavenArgs
  ], { cwd: repoPath, timeoutMs: config.verifier.timeoutMs })
}

function processDetail(result: ProcessResult): string {
  return (result.stderr.trim() || result.stdout.trim() || `exit=${String(result.exitCode)}`).slice(-2_000)
}

function hasNetworkFailure(result: ProcessResult): boolean {
  const output = `${result.stderr}\n${result.stdout}`
  return NETWORK_FAILURE_PATTERNS.some((pattern) => pattern.test(output))
}

export function verifierMavenCachePath(config: BenchmarkConfig): string {
  const digest = config.verifier.image.split("@sha256:")[1]?.slice(0, 16) ?? "unknown"
  return resolve(config.reportRoot, "_cache", `maven-${digest}`)
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
  if (result.exitCode !== 0 && hasNetworkFailure(result)) {
    throw new EvalError(
      "infrastructure",
      `验证器 ${stage} 无法下载 Maven 依赖：${processDetail(result)}`,
      "恢复 Maven 仓库网络后仅重评该 run；已下载依赖会保留在评测缓存中。",
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
  const hiddenClassPath = resolve(
    repoPath,
    "target/test-classes/org/springframework/samples/petclinic/owner/WeightRecordApiBenchmarkTest.class"
  )
  const hiddenTextPath = resolve(
    repoPath,
    "target/surefire-reports/org.springframework.samples.petclinic.owner.WeightRecordApiBenchmarkTest.txt"
  )
  const removeArtifact = async (path: string): Promise<void> => {
    await unlink(path).catch((error: NodeJS.ErrnoException) => {
      if (error.code !== "ENOENT") throw error
    })
  }
  await Promise.all([hiddenDestination, hiddenClassPath, xmlPath, hiddenTextPath].map(removeArtifact))
  try {
    const build = await runMaven(config, repoPath, ["-DskipTests", "compile"], processRunner)
    assertVerifierProcessHealthy("build", build)
    const regression = build.exitCode === 0
      ? await runMaven(config, repoPath, ["test"], processRunner)
      : notRun(repoPath, "build failed; regression not run")
    if (build.exitCode === 0) assertVerifierProcessHealthy("regression", regression)
    await mkdir(dirname(hiddenDestination), { recursive: true })
    await copyFile(config.verifier.hiddenTestPath, hiddenDestination)
    const hidden = build.exitCode === 0
      ? await runMaven(config, repoPath, [`-Dtest=${config.verifier.testClass}`, "test"], processRunner)
      : notRun(repoPath, "build failed; hidden test not run")
    if (build.exitCode === 0) assertVerifierProcessHealthy("hidden tests", hidden)
    let tests: TestCaseResult[] = []
    if (existsSync(xmlPath)) tests = parseJUnitXml(readFileSync(xmlPath, "utf8"))
    else if (build.exitCode === 0 && hidden.exitCode === 0) {
      throw new EvalError(
        "verifier",
        `hidden JUnit XML 缺失：${xmlPath}`,
        "检查固定测试类名和 Maven/Surefire 测试发现配置。",
        hidden
      )
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
  } finally {
    await Promise.all([hiddenDestination, hiddenClassPath].map(removeArtifact))
  }
}

export { FEATURE_TESTS, INTEGRATION_TESTS }
