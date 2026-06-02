---
name: autoops
description: Autoops Ops 阶段根路由器。基于 checkpoint 自动路由到 CI/CD 或归档子技能，负责 Ops 阶段准入、技能调度与终态识别。
version: v1.1.0_v0602
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

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
- 否则先读取全部 State 快照，再从 `STATE.records` 自动选择单一进行中的 Feature；无法唯一确定时列出候选并让用户选择：

```bash
python "$PLUGIN_ROOT/read_state_json.py"
```

确定 `{slug}` 后，立即读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续 checkpoint 路由、准入判断和执行后校验直接取用 `CHECKPOINT`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `CHECKPOINT`。

---

## 2. Checkpoint 路由

使用 Step 1.2 获取的 `CHECKPOINT`，按以下规则路由：

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

1. 子技能返回后重新调用 `read_state_json.py` 重新捕获 `CHECKPOINT`。
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
