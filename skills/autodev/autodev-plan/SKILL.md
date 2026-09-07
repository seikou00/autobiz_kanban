---
name: autodev-plan
description: Dev 阶段执行计划生成。
version: v2.2.0906
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
```

# /autodev-plan - Executable Task Plan

本技能只负责 Plan 流程的后半段：基于已确认的 `design.md` 拆分任务，并生成 `plan.json`、全部 Batch `plans/Bxxx/plan.json` 与 `PLAN.md`。探索、接口/数据/技术决策和 `design.md` 的生成、裁定由上一节点 `/autodev-design` 负责。

进入本技能时读取当前状态与输入契约：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
```

- 只读取已经确认的 `proposal.md`、`specs/**/*.md`、`UI_CONTEXT.json`、`design.md`、`.design-contract.lock.json` 与实际代码仓库；不重写行为契约或技术设计。
- `.design-contract.lock.json` 是 Design 完成时生成的唯一机器契约。Plan 只消费其中的 API / Data / D ID 与标记，绝不重新解析或校验 `design.md`；缺锁或锁无效说明上游 Design 未完成，停止并回到 `/autodev-design`。`x-auto-no-http-api: true` 与 `x-auto-no-sql: true` 代表对应设计项不存在，任务只能据此写空引用数组，不能补造 ID。
- 若当前为 `design_done`，先进入本节点；已有 Feature 处于旧的 `plan_in_progress` 时如缺少 Design 锁，必须先回 `/autodev-design` 补齐，不能在 Plan 兼容生成。
- 进入时使用 `write_todos` 建立：`建立覆盖矩阵与候选分组` / `生成 Draft 任务详情` / `生成 plan.json 与 PLAN.md` / `推进 plan_done`。本阶段不生成业务代码、测试、迁移或配置。

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_in_progress --stage "执行计划（来源: 技术设计）"
```

```text
design_done
    ↓
/autodev-plan
    ↓
plan.json + PLAN.md
    ↓
plan_done
```

## 固定并行 Plan Workflow（强制）

进入 `/autodev-plan` 后，无论 capability 或候选 Task 数量是多少，**必须立即由仓库固定的 `plan-generation.workflow.js` 调度整个 Plan 产物生成**。它覆盖候选分组、Draft 创建、Task detail、工程命令、Draft 预检和正式发布；并行只发生在候选分组/Task detail 提案，Draft 与正式计划仍由 Workflow 内的唯一 coordinator 串行、原子写入。

父会话只允许执行四件事：推进 `plan_in_progress`、解析实际代码 workspace、准备并启动 Workflow、在 Workflow 成功后运行 Plan 阶段门和推进 `plan_done`。父会话不得手工调用 `plan_writer.py`、`plan_generation_launcher.py` 来生成或修复任何 Plan 产物，也不存在串行回退路径。先准备固定脚本，得到 `workflowScriptPath` 和完整 `workflowArgs`：

```bash
python "${pluginPath}/hooks/plan_workflow_launcher.py" \
  --workspace "${pluginWorkspace}" --feature "${feature}" \
  --code-workspace "<ACTUAL_CODE_WORKSPACE>" --max-parallel 3 --json
