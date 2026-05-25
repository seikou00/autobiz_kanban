---
name: autoops
description: Autoops Ops 阶段根路由器。基于 checkpoint 自动路由到 CI/CD 或归档子技能，负责 Ops 阶段准入、技能调度与终态识别。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。
```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```
# /autoops — Ops 阶段根路由器

## 技能映射

| 阶段 | 调用 Skill | 本工程文件 |
|------|------------|------------|
| CI/CD | `/autoops-cicd` | `autoops/autoops-cicd/SKILL.md` |
| Archive | `/autoops-archive` | `autoops/autoops-archive/SKILL.md` |

## 工作流

```text
/autoops-cicd
   ↓
/autoops-archive
```

---

## 1. 准入检查

### 1.1 解析参数

扫描 `$ARGUMENTS`：

| 标志 | 含义 |
|------|------|
| `--feature {slug}` | 指定 Feature |
| `pipeline_code` | 可选流水线编号，透传给 `/autoops-cicd` |

### 1.2 确定 Feature

- `--feature {slug}` 优先
- 否则从 `.autobizdevops/state.json` 自动选择单一进行中的 Feature，无法唯一确定时列出候选并让用户选择

---

## 2. Checkpoint 路由

读取 `.autobizdevops/state.json` 中当前 Feature 的 checkpoint，按以下规则路由：

| Checkpoint | 路由 |
|------------|------|
| `verify_done` | `/autoops-cicd` |
| `cicd_in_progress` | `/autoops-cicd`（恢复） |
| `cicd_done` | `/autoops-archive` |
| `archived` | Ops 终态，提示已归档并输出归档位置（如可定位） |
| `needs_fix` | 停止串联，读取最近阶段报告中的建议回流阶段并提示用户 |
| 其他 | 停止并提示 checkpoint 不属于 Ops 阶段 |

所有非终止状态默认将 `$ARGUMENTS` 透传至子技能。

---

## 3. 执行后校验

子技能返回后，根路由器必须：

1. 读取 `.autobizdevops/state.json` 中当前 Feature 的 checkpoint。
2. 对照下表确认是否为合法出口：

| 子技能 | 合法出口 checkpoint |
|--------|-------------------|
| `autoops-cicd` | `cicd_done` / `cicd_in_progress` |
| `autoops-archive` | `archived` |

3. 出口不合法时保持原状态并告警，不继续推进。
4. `cicd_done` 后自动触发 `/autoops-archive`。
5. `archived` 后 Ops 阶段结束。

---

$ARGUMENTS
