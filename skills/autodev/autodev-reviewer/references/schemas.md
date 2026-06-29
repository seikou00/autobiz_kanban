# Completion Review Schemas

这里定义主 agent 的完成声明，以及独立 reviewer 的正式需求评估交接文件。字段名保持稳定，方便后续工具解析。跨仓库支持直接扩展 `cowork.completion-proposal.v1`，不创建 v2。

## 稳定 ID 规范

- Requirement ID：`REQ-001`、`REQ-002` ...
- Scenario ID：`SCN-001`、`SCN-002` ...
- Task ID：`T001`、`T002` ...
- Evidence ID：`ev_0001`、`ev_0002` ...
- Eval ID：`eval_0001`、`eval_0002` ...
- 跨阶段引用应使用稳定本地 ID，例如 `specs/<capability>/spec.md#REQ-001`、`specs/<capability>/spec.md#SCN-001`、`design.md#API-001`、`T001`、`ev_0001`。

## .autobizdevops/features/{slug}/completion-proposal.json

主 agent 第一阶段只需要写这个文件。

```
{
  "schema_version": "cowork.completion-proposal.v1",
  "session_id": "optional-session-id",
  "created_at": "2026-04-17T12:00:00-07:00",
  "task": {
    "id": "optional-ticket-or-user-request-id",
    "summary": "描述用户任务。"
  },
  "prd_references": [
    {
      "path": ".autobizdevops/features/{slug}/PRD.md",
      "description": "用户要求 reviewer 参考的原始 PRD 文件。没有 PRD 时使用空数组。",
      "required": true
    }
  ],
  "contract_references": [
    {
      "path": ".autobizdevops/features/{slug}/proposal.md",
      "type": "proposal",
      "required": true
    },
    {
      "path": ".autobizdevops/features/{slug}/specs/**/*.md",
      "type": "specs",
      "required": true
    },
    {
      "path": ".autobizdevops/features/{slug}/design.md",
      "type": "design",
      "required": true
    },
    {
      "path": ".autobizdevops/features/{slug}/plan.json",
      "type": "plan",
      "required": true
    }
  ],
  "affected_repositories": [
    {
      "id": "frontend",
      "path": "../frontend",
      "role": "用户界面与前端交互",
      "required": true,
      "source": "user_input",
      "source_evidence": "用户输入中提到 frontend/backend 需要共同完成。",
      "expected_changes": [
        "实现 specs 中的前端入口、状态展示和错误处理。"
      ]
    },
    {
      "id": "backend",
      "path": "../backend",
      "role": "后端接口、数据校验和业务规则",
      "required": true,
      "source": "prd",
      "source_evidence": "PRD 明确要求前后端共同交付接口与页面。",
      "expected_changes": [
        "提供前端所需 API，并保持字段、错误码和权限规则一致。"
      ]
    }
  ],
  "summary": "描述本次声称完成了什么。",
  "task_refs": [
    "T001"
  ],
  "spec_refs": [
    "specs/{capability}/spec.md#REQ-001",
    "specs/{capability}/spec.md#SCN-001"
  ],
  "evidence_ids": [
    "ev_0001"
  ],
  "files_changed": [
    {
      "repository_id": "frontend",
      "path": "src/example.ts",
      "change_type": "modified",
      "summary": "说明这个文件声称改了什么。",
      "risk": "low"
    }
  ],
  "behavior_changed": [
    "列出声称发生变化的可观察行为。没有则留空数组。"
  ],
  "verification": {
    "commands": [
      {
        "command": "npm test -- example",
        "status": "passed",
        "summary": "声称 12 tests passed。"
      }
    ],
    "manual_checks": [
      {
        "description": "手工验证的场景。",
        "result": "passed",
        "evidence": "简短事实证据；没有证据时明确写 none。"
      }
    ]
  },
  "known_limitations": [
    {
      "description": "已知未完成项或风险。",
      "impact": "说明它为什么重要。",
      "follow_up": "后续应该怎么处理。"
    }
  ],
  "not_done": [
    "明确不在本次范围内的工作。"
  ]
}
```

proposal 规则：

