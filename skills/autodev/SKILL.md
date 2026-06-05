---
name: autodev
description: Autodev Dev 阶段根路由器。基于 checkpoint 路由到对应子技能；各子技能独立负责准入检查与产物自检。
version: v1.1.1604
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

## autodev

### 技能映射
| 阶段 | 调用 Skill | 本工程文件 |
|------|------------|------------|
| Frontend（`frontend_before_specs` profile） | `/autodev-frontend` | `autodev/autodev-frontend/SKILL.md` |
| Specs | `/autodev-specs` | `autodev/autodev-specs/SKILL.md` |
| Plan | `/autodev-plan` | `autodev/autodev-plan/SKILL.md` |
| Detail Design（dynamic stage） | `/autodev-detail-design` | `autodev/autodev-detail-design/SKILL.md` |
| Code | `/autodev-code` | `autodev/autodev-code/SKILL.md` |
| Requirements Review | `/autodev-reviewer` | `autodev/autodev-reviewer/SKILL.md` |
| Unit Test | `/autodev-utest` | `autodev/autodev-utest/SKILL.md` |
| E2E | `/autodev-e2e` | `autodev/autodev-e2e/SKILL.md` |
| Verify | `/autodev-verify` | `autodev/autodev-verify/SKILL.md` |

### 工作流

```text
prd_done → resolve_next_skill.py --json
            ├── standard → /autodev-specs
            └── frontend_before_specs → /autodev-frontend
                                             ↓
                                      /autodev-specs
                                             ↓
	                                      /autodev-plan
	                                             ↓
	                         plan_done → detail_design_before_code choice
	                                      ├── enabled → /autodev-detail-design
	                                      └── skipped → /autodev-code
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

随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python "$PLUGIN_ROOT/hooks/resolve_next_skill.py" --workspace "$PROJECT_PLUGIN_DIR" --feature "$FEATURE_ID" --json
```

若脚本返回 `requiresProfileChoice: true`，按用户表达选择是否需要 HTML 设计稿转换：

- 用户说 `/autodev-frontend`、需要进入、需要先转 HTML、先把设计稿转工程文件等，视为需要转换：推进到 `frontend_in_progress`，并传入 `--workflow-profile frontend_before_specs`。
- 用户说不需要、直接进规格、先走 `autodev-specs` 等，视为不需要转换：推进到 `specs_in_progress`。
- 如果用户只触发 `/autodev`，且没有表达需要或不需要，简短询问：`是否需要将 HTML 设计稿转换为项目内工程文件？需要则先进入 autodev-frontend 阶段，不需要则直接进入 autodev-specs 阶段。`

若脚本返回 `requiresWorkflowChoice: true`，读取 `workflowChoices` 中的 `stageId`、`decision` 和 `targetCheckpoint`，按用户表达选择 dynamic stage：

- 对 `detail_design_before_code`，用户说需要详细设计、先出详细设计、code 前设计等，视为启用：推进到 `detail_design_in_progress`，并传入 `--workflow-decision detail_design_before_code=enabled`。
- 用户说不需要、直接编码、跳过详细设计等，视为跳过：推进到 `code_in_progress`，并传入 `--workflow-decision detail_design_before_code=skipped`。
- 如果用户只触发 `/autodev`，且没有表达需要或不需要，简短询问：`是否需要在代码实现前生成 DETAIL_DESIGN.md？需要则进入 autodev-detail-design，不需要则直接进入 autodev-code。`

### 1.3 产出物校验

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

使用 `resolve_next_skill.py --json` 的返回结果路由：

- `requiresProfileChoice: true`：先完成 workflow profile 选择并写入 checkpoint。
- `requiresWorkflowChoice: true`：先完成 dynamic stage 选择，使用 `--workflow-decision {stageId}=enabled|skipped` 写入 state.json 后再路由。
- `recommendedNextSkill` 非空：调用对应子技能，所有非终止状态默认将 `/ARGUMENTS` 透传至子技能。
- `recommendedNextSkill` 为空且当前 checkpoint 为 `verify_done`：Dev 阶段结束，进入 Ops。
- `checkpoint` 为 `needs_fix`：停止，读取最近阶段报告中的建议回流阶段并提示用户。
- `ok: false`：展示 `errors` 并停止。

