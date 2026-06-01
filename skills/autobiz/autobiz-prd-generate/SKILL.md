---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。读取已收敛的 `{FEATURE_DIR}/PRD_DISCUSS.md`，按 `{PLUGIN_DIR}/skills/autobiz/autobiz-prd-generate/templates/prd.md` 生成生成标准化 `{FEATURE_DIR}/PRD.md`。适用于需求讨论完成后，输出可供下游 Plan 阶段消费的正式需求文档。
---

**路径变量约定（必须区分）：**
- **PLUGIN_OUTPUT_DIR**：项目插件根目录环境变量，必须指向包含 `.autobizdevops/state.json` 的目录；`read_state_json.py` / `update_checkpoint.py` 固定从这里读写状态，命令中不得传 `--workspace/-w`。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现、验证和 `init_dev_agents.py --code-workspace`。

# /autobiz-prd-generate — Biz 阶段 PRD 生成技能

> 本技能专注将讨论稿生成为正式 PRD，不负责需求澄清循环。

## 概述

本技能用于在需求讨论收敛后，基于 `PRD_DISCUSS.md` 生成标准化、可交付的正式 `PRD.md`。

## 核心能力

- 读取讨论稿：解析 `PRD_DISCUSS.md` 中的已确认结论、功能范围、验收口径
- 正式 PRD 生成：保留确认内容，保留完整业务细节，转写为标准格式
- PRD 质量检查：确保正式 PRD 无开放式问题、无未决候选方案、验收标准可验证

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

## 加载参考文档

在开始工作流程前，必须加载以下参考文档：

1. `references/doc_module.md`
   - 需求输出格式示例与结构规范
2. `references/example_doc.md`
   - 具体案例参考
3. `{PLUGIN_DIR}/skills/autobiz/autobiz-prd-generate/templates/prd.md`
   - 当前技能集合的标准 PRD 模板；生成 `PRD.md` 时必须遵循

推进 checkpoint 必须使用统一脚本。

## 工作流程

### Step 1: 前置检查

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --feature "{slug}")
```

后续准入检查直接取用 `CHECKPOINT`；若 `CHECKPOINT` 为空、未知或 Feature 不存在，必须停止并提示用户选择 Feature。执行 `update_checkpoint.py` 后必须刷新 `CHECKPOINT`。

**确定 FEATURE_DIR：**

```
FEATURE_DIR = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}
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
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --feature "{slug}" --checkpoint prd_in_progress
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --feature "{slug}")
```

### Step 3: 生成正式 `PRD.md`

#### 目标

- 删除纯讨论过程信息，如开放问题、对话原文、未采用方案
- 保留已经确认的业务目标、范围、规则、业务细节、边界和验收标准
- 将讨论稿转写为标准化、可交付、可供下游消费的正式文档

#### `PRD.md` 结构要求

最终 `PRD.md` 必须以 `{PLUGIN_DIR}/skills/autobiz/autobiz-prd-generate/templates/prd.md` 的模板主体为基础。

#### 要求

- 只写已确认内容，不把未确认讨论直接写进正式 PRD
- 验收标准必须可验证，避免纯主观表述
- 非目标必须显式写出，避免需求蠕变
- 关键约束应覆盖技术、安全、数据、时间、组织等维度
- 若某项内容来自合理推断，需明确标注"待复核"
- 不要把模板占位符（如 `[标题]`、`[要求1]`）原样留在最终 `PRD.md` 中；无法确认的内容应写成明确待确认项或回到需求澄清阶段

#### 输出文件

- 输入稿：`{FEATURE_DIR}/PRD_DISCUSS.md`
- 正式稿：`{FEATURE_DIR}/PRD.md`
- 格式：Markdown

### Step 4: 最终检查与交接

完成 PRD 生成后，检查以下事项：

- `{FEATURE_DIR}/PRD_DISCUSS.md` 已存在，且保留了完整收敛过程
- `{FEATURE_DIR}/PRD.md` 已存在，且内容已从讨论稿生成为正式交付文档
- `{FEATURE_DIR}/PRD.md` 不再包含开放式问题、原始追问对话、未决候选方案
- 正式 PRD 足以支撑后续 Dev 阶段工作

若存在待确认项，列成清单，避免 Dev 把假设当事实。

向用户明确说明：

- 讨论稿位于 `{FEATURE_DIR}/PRD_DISCUSS.md`
- 正式 PRD 位于 `{FEATURE_DIR}/PRD.md`
- 下一步必须进入 `/autodev`。

### Step 5: 更新状态（标记完成）

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --feature "{slug}" --checkpoint prd_done
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --feature "{slug}")
```

## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python autobiz/hooks/biz_validate.py prd --feature {slug}
```

通过脚本检查即视为**Skill 完成。** 下一步：`/autodev`

## 技能使用约束

1. 本技能专注将讨论稿生成为正式 PRD，不替代需求澄清过程
2. 正式 `PRD.md` 必须由讨论稿生成生成，不能跳过中间稿直接输出
3. 不能把用户未确认的内容"补全成看起来合理的实现"
4. 若发现讨论稿中存在未解决的 P0 / P1 问题，必须停止并提示用户先回到 `/autobiz-requirement-discuss`
5. 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