- prd_references 是用户提供的原始 PRD 文件入口。主 agent 只记录路径和简短说明，不要用自己的 PRD 摘要替代文件路径。没有 PRD 时写空数组。
- contract_references 固定记录 feature 目录中的 proposal.md、specs/**/*.md、design.md、plan.json。reviewer 以 specs Requirement / Scenario 作为行为验收主依据，以 design.md 作为接口、数据和技术决策依据，以 plan.json 作为任务闭环事实源。
- 如果用户明确提供 PRD 路径，主 agent 必须把它写入 prd_references；遗漏用户提供的 PRD 会使完成声明不可信。
- affected_repositories 是 v1 的扩展字段。跨仓库任务必须填写；单仓库任务可以省略或留空，reviewer 会把当前 cwd 当作唯一仓库。
- affected_repositories[].id 是仓库稳定标识，供 files_changed[].repository_id、报告、blocker 和 warning 引用。
- affected_repositories[].path 是本地仓库路径，可以是相对协调仓库的路径，也可以是绝对路径。
- affected_repositories[].required 为 true 时，reviewer 必须能访问该路径并确认它是 git 仓库；否则 verdict 为 DEGRADED。
- affected_repositories[].source 只能使用 user_input、prd、implementation_diff、repo_discovery、inferred。
- affected_repositories[].source_evidence 必须写明纳入该仓库的依据。用户主动输入仓库信息时，主 agent 必须使用 source=user_input 并把输入事实转写到 source_evidence。
- affected_repositories[].expected_changes 描述该仓库声称完成的行为或改动，不要只写“已修改”。
- files_changed 是主 agent 的完成声明，不是最终事实；reviewer 会独立用 shell/git 核对。
- files_changed[].repository_id 是可选字段；跨仓库任务中必须填写，并且必须匹配 affected_repositories[].id。单仓库旧流程可以省略。
- task_refs / spec_refs / evidence_ids 是稳定追踪引用。没有它们也能评审，但有它们时 reviewer 会优先按引用追溯。
- verification.commands 记录“声称运行过的验证”。如果没有真实输出证据，不要夸大，只写能确认的事实。
- known_limitations 必须诚实。没有已知限制时才留空。
- not_done 用来区分“明确不做”和“忘了做”。
- reviewer 没有隐式用户对话上下文。所有可审查上下文必须来自 completion proposal、proposal.md、specs、design、plan.json、evidence/EVIDENCE.jsonl、可选 PRD、启动 prompt 或真实 repo 状态。若需要 reviewer 检查用户主动输入的仓库是否被遗漏，启动 prompt 必须额外提供 User repository references。

## .autobizdevops/features/{slug}/REVIEW_FINDINGS.json

reviewer 必须直接写这个文件。它是下游 `/autodev-utest`、`/autodev-e2e` 与 `/autodev-verify` 的机器事实源；`REQUIREMENTS_EVAL.md` 只是可选人类报告，不参与机器对账。

```json
{
  "version": 1,
  "verdict": "PASS_WITH_WARNINGS",
  "findings": [
    {
      "id": "R001",
      "taskId": "T001",
      "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
      "evidenceIds": ["ev_0001"],
      "severity": "medium",
      "message": "Edge case still needs E2E coverage",
      "suggestedCheckpoint": "code_in_progress"
    }
  ]
}
```

规则：

- `verdict` 必须为 `PASS`、`PASS_WITH_WARNINGS`、`FAIL` 或 `DEGRADED`。
- 只有 `PASS` / `PASS_WITH_WARNINGS` 是 review 收敛态，可进入下游。
- `FAIL` 必须至少有一条 blocker/high/important 级 finding 指向失败原因。
- 每条 finding 必须有稳定 `taskId`、`specRefs`、`evidenceIds`。没有真实 evidence 时不得伪造；应回到 code 阶段补齐证据。
- `specRefs` 中的 `SCN-xxx` 是 scenario 覆盖投影来源，不另建平行 scenario 源。

## .autobizdevops/features/{slug}/REQUIREMENTS_EVAL.md

reviewer 可同步写这个文件作为人类报告。它不是下游机器输入，verdict 以 `REVIEW_FINDINGS.json` 为准。

