---
name: autobiz-requirement-discuss
description: Biz 阶段需求澄清技能。读取原始需求材料，通过分析评估、问题清单、对话循环收敛需求，沉淀中间讨论稿 `{FEATURE_DIR}/PRD_DISCUSS.md`。适用于需求评审、需求完善、协助 PM 补齐信息。
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

# /autobiz-requirement-discuss — Biz 阶段需求澄清技能

> 本技能专注需求澄清与讨论收敛，不直接输出正式 PRD。
>
> 需求收敛后，下一步应运行 `/autobiz-prd-generate` 提炼生成正式 `PRD.md`。

## 概述

本技能用于引导产品经理完善需求文档，通过分析原始材料、输出问题清单、与 PM 逐轮确认，将讨论结论稳定沉淀到 `PRD_DISCUSS.md`。

## 产物协议

- 中间产物：`{FEATURE_DIR}/PRD_DISCUSS.md`
- `PRD_DISCUSS.md` 用于承接循环中的讨论结论、待确认项、假设与阶段性方案
- 除非用户明确要求只停在讨论阶段，否则本技能应在收敛后结束，并提示用户运行 `/autobiz-prd-generate` 生成正式 PRD

## 核心能力

- 需求文档分析：按照评估准则检查需求，识别缺失、冲突和模糊点
- 问题清单生成：将问题按优先级整理成结构化清单
- 对话式引导：通过逐轮问答收集补充信息并确认关键决策
- 详细需求提取：从原始需求文档提取每个任务的**业务逻辑、字段定义、筛选条件、状态流转、验收标准等信息**，形成可开发的完整描述
- 讨论稿沉淀：结合需求描述和用户讨论，并回检优化，把循环过程稳定写入 `{FEATURE_DIR}/PRD_DISCUSS.md`

## 触发条件

以下场景应自动触发本技能：

- 用户提到"完善需求文档""优化需求""引导完善需求"
- 用户提到"需求标准化""需求文档规范化"
- 用户提交需求文档后要求"引导补充""协助完善"
- 用户提到"帮我整理需求文档""把需求文档完善一下"
- 用户提到"按照标准格式整理需求"
- 用户提到"先讨论需求""需求澄清""对齐需求"

## 准备工作

### State 快照读取

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续需要当前 checkpoint 时直接取用 `CHECKPOINT`。若脚本提示 Feature 不存在，本技能允许通过下面的 `update_checkpoint.py --allow-create` 创建；创建或推进 checkpoint 后，必须再次调用 `read_state_json.py` 刷新 `CHECKPOINT`。

### 加载参考文档

在开始工作流程前，必须加载以下参考文档：

1. `references/analysis-guide.md`
   - 需求内容评估准则，包含检查项和优化建议
2. `references/default-rules.md`
   - 默认规则，生成问题清单时需豁免的默认项

执行流程时，必须以评估准则作为判断依据，确保分析有据可依。

###  更新状态

```
开始时: 通过统一脚本写入 checkpoint: discuss_in_progress
完成时: 通过统一脚本写入 checkpoint: discuss_done
```

开始需求澄清时必须用脚本写入开始态（允许新建 Feature 行）：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint discuss_in_progress --allow-create
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

## 工作流程

### Step 1: 建立需求上下文

1. 读取产品经理上传的需求材料
   - 优先读取 Word 文档（`.docx` / `.doc`）
   - 若用户提供 Markdown、需求说明、会议纪要、飞书导出内容，也可作为输入
2. 提取以下信息
   - 特性概述 / 背景 / 目标
   - 功能任务描述
   - 验收标准
   - 角色、流程、边界、外部依赖
3. 记录原始文档的结构、编号规范、术语和文风特点

**确定 FEATURE_DIR：**

```
FEATURE_DIR = {PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}
```

Expected output: 已完成原始需求材料读取，并形成后续分析所需上下文。

### Step 2: 需求分析

【核心原则】严格按照 `references/analysis-guide.md` 的评估规则检查，生成需求分析的问题清单。

【关键约束】 - 仅输出有问题、需求有遗漏、需求不明确的事项；无问题则不制造问题


### Step 3: 问题清单展示与用户确认

将 Step 2 发现的问题整理成结构化问题清单，并按重要性分类：

| 优先级 | 分类 | 说明 |
|--------|------|------|
| P0 | 阻断性问题 | 需求无法理解或无法开发，必须立即解决 |
| P1 | 重要问题 | 功能实现可能受影响，需要 PM 确认 |
| P2 | 优化建议 | 文档完整性或规范性问题，建议改进 |

输出格式：

```markdown
## 问题清单
| 序号 | 重要性 | 检查项 | 问题描述 | 优化建议 |
|------|--------|--------|----------|----------|
| 1 | P0 - 阻断性问题 | [检查项] | [问题描述] | [优化建议] |
| 2 | P1 - 重要问题 | [检查项] | [问题描述] | [优化建议] |
| 3 | P2 - 优化建议 | [检查项] | [问题描述] | [优化建议] |
```
【关键约束 - 必须展示并等待确认】

**禁止假设用户确认：无论需求文档多详细、用户意图多明确，都必须在展示问题清单后停止输出并等待用户回复。**

