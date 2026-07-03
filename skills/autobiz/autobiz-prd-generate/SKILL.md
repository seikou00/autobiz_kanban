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
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autobiz-prd-generate --feature "{FEATURE_ID}" --json
```
### 更新状态

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

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
- `UI_CONTEXT.json` 是 UI 范围；先读它，再写 `PRD.md`，不要从 PRD 正文重新推导 `uiRequired`。
- `PRD.md` 只描述 UI 行为范围、页面目标、关键交互、加载态、空态、错误态和成功态，不描述前端实现方案、组件库选择或代码结构。
- 生成 PRD 后必须同步更新 `UI_CONTEXT.json`：将已确认的 UI 决策推进到 `decisionStatus=confirmed`。
- `uiRequired=true` 时，确保 `pages[]`、`interactions[]` 或 `visualSources[]` 至少能表达 UI 范围；页面数、页面列表、页面目标和核心交互必须能从这些结构化字段读出。
- 高保真 HTML、标准 HTML、设计稿、Figma/MasterGo 或原型链接只保留在 `visualSources[]`，作为 code 阶段实现输入；不要把 HTML、设计稿、原型链接直接混入 `PRD.md` 正文作为需求实现。
- 若讨论阶段确认存在高保真但尚未拿到文件或链接，保留 `visualSources[]` 的占位引用和 `required=true`，并在 `PRD.md` 中只写“高保真输入待提供”的风险或依赖，不把缺失文件当作已确认需求内容。
- PRD 阶段不要编造 `capabilities[].specRefs`；PRD 阶段通常只维护 `pages[]`、`interactions[]`、`visualSources[]`。
- `uiRequired=false` 时，保留或补齐 `notApplicableReason`。

### 最终检查与交接

完成 PRD 生成后，检查以下事项：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已存在，且以 `# 需求正式稿` 开头
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 不包含讨论稿说明句 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json` 已存在，且 `decisionStatus` 至少为 `confirmed`

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
python "${pluginPath}/skills/autobiz/hooks/biz_validate.py" prd --feature ${feature}
```

通过脚本检查即视为**Skill 完成。**

## 技能使用约束

1. 本技能专注生成正式 PRD，不替代需求澄清过程
2. 不能把用户未确认的内容"补全成看起来合理的实现"
3. 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
