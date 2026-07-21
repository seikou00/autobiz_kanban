---
name: autodev-reviewer
description: 对单个 feature 的完成声明做独立需求评审。Dev 实现完成后使用：主 agent 写 completion-proposal.json，启动 source-read-only 的独立 reviewer 子代理核验真实仓库状态，落盘 REQUIREMENTS_EVAL.md，并按 verdict 走修复复审闭环。
version: v1.3.0713
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-reviewer --feature "${feature}" --plain
```


# Completion Reviewer

使用此技能来避免执行者自证完成。主 agent 负责写完成声明、按失败审查结论修复问题并重新发起审查；独立 reviewer 只负责用真实仓库状态核验声明并落盘需求评估。

reviewer 没有隐式用户对话上下文。所有可审查上下文必须来自 completion proposal、feature 目录产物（proposal.md、specs/**/*.md、design.md、PLAN.md）、可选 PRD、启动 prompt 和真实 repo 状态。跨仓库任务中，当前 workspace 是协调仓库，业务仓库由 proposal 的 `affected_repositories` 显式列出。

## 严格职责边界

| 角色                | 职责                                                         | 禁止                                                         |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 主 agent / Executor | 写 completion-proposal.json；启动 reviewer；FAIL 时修复 blockers 并重新 review；最后摘要 verdict | 在同一回合同时执行 reviewer 与 executor 角色；替 reviewer 改评估；未经重新 review 就宣称完成 |
| Reviewer agent      | 通过 shell/git/read/search 独立核验 proposal；只允许写 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` | 修改源码、测试、配置、依赖、锁文件；运行任何写操作命令；修复问题 |

reviewer 的只读命令白名单、禁止清单、审查流程和评分标准全部在 references/reviewer-agent.md。如果 reviewer 无法用 shell/git 获取真实状态、无法访问 required 仓库或无法写报告文件，本次 review 不成立，verdict 记 `DEGRADED`。平台禁用 task 工具时允许主 agent 内联执行 reviewer 角色，但必须显式记录 `inline_main_agent` 模式并通过用户确认把 reviewer 与 executor 分隔到不同回合；不得把该模式包装成独立 review。

## 执行步骤

### 1. 准入

确定 `${feature}` 后读取 Feature 快照，捕获为 `CHECKPOINT`，后续准入和分支判断直接取用：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

开始审查前写入进行中状态：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint requirements_eval_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

### 2. 写 completion proposal

按 references/schemas.md 创建 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`，描述任务、规格输入、受影响仓库、改动、声称的验证、已知限制和未完成事项。两个输入入口：

- **PRD（可选，不是前置条件）**：用户提供 PRD 路径时（如"参考 .autobizdevops/features/feat-demo/PRD.md 做完成审查"），把路径原样写入 `prd_references`，支持多个；没有则写空数组。不要用自己总结的 PRD 内容替代文件路径，也不要提前判断实现是否满足 PRD——PRD 验收由 reviewer 独立完成。
- **跨仓库**：跨仓库任务必须写 `affected_repositories`（字段规则和示例见 references/schemas.md），且 `files_changed` 每项带 `repository_id`；单仓库任务省略，reviewer 会把当前 cwd 当作唯一仓库。用户主动输入的仓库必须以 `source: "user_input"` 记录并转写依据到 `source_evidence`。

### 3. 启动 reviewer 角色

先检查当前平台是否提供 task 工具，然后二选一执行：

- **`independent_task`**：task 工具可用时，通过 task 工具启动独立 reviewer 子代理。reviewer 返回后，主 agent 在同一回合继续执行第 4 步。
- **`inline_main_agent`**：task 工具被平台禁用或不可用时，主 agent 切换为 source-read-only reviewer 角色内联完成审查。`REQUIREMENTS_EVAL.md` 落盘后必须停止当前回合，明确告知用户本次为主 agent 内联 review，并请用户确认是否切回 executor 角色继续。未获得确认前，不得在同一回合读取 verdict 分支、修复问题或推进 checkpoint。

把 references/reviewer-agent.md 中的指令作为 reviewer prompt，并附带：

- `Review execution mode:` `independent_task` 或 `inline_main_agent`。
- `User PRD references:` 用户提供的原始 PRD 路径列表；没有则写 none。
- `User repository references:`（可选）仅当流程需要 reviewer 核对用户主动输入的仓库是否被 proposal 遗漏时附带；否则省略，reviewer 只以 proposal 和真实仓库状态为依据。

reviewer 自己通过工具获取真实仓库状态并直接写 `REQUIREMENTS_EVAL.md`；不要替它预生成 diff snapshot 或规格摘要。

### 4. 读取 verdict 并分支

`independent_task` 返回后，或 `inline_main_agent` 在后续回合获得用户明确确认后，主 agent 读取 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` 中的 verdict。不要接受自由文本的 "looks good" 作为 review 结果。