```
# Requirements Evaluation

## Verdict

PASS | PASS_WITH_WARNINGS | FAIL | DEGRADED

## Summary

一句话说明本次实现与需求覆盖结论。

## Evidence

- Completion proposal: `.autobizdevops/features/{slug}/completion-proposal.json`
- Git status: `git status --short`（跨仓库任务中逐仓库列出）
- Git diff: `git diff --name-only` / `git diff --binary`（跨仓库任务中逐仓库列出）
- PRD references: 用户显式提供的 PRD 路径（没有 PRD 时写 none）
- Contract references: `proposal.md`, `specs/**/*.md`, `design.md`, `plan.json`, `evidence/EVIDENCE.jsonl`
- Verification evidence: proposal 中声明的测试、lint、build 或手工验证证据

## Repositories Reviewed

| Repository | Path | Source | Git Status | Changed Files |
|---|---|---|---|---|
| frontend | `../frontend` | user_input | clean / dirty / staged / unavailable | `src/example.ts` |

单仓库旧流程没有 affected_repositories 时，可以写 none 或写 current repository。

## Requirement Coverage

| Requirement | Status | Evidence | Risk |
|---|---|---|---|
| specs/[capability]/spec.md#REQ-001 / #SCN-001 | covered / missing / risky / not_applicable | `frontend: src/App.tsx` / `backend: app/api/example.py` / `cross-repo: API contract` | low / medium / high / blocker |

## E2E Focus

- 下游 E2E 必须验证的用户路径、API、UI 行为或风险点。跨仓库任务中必须标明 API contract、字段一致性、配置同步、迁移顺序等集成风险。
- 如果没有可自动化的 E2E 重点，明确写 none，并说明原因。

## Blockers

- 没有 blocker 时写 none。

## Warnings

- 没有 warning 时写 none。

## Required Next Action

- PASS / PASS_WITH_WARNINGS: 进入 `/autodev-utest`。
- FAIL: 主 agent 修复 blockers 或 must fix 项后，更新 `completion-proposal.json` 并重新运行 `/autodev-reviewer`。
- DEGRADED: 停止并等待用户下一步指令；不要把独立审查未成立伪装成 PASS。
```

规则：

- `REQUIREMENTS_EVAL.md` 若生成，落盘到 `.autobizdevops/features/{slug}/REQUIREMENTS_EVAL.md`。
- 不新增 `VERIFY_REPORT.md` 等后置文件门禁；没有额外 PRD 引用时不要求读取 PRD.md。
- verdict 必须能追溯到 completion proposal、proposal.md、specs、design.md、可选 PRD、shell/git 输出和实际文件内容。
- 跨仓库任务中，verdict 必须能追溯到每个 affected repository 的 shell/git 输出和实际文件内容。
- `Repositories Reviewed` 必须列出每个被审查仓库的 id、path、source、git status、changed files。required 仓库不可访问、不是 git 仓库或无法获取状态时，verdict 必须为 DEGRADED。
- `E2E Focus` 是给 `/autodev-e2e` 的交接摘要，不要复制整份 diff 或完整审查报告。
- `Requirement Coverage` 的 evidence、`Blockers` 和 `Warnings` 必须标明 repo id 或 `cross-repo`，避免下游无法定位。
- 如果 verdict 是 FAIL，Required Next Action 必须列出需要修复后重新 review 的 blockers 或 must fix 项。
- 如果 verdict 是 DEGRADED，Required Next Action 必须说明停止并等待用户，不得引导 agent 立即修复。

severity 规则：

- blocker：使完成声明失效，或带来可信的正确性、安全、数据、部署风险。
- important：应该尽快处理，但不直接使完成声明失效。
- minor：有价值的清理、文档或维护性建议。

推荐 category：

- **claim_mismatch**：completion proposal 和真实 git diff 不一致
- **spec_mismatch**：实现、completion proposal 或验证结果和 specs 行为契约不一致
- **requirement_gap**：specs 中的 Requirement / Scenario 未被实现或验证
- **missing_evidence**：声称跑了测试/验证，但缺少证据
- **test_gap**：测试覆盖缺口
- **silent_failure**：吞错、假 fallback、用户不可见失败
- **unfinished_work**：TODO、stub、mock、disabled test 等半成品
- **contract_inconsistency**：API、类型、配置、路由、文档不一致
- **type_invariant**：类型设计或 invariant 问题
- **comment_rot**：注释/文档和代码不一致
- **scope_creep**：改了任务范围外的东西
- **quality**：一般代码质量问题
