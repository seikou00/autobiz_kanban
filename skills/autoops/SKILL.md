---
name: autoops
description: Autoops Ops 阶段根路由器。基于 checkpoint 路由到 CI/CD 或归档子技能，负责 Ops 阶段准入、技能调度与终态识别。
version: v1.1.2609
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

## 准入检查

### 解析参数

扫描 `$ARGUMENTS`：

| 标志 | 含义 |
|------|------|
| `pipeline_code` | 可选流水线编号，透传给 `/autoops-cicd` |

###  确定 Feature状态

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

每次需要当前 checkpoint 时，运行上面脚本读取，不得从 `hooks.ndjson` 等其他文件推断。
随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_next_skill.py" --json
```

---

## Checkpoint 路由

使用`CHECKPOINT` 和 `resolve_next_skill.py --json` 的返回结果路由。`recommendedNextSkill`、`allowedNextCheckpoints` 与 `nextAction` 均以 `${pluginPath}/board_core/board_config.json` 的有效 workflow 为准。

- `recommendedNextSkill` 为 `autoops-cicd` 或 `autoops-archive` 时，调用对应子技能。
- `checkpoint` 为 `archived` 时，Ops 终态，提示已归档并输出归档位置（如可定位）。
- `checkpoint` 为 `needs_fix` 时，停止，读取最近阶段报告中的建议回流阶段并提示用户。
- `ok: false` 或 `recommendedNextSkill` 不属于 Ops skill 时，停止并展示脚本返回的错误或当前 checkpoint。


---

## 执行后校验

子技能返回后，根路由器必须：

1. 子技能返回后重新运行 `read_state_json.py` 读取当前 checkpoint。
2. 重新调用 `resolve_next_skill.py --json`，确认出口仍在当前 profile 的合法矩阵中。
3. 出口不合法时保持原状态并告警，不继续推进。
4. 若脚本推荐 `/autoops-archive`，继续归档；`archived` 后 Ops 阶段结束。

---
