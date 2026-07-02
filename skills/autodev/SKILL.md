---
name: autodev
description: Autodev Dev 阶段根路由器。基于 checkpoint 路由到对应子技能；各子技能独立负责准入检查与产物自检。
version: v1.1.1604
---

## autodev

### 技能映射
| 阶段 | 调用 Skill | 本工程文件 |
|------|------------|------------|
| Specs | `/autodev-specs` | `autodev/autodev-specs/SKILL.md` |
| Plan | `/autodev-plan` | `autodev/autodev-plan/SKILL.md` |
| Detail Design（dynamic stage） | `/autodev-detail-design` | `autodev/autodev-detail-design/SKILL.md` |
| Code（含可选前端 HTML 实现分支） | `/autodev-code` | `autodev/autodev-code/SKILL.md` |
| Requirements Review | `/autodev-reviewer` | `autodev/autodev-reviewer/SKILL.md` |
| Unit Test | `/autodev-utest` | `autodev/autodev-utest/SKILL.md` |
| E2E | `/autodev-e2e` | `autodev/autodev-e2e/SKILL.md` |
| Verify | `/autodev-verify` | `autodev/autodev-verify/SKILL.md` |

### 工作流

```text
prd_done → resolve_next_skill.py --json
            ↓
          /autodev-specs
            ↓
          /autodev-plan
            ↓
plan_done → detail_design_before_code choice
            ├── enabled → /autodev-detail-design
            └── skipped → /autodev-code
                         ├── 可选前端 HTML → deps/frontend-html/with-absolute-html
                         └── 可选前端 HTML → deps/frontend-html/with-standard-html
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
python "{PLUGIN_ROOT}/read_state_json.py"
```

- 需要用户从候选 Feature 中选择时，若当前运行模式支持 `request_user_input`，必须优先用它把 `STATE.records` 中的候选列成结构化选项供用户单选；若不支持，必须列出候选 slug 并显式追问用户回复其一。未拿到明确选择前，不得推进任何 checkpoint。

确定 `{slug}` 后，立即读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

后续 checkpoint 路由、准入判断和执行后校验直接取用 `CHECKPOINT`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `CHECKPOINT`。

