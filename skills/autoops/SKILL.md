---
name: autoops
description: Autoops Ops 阶段根路由器。基于 checkpoint 路由到 CI/CD 或归档子技能，负责 Ops 阶段准入、技能调度与终态识别。
version: v1.1.1604
---

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
- 否则先读取全部 State 快照，再从 `STATE.records` 优先选择单一进行中的 Feature；无法唯一确定时列出候选并让用户选择：

```bash
python "{PLUGIN_ROOT}/read_state_json.py"
```

确定 `{slug}` 后，立即读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

后续 checkpoint 路由、准入判断和执行后校验直接取用 `CHECKPOINT`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `CHECKPOINT`。

随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_next_skill.py" --workspace "{PROJECT_PLUGIN_DIR}" --feature "{FEATURE_ID}" --json
```

---

## 2. Checkpoint 路由

使用 Step 1.2 获取的 `CHECKPOINT` 和 `resolve_next_skill.py --json` 的返回结果路由。`recommendedNextSkill`、`allowedNextCheckpoints` 与 `nextAction` 均以 `{PLUGIN_ROOT}/board_core/board_config.json` 的有效 workflow 为准。

- `recommendedNextSkill` 为 `autoops-cicd` 或 `autoops-archive` 时，调用对应子技能。
- `checkpoint` 为 `archived` 时，Ops 终态，提示已归档并输出归档位置（如可定位）。
- `checkpoint` 为 `needs_fix` 时，停止，读取最近阶段报告中的建议回流阶段并提示用户。
- `ok: false` 或 `recommendedNextSkill` 不属于 Ops skill 时，停止并展示脚本返回的错误或当前 checkpoint。

所有非终止状态默认将 `$ARGUMENTS` 透传至子技能。

---

## 3. 执行后校验

子技能返回后，根路由器必须：

1. 子技能返回后重新调用 `read_state_json.py` 重新捕获 `CHECKPOINT`。
2. 重新调用 `resolve_next_skill.py --json`，确认出口仍在当前 profile 的合法矩阵中。
3. 出口不合法时保持原状态并告警，不继续推进。
4. 若脚本推荐 `/autoops-archive`，继续归档；`archived` 后 Ops 阶段结束。

---

$ARGUMENTS
