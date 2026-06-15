---
name: autodev-detail-design
description: Autodev dynamic detailed design node. When the `detail_design_before_code` workflow decision is enabled at `plan_done`, use after /autodev-plan and before /autodev-code to write `DETAIL_DESIGN.md` with file-level change design, implementation logic, and overall flow. This skill updates detail_design checkpoints but must not modify business code.
version: v1.1.1604
---
# /autodev-detail-design - 详细计划

## 阶段定位

autodev-detail-design 是 `detail_design_before_code` dynamic stage 启用后的正式 Dev 节点，通常在 /autodev-plan 已生成 `PLAN.md` 后、进入 /autodev-code 前调用。

本 skill 只回答：

- 本次实现预计新增、修改或删除哪些文件。
- 每个文件的改动逻辑是什么。
- 模块之间如何调用。
- 接口、数据、状态和异常流程如何串起来。
- 后续编码时应重点遵守哪些设计约束。

本 skill 不做：

- 不修改 board_core/board_config.json。
- 不修改业务代码、测试代码、配置、迁移脚本或已有阶段产物。
- 不重写 `proposal.md`、`specs/**/*.md`、`design.md`、`PLAN.md`。

输出产物：

- {FEATURE_DIR}/DETAIL_DESIGN.md

## 准入与上下文

确定 {FEATURE_ID} 后，读取当前 Feature 快照判断是否已进入本动态节点：

```
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

如当前 checkpoint 仍为 `plan_done` 且用户选择需要详细设计，先使用统一状态脚本启用 dynamic stage 并进入本节点：

```
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint detail_design_in_progress --workflow-decision detail_design_before_code=enabled
```

可查看动态节点契约：

```
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-detail-design --workflow-decision detail_design_before_code=enabled --json
```

读取输入（消费 Source Bundle）：

- 按契约 `sourceBundle` 读取上游产物原件（本节点为 proposal.md、specs/**/*.md、design.md、PLAN.md），按各自 `extract` 抽取重点。
- {CODE_WORKSPACE}/AGENTS.md（如存在）
- 与本 Feature 相关的现有业务代码、测试、配置和接口定义

`required_inputs` 中任一产物缺失时停止并提示先完成对应上游阶段（本节点仅在标准链启用 detail_design 决策后插入，design.md/PLAN.md 均为必需）；本 skill 不补写上游设计契约。

## 工作原则

- **扎根代码现实。** 文件清单必须来自实际代码探索、PLAN.md 任务、design.md 决策和现有项目结构，不要凭空发明路径。
- **比 PLAN 更具体，但仍不编码。** 可以写文件级改动说明、伪代码、流程图和调用链；不得直接改实现文件。
- **保留不确定性。** 无法确认的文件路径、接口字段、权限、数据模型、状态流必须标为待确认，不要写成硬结论。
- **面向读者。**DETAIL_DESIGN.md 是给用户和后续编码者读的，应清楚说明“为什么改这里、怎么改、怎么流转、怎么验证”。
- **按动态节点推进流程。** 完成后必须调用 update_checkpoint.py 推进到 `detail_design_done`；若用户不需要详细设计，应在 `plan_done` 选择 skip 并直接进入 code，而不是进入本 skill。

## 生成 DETAIL_DESIGN.md

写入 {FEATURE_DIR}/DETAIL_DESIGN.md，建议结构如下：

````
# 详细设计: [Feature 名称]

来源: proposal.md + specs/**/*.md + design.md + PLAN.md + 现有代码探索
状态: 可选设计产物
创建时间: [ISO 日期时间]

## 1. 设计目标

- **Feature:** {FEATURE_ID}
- **目标:** [本次改动要达成的结果]
- **规格依据:** [列出 specs 中的 Requirement / Scenario]
- **计划依据:** [列出 PLAN.md 中相关任务]

## 2. 整体实现流程

[用文字、ASCII 图或 mermaid 描述用户操作、入口、服务处理、数据读写、返回结果、异步/异常分支。]

## 3. 文件改动清单

| 类型 | 文件 | 改动说明 | 关联规格/设计/任务 | 状态 |
|------|------|----------|--------------------|------|
| 修改 | path/to/file | [改什么逻辑] | REQ- / API- / DATA- / D- / Task | 已确认/待确认 |
| 新增 | path/to/file | [新增职责] | REQ- / API- / DATA- / D- / Task | 已确认/待确认 |
| 删除 | path/to/file | [删除原因与兼容处理] | REQ- / API- / DATA- / D- / Task | 已确认/待确认 |

## 4. 详细逻辑设计

### 4.1 path/to/file

**当前逻辑:**
- [现有行为、关键函数、调用关系]

**目标逻辑:**
- [新增/修改的判断、分支、调用、返回结果]

**伪代码:**
```text
[伪代码]
```

**异常与边界:**

- [错误码、权限、租户、审计、幂等、兼容性、空值、并发等]

**验证点:**

- [单测/集成/E2E/手工验证建议]

## 5. 模块调用关系

[说明入口 -> service -> repository/client -> response 的调用链，或前端 -> API -> 后端 -> 数据的链路。]

## 6. 数据、状态与接口流转

- **接口流转:** [请求/响应/错误处理]
- **数据流转:** [读写模型、字段、迁移、回滚；无数据变更则说明无]
- **状态流转:** [状态机、枚举、缓存、异步任务；无状态变更则说明无]

## 7. 测试设计

| 场景       | 建议测试文件 | 验证点     | 覆盖规格/任务 |
| ---------- | ------------ | ---------- | ------------- |
| [主流程]   | path/to/test | [预期结果] | REQ- / Task   |
| [异常流程] | path/to/test | [预期结果] | REQ- / Task   |

## 8. 风险与待确认

| ID    | 类型        | 描述   | 影响   | 下一步     |
| ----- | ----------- | ------ | ------ | ---------- |
| DD-01 | 待确认/风险 | [描述] | [影响] | [确认方式] |

````

## 完成条件

- `{FEATURE_DIR}/DETAIL_DESIGN.md` 已写入。
- 文件改动清单覆盖 `PLAN.md` 中所有待编码任务，或明确说明某任务无需文件改动。
- 每个文件级改动都能追溯到 specs、design 或 PLAN。
- 仍不确定的路径、字段、接口、权限、数据或状态流已标为待确认。
- 已调用 `python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint detail_design_done`，且未修改业务代码。

**Skill 完成。** 下一步通常是继续 `/autodev-code`。

$ARGUMENTS
