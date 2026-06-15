---
name: autoops-archive
description: Ops 归档阶段技能。负责在上游阶段完成后将当前 Feature 过程目录移入 archive，并把 checkpoint 从上游 done checkpoint（以契约转移表为准）推进到 archived。
version: v1.1.1604
author: zhangQiuFeng
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `{PLUGIN_ROOT}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autoops-archive --feature "{FEATURE_ID}" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）。
- **方法优先**：每个 input 的 `extract.method` 是它在场时的专属指令，优先于技能正文的通用默认。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止。
- **不列即不存在**：bundle 未列出的 id 不属于本工作流，一律不予考虑——不读、不等、不索要，也不要为其设想任何分支。
- **降级语义**：`required: false` 的输入缺失时按其 `extract.degrade` 继续，不要因缺失而停止。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->

# /autoops-archive — Feature 过程归档

## 目标

在 CI/CD 已由用户确认完成后，将当前 Feature 的过程产物从 active features 目录移动到 archive 目录，并把状态推进到终态。

## 合法入口

- 上游入口：当前工作流中允许转移到 `archived` 的上游 done checkpoint（以本 Feature 工作流契约的转移表为准，`update_checkpoint.py` 会强制校验）
- 恢复入口：若 `{FEATURE_DIR}/` 已不存在、`.autobizdevops/archive/{slug}-iter*` 已存在且 `state.json` 为 `archived`，直接提示已归档并退出

其他 checkpoint 均不得执行归档。

## 输入参数

- `--feature {slug}`（推荐）：指定当前 Feature

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

后续准入和恢复直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 路径约定

| 项目 | 路径 |
|------|------|
| 活跃 Feature 目录 | `{FEATURE_DIR}/` |
| 归档根目录 | `.autobizdevops/archive/` |
| 归档目标目录 | `.autobizdevops/archive/{slug}-iter{N}/` |
| 全局状态 | `.autobizdevops/state.json` |

`iter{N}` 的确定规则：

1. 先调用 `python "{PLUGIN_ROOT}/read_state_json.py"` 读取全量 JSON，优先取 `STATE.records[{FEATURE_ID}].iteration`；若为有效数字则作为起始候选。
2. 若迭代列为空或不是数字，则从 `1` 开始。
3. 若 `.autobizdevops/archive/{slug}-iter{N}/` 已存在，递增 N，直到找到不存在的目录。
4. 禁止覆盖、合并或删除已有归档目录。

## 执行步骤

### Step 1: 前置检查

1. 确定 `{slug}`。
2. 确认 `CHECKPOINT` 为当前工作流中允许进入 `archived` 的上游 done checkpoint（以契约转移表为准）。
3. 确认 `{FEATURE_DIR}/` 存在。
4. 确认 `.autobizdevops/archive/` 存在；若缺失，可创建该目录。

### Step 2: 选择归档目标

1. 按 `iter{N}` 规则计算目标目录。
2. 输出即将归档的源目录和目标目录。
3. 若目标目录已存在，不得覆盖，必须递增 N 后重新选择。

### Step 3: 更新状态

先更新状态、再移动目录：归档事件的 hook 日志会写入活跃 `features/{slug}/`，先更新可让该日志随目录一并归档，不会在 `features/` 留下空壳目录。

使用统一脚本将当前 Feature 的 checkpoint 推进为 `archived`，并写入归档迭代号：

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint archived --iteration "{N}"
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

只允许更新当前 `{slug}` 对应的 Feature 行，不得删除该行，不得改写其他 Feature 状态。

### Step 4: 移动过程目录

确认 `CHECKPOINT` 已为 `archived` 后，将当前 Feature 过程目录整体移动：

```text
{FEATURE_DIR}/
→ .autobizdevops/archive/{slug}-iter{N}/
```

移动后必须确认：

- 源目录 `{FEATURE_DIR}/` 不再存在
- 目标目录 `.autobizdevops/archive/{slug}-iter{N}/` 存在
- 目标目录中保留本工作流实际产生的过程产物（如 `proposal.md`、`specs/`、各阶段报告与日志；具体以本 Feature 工作流产物为准）

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

- checkpoint 不是合法上游 done checkpoint：停止，提示应先完成当前工作流的上游阶段。
- 状态已更新为 `archived` 但源目录仍在（移动失败或中断）：直接重试移动到 `.autobizdevops/archive/{slug}-iterN/`（该步幂等），不要重复更新状态。
- 目标目录冲突：不得覆盖，递增 `iterN` 后重试。
- 状态更新失败：停止并提示人工检查 `.autobizdevops/state.json`，不得删除已归档目录。

## 输出清单

Skill 完成前必须满足：

- [ ] `.autobizdevops/archive/{slug}-iter{N}/` 已存在
- [ ] `{FEATURE_DIR}/` 已不存在
- [ ] 已向用户输出归档路径与保留的过程产物清单
