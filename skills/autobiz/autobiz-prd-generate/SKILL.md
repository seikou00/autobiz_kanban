---
name: autobiz-prd-generate
description: Biz 阶段 PRD 生成技能。
version: v1.2.1701
---

# /autobiz-prd-generate — Biz 阶段 PRD 生成

## 概述

本技能用于生成可交付的正式 `PRD.md`。

## 核心能力

- 提炼上游产物：如有PRD_DISCUSS.md，则读取已收敛的需求摘要、确认结论、问题处理状态、假设与风险；若无则根据用户输入内容提取
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

### 生成正式 `PRD.md`

#### 目标

- 基于上游已确认内容（PRD_DISCUSS.md 如存在）提炼正式需求，未确认的内容不得写入；不要求 `PRD.md` 正文与上游产物逐字一致
- PRD.md必须以 `# 需求正式稿` 开头，如上游内容有 `本文档为需求讨论中间稿，用于记录需求讨论过程和结论`类似字样需要删除
- PRD.md只保留已确认的正式需求，上游内容如有讨论记录正文与讨论态章节需要删除、不输出包装标题
- PRD.md必须包含用户故事、验收口径、验收标准和关键约束，内容由已有内容总结

#### `PRD.md` 结构要求

最终 `PRD.md` 必须采用结构：

1. **标题与需求正文**
   - 第一行必须是 `# 需求正式稿`
   - 可以基于上游产物或用户确认的需求结论整理需求摘要、确认结论、问题处理状态、假设与风险
2. **需求段落**
   - 必须包含 `用户故事`、`验收口径`、`验收标准`、`关键约束`
   - 第一段正式需求段落建议直接从 `## 用户故事` 开始

#### 要求

- 若讨论稿包含 `历次讨论记录` 或 `讨论记录`，该标题及其后的讨论记录正文不得进入 `PRD.md`
- 用户故事应描述角色、目标和业务价值，避免写成内部实现任务
- 验收口径应拆分用户视角、工程视角和回归视角
- 验收标准必须可验证，覆盖关键输入、处理、输出、边界和异常路径
- 关键约束应覆盖需求中已明确或可从正式需求正文直接追溯的权限、数据、状态、时间和组织约束
- 若信息不足以生成正式段落，必须停止补齐信息后再生成：回到需求澄清或直接与用户确认；不要把未确认内容写进 PRD.md

#### 输出文件

- 正式稿：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`


###  最终检查与交接

完成 PRD 生成后，检查以下事项：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 已存在，且以 `# 需求正式稿` 开头,且有 `用户故事`、`验收口径`、`验收标准`、`关键约束`内容

向用户明确说明：

- PRD 位于 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`

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

通过脚本检查即视为**Skill 完成。** 提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。

## 技能使用约束

- 不能把用户未确认的内容"补全成看起来合理的实现"
- 不要输出伪代码、数据库实现方案或服务内部类设计来替代 PRD