```

只有 launcher 返回 `ok=true`、`useWorkflow=true`、`executionMode=fixed`、`canStartWorkflow=true` 且 `requiredAction=start_fixed_plan_generation_workflow` 时才能启动。将它**直接作为顶层 Workflow tool** 启动；传入的是该 tool 的原生参数对象，`args` 必须是 launcher 返回的完整对象，绝不能 `JSON.stringify`：

```javascript
// Workflow tool parameters — not JavaScript to run inside another workflow.
{
  scriptPath: launcher.workflowScriptPath,
  args: launcher.workflowArgs
}
```

不得创建 wrapper Workflow 再启动 Plan Workflow。若确实是在某个 workflow 脚本中调用子 workflow，平台签名是 `await workflow({ scriptPath: "…" }, childArgs)`；把 `args` 放进第一个 `{ scriptPath, args }` 对象会被忽略，但本阶段不允许这种 wrapper。

`plan-generation.workflow.js` 是由 launcher 物化到 `artifactWorkspace/.cmbdevclaw/workflows/<feature>/` 的唯一执行体。不得调用 Python launcher 代替 Workflow、不得改用插件源码路径、不得内联 JavaScript、不得重建或删改 `workflowArgs`；任一启动条件不满足时停止并处理 launcher 返回的恢复动作。

Workflow 返回 `{ok:true, finalStatus:"finalized"}` 前，父会话不得执行本技能后续任何 `plan_writer.py`、`stage_gate.py` 或 `update_checkpoint.py --checkpoint plan_done` 命令。它返回 `needs_repair`、输入摘要失效或启动条件不满足时，保留 run/Draft/worker proposal；在修复上游输入或对应 proposal 后，以同一 launcher 输出重新启动**同一固定 Workflow**，由 `ensure` 复用可恢复的 run。禁止改为父会话串行补写。

固定脚本按以下边界执行：

1. Launcher 锁定 `design.md`、全部 specs、每个代码仓库 Git SHA/工作区状态和 writer/template 摘要，创建或恢复 `.tmp/plan_generation/<runId>/manifest.json`。
2. 分组 worker 按 capability 并行，只向 `group-proposals/` 登记带 `snapshotDigest` 的提案；单一 reducer 收口 Scenario 覆盖、DAG、workspace、写集 owner 与稳定 Task ID，随后由 launcher 运行 `preflight-task-groups` 和 `prepare-task-draft`。
3. Detail worker 按已经冻结的 `Txxx` 并行，只向 `detail-proposals/` 登记完整详情；launcher 使用 `set-draft-task-details` 对全部详情一次校验、一次提交。任一 detail 不合法时，Draft 保持原样。
4. 单一 coordinator 配置工程命令、运行 Draft 预检与 `finalize-task-draft`；只有这一段可以生成正式 `plan.json`、Batch 计划和 `PLAN.md`。

每次 worker 最多重试两次。超时或 worker 失败只重跑相应 partition / `Txxx`；`status` 可查看 pending 项，复用同一输入摘要时再次启动固定 Workflow 会继续原 `runId`。Design、spec、代码仓库或 writer/template 摘要变化时 run 变为 `invalidated`，禁止复用旧 proposal，必须回到相应上游阶段。预检/finalize 失败会保留 Draft 并进入 `needs_repair`，修复后恢复同一 run；禁止删除 Draft 或直接改正式 JSON。

运行控制命令仅用于查看、取消或显式恢复，不直接写计划：

```bash
python "${pluginPath}/hooks/plan_generation_launcher.py" status --workspace "${pluginWorkspace}" --feature "${feature}" --run-id "<runId>"
python "${pluginPath}/hooks/plan_generation_launcher.py" cancel --workspace "${pluginWorkspace}" --feature "${feature}" --run-id "<runId>" --lease-token "<leaseToken>"
```

## Workflow 内部的 Plan 生成契约

以下拆分算法、writer 命令和修复规则是固定 Workflow 的 worker/coordinator 必须遵守的内部协议，供其 Agent 调用；**不是父会话可替代 Workflow 逐条执行的步骤**。

#### 生成 plan.json + PLAN.md

本阶段必须生成完整的 plan.json + PLAN.md，并同时生成全部 `plans/Bxxx/plan.json`。不得只生成第一批并等待 Code 跑完后再规划下一批。`plan.json` 只保存 feature 状态、任务集封口状态、批次索引、批次状态、lane 级 `compileProfiles`、可选 `qualityGateProfiles` 和跨批次项目验证，不得包含 `tasks`；每个 `plans/Bxxx/plan.json` 保存该批任务契约、task 状态、投影后的唯一 `compileCommand` 与可选 `qualityGateCommands`。`PLAN.md` 是从 `plan.json` 投影，并包含全部批次计划中的任务摘要；行为冲突以 `specs/**/*.md` 为准，技术冲突以 `design.md` 为准。

生成或修改 `plan.json` / `PLAN.md` 必须由 Workflow 内部使用 `${pluginPath}/hooks/plan_writer.py` 完成。不得直接整份写入或编辑这些 JSON；`PLAN.md` 必须由 `plan_writer.py render-md` 从 `plan.json` 生成。调试只使用 writer 的 `validate` / `show --summary`，不要把整份 JSON 打进上下文。运行 `init` 前必须先确认目标产物是否已存在；writer 默认拒绝覆盖已有非空产物，只有在明确需要重建并理解会丢弃旧内容时才传 `--force`。

生成计划时必须完整读取 `${pluginPath}/skills/autodev/autodev-plan/templates/task-groups.json` 和 `${pluginPath}/skills/autodev/autodev-plan/templates/task-detail-input.json`。先定位本期实际涉及的全部代码仓库，对每个 `--code-workspace` 执行 `git rev-parse --show-toplevel`，以 Git 根目录名作为稳定 `workspaceRef`；前后端或同一 lane 涉及多个仓库时必须全部登记，不得因当前 cwd 位于某一仓库就遗漏其他仓库。再把最终候选分组表写入 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/task-groups.json`；分组表是 `id/title/deps/uiRequired/workspaceRef/specRefs/mergedScenarioRefs/apiIds/uiRefs/splitRationale/validationBoundary` 的唯一事实源。每个 group 必须且只能绑定一个实际实现仓库；一个行为需要修改多个仓库时必须拆成多个 TASK 并用 deps 表达顺序，禁止单 TASK 跨仓库。每个 `validationBoundary` 必须是具体、非空的公开 seam 与可执行校验边界，不得保留模板占位文本。禁止创建 `.tmp/plan_writer/tasks/Txxx.json` 或任何独立完整 task 副本。writer 会从分组表直接创建 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/draft/plan.json` 与 Draft `plans/Bxxx/plan.json`，调用方只补 task detail；正式根 `plan.json` 和 `plans/Bxxx/plan.json` 在 finalize 前不存在。

候选分组必须先做可验证性判断：backend group 若只产出 Entity/PO/DO/DTO/Mapper、配置或脚手架等结构，且唯一校验是 `compile/build` 或文件存在检查，则不得独立成 TASK；在不跨 workspace/lane 且不突破粒度上限时，合并到最早消费它的下游行为 group，并重排 ID/deps。只有能在不依赖后续 TASK 的情况下，通过真实的 behavior/integration/static 契约测试验证的数据迁移、ORM、序列化或 Schema 契约，才可保留为独立 backend TASK。frontend group 可按 frontend validation profile 使用 compile/build/typecheck 验证页面工程能成功编译，但不得把该命令伪装成 behavior test。此判断必须在 `preflight-task-groups` 和创建 Draft 前完成，不得在 task detail 阶段用空 `validationCommands`、伪 `static_check` 或占位命令兜底。Plan 仍必须生成测试相关的 `validationTestPlan` 和 `testIntent`，但这些内容不表示 Code 阶段创建或执行测试。

每次 Plan 会话准备 Draft 前只执行一次以下只读命令；该操作仅由固定 Workflow 的 coordinator 执行，并以其 JSON 输出获取分组/详情模板路径、group-owned 字段、合法 validation kind、AC 覆盖规则和 Draft 工作流。后续复用该 contract，不重复查 `--help`，不得读取 writer 源码来发现参数或枚举值：

```bash
python "${pluginPath}/hooks/plan_writer.py" add-task-contract
```

writer 自动分组，调用方不指定 batch。`executionLane` 由 writer 根据 `uiRequired` 自动推导：`false=backend`、`true=frontend`，调用方不得自行维护该字段。`task-groups.json` 必须按 DAG 拓扑序排列全部 backend group，再排列全部 frontend group；frontend group 可以依赖更早的 backend group，backend group 不得依赖 frontend group。writer 以第一个 `specRefs` 中 `#` 前的文件路径作为主 capability；只有与紧邻前一批的主 capability 和 execution lane 都相同且该批少于 5 个任务时才合批，否则创建下一 `Bxxx`。因此即使最后一个 backend batch 未满，首个 frontend task 也必须新建 batch。不得伪造 batch ID，也不得通过调整 `specRefs` 顺序伪造分组结果。

**共享写集所有权（硬约束）**：`touches` 是物理写入文件的 owner 声明；同一 `workspaceRef` 下一个路径不得跨多个自动 Batch 声明，`scope.paths` 与 `expectedFiles` 也必须保持这一规则。一个 Batch 内的 TASK 由同一队列顺序执行，但共享 SQL、路由或全局配置仍应优先收敛为该 Batch 的一个前置 owner Task。不得让多个 capability Batch 都写“统一脚本末尾追加”、路由注册表、协议文件或全局配置，再期待 scheduler 自动并行。若多个能力需要同一共享脚本，先生成一个可独立验证的前置 owner Task（数据库/全局配置通常标 `executionStage=global`），让它一次性完成该文件的所有 DDL/seed/配置改动和对应验证；消费者只通过 `deps` 依赖 owner，且不得再在 `touches`、详情路径、预期文件或实现要点中声明修改该文件。writer 会在分组预检、Draft 预检和正式 Bundle 校验中拒绝跨 Batch 的多 owner 写集。

最终候选分组表完成后，先运行只读分组预检。`task-groups.json.uiRequiredExample` / `add-task-contract.taskGroupUiRequiredExample` 是 `uiRequired:true` 的完整分组示例，`task-groups.json.matrixExceptionExample` / `add-task-contract.taskGroupMatrixExceptionExample` 是 6-12 个 SCN 共享同一验证闭环时的分组例外示例；两者都只用于指导，不是 `groups[]` 的实际成员。该命令只校验拆分所需的完整路径级 `specRefs`、SCN/API/Page/UIX/VIS/route、DAG/lane 顺序、`mergedScenarioRefs`、`splitRationale`、`validationBoundary` 和完整 Scenario 覆盖，不要求 goal、scope、AC、decisionIds 或完整 validation command：

