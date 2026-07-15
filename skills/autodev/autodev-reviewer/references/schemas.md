# Completion Review Schemas

本文件定义代码阶段捕获的 Git 基线、Executor 的完成声明和 Reviewer 的正式交接报告。`cowork.completion-proposal.v1` 采用向后兼容的可选字段扩展，不创建 v2。

## review-baseline.json

`/autodev-code` 在修改业务文件前调用 `scripts/capture_review_baseline.py` 生成：

```json
{
  "schema_version": "autobizdevops.review-baseline.v1",
  "captured_at": "2026-07-15T08:00:00+00:00",
  "capture_sources": ["module_manifest", "explicit_repo"],
  "repositories": [
    {
      "id": "frontend",
      "path": "/absolute/path/to/frontend",
      "base_sha": "full-commit-sha",
      "branch": "feature/example",
      "initial_status": [],
      "initial_dirty_paths": [],
      "initial_untracked_paths": [],
      "scope_confidence": "full"
    }
  ]
}
```

- `base_sha` 是代码修改前的 HEAD，不得在 review 前重新捕获。
- `scope_confidence=partial` 表示起点已有脏文件；Reviewer 必须检查这些路径是否与 Feature 变更重叠。
- 仓库匹配以 canonical path 为准，`id` 只用于显示和 proposal 映射。

## completion-proposal.json

主 agent 创建 `.autobizdevops/features/{slug}/completion-proposal.json`：

```json
{
  "schema_version": "cowork.completion-proposal.v1",
  "session_id": "optional-session-id",
  "created_at": "2026-07-15T16:05:00+08:00",
  "task": {
    "id": "optional-ticket-or-user-request-id",
    "summary": "描述用户任务。"
  },
  "prd_references": [
    {
      "path": ".autobizdevops/features/{slug}/PRD.md",
      "description": "用户要求参考的原始 PRD。",
      "required": true
    }
  ],
  "contract_references": [
    {"path": ".autobizdevops/features/{slug}/proposal.md", "type": "proposal", "required": true},
    {"path": ".autobizdevops/features/{slug}/specs/**/*.md", "type": "specs", "required": true},
    {"path": ".autobizdevops/features/{slug}/design.md", "type": "design", "required": false},
    {"path": ".autobizdevops/features/{slug}/PLAN.md", "type": "plan", "required": false}
  ],
  "review_scope": {
    "baseline_path": ".autobizdevops/features/{slug}/review-baseline.json",
    "strategy": "feature_start_head",
    "repositories": [
      {
        "repository_id": "frontend",
        "path": "/absolute/path/to/frontend",
        "base_sha": "sha-from-review-baseline",
        "head_sha_at_proposal": "current-head-sha",
        "include_index": true,
        "include_worktree": true,
        "include_untracked": true
      }
    ]
  },
  "affected_repositories": [
    {
      "id": "frontend",
      "path": "../frontend",
      "role": "用户界面与前端交互",
      "required": true,
      "source": "user_input",
      "source_evidence": "用户输入要求前后端共同完成。",
      "expected_changes": ["实现前端入口、状态展示和错误处理。"]
    }
  ],
  "summary": "描述本次声称完成了什么。",
  "files_changed": [
    {
      "repository_id": "frontend",
      "path": "src/example.ts",
      "change_type": "modified",
      "summary": "说明这个文件声称改了什么。",
      "risk": "low"
    }
  ],
  "behavior_changed": ["列出可观察行为变化。"],
  "verification": {
    "commands": [
      {
        "command": "npm test -- example",
        "status": "passed",
        "summary": "12 tests passed。",
        "evidence": "日志路径或可核验输出；没有时写 none。"
      }
    ],
    "manual_checks": [
      {"description": "手工场景", "result": "passed", "evidence": "事实证据或 none"}
    ]
  },
  "known_limitations": [
    {"description": "已知风险", "impact": "影响", "follow_up": "后续动作"}
  ],
  "not_done": ["明确不在本次范围内的工作。"]
}
```

### Proposal 规则

- `review_scope` 从原始 `review-baseline.json` 复制 base SHA，并在 proposal 创建时独立读取当前 HEAD；不得用 review 时临时生成的 SHA 冒充起点。
- 多仓库任务中，`review_scope.repositories` 必须覆盖全部 required `affected_repositories`；按 canonical path 核对，`repository_id` 必须映射到 `affected_repositories[].id`。
- 新流程必须设置三个 include flag 为 true。tracked scope 使用 merge-base 验证后的 `git diff <base_sha>`，未跟踪文件单独获取。
- 旧 Feature 没有 baseline 时，`review_scope.strategy=legacy_scope`，列出当前可用的仓库证据并在 `known_limitations` 披露范围限制；不得伪造 base SHA。
- `prd_references` 只记录用户提供的原始路径，不用主 agent 摘要替代；没有时写空数组。
- `files_changed`、verification 和 limitations 都是完成声明，Reviewer 必须独立核验。
- `affected_repositories[].source` 只能是 `user_input`、`prd`、`implementation_diff`、`repo_discovery`、`inferred`。
- 用户主动提供的 PRD 或仓库不得从 proposal 遗漏。