随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_next_skill.py" --workspace "{PROJECT_PLUGIN_DIR}" --feature "{FEATURE_ID}" --json
```

若脚本返回 `requiresProfileChoice: true`，当前默认不再把“需要 HTML 转前端”解释为旧 frontend workflow profile：

- 用户说需要先转 HTML、先把设计稿转工程文件、HTML 转 React、静态 HTML 转前端代码、按 PRD 先做前端页面等，视为本轮 code 阶段需要使用 HTML 实现素材；仍推进到 `specs_in_progress`，先沉淀行为规格，后续由 `/autodev-code` 在 code 阶段按内部 HTML route 分流处理。
- 用户说不需要、直接进规格、先走 `autodev-specs` 等，同样推进到 `specs_in_progress`。
- 旧 `/autodev-frontend` / `frontend_before_specs` 路线已移除；即使用户明确要求旧路线，也只能说明该路线当前不可执行，并转为 `/autodev-code` 内部 `FRONTEND_ROUTE.json` 分流，不得新增 wrapper 或改写 board_config。
- 如果用户只触发 `/autodev`，且没有表达需要或不需要：直接推进到 `specs_in_progress`；不要为了 HTML 转前端发起 profile 选择，也不要写入 `frontend_in_progress`。

若脚本返回 `requiresWorkflowChoice: true`，读取 `workflowChoices` 中的 `stageId`、`decision` 和 `targetCheckpoint`，按用户表达选择 dynamic stage：

- 对 `detail_design_before_code`，用户说需要详细设计、先出详细设计、code 前设计等，视为启用：推进到 `detail_design_in_progress`，并传入 `--workflow-decision detail_design_before_code=enabled`。
- 用户说不需要、直接编码、跳过详细设计等，视为跳过：推进到 `code_in_progress`，并传入 `--workflow-decision detail_design_before_code=skipped`。
- 如果用户只触发 `/autodev`，且没有表达需要或不需要：若当前运行模式支持 `request_user_input`，必须优先用它发起选择，选项至少包含 `先做详细设计` / `直接进入编码 (Recommended)`；若不支持，必须显式追问：`是否需要在代码实现前生成 DETAIL_DESIGN.md？需要则进入 autodev-detail-design，不需要则直接进入 autodev-code。请回复"先做详细设计"或"直接进入编码"。` 未拿到明确答复前，不得写入 workflow-decision，也不得推进 checkpoint。

### 1.3 产出物校验

根路由器只确认当前 Feature 能唯一定位；具体输入产物由即将路由到的子技能按本 Feature 的执行清单（`inspect_skill_contract.py --feature ... --plain` 输出）校验。

- 标准链下，`prd_done` / `specs_in_progress` 进入 `/autodev-specs` 时必须存在 `PRD.md`；精简链（lean）等无 Biz 阶段的工作流中，契约不含 `PRD.md`，`/autodev-specs` 基于用户描述直接澄清，不得因缺 PRD 阻断。
- `specs_done` 之后的 Dev 阶段不再把 `PRD.md` 作为硬输入，统一以 `proposal.md` 与 `specs/**/*.md` 作为行为契约源。

**提示（仅标准链缺 PRD 时）：** `请先使用 /autobiz 系列技能补齐 Biz 阶段产出物 PRD.md，然后重新触发 /autodev。proposal.md 与 specs/**/*.md 将由 /autodev-specs 生成，design.md 与 PLAN.md 将由 /autodev-plan 生成。`


### 禁止事项

1. **禁止在 Dev 阶段凭空生成 PRD；只有 `/autodev-specs` 可以生成或更新 proposal.md 与 specs/**/*.md，只有 `/autodev-plan` 可以生成或更新 design.md 与 PLAN.md。**
2. **禁止跳跃 checkpoint。**
---


## 2. Checkpoint 路由

使用 `resolve_next_skill.py --json` 的返回结果路由：

- `requiresProfileChoice: true`：按 §1.2 的当前默认策略进入 `specs_in_progress`；只有用户明确要求 legacy frontend profile 时才尝试旧路线。
- `requiresWorkflowChoice: true`：先完成 dynamic stage 选择，使用 `--workflow-decision {stageId}=enabled|skipped` 写入 state.json 后再路由。
- `recommendedNextSkill` 非空：调用对应子技能。
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

各子技能的产物契约、validators 与 checkpoint 合法矩阵以 `{PLUGIN_ROOT}/board_core/board_config.json` 为唯一事实来源；如本文静态说明与 board config 冲突，以 board config 为准。不得再新增 per-skill `artifact-check.yaml`。可运行以下只读命令查看某个子技能的当前契约：

```bash
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-plan --feature "{FEATURE_ID}" --plain
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-plan --feature "{FEATURE_ID}" --json
```

---

## 4. 前端 HTML 实现归属

HTML 转前端现在归属 `/autodev-code`，作为 code 阶段的内部实现分支：

1. `/autodev` 不再路由到 `/autodev-frontend`，也不再写入 `frontend_in_progress` / `frontend_done`。
2. `prd_done` 后统一进入 `/autodev-specs`，先沉淀行为契约。
3.  PLAN/specs/用户任务要求根据 HTML 实现前端，`/autodev-code` 把这些 HTML/DOM/设计导出稿作为 code 阶段实现素材处理。
4. code 阶段内部按 HTML 形态分流：高保真/绝对定位/Figma 导出的 HTML 走 `autodev-code/deps/frontend-html/with-absolute-html/SKILL.md`；普通静态 HTML、复制 DOM、小型静态站点或 HTML 转 React 走 `autodev-code/deps/frontend-html/with-standard-html/SKILL.md`。
5. 任一分支完成后都回到 `/autodev-code` 主流程，按 code 节点完成条件推进 `code_done`。

### 约束

- HTML 只是 code 阶段的视觉与结构输入，不得覆盖 specs/design/PLAN。
- 缺少 HTML 但任务明确要求高保真转换时，由 `/autodev-code` 停止并要求补充；可由 specs/design/PLAN 直接实现时跳过 HTML 分支。
- 不得直接调用 `/autodev-frontend` 或写入旧 frontend checkpoint；旧 `frontend_before_specs -> autodev-frontend` 编排已移除，不再作为可启用配置。

---

## 5. 动态 Dev 阶段

Dev 阶段的可选步骤由 `{PLUGIN_ROOT}/board_core/board_config.json` 的 `workflow.dynamicStages` 声明，运行态选择写入 `.autobizdevops/state.json` 的 `workflowDecisions`。根路由器不得硬编码某个可选节点的流程结构，应以 `resolve_next_skill.py --json` 的 `workflowChoices` 为准。

### 详细设计（`detail_design_before_code` dynamic stage）

在 `plan_done` checkpoint 若脚本返回 `requiresWorkflowChoice: true`：

1. 根据用户表达判断是否需要在 code 前生成 `DETAIL_DESIGN.md`。
2. 不需要时，推进到 `code_in_progress`：
   `python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_in_progress --workflow-decision detail_design_before_code=skipped`
3. 需要时，推进到 `detail_design_in_progress`：
   `python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint detail_design_in_progress --workflow-decision detail_design_before_code=enabled`
4. `/autodev-detail-design` 生成 `DETAIL_DESIGN.md` 后推进到 `detail_design_done`，根路由器再次刷新状态并进入 `/autodev-code`。

### 约束

- dynamic stage 必须先在 board_config 的 `workflow.dynamicStages` 声明，不得由根 skill 临时发明节点。
- 未写入对应 `workflowDecisions` 时，不得直接跳入动态节点 checkpoint。
- 已启用的 dynamic stage 只通过 `workflowDecisions` 生效；前端 HTML 实现不再作为 workflowProfile 叠加节点。

---