```bash
python "${pluginPath}/hooks/plan_writer.py" preflight-task-groups --feature "${feature}" --group-file "${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/task-groups.json"
```

分组预检失败时只能修改候选分组，不得准备 Draft。`oversized_plan_task_must_split` 必须先拆分；不得先补 AC、VAL、decisionIds、scope 或 implementationPoints。禁止看到 6-12 个 SCN 就为所有 group 自动补 `mergedScenarioRefs` / `splitRationale`，也禁止按连续 SCN 编号机械切块；必须先在候选分组表证明共享验证闭环，确认这些 SCN 共享同一用户动作、公开 seam 和自动化验证边界，否则按业务闭环继续拆分。

分组预检成功后立即创建并锁定 Draft Batch；`prepare-task-draft` 会保存 `groupingDigest`，投影全部 group-owned 字段和自动 Batch，不需要也不接受 task 目录。`.design-contract.lock.json` 已由 Design 阶段写入且不在 `.tmp/plan_writer` 下；删除临时 Draft 不会影响它，Plan 也无权生成或刷新它。Design 变更必须先回 `/autodev-design` 完成重锁，再重建或重开 Draft。下例以当前项目根为代码仓库；涉及多个仓库时，为每个实际绝对路径重复传入 `--code-workspace`：

```bash
python "${pluginPath}/hooks/plan_writer.py" prepare-task-draft --feature "${feature}" --group-file "${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/.tmp/plan_writer/task-groups.json" --code-workspace "<FRONTEND_MODULE>"
```

每个候选分组应显式选择 `executionMode=code|verified_existing|external_dependency`，缺省仅兼容为 `code`。`verified_existing` 表示本 Feature 内已有实现，只允许复用现存可执行验证目标；`external_dependency` 表示行为与验证均由 Feature 外的系统或仓库负责，必须同时写 `externalDependency.system/owner/trackingRefs`，不得配置本地验证命令或待创建测试。外部依赖不是本地 no-code 实现，也不得借创建占位测试把它伪装成已验证。

父会话不得使用串行模式按 Task ID 逐个写入详情。固定 Workflow 必须使用 `set-draft-task-details`，传入所有冻结 Task 的完整详情并作为一次原子提交。详情不得包含 group-owned 字段，`acceptanceCriteria[].id`、`validationCommands[].id`、`scope.pages` 和 `scope.workspaceRoots` 也不得由调用方提供；writer 自动编号、从 `uiRefs.pageRefs` 投影 pages、根据 group `workspaceRef` 只投影该 TASK 对应的 workspace root，并在命令未显式提供时自动补正确的 `repo` 与 `cwd`。禁止为了通过校验把缺失的前端仓库替换成后端 workspace 或 Git 根 `.`。每个 detail 的 `nonGoals` 必须至少包含一条具体、非空的相邻行为或范围排除说明，不得写空数组、`无` 或保留模板占位文本。每次详情在写入 Draft Batch 前完成结构、AC 场景归属、2-6 条 implementation points、nonGoals、cwd/manifest 和 required AC 覆盖校验；Workflow 的原子提交会在全部 task 合格前保持整个 Draft 不变：

```bash
python "${pluginPath}/hooks/plan_writer.py" set-draft-task-detail --feature "${feature}" --task-id T001 --body-stdin
python "${pluginPath}/hooks/plan_writer.py" set-draft-task-details --feature "${feature}" --body-stdin
```

Plan 阶段生成测试意图和验证命令契约，但不生成测试源码目标。测试目标不在 Code 阶段分配或创建；TASK 的 `testIntent` 供后续 UTest/E2E 阶段决定测试文件归属、复用策略和执行顺序。不得填写或推导 `create_in_code`，也不得依据测试文件是否存在来调整任务拆分。

不得直接编辑 Draft 根或 Batch JSON。需要查看进度时只运行 `show-task-draft`；它只返回 ready/pending Task ID 和 Batch 摘要。若分组表在 Draft 创建后改变，所有 Draft 命令返回 `task_group_changed_after_draft_created`；只能运行 `rebuild-task-draft --group-file <file>`，writer 仅保留 group projection 与该 TASK workspace contract 都未变化的 ready task 详情，只重置受影响 task，禁止逐字段同步旧 task。若旧 Draft 缺少 code workspace，修改单个 task detail 无法修复，必须运行 `rebuild-task-draft --group-file <file> --code-workspace <path>`；重复参数可登记多个仓库。

全部 task ready 后运行一次 Draft 全局预检，再原子发布正式 Bundle：

```bash
python "${pluginPath}/hooks/plan_writer.py" preflight-task-draft --feature "${feature}"
python "${pluginPath}/hooks/plan_writer.py" finalize-task-draft --feature "${feature}"
```

正式 Bundle 发布后，scheduler 会按 `executionStage` 和实际写集生成安全波次。`parallel` 阶段只有同仓库写集不重叠的 Batch 才进入同一波；路径相同或父子目录重叠、写集未知的 Batch 会自动串行。`proto`、`global` 和 `integration` 阶段按单 Batch 串行收口，分别用于协议桩、数据库/全局配置和共享入口文件。候选分组中的 `touches` 是 task-planner 的文件级隔离输入，writer 会把它归一化到最终 `scope.paths`，不会把 `touches` 写入正式计划；无法确认的写集必须保守串行，最终仍应补充真实 `scope.paths` 或 `expectedFiles`。共享文件必须在这里已经收敛到唯一 owner，而不是依赖这一层的保守串行兜底。

合并冲突不得通过丢弃一侧改动、`--no-verify`、`ours/theirs` 或跳过提交绕过。合并器会保留冲突 Batch 的插件原生 Worktree，启动单个集成 Agent 在该 Worktree 内对当前主分支执行语义 rebase，逐文件保留双方有效改动并运行 Batch 验证；只有 `resolve` 校验出 clean、无冲突且基于最新主分支的提交后，才会再次执行真实 merge 并释放下游依赖。解决失败则保持 `needs_resolution`，禁止标记完成。

**预检与修复**：

`preflight-task-groups` 和 `preflight-task-draft` 会依据 Design 锁快照校验任务引用、任务结构、场景覆盖、workspace/manifest、验证命令、DAG、backend/frontend 顺序、Batch 投影和 Design 双向覆盖；不会重新校验 `design.md`。Draft 创建时记录所消费的锁摘要，Design 重新锁定后会返回 `confirmed_design_changed_after_draft_created`，当前 Plan 不得继续。

失败时读取返回 JSON 的 `validation.issues` 和 `validation.invalidTaskIds`，每条 issue 包含：
- `reason`: 错误类型（机器可读）
- `repairSuggestion`: 具体修复步骤（中文，直接可执行）
- `repairTarget`: 修复层级（`design_revision` / `task_group` / `task_detail` / `draft_integrity`）