## REQUIREMENTS_EVAL.md

Reviewer coordinator 唯一允许写入的正式报告，也是 `/autodev-utest` 与 `/autodev-e2e` 的输入：

```markdown
# Requirements Evaluation

## Review Mode

independent_task | inline_main_agent

## Review Topology

dual_axis_parallel | dual_axis_single_reviewer

## Verdict

PASS | PASS_WITH_WARNINGS | FAIL | DEGRADED

## Summary

一句话结论。

## Review Scope

| Repository | Path | Base SHA | Head SHA | Scope Confidence | Initial Dirty Overlap | Changed Files |
|---|---|---|---|---|---|---|
| frontend | `/path/frontend` | `abc` | `def` | full / partial / degraded | none | `src/App.tsx` |

## Evidence

- Completion proposal: `completion-proposal.json`
- Baseline: `review-baseline.json` / legacy_scope
- Git commands: 每仓库实际使用的 base diff、commit log、status 和 untracked 命令
- Contract references: proposal/specs/design/PLAN/可选 PRD
- Verification evidence: completion proposal 中可核验的日志或输出

## Repositories Reviewed

| Repository | Path | Source | Git Status | Committed | Staged | Unstaged | Untracked |
|---|---|---|---|---|---|---|---|

## Axis Summary

| Axis | Status | Findings | Worst Finding |
|---|---|---|---|
| Standards | PASS / WARN / FAIL | 0 | none |
| Spec | PASS / WARN / FAIL | 0 | none |

## Standards Sources

| Repository / Scope | Source | Rule Coverage | Tool Evidence |
|---|---|---|---|

## Standards Review

| ID | Severity | Kind | Judgement Call | Location | Standard Source | Evidence | Impact | Suggested Action |
|---|---|---|---|---|---|---|---|---|

没有 finding 时写 none。

## Spec Review

| ID | Severity | Category | Location | Spec Source | Evidence | Impact | Required Action |
|---|---|---|---|---|---|---|---|

没有 finding 时写 none。

## Requirement Coverage

| Requirement | Status | Evidence | Risk |
|---|---|---|---|
| specs/... / Requirement / Scenario | covered / missing / risky / not_applicable | `frontend: src/App.tsx` | low / medium / high / blocker |

## E2E Focus

- 下游必须验证的用户路径、API、UI 行为和跨仓库集成风险；没有时写 none 并说明原因。

## Blockers

- 汇总两个轴的 blocker，但保留 axis、finding id 和 repo id；没有时写 none。

## Warnings

- 汇总 important/minor、legacy scope 限制；没有时写 none。

## Required Next Action

- PASS / PASS_WITH_WARNINGS: 进入 `/autodev-utest`。
- FAIL: 主 agent 只修复 blockers/must fix，更新 proposal 后重新 review。
- DEGRADED: 停止并等待用户处理缺失能力、仓库或 scope 证据。
```

### Axis status 与总 Verdict

- Standards：存在 Standards blocker 为 `FAIL`；只有 important/minor 为 `WARN`；无 finding 为 `PASS`。Smell 永远不能单独形成 blocker。
- Spec：存在 requirement/spec/claim blocker 为 `FAIL`；只有非阻塞 finding 为 `WARN`；无 finding 为 `PASS`。
- 任一必要 scope、仓库、contract 或写报告能力不可用，总 Verdict=`DEGRADED`。
- 否则任一轴 `FAIL`，总 Verdict=`FAIL`。
- 否则任一轴 `WARN` 或 scope confidence=`partial`，总 Verdict=`PASS_WITH_WARNINGS`。
- 其余为 `PASS`。两个轴的 finding 不互相抵消或 rerank。

### Severity 与 category

- `blocker`：使完成声明失效，或带来可信的正确性、安全、数据、部署风险。
- `important`：应该尽快处理，但不直接使完成声明失效。
- `minor`：有价值的清理、文档或维护建议。
- Spec categories：`claim_mismatch`、`spec_mismatch`、`requirement_gap`、`missing_evidence`、`test_gap`、`silent_failure`、`unfinished_work`、`contract_inconsistency`、`type_invariant`、`comment_rot`、`scope_creep`。
- Standards categories：`standard_violation`、`code_smell`、`quality`。
- Shared scope category：`scope_uncertainty`。

报告必须保留单一 `Verdict`、`Requirement Coverage`、`E2E Focus`、`Blockers` 和 `Warnings` 章节，以保持下游兼容。不要新增独立后置门禁文件。
`Review Mode` 必须与启动 prompt 一致；`inline_main_agent` 不得表述为独立子代理审查。
