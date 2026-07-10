---
name: autodev-reviewer
description: "默认通过独立 reviewer 子代理执行"
version: v1.2.1702
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-reviewer --feature "${feature}" --plain
```


# Completion Reviewer

使用此技能来避免执行者自证完成。主 agent 负责写完成声明、按失败审查结论修复问题并重新发起审查；独立 reviewer 只负责用真实仓库状态核验声明并落盘需求评估。对于跨仓库任务，当前 workspace 是协调仓库，业务仓库由 proposal 中的 `affected_repositories` 显式列出；reviewer 不依赖隐式对话记忆。

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入和分支判断直接取用 `CHECKPOINT`。

开始审查前，使用统一脚本写入 `requirements_eval_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint requirements_eval_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

最终 verdict 为 `PASS` 或 `PASS_WITH_WARNINGS` 后，使用统一脚本写入 `requirements_eval_done`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint requirements_eval_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```


## 核心协议

1. **主 agent 写 completion proposal。**按 references/schemas.md 创建 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`。proposal 应描述任务、规格输入、受影响仓库、改动、声称的验证、已知限制和未完成事项。跨仓库任务必须写 `affected_repositories`；单仓库任务可以省略该字段。
2. **主 agent 启动独立 reviewer agent。**使用 subagent 机制启动独立 reviewer。启动子代理，并把 references/reviewer-agent.md 中的 reviewer 指令作为 prompt。启动子agent附带用户提供的原始 PRD 路径列表；没有则写 none，供 reviewer 与 proposal.prd_references 交叉核对。如果流程希望 reviewer 核对用户主动输入的仓库是否被遗漏，启动 prompt 还必须附带 `User repository references`；否则 reviewer 只以 completion proposal、proposal.md、specs、design、PLAN、可选 PRD 和真实仓库状态为依据。
3. **reviewer 自己获取真实状态。**reviewer 必须自行通过工具获取仓库状态，并读取 feature 目录中的 proposal.md、specs/**/*.md，以及 design.md、PLAN.md（如果存在）；PRD 只在用户或 completion proposal 显式引用时读取。若 completion proposal 有 `affected_repositories`，reviewer 必须对每个仓库逐个执行 git status/diff/log 等只读检查；若没有，则按旧流程把当前 cwd 当作唯一仓库。不要依赖主 agent 预先生成的 diff snapshot 或规格摘要。
4. **reviewer 直接写需求评估文件。**reviewer 必须直接写入 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md`。
5. **主 agent 读取 verdict 并分支。**如果 verdict 是 `PASS` 或 `PASS_WITH_WARNINGS`，报告 verdict 与 `REQUIREMENTS_EVAL.md` 路径后结束本阶段。如果 verdict 是 `FAIL`，主 agent 必须按 `REQUIREMENTS_EVAL.md` 中的 blockers 做最小修复，更新 `completion-proposal.json`，重新启动独立 reviewer，直到 verdict 变为 `PASS` 或 `PASS_WITH_WARNINGS`。如果 verdict 是 `DEGRADED`，停止并报告独立审查未成立。

## 严格职责边界

| 角色                | 职责                                                         | 禁止                                                         |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 主 agent / Executor | 写 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`；启动 reviewer；FAIL 时修复 blockers 并重新 review；最后摘要 verdict | 自己给 PASS；替 reviewer 改评估；未经重新 review 就宣称完成 |
| Reviewer agent      | 通过 shell/git/read/search 独立核验 proposal；写 REQUIREMENTS_EVAL.md | 修改源码、测试、配置、依赖文件、锁文件；运行 commit/push/deploy；修复问题 |

reviewer 可以使用 Write，但只允许写协调仓库中的：
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

reviewer 没有隐式用户对话上下文。所有可审查上下文必须来自 completion proposal、proposal.md、specs、design、PLAN、可选 PRD、启动 prompt 或真实 repo 状态。reviewer 只能核验 completion proposal 中已经记录的用户输入来源是否自洽；如果要检查用户主动输入是否被 proposal 遗漏，启动 reviewer 时必须额外传入 `User repository references`。

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

每轮 reviewer 写完 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` 后，主 agent 必须读取其中的 verdict：

- `PASS`：结束 review 阶段。
- `PASS_WITH_WARNINGS`：结束 review 阶段，但最终回复必须摘要 warnings。
- `FAIL`：不得进入下一阶段。主 agent 必须读取 blockers，修复必须修复项，更新 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`，然后重新启动独立 reviewer 覆盖更新 `REQUIREMENTS_EVAL.md`。
- `DEGRADED`：停止当前阶段，说明独立审查未成立；不要把 DEGRADED 当作可修复代码问题自动处理。

FAIL 修复规则：

- 只修复 `REQUIREMENTS_EVAL.md` 中使 verdict 变为 FAIL 的 blockers 或明确 must fix 项。
- 不要替 reviewer 改写 `REQUIREMENTS_EVAL.md`；修复后必须重新启动独立 reviewer 生成新版评估。
- 每轮修复后必须更新 `completion-proposal.json`，使 files_changed、behavior_changed、verification、known_limitations 与真实状态一致。
- 如果修复需要超出当前任务范围、缺少信息、工具不可用或存在人工决策点，停止并报告 blocker，不要伪造 PASS。

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

## Reviewer 必查项

要求 reviewer 核对：

1. **声明准确性**：proposal 中的 affected_repositories、files_changed、summary、behavior_changed 是否匹配每个仓库的真实 git status 和 git diff。
2. **证据可信度**：proposal 中声称的测试、lint、build、手工验证是否有足够证据；证据不足要降分或给 warning。
3. **规格对齐度**：真实 diff 和 completion proposal 是否满足 proposal.md、specs/**/*.md 中的能力边界、Requirement / Scenario、约束和非目标；如用户提供 PRD，再检查 PRD 与 specs 的上游一致性。
4. **代码现实**：每个仓库的真实 diff 是否实现了 completion proposal 声称的行为；如有 design.md，是否与其接口/数据/技术决策一致。
5. **风险诚实度**：known_limitations 是否遗漏了 diff、specs、design 或可选 PRD 中可见的明显风险。
6. **一致性**：API、routes、config、types、tests、docs 是否在单仓库内及跨仓库之间同步。
7. **未完成痕迹**：是否存在未解释的 TODO/FIXME/HACK、stub、mock、dead code、disabled tests 或 silent failures。

## Verdict 处理

- PASS：准许评估完成，进入下游。
- PASS_WITH_WARNINGS：准许评估完成，进入下游，但必须附带 warnings 和 `REQUIREMENTS_EVAL.md` 路径。
- FAIL：不准进入下游；修复 blockers 后重新写 proposal 并重新 review。
- DEGRADED：独立 review 未成立。不要宣称完成，说明缺失的能力或证据，然后停止等待用户。

只有 `PASS` / `PASS_WITH_WARNINGS` 是 review 收敛态。`FAIL` 是继续修复与复审的中间态，`DEGRADED` 是无法继续自动收敛的阻断态。

## 输出约定

reviewer 必须按 references/schemas.md 直接写入：

- ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md

不要接受自由文本的 “looks good” 作为 review 结果。verdict 必须能追溯到 completion proposal、proposal.md、specs、design.md、可选 PRD、每个受影响仓库的 shell/git 输出和实际文件内容。

## 参考文件

- 创建或启动 reviewer agent 时，读取 references/reviewer-agent.md。
- 写 completion-proposal.json 或 REQUIREMENTS_EVAL.md 时，读取 references/schemas.md。