按照 `repairSuggestion` 中的指导执行修复，不要根据编号、标题或数量自行猜测。`repairTarget=design_revision` 表示停止本阶段并回到 `/autodev-design` 修订、确认 design.md，禁止用 Plan 迎合设计；`repairTarget=task_group` 只修候选分组，`repairTarget=task_detail` 只修对应任务详情。不得因为 task detail 错误就删除 Draft 或全量重填 task：调用 `repair-draft-task` / `repair-draft-tasks` 后重复 preflight。批量修复在任一 patch 不合法时整体不落盘。不得删除 `.tmp/plan_writer` 来规避局部错误；若临时 Draft 丢失，必须保留 Feature 级 Design 锁并按错误提示恢复。

finalize 会重跑同一校验并通过事务一次写入正式根计划、全部 Batch 和 `PLAN.md`；失败时不写任何正式产物。正式计划已存在时默认拒绝覆盖；需要修改已 finalized 但尚未执行的计划时，先运行 `diagnose-plan-repair`，再运行 `reopen-finalized-draft --reason <reason>`，随后局部 repair、preflight，最后 `finalize-task-draft --force` 重新物化并重算摘要。若诊断返回 `plan_revision_required`，说明已有执行状态或证据，必须回到计划修订流程；只有返回 `full_rebuild_required`（Draft 缺失或不可校验）时才允许删除并重建 Draft。不得删除 Draft 或全量重填 task。禁止使用 `python -c` 构造 Python dict 或 JSON，也不得混用 Python 的 `True/False/None` 与 JSON 的 `true/false/null`。


除 `executionMode=external_dependency` 外，每个 task detail 必须包含非空 `validationCommands`，writer 会据此生成 `validationTestPlan`/`testIntent`。合法 kind、命令禁令与意图字段以 `add-task-contract` 输出为唯一事实源；不得生成 `create_in_code`、测试文件目标或 Code 阶段测试执行计划。不得通过 validator 失败来探索 schema。

所有 JSON 必须合法，不允许 Markdown、注释、尾逗号或解释性文本。任务依赖只能指向本批更早任务或更早批次任务，禁止前向依赖、backend 依赖 frontend 和跨批环。不得直接整份写入 root/batch 正式 JSON，也不得生成 `plan_v1.json`、`plan_v2.json` 等平行版本；发现根 `plan.json` 含 `tasks`、缺少 `taskSetStatus` / `executionLane` / `compileProfiles` / `qualityGateProfiles`、任一最终 Batch 缺少 `compileCommand` / `qualityGateCommands`，或使用旧 batch strategy 时不迁移、不兼容。validator 会返回 `batch_compile_contract_requires_rebuild` 或 `quality_gate_contract_requires_rebuild`，必须清理并重跑完整 Plan。

`templates/task-detail-input.json` 是唯一 task detail 示例，不包含 ID、标题、依赖、specRefs、apiIds、uiRefs 或 splitRationale。`status`、Evidence 字段和 completionPolicy 也由 writer 设置。`uiRequired` 与全部 UI refs 只写在分组表并由 writer 投影；task detail 的 scope 不写 pages 或 workspaceRoots。批次和项目级验证命令使用结构化 argv/cwd/kind/required；项目级验证只用于确有必要的跨 backend/frontend 或跨批次检查。不得先自由生成再依赖 validator 反复修字段。

任务需要 `splitRationale` 时必须在候选分组表首次定稿时写入；Draft task 由 writer 原样投影，不允许 detail 再维护。

旧 `preflight-task-set --task-dir` / `materialize-task-set --task-dir` 只保留兼容；已有未完成的旧 task 目录可一次性运行 `import-task-directory --group-file <file> --task-dir <directory> --code-workspace <path>` 导入 Draft，新 Plan 不得使用旧流程。

`plan.json` 语义规则：

