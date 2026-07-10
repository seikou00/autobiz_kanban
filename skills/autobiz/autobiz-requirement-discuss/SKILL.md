---
name: autobiz-requirement-discuss
description: Biz 阶段需求澄清技能。
version: v1.2.1701
---

# /autobiz-requirement-discuss — Biz 阶段需求澄清技能

> 本技能专注需求澄清与讨论收敛，不直接输出正式 PRD。

## 概述

本技能用于引导产品经理完善需求文档，通过分析原始材料、输出问题清单、与 PM 逐轮确认，将讨论结论稳定沉淀到 `PRD_DISCUSS.md`。

## 产物协议

- 产物：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD_DISCUSS.md`
- `PRD_DISCUSS.md` 用于承接循环中的讨论结论、待确认项、假设与阶段性方案
- 除非用户明确要求只停在讨论阶段，否则本技能应在收敛后结束。

## 核心能力

- 需求文档分析：按照评估准则检查需求，识别缺失、冲突和模糊点
- 问题清单生成：将问题按优先级整理成结构化清单
- 对话式引导：通过逐轮问答收集补充信息并确认关键决策
- 详细需求提取：从原始需求文档提取每个任务的**业务逻辑、字段定义、筛选条件、状态流转、验收标准等信息**，形成可开发的完整描述
- 讨论稿沉淀：结合需求描述和用户讨论，并回检优化，把循环过程稳定写入 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD_DISCUSS.md`

## 准备工作

### 获取feature状态
```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续需要当前 checkpoint 时直接取用 `CHECKPOINT`。若脚本提示 Feature 不存在，本技能允许通过下面的 `update_checkpoint.py --allow-create` 创建；创建或推进 checkpoint 后，必须再次调用 `read_state_json.py` 刷新 `CHECKPOINT`。

### 加载参考文档

在开始工作流程前，必须加载以下参考文档：

 `{pluginPath}/skills/autobiz/autobiz-requirement-discuss/references/analysis-guide.md`
   - 需求内容评估准则，包含检查项和优化建议

执行流程时，必须以评估准则作为判断依据，确保分析有据可依。

###  更新状态

开始需求澄清时必须用脚本写入开始态：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint discuss_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```


## 工作流程

> **流程管控**：执行开始时，必须使用 `write_todos` 创建以下固定 todo 列表（逐项创建，不得自行修改变更），每完成一步立即标记完成，确保不跳过任何环节：
>
> 1. 建立需求上下文（读取原始材料）
> 2. 创建 prd_original 文件夹并保存原始需求文档
> 3. 按 prd-formatter.md 模板改造需求文档并写入 PRD_DISCUSS.md
> 4. 需求分析 — 角色选择与通用基础检查
> 5. 需求分析 — 角色专项检查
> 6. 询问用户提供 HTML 文件位置并逐一分析
> 7. 需求分析 — 输出规范检查
> 8. 问题清单展示与用户确认
> 9. 对话式引导与 PRD_DISCUSS.md 调整
> 10. 迭代检查（对照 analysis-guide 回检）
> 11. 更新状态与校验



**确定 FEATURE_DIR：**

