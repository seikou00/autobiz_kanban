---
name: autodev-plan
description: 基于已确认技术设计生成可执行任务计划、批次计划和 PLAN.md。
version: v2.4.0911
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

模型只生成紧凑输入合同，不能生成或回传完整 `plan.json`、Batch JSON、Draft JSON 或 `PLAN.md`：

- 候选分组使用 `autodev.plan-core.v1`（模板中的 `tasks[]`）：`outcome` 合并标题、`dependsOn` 合并依赖、`writeSet` 是唯一候选写集、`refs` 合并需求/场景/API 引用，`validation.seam` 取代独立验证边界字段。仅任一粒度软上限超出时，才写 `validation.mergeJustification`；它必须点名完整场景路径和相关 ID。writer 自动投影成运行时兼容的 group contract。
- 单 Task 详情使用 `autodev.plan-detail.v1`：`context`、`implementation`、`acceptance`、`checks`、`refs`。`outcome` 默认复用 Plan Core，避免重复生成；只有需覆盖时才显式传入。验收 ID、命令 ID、命令 cwd、workspace roots、lane、Batch 和 `PLAN.md` 均由 writer 派生。
- 需要上下文时只运行 `show-draft-task-work --task-id T001`，它只返回该 Task 的 compact core、当前 detail、Draft revision 和 writer-owned 字段；不得读取全量 Draft 后再输出全量替换。

兼容运行时字段只在 writer 投影和审阅中出现：候选表使用 `refs.requirements`、`refs.scenarios` 和“可合并（附 `validation.mergeJustification`）”；不得输出 `specRefs`、`mergedScenarioRefs`、`splitRationale`、`uiRequired`、`uiRefs` 或 `validationBoundary`。非 UI Core task 不写 `ui`，writer 投影为 `uiRequired:false`。最终的 `validationCommands[].cwd` 保持 Git 根相对路径，由 writer 按 `scope.workspaceRoots` 派生或校验。

开始候选分组前，必须完整读取：

- `${pluginPath}/skills/autodev/autodev-plan/templates/task-groups.json`
- `${pluginPath}/skills/autodev/autodev-plan/templates/task-detail-input.json`
- [任务拆分与计划语义](references/task-planning.md)
- [Detail 字段速查](references/detail-cheatsheet.md)

`templates/task-input.json` 仅保留为弃用提示，**不是**输入模板，也不包含旧运行时字段。实际输入只能来自前述的 Core 与 Detail 两份模板；不得从历史 `plan.json` 复制 `specRefs`、`mergedScenarioRefs`、`splitRationale` 等字段。

每次 Plan 会话只运行一次以下只读命令。它是字段、枚举、模板路径、分批、workspace 和校验规则的权威来源；不要查 `--help`，也不得读取 writer 源码来发现参数或枚举值。

```bash
python "${pluginPath}/hooks/plan_writer.py" add-task-contract
```

在写入候选分组前，先完成下列只读盘点，并把结论带入覆盖矩阵和候选分组表：

- 从 `proposal.md` 的影响模块、`scope.md`、`UI_CONTEXT.json` 和实际代码仓库确认所有相关 Git 根（含前端）；缺少某个实际 Git 根时先向用户确认，不得按 backend-only 猜测。
- 建立 `workspace -> 实际 Git 根 -> workspaceRoot -> 路径写法` 映射；writer 会投影运行时 `workspaceRef`。Core 的 `writeSet.path` 使用 `repoId:relative/path`，默认根不带前缀；不得写绝对路径或重复 workspace root。
- 建立写入归属表，只检查 Core 的 `writeSet`；writer 才会投影 `touches`、`scope.paths` 和 `expectedFiles`，Detail 阶段才补 `implementation`。跨 Batch 的共享 Controller/Service 默认只保留一个前置 owner；若确为互不重叠的稳定方法，Core 的同一 `writeSet.path` 可分别声明 `symbols: ["Class#method"]`，每个 symbol 只能有一个 owner。SQL、路由、协议或全局配置仍必须单 owner；消费者移除该路径并通过 `dependsOn` 消费其结果。
- 先为每个候选任务判定 `mode`。`verified_existing` 仅表示本任务不做业务代码改动：通常保持 `writeSet: []`，用真实的 `validation.seam`、`refs.scenarios` 与后续 Detail 的 `checks`、`implementation` 声明验证表面；不要把仅供验证阅读的现有文件伪装成写入归属。

不要用覆盖不完整的“2-task mini group”作为真实 Feature 的 `preflight-task-groups` 冒烟样本：该预检还会校验完整场景覆盖。路径和字段形状以本节模板、`add-task-contract` 和完整候选分组的预检为准。

## 生成流程

