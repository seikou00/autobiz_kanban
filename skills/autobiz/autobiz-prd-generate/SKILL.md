---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。按上游产物契约（Source Bundle）读取输入并按各 input 的 Method Bundle 提炼已确认需求，追加用户故事、验收口径、验收标准和关键约束，生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`。适用于需求确认后，输出可供下游阶段消费的正式需求文档。
version: v1.1.1604
---

# /autobiz-prd-generate — Biz 阶段 PRD 生成技能

> 本技能专注生成正式 PRD（按各 input 的 Method Bundle 提炼上游已确认需求），不负责需求澄清循环。

## 概述

本技能用于生成可交付的正式 `PRD.md`，输入以 Source Bundle 为准：按各 input 的 Method Bundle 提炼上游已确认需求生成正式稿（上游素材的收敛要求与缺失时的澄清路径由各 input 的 `method`/`degrade` 规定）。

## 核心能力

- 提炼上游素材：按其 Method Bundle 读取已收敛的需求摘要、确认结论、问题处理状态、假设与风险
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

输入清单以本 Feature 契约的 Source Bundle 为准，按可信度从高到低使用：

1. bundle 列出的上游产物（按其 Method Bundle 提炼）
2. 用户明确给出的已确认需求结论、功能范围、验收标准

上游素材的使用前提由各 input 的 `method`/`degrade` 规定：讨论稿类素材须在需求已收敛后使用，未收敛（仍有大量 P0 / P1 未解决）时先回到需求澄清；契约未提供讨论稿时按其 `degrade`，与用户确认需求结论、功能范围和验收标准后直接生成。

如果当前还没有 `PRD.md`，必须按本技能流程完成"生成正式 PRD"。

推进 checkpoint 必须使用统一脚本。

## 工作流程

### Step 1: 前置检查

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入检查直接取用 `CHECKPOINT`；若 `CHECKPOINT` 为空、未知或 Feature 不存在，必须停止并提示用户选择 Feature。执行 `update_checkpoint.py` 后必须刷新 `CHECKPOINT`。

**确定 FEATURE_DIR：**

```
FEATURE_DIR = ${projectDir}/.autobizdevops/features/${feature}
```

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autobiz-prd-generate --feature "${feature}" --json
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


由父级入口统一调用脚本校验上游产物（校验范围以本 Feature 契约的 `required_inputs` 为准）：

```bash
python autobiz/hooks/biz_validate.py discuss --feature {slug}
```

脚本通过后，按 Source Bundle 读取上游产物原件，按各自 `method`/`degrade` 处理：上游讨论稿类素材仍有大量 P0 / P1 未解决时先回到需求澄清；已收敛（或只剩可接受的 P2）则继续；契约未提供该素材时不读取不索要，与用户确认需求结论后直接生成。

### Step 2: 更新状态

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

### Step 3: 生成正式 `PRD.md`

#### 目标

- 基于上游已确认内容（按各 input 的 `method`/`degrade`）提炼正式需求，未确认的内容不得写入；不要求 `PRD.md` 正文与上游素材逐字一致
- 正式稿必须以 `# 需求正式稿` 开头，直接剔除独立出现的 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- 正式稿不得包含 `待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` Markdown 章节及其正文
- 正式稿不得包含讨论记录正文，也不得输出包装标题
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
- `PRD.md` 不得包含 `审理提炼`、`待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` 标题
- 正式需求段落不得把讨论稿内容改写成旧 PRD 模板
- 用户故事应描述角色、目标和业务价值，避免写成内部实现任务
- 验收口径应拆分用户视角、工程视角和回归视角
- 验收标准必须可验证，覆盖关键输入、处理、输出、边界和异常路径
- 关键约束应覆盖需求中已明确或可从正式需求正文直接追溯的权限、数据、状态、时间和组织约束
- 若信息不足以生成正式段落，必须停止补齐信息后再生成：按上游 `method`/`degrade` 回到需求澄清或直接与用户确认；不要把未确认内容写进正式 PRD

#### 输出文件

- 输入稿：以 Source Bundle 为准
- 正式稿：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`
- UI 范围机器事实源：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json`
- 格式：Markdown

### UI 范围处理

- 必须读取 Source Bundle 中的 `UI_CONTEXT.json`；它是 UI 范围机器事实源。
- `PRD.md` 只描述 UI 行为范围、页面目标、关键交互和状态反馈，不从 PRD 正文重新推导 `uiRequired`。
- 生成 PRD 后必须同步更新 `UI_CONTEXT.json`：将已确认的 UI 决策推进到 `decisionStatus=confirmed`。
- `uiRequired=true` 时，确保 `pages[]`、`interactions[]` 或 `visualSources[]` 至少能表达 UI 范围；高保真 HTML / 设计稿只保留在 `visualSources[]`。
- PRD 阶段不要编造 `capabilities[].specRefs`；REQ/SCN 由 specs 阶段定义并锁定，PRD 阶段通常只维护 `pages[]`、`interactions[]`、`visualSources[]`。
- `uiRequired=false` 时，保留或补齐 `notApplicableReason`。
- 不要把 HTML、设计稿、原型链接直接写成 PRD 正文中的需求实现；它们是 code 阶段实现输入。

### Step 4: 最终检查与交接

完成 PRD 生成后，检查以下事项：

- 上游讨论稿（若属于本工作流）已保留完整收敛过程
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已存在，且以 `# 需求正式稿` 开头
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 不包含讨论稿说明句 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 不包含 `审理提炼`、`待确认事项`、`待确认项`、`外部依赖`、`第三方依赖` 标题
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 不包含 `历次讨论记录` 或 `讨论记录` 标题及其后的讨论记录正文
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json` 已存在，且 `decisionStatus` 至少为 `confirmed`
- 正式 PRD 足以支撑后续 Dev 阶段工作

向用户明确说明：

- 上游讨论稿（若属于本工作流）的位置见 Source Bundle
- 正式 PRD 位于 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`
- 下一步按工作流推进（以 `resolve_next_skill.py` 为准）。

### Step 5: 更新状态（标记完成）

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python "${pluginPath}/skills/autobiz/hooks/biz_validate.py" prd --feature {slug}
```

通过脚本检查即视为**Skill 完成。** 下一步：`/autodev`。

## 技能使用约束

1. 本技能专注生成正式 PRD，不替代需求澄清过程
2. 上游讨论稿在工作流中时，正式 `PRD.md` 必须由其提炼、不能跳过中间稿直接输出；否则必须基于与用户确认的需求生成，不得凭空编造
3. 不能把用户未确认的内容"补全成看起来合理的实现"
4. 上游讨论稿未收敛（存在未解决的 P0 / P1）时，必须停止并提示用户先回到需求澄清
5. 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