```
FEATURE_DIR = ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}
```
## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autobiz-requirement-discuss --feature "${feature}" --plain
```

### 缓存检测与清理

在开始分析前，检测用户是否要求重新讨论：
- 若用户明确提到"重新 DISCUSS"、"重新讨论"、"重新分析"、"重新梳理需求"等关键词
- 且 `{FEATURE_DIR}/PRD_DISCUSS.md` 已存在

则执行缓存清理：
1. 删除 `{FEATURE_DIR}/PRD_DISCUSS.md`
2. 清理问题清单缓存
3. 重新执行完整 DISCUSS 流程

### 建立需求上下文

1. 读取产品经理上传的需求材料
    - 优先读取 Word 文档（`.docx` / `.doc`）
    - 若用户提供 Markdown、需求说明、会议纪要、飞书导出内容，也可作为输入
2. **动态提取原始文档的所有章节内容**：按原始文档的实际目录结构逐章逐节提取，不预设固定的信息类别
    - 识别并记录原始文档的完整章节树（章节编号、标题层级、子章节关系）
    - 提取每个章节下的具体内容，完整详细
3. 记录原始文档的目录结构、编号规范、术语和文风特点
4. **创建 prd_original 文件夹并保存原始需求文档**：
    - 在 `{FEATURE_DIR}` 下创建 `prd_original` 文件夹（如已存在则跳过）
    - 将读取到的原始需求文档文件直接复制到 `{FEATURE_DIR}/prd_original/` 中
    - 此操作用于保留原始需求快照，供后续验证使用

Expected output: 已完成原始需求材料读取和复制保存，形成文档结构记录，为后续格式化与分析提供上下文。

### 需求内容格式改造

读取原始需求文档，按照 `{pluginPath}/skills/autobiz/autobiz-requirement-discuss/references/prd_module.md` 模板格式重写需求文档，将改造后的内容写入 `{FEATURE_DIR}/PRD_DISCUSS.md`。

详细格式化流程请参考 `{pluginPath}/skills/autobiz/autobiz-requirement-discuss/references/prd-formatter.md` 执行。

### 需求分析

【核心原则】严格按照 `{pluginPath}/skills/autobiz/autobiz-requirement-discuss/references/analysis-guide.md` 的评估规则检查，生成需求分析的问题清单。

【关键约束】 - 仅输出有问题、需求有遗漏、需求不明确的事项；无问题则不制造问题

### 问题清单展示与用户确认

####  问题清单展示

将发现的问题整理成结构化问题清单并按重要性分类，然后展示给用户。

| 优先级 | 分类 | 说明 |
|--------|------|------|
| P0 | 阻断性问题 | 需求无法理解或无法开发，必须立即解决 |
| P1 | 重要问题 | 功能实现可能受影响，需要 PM 确认 |
| P2 | 优化建议 | 文档完整性或规范性问题，建议改进 |

输出格式：

```markdown
## 问题清单

| 序号 | 重要性 | 检查项 | 问题描述 | 功能定位       | 优化建议 |
|------|--------|--------|----------|----------|----------|
| 1 | P0 - 阻断性问题 | [检查项] | [问题描述] | [功能定位] | [优化建议] |
| 2 | P1 - 重要问题 | [检查项] | [问题描述]| [功能定位]  | [优化建议] |
| 3 | P2 - 优化建议 | [检查项] | [问题描述] | [功能定位] | [优化建议] |
```

#### 询问用户是否需要补充

展示问题清单后，使用 `request_user_input` 询问用户：

问题清单已生成，请确认是否需要补充其他问题？
- **选项1**：确认讨论当前问题清单
- **选项2**：补充其他问题

**处理逻辑**：
- 若用户选择「确认讨论当前问题清单」→ 直接进入 Step 5 逐项确认
- 若选择「补充其他问题」或者「其他」→ 引导用户补充说明 → 记录补充内容，合并到问题清单再进入 Step 5

【关键约束 - 必须先展示问题清单后并等待确认】

**禁止假设用户确认需求没有问题**： 需求已经很清楚所以跳过问题确认是**错误推理**。即使没有问题清单，也必须告知用户"需求检查完毕，未发现问题，是否确认进入下一阶段？"


### 对话式引导并调整 `PRD_DISCUSS.md`

【关键方法】这一阶段是在完成问题清单确认后，基于所有的问题（生成和补充）逐项进行深度对话，结合原始需求文档和用户回复，把需求内容调整结果稳定沉淀到 `{FEATURE_DIR}/PRD_DISCUSS.md`。
#### 深度对话引导策略

基于『问题清单展示与用户确认』已确认的问题清单，按优先级（P0→P1→P2）逐项进行单独对话确认。

**对话流程（每个问题单独执行）：**

1. **展示当前问题**：展示问题的检查项、问题描述和优化建议
2. **智能生成选项**：根据问题类型生成 1-2 个选项
    - P0 问题示例选项：「已明确」「暂时搁置」
    - P1 问题示例选项：「确认按建议处理」「后续讨论」
    - P2 问题示例选项：「确认按建议处理」「本期不做」
3. **使用 request_user_input 询问**：
    - 问题：描述当前问题，询问 PM 的处理意向
    - 选项：智能生成的选项
4. **处理用户回复**：
    - 若选择预设选项 → 记录选择结果，继续下一问题
    - 若选择「其他」→ 引导用户补充说明 → 记录补充内容，继续下一问题
5. **记录完整对话**：将每个问题的对话内容（问题、选项、用户选择、补充内容）记录到 PRD_DISCUSS.md

**示例对话流程：**

```
问题 1：P0 - 字段定义缺失
- 检查项：字段定义完整性
- 问题描述：用户ID字段未定义类型和长度
- 优化建议：补充 userId 字段的类型为 VARCHAR(32)