### 1. 候选分组与 Draft

按参考文档先输出覆盖矩阵和最终候选任务分组表。分组必须已覆盖本轮全部 SCN，且已完成写入归属审查；每个 Core task 只能绑定一个实际 Git 根，多仓库行为拆 task 并使用真实 `dependsOn`。通过 stdin 提交完整 Core；`write-task-groups` 会在内存中完成预检，**仅在全部通过后**原子写入 Feature 的 `.tmp/plan_writer/task-groups.json`：

```bash
python "${pluginPath}/hooks/plan_writer.py" write-task-groups \
  --feature "${feature}" --body-stdin
```

写入结果中的 `groupFile` 是后续命令唯一可用的 Core 源。预检失败时不会覆盖旧文件；不要用 `write_file`、heredoc 或复用 `/tmp` 文件。只修候选分组；`oversized_plan_task_must_split`、`missing_plan_task_split_rationale` 或 `invalid_plan_task_split_rationale` 都回覆盖矩阵重新分组。通过后创建并锁定 Draft：

```bash
python "${pluginPath}/hooks/plan_writer.py" prepare-task-draft \
  --feature "${feature}" \
  --group-file "${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/task-groups.json" \
  --code-workspace "<ACTUAL_CODE_WORKSPACE>"
```

Draft 的 `.tmp/plan_writer/draft/plan.json` 和 Batch 草稿由 writer 创建；正式根计划和 `plans/Bxxx/plan.json` 在 finalize 前不得存在。Core 先选定 `mode=code|verified_existing|external_dependency`（writer 投影为运行时 `executionMode`）后，按 Task ID 补完整详情：

```bash
python "${pluginPath}/hooks/plan_writer.py" set-draft-task-detail \
  --feature "${feature}" --task-id T001 --body-stdin
```

详情不得改写 group-owned 字段、`scope.pages` 或 `scope.workspaceRoots`；这些由 writer 投影。单任务探索可用 `lint-draft-task-detail --task-id <id> --body-stdin`。首次全量提交时，必须将每个 Task 的 Detail 放进同一个 `{ "details": [{"taskId":"T001","body":{...}}] }` payload，先运行 `lint-draft-task-details --full --body-stdin`，再将**同一 payload**交给 `set-draft-task-details --full --body-stdin`。两者共用同一候选 Draft 和聚合预检：它会同时检查 implementation、AC/SCN、matrix、写集、命令、Design 和覆盖；任一项失败时两者都不写入。非 `--full` 批量写入只用于 rebuild 后的 `resetTaskIds` 局部重填。避免复用 `/tmp/T001-detail.json`；不读取或手改 Draft JSON。

### 2. 配置质量门、预检与发布

批次编译不属于 Plan 合同。质量门和项目级 E2E 是按需补充；多仓库时每条命令都显式指定正确的 `--repo <workspaceRef>`。

```bash
python "${pluginPath}/hooks/plan_writer.py" add-quality-gate-command --feature "${feature}" --lane backend --command "<BACKEND_LINT_OR_STATIC_CHECK>"
python "${pluginPath}/hooks/plan_writer.py" add-project-validation-command --feature "${feature}" --command "<FINAL_E2E_COMMAND>" --cwd "<GIT_ROOT_RELATIVE_CWD>" --kind e2e_test --repo "<workspaceRef>"
python "${pluginPath}/hooks/plan_writer.py" preflight-task-draft --feature "${feature}"
python "${pluginPath}/hooks/plan_writer.py" finalize-task-draft --feature "${feature}"
```

finalize 会通过事务一次性写入根 `plan.json`、所有 Batch 计划与 `PLAN.md`。根计划不含 `tasks`；每个 Batch 只按需带 `qualityGateCommands`。Draft 未完整通过时，不写任何正式产物。

## 修复与变更

`write-task-groups` / `preflight-task-groups` / `preflight-task-draft` 使用 Design 锁和当前 Draft 校验引用、DAG、Batch、workspace、场景覆盖和验证命令。读取返回的 `validation.issues`、`validation.invalidTaskIds` 和每项 `repairSuggestion`：

- `repairTarget=design_revision`：停止 Plan，回 `/autodev-design` 修订并重锁。
- `repairTarget=task_group`：只修分组，**保留现有 `task-groups.json`**。如果 Draft 尚未创建，使用 `create-repair-work --group-file <file>`；如果 Draft 已创建，将同一命令的输出交给 `apply-draft-patch`。工单只会开放 `/groups/<task>/writeSet`、`/groups/<task>/dependsOn` 或 `/groups/<task>/validation.mergeJustification` 等必要路径，并要求 `baseGroupingDigest`（预 Draft）或 `baseRevision`（Draft）。不要删除文件、不要提交完整 Core、不要用全量生成替代修复。若错误不能由这些受限字段安全表达，回覆盖矩阵定位遗漏并重新分组；Draft 重投影后读取 `preservedTaskIds` 和 `resetTaskIds`，只重填 reset 的详情；不得假定 rebuild 会重置全部或保留全部任务。
- `repairTarget=task_detail`：先生成受限修订工单，再只提交该工单允许的增量 patch。将 validator 输出或评审输出传给：

