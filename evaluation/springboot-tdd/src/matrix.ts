import { resolve } from "node:path"

import { EvalError } from "./errors.ts"
import type { BenchmarkConfig, ConditionId, RunPlan } from "./types.ts"

export interface MatrixFilter {
  conditions?: ConditionId[]
  repeats?: number[]
}

export function buildRunMatrix(config: BenchmarkConfig, filter: MatrixFilter = {}): RunPlan[] {
  const configuredConditions = new Set(config.conditions.map((item) => item.id))
  for (const condition of filter.conditions ?? []) {
    if (!configuredConditions.has(condition)) {
      throw new EvalError("setup", `condition 不存在：${condition}`, "使用 control 或 full-chain。")
    }
  }
  if (filter.conditions && new Set(filter.conditions).size !== filter.conditions.length) {
    throw new EvalError("setup", "condition 过滤包含重复值", "删除重复 condition。")
  }
  const allowedConditions = new Set(filter.conditions ?? config.conditions.map((item) => item.id))
  const repeats = filter.repeats ?? Array.from({ length: config.repeats }, (_item, index) => index + 1)
  if (new Set(repeats).size !== repeats.length) {
    throw new EvalError("setup", "repeat 过滤包含重复值", "删除重复 repeat。")
  }
  for (const repeat of repeats) {
    if (!Number.isInteger(repeat) || repeat < 1 || repeat > config.repeats) {
      throw new EvalError("setup", `repeat 超出范围：${repeat}`, `使用 1..${config.repeats}。`)
    }
  }
  const plans: RunPlan[] = []
  for (const condition of config.conditions) {
    if (!allowedConditions.has(condition.id)) continue
    for (const repeat of repeats) {
      const id = `${config.task.id}__${condition.id}__r${String(repeat).padStart(2, "0")}`
      plans.push({
        id,
        condition: condition.id,
        repeat,
        taskId: config.task.id,
        reportDir: resolve(config.reportRoot, id)
      })
    }
  }
  if (plans.length === 0) {
    throw new EvalError("setup", "run matrix 为空", "检查 --condition 与 --repeat 过滤条件。")
  }
  return plans
}
