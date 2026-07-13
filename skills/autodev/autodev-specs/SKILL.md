---
name: autodev-specs
description: Dev 阶段行为规格生成。
version: v1.2.1701
---

## 缺失产物处理
```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-specs --feature "${feature}" --plain
```


# /autodev-specs — Proposal + Behavior Specs

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## 阶段定位

`autodev-specs` 是 Dev 阶段的上下文边界，负责把上游需求输入转成稳定的行为契约。

本阶段只回答：

- **为什么做**：沉淀到 `proposal.md`
- **系统应该表现为什么行为**：沉淀到 `specs/**/*.md`

本阶段不回答：

- **怎么实现 / 怎么拆编码任务**：交给后续设计与计划阶段
- **怎么改代码**：交给后续编码阶段

## 输入与输出

读取输入:
- 与当前 feature 相关的现有代码、接口、数据模型、测试、配置

输出产物：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/<capability>/spec.md`

禁止写入：

- 业务代码、测试代码、配置、迁移脚本
- `design.md`
- `PLAN.md`
- 后续阶段报告

## 写入 checkpoint

开始生成规格前推进到 `specs_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## Explore 协议

进入探索模式，严禁使用task工具。先把需求、现状、隐性约束和行为边界想清楚，再生成 specs。

> 进入探索前先使用write_todos工具建立一份覆盖宏观流程的任务清单：`探索澄清行为/接口/数据边界` / `生成 proposal.md` / `生成 specs/**/*.md` / `推进 specs_done`，并随阶段推进实时更新状态（待做 / 进行中 / 完成）。

探索时必须：

- 从上游需求输入提取目标、用户角色、主流程、验收标准、非目标。
- 阅读现有代码，识别已有接口、数据模型、权限、租户、审计、错误体、分页、状态流、配置和测试风格。
- 将上游需求改写为外部可观察行为，不要把实现猜测写成需求。
- 识别 capabilities：一组可以独立命名、独立验收的能力边界，例如 `order-export`、`approval-reminder`。
- 如果 API 或数据边界会影响行为契约，必须先与用户讨论。不要带着关键待确认项生成 specs。

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先讨论。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据语义还不准确，先讨论。
- 讨论时只提出影响实现路径或验收结果的关键问题，并给出当前建议、备选方案和影响面；不要机械问卷。
- 仍有 `待确认` 且会影响接口形态、数据模型、权限/租户/审计、幂等、分页、异步、状态流、迁移或验收结果时，不要结束探索进入 specs 生成。
- 待确认决策逐项与用户对齐后，是否结束探索进入 specs 生成必须由用户拍板：
- 按共享 `ask-user-question.md` 协议用 `request_user_input` 发起选择，选项为
  `这些决策已确认、生成 specs (Recommended)` / `继续讨论待确认项`；Other 由客户端自动提供；
- **自由表达即退出结构化**：若用户不点选项、而是直接给出实质回复（补一条决策、
  改一个字段、提新问题），当作普通文本吸收进待确认表并更新建议，**不得机械重复弹同一个
  结构化选择**；下一轮合适时机再重新发起该门。
- 仍有影响行为契约的待确认项时，不得进入 specs 生成。
讨论输出建议：

```markdown
## 行为/API/数据决策待确认

我不建议现在直接生成 specs，因为以下决策会影响行为契约或实现路径：

| ID | 类型 | 决策点 | 当前建议 | 备选方案 | 影响 | 需要确认 |
|----|------|--------|----------|----------|------|----------|
| SPEC-01 | Behavior | [行为边界] | [建议] | [备选] | [影响验收] | [问题] |
| API-001 | API | [接口入口/请求响应/错误码] | [建议] | [备选] | [影响任务/验收] | [问题] |
| DATA-001 | Data | [表/字段/状态/约束] | [建议] | [备选] | [影响任务/验收] | [问题] |
```

## 生成 proposal.md

按 `${pluginPath}/skills/autodev/autodev-specs/templates/proposal.md` 输出。

生成前先建立 capability 变更分类表，后续 `proposal.md` 与 `specs/**/*.md` 必须保持一致：

| Capability | Operation | 判定依据 | 既有行为来源 | 本轮目标 |
|------------|-----------|----------|--------------|----------|
| `[name]` | `ADDED/MODIFIED/REMOVED` | [为什么归入该类] | [现有 spec/代码/API/UI/无] | [目标行为] |

分类规则：

