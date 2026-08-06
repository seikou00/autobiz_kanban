---
name: autodev
description: Autodev Dev 阶段根路由器。基于 checkpoint 路由到对应子技能；各子技能独立负责准入检查与产物自检。
version: v1.1.0804
---

## autodev

### 技能映射
| 阶段 | 调用 Skill | 本工程文件 |
|------|------------|------------|
| Frontend（`frontend_before_specs` profile） | `/autodev-frontend`（内部按标准 HTML / 绝对定位 HTML 分流，用户确认后可 review） | `autodev/autodev-frontend/SKILL.md` |
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
                                      ├── 高保真/绝对定位 HTML → route/with-absolute-html
                                      └── 普通静态/拷贝 HTML → route/with-standard-html
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



##  Checkpoint 路由

使用 `resolve_next_skill.py --json` 的返回结果路由：

- `requiresProfileChoice: true`：先完成 workflow profile 选择并写入 checkpoint。
- `requiresWorkflowChoice: true`：先完成 dynamic stage 选择，使用 `--workflow-decision {stageId}=enabled|skipped` 写入 state.json 后再路由。
- `recommendedNextSkill` 非空：调用对应子技能。
- `recommendedNextSkill` 为空且当前 checkpoint 为 `verify_done`：Dev 阶段结束，进入 Ops。
- `checkpoint` 为 `needs_fix`：停止，读取最近阶段报告中的建议回流阶段并提示用户。
- `ok: false`：展示 `errors` 并停止。

---

## 执行后校验

子技能返回后，根路由器必须：

1. 子技能返回后重新运行 `read_state_json.py` 读取当前 checkpoint。
2. 重新调用 `resolve_next_skill.py --json`，若返回 `ok: false` 或 checkpoint 不在 board_config 当前 profile 的合法矩阵中，保持原状态并告警。
3. `needs_fix` → 按最近阶段报告中的建议回流阶段处理。
4. 合法出口只更新当前阶段结果；后续阶段需由用户再次触发根路由器或指定子技能继续执行。



---

##  HTML 转前端（`frontend_before_specs` profile）

在 `prd_done` checkpoint 进入 Dev 阶段时，若 `resolve_next_skill.py --json` 返回 `requiresProfileChoice: true`：

1. 根据用户表达判断是否需要将 HTML 转换为项目内工程文件；若不明确，使用上面的简短问题确认。
2. 不需要转换时，推进到 `specs_in_progress` 并进入 `/autodev-specs`。
3. 需要转换时，使用 `--workflow-profile frontend_before_specs` 推进到 `frontend_in_progress`，进入 `/autodev-frontend` 这个工作流节点。
4. 工作流节点内按输入形态分流：高保真/绝对定位/Figma 导出的 HTML 走 `/autodev-frontend` 的 `route/with-absolute-html/SKILL.md`；普通静态 HTML、复制的 DOM 片段、小型静态站点或用户明确说 HTML 转 React 时，走 `route/with-standard-html/SKILL.md`；主线完成且用户明确确认后，才走 `route/review/SKILL.md`。
5. 任一入口完成后都推进到 `frontend_done`，根路由器再次刷新状态并进入 `/autodev-specs`。

### 约束

- HTML / PRD 转前端是 `frontend_before_specs` profile 的正式 Dev 节点，不是停留在 `prd_done` 的无状态临时步骤；具体路线由 `/autodev-frontend` 内部 route 目录负责。
- 转换不影响 Specs 阶段的输入产物要求，`PRD.md` 仍为 `/autodev-specs` 的必需输入。
- 未选择 `frontend_before_specs` 时，不得直接调用 `/autodev-frontend` 或其内部 route 绕过 workflow profile。

---

##  动态 Dev 阶段

Dev 阶段的可选步骤由 `${pluginPath}/board_core/board_config.json` 的 `workflow.dynamicStages` 声明，运行态选择写入 `.autobizdevops/state.json` 的 `workflowDecisions`。根路由器不得硬编码某个可选节点的流程结构，应以 `resolve_next_skill.py --json` 的 `workflowChoices` 为准。

### 详细设计（`detail_design_before_code` dynamic stage）

在 `plan_done` checkpoint 若脚本返回 `requiresWorkflowChoice: true`：

1. 根据用户表达判断是否需要在 code 前生成 `DETAIL_DESIGN.md`。
2. 不需要时，推进到 `code_in_progress`：
   `python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress --workflow-decision detail_design_before_code=skipped`
3. 需要时，推进到 `detail_design_in_progress`：
   `python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint detail_design_in_progress --workflow-decision detail_design_before_code=enabled`
4. `/autodev-detail-design` 生成 `DETAIL_DESIGN.md` 后推进到 `detail_design_done`，根路由器再次刷新状态并进入 `/autodev-code`。

### 约束

- dynamic stage 必须先在 board_config 的 `workflow.dynamicStages` 声明，不得由根 skill 临时发明节点。
- 未写入对应 `workflowDecisions` 时，不得直接跳入动态节点 checkpoint。
- 已启用的 dynamic stage 与 `workflowProfile` 叠加生效；例如先走过 `frontend_before_specs` 的 Feature，启用详细设计后仍必须保留 frontend 节点历史。

---