- Task ID 使用 `T001`、`T002` ...，不跳号、不复用已删除或已完成任务 ID，且在全部批次内全局唯一。
- `taskSetDigest` 保护 writer 生成的根索引和 task 契约；直接编辑正式 JSON 会被后续读取拒绝。`finalize-task-draft` 只写完整覆盖且 `finalized` 的任务集。
- 字段清单、必填项与取值枚举以 `add-task-contract` 为准：`fields` / `conditionalFields` 给字段契约，`workspaceContract` 给 workspace 与 scope 派生规则，`batchAssignment` 给分批与 lane 推导，`taskSetFinalization` 给发布顺序。下面几条是它无法表达的语义判断和易错点。
- 只使用当前结构，不写 `version` / `taskDetailVersion` 字段。发现带版本字段或根含 tasks 的 plan 时，不迁移、不兼容，清理后重新执行 Plan。
- `goal` 写用户可观察结果，不是实现动作；`scope.modules/entrypoints/dataObjects` 写执行范围，`scope.pages` 由 writer 从分组 UI refs 投影。`validationBoundary` 必须描述公开 seam 与可执行校验边界，`nonGoals` 至少一条具体、非空的相邻行为排除，二者都不接受模板占位。
- `scope.workspaceRoots` 由 writer 根据 `prepare-task-draft --code-workspace` 派生，再按 `workspaceRef` 选择唯一仓库；`scope.paths` 只写相对该 workspace 的提示性路径，**不是实现文件白名单**——runner 会从 start 快照自动统计该 workspace 内全部有效生产代码与生产配置变更，DTO、domain、resources、迁移或配置遗漏在 `scope.paths` 中不会导致 TASK abort；测试文件和跨 workspace 变更仍然拒绝。具名 repo 使用 `repoId:relative/path`，禁止再次包含 workspace 前缀。
- `validationCommands[].cwd` 保持 Git 根相对路径，必须等于或位于该 TASK 的 workspace root 下；省略时 writer 自动补 repo 与 workspace root。多仓库计划中每个 TASK validation command 的 `repo` 必须等于该 TASK 唯一 `workspaceRef`；project command 按实际执行仓库填写。所有 evidence 文件仍属于 feature 产物目录。
- 顶层 `compileProfiles` 按实际使用的 lane 配置一条 `kind=compile` 的 required 命令；backend 可使用 `mvn compile`，frontend 可使用 `npm run build`、`pnpm typecheck` 或等价的不执行测试的编译命令。它会投影为每个 Batch 唯一的 `compileCommand`，且只在 implement 阶段执行。顶层 `qualityGateProfiles` 只放 `kind=static_check` 的 lint/静态检查；为空时 Batch 不创建 `quality_gate` 阶段。TASK 的 `validationCommands` 不在 Code 阶段运行。
- 顶层 `projectValidationCommands` 是最终 B-E2E 的可选系统级命令源：它们只在所有 delivery 已合并后的临时 main Worktree 中执行，绝不成为候选 Merge Train 门槛。不得把 batch compile/build 或各 Batch 的 UTest 命令复制进该数组，也不能替代 TASK 的 AC 覆盖。单仓库可留空；多仓库若声明命令，必须为每条命令显式填写对应 `repo`，并确保每个实际 `workspaceRef` 由 E2E intent 或项目命令覆盖。
- Plan 阶段所有任务初始状态为 `todo`，evidence 相关字段为空或 null，这些运行字段只由 task runner 更新。单 Batch Plan 可初始激活 `B001`；多 Batch Plan 由并行 scheduler 从零入度 Batch 集合启动，不写人为 batch handoff。依赖 Batch 必须等其 `deps` 全部合并后才进入下一调度波次。
- 每个任务必须追溯到真实 specs 与已确认 design：`specRefs` 至少覆盖一个 `REQ-xxx` 和一个 `SCN-xxx`；`designRefs`/`apiIds`/`dataIds`/`decisionIds` 只引用 `design.md` 中真实定义的 ID。四个字段在结构上可以存在但取空数组；任务不涉及对应设计项时必须写 `[]`，禁止为了过校验强行编造或按 Task 编号续写 `API-*` / `DATA-*` / `D-*`。模板中的 API/Data/Decision ID 都是占位示例，生成前必须替换为 Design 中真实 ID；不要为了过校验强行编造，未涉及时使用空数组 `[]`。如果 `design.md` 中存在 API/Data/D 决策，则这些设计项必须被至少一个真正相关的任务覆盖；覆盖缺失时把已有 ID 绑定到正确任务，禁止向 design 新增 ID 迎合 Plan。`x-auto-no-http-api` / `x-auto-no-sql` 必须已由 `/autodev-design` 依据真实范围写入；缺失或需要改变时回到设计阶段。
- `specRefs` / `designRefs` 是 feature 产物目录下的逻辑相对引用，必须写成 `specs/<capability>/spec.md#SCN-001`、`design.md#API-001` 这类形式；不要写业务代码仓库相对路径，也不要把绝对产物路径固化进 `plan.json`。Code 阶段会通过 `${pluginPath}/hooks/code_task_context.py` 按 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}` 解析这些引用。
- `validationCommands` 是 task 级测试意图契约，必须窄、快、可追溯；不能确定真实文件时不要凭空填写 `expectedFiles`。Code 阶段不运行 TASK 验证；`executionMode=external_dependency` 仍由后续阶段处理。

用户补充信息沉淀规则：
- 用户补充的任务边界、模块拆分、验证方式或风险，写入候选分组、Draft 与 `plan.json`，并由 writer 同步更新 `PLAN.md`。
- 用户补充接口设计、数据库设计、API/DATA/D 决策或任何会改变 `design.md` 的内容时，停止本阶段并回到 `/autodev-design`。设计重新确认后才能重建或继续 Draft；不得在 Plan 阶段直接改写 design.md。
- 必须在 `plan.json` 对应任务或风险字段中记录用户补充说明 / 技术细节； `PLAN.md`同步新增或更新「用户补充说明 / 技术细节」章节。
- `PLAN.md` 必须从 `plan.json` 投影，任务 id / deps / status / workspaceRef / specRefs / designRefs / validationCommands / evidenceIds 不能漂移；任务的「做什么」「代码工作区」「涉及范围」「执行要点」「验收标准」「不做什么」只能来自 `goal` / `workspaceRef` / `scope` / `implementationPoints` / `acceptanceCriteria` / `nonGoals`，不得在 `PLAN.md` 独写机器事实源没有的内容。
- 如果用户补充内容影响任务拆分、验证方法或风险，应同步更新对应任务。
- 如果用户补充内容与 specs、design.md 或既有系统约束冲突，记录 Plan 阻断原因并回到对应的 `/autodev-specs` 或 `/autodev-design`，不得擅自覆盖上游产物。
- 用户补充的实现细节只能作为计划依据，不得在 Plan 阶段创建或修改业务代码文件。

UI 任务规则：
- `uiRequired` 是 task 顶层 bool 字段，不在 `uiRefs` 内部，每个 task 必须显式写。`uiRefs` 只包含 `pageRefs`、`interactionRefs`、`visualSourceRefs`、`frontendRoute`。
- Plan 开始前必须读取并校验 `UI_CONTEXT.json`；页面、交互和视觉来源 ID 只能来自该 JSON，不得在 Markdown 或 Plan 阶段临时分配 `PAGE/UIX/VIS` ID。
- `uiRequired=true` 时，`uiRefs.pageRefs`、`interactionRefs`、`visualSourceRefs` 必须逐项投影自 `UI_CONTEXT.json`，并保留 `frontendRoute` 的机器判定；同一页面或交互在多个 task 中复用同一 ID。
- 不得按本 Feature 内页面与交互出现顺序自行分配 PAGE/UIX；不得无条件把 `visualSourceRefs` 写空数组或把 `frontendRoute` 写 `spec-driven-ui`，这些值必须来自 UI_CONTEXT 投影与路由解析。
- `uiRequired` 不是 `true` 的任务必须显式写 `uiRequired:false`，且不得带非空 `uiRefs`；纯后端支撑任务只保留业务/设计/验证依据。
- 如果 UI_CONTEXT 标记 `uiRequired=true` 但当前能力还没有可引用的 UI capability，禁止猜测或虚构 UI task，应回到 Specs 补齐 UI capability。
- 仅配置后端菜单、权限或菜单数据且不修改前端页面/路由实现的任务保持 `uiRequired:false`，不得为通过分组预检虚构 UI 引用。真正修改前端页面、交互或路由入口时才标记 `uiRequired:true`。
- 不得为通过分组预检虚构 PAGE/UIX；缺失引用时必须回到 UI_CONTEXT/Specs 修正事实源。
- UI task 的 `scope.pages` 必须与 `uiRefs.pageRefs` 集合一致；非 UI task 的 `scope.pages` 必须为空数组。
- `uiRefs.frontendRoute` 取值为 `none`、`spec-driven-ui`、`absolute-html`、`standard-html` 或 `missing-html`，由 UI_CONTEXT 的视觉来源和路由解析结果决定；HTML 设计稿转换在 `/autodev-code` 的 frontend route 内完成，不再依赖独立的 `frontend_before_specs` profile 或 `/autodev-frontend` 节点。

### Plan Task 拆分算法（生成 plan.json 前必走）

核心：一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令。默认先按 vertical slice 拆开，再按严格条件合并；不要先按 capability、模块或文件层合成巨型任务。

1. 确认本轮实现范围
   - 先按 Source Bundle 与当前 `implementationScope`（如存在）确认本轮 specs 分母；`backend_only` 时不要把已剥离的 UI 场景、页面或交互放进 task 覆盖矩阵，`frontend_only` 时不要把已剥离的后端 API/数据实现放进当前 task。
   - 只从当前实现范围内的 `specs/**/*.md` 与 `design.md` 提取任务依据；不要从被剥离范围、PRD 余量或 Markdown 关键词反推额外任务。

2. 建立 Scenario 覆盖矩阵
   - 写 task 前，必须在对话中输出覆盖矩阵，不得只在脑内跳过。矩阵列：`SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam / 风险或依赖`。
   - 没有进入矩阵的 Scenario 不允许直接生成 task；矩阵中的每个 `SCN-xxx` 最终必须映射到某个 task 的 `specRefs`。

3. 按验证闭环生成候选任务分组表
   - 默认按 specs 中的 Requirement / Scenario、用户主流程或验收闭环拆成“需求任务”，不要按 Controller、DTO、Mapper、SQL、样式文件、测试文件等代码层步骤拆任务；禁止按文件/分层机械拆，但必须按用户可观察的 vertical slice 拆。
   - 不同用户动作、不同公开入口/API/页面/job/CLI、不同可观察结果、不同页面、不同数据模型/状态流/迁移风险、不同验证命令，默认拆成不同 task。
   - 一个任务应交付一个可理解、可执行、可验证的业务闭环；它可以同时涉及接口、服务、数据、前端、测试和配置。
   - 基础能力可以单独成 task，但必须服务于后续业务 vertical slice，并且 `validationCommands` 必须验证下游公开 seam。若只能验证工具类、DTO、Mapper 或内部函数，则并入第一个消费它的业务 task。
   - 准备 Draft Batch 前，必须先输出最终候选任务分组表，不得边补 task detail 边重新拆分。草稿阶段可用标题或 `C001` 标识候选项；进入 writer 前的最终表必须把 taskId 一次性重排为连续 `T001`、`T002`、`T003`...，禁止 `T003a`、`T004b1` 这类临时编号。
   - 最终分组表列：`候选 Task / 完整 specRefs 清单 / SCN 数 / API 数 / Page 数 / UIX 数 / implementationPoints 数 / validationCommands / deps / 拆分结论 / splitRationale 草稿`。
   - 先按 `用户动作 + 公开 seam + 自动化验证边界` 分组，再为每组分配候选 task；不得先按 capability、同一页面或同一模块合并。
   - `SCN 数` 必须从完整路径级 `specRefs` 展开后计数；不同 spec 文件里的同号 `SCN-001` 必须按不同场景分别计数。最终表不得用 `SCN-007~SCN-016`、`SCN-001SCN-003(menu)` 这类范围或拼接文本作为计数依据；每个 SCN 必须单独写为 `specs/...#SCN-xxx`。
   - `拆分结论` 只能写 `通过`、`需拆分`、`可合并(附 splitRationale)`。`需拆分` 行不允许生成 task 输入文件；`可合并(附 splitRationale)` 行必须在分组表中写出完整 `splitRationale` 草稿，生成 task JSON 时原样带入，不得临场改写。

