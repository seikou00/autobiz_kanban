---
name: autoops-archive
description: Ops 归档阶段技能。负责在 CI/CD 完成后将当前 Feature 过程目录移入 archive，并把 checkpoint 从 cicd_done 推进到 archived。
version: 1.0.0
author: zhangQiuFeng
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。
```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```
# /autoops-archive — Feature 过程归档

## 目标

在 CI/CD 已由用户确认完成后，将当前 Feature 的过程产物从 active features 目录移动到 archive 目录，并把状态推进到终态。

## 合法入口

- 上游入口：`checkpoint = cicd_done`
- 恢复入口：若 `.autobizdevops/features/{slug}/` 已不存在、`.autobizdevops/archive/{slug}-iter*` 已存在且 `state.json` 为 `archived`，直接提示已归档并退出

其他 checkpoint 均不得执行归档。

## 输入参数

- `--feature {slug}`（推荐）：指定当前 Feature
- 若 checkpoint 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 路径约定

| 项目 | 路径 |
|------|------|
| 活跃 Feature 目录 | `.autobizdevops/features/{slug}/` |
| 归档根目录 | `.autobizdevops/archive/` |
| 归档目标目录 | `.autobizdevops/archive/{slug}-iter{N}/` |
| 全局状态 | `.autobizdevops/state.json` |

`iter{N}` 的确定规则：

1. 优先读取 `state.json` 当前 Feature 记录中的迭代字段，若为有效数字则作为起始候选。
2. 若迭代列为空或不是数字，则从 `1` 开始。
3. 若 `.autobizdevops/archive/{slug}-iter{N}/` 已存在，递增 N，直到找到不存在的目录。
4. 禁止覆盖、合并或删除已有归档目录。

## 执行步骤

### Step 1: 前置检查

1. 确定 `{slug}`。
2. 确认当前 Feature checkpoint 为 `cicd_done`。
3. 确认 `.autobizdevops/features/{slug}/` 存在。
4. 确认 `.autobizdevops/archive/` 存在；若缺失，可创建该目录。

### Step 2: 选择归档目标

1. 按 `iter{N}` 规则计算目标目录。
2. 输出即将归档的源目录和目标目录。
3. 若目标目录已存在，不得覆盖，必须递增 N 后重新选择。

### Step 3: 移动过程目录

将当前 Feature 过程目录整体移动：

```text
.autobizdevops/features/{slug}/
→ .autobizdevops/archive/{slug}-iter{N}/
```

移动后必须确认：

- 源目录 `.autobizdevops/features/{slug}/` 不再存在
- 目标目录 `.autobizdevops/archive/{slug}-iter{N}/` 存在
- 目标目录中保留原 Feature 过程产物，例如 `PRD.md`、`PLAN.md`、`CICD_CHECKLIST.md`、`PR_BODY.md`、报告与日志等

### Step 4: 更新状态

使用统一脚本更新 `.autobizdevops/state.json` 中当前 Feature 为 `archived`，并写入归档迭代号：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint archived --iteration "{N}"
```

只允许更新当前 `{slug}` 对应的 Feature 行，不得删除该行，不得改写其他 Feature 状态。

### Step 5: 输出结果

归档完成后输出：

```text
## ✓ Ops 归档完成

Feature: {slug}
归档位置: .autobizdevops/archive/{slug}-iter{N}/
checkpoint=archived

保留的过程产物:
- [列出归档目录中的主要文件]
```

## 失败处理

- checkpoint 不是 `cicd_done`：停止，提示应先完成 `/autoops-cicd`。
- 源目录不存在但状态仍是 `cicd_done`：停止，提示过程目录缺失，需人工确认是否已被移动。
- 目标目录冲突：不得覆盖，递增 `iterN` 后重试。
- 状态更新失败：停止并提示人工检查 `.autobizdevops/state.json`，不得删除已归档目录。

## 输出清单

Skill 完成前必须满足：

- [ ] `.autobizdevops/archive/{slug}-iter{N}/` 已存在
- [ ] `.autobizdevops/features/{slug}/` 已不存在
- [ ] 已向用户输出归档路径与保留的过程产物清单