```bash
python "${pluginPath}/hooks/plan_writer.py" create-repair-work \
  --feature "${feature}" --feedback-file "<VALIDATION_OR_REVIEW_JSON>"
```

返回的 `issues[]` 是模型唯一允许读取的失败上下文：包含 `reasonCode`、失败原因、Task、字段、当前值哈希、`allowedOps` 与成功条件。模型必须返回 `autodev.plan-repair-patch.v1`，带 `workId`、`baseRevision`、`resolves` 和每条 `replace` 的 `expectedHash`，再执行：

```bash
python "${pluginPath}/hooks/plan_writer.py" apply-draft-patch \
  --feature "${feature}" --patch-file "<PATCH_JSON>"
```

预 Draft 的分组错误改为传入现有 Core 文件：

```bash
python "${pluginPath}/hooks/plan_writer.py" create-repair-work \
  --feature "${feature}" --group-file "<TASK_GROUPS_JSON>"
```

它返回 `source.groupingDigest`；patch 使用 `baseGroupingDigest`，其 `ops` 仍只能是 `allowedOps` 中的 `replace`。patch 不能修改未列出的路径、其他 Task、Batch 或 `PLAN.md`。revision、grouping digest 或字段哈希不匹配时，重新生成 repair work，绝不能复用旧 patch、删除 `task-groups.json` 或退化为全量重写。旧的 `repair-draft-task` / `repair-draft-tasks` 仅用于兼容，不应用于新的模型修订流程。
- `repairTarget=draft_integrity`：按错误恢复；不得为了规避错误删除 `.tmp/plan_writer`、删除 Draft 或全量重填 task。

Draft 已创建后，候选 Core 的 `refs`、`writeSet`、`dependsOn`、`workspace`、`validation.seam` 与 `validation.mergeJustification` 均为 group-owned；writer 投影出的 `specRefs`、`touches`、`deps`、`mergedScenarioRefs`、`splitRationale`、`validationBoundary` 也不得在 Detail 中修改。只有返回 `repairTarget=task_group` 时才编辑候选分组并 rebuild；新流程优先使用受限 patch，writer 自动重投影而不是要求模型重建 Draft。`scope.paths`、implementationPoints、acceptanceCriteria、task validationCommands 等 `task_detail` 问题不得改动候选分组。重投影的返回值是唯一的重填清单：**只**重填 `resetTaskIds`，不得重填 `preservedTaskIds`。若发现 specs 或 design 本身需要改动，停止 Plan，回到对应上游阶段；不得在 Plan/Draft 阶段直接修改它们。

已 finalized 且尚未执行的计划，先运行 `diagnose-plan-repair`；仅在允许修复时使用 `reopen-finalized-draft --reason <reason>`，局部修复后 `finalize-task-draft --force`。若 diagnosis 返回 `full_rebuild_required`，表示 Design 与 Core digest 同时漂移：保留原 Draft 和 Core 文件，执行 `rebuild-finalized-draft --group-file <file> --design-revision-confirmed --reason <reason>`，只重填返回的 `resetTaskIds`，再 `finalize-task-draft --force`。任何 execution blocker 都必须转入 `plan_revision_required`，不得重建。

## 产物契约预检（机器校验）

`write-task-groups` 和 `preflight-task-groups` 会一次性返回结构、范围、粒度、写集归属、Design 锁与引用、Spec/SCN 实体引用和场景覆盖的全部独立问题；每项带 `validationStage`、Task、字段和修复建议。输出 `grouping.detailObligations` 时，先按其要求生成 matrix Detail（SCN 超过 5 时恰好一条 required 命令；省略 `covers` 让 writer 自动覆盖全部 AC）。首次提交详情必须先用 `lint-draft-task-details --full` 校验，再用 `set-draft-task-details --full` 原子批量写入：任一 Task 或跨 Task 聚合校验不通过时不会写入任何详情。它们是产物契约预检（机器校验），只修返回的 Task 与字段，不要依赖逐条报错来猜测其余约束。

## 阶段门与完成

finalize 后运行机器阶段门；失败项按返回的 `artifact` / `target` / `problem` / `action` / `route` 修完重跑，通过后推进 `plan_done`。本阶段不重复审查 design.md。

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
