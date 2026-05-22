---
name: autodev
description: Autodev Dev 阶段根路由器。基于 checkpoint 自动路由到对应子技能；各子技能独立负责准入检查与产物自检。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

## autodev

### 技能映射
| 阶段 | 调用 Skill | 本工程文件 |
|------|------------|------------|
| Plan | `/autodev-plan` | `autodev/autodev-plan/SKILL.md` |
| Code | `/autodev-code` | `autodev/autodev-code/SKILL.md` |
| Requirements Review | `/autodev-reviewer` | `autodev/autodev-reviewer/SKILL.md` |
| Unit Test | `/autodev-utest` | `autodev/autodev-utest/SKILL.md` |
| E2E | `/autodev-e2e` | `autodev/autodev-e2e/SKILL.md` |
| Verify | `/autodev-verify` | `autodev/autodev-verify/SKILL.md` |

### 工作流

```text
/autodev-plan
   ↓
/autodev-code
   ↓
/autodev-reviewer
   ↓
/autodev-utest
   ↓
/autodev-e2e
   ↓
/autodev-verify
```


---

## 1. 准入检查

### 1.1 解析参数

扫描 `/ARGUMENTS`：

| 标志 | 含义 |
|------|------|
| `--auto` | 自动串联 Plan → Code → Review → UTest → E2E → Verify |
| `--feature {slug}` | 指定 Feature |

### 1.2 确定 Feature

- `--feature {slug}` 优先
- 否则从 `.autobizdevops/STATE.md` 列出让用户选择

### 1.3 产出物校验

以下文件必须存在，缺失则停止：

```
.autobizdevops/features/{slug}/PRD.md
```

**提示：** `请先使用 /autobiz 系列技能补齐 Biz 阶段产出物 PRD.md，然后重新触发 /autodev。design.md 与 PLAN.md 将由 /autodev-plan 生成。`


### 禁止事项

1. **禁止在 Dev 阶段凭空生成 PRD；只有 `/autodev-plan` 可以生成或更新 design.md 与 PLAN.md。**
2. **禁止跳跃 checkpoint。**
3. **在执行autobiz与子技能时，约束必须参考AGENTS.md中存在的定制约束，不能仅遵守技能的约束。**
4. **本 skill 的规则不得覆盖 AGENTS.md；如冲突，以 AGENTS.md 中项目约束为准，除非系统级指令另有要求。**
---


## 2. Checkpoint 路由

读取 `.autobizdevops/STATE.md` 中当前 Feature 行的 checkpoint，按以下规则路由：

所有非终止状态默认将 `/ARGUMENTS` 透传至子技能；

| Checkpoint | 路由 |
|------------|------|
| `prd_done` | `/autodev-plan` |
| `plan_in_progress` | `/autodev-plan`（恢复） |
| `plan_done` | `/autodev-code` |
| `code_in_progress` | `/autodev-code`（恢复） |
| `code_done` | `/autodev-reviewer` |
| `requirements_eval_in_progress` | `/autodev-reviewer`（恢复） |
| `requirements_eval_done` | `/autodev-utest` |
| `unit_test_in_progress` | `/autodev-utest`（恢复） |
| `unit_test_done` | `/autodev-e2e` |
| `e2e_in_progress` | `/autodev-e2e`（恢复） |
| `e2e_done` | `/autodev-verify` |
| `verify_in_progress` | `/autodev-verify`（恢复） |
| `verify_done` | **Dev 阶段结束** |
| `needs_fix` | **停止串联**，读取最近阶段报告中的建议回流阶段并提示用户 |

---

## 3. 执行后校验

子技能返回后，根路由器必须：

1. 读取 `.autobizdevops/STATE.md` 中当前 Feature 行的 checkpoint。
2. 对照下表确认是否为合法出口：

| 子技能 | 合法出口 checkpoint |
|--------|-------------------|
| `autodev-plan` | `plan_done` |
| `autodev-code` | `code_done` |
| `autodev-reviewer` | `requirements_eval_done` |
| `autodev-utest` | `unit_test_done` |
| `autodev-e2e` | `e2e_done` / `needs_fix` |
| `autodev-verify` | `verify_done` / `needs_fix` |

3. 出口不合法 → 保持原状态并告警，不推进。
4. `needs_fix` → 终止 `--auto` 串联，按最近阶段报告中的建议回流阶段处理。
5. `--auto` 模式下合法出口自动触发下一子技能；`verify_done` 后 Dev 阶段结束。

各子技能的实际产物校验由对应 `hooks/artifact-check.yaml` 声明；根路由器不直接维护阶段产物检查规则。

---

$ARGUMENTS
