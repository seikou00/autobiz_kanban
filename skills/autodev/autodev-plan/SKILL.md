---
name: autodev-plan
description: 基于已确认技术设计生成可执行任务计划、批次计划和 PLAN.md。
version: v2.3.0908
---

# /autodev-plan - Executable Task Plan

本技能只负责 Design 之后的任务规划：消费已确认的设计，生成 `plan.json`、所有 `plans/Bxxx/plan.json` 和 `PLAN.md`。探索、行为澄清、接口/数据/技术决策和 `design.md` 的生成或裁定属于 `/autodev-design`。

## 启动、输入与边界

先读取状态和缺失产物处理；严格遵循第二个命令的输出，不自行猜测缺失输入：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
```

- 只读取 `proposal.md`、`specs/**/*.md`、`UI_CONTEXT.json`、`design.md`、`.design-contract.lock.json` 与实际代码仓库；不修改行为契约、技术设计或业务代码、测试、迁移、配置。
- `.design-contract.lock.json` 是 API / Data / D ID 与 `x-auto-no-http-api` / `x-auto-no-sql` 标记的唯一机器事实源。缺锁、无效锁，或旧 `plan_in_progress` Feature 没有锁时，回 `/autodev-design` 补齐；Plan 不重新校验或改写 `design.md`。
- 用 `write_todos` 跟踪：覆盖矩阵与候选分组、Draft 详情、正式计划、`plan_done`。确认可进入时推进：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_in_progress --stage "执行计划（来源: 技术设计）"
```

## 唯一写入入口与规划依据

`plan_writer.py` 是候选分组、Draft、工程命令、正式 Bundle 和 `PLAN.md` 的唯一写入入口。不得直接编辑根 / Batch JSON、维护平行 `plan_v*.json`，或根据 validator 失败反推 schema。`PLAN.md` 必须从 `plan.json` 投影，不能独自维护机器事实。

开始候选分组前，必须完整读取：

- `${pluginPath}/skills/autodev/autodev-plan/templates/task-groups.json`
- `${pluginPath}/skills/autodev/autodev-plan/templates/task-detail-input.json`
- [任务拆分与计划语义](references/task-planning.md)

每次 Plan 会话只运行一次以下只读命令。它是字段、枚举、模板路径、分批、workspace 和校验规则的权威来源；不要查 `--help`，也不得读取 writer 源码来发现参数或枚举值。

```bash
python "${pluginPath}/hooks/plan_writer.py" add-task-contract
```

在写入候选分组前，先完成下列只读盘点，并把结论带入覆盖矩阵和候选分组表：

- 从 `proposal.md` 的影响模块、`scope.md`、`UI_CONTEXT.json` 和实际代码仓库确认所有相关 Git 根（含前端）；缺少某个实际 Git 根时先向用户确认，不得按 backend-only 猜测。
- 建立 `workspaceRef -> 实际 Git 根 -> workspaceRoot -> 路径写法` 映射。具名仓库的 `scope.paths` 使用 `repoId:relative/path`，默认根不带前缀；不得写绝对路径或重复 workspace root。
- 建立写入归属表，逐项检查 `touches`、`scope.paths`、`expectedFiles` 和 `implementationPoints`。跨 Batch 的共享 Controller、SQL、路由、协议或全局配置只保留一个前置 owner，消费者移除该路径并通过 `deps` 消费其结果。
- 先为每个候选任务判定 `executionMode`。`verified_existing` 仅表示本任务不做业务代码改动：通常保持 `touches: []`，用真实的 `validationBoundary`、`validationCommands`、`implementationPoints` 和 `specRefs` 声明验证表面；不要把仅供验证阅读的现有文件伪装成写入归属。

不要用覆盖不完整的“2-task mini group”作为真实 Feature 的 `preflight-task-groups` 冒烟样本：该预检还会校验完整场景覆盖。路径和字段形状以本节模板、`add-task-contract` 和完整候选分组的预检为准。

## 生成流程

### 1. 候选分组与 Draft

按参考文档先输出覆盖矩阵和最终候选任务分组表。分组必须已覆盖本轮全部 SCN，且已完成写入归属审查；每个 group 只能绑定一个实际 Git 根，多仓库行为拆 task 并使用真实 `deps`。将分组写入 Feature 的 `.tmp/plan_writer/task-groups.json`，并在创建 Draft 前完成预检：

```bash
python "${pluginPath}/hooks/plan_writer.py" preflight-task-groups \
  --feature "${feature}" \
  --group-file "${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/task-groups.json"
```

预检失败时只修候选分组；`oversized_plan_task_must_split`、`missing_plan_task_split_rationale` 或 `invalid_plan_task_split_rationale` 都回覆盖矩阵重新分组。通过后创建并锁定 Draft：

```bash
python "${pluginPath}/hooks/plan_writer.py" prepare-task-draft \
  --feature "${feature}" \
  --group-file "${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/task-groups.json" \
  --code-workspace "<ACTUAL_CODE_WORKSPACE>"
```