- `ADDED`：当前系统没有对应的外部可观察能力、入口、流程或业务结果；本轮新增一个可独立验收的行为边界。复用已有组件、接口或表，不影响 `ADDED` 判定。
- `MODIFIED`：已有能力仍然存在，但本轮改变或扩展其外部可观察行为，包括条件、输出、校验、权限、错误码、状态流、异步时机、数据口径、UI 状态或交互分支。给已有流程增加筛选项、字段、按钮、状态、限制条件或兼容逻辑，默认是 `MODIFIED`，不是 `ADDED`。
- `REMOVED`：已有能力、入口、分支或业务结果在本轮后不再支持、不可访问或不再生效；必须说明移除原因、迁移/兼容方式，以及旧入口被触发时的期望行为。
- 同一用户目标同时包含新增独立能力和修改既有能力时，拆成不同 capability 或同一 spec 内不同 Requirement，不得用一个分类吞掉全部变化。
- 无法判断是否已有行为时，先搜索既有 specs、代码入口、接口、菜单、配置和测试；仍不确定则回到用户确认，不要猜测分类。
- capability 分类小节为空时，该小节正文只写 `无`；不得保留 `[capability-name]` / `[existing-capability]` / `[removed-capability]` 占位项，也不得写 `- [capability]: 无`。
- 一旦列出 capability，说明必须写实质原因、范围或迁移方式，并且必须对应真实 `specs/<capability>/spec.md`。

必须包含：

- **Why**：为什么要做。
- **What Changes**：用户可见或系统外部可观察变化。
- **Capabilities**：按 `New Capabilities` / `Modified Capabilities` / `Removed Capabilities` 填入分类表中的 capability；名称使用 kebab-case；每个非“无”的 capability 必须对应一个 `specs/<capability>/spec.md`。
- **Impact**：影响模块、接口、数据、权限、配置、测试或运维。
- **Out of Scope**：本轮明确不做的内容。

## 生成 specs/**/*.md

按 `${pluginPath}/skills/autodev/autodev-specs/templates/spec.md` 输出。

规则：

- 每个 capability 一个 spec 文件：`specs/<capability>/spec.md`。
- **列出即生成（无裁量权）**：proposal `Capabilities` 三个小节（New/Modified/Removed）中列出的每一个 capability（占位"无"除外）都必须有对应的 `specs/<capability>/spec.md`。不得以等任何理由跳过——你没有这个裁量权。若认为某 capability 不值得单独成 spec，唯一合法做法是回到 proposal 将其移除或并入其他 capability，保持两边严格一致；禁止单方面少生成。
- Modified/Removed capability 的 spec 用 `## MODIFIED Requirements` / `## REMOVED Requirements` 承载完整新行为或移除说明，同样不可省略。
- specs 定义 **WHAT**，不得写实现步骤、类名、SQL 细节或任务拆分。
- Requirement 使用 `### Requirement: <name>`。
- Scenario 使用四级标题 `#### Scenario: <name>`。
- 每个 Requirement 至少一个 Scenario。
- 使用 SHALL/MUST 表达可验证行为。
- 每个 Requirement 只能放入一个操作段：`ADDED Requirements`、`MODIFIED Requirements` 或 `REMOVED Requirements`。
- `ADDED Requirements` 只写新增行为；如果只是已有行为增加条件、字段、状态或分支，放入 `MODIFIED Requirements`。
- `MODIFIED Requirements` 必须写修改后的完整行为，并在 Requirement 正文或 Scenario 中覆盖旧行为受影响的触发条件和新期望；不要只写“新增字段”“调整逻辑”这类差异片段。
- `REMOVED Requirements` 必须写旧能力的移除原因、迁移/兼容方式，并用 Scenario 描述旧入口、旧条件或旧分支被触发时系统应该如何响应。
- 某个操作段无内容时写“无”；不要保留模板占位 Requirement。
- 对未确认且影响行为的内容，必须回到用户确认；不要把猜测写进 specs。

## 完成条件

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md` 已生成。
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/` 下至少存在一个 `spec.md`。
- **数量核对**：数出 proposal `Capabilities` 实际列出的 capability 数 N（排除"无"），数出 `specs/*/spec.md` 数 M，必须 N == M 且逐一对应；推进 specs_done 前在回复中输出对照表 `capability → specs/<capability>/spec.md ✓`，缺任何一行不得推进。
- 每个 spec 至少包含一个 Requirement 和一个 Scenario。
- specs 只描述行为契约，不包含实现任务。

完成后推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
