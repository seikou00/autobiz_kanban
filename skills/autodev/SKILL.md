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
                         ├── 可选前端 HTML → references/frontend-html/with-absolute-html
                         └── 可选前端 HTML → references/frontend-html/with-standard-html
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



##  Checkpoint 路由

使用 `resolve_next_skill.py --json` 的返回结果路由：

- `requiresProfileChoice: true`：按 §1.2 的当前默认策略进入 `specs_in_progress`；只有用户明确要求 legacy frontend profile 时才尝试旧路线。
- `requiresWorkflowChoice: true`：先完成 dynamic stage 选择，使用 `--workflow-decision {stageId}=enabled|skipped` 写入 state.json 后再路由。
- `recommendedNextSkill` 非空：调用对应子技能。
- `recommendedNextSkill` 为空且当前 checkpoint 为 `verify_done`：Dev 阶段结束，进入 Ops。
- `checkpoint` 为 `needs_fix`：停止，读取最近阶段报告中的建议回流阶段并提示用户。
- `ok: false`：展示 `errors` 并停止。

---

## 执行后校验

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

##  前端 HTML 实现归属

HTML 转前端现在归属 `/autodev-code`，作为 code 阶段的内部实现分支：

1. `/autodev` 不再路由到 `/autodev-frontend`，也不再写入 `frontend_in_progress` / `frontend_done`。
2. `prd_done` 后统一进入 `/autodev-specs`，先沉淀行为契约。
3.  PLAN/specs/用户任务要求根据 HTML 实现前端，`/autodev-code` 把这些 HTML/DOM/设计导出稿作为 code 阶段实现素材处理。
4. code 阶段内部按 HTML 形态分流：高保真/绝对定位/Figma 导出的 HTML 走 `autodev-code/references/frontend-html/with-absolute-html/SKILL.md`；普通静态 HTML、复制 DOM、小型静态站点或 HTML 转 React 走 `autodev-code/references/frontend-html/with-standard-html/SKILL.md`。
5. 任一分支完成后都回到 `/autodev-code` 主流程，按 code 节点完成条件推进 `code_done`。

### 约束

- HTML 只是 code 阶段的视觉与结构输入，不得覆盖 specs/design/PLAN。
- 缺少 HTML 但任务明确要求高保真转换时，由 `/autodev-code` 停止并要求补充；可由 specs/design/PLAN 直接实现时跳过 HTML 分支。
- 不得直接调用 `/autodev-frontend` 或写入旧 frontend checkpoint；旧 `frontend_before_specs -> autodev-frontend` 编排已移除，不再作为可启用配置。

---

## 动态 Dev 阶段

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
