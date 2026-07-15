---
name: autodev-reviewer
description: 对单个 Feature 的完成声明做独立双轴评审。Dev 实现完成后使用：用代码修改前的逐仓库 Git 基线固定审查范围，分别核验 Standards 与 Spec，落盘 REQUIREMENTS_EVAL.md，并按 verdict 走修复复审闭环。
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-reviewer --feature "${feature}" --plain
```


# Completion Reviewer

使用此技能来避免执行者自证完成。主 agent 负责写完成声明、按失败审查结论修复问题并重新发起审查；独立 reviewer coordinator 用固定 Git scope 和真实仓库状态分别完成 Standards 与 Spec 评审并落盘需求评估。

reviewer 没有隐式用户对话上下文。所有可审查上下文必须来自 completion proposal、feature 目录产物（proposal.md、specs/**/*.md、design.md、PLAN.md）、可选 PRD、启动 prompt 和真实 repo 状态。跨仓库任务中，当前 workspace 是协调仓库，业务仓库由 proposal 的 `affected_repositories` 显式列出。

## 严格职责边界

| 角色                | 职责                                                         | 禁止                                                         |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 主 agent / Executor | 写 completion-proposal.json；启动 reviewer；FAIL 时修复 blockers 并重新 review；最后摘要 verdict | 在同一回合同时执行 reviewer 与 executor 角色；替 reviewer 改评估；未经重新 review 就宣称完成 |
| Reviewer coordinator | 预检 scope，组织 Standards / Spec 双轴核验并聚合；只允许写 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/REQUIREMENTS_EVAL.md` | 修改被审查对象；让轴 reviewer 写文件；跨轴抵消或重排 finding；修复问题 |
| Axis reviewer（可选） | 在并行 topology 中只返回 Standards 或 Spec finding | 写文件；查看另一轴结论；执行另一轴职责；修复问题 |

reviewer 的只读边界、scope preflight、双轴流程和 Verdict 矩阵在 references/reviewer-agent.md 与 references/schemas.md。如果 reviewer 无法可靠固定 Feature 范围、获取真实状态、访问 required 仓库或写报告，本次 review 不成立，Verdict 记 `DEGRADED`。平台禁用 task 工具时允许主 agent 内联执行 reviewer 角色，但必须显式记录 `inline_main_agent` 模式并通过用户确认把 reviewer 与 executor 分隔到不同回合；不得把该模式包装成独立 review。

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

按 references/schemas.md 创建 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/completion-proposal.json`，描述任务、规格输入、审查范围、受影响仓库、改动、声称的验证、已知限制和未完成事项。三个输入入口：

- **审查范围**：读取代码阶段在任何业务修改前生成的 `review-baseline.json`。逐仓库把 base SHA 和 canonical path 写入 `review_scope`，并现场读取 `head_sha_at_proposal`；三个 include flag 都写 true。不得覆盖或重新捕获 baseline。旧 Feature 缺失 baseline 时只能写 `strategy: "legacy_scope"` 并在 known limitations 披露，不能伪造 SHA。
- **PRD（可选，不是前置条件）**：用户提供 PRD 路径时（如"参考 .autobizdevops/features/feat-demo/PRD.md 做完成审查"），把路径原样写入 `prd_references`，支持多个；没有则写空数组。不要用自己总结的 PRD 内容替代文件路径，也不要提前判断实现是否满足 PRD——PRD 验收由 reviewer 独立完成。
- **跨仓库**：跨仓库任务必须写 `affected_repositories`，且 `files_changed` 和 `review_scope.repositories` 每项带 `repository_id`；单仓库任务可省略 affected_repositories。用户主动输入的仓库必须以 `source: "user_input"` 记录并转写依据到 `source_evidence`。

### 3. 启动 reviewer 角色

先检查当前平台是否提供 task 工具，然后二选一执行：

- **`independent_task`**：task 工具可用时，启动一个独立 reviewer coordinator。coordinator 能继续启动只读子代理时使用 `dual_axis_parallel`，否则使用 `dual_axis_single_reviewer`；两种 topology 都由 coordinator 独立写报告。reviewer 返回后，主 agent 在同一回合继续执行第 4 步。
- **`inline_main_agent`**：task 工具不可用时，主 agent 切换为 source-read-only reviewer coordinator，使用 `dual_axis_single_reviewer`。`REQUIREMENTS_EVAL.md` 落盘后必须停止当前回合，明确告知用户本次为主 agent 内联 review，并请用户确认是否切回 executor。未获得确认前，不得读取 verdict 分支、修复或推进 checkpoint。

把 references/reviewer-agent.md 中的指令作为 reviewer prompt，并附带：

- `Review execution mode:` `independent_task` 或 `inline_main_agent`。
- `Requested review topology:` independent_task 使用 `dual_axis_parallel_if_available`；inline 使用 `dual_axis_single_reviewer`。
- `User PRD references:` 用户提供的原始 PRD 路径列表；没有则写 none。
- `User repository references:`（可选）仅当流程需要 reviewer 核对用户主动输入的仓库是否被 proposal 遗漏时附带；否则省略，reviewer 只以 proposal 和真实仓库状态为依据。

reviewer coordinator 自己核验 baseline、获取真实仓库状态并写 `REQUIREMENTS_EVAL.md`；不要替它预生成 diff snapshot、规范清单或规格摘要。并行 topology 中，轴 reviewer 只返回 finding，不能写文件；coordinator 不得跨轴 rerank。

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
- 修复不改变 `review-baseline.json`；它始终指向本 Feature 开始修改前的固定点。每轮 proposal 更新当前 HEAD 后重新 review。

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

- 启动 reviewer agent 时，读取 references/reviewer-agent.md（reviewer 完整指令：scope preflight、只读命令、双轴流程与 Verdict 规则）。
- 写 completion-proposal.json、读取 review-baseline.json 或解析 REQUIREMENTS_EVAL.md 时，读取 references/schemas.md。
- 执行 Standards 轴时读取 references/standards-baseline.md。
- reviewer coordinator 使用并行双轴 topology 时读取 references/axis-reviewers.md。
