import assert from "node:assert/strict"
import test from "node:test"

import { appendSkillInvocation, formatSkillInvocation } from "../src/cmbdevclaw-driver.ts"
import { EvalError } from "../src/errors.ts"
import { answerUserInput } from "../src/user-input.ts"
import {
  assertWorkflowAction,
  assertWorkflowHandoff,
  assertWorkflowProgress,
  decodeRunDetail
} from "../src/workflow.ts"

test("answers every CMBDevClaw question with its recommended choice", () => {
  const decision = answerUserInput({
    requestId: "request-1",
    threadId: "thread-1",
    createdAt: "2026-08-12T00:00:00.000Z",
    questions: [
      {
        header: "Scope",
        id: "scope",
        question: "Choose scope",
        options: [
          { label: "Narrow", description: "One layer" },
          { label: "Full (Recommended)", description: "All layers" }
        ]
      },
      {
        header: "Style",
        id: "style",
        question: "Choose style",
        options: [{ label: "Default", description: "Stable" }]
      }
    ]
  }, new Date("2026-08-12T01:02:03.000Z"))

  assert.equal(decision.answers.scope?.optionIndex, 1)
  assert.equal(decision.answers.style?.optionIndex, 0)
  assert.equal(decision.submittedAt, "2026-08-12T01:02:03.000Z")
})

function detail(status: string, skill: string) {
  return decodeRunDetail({
    project: { projectId: "project-1" },
    run: {
      slug: "pet-weight-tracking",
      currentNodeId: "dev.specs",
      nodes: [{ id: "dev.specs", nodeStatus: status }]
    },
    workflow: {
      nodes: [{
        id: "dev.specs",
        states: [{ nodeStatus: status, nextAction: { slashSkill: skill, userMessage: "continue" } }]
      }],
      states: []
    }
  })
}

test("decodes Harness state and checks the exact Skill next action", () => {
  const projection = detail("specs_pending", "autodev-specs")

  assert.equal(projection.projectId, "project-1")
  assert.equal(assertWorkflowAction(projection, "dev.specs", "autodev-specs").userMessage, "continue")
  assert.throws(() => assertWorkflowAction(projection, "dev.specs", "autodev-code"), EvalError)
})

test("accepts the Harness completed-node handoff to the next Skill", () => {
  const projection = detail("done", "autodev-plan")

  assert.equal(assertWorkflowHandoff(projection, "dev.specs", "autodev-plan").userMessage, "continue")
  assert.throws(() => assertWorkflowHandoff(projection, "dev.plan", "autodev-plan"), /交接节点不匹配/)
  assert.throws(() => assertWorkflowHandoff(projection, "dev.specs", "autodev-code"), /交接 nextAction 不匹配/)
  assert.throws(() => assertWorkflowHandoff(detail("in_progress", "autodev-plan"), "dev.specs", "autodev-plan"), /交接节点不匹配/)
})

test("requires observable Harness progress", () => {
  const before = detail("specs_pending", "autodev-specs")
  const after = detail("specs_done", "autodev-plan")

  assert.doesNotThrow(() => assertWorkflowProgress(before, after))
  assert.throws(() => assertWorkflowProgress(before, before), /workflow 无进展/)
})

test("serializes the explicit CMBDevClaw plugin Skill protocol", () => {
  const block = formatSkillInvocation({
    name: "autodev-specs",
    path: "/plugin/skills/a&b/SKILL.md",
    description: "Specs <contract>",
    metadata: { whenToUse: "Before > code" },
    allowedTools: ["read_file", "execute"]
  })

  assert.match(block, /^<CMBDEVCLAW-SKILL-USE-V1>/)
  assert.match(block, /<name>autodev-specs<\/name>/)
  assert.match(block, /<path>\/plugin\/skills\/a&amp;b\/SKILL.md<\/path>/)
  assert.match(block, /<description>Specs &lt;contract&gt;<\/description>/)
  assert.match(block, /<when_to_use>Before &gt; code<\/when_to_use>/)
  assert.ok(appendSkillInvocation("do it", { name: "autodev-specs", path: "/skill/SKILL.md" }).endsWith("</CMBDEVCLAW-SKILL-USE-V1>"))
})
