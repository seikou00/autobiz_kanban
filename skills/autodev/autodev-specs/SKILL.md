---
name: autodev-specs
description: Dev 阶段行为规格生成。按上游产物契约（Source Bundle）读取输入（缺上游 PRD 时基于用户描述澄清），探索需求、现有代码和隐性约束，与用户确认行为/API/数据边界后生成 proposal.md 与 specs/**/*.md；不得生成技术设计、任务计划或修改业务代码。
version: v1.1.1604
---


<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-specs --feature "${feature}" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）。
- **方法优先**：每个 input 的 `extract.method` 是它在场时的专属指令，优先于技能正文的通用默认。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止。
- **不列即不存在**：bundle 未列出的 id 不属于本 workflow 的正式流程产物 input，不要把它当作上游阶段产物读取、等待或索要。
- **适用边界**：上一条只约束正式流程产物 input；不限制用户本轮直接提供的材料、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 或技能正文明确要求读取的辅助素材。
- **降级语义**：`required: false` 的输入缺失时按其 `extract.degrade` 继续，不要因缺失而停止。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-specs — Proposal + Behavior Specs

## 阶段定位

`autodev-specs` 是 Dev 阶段的第一个上下文边界，负责把上游需求输入转成稳定的行为契约。

本阶段只回答：

- **为什么做**：沉淀到 `proposal.md`
- **系统应该表现为什么行为**：沉淀到 `specs/**/*.md`

本阶段不回答：

- **怎么实现 / 怎么拆编码任务**：交给后续设计与计划阶段
- **怎么改代码**：交给后续编码阶段

## 输入与输出

读取输入（消费 Source Bundle）：

- 按「流程契约」一节取本 Feature 的契约，读取 `sourceBundle` 列出的上游产物原件，按对应methodBundle `extract` 抽取重点；契约未列出的上游产物不读不等，按其 `degrade` 处理（如基于用户直供需求澄清行为）。
- 用户补充说明(如有)
- AGENTS.md 与项目约束
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

进入探索模式。先把需求、现状、隐性约束和行为边界想清楚，再生成 specs。

> 进入探索前先使用write_todos工具建立一份覆盖宏观流程的任务清单：`探索澄清行为/接口/数据边界` / `生成 proposal.md` / `生成 specs/**/*.md` / `推进 specs_done`，并随阶段推进实时更新状态（待做 / 进行中 / 完成）。

探索时必须：

- 从上游需求输入提取目标、用户角色、主流程、验收标准、非目标。
- 阅读现有代码和 AGENTS.md，识别已有接口、数据模型、权限、租户、审计、错误体、分页、状态流、配置和测试风格。
- 将上游需求改写为外部可观察行为，不要把实现猜测写成需求。
- 识别 capabilities：一组可以独立命名、独立验收的能力边界，例如 `order-export`、`approval-reminder`。
- 如果 API 或数据边界会影响行为契约，必须先与用户讨论。不要带着关键待确认项生成 specs。

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先讨论。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据语义还不准确，先讨论。
- 讨论时只提出影响实现路径或验收结果的关键问题，并给出当前建议、备选方案和影响面；不要机械问卷。
- 仍有 `待确认` 且会影响接口形态、数据模型、权限/租户/审计、幂等、分页、异步、状态流、迁移或验收结果时，不要结束探索进入 specs 生成。
- 待确认决策逐项与用户对齐后，是否结束探索进入 specs 生成必须由用户拍板：若当前运行模式支持 `request_user_input`，必须优先用它发起选择，选项至少包含 `这些决策已确认、生成 specs (Recommended)` / `继续讨论待确认项`；若不支持，必须显式追问：`以上行为/API/数据决策是否已确认？确认后我才生成 specs。请回复"生成 specs"或"继续讨论"。` 仍有影响行为契约的待确认项时，不得进入 specs 生成。

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

必须包含：

- **Why**：为什么要做。
- **What Changes**：用户可见或系统外部可观察变化。
- **Capabilities**：列出将生成或修改的能力，名称使用 kebab-case；每个 capability 必须对应一个 `specs/<capability>/spec.md`。
- **Impact**：影响模块、接口、数据、权限、配置、测试或运维。
- **Out of Scope**：本轮明确不做的内容。

## 生成 specs/**/*.md

按 `${pluginPath}/skills/autodev/autodev-specs/templates/spec.md` 输出。

规则：

- 每个 capability 一个 spec 文件：`specs/<capability>/spec.md`。
- specs 定义 **WHAT**，不得写实现步骤、类名、SQL 细节或任务拆分。
- Requirement 使用 `### Requirement [REQ-001]: <name>`。
- Scenario 使用四级标题 `#### Scenario [SCN-001]: <name>`。
- 每个 Requirement 至少一个 Scenario。
- 使用 SHALL/MUST 表达可验证行为。
- 修改已有行为时，写完整的新行为，不要只写差异片段。
- 对未确认且影响行为的内容，必须回到用户确认；不要把猜测写进 specs。

## 完成条件

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md` 已生成。
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/` 下至少存在一个 `spec.md`。
- proposal 的每个 capability 都有对应 spec 文件。
- 每个 spec 至少包含一个 Requirement 和一个 Scenario。
- specs 只描述行为契约，不包含实现任务。

完成后推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。** 下一步以当前 Feature 的工作流为准：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py"
```