| Verdict | 含义 | 主 agent 动作 |
| --- | --- | --- |
| `PASS` | review 收敛 | 进入第 5 步 |
| `PASS_WITH_WARNINGS` | review 收敛，遗留非阻塞问题 | 进入第 5 步；最终回复必须摘要 warnings |
| `FAIL` | 修复与复审的中间态 | 按下方修复规则处理后回到第 3 步 |
| `DEGRADED` | 独立审查未成立的阻断态 | 停止，说明缺失的能力或证据，等待用户；不要当作可修复代码问题自动处理 |

FAIL 修复规则：

- 只修复 `REQUIREMENTS_EVAL.md` 中使 verdict 变为 FAIL 的 blockers 或明确 must fix 项。
- 不要替 reviewer 改写 `REQUIREMENTS_EVAL.md`；修复后必须重新启动独立 reviewer 生成新版评估。
- 每轮修复后必须更新 `completion-proposal.json`，使 files_changed、behavior_changed、verification、known_limitations 与真实状态一致。
- 如果修复需要超出当前任务范围、缺少信息、工具不可用或存在人工决策点，停止并报告 blocker，不要伪造 PASS。

### 5. 落盘完成 checkpoint

verdict 为 `PASS` 或 `PASS_WITH_WARNINGS` 后写入：

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.review --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint requirements_eval_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

### 6. 最终回复

## 核心协议

1. **主 agent 写 completion proposal。**按 references/schemas.md 创建 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`。proposal 应描述任务、规格输入、受影响仓库、改动、声称的验证、已知限制和未完成事项。跨仓库任务必须写 `affected_repositories`；单仓库任务可以省略该字段。
2. **主 agent 启动独立 reviewer agent。**使用 subagent 机制启动独立 reviewer。启动子代理，并把 references/reviewer-agent.md 中的 reviewer 指令作为 prompt。启动子agent附带用户提供的原始 PRD 路径列表；没有则写 none，供 reviewer 与 proposal.prd_references 交叉核对。如果流程希望 reviewer 核对用户主动输入的仓库是否被遗漏，启动 prompt 还必须附带 `User repository references`；否则 reviewer 只以 completion proposal、执行清单输入、可选 PRD 和真实仓库状态为依据。
3. **reviewer 自己获取真实状态。**reviewer 必须自行通过工具获取仓库状态，并读取执行清单列出的 proposal.md、specs/**/*.md、design.md、plan.json、evidence/EVIDENCE.jsonl；PRD 只在用户或 completion proposal 显式引用时读取。若 completion proposal 有 `affected_repositories`，reviewer 必须对每个仓库逐个执行 git status/diff/log 等只读检查；若没有，则按旧流程把当前 cwd 当作唯一仓库。不要依赖主 agent 预先生成的 diff snapshot 或规格摘要。
4. **reviewer 通过 writer 写结构化评审事实源。**reviewer 必须使用 `${pluginPath}/hooks/review_findings_writer.py` 写入机器事实源 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REVIEW_FINDINGS.json`，禁止直接整份写入或编辑该 JSON；可同步写入 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` 作为人类报告。
5. **主 agent 读取 verdict 并分支。**如果 `REVIEW_FINDINGS.json.verdict` 是 `PASS` 或 `PASS_WITH_WARNINGS`，报告 verdict 与 `REVIEW_FINDINGS.json` 路径后结束本阶段。如果 verdict 是 `FAIL`，主 agent 必须按 `REVIEW_FINDINGS.json.findings` 中的 blockers/high severity 项做最小修复，更新 `completion-proposal.json`，重新启动独立 reviewer，直到 verdict 变为 `PASS` 或 `PASS_WITH_WARNINGS`。如果 verdict 是 `DEGRADED`，停止并报告独立审查未成立。

## 严格职责边界

| 角色                | 职责                                                         | 禁止                                                         |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 主 agent / Executor | 写 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`；启动 reviewer；FAIL 时修复 blockers 并重新 review；最后摘要 verdict | 自己给 PASS；替 reviewer 改评估；未经重新 review 就宣称完成 |
| Reviewer agent      | 通过 shell/git/read/search 独立核验 proposal；写 REVIEW_FINDINGS.json，可同步写 REQUIREMENTS_EVAL.md 人类报告 | 修改源码、测试、配置、依赖文件、锁文件；运行 commit/push/deploy；修复问题 |