"需求已经很清楚所以跳过确认"是**错误推理**。即使没有问题清单，也必须告知用户"需求检查完毕，未发现问题，是否确认进入下一阶段？"


### Step 4: 对话式引导并沉淀 `PRD_DISCUSS.md`

【关键方法】这一阶段的主要目标不是直接写正式 PRD，而是通过对话循环结合原始需求文档和用户回复，把需求内容调整结果稳定沉淀到 `{FEATURE_DIR}/PRD_DISCUSS.md`。

#### Step 4.1 问题清单引导策略

1. 按优先级排序：先解决 P0，再解决 P1，最后讨论 P2
2. 逐项确认：每个问题都要与 PM 确认当前状态和真实意图
3. 引导补充：针对缺失项，用提问帮助 PM 补齐
4. 记录回复：详细记录PM的回复和补充内容

#### Step 4.2 需求摘要总结策略

**核心原则**：PRD_DISCUSS.md 的需求摘要必须包含需求的完整开发内容，而非仅记录讨论的问题。

**需求摘要必须包含**：
1. **需求概述**
2. **痛点与解决方案**
3. **特性说明**（核心，**逐任务详细展开**，格式参照 `references/doc_module.md`）
   每个任务必须包含：业务逻辑、字段定义、筛选条件、状态流转、验收标准、第三方依赖等，**禁止遗漏原文档任何细节**

**【强制完整性校验 — 生成后立即执行】**
生成需求摘要后，**必须**执行以下校验，不得跳过：

| # | 检查项 | 内容 |
|---|--------|------|
| 1 | 逐任务对照 | 每个任务与原始文档逐字逐句比对 |
| 2 | 字段完整性 | 字段名称、类型、长度、枚举值、默认值、校验规则 |
| 3 | 筛选条件完整性 | 筛选字段、方式、数据来源、默认值 |
| 4 | 验收标准完整性 | 所有验收标准 |
| 5 | 状态流转完整性 | 状态定义、流转规则、操作权限 |
| 6 | 第三方依赖完整性 | 接口、对接系统、外部数据源 |
| 7 | 删除线/不做标记 | "不做""二期""已废弃"内容已在任务标题标注 |

校验结果写入 PRD_DISCUSS.md 的 `## 完整性校验` 章节（格式：`- 逐任务对照：✅`）。**校验未通过则立即补充，直至全部通过再进入下一步。**


#### Step 4.3 调整需求摘要
- 根据用户回答调整需求摘要，确保需求摘要内容的准确性
- 不做的任务在任务标题上明确标注"（二期）"或"本期不做"
- 对于需求摘要内容中描述不明确的内容，应在对应位置标注"【待确认】"并在"待确认事项"中记录


#### Step 4.4 讨论沉淀生成

`PRD_DISCUSS.md` 是固定文件名，每轮增量更新。必须包含：

1. **需求摘要【核心】**：即 Step 4.2 生成的完整需求内容
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

Expected output: `{FEATURE_DIR}/PRD_DISCUSS.md` 已沉淀当前轮次的需求结论、待确认项和风险。

### Step 5: 迭代直到收敛

将 `{FEATURE_DIR}/PRD_DISCUSS.md` 与 `analysis-guide.md` 反复对照检查：

1. 检查原问题是否已解决
2. 检查是否引入新问题或新歧义
3. 若仍存在 P0 / P1，继续回到 Step 2-4
4. 每轮都要向用户展示检查结果，由用户判断是否可以终止循环

**【完整性检查 — 每轮迭代必执行】**
按 Step 4.2 的 7 项完整性校验标准，将 PRD_DISCUSS.md 与原始需求文档逐项对照。发现遗漏立即补充后再进入下一轮。

#### 迭代终止条件

满足以下任一条件可视为需求已收敛：

- 用户明确表示"无问题""可以了""通过""开始整理正式 PRD"
- 用户表示"不需要""不考虑"等问题暂存的相似语句
- 所有 P0 / P1 已处理完毕，只剩可接受的 P2 建议
- 连续两次检查没有新增实质问题

### Step 6: 更新状态

使用统一脚本将当前 Feature 推进到 `discuss_done`：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint discuss_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```


## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python autobiz/hooks/biz_validate.py discuss --feature {slug}
```

脚本通过即视为以下清单已完成：

- `{FEATURE_DIR}/PRD_DISCUSS.md` — 已存在，且保留了完整收敛过程
- `{FEATURE_DIR}/PRD_DISCUSS.md` — 包含需求摘要、已确认结论、问题清单与处理状态、待确认事项、假设与风险
- `{FEATURE_DIR}/PRD_DISCUSS.md` — 完整性校验章节所有检查项均为检查通过
- `.autobizdevops/state.json` — Feature checkpoint 为 `discuss_done`
- 所有 P0 / P1 问题已处理完毕（或已和用户确认接受风险）

**Skill 完成。** 下一步：`/autobiz-prd-generate`（生成正式 PRD）

## 技能使用约束

1. 本技能专注需求文档完善优化，不涉及代码实现检查
2. 分析和输出必须严格参照参考文档
3. 对话式引导中保持专业、友好的沟通态度
4. `PRD_DISCUSS.md` 是固定中间产物，不要临时改名