4. 只有共享同一验证闭环时才允许合并
   - 多个 SCN/API/PAGE/UIX 合并到一个 task，必须同时满足：同一触发动作、同一公开 seam、同一验证命令或同一组响应/页面断言（frontend 可共享同一编译门禁）、拆开会复制同一验证闭环、没有超过硬上限。
   - 任务超过软阈值时默认必须继续拆分；`splitRationale` 只允许用于已经按公开入口、用户动作、可观察结果和验证命令拆到最小闭环后，仍因同一请求、同一权限/状态矩阵或同一响应断言无法独立验证的少数例外。
   - 普通 group 的 `mergedScenarioRefs` 保持空数组。SCN 超软阈值时使用 `add-task-contract.taskGroupMatrixExceptionExample` 在候选 group 填写 `specRefs`、`mergedScenarioRefs` 与 `splitRationale`；writer 将三者原样投影到 Draft task。对应 detail 必须恰有一个 required 的 `behavior_test`、`integration_test` 或 `e2e_test` 覆盖全部 AC；`splitRationale` 至少点名 3 个相关 SCN，并说明共享请求/响应、权限或状态矩阵与同一验证闭环。
   - API/PAGE/UIX 超软阈值但未超硬上限时仍可用 `splitRationale`，必须点名相关 API/PAGE/UIX ID，并说明为什么无法独立验证。
   - 标记 `可合并(附 splitRationale)` 前必须逐项确认：不同触发动作已拆开；不同公开 seam 已拆开；不同可观察结果已拆开；不同 validation command 已拆开。任一项未满足时不得标记可合并。
   - 合格示例：`SCN-001、SCN-004、SCN-007 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。`
   - 跨 spec 同号场景必须点名完整路径，合格示例：`specs/menu/spec.md#SCN-001、specs/my-approval/spec.md#SCN-001、specs/apply-report/spec.md#SCN-001 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。`
   - 状态/操作矩阵例外示例：`SCN-006、SCN-007、SCN-008、SCN-009、SCN-010、SCN-011、SCN-012 均由同一个操作权限计算入口返回操作集合，并由同一组状态-操作矩阵断言验证；拆开会复制同一验证闭环。`
   - 不合格示例：`这些都是同一个操作权限判断逻辑。`
   - 不得用“同一模块”“同一 capability”“同一页面”“同一列表”“不同组成部分”“实现方便”“一起实现”“顺手一起”等空泛理由。
   - 硬上限不可豁免：任一维度超过下节两档计数预检列出的硬上限时必须继续拆分，不能用 `splitRationale` 放行。

5. 写入前两档计数预检

   两档阈值的事实源是 `add-task-contract.matrixException`（`normalScenarioMaximum` / `scenarioMaximum`）与 `preflight-task-groups` 的判定，下面的数字与它们同源，改动以脚本常量为准：

   - `拆分结论=通过` 的候选 task 必须满足：SCN `<=5`、apiIds `<=2`、pageRefs `<=1`、interactionRefs `<=3`、`implementationPoints` 为 2-6 条、至少 1 条可独立运行的 `validationCommands`。
   - `拆分结论=可合并(附 splitRationale)` 的候选 task 必须满足：未超过硬上限——SCN 数 `>12`、apiIds `>3`、pageRefs `>2`、interactionRefs `>4` 即越界；至少一个维度超过软阈值；分组表已有完整 `splitRationale`；SCN 超软阈值时还必须有完整 `mergedScenarioRefs`。
   - 最终候选任务分组表不得包含 `拆分结论=需拆分` 的行；超过硬上限、缺少 rationale 或未完成最小闭环确认的候选 task，不得进入 Draft。
   - `task-groups.json` 的分组预检通过后由 `prepare-task-draft` 锁定 digest；内容结构错误由 `set-draft-task-detail` 当场拒绝。若确需改变分组，运行 `rebuild-task-draft`，不得手工同步 Draft Batch。
   - 一个候选组只允许一次拆分：若拆分后仍是同一公开 seam 和同一自动化验证边界，且 SCN `<=12`，使用矩阵例外；若超过 `12` 或存在多个独立用户动作、seam 或验证边界，停止并报告规格/规划冲突。不得输出 `v2`、`v3` 等重复分组表，也不得生成 `T012a`、`T012b1` 等临时 taskId。

