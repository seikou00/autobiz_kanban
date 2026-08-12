import { createHash } from "node:crypto"

import { EvalError } from "./errors.ts"

export function asRecord(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new EvalError("setup", `${field} 必须是对象`, `检查 benchmark_config.yaml 的 ${field}。`)
  }
  return value as Record<string, unknown>
}

export function asString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new EvalError("setup", `${field} 必须是非空字符串`, `填写 benchmark_config.yaml 的 ${field}。`)
  }
  return value.trim()
}

export function asInteger(value: unknown, field: string, minimum = 1): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new EvalError("setup", `${field} 必须是大于等于 ${minimum} 的整数`, `修正 ${field}。`)
  }
  return value as number
}

export function asBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new EvalError("setup", `${field} 必须是布尔值`, `修正 ${field}。`)
  }
  return value
}

export function asStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new EvalError("setup", `${field} 必须是非空数组`, `填写 ${field}。`)
  }
  const values = value.map((item, index) => asString(item, `${field}[${index}]`))
  if (new Set(values).size !== values.length) {
    throw new EvalError("setup", `${field} 包含重复值`, `删除 ${field} 中的重复项。`)
  }
  return values
}

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`
  }
  return JSON.stringify(value)
}

export function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex")
}