reviewer 可以使用 Write，但只允许写协调仓库中的：

${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REVIEW_FINDINGS.json
${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md

reviewer 可以使用 shell/Bash 获取只读证据，但只能运行读操作。跨仓库任务中，以下命令必须在每个 `affected_repositories[].path` 对应的仓库内逐个执行。允许示例：

```
pwd
git status --short
git diff --name-only
git diff --binary
git diff --cached --name-only
git diff --cached --binary
git log --oneline -n 5
rg "TODO|FIXME|HACK|stub|mock|skip\\(|describe\\.skip|it\\.skip" .
```

禁止示例：

```
git add
git commit
git push
git checkout
git reset
rm
mv
cp
npm install
任何会修改源码、依赖、缓存、构建产物或远端状态的命令
```

如果 reviewer 无法使用 shell/git 获取真实状态，无法访问 proposal 中 required 的仓库，或无法直接写报告文件，本次独立 review 不成立，必须标记为 DEGRADED，不得把自检包装成独立 review。

## 跨仓库输入入口

`affected_repositories` 用于声明本次完成声明涉及哪些 git 仓库，以及这些仓库为什么应被纳入审查。

跨仓库任务中，主 agent 必须在 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json` 写入 `affected_repositories`：

```json
{
  "affected_repositories": [
    {
      "id": "frontend",
      "path": "../frontend",
      "role": "用户界面与前端交互",
      "required": true,
      "source": "user_input",
      "source_evidence": "用户输入中提到 frontend/backend 需要共同完成",
      "expected_changes": [
        "实现 specs 中的前端入口、状态展示和错误处理"
      ]
    }
  ]
}
```

字段规则：

- `id` 是稳定仓库标识，供 `files_changed[].repository_id`、报告、blocker 和 warning 引用。
- `path` 是本地仓库路径，可以是相对协调仓库的路径，也可以是绝对路径。
- `required` 为 true 时，reviewer 必须能访问该路径并确认它是 git 仓库；否则 verdict 为 `DEGRADED`。
- `source` 只能使用 `user_input`、`prd`、`implementation_diff`、`repo_discovery`、`inferred`。
- `source_evidence` 必须写明纳入该仓库的依据。用户主动输入仓库信息时，必须使用 `source: "user_input"` 并把输入事实转写到 `source_evidence`。
- `expected_changes` 描述该仓库声称完成的行为或改动，不要只写“已修改”。

reviewer 没有隐式用户对话上下文。所有可审查上下文必须来自 completion proposal、执行清单输入、可选 PRD、启动 prompt 或真实 repo 状态。reviewer 只能核验 completion proposal 中已经记录的用户输入来源是否自洽；如果要检查用户主动输入是否被 proposal 遗漏，启动 reviewer 时必须额外传入 `User repository references`。

跨仓库任务中，`files_changed` 每项必须写 `repository_id`。单仓库旧流程可以继续只写 `path`。

## PRD 输入入口

PRD 是可选输入，不是使用本 skill 的前置条件。

当用户使用 skill 时提供 PRD 文件路径，例如“参考 .autobizdevops/features/feat-demo/PRD.md 做完成审查”，主 agent 必须把路径原样写入 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json` 的 prd_references。支持多个本地文件；没有 PRD 时写空数组。

没有 PRD 时，主 agent 启动 reviewer 的 prompt 中写明 User PRD references: none；reviewer 跳过 PRD 额外验收，仍以 specs 计算 `spec_alignment`。

主 agent 启动 reviewer 时，还必须在 reviewer prompt 中附带同一组原始 PRD 路径，例如：

```
User PRD references:
- .autobizdevops/features/feat-demo/PRD.md
```

主 agent 不要用自己总结的 PRD 内容替代文件路径，也不要提前判断实现是否满足 PRD。PRD 验收由 reviewer 独立完成。

reviewer 必须读取每个 prd_references[].path，并把 PRD 纳入审查依据：

- 需求覆盖：真实 diff 是否覆盖 PRD 中的目标、用户流程、验收标准。
- 行为一致：completion proposal 声称的行为是否与 proposal.md、specs/**/*.md 一致，是否遗漏关键 Requirement / Scenario；如用户提供 PRD，再检查是否存在上游规格遗漏。
- 范围控制：实现是否加入 PRD 未要求且有风险的 scope creep。
- 非功能要求：权限、安全、性能、兼容性、可观测性、迁移、降级等 PRD 约束是否被处理。
- 跨仓库边界：如果 PRD 或 specs 要求多个系统、服务或仓库共同交付，completion proposal 的 `affected_repositories` 是否覆盖这些边界。

如果用户提供了 PRD 路径但 reviewer 无法读取，或 proposal 遗漏用户明确提供的 PRD 路径，独立 review 不成立或完成声明不可信；按影响标记为 DEGRADED 或 FAIL。

## Review-Fix-Review 闭环

每轮 reviewer 写完 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REVIEW_FINDINGS.json` 后，主 agent 必须读取其中的 verdict：

- `PASS`：结束 review 阶段。
- `PASS_WITH_WARNINGS`：结束 review 阶段，但最终回复必须摘要 `REVIEW_FINDINGS.json.findings` 中的 warning。
- `FAIL`：不得进入下一阶段。主 agent 必须读取结构化 findings 中的 blockers/high severity 项，修复必须修复项，更新 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`，然后重新启动独立 reviewer 覆盖更新 `REVIEW_FINDINGS.json`。
- `DEGRADED`：停止当前阶段，说明独立审查未成立；不要把 DEGRADED 当作可修复代码问题自动处理。

FAIL 修复规则：

- 只修复 `REVIEW_FINDINGS.json` 中使 verdict 变为 FAIL 的 blockers/high severity 项，或人类报告中明确标为 must fix 的同源问题。
- 不要替 reviewer 改写 `REVIEW_FINDINGS.json`；修复后必须重新启动独立 reviewer 生成新版评估。
- 每轮修复后必须更新 `completion-proposal.json`，使 files_changed、behavior_changed、verification、known_limitations 与真实状态一致。
- 如果修复需要超出当前任务范围、缺少信息、工具不可用或存在人工决策点，停止并报告 blocker，不要伪造 PASS。

`REVIEW_FINDINGS.json` 是下游机器主入口，只放结构化评审 verdict 与发现项，不和 Markdown 做文本对账。顶层 `verdict` 必须是 `PASS` / `PASS_WITH_WARNINGS` / `FAIL` / `DEGRADED`；每条 finding 必须包含 `taskId`、`specRefs`、`evidenceIds`、`severity`、`message`，可带 `suggestedCheckpoint`。若 finding 指向 `UI_CONTEXT.json` 中的 UI task 或 UI scenario，必须同时投影 `uiRequired=true`、`pageRefs`、`interactionRefs`、`visualSourceRefs`；非 UI finding 不要伪造 UI refs：

写完 `REVIEW_FINDINGS.json` 后必须运行 `${pluginPath}/hooks/stage_gate.py validate --stage dev.review --feature "${feature}"`。writer 的本地 `validate` 只做结构检查，不能替代 stage gate。

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
      "severity": "high",
      "message": "Missing assertion for SCN-001",
      "uiRequired": true,
      "pageRefs": ["PAGE-001"],
      "interactionRefs": ["UIX-001"],
      "visualSourceRefs": ["VIS-001"],
      "suggestedCheckpoint": "code_in_progress"
    }
  ]
}
```

最终回复使用这个形状：

```
审查已完成。
Verdict: <PASS | PASS_WITH_WARNINGS | FAIL | DEGRADED>
交接文件:
- ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md
摘要: <一句话说明最终结果；如 PASS_WITH_WARNINGS，摘要 warnings>

<PASS/PASS_WITH_WARNINGS 时说明 review 已收敛；FAIL/DEGRADED 时说明为什么无法继续>
```

只有最终 verdict 是 `PASS` 或 `PASS_WITH_WARNINGS` 时，本 skill 才算完成。
**Skill 完成。**

提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。

## 参考文件

- 启动 reviewer agent 时，读取 references/reviewer-agent.md（reviewer 完整指令：只读命令清单、审查流程、必查维度与评分）。
- 写 completion-proposal.json 或读取 REQUIREMENTS_EVAL.md 时，读取 references/schemas.md（两份文件的字段规则与模板）。
