---
name: autodev
description: Autodev Dev 阶段根路由器。基于 checkpoint 自动路由到对应子技能；各子技能独立负责准入检查与产物自检。
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现、验证和 `init_dev_agents.py --code-workspace`。

## autodev

### 技能映射
| 阶段 | 调用 Skill | 本工程文件 |
|------|------------|------------|
| Specs | `/autodev-specs` | `autodev/autodev-specs/SKILL.md` |
| Plan | `/autodev-plan` | `autodev/autodev-plan/SKILL.md` |
| Code | `/autodev-code` | `autodev/autodev-code/SKILL.md` |
| Requirements Review | `/autodev-reviewer` | `autodev/autodev-reviewer/SKILL.md` |
| Unit Test | `/autodev-utest` | `autodev/autodev-utest/SKILL.md` |
| E2E | `/autodev-e2e` | `autodev/autodev-e2e/SKILL.md` |
| Verify | `/autodev-verify` | `autodev/autodev-verify/SKILL.md` |

### 工作流

```text
/autodev-specs
   ↓
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
| `--auto` | 自动串联 Specs → Plan → Code → Review → UTest → E2E → Verify |
| `--feature {slug}` | 指定 Feature |

### 1.2 确定 Feature

- `--feature {slug}` 优先
- 否则先读取全部 State 快照，再从 `STATE.records` 列出候选让用户选择：

```bash
python "$PLUGIN_ROOT/read_state_json.py"
```

确定 `{slug}` 后，立即读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续 checkpoint 路由、准入判断和执行后校验直接取用 `CHECKPOINT`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `CHECKPOINT`。

### 1.3 初始化代码工作区 AGENTS.md

Dev 阶段进入路由前必须先确定 `CODE_WORKSPACE`。`CODE_WORKSPACE` 是要修改和验证的项目代码根目录，不是 `.autobizdevops/` 所在的 Feature 产物目录；如果无法从当前会话、用户项目路径或工具上下文确定，必须停止并询问用户，不能回退到 `PROJECT_PLUGIN_DIR` 猜测。

确定后执行初始化脚本，并显式传入代码工作区：

```bash
python "$PLUGIN_ROOT/hooks/init_dev_agents.py" --code-workspace "{CODE_WORKSPACE}"
```

如果脚本返回非 0，停止路由并按错误信息处理。

初始化成功后只检查 `{CODE_WORKSPACE}/AGENTS.md` 及文档地图声明的同级约束文件是否存在；不得检查 `{PROJECT_PLUGIN_DIR}/AGENTS.md`，除非 `CODE_WORKSPACE == PROJECT_PLUGIN_DIR` 已被明确确认。

### 1.4 产出物校验

根路由器只确认当前 Feature 能唯一定位；具体输入产物由即将路由到的子技能按 `$PLUGIN_ROOT/board_core/board_config.json` 校验。

- `prd_done` / `specs_in_progress` 进入 `/autodev-specs` 时必须存在 `PRD.md`。
- `specs_done` 之后的 Dev 阶段不再把 `PRD.md` 作为硬输入，统一以 `proposal.md` 与 `specs/**/*.md` 作为行为契约源。

**提示：** `请先使用 /autobiz 系列技能补齐 Biz 阶段产出物 PRD.md，然后重新触发 /autodev。proposal.md 与 specs/**/*.md 将由 /autodev-specs 生成，design.md 与 PLAN.md 将由 /autodev-plan 生成。`


### 禁止事项

1. **禁止在 Dev 阶段凭空生成 PRD；只有 `/autodev-specs` 可以生成或更新 proposal.md 与 specs/**/*.md，只有 `/autodev-plan` 可以生成或更新 design.md 与 PLAN.md。**
2. **禁止跳跃 checkpoint。**
3. **在执行autobiz与子技能时，约束必须参考AGENTS.md中存在的定制约束，不能仅遵守技能的约束。**
4. **本 skill 的规则不得覆盖 AGENTS.md；如冲突，以 AGENTS.md 中项目约束为准，除非系统级指令另有要求。**
---


## 2. Checkpoint 路由

使用 Step 1.2 获取的 `CHECKPOINT`，按以下规则路由：

所有非终止状态默认将 `/ARGUMENTS` 透传至子技能；

| Checkpoint | 路由 |
|------------|------|
| `prd_done` | `/autodev-specs` |
| `specs_in_progress` | `/autodev-specs`（恢复） |
| `specs_done` | `/autodev-plan` |
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

1. 子技能返回后重新调用 `read_state_json.py` 重新捕获 `CHECKPOINT`。
2. 对照下表确认是否为合法出口：

| 子技能 | 合法出口 checkpoint |
|--------|-------------------|
| `autodev-specs` | `specs_done` |
| `autodev-plan` | `plan_done` |
| `autodev-code` | `code_done` |
| `autodev-reviewer` | `requirements_eval_done` |
| `autodev-utest` | `unit_test_done` |
| `autodev-e2e` | `e2e_done` / `needs_fix` |
| `autodev-verify` | `verify_done` / `needs_fix` |

3. 出口不合法 → 保持原状态并告警，不推进。
4. `needs_fix` → 终止 `--auto` 串联，按最近阶段报告中的建议回流阶段处理。
5. `--auto` 模式下合法出口自动触发下一子技能；`verify_done` 后 Dev 阶段结束。

各子技能的产物契约、validators 与 checkpoint 合法矩阵以 `$PLUGIN_ROOT/board_core/board_config.json` 为唯一事实来源；如本文静态说明与 board config 冲突，以 board config 为准。不得再新增 per-skill `artifact-check.yaml`。可运行以下只读命令查看某个子技能的当前契约：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-plan
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-plan --json
```

---

$ARGUMENTS
