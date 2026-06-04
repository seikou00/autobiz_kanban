---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。读取已收敛的 `{FEATURE_DIR}/PRD_DISCUSS.md`，截取讨论记录标题之前的内容并规范化为正式稿前缀，再直接追加用户故事、验收口径、验收标准和关键约束，生成 `{FEATURE_DIR}/PRD.md`。适用于需求讨论完成后，输出可供下游阶段消费的正式需求文档。
version: v1.1.1604
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

# /autobiz-prd-generate — Biz 阶段 PRD 生成技能

> 本技能专注将讨论稿生成为正式 PRD，不负责需求澄清循环。

## 概述

本技能用于在需求讨论收敛后，基于 `PRD_DISCUSS.md` 生成可交付的正式 `PRD.md`。正式稿应提炼讨论稿中的已确认需求，形成可供下游 Dev 阶段消费的正式需求文档。

## 核心能力

- 读取讨论稿：读取 `PRD_DISCUSS.md` 中已经收敛的需求摘要、确认结论、问题处理状态、假设与风险
- 正式稿整理：正式稿标题固定为 `# 需求正式稿`，剔除讨论稿说明句、讨论记录、待确认事项和外部依赖章节
- 正式段落追加：直接包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- PRD 质量检查：确保追加段落可供下游 Dev 阶段消费，且正式 PRD 不包含讨论记录正文、包装标题、待确认事项或外部依赖章节

## 触发条件

以下场景应自动触发本技能：

- 用户要求"生成正式 PRD""整理 PRD""输出标准需求文档"
- 用户要求"基于讨论稿写 PRD"
- 用户在 `/autobiz-requirement-discuss` 收敛后，要求继续生成正式文档
- 用户提到"PRD 定稿""需求定稿""输出最终需求"

## 输入前提

优先读取以下输入，按可信度从高到低使用：

1. `{FEATURE_DIR}/PRD_DISCUSS.md`
2. 用户明确给出的已确认需求结论、功能范围、验收标准

`{FEATURE_DIR}/PRD_DISCUSS.md` 是 `/autobiz-requirement-discuss` 的中间讨论稿，必须在需求已收敛的前提下使用。

如果当前没有 `PRD_DISCUSS.md`，或需求尚未收敛（仍有大量 P0 / P1 未解决），先回到 `/autobiz-requirement-discuss` 完成需求澄清。

如果当前只有 `PRD_DISCUSS.md`、还没有 `PRD.md`，必须按本技能流程完成"生成正式 PRD"。

推进 checkpoint 必须使用统一脚本。

## 工作流程

### Step 1: 前置检查

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续准入检查直接取用 `CHECKPOINT`；若 `CHECKPOINT` 为空、未知或 Feature 不存在，必须停止并提示用户选择 Feature。执行 `update_checkpoint.py` 后必须刷新 `CHECKPOINT`。

**确定 FEATURE_DIR：**

```
FEATURE_DIR = {PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}
```

由父级入口统一调用脚本校验上游产物：

```bash
python autobiz/hooks/biz_validate.py discuss --feature {slug}
```

脚本通过后，读取 `{FEATURE_DIR}/PRD_DISCUSS.md`，检查讨论稿的收敛状态：

- 若仍有大量 P0 / P1 未解决：提示用户先回到 `/autobiz-requirement-discuss` 继续澄清
- 若已收敛或只剩可接受的 P2：继续执行

### Step 2: 更新状态

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint prd_in_progress
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

### Step 3: 生成正式 `PRD.md`

#### 目标

- 基于 `PRD_DISCUSS.md` 中已经确认的内容提炼正式需求，不要求 `PRD.md` 正文与讨论稿截断前内容逐字一致
- 正式稿必须以 `# 需求正式稿` 开头，直接剔除独立出现的 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- 正式稿不得包含 `待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` Markdown 章节及其正文
- 正式稿不得包含讨论记录正文，也不得输出包装标题
- 正式稿必须直接包含用户故事、验收口径、验收标准和关键约束

#### `PRD.md` 结构要求

最终 `PRD.md` 必须采用正式稿结构：

1. **正式标题与需求正文**
   - 第一行必须是 `# 需求正式稿`
   - 可以基于讨论稿整理需求摘要、确认结论、问题处理状态、假设与风险
   - 不需要与 `PRD_DISCUSS.md` 截断前内容做正文一致性对比
2. **正式需求段落**
   - 必须直接包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
   - 第一段正式需求段落建议直接从 `## 用户故事` 开始，不要额外包一层标题

#### 要求

- 若讨论稿包含 `历次讨论记录` 或 `讨论记录`，该标题及其后的讨论记录正文不得进入 `PRD.md`
- `PRD.md` 不得包含 `审理提炼`、`待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` 标题
- 正式需求段落不得把讨论稿内容改写成旧 PRD 模板
- 用户故事应描述角色、目标和业务价值，避免写成内部实现任务
- 验收口径应拆分用户视角、工程视角和回归视角
- 验收标准必须可验证，覆盖关键输入、处理、输出、边界和异常路径
- 关键约束应覆盖需求中已明确或可从正式需求正文直接追溯的权限、数据、状态、时间和组织约束
- 若信息不足以生成正式段落，必须停止并回到 `/autobiz-requirement-discuss` 继续澄清，不要把未确认内容写进正式 PRD

#### 输出文件

- 输入稿：`{FEATURE_DIR}/PRD_DISCUSS.md`
- 正式稿：`{FEATURE_DIR}/PRD.md`
- 格式：Markdown

### Step 4: 最终检查与交接

完成 PRD 生成后，检查以下事项：

- `{FEATURE_DIR}/PRD_DISCUSS.md` 已存在，且保留了完整收敛过程
- `{FEATURE_DIR}/PRD.md` 已存在，且以 `# 需求正式稿` 开头
- `{FEATURE_DIR}/PRD.md` 不包含讨论稿说明句 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- `{FEATURE_DIR}/PRD.md` 不包含 `审理提炼`、`待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` 标题
- `{FEATURE_DIR}/PRD.md` 不包含 `历次讨论记录` 或 `讨论记录` 标题及其后的讨论记录正文
- `{FEATURE_DIR}/PRD.md` 已追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- 正式 PRD 足以支撑后续 Dev 阶段工作

向用户明确说明：

- 讨论稿位于 `{FEATURE_DIR}/PRD_DISCUSS.md`
- 正式 PRD 位于 `{FEATURE_DIR}/PRD.md`
- 下一步必须进入 `/autodev`。

### Step 5: 更新状态（标记完成）

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint prd_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python autobiz/hooks/biz_validate.py prd --feature {slug}
```

通过脚本检查即视为**Skill 完成。** 下一步：`/autodev`

## 技能使用约束

1. 本技能专注将讨论稿生成为正式 PRD，不替代需求澄清过程
2. 正式 `PRD.md` 必须由讨论稿生成，不能跳过中间稿直接输出
3. 不能把用户未确认的内容"补全成看起来合理的实现"
4. 若发现讨论稿中存在未解决的 P0 / P1 问题，必须停止并提示用户先回到 `/autobiz-requirement-discuss`
5. 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
