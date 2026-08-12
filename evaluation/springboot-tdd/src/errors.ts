import type { FailureClass } from "./types.ts"

export class EvalError extends Error {
  readonly failureClass: FailureClass
  readonly fix: string
  readonly causeValue: unknown

  constructor(failureClass: FailureClass, message: string, fix: string, causeValue?: unknown) {
    super(`${message}\n修复：${fix}`)
    this.name = "EvalError"
    this.failureClass = failureClass
    this.fix = fix
    this.causeValue = causeValue
  }
}

export function asErrorMessage(value: unknown): string {
  return value instanceof Error ? value.message : String(value)
}