【询问】
问题：用户ID字段未明确定义，请问如何处理？
选项：
1. 补充字段定义为 VARCHAR(32)（推荐）
2. 后续讨论
3. 其他

【用户选择后记录】
- 用户选择：补充字段定义为 VARCHAR(32)
- 补充说明：[如有]
- 确认结论：[记录最终决策]
```

#### 调整需求摘要
- 根据用户回答调整需求摘要，确保需求摘要内容的准确性
- 不做的任务在任务标题上明确标注"（二期）"或"本期不做"
- 对于需求摘要内容中描述不明确的内容，应在对应位置标注"【待确认】"并在"待确认事项"中记录


#### 讨论沉淀生成

`PRD_DISCUSS.md` 是固定文件名，每轮增量更新。必须包含：

1. **需求摘要【核心】**：即完整需求内容
2. **当前已确认结论**：本轮讨论后已确认的功能范围、审批流等结论
3. **问题清单与处理状态**：P0/P1/P2 问题及处理状态
4. **待确认事项**：待开发确认的高保真链接、接口文档、数据同步机制等
5. **假设与风险**：基于什么假设、存在哪些风险
6. **历次讨论记录**：按时间记录讨论过程和结论

#### 写作要求

- 讨论稿可以保留"待确认""候选方案""暂定结论"这类中间状态
- 每次新增或修改都要明确哪些内容是已确认，哪些仍待确认
- 讨论稿不要求完全标准化，但必须保证信息可追溯、语义稳定、便于后续提炼
- 若用户指定"只先讨论，不输出正式 PRD"，可以停留在本文件；否则提示用户运行 `/autobiz-prd-generate`

Expected output: `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD_DISCUSS.md` 已沉淀当前轮次的需求结论、待确认项和风险。

### 迭代直到收敛

将 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD_DISCUSS.md` 与 `analysis-guide.md` 反复对照检查：

1. 检查原问题是否已解决
2. 检查是否引入新问题或新歧义
3. 若仍存在 P0 / P1，继续回到『需求分析』至『对话式引导并沉淀 PRD_DISCUSS.md』环节
4. 每轮都要向用户展示检查结果，由用户判断是否可以终止循环

#### 迭代终止条件

满足以下任一条件可视为需求已收敛：

- 用户明确表示"无问题""可以了""通过""开始整理正式 PRD"
- 用户表示"不需要""不考虑"等问题暂存的相似语句
- 所有 P0 / P1 已处理完毕，只剩可接受的 P2 建议
- 连续两次检查没有新增实质问题

### 更新状态

使用统一脚本将当前 Feature 推进到 `discuss_done`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint discuss_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```


## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python "${pluginPath}/skills/autobiz/hooks/biz_validate.py" discuss --feature "${feature}"
```

脚本通过即视为以下清单已完成：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD_DISCUSS.md` — 已存在，且保留了完整收敛过程
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD_DISCUSS.md` — 包含需求摘要、已确认结论、问题清单与处理状态、待确认事项、假设与风险
- `.autobizdevops/state.json` — Feature checkpoint 为 `discuss_done`
- 所有 P0 / P1 问题已处理完毕（或已和用户确认接受风险）

**Skill 完成。** 提醒用户：请回到特性面板新开新对话。

## 技能使用约束

1. 本技能专注需求文档完善优化，不涉及代码实现检查
2. 分析和输出必须严格参照参考文档
3. 对话式引导中保持专业、友好的沟通态度