---

## 3. 执行后校验

子技能返回后，根路由器必须：

1. 子技能返回后重新调用 `read_state_json.py` 重新捕获 `CHECKPOINT`。
2. 重新调用 `resolve_next_skill.py --json`，若返回 `ok: false` 或 checkpoint 不在 board_config 当前 profile 的合法矩阵中，保持原状态并告警。
3. `needs_fix` → 按最近阶段报告中的建议回流阶段处理。
4. 合法出口只更新当前阶段结果；后续阶段需由用户再次触发根路由器或指定子技能继续执行。

各子技能的产物契约、validators 与 checkpoint 合法矩阵以 `$PLUGIN_ROOT/board_core/board_config.json` 为唯一事实来源；如本文静态说明与 board config 冲突，以 board config 为准。不得再新增 per-skill `artifact-check.yaml`。可运行以下只读命令查看某个子技能的当前契约：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-plan
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-plan --json
```

---

## 4. HTML 转前端（`frontend_before_specs` profile）

在 `prd_done` checkpoint 进入 Dev 阶段时，若 `resolve_next_skill.py --json` 返回 `requiresProfileChoice: true`：

1. 根据用户表达判断是否需要将 HTML 设计稿转换为项目内工程文件；若不明确，使用上面的简短问题确认。
2. 不需要转换时，推进到 `specs_in_progress` 并进入 `/autodev-specs`。
3. 需要转换时，使用 `--workflow-profile frontend_before_specs` 推进到 `frontend_in_progress`，进入 `/autodev-frontend`。
4. `/autodev-frontend` 按 `route/route-with-html.md` 的 HTML-first 流程将高保真 HTML 转换为前端代码；完成后推进到 `frontend_done`，根路由器再次刷新状态并进入 `/autodev-specs`。

### 约束

- HTML 转换是 `frontend_before_specs` profile 的正式 Dev 节点，不是停留在 `prd_done` 的无状态临时步骤。
- 转换不影响 Specs 阶段的输入产物要求，`PRD.md` 仍为 `/autodev-specs` 的必需输入。
- 未选择 `frontend_before_specs` 时，不得直接调用 `/autodev-frontend` 绕过 workflow profile。

---

## 5. 动态 Dev 阶段

Dev 阶段的可选步骤由 `$PLUGIN_ROOT/board_core/board_config.json` 的 `workflow.dynamicStages` 声明，运行态选择写入 `.autobizdevops/state.json` 的 `workflowDecisions`。根路由器不得硬编码某个可选节点的流程结构，应以 `resolve_next_skill.py --json` 的 `workflowChoices` 为准。

### 详细设计（`detail_design_before_code` dynamic stage）

在 `plan_done` checkpoint 若脚本返回 `requiresWorkflowChoice: true`：

1. 根据用户表达判断是否需要在 code 前生成 `DETAIL_DESIGN.md`。
2. 不需要时，推进到 `code_in_progress`：
   `python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint code_in_progress --workflow-decision detail_design_before_code=skipped`
3. 需要时，推进到 `detail_design_in_progress`：
   `python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint detail_design_in_progress --workflow-decision detail_design_before_code=enabled`
4. `/autodev-detail-design` 生成 `DETAIL_DESIGN.md` 后推进到 `detail_design_done`，根路由器再次刷新状态并进入 `/autodev-code`。

### 约束

- dynamic stage 必须先在 board_config 的 `workflow.dynamicStages` 声明，不得由根 skill 临时发明节点。
- 未写入对应 `workflowDecisions` 时，不得直接跳入动态节点 checkpoint。
- 已启用的 dynamic stage 与 `workflowProfile` 叠加生效；例如先走过 `frontend_before_specs` 的 Feature，启用详细设计后仍必须保留 frontend 节点历史。

---

$ARGUMENTS
