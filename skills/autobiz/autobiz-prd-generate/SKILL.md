---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。
version: v1.2.1701
---

# /autobiz-prd-generate — Biz 阶段 PRD 生成技能

## 概述

本技能用于生成可交付的正式 `PRD.md`。

## 核心能力

- 提炼上游素材：按其 `读取方式` 读取已收敛的需求摘要、确认结论、问题处理状态、假设与风险
- 正式稿整理：正式稿标题固定为 `# 需求正式稿`，剔除讨论稿说明句、讨论记录、待确认事项和外部依赖章节
- 正式段落追加：直接包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- PRD 质量检查：确保追加段落可供下游 Dev 阶段消费，且正式 PRD 不包含讨论记录正文、包装标题、待确认事项或外部依赖章节

## 工作流程

### 前置检查

调用脚本读取当前 Feature 状态：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autobiz-prd-generate --feature "${feature}" --plain
```
### 更新状态

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

### 待确认问题裁定门

- 范围：汇总上游材料中的每个实质待确认项与每一处 `【待确认】`，同一决策去重后逐条裁定；`id` 用条目内容的简短 snake_case 概括。
- 展示：裁定前展示 `待确认内容 / 所在上下文 / 当前建议 / 备选 / 影响`。展示不等于裁定，必须逐条获得明确结论。
- 协议：先读取 `${pluginPath}/skills/references/ask-user-question.md`，再用 `request_user_input` 逐项提问，每轮最多 3 项；不设置 `autoResolutionMs`，必须等待明确答复。
- 消解定义：裁定即消解，但裁定必须落盘才算数；具体结论必须进入正式需求正文，原待确认标记必须移除。
- 信息缺口只有三个出口：当场提供实体、调整需求移除依赖、暂停，拿到材料后继续。声称拥有 ≠ 提供；缺失材料时不存在「先假设 / 先按默认方案 / 先占位」后推进的出口，且不得以任何措辞重新引入。
- 延后判定按语义不按字面。探索期的「后续补充并继续」模板在裁定阶段禁止使用、禁止搬进裁定门；凡选中后条目仍处于待确认状态的选项都是非法选项。
- 用户直接给出实质自由文本时应直接吸收，不得机械重复同一选择。全部条目裁定并落盘前，不得生成正式 `PRD.md`、推进 `prd_done` 或运行完成校验。
- 消解自查：产物中无待确认章节、`【待确认】`、TBD、待补充、待提供、后续确认或对缺失材料的延后引用，再进入生成步骤。

### 生成正式 `PRD.md`

#### 目标

- 基于上游已确认内容（按各 input 的 `method`/`degrade`）提炼正式需求，未确认的内容不得写入；不要求 `PRD.md` 正文与上游素材逐字一致
- 正式稿必须以 `# 需求正式稿` 开头，直接剔除独立出现的 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- 正式稿只保留已确认的正式需求，剔除讨论记录正文与讨论态章节、不输出包装标题
- 正式稿必须直接包含用户故事、验收口径、验收标准和关键约束

#### `PRD.md` 结构要求

最终 `PRD.md` 必须采用正式稿结构：

1. **正式标题与需求正文**
   - 第一行必须是 `# 需求正式稿`
   - 可以基于上游素材或用户确认的需求结论整理需求摘要、确认结论、问题处理状态、假设与风险
   - 不需要与 `PRD_DISCUSS.md` 截断前内容做正文一致性对比
2. **正式需求段落**
   - 必须直接包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
   - 第一段正式需求段落建议直接从 `## 用户故事` 开始，不要额外包一层标题

#### 要求

- 若讨论稿包含 `历次讨论记录` 或 `讨论记录`，该标题及其后的讨论记录正文不得进入 `PRD.md`
- `PRD.md` 不得包含 `审理提炼`、`待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` 标题（本条为禁用标题的单一事实源，由 `biz_validate.py prd` 强制）
- 正式需求段落不得把讨论稿内容改写成旧 PRD 模板
- 用户故事应描述角色、目标和业务价值，避免写成内部实现任务
- 验收口径应拆分用户视角、工程视角和回归视角
- 验收标准必须可验证，覆盖关键输入、处理、输出、边界和异常路径
- 关键约束应覆盖需求中已明确或可从正式需求正文直接追溯的权限、数据、状态、时间和组织约束
- 若信息不足以生成正式段落，必须停止补齐信息后再生成：按上游 `method`/`degrade` 回到需求澄清或直接与用户确认；不要把未确认内容写进正式 PRD

#### 输出文件

- 正式稿：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`
- UI 范围机器事实源：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json`

### UI 范围处理

- 写入或更新 `UI_CONTEXT.json` 前，必须先读取 `{pluginPath}/skills/autobiz/references/ui-context.md`，按其中模板和枚举生成，不要等校验失败后再读取 Python validator 反推格式。
- `UI_CONTEXT.json` 是 UI 范围机器事实源；先读它，再写 `PRD.md`，不要从 PRD 正文重新推导 `uiRequired`。
- `PRD.md` 只描述 UI 行为范围、页面目标、关键交互、加载态、空态、错误态和成功态，不描述前端实现方案、组件库选择或代码结构。
- 生成 PRD 后必须同步更新 `UI_CONTEXT.json`：将已确认的 UI 决策推进到 `decisionStatus=confirmed`。
- `uiRequired=true` 时，确保 `pages[]`、`interactions[]` 或 `visualSources[]` 至少能表达 UI 范围；页面数、页面列表、页面目标和核心交互必须能从这些结构化字段读出。
- 高保真 HTML、标准 HTML、设计稿、Figma/MasterGo 或原型链接只保留在 `visualSources[]`，作为 code 阶段实现输入；已提供的 HTML 必须使用 Feature 内归档的 `frontend-html/VIS-xxx/...` 路径和稳定 `VIS-xxx`，不要把 HTML、设计稿、原型链接直接混入 `PRD.md` 正文作为需求实现。
- 若讨论阶段确认存在高保真但尚未拿到文件或链接，保留 `visualSources[]` 的占位引用和 `required=true`，并在 `PRD.md` 中只写“高保真输入待提供”的风险或依赖；进入锁定/Code 前必须完成归档。没有高保真要求的 UI Capability 保持空 `visualSourceRefs`，不得因此阻断其他 UI Task。
- PRD 阶段不要编造 `capabilities[].specRefs`；PRD 阶段通常只维护 `pages[]`、`interactions[]`、`visualSources[]`。
- `uiRequired=false` 时，保留或补齐 `notApplicableReason`。
- 如果 `PRD_DISCUSS.md` 的自然语言与 `UI_CONTEXT.json` 冲突，以 `UI_CONTEXT.json` 为准；需要改变 UI 范围时先回到用户确认，再更新 `UI_CONTEXT.json`，不要让 Markdown 与 JSON 双源漂移。

### 最终检查与交接

完成 PRD 生成后，检查以下事项：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已存在，且以 `# 需求正式稿` 开头
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 不包含讨论稿说明句 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json` 已存在，格式符合 `ui-context.md`，且 `decisionStatus` 至少为 `confirmed`

向用户明确说明：

- 正式 PRD 位于 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`

### 更新状态

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python "${pluginPath}/skills/autobiz/hooks/biz_validate.py" prd --feature "${feature}"
```

通过脚本检查即视为**Skill 完成。**

## 技能使用约束

1. 本技能专注生成正式 PRD，不替代需求澄清过程
2. 不能把用户未确认的内容"补全成看起来合理的实现"
3. 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
