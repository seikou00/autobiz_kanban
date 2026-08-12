import { EvalError } from "./errors.ts"
import type { TestCaseResult } from "./types.ts"

function decodeXml(value: string): string {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&")
}

function attribute(attributes: string, name: string): string | undefined {
  const match = new RegExp(`\\b${name}="([^"]*)"`).exec(attributes)
  return match ? decodeXml(match[1]!) : undefined
}

export function parseJUnitXml(xml: string): TestCaseResult[] {
  const cases: TestCaseResult[] = []
  const pattern = /<testcase\b([^>]*?)(?:\/>|>([\s\S]*?)<\/testcase>)/g
  for (const match of xml.matchAll(pattern)) {
    const attributes = match[1] ?? ""
    const body = match[2] ?? ""
    const name = attribute(attributes, "name")
    if (!name) throw new EvalError("verifier", "JUnit testcase 缺少 name", "检查 Surefire XML 是否完整。")
    const time = Number(attribute(attributes, "time") ?? "0")
    const failure = /<failure\b([^>]*)>([\s\S]*?)<\/failure>|<failure\b([^>]*)\/>/.exec(body)
    const error = /<error\b([^>]*)>([\s\S]*?)<\/error>|<error\b([^>]*)\/>/.exec(body)
    const skipped = /<skipped\b/.test(body)
    const status = error ? "error" : failure ? "failed" : skipped ? "skipped" : "passed"
    const message = error
      ? attribute(error[1] ?? error[3] ?? "", "message") ?? decodeXml((error[2] ?? "").trim())
      : failure
        ? attribute(failure[1] ?? failure[3] ?? "", "message") ?? decodeXml((failure[2] ?? "").trim())
        : undefined
    cases.push({
      name,
      status,
      durationSeconds: Number.isFinite(time) ? time : 0,
      ...(message ? { message } : {})
    })
  }
  if (cases.length === 0) {
    throw new EvalError("verifier", "JUnit XML 没有 testcase", "确认 hidden test 被精确执行且 Surefire 生成报告。")
  }
  const names = cases.map((item) => item.name)
  if (new Set(names).size !== names.length) {
    throw new EvalError("verifier", "JUnit XML testcase name 重复", "检查 Surefire 报告是否混入重复 suite。")
  }
  return cases
}
