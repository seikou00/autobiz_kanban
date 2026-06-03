---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。读取已收敛的 `{FEATURE_DIR}/PRD_DISCUSS.md`，先原样复制讨论记录标题之前的内容，再追加用户故事、验收口径、验收标准和关键约束审理提炼，生成 `{FEATURE_DIR}/PRD.md`。适用于需求讨论完成后，输出可供下游阶段消费的正式需求文档。
version: v1.1.1_v0603
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

本技能用于在需求讨论收敛后，基于 `PRD_DISCUSS.md` 生成可交付的正式 `PRD.md`。正式稿由两部分组成：讨论稿前半部分原文复制区，以及基于复制区追加生成的审理提炼区。

## 核心能力

- 读取讨论稿：定位 `PRD_DISCUSS.md` 中第一个 `历次讨论记录` 或 `讨论记录` Markdown 标题
- 原文复制：将讨论记录标题之前的内容完整复制到 `PRD.md`，不得改写、整理、重排
- 审理提炼：只在复制区之后追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- PRD 质量检查：确保追加区可供下游 Dev 阶段消费，且正式 PRD 不包含讨论记录正文

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

- 从 `PRD_DISCUSS.md` 开头复制到第一个讨论记录标题之前，保留讨论稿中已经沉淀的需求摘要、结论、问题处理状态、待确认事项、假设与风险等内容
- 复制区必须保持原文，不改写、不整理、不重排、不删除待确认标记或风险描述
- 在复制区之后追加审理提炼区，抽出用户故事、验收口径、验收标准和关键约束

#### `PRD.md` 结构要求

最终 `PRD.md` 必须采用两段式结构：

1. **原文复制区**
   - 从 `{FEATURE_DIR}/PRD_DISCUSS.md` 文件开头开始复制
   - 截止到第一个标题文本包含 `历次讨论记录` 或 `讨论记录` 的 Markdown 标题之前
   - 该标题及其后的讨论记录正文不得进入 `PRD.md`
2. **审理提炼区**
   - 基于原文复制区追加生成，不反向修改复制区
   - 必须包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
   - 若复制区信息不足，在对应追加段落中标注 `待确认`，不要补写到复制区

#### 要求

- 讨论记录标题识别规则：匹配 Markdown 标题行（如 `## 历次讨论记录`、`### 讨论记录`），标题文本包含 `历次讨论记录` 或 `讨论记录` 即为截断点
- 若找不到讨论记录标题，必须停止生成并提示先修正 `PRD_DISCUSS.md`，不得猜测截断范围
- 复制区必须完全保持原样，包括标题、表格、列表、待确认标记和风险描述
- 审理提炼只处理追加区，不得把复制区内容改写成旧 PRD 模板
- 用户故事应描述角色、目标和业务价值，避免写成内部实现任务
- 验收口径应拆分用户视角、工程视角和回归视角
- 验收标准必须可验证，覆盖关键输入、处理、输出、边界和异常路径
- 关键约束应覆盖需求中已明确或可从复制区直接追溯的权限、数据、状态、外部依赖、时间和组织约束
- 若某项内容来自合理推断，需在追加区明确标注 `待确认`

#### 输出文件

- 输入稿：`{FEATURE_DIR}/PRD_DISCUSS.md`
- 正式稿：`{FEATURE_DIR}/PRD.md`
- 格式：Markdown

### Step 4: 最终检查与交接

完成 PRD 生成后，检查以下事项：

- `{FEATURE_DIR}/PRD_DISCUSS.md` 已存在，且保留了完整收敛过程
- `{FEATURE_DIR}/PRD.md` 已存在，且前半部分为讨论记录标题之前的原文复制区
- `{FEATURE_DIR}/PRD.md` 不包含 `历次讨论记录` 或 `讨论记录` 标题及其后的讨论记录正文
- `{FEATURE_DIR}/PRD.md` 已追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- 正式 PRD 足以支撑后续 Dev 阶段工作

若存在待确认项，列成清单，避免 Dev 把假设当事实。

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
