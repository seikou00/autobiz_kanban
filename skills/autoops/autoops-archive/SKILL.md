---
name: autoops-archive
description: Ops 归档阶段技能。负责在上游阶段完成后将当前 Feature 过程目录移入 archive，并把 checkpoint 从上游 done checkpoint（以契约转移表为准）推进到 archived。
version: v1.1.1604
author: zhangQiuFeng
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（执行清单）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，取当前 Feature 的执行清单（脚本已按 feature 目录的真实产物状态，把每个 input 解析成一条确定指令）：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autoops-archive --feature "${feature}" --plain
```

- **逐条执行**：`## 输入产物` 下每个 input 只有一行确定指令，按序执行即可，不需要自己判断产物是否存在或该走哪个分支。
- **已生成**：按其 `读取方式` 读原件并纳入上下文；`读取方式` 是该 input 在场时的专属指令，优先于技能正文的通用默认。
- **未生成**：按其 `缺失处理` 执行——必需 input 停止并回流上游补齐；可选 input 按其降级动作继续，不因缺失而停止。
- **不列即不存在**：清单未列出的 id 不属于本 workflow 的正式流程产物 input，不要把它当作上游阶段产物读取、等待或索要。
- **适用边界**：上一条只约束正式流程产物 input；不限制用户本轮直接提供的材料、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 或技能正文明确要求读取的辅助素材。
- **输出与校验**：`## 输出产物` 是本节点应产出的产物；`## Validators`/`## Guards` 是推进 checkpoint 的校验项。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线清单（此时按 `读取方式` 预览，不含产物状态）。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->

# /autoops-archive — Feature 过程归档

## 目标

在 CI/CD 已由用户确认完成后，将当前 Feature 的过程产物从 active features 目录移动到 archive 目录，并把状态推进到终态。

## 合法入口

- 上游入口：当前工作流中允许转移到 `archived` 的上游 done checkpoint（以本 Feature 工作流契约的转移表为准，`update_checkpoint.py` 会强制校验）
- 恢复入口：若 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` 已不存在、`.autobizdevops/archive/{slug}-iter*` 已存在且 `state.json` 为 `archived`，直接提示已归档并退出

其他 checkpoint 均不得执行归档。

## 输入参数

- `--feature {slug}`（推荐）：指定当前 Feature

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入和恢复直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 路径约定

| 项目 | 路径 |
|------|------|
| 活跃 Feature 目录 | `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` |
| 归档根目录 | `.autobizdevops/archive/` |
| 归档目标目录 | `.autobizdevops/archive/{slug}-iter{N}/` |
| 全局状态 | `.autobizdevops/state.json` |

`iter{N}` 的确定规则：

1. 先调用 `python "${pluginPath}/read_state_json.py"` 读取全量 JSON，优先取 `STATE.records[${feature}].iteration`；若为有效数字则作为起始候选。
2. 若迭代列为空或不是数字，则从 `1` 开始。
3. 若 `.autobizdevops/archive/{slug}-iter{N}/` 已存在，递增 N，直到找到不存在的目录。
4. 禁止覆盖、合并或删除已有归档目录。

## 执行步骤

### Step 1: 前置检查

1. 确定 `{slug}`。
2. 确认 `CHECKPOINT` 为当前工作流中允许进入 `archived` 的上游 done checkpoint（以契约转移表为准）。
3. 确认 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` 存在。
4. 确认 `.autobizdevops/archive/` 存在；若缺失，可创建该目录。

### Step 2: 选择归档目标

1. 按 `iter{N}` 规则计算目标目录。
2. 输出即将归档的源目录和目标目录。
3. 若目标目录已存在，不得覆盖，必须递增 N 后重新选择。

### Step 3: 更新状态

先更新状态、再移动目录：归档事件的 hook 日志会写入活跃 `features/{slug}/`，先更新可让该日志随目录一并归档，不会在 `features/` 留下空壳目录。

使用统一脚本将当前 Feature 的 checkpoint 推进为 `archived`，并写入归档迭代号：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint archived --iteration "{N}"
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

只允许更新当前 `{slug}` 对应的 Feature 行，不得删除该行，不得改写其他 Feature 状态。

### Step 4: 移动过程目录

确认 `CHECKPOINT` 已为 `archived` 后，将当前 Feature 过程目录整体移动：

```text
${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/
→ .autobizdevops/archive/{slug}-iter{N}/
```

移动后必须确认：

- 源目录 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` 不再存在
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
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` 已不存在
- [ ] 已向用户输出归档路径与保留的过程产物清单