Draft 的 `.tmp/plan_writer/draft/plan.json` 和 Batch 草稿由 writer 创建；正式根计划和 `plans/Bxxx/plan.json` 在 finalize 前不得存在。每个 task 选定 `executionMode=code|verified_existing|external_dependency` 后，按 Task ID 补完整详情：

```bash
python "${pluginPath}/hooks/plan_writer.py" set-draft-task-detail \
  --feature "${feature}" --task-id T001 --body-stdin
```

详情不得改写 group-owned 字段、`scope.pages` 或 `scope.workspaceRoots`；这些由 writer 投影。先选择路径格式或验证边界最复杂的一个任务执行一次 `set-draft-task-detail`，并用 `show-task-draft` 核对投影；成功后再批量补齐其余详情。该命令对单个详情原子校验，失败不落盘。所有 task ready 前，`preflight-task-draft` 出现 `draft_task_not_ready` 属于预期，不能把它当作详情格式失败；不读取或手改 Draft JSON。

### 2. 配置工程命令、预检与发布

为每个实际存在 `executionMode=code` 任务的 lane 配置一条 required 编译命令；`verified_existing` 和 `external_dependency` 不需要编译命令。质量门和项目级 E2E 是按需补充；多仓库时每条命令都显式指定正确的 `--repo <workspaceRef>`。

```bash
python "${pluginPath}/hooks/plan_writer.py" add-compile-command --feature "${feature}" --lane backend --command "<BACKEND_COMPILE_OR_BUILD>"
python "${pluginPath}/hooks/plan_writer.py" add-compile-command --feature "${feature}" --lane frontend --command "<FRONTEND_COMPILE_OR_BUILD>"
python "${pluginPath}/hooks/plan_writer.py" add-quality-gate-command --feature "${feature}" --lane backend --command "<BACKEND_LINT_OR_STATIC_CHECK>"
python "${pluginPath}/hooks/plan_writer.py" add-project-validation-command --feature "${feature}" --command "<FINAL_E2E_COMMAND>" --cwd "<GIT_ROOT_RELATIVE_CWD>" --kind e2e_test --repo "<workspaceRef>"
python "${pluginPath}/hooks/plan_writer.py" preflight-task-draft --feature "${feature}"
python "${pluginPath}/hooks/plan_writer.py" finalize-task-draft --feature "${feature}"
```

finalize 会通过事务一次性写入根 `plan.json`、所有 Batch 计划与 `PLAN.md`。根计划不含 `tasks`；每个 Batch 由 `compileProfiles` 投影出唯一 `compileCommand`，并按需带 `qualityGateCommands`。Draft 未完整通过时，不写任何正式产物。

## 修复与变更

`preflight-task-groups` / `preflight-task-draft` 使用 Design 锁和当前 Draft 校验引用、DAG、Batch、workspace、场景覆盖和验证命令。读取返回的 `validation.issues`、`validation.invalidTaskIds` 和每项 `repairSuggestion`：

- `repairTarget=design_revision`：停止 Plan，回 `/autodev-design` 修订并重锁。
- `repairTarget=task_group`：只修分组；分组 digest 变动后用 `rebuild-task-draft`，不要把修改同步进旧 Draft。读取返回的 `preservedTaskIds` 和 `resetTaskIds` 后，只重填 reset 的详情；不得假定 rebuild 会重置全部或保留全部任务。
- `repairTarget=task_detail`：只修对应详情，使用 `repair-draft-task` / `repair-draft-tasks` 后重跑预检。
- `repairTarget=draft_integrity`：按错误恢复；不得为了规避错误删除 `.tmp/plan_writer`、删除 Draft 或全量重填 task。

Draft 已创建后，`specRefs`、`touches`、`deps`、`workspaceRef`、`splitRationale`、`validationBoundary` 等均为 group-owned 字段。只有返回 `repairTarget=task_group` 时才编辑候选分组并 rebuild；`scope.paths`、implementationPoints、acceptanceCriteria、task validationCommands 等 `task_detail` 问题不得改动候选分组。rebuild 的返回值是唯一的重填清单：**只**重填 `resetTaskIds`，不得重填 `preservedTaskIds`。若发现 specs 或 design 本身需要改动，停止 Plan，回到对应上游阶段；不得在 Plan/Draft 阶段直接修改它们。

已 finalized 且尚未执行的计划，先运行 `diagnose-plan-repair`；仅在允许修复时使用 `reopen-finalized-draft --reason <reason>`，局部修复后 `finalize-task-draft --force`。返回 `plan_revision_required` 时转入计划修订；只有 `full_rebuild_required` 才能全量重建。

## 阶段门与完成

finalize 后先运行机器阶段门，再按渲染出的协议进行回检；回检改动产物后重跑阶段门。

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.plan
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
