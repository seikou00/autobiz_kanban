---
name: autodev-specs
description: Dev 阶段行为规格生成。读取 PRD，探索需求、现有代码和隐性约束，与用户确认行为/API/数据边界后生成 proposal.md 与 specs/**/*.md；不得生成技术设计、任务计划或修改业务代码。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约

当前 skill 的 checkpoint、输入/输出产物和 validators 以 `{PLUGIN_DIR}/board_core/board_config.json` 为唯一事实来源。
运行前如需查看当前契约，执行：

```bash
python "{PLUGIN_DIR}/hooks/inspect_skill_contract.py" autodev-specs --json
```
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-specs — Proposal + Behavior Specs

## 阶段定位

`autodev-specs` 是 Dev 阶段的第一个上下文边界，负责把 `PRD.md` 转成稳定的行为契约。

本阶段只回答：

- **为什么做**：沉淀到 `proposal.md`
- **系统应该表现为什么行为**：沉淀到 `specs/**/*.md`

本阶段不回答：

- **怎么实现**：交给 `/autodev-plan` 的 `design.md`
- **怎么拆编码任务**：交给 `/autodev-plan` 的 `PLAN.md`
- **怎么改代码**：交给 `/autodev-code`

## 输入与输出

工作目录：

```text
.autobizdevops/features/{slug}/
```

必读输入：

- `{工作目录}/PRD.md`
- 用户补充说明
- AGENTS.md 与项目约束
- 与当前 feature 相关的现有代码、接口、数据模型、测试、配置

输出产物：

- `{工作目录}/proposal.md`
- `{工作目录}/specs/<capability>/spec.md`

禁止写入：

- 业务代码、测试代码、配置、迁移脚本
- `design.md`
- `PLAN.md`
- 后续阶段报告

## 写入 checkpoint

开始生成规格前推进到 `specs_in_progress`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint specs_in_progress
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}")
```

## Explore 协议

进入探索模式。先把需求、现状、隐性约束和行为边界想清楚，再生成 specs。

探索时必须：

- 从 PRD 提取目标、用户角色、主流程、验收标准、非目标。
- 阅读现有代码和 AGENTS.md，识别已有接口、数据模型、权限、租户、审计、错误体、分页、状态流、配置和测试风格。
- 将 PRD 改写为外部可观察行为，不要把实现猜测写成需求。
- 识别 capabilities：一组可以独立命名、独立验收的能力边界，例如 `order-export`、`approval-reminder`。
- 如果 API 或数据边界会影响行为契约，必须先与用户讨论。不要带着关键待确认项生成 specs。

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先讨论。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据语义还不准确，先讨论。
- 讨论时只提出影响实现路径或验收结果的关键问题，并给出当前建议、备选方案和影响面；不要机械问卷。
- 仍有 `待确认` 且会影响接口形态、数据模型、权限/租户/审计、幂等、分页、异步、状态流、迁移或验收结果时，不要结束探索进入 specs 生成。

讨论输出建议：

```markdown
## 行为/API/数据决策待确认

我不建议现在直接生成 specs，因为以下决策会影响行为契约或实现路径：

| ID | 类型 | 决策点 | 当前建议 | 备选方案 | 影响 | 需要确认 |
|----|------|--------|----------|----------|------|----------|
| SPEC-01 | Behavior | [行为边界] | [建议] | [备选] | [影响验收] | [问题] |
| API-01 | API | [接口入口/请求响应/错误码] | [建议] | [备选] | [影响任务/验收] | [问题] |
| DATA-01 | Data | [表/字段/状态/约束] | [建议] | [备选] | [影响任务/验收] | [问题] |
```

## 生成 proposal.md

按 `{PLUGIN_DIR}/skills/autodev/autodev-specs/templates/proposal.md` 输出。

必须包含：

- **Why**：为什么要做。
- **What Changes**：用户可见或系统外部可观察变化。
- **Capabilities**：列出将生成或修改的能力，名称使用 kebab-case；每个 capability 必须对应一个 `specs/<capability>/spec.md`。
- **Impact**：影响模块、接口、数据、权限、配置、测试或运维。
- **Out of Scope**：本轮明确不做的内容。

## 生成 specs/**/*.md

按 `{PLUGIN_DIR}/skills/autodev/autodev-specs/templates/spec.md` 输出。

规则：

- 每个 capability 一个 spec 文件：`specs/<capability>/spec.md`。
- specs 定义 **WHAT**，不得写实现步骤、类名、SQL 细节或任务拆分。
- Requirement 使用 `### Requirement: <name>`。
- Scenario 使用四级标题 `#### Scenario: <name>`。
- 每个 Requirement 至少一个 Scenario。
- 使用 SHALL/MUST 表达可验证行为。
- 修改已有行为时，写完整的新行为，不要只写差异片段。
- 对未确认且影响行为的内容，必须回到用户确认；不要把猜测写进 specs。

## 完成条件

- `{工作目录}/proposal.md` 已生成。
- `{工作目录}/specs/` 下至少存在一个 `spec.md`。
- proposal 的每个 capability 都有对应 spec 文件。
- 每个 spec 至少包含一个 Requirement 和一个 Scenario。
- specs 只描述行为契约，不包含实现任务。

完成后推进 checkpoint：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint specs_done
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}")
```

**Skill 完成。** 下一步：`/autodev-plan`