6. 写入前预检每个 task 内容
   - `specRefs` 至少包含一个真实 `REQ-xxx` 和一个真实 `SCN-xxx`；不同 spec 文件里的 `SCN-001` 是不同场景，必须写完整 `specs/<capability>/spec.md#SCN-001` 路径，不能只写 `#SCN-001` 造成路径级覆盖缺失。
   - 任务名用业务结果命名，例如“实现订单导出主链路”“支持审批超时提醒”“补齐用户配置保存与回显”，避免“修改某文件”“新增某类”。
   - 不要生成“新增 DTO”“修改 Controller”“补 Mapper”“写单测”这类单纯代码操作任务；不要生成只有“实现某能力”“补充验证”“更新相关代码”这类泛泛描述的任务。
   - 每个任务必须包含「涉及范围」「执行要点」「验证命令」「预期结果」：
     - 「涉及范围」写模块、入口、服务、模型、配置、测试等方向；能确定真实路径时写路径，不能确定时写现有代码中要定位的范围，不要凭空发明文件。
     - 「执行要点」写入 `implementationPoints`，每条是一个可执行动作或关键约束，覆盖实现切入点、关键改动、复用现有能力、边界/失败路径和测试补充；条数上限见上一节的两档计数预检，超限时合并同一实现动作或拆 Task，不得机械删除覆盖点。
     - 「验证命令」必须是后续 UTest/E2E 执行者能直接运行并自行判读结果的命令，窄、快、可单独运行。backend 用精确自动化测试或接口级 `curl`/HTTP 断言脚本，frontend 用真实的 compile/build/typecheck 作为最低门禁；具体 kind 与命令禁令见「生成 plan.json」中指向的 `add-task-contract` 契约。若 backend 当前缺少自动验证手段，则在 `testIntent` 中声明应补的真实测试边界，由后续 UTest 阶段创建测试；Code 不补测试。frontend package script 必须真实存在且不能是 no-op。
     - 「预期结果」写可观察结果，不要只写“通过”。
   - 执行要点要写到可直接开工的可执行程度：钉住真实文件/符号/入口、真实命令与预期结果；但不要拆成 2-5 分钟步骤、完整代码块、逐文件微任务或频繁 commit，PLAN 仍保持需求闭环任务粒度。
   - 测试通常作为每个需求任务的验证方法沉淀；只有跨多个需求的验收闭环、E2E 主链路或质量门禁需要单独编排时，才生成独立验证任务。

7. 生成 DAG 与覆盖检查
   - Batch 以真实跨 Batch `deps` 构成 DAG；依赖仍只能表达真实产出关系，不得为了串行而按编号虚构依赖。没有未完成依赖的 Batch 先进入调度器安全波次：普通 Batch 依据同仓库的 `scope.paths`/`expectedFiles` 做文件级隔离，未知写集保守串行；`proto`、`global`、`integration` 阶段强制单 Batch 收口。每个就绪 Batch 由独立 subagent 在 worktree 执行；当前波次全部真实 merge 后，scheduler 才重新计算下一波并释放下游。共享入口、协议、数据库和全局配置必须在候选分组表中标记对应阶段，并写明专属 Agent 的验证边界。同一 Batch 的 TASK 仍由单一队列逐个完成生产实现，全部成为 `implemented` 后统一编译一次。
   - 任务数不是首要目标：8-15 个清晰 vertical slice 优于 5 个巨型 capability task。超过 15 个任务时才检查是否把代码步骤误拆成任务；禁止为了压低任务数合并独立场景。
   - specs 中每个 `SCN-xxx` 必须至少被一个 task 的 `specRefs` 覆盖；design.md 中的每个 API Decision、Data Decision 和关键 Technical Decision 都必须被实现任务和验证方法覆盖，或明确说明无需实现。

与 writer 的衔接：

- 最终候选任务分组表必须覆盖全部 Scenario，并按 `backend`、`frontend` 两个区段排序。writer 一次创建全部 Draft Batch；不得把剩余 task 延迟到 Code 阶段。Batch 只能包含同一 lane 且同一 `workspaceRef` 的 TASK：前后端绝不共用 Batch，同为 backend/frontend 但仓库不同也必须拆成不同 Batch。
- 必须按 DAG 拓扑序编号：当前 task 的 `deps` 只能指向更早的 task。若分组预检报告依赖错误，只修候选表，不补 task detail。
- `preflight-task-groups` 成功后只运行一次 `prepare-task-draft`，并且必须带真实的 `--code-workspace`。缺少 workspace 时必须先确定业务代码目录，不得创建无 workspace 的 Draft；不得创建独立 `Txxx.json`，不得在每写 5 个 task 后提前 finalize。遇到 `missing_plan_task_split_rationale` 或 `invalid_plan_task_split_rationale` 时，回 Scenario 覆盖矩阵定位遗漏并重新分组。
- 不得通过完整 task 的内容校验失败来探索如何拆分；拆分必须在覆盖矩阵、候选任务分组表和 `preflight-task-groups` 阶段完成。
- 预检失败时读取 `validation.issues`，按每条 issue 的 `repairSuggestion` 执行修复。不要根据 SCN 编号连续性、标题相似度或 API 数量自行猜测应移动哪些 Scenario；不得把缺失 Scenario 添加到标题相近的任务；若要拆分，必须回到覆盖矩阵定位遗漏并重新分组。
- 串行 `set-draft-task-detail` 成功后对应 task 才进入 ready；Workflow 的 `set-draft-task-details` 只在全部 task 成功时统一进入 ready。两种写入失败均不落盘。`show-task-draft` 只看摘要，不读取或编辑 Draft JSON。
- 分组 digest 变化时运行 `rebuild-task-draft`；writer 保留分组投影未变化的 ready task，重置其余 task。不得修改 group 后继续向旧 Draft 写详情。
- 全部 task ready 后，**在 finalize 之前**必须配置工程命令（见下节），然后运行一次 `preflight-task-draft` 和一次 `finalize-task-draft`；未完整通过时正式根计划和批次均不存在。若预检失败，先按 `validation.issues` 定位并修复 Draft，再重新预检，不删除 Draft。
- 对 finalized 计划不原地解封、不直接编辑 JSON（不得绕过 Draft lock 修改正式 Bundle）。先运行 `diagnose-plan-repair`：未开始执行且 Draft 完整时，运行 `reopen-finalized-draft --reason <reason>` 进入可修复状态；修复后使用 `finalize-task-draft --force` 重新物化并重算 `taskSetDigest`、`taskContractSha256ByTask`。若已开始执行，禁止覆盖正式计划并转入计划修订；只有 Draft 缺失或不可校验时才清理并全量重建。
- `validate --structure` 会复核已生成 bundle 的结构、完整性摘要和 Task 粒度，但不替代完整 Scenario 覆盖预检或 `dev.plan` 阶段门禁。

