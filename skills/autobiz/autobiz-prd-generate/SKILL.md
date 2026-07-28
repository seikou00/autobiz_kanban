---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。
version: v1.2.1701
---

# /autobiz-prd-generate — Biz 阶段 PRD 生成

## 概述

本技能用于生成可交付的正式 `PRD.md`。

`PRD.md` 的正文由脚本从 `PRD_DISCUSS.md` **逐字搬运**，本技能只负责追加 `用户故事`、`验收口径`、`验收标准`、`关键约束` 四段。

## 核心能力

- 正文搬运：由 `prd_transplant.py` 把 `PRD_DISCUSS.md` 正文逐字搬进 `PRD.md`，并完成标题改写、讨论态章节整段删除、讨论稿说明句删除
- 正式段落追加：在搬运结果末尾追加 `用户故事`、`验收口径`、`验收标准`、`关键约束`
- 待确认项收口：脚本报出的 `【待确认】` 残留必须先与用户确认再落稿
- PRD 质量检查：由 `biz_validate.py prd` 校验首行标题、四段齐全、无讨论记录与禁用标题

## 工作流程

### 前置检查

调用脚本读取当前 Feature 状态：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

每次需要当前 checkpoint 时，运行上面脚本读取，不得从 `hooks.ndjson` 等其他文件推断。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autobiz-prd-generate --feature "${feature}" --plain
```
### 更新状态

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_in_progress
```

### 搬运正文

```bash
python "${pluginPath}/skills/autobiz/hooks/prd_transplant.py" --feature "${feature}"
```

脚本完成三件确定性动作，**不得手工重做、不得事后调整**：

1. `PRD.md` 首行标题固定为 `# 需求正式稿`
2. 整段删除 `历次讨论记录`、`讨论记录`、`待确认事项`、`待确认项`、`审理提炼`、`外部依赖`、`第三方依赖` 标题及其下全部正文（禁用标题的单一事实源为 `${pluginPath}/skills/autobiz/hooks/prd_rules.py` 的 `FORBIDDEN_PRD_SECTION_TITLES`，由 `biz_validate.py prd` 强制）
3. 删除 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论` 这类讨论稿说明句

其余正文逐字保留：需求概述、需求解析、功能清单与 FR 详情、当前已确认结论、问题清单与处理状态、假设与风险全部原样搬入，表格、编号、层级都不变。

脚本输出必须原样转述给用户，不得只说"已生成"：

- 「已删除章节」逐条列出（章节名 + 行数）
- 「`【待确认】`告警」逐条列出（行号 + 内容）

脚本报错时按分支处理：

- `PRD.md 已存在` → 先与用户确认是否要重刷正文；确认后加 `--force` 重跑。此时模型已追加的四段会被冲掉，需要重新追加
- `PRD_DISCUSS.md 不存在` → 走降级：先与用户完成需求澄清，再基于用户**已确认**的内容手写 `PRD.md` 正文。这是唯一允许手写正文的分支

### 处理 `【待确认】` 残留

脚本告警的每一处 `【待确认】` 都必须先与用户确认，再把确认结果写回 `PRD.md` 对应位置，并去掉标记。

使用 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

- 不得静默把 `【待确认】` 标记留在正式稿里
- 不得自行猜测内容填上
- 用户明确表示本期不做的，按讨论稿约定标注"（二期）"或"本期不做"，不要整段删除

### 追加正式段落

用 Edit 在 `PRD.md` **末尾追加**四段，禁止用 Write 整文件覆盖——那会冲掉搬运结果。

#### `PRD.md` 结构要求

1. **标题与需求正文**（脚本产出，只读）
   - 第一行是 `# 需求正式稿`
   - 其后是搬运自 `PRD_DISCUSS.md` 的需求正文
2. **需求段落**（本技能追加）
   - 必须包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
   - 从 `## 用户故事` 开始，接在正文末尾，四段之间不插入其他章节

#### 要求

- 四段内容必须能从搬运后的正文直接追溯，未确认的内容不得写入
- 用户故事应描述角色、目标和业务价值，避免写成内部实现任务
- 验收口径应拆分用户视角、工程视角和回归视角
- 验收标准必须可验证，覆盖关键输入、处理、输出、边界和异常路径
- 关键约束应覆盖需求中已明确或可从正文直接追溯的权限、数据、状态、时间和组织约束
- 若信息不足以生成四段，必须停止补齐信息后再生成：回到需求澄清或直接与用户确认；不要把未确认内容写进 `PRD.md`

#### 硬禁令与反模式

- 禁止跳过 `prd_transplant.py` 直接写 `PRD.md`
- 禁止改写、摘要、重排、重新编号搬运下来的正文——正文与 `PRD_DISCUSS.md` 保留部分逐字一致是本技能的验收口径
- 禁止把四段插进正文中间，或给四段包一层新标题
- 反模式：把讨论稿"总结"成更精炼的新正文
- 反模式：把讨论稿内容套进旧 PRD 模板重新组织章节
- 反模式：觉得功能清单表格太长而合并、省略、改成条目列表

#### 输出文件

- 正式稿：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`


###  最终检查与交接

完成 PRD 生成后，检查以下事项：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已存在，且以 `# 需求正式稿` 开头,且有 `用户故事`、`验收口径`、`验收标准`、`关键约束`内容
- `PRD.md` 中已无 `【待确认】` 残留

向用户明确说明：

- PRD 位于 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`

### 更新状态

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint prd_done
```

## 输出清单

Skill 完成后，必须运行脚本校验：

```bash
python "${pluginPath}/skills/autobiz/hooks/biz_validate.py" prd --feature "${feature}"
```

通过脚本检查即视为**Skill 完成。** 提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。

## 技能使用约束

- 不能把用户未确认的内容"补全成看起来合理的实现"
- 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
