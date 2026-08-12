import { EvalError } from "./errors.ts"
import type { UserInputAnswer, UserInputDecision, UserInputRequest } from "./types.ts"

const RECOMMENDED_PATTERN = /\brecommended\b|推荐/i

export function answerUserInput(request: UserInputRequest, now = new Date()): UserInputDecision {
  if (!request.requestId || !request.threadId || request.questions.length === 0) {
    throw new EvalError("user_input", "收到无效的 request_user_input 请求", "检查 CMBDevClaw userInput payload。")
  }
  const answers: Record<string, UserInputAnswer> = {}
  for (const question of request.questions) {
    if (!question.id || question.options.length === 0) {
      throw new EvalError("user_input", `问题 ${question.id || "(missing id)"} 没有候选项`, "让 Skill 提供可选择候选。")
    }
    const recommended = question.options.findIndex((option) => RECOMMENDED_PATTERN.test(option.label))
    const optionIndex = recommended >= 0 ? recommended : 0
    const option = question.options[optionIndex]!
    answers[question.id] = {
      type: "option",
      questionId: question.id,
      optionIndex,
      label: option.label,
      description: option.description
    }
  }
  return {
    requestId: request.requestId,
    threadId: request.threadId,
    questions: request.questions,
    answers,
    submittedAt: now.toISOString()
  }
}