#### 配置工程命令（Draft 阶段，finalize 之前）

**重要**：必需的工程编译命令必须在 Draft 阶段配置完成，finalize 会校验其完整性。项目级 E2E 命令是可选补充，也应在 Draft 阶段声明。finalize 后计划进入只读状态；如需修订，先 `reopen-finalized-draft`，再在 Draft 中重新运行对应命令，最后 `finalize-task-draft --force`。同一 lane + workspace 的编译命令和 required 项目 E2E 命令会被替换而非重复追加；质量门默认可追加多个检查，传 `--replace` 才替换该 workspace 的质量门集合。

为每个实际使用的 lane 添加一条 required 编译命令。若 Feature 需要系统级命令，可为相应 workspace 添加一条项目 E2E 命令；它不是 finalize 前置条件，批次统一使用 `mode=commands`：

```bash
python "${pluginPath}/hooks/plan_writer.py" add-compile-command --feature "${feature}" --lane backend --command "<BACKEND_COMPILE_OR_BUILD>"
python "${pluginPath}/hooks/plan_writer.py" add-compile-command --feature "${feature}" --lane frontend --command "<FRONTEND_COMPILE_OR_BUILD>"
python "${pluginPath}/hooks/plan_writer.py" add-quality-gate-command --feature "${feature}" --lane backend --command "<BACKEND_LINT_OR_STATIC_CHECK>"
python "${pluginPath}/hooks/plan_writer.py" add-quality-gate-command --feature "${feature}" --lane backend --command "<REPLACEMENT_STATIC_CHECK>" --replace
python "${pluginPath}/hooks/plan_writer.py" add-project-validation-command --feature "${feature}" --command "<FINAL_E2E_COMMAND>" --cwd "<GIT_ROOT_RELATIVE_CWD>" --kind e2e_test --repo "<workspaceRef>"
```

工程命令默认复用 `prepare-task-draft` 已锁定的 `--code-workspace`，无需重复传入；仅在需要显式复检某个工作区时传 `--code-workspace`。同一 lane 只使用一个 workspace 时 writer 可自动选择；使用多个仓库时必须为每个 workspace 分别添加 required 编译命令并传 `--repo <workspaceRef>`，writer 只把该命令投影到相同 repo 的 Batch。可选项目 E2E 命令同理：单仓库可以省略 `--repo`，多仓库必须为每条命令显式传 `--repo <workspaceRef>`。未显式传 `--cwd` 时 writer 使用该 TASK/Batch 声明的唯一 workspace 根；显式 `--cwd` 仍是 Git 根相对路径且必须位于 workspace 内。项目命令只在所有 Batch 合并后的 B-E2E 临时 Worktree 中执行，绝不成为候选阻塞门。

Plan 阶段不再生成独立 smoke 计划。每个 Batch 的 Code 编译收口只落在 `compileCommand` 的 required `kind=compile` 命令中；frontend 命令的 argv 可以是 build/typecheck，但不得执行或串联测试。只有声明了 `qualityGateCommands` 时，才在 test 后运行对应的 lint/静态检查。TASK `validationCommands` 继续投影为 `testIntent`，由后续 UTest/E2E 阶段执行。

#### Finalize 与产物生成

所有 Draft 配置（任务详情 + 工程命令）完成后，运行 `finalize-task-draft` 会一次性物化为正式执行产物，包括：
- 根 `plan.json`
- 各 Batch `plans/B*/plan.json`
- `PLAN.md`（自动生成，无需手动 render-md）

finalize 后计划进入只读状态。发现问题需要先运行 `diagnose-plan-repair`，然后 `reopen-finalized-draft --reason <reason>` 进入可修复状态；修复后使用 `finalize-task-draft --force` 重新物化。

完成条件：
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.json` 文件已写入磁盘
- [ ] `plans/B001/plan.json` 起的批次计划已写入磁盘，每批最多 5 个任务，根 plan 不含 tasks
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PLAN.md` 文件已写入磁盘，且从 `plan.json` 投影生成
- [ ] 根 `plan.json` 与各批次计划共同作为任务 DAG 机器事实源，状态投影一致
- [ ] 每个任务已通过 `set-draft-task-detail` 或原子 `set-draft-task-details`，详情符合 `templates/task-detail-input.json`，并能清楚读出业务目标、规格/设计依据、涉及范围、执行要点、强验证命令和预期结果
- [ ] 任务按用户可观察 vertical slice 拆分，不按代码层或文件层机械拆分；超过 15 个 task 时已检查是否误拆到代码步骤，没有为了压低任务数合并独立场景
- [ ] 任务没有停留在泛泛描述；每个任务的执行要点至少有一条钉住真实锚点（文件#符号 / 真实入口 / design.md#API/DATA/D-xxx）
- [ ] 每个任务的「验证命令」都能直接运行并自行判读，没有任何需要人参与的步骤
- [ ] specs 中每个 Requirement / Scenario 至少被一个任务覆盖
- [ ] design.md 中每个接口/数据/技术决策至少被一个实现任务和一个验证方法覆盖，或明确标注无需实现
- [ ] Plan 阶段额外提供的实现细节已写入 plan.json 并更新相关任务或风险项；若它改变技术设计，已回到 `/autodev-design` 完成重新确认。

#### 产物契约预检（机器校验）

这是脚本对产物做的**机器检查**，只判定：必备产物与章节是否齐全、格式与结构是否合法、稳定 ID 是否规范唯一、引用能否解析、机械可判的覆盖关系是否成立。

它**不**判定需求语义是否完整、方案是否合理、测试策略是否充分、代码事实是否属实——那些由回检子代理负责，两者职责不重叠。

确认已有 design.md 未变更且 plan.json、PLAN.md 全部生成完成后执行：

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
```

处理流程：

1. 等命令完整结束后再处理结果，不处理一条就重跑一次。
2. 读取全部失败项。每一项都带 `artifact` / `target` / `problem` / `action` / `route`，按 `action` 修，不要自行推断修法。
3. 按 `route` 分流：`fix_current` 在本阶段修；`return_specs` / `return_plan` 停止本阶段并回流；`ask_user` 回到用户确认，禁止自行填值。
4. 按 `artifact` 归组，一次性改完全部可修项。
5. 重跑完整预检；通过前不得进入回检，也不得推进 checkpoint。

#### 回检与修复

本节完整协议由脚本按阶段渲染,必须先运行下面命令，并完整遵循其输出；不得凭记忆执行本节，也不得跳过该命令。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.plan
```

回检导致产物变化时，重跑一次产物契约预检。

---

## 完成

只有固定 Workflow 已返回 `{ok:true, finalStatus:"finalized"}`，才由父会话执行以下最终阶段门与 checkpoint 推进。任何可恢复失败都回到同一 Workflow，不得补跑这些命令或手工修改计划。

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
