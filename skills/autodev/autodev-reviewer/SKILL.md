---
name: autodev-reviewer
description: 对单个 feature 的完成声明做独立需求评审。Dev 实现完成后使用：主 agent 写 completion-proposal.json，启动 source-read-only 的独立 reviewer 子代理核验真实仓库状态，落盘 REQUIREMENTS_EVAL.md，并按 verdict 走修复复审闭环。
version: v1.3.0710
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
| 主 agent / Executor | 写 completion-proposal.json；启动 reviewer；FAIL 时修复 blockers 并重新 review；最后摘要 verdict | 自己给 PASS；替 reviewer 改评估；未经重新 review 就宣称完成 |
| Reviewer agent      | 通过 shell/git/read/search 独立核验 proposal；只允许写 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` | 修改源码、测试、配置、依赖、锁文件；运行任何写操作命令；修复问题 |

reviewer 的只读命令白名单、禁止清单、审查流程和评分标准全部在 references/reviewer-agent.md。如果 reviewer 无法用 shell/git 获取真实状态、无法访问 required 仓库或无法写报告文件，本次独立 review 不成立，verdict 记 `DEGRADED`；不得把自检包装成独立 review。

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

### 3. 启动独立 reviewer 子代理

使用 subagent 机制启动独立 reviewer，把 references/reviewer-agent.md 中的指令作为 prompt，并附带：

- `User PRD references:` 用户提供的原始 PRD 路径列表；没有则写 none。
- `User repository references:`（可选）仅当流程需要 reviewer 核对用户主动输入的仓库是否被 proposal 遗漏时附带；否则省略，reviewer 只以 proposal 和真实仓库状态为依据。

reviewer 自己通过工具获取真实仓库状态并直接写 `REQUIREMENTS_EVAL.md`；不要替它预生成 diff snapshot 或规格摘要。

### 4. 读取 verdict 并分支

reviewer 写完 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` 后，主 agent 读取其中的 verdict。不要接受自由文本的 "looks good" 作为 review 结果。

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
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint requirements_eval_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

### 6. 最终回复

使用这个形状：

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
