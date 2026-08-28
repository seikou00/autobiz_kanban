---
name: autodev-code
description: 进行代码实现。
version: v1.7.08041
allowed-tools: execute task_output read_file grep glob write_file edit_file
---

# /autodev-code — 代码执行

## 前端 Route 闸门（按 Task 在 Agent 内执行）

前端 Route 闸门属于前端 Task 的实现前置条件，不属于整个 Code Session 的全局入口。Code 入口只负责读取 `IMPLEMENTATION_SCOPE.json`、捕获一次基线并启动 Batch DAG；不得因为 Feature 含有任意前端 Task，就在进入 Code 或启动首个 Batch 前解析 Route。UI 范围以 `UI_CONTEXT.json` 和当前 Task 的 `taskContract.uiRequired/uiRefs` 为机器事实源，Markdown 只作迁移兜底。

每个 Batch agent 在执行 `code_task_context.py` 后，逐个判断当前 Task：

- `taskContract.uiRequired=false`：跳过 Route resolver，不读取 HTML 或 Route SKILL，直接按后端 Task 协议实现。
- `taskContract.uiRequired=true`：必须在该 Batch agent 内、写前端源码前完成下面的 Route 协议；不得由宿主 Code 入口代跑，也不得把后端 Task 的 Route 证据当作前端 Task 的证据。

1. 前端 Task 在其 Agent 内解析并记录 route：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --workspace "{ARTIFACT_WORKSPACE}" --feature "{FEATURE_ID}" --start-route-run --json
```

active Task 已绑定的 HTML 必须来自 `UI_CONTEXT.json` 的 `visualSourceRefs`，由 resolver 从 Feature 内 `frontend-html/VIS-xxx/` 读取；不要用本轮 `--html-file` 替换 required VIS。只有没有 active Plan 绑定的兼容迁移场景，才允许追加 `--html-file`。

2. 仅当前端 Task 按输出的 `route` 读取 route SKILL 到 EOF：
   - `route=absolute-html`：完整读取 `skills/autodev/autodev-code/references/frontend-html/with-absolute-html/SKILL.md`
   - `route=standard-html`：完整读取 `skills/autodev/autodev-code/references/frontend-html/with-standard-html/SKILL.md`
   - `route=spec-driven-ui`：当前 active UI Task 的 `visualSourceRefs=[]`，按 specs/design/plan 实现前端；不读取 HTML parser，不要求 route SKILL。其他 Capability 的 required VIS 缺失不影响该 Task。
   - `route=none`：`UI_CONTEXT.json` 标记 `uiRequired=false`，不得写前端业务代码。
   - 如果读取工具返回截断内容，继续续读直到 EOF；未确认 `routeSkillReadComplete=true` 前，不得读取 parser、不得读取 HTML、不得写前端代码。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --workspace "{ARTIFACT_WORKSPACE}" --feature "{FEATURE_ID}" --mark route-skill-read-complete --json
```

3. 把 route SKILL 中定义的 `write_todos` 主流程转成该 Agent 的可见任务清单，逐项执行并更新状态，不能合并成一句“实现前端页面”。清单创建后立即记录机器证据：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --workspace "{ARTIFACT_WORKSPACE}" --feature "{FEATURE_ID}" --mark route-todos-created --json
```

4. 只有 route SKILL 的清单推进到“转交 parser”步骤时，才能读取 parser：
   - `absolute-html` 只能由 `with-absolute-html/SKILL.md` 转交 `references/html-parser.md`
   - `standard-html` 只能由 `with-standard-html/SKILL.md` 转交 `references/standard-html-parser.md`
   - `/autodev-code` 根技能不得直接跳入 parser 文档。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --workspace "{ARTIFACT_WORKSPACE}" --feature "{FEATURE_ID}" --mark parser-read --json
```

5. route SKILL 的全部主流程清单完成后记录：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --workspace "{ARTIFACT_WORKSPACE}" --feature "{FEATURE_ID}" --mark route-todos-completed --json
```

6. 统一前端回检后，把结果写入 `{FEATURE_DIR}/FRONTEND_ROUTE.json`：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --workspace "{ARTIFACT_WORKSPACE}" --feature "{FEATURE_ID}" --review-status passed --json
```

允许值：`passed`、`has-suggestions`、`skipped-by-user`、`failed`。`failed` 或未写明且未明确跳过时，`frontend_route_gate` 会阻断 `code_done`。

`{FEATURE_DIR}/FRONTEND_ROUTE.json` 是本闸门的机器证据。HTML 路线下，当前前端 Task 缺少该文件、route SKILL 未读完、route todos 未创建/未完成、parser 未读、回检未通过或未明确跳过时，不得写入前端业务代码，也不得让该 Task 完成。`spec-driven-ui` 不要求 route SKILL / HTML parser，但仍必须完成统一前端回检并写入 `reviewStatus`；`none` 不允许写前端业务代码。后端 Task 不受残留的前端 Route 证据影响。

进入 Code 前读取 Feature 的 `IMPLEMENTATION_SCOPE.json`。`backend_only` 只执行 backend task，`frontend_only` 只执行 frontend task；如果计划中存在相反 lane 的任务，停止并回到 `/autodev-plan` 修复，不得通过手工修改 `uiRequired` 绕过范围门禁。

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```

## 前端 HTML 实现分支（由前端 Task Agent 触发）

HTML 转前端已经并入 `/autodev-code`。它不是独立 workflow 节点，也不产生 `frontend_in_progress` / `frontend_done` checkpoint；完成后仍按本技能的批次编译协议推进到 `code_done`。本分支只处理 HTML/DOM/设计导出稿到真实工程代码的实现方式。

触发条件（由 Batch Agent 完成 `code_task_context.py` 后判断，任一满足即进入本分支）：

- 当前 Task 的 `taskContract.uiRequired=true`（该字段由 `code_task_context.py` 从 plan task 生成）；Feature 级 `UI_CONTEXT.json` 仅作为范围校验和回检依据。
- active batch task 的 `uiRequired/uiRefs`、specs 或用户本轮任务明确要求根据 HTML、DOM 片段、设计导出 HTML 实现前端页面。
- 用户本轮直接粘贴或提供了可读取的 HTML/DOM 片段、设计导出稿或静态页面素材。

总优先级：

1. UI 范围以 `UI_CONTEXT.json` 为最高机器事实源。
2. 行为契约以 `specs/**/*.md` 为最高依据。
3. 技术边界以 `design.md` 与 `plan.json` 为实现依据。
4. HTML/DOM/设计导出稿只提供页面结构、视觉布局、组件槽位、文案内容和交互线索，不得覆盖 UI_CONTEXT/specs/design/plan.json。
5. PRD / specs / plan.json 与 HTML 同时存在时：业务字段、文案、交互和任务边界以流程契约为准；布局、结构、间距、视觉层级以 HTML 为准。
6. 如果当前 active UI Task 引用了 required VIS，但 resolver 报 `required_visual_source_missing` 或摘要不一致，先修复/重新归档该 VIS，不能降级为 `spec-driven-ui`，也不能用另一个 HTML 临时替代。

路径边界：上述产物均指 feature 产物目录中的文件，不是业务代码仓库 cwd 下的同名路径；执行具体 task 时必须通过 `hooks/code_task_context.py` 解析并读取对应片段。后端 Task (`taskContract.uiRequired=false`) 不得因 Feature 存在 UI 产物而启动 Route resolver。

HTML 分流规则：

| 输入形态 | 路线 |
| --- | --- |
| 标准 DOM、语义结构清晰、`form` / `table` / `button` / flex / grid / class 规则明显 | `references/frontend-html/with-standard-html/SKILL.md` |
| 普通静态 HTML、HTML 转 React，且页面主体不是绝对定位碎片结构 | `references/frontend-html/with-standard-html/SKILL.md` |
| 高保真 HTML、Figma/MasterGo/低代码导出稿、坐标稿，主体由绝对定位或固定像素尺寸主导 | `references/frontend-html/with-absolute-html/SKILL.md` |
| 有 UI 任务但没有 HTML/设计稿输入 | `spec-driven-ui`，按 specs/design/plan 直接实现 |

组件、图标与图表来源及收尾要求沿用 `dev_workflow_py` 约束：先遵循项目既有规则和真实依赖；图表使用真实组件；缺少新依赖时按用户确认流程处理；完成页面拆分、公共逻辑抽取和可见样式细节后，必须返回 `/autodev-code` 主流程。两个 HTML route 都必须按各自 SKILL 的清单执行并回传目标源码、原始 HTML、分析产物、`uiLibraryTarget`、`antdMode` 与 `auditRequired`，不得调用独立的 `autodev-frontend` 节点。

## 准入检查


```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

准入只验证 Plan 声明的生产 workspace、scope 绑定与唯一编译策略，不执行编译命令，也不检查 TASK 测试命令的 cwd、manifest、依赖或可执行文件；这些测试设施由后续 UTest/E2E 阶段负责。批次编译命令以 `batchValidation.commands` 中 required `kind=compile` 的命令为准。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
```

## 执行协议

### 唯一 Code 策略

根 `plan.json.taskValidationPolicy` 必须同时满足 `mode=defer_to_test_stages`、`orchestration=inline`、`codeGate=batch_compile_only`。Code 只实现生产代码；`validationTestPlan` 只作为后续 UTest/E2E 阶段的只读 `testIntent`，不得创建或修改测试文件，不得生成/消费 `create_in_code`，不得执行 TASK `validationCommands`。当前批次所有 TASK 成为 `implemented` 后，只执行下方「批次只编译与模型修复」。

策略字段缺失、组合不完整或值不匹配时立即停止并回流 `/autodev-plan` 重建；不得根据测试文件是否存在自行猜测策略，也不得调用已移除的逐 TASK 验证或 batch-check 接口。


### Code 会话入口

首次为当前 Feature 启动 Code Session 前，如果还没有基线，先对计划声明的每个生产代码 workspace 执行一次独立回退脚本的基线捕获；同一 Session 后续批次不得重复捕获：
```bash
python "${pluginPath}/hooks/rollback_stage.py" \
  --capture-code-session \
  --feature "${feature}" \
  --code-workspace "<plan 中声明的生产代码 workspace>" \
  --json
```
该命令只保存 Code 开始前的 Git 可见文件快照，不修改业务仓库。基线 v2 对未修改的已提交文件只记录 Git blob 引用，不复制文件内容；已暂存、未暂存和未跟踪文件仍保存独立内容对象，保证回退不会丢失用户已有改动。已有 active v2 基线时脚本会复用它；旧版本 active 基线不会自动迁移，必须先完成回退清理后重新捕获。

完成上述一次性基线检查后，读取根 Plan 的未完成 Batch 数量，并统一进入固定 Workflow 入口。不得让模型生成或改写 workflow 脚本。

Code 会话入口固定为 `python "${pluginPath}/hooks/task_runner.py" code-session ...` 或下方的 `workflow_launcher.py`；不得调用不存在的 `hooks/code_session.py`。`--code-workspace` 必须是 Plan 的绝对业务 Git 根，逻辑名 `RouYi` 不是可传给基线脚本的路径。

多个未完成 Batch 必须统一通过并行调度工作流按依赖 DAG 执行，不得选择一个 `activeBatchId` 后串行推进。必须严格按返回的 `action` 分支：

- `execute_active_batch`：只加载返回的 `activeBatchId` 对应批次，按下方 Task 协议执行。
- `run_batch_compile`：执行一次 `batch-compile`，不得先执行任何 TASK 测试命令。
- `start_batch_compile_repair` / `continue_batch_compile_repair`：按 runner 返回的 `repairOwnerTaskIds` 由模型修复生产代码，最多 3 次，不得要求用户手工修改。
- `start_parallel_batch_workflow`：立即按下方 Workflow 并行执行模式进入 scheduler `ensure`；它只会创建首个 run，或在交付物完整时复用已有 run，绝不通过新 run 覆盖待合并或待恢复的交付物。不得选择一个 `activeBatchId` 后串行执行。
- `code_done_ready`：所有批次均已通过生产代码编译门禁，继续 Code 完成门禁。

入口返回失败或活动批次缺失时必须停止，不得猜测 batch ID、直接编辑计划或绕过入口启动 Task。旧的串行 Code 会话不再作为执行路径。

### 建立执行上下文与任务队列

- 只读取根 `plan.json` 的批次摘要和 `activeBatchId` 对应的一个 `plans/Bxxx/plan.json`，不得把其他批次完整 task 契约加载进当前对话。使用 `write_todos` 映射当前批次任务，状态用待做 / 进行中 / 实现已就绪 / 完成 / 失败；每次只置一个任务为进行中。根 plan 含 `tasks` 或缺少批次时回流 `/autodev-plan` 重建。

### Batch 级代码探索闸门

每个 active Batch 只允许在首个 TASK run 启动前建立一次仓库认知。选择该 Batch 第一个依赖已满足的待做 TASK，先运行 `code_task_context.py`，读取输出中的 `batchExplorationScope`、`explorationCaches`、`explorationPolicy` 和 `explorationDirective`。`batchExplorationScope` 是本批全部 TASK 的 `modules/entrypoints/paths/pages/dataObjects/validationCommands` 并集，首次有界探索必须以该并集为边界，不得只探索第一个 TASK，也不得扩展到其他 Batch。

`explorationDirective.phase=batch_bootstrap` 时按 `scopeSource=batchExplorationScope` 完成本批探索接续；`phase=task_guard` 且 `fullExplorationAllowed=false` 时只能使用 `taskContract.scope` 做定点复核，不得把仍然返回的 Batch scope 当成重新全探授权。按下文缓存状态完成 `record` 或 `patch`，并重新运行 context，直到所有仓库均成为 `fresh` 后，才能启动本批第一个 TASK run。进入后续 TASK 时仍要重跑 context 作为轻量快照守卫；同批前序带有效 `latestImplementationEvidenceId` 的 `implemented` TASK，以及批编译修复中带该证据的 `in_progress` TASK，能完整解释且最终快照匹配时必须返回 `fresh_with_trusted_changes`，不得重新探测项目框架或模块布局。若后续 TASK 返回 `stale`，先根据 `staleReasons/criticalHits/unexplainedPaths` 处理异常漂移，不得把每个 TASK 的正常实现变化当成新的全量探索机会。

###  选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目。

###  执行单个任务

**固定命令顺序：`code_task_context` → runtime ignore 检查 → `contract` → 有界探索 → `record/patch` → 再次 `code_task_context` 确认全部仓库为 `fresh` → `start`。禁止为了保存 Git 快照先调用 `start`；首次探索记录本身会绑定当前 Git snapshot，`start` 在探索未就绪时必然阻断且不会创建 run。**

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。启动前必须确保每个业务仓库都通过 `.gitignore` 或 `.git/info/exclude` 忽略 `.cmbdevclaw/large_tool_results/`；runner 只校验该契约，不会代写业务仓库。未命中 ignore 时先配置窄规则，再执行 start。

2. 读唯一 `plan.json` 中的结构化执行契约。**必须先运行任务上下文解析脚本，且这一步发生在上面的 `start` 命令之前：**

```bash
python "${pluginPath}/hooks/code_task_context.py" --feature "${feature}" --task-id "<TASK_ID>" --code-workspace "<BUSINESS_REPO>"
```

该脚本输出是当前 task 的上游上下文，必须读取其中的 `batchExplorationScope`、`taskContract`、`resolvedSpecRefs`、`resolvedDesignRefs`、`explorationCaches`、`explorationPolicy` 和 `explorationDirective`。`explorationDirective.nextCommands` 是机器生成的下一步命令，必须原样执行，不得自行猜参数；`explorationDirective.requiredAction` 不是建议而是当前闸门动作。探索范围按上方 Batch 级探索闸门服从 `explorationDirective`。只传 `taskContract.workspaceRef` 对应的一个 `--code-workspace`。`specRefs` / `designRefs` 一律按 `artifactFeatureDir`（`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}`）解析，不得按业务代码仓库 cwd 直接读取 `specs/...`、`design.md`、`PLAN.md`；业务代码仓库 cwd 只用于定位生产源码和理解既有实现。脚本只要返回 `ok=false`，无论是引用、计划、Git 快照还是探索缓存错误，都必须停止编码、不得读取 HTML/调用 parser/修改业务代码；先按 `requiredAction` 修复并重新运行，直到返回 `ok=true`。其中 `explorationBlocked=true` 或 `implementationAllowed=false` 是机器阻断证据，不得被自然语言解释覆盖。若脚本返回 `missing_ref_file` / `missing_ref_anchor` / `invalid_plan_json` / `task_not_found`，停止编码并回流 `/autodev-plan` 修复产物引用，不得猜测补路径。

必须按每个仓库的缓存状态执行，不能只看总体最严 policy 后忽略其他仓库：

若 `explorationPolicy.status=unavailable`，说明没有传入业务仓库，必须停止并补传 `--code-workspace`，不得绕过缓存协议继续编码。

- `missing` / `stale`（`full_bounded_explore`、`requiresRecord=true`）：读取 `staleReasons` / `criticalHits`，完成一次有界探索；先执行 `explorationDirective.contractArgv` 读取完整 schema/example，并确认业务仓内存在的 `.autobizdevops/`、`.cmbdevclaw/` 等运行期目录已被 Git ignore，再对每个仓库原样执行对应 `explorationDirective.nextCommands[].argv`，通过 `--body-stdin` 写入完整 findings。只有无法使用 stdin 时才允许 `--body-file`，且文件必须位于业务 Git 仓库之外，禁止把临时探索 JSON 纳入仓库 snapshot。writer 返回 `issues[]` 时必须一次修复列出的全部 JSON path，不得只修第一项后继续猜。重新运行 `code_task_context.py`，对应仓库必须成为 `fresh` 后才能 start 或改业务代码。`headCommit` 单独变化但 Git 可见文件快照未变，或当前文件快照与可信 TASK/Batch Evidence 的最终快照一致时允许复用；HEAD 变化且缺少可信最终快照时仍按 `stale` 处理。
- `fresh`（`task_scope_only`）：直接使用缓存 findings，只定点读取当前 Task scope / entrypoint 涉及文件；不得重复探测项目框架和模块布局。
- `fresh_with_trusted_changes`（`task_scope_only`）：implementation evidence 已解释本次仓库变化，且缓存与当前 Task 属于同一 active batch；只定点读取当前 Task scope / entrypoint，不执行 `record` 或 `patch`。这是批次内的 `deferredCacheUpdate`，变化会在批次边界统一吸收。
- `reusable_with_changes`（`targeted_reread`、`requiresPatch=true`）：只读取 `changedPaths + 1-hop 依赖 + 当前 Task scope`，再对每个仓库原样执行对应 `explorationDirective.nextCommands[].argv`，优先通过 `--body-stdin` 提交 patch；即使事实未变化，也必须传完整 `reviewedPaths` 和空 `findingUpdates`。`findingUpdates` 对每个字段采用整类替换语义，不得只传该列表的一部分；writer 会在合并后重新校验完整 findings。重新运行 context，成为 `fresh` 后才能 start 或改业务代码。
- 同一 active batch 内，普通且已被 implementation Evidence 解释的源文件变化不要求每个 Task 都 patch；进入下一批次时在 Batch 级探索闸门统一 patch。即使仍在同一批次，命中 `shared/integration` 路径也必须立即 targeted reread/patch；构建配置、迁移/schema、HEAD 变化且缺少可信最终快照，或无法由 Evidence 解释的路径仍进入 `stale`，重新做有界 `record`。

`fresh/reusable_with_changes 时禁止无边界全仓探索`：不得重新运行无范围全仓 `rg`、递归目录 listing 或框架发现。缓存只能通过 `code_exploration_writer.py` 修改，禁止直接编辑 `cache/code-exploration/**/*.json`。共享路径只更新当前 executionLane；另一 lane 在后续 inspect 时独立判定。runner 能返回 machine policy，但宿主未提供工具调用遥测，无法独立证明 Agent 执行过多少次搜索，这是协议层约束。

必须读取当前 task 的 `workspaceRef`、`goal`、`scope`、`validationBoundary`、`implementationPoints`、`acceptanceCriteria`、`nonGoals`、`splitRationale`（若存在）、`specRefs`、`designRefs`、`validationCommands`；不得只根据 `title` / `specRefs` 脑补实现范围。缺少 `workspaceRef` / `goal` / `scope` / `validationBoundary` / `implementationPoints` / `acceptanceCriteria` / `nonGoals` 时停止编码，回到 `/autodev-plan` 补齐，不得边做边猜。先依各输入的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。`splitRationale` 只用于理解合并背景，不得作为扩大 scope 的理由。
3. 改代码前按 `explorationPolicy` 进行首次有界探索或缓存定点复核；先识别或复用项目分层、命名、错误处理、校验与日志风格，形成简短修改映射（依据、拟改生产文件、复用模式）再动手。可以只读既有测试理解行为契约，但不得在 Code 阶段创建、修改或执行测试。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. context 返回 `startAllowed=true` 后，在修改业务代码前启动任务运行并保存 Git 快照：

```bash
python "${pluginPath}/hooks/task_runner.py" start --feature "${feature}" --task-id "<TASK_ID>" --code-workspace "<TASK_WORKSPACE>"
```

保存输出中的 `runId`。同一 feature 同时只允许一个活动 task run；重复执行、异常中断或工具崩溃后，不得新建 run 绕过，必须使用 `inspect` / `resume` / `abort` 处理原 run。收到 `active_task_run_exists` / `active_feature_task_run_exists` 时必须 inspect 并继续现有 run，不得为了重新 start 而 abort。业务源码写入前，写入闸门会严格校验完整 v2 run、路径身份、`executionMode=code` 和密封探索证明；`verified_existing` / `external_dependency` 不允许写业务源码。每次普通 start 都会重新检查当前探索缓存；abort 后不得复用过期 `fresh`。批编译修复导致的受控重试允许在重新检查后继承旧证明，并必须记录 `inheritedFromRunId`、本次观察状态和 stale 原因。
`--code-workspace` 同时是 Git 仓库定位入口和 task workspace 基准，run 中固化 `scopePathBase=requested_code_workspace`；必须选择 task `workspaceRef` 指向的实际仓库，请求路径必须与 task `scope.workspaceRoots` 声明的位置完全一致。即使传模块子目录，runner 也会解析并快照整个 Git 根；`scope.paths` 只作为相对该模块的提示性范围，不是实现文件白名单。start 只验证 workspace/scope 绑定和生产仓库状态，不检查或执行 TASK 测试命令的 cwd、manifest、依赖或可执行文件；这些由后续 UTest/E2E 阶段负责。TASK finish 会自动记录该 workspace 内全部有效生产代码和生产配置变更；测试文件变更会被拒绝，跨 workspace 的变更也必须先修复工作区。`finish-implementation` / `abort` / `resume` 必须继续传相同请求路径并保持同一个 run；同一 Git 根下替换成其他模块会返回 `task_run_requested_workspace_mismatch`。快照比较 Git 可见文件的内容哈希，包含未跟踪且未忽略文件；`staging / unstaging` 不会制造内容变更，也不能恢复丢失的 start 基线。TASK 的实现 Evidence 使用该 TASK 从首次 start 到最终实现收口之间所有 run 的累计 `fileChanges/changedFiles`；abort 只结束一次 run，不清除已记录的变更。
`start` 会固化当前 task 契约哈希，并对请求 workspace、scope 投影和初始 Git 快照写入 `integritySha256`。Batch 级探索和首次 `record/patch` 必须发生在首个 TASK `start` 前；后续 TASK 的 context 复核同样应在对应 `start` 前完成。run 活动期间不得修改该 task 的 goal/scope/AC/validationCommands 等计划字段，也禁止直接编辑 `plan.json`、批次 `plan.json` 或 `.task-runs/**/*.json`、手工重算 digest/hash；否则 runner 返回 `task_run_integrity_mismatch` 或计划完整性错误。确需修复 Plan contract 时按下方协议保存 patch、force abort、由 Plan writer 整体重建契约和基线后再应用 patch；普通新增 DTO/XML/生产资源文件不需要修 scope 或重建 digest。

每个 TASK 必须且只能绑定一个 `workspaceRef`；只向 runner 的 start/finish/repair 命令传该 TASK 的 workspace，前端 TASK 不得传后端仓库、后端 TASK 也不得传前端仓库。若一个需求闭环需要修改多个业务仓库，Plan 必须拆成多个 TASK 并用 deps 串联；跨仓库集成检查留给后续 UTest/E2E 阶段。Plan 中具名 workspace 的 command 必须用 `repo` 指明 Git 根目录名；changed/supporting 路径使用 `repoId:relative/path`。无论涉及多少仓库，`evidence/` 与 `.task-runs/` 只能写入 feature 产物目录，禁止写入任一业务仓库。

Batch 同样只能包含同一 lane 且同一 `workspaceRef` 的 TASK；前后端不会进入同一 Batch，同一 lane 的不同仓库也不会进入同一 Batch。执行 `batch-compile` 时只传该 Batch 的唯一 workspace。

5. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：只实现 `scope` / `implementationPoints` / `acceptanceCriteria` 指向的业务范围；`scope.paths` 只是相对 workspace 的文件提示，不是逐文件白名单，因实现需要新增的 DTO/domain/resources/迁移/配置会由 runner 自动归集。不得实现 `nonGoals` 中列出的内容。观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 只读取 `validationTestPlan[].testIntent` 理解后续测试意图，不创建、不修改、不补齐任何测试资产；runner 返回 `code_stage_test_changes_forbidden` 时必须恢复测试文件变更。TASK 实现期间不执行测试、compile/build/typecheck/lint，批次结束只由 runner 执行一次 `batch-compile`。
6. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
7. **实现差异协议**：实现中遇到以下任一情况，停下用 `request_user_input` 单次确认，展示「design/spec 说 X，代码/实现是 Y」，拿到裁定前不得继续，也不得先调用 `finish-implementation` 收口：
   - **`EVD` / design 依据与代码现实不符** → 裁定后回写 `artifactFeatureDir` 下 `design.md` 的对应行（注明「code 阶段修订」）再继续；不得改写业务代码仓库 cwd 下的同名文件。
   - **必须偏离 `API` / `DATA` / `D` 已定形态**（定的做不了或明显更差）→ 同上，偏离经裁定回写 `design.md` 后才可按新形态实现；「实现细节自由度」不覆盖已定的接口/数据/技术决策形态。
   - **实现将违反 `REQ` / `SCN` 行为契约** → 停止编码，不得实现一个违反行为契约的版本；按下方阻断口径记录原因与建议回流阶段（specs/plan），回流 `/autodev-plan` 修订契约后重新进入。TASK 状态由 runner 负责流转，不得手工置「失败」。
8. 实现完成必须只走 `finish-implementation`。该命令检查 scope 和 start 快照、写 `action=implementation` Evidence，并把 TASK 从 `in_progress` 置为 `implemented`；它不运行 `validationCommands`，不写 `completionEvidenceIds`，也不把 TASK 置为 done。旧 `complete` 命令已删除：

```bash
python "${pluginPath}/hooks/task_runner.py" finish-implementation --feature "${feature}" --task-id "<TASK_ID>" --run-id "<RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

若返回 scope/workspace 错误，仍按原 run 修正或回流 Plan，不得重新 start 掩盖基线。`implemented` 是实现终态，不等于业务完成；同批后继 TASK 可以依赖它，跨批依赖、handoff 和 code-done 只接受 `done`。

实现 Evidence 尚未落盘时发生进程中断，才使用原 runId 恢复：

```bash
python "${pluginPath}/hooks/task_runner.py" resume --feature "${feature}" --task-id "<TASK_ID>" --run-id "<ORIGINAL_RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

确实没有文件变更时，不得伪造 changedFiles，也不得把空 diff 当遗漏。必须说明原因并提供至少一个仓库内已有生产实现文件；Plan 中仍必须保留该 TASK 的测试意图，Code 阶段不执行它：

```bash
python "${pluginPath}/hooks/task_runner.py" finish-implementation --feature "${feature}" --task-id "<TASK_ID>" --run-id "<RUN_ID>" --code-workspace "<BUSINESS_REPO>" --no-code-change-why "<WHY_EXISTING_IMPLEMENTATION_IS_SUFFICIENT>" --supporting-file "<RELATIVE_PATH>"
```

`--supporting-file` 必须是仓库根相对路径；多仓库时使用 `repoId:relative/path`。`--no-code-change-why` 只用于 start 前已经存在且经行为验证确认满足契约的实现，不得用它绕过误 abort、重启 run 或 staging 操作造成的空 diff；runner 会拒绝与历史 aborted run 变更冲突的 no-code claim。

`finish-implementation` 成功后，把该 TASK 在 `write_todos` 标记为“实现已就绪/待编译”，不是完成；返回 `continue_active_batch`、`continueCurrentBatch=true` 和 `nextTaskId` 时，同批仍有可执行任务时禁止询问用户是否继续，立即进入下一个 Task。最后一个 TASK 后只接受 `run_batch_compile` 并进入下方批次编译。

### 批次只编译与模型修复

当前批次全部 TASK 为 `implemented` 且 `code-session.action=run_batch_compile` 后执行：

```bash
python "${pluginPath}/hooks/task_runner.py" batch-compile --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

`batch-compile` 是 Code 阶段唯一构建命令，只编译生产代码，不运行 TASK 测试，也不创建测试资产。长时间编译仍通过宿主异步命令执行并持续获取同一后台任务结果，不得重复启动编译。

- 返回 `compileStatus=passed` 后，parallel Batch 的 TASK 保持 `implemented`，Workflow 将已提交的 Worktree 标记为 `ready_to_merge`。只有 `batch_merger.py` 已将该 commit 合入源分支并写入 `mergeCommitSha` 后，才将 TASK 和 Batch 标记为 `done`；由 scheduler 在此后释放下游 Batch。非并行 Batch 仍在编译通过后完成。
- 返回 `requiredAction=start_batch_compile_repair` 时，必须从 `repairOwnerTaskIds` 选择 runner 允许的责任 TASK，由模型根据 `diagnosticPaths`、`diagnosticSummary` 和编译输出修复生产代码。推荐先执行下列命令建立 repair run，再修改代码：

```bash
python "${pluginPath}/hooks/task_runner.py" start-batch-compile-repair --feature "${feature}" --batch-id "<BATCH_ID>" --task-id "<REPAIR_OWNER_TASK_ID>" --code-workspace "<BUSINESS_REPO>"
```

如果模型已先完成生产代码修复，再执行 `start-batch-compile-repair`，不得回滚修复：runner 会以失败编译快照为基线收编真实文件差异，并在返回中设置 `adoptedPreStartChanges=true`。随后即使不再修改文件，也必须用返回的 runId 执行 `finish-implementation`，生成新的 implementation evidence，再重新执行同一批次的 `batch-compile`。收编仍受 workspace 边界和测试文件禁改规则约束。

不得让用户手工修改，不得自行执行 `mvn compile`、前端 build/typecheck 或其他旁路编译，也不得在 repair 中创建/修改测试或执行测试命令。每批最多 3 次编译 repair；达到上限后按 `escalate_batch_compile_repair_exhausted` 阻断，不得开启第 4 次或绕过状态。

### 单任务修复协议

当某个已完成的 TASK 需要修复时（例如回检发现问题），使用单任务修复流程。

#### 通过 Claw 对话触发修复

用户可以直接在对话中说明需要修复的任务，例如：

```
"我发现 T001 的实现有问题，需要修复一下"
"T002 执行得不对，帮我重新修复"
```

收到修复请求后，你应该：

1. **查看任务状态**：先读取 PLAN.md 或调用 `code-session` 确认任务状态
2. **获取 evidence ID**：从计划中获取该任务的 `latestImplementationEvidenceId`
3. **启动修复**：调用 `start-task-repair` 命令
4. **修改代码**：根据问题描述修改相关代码
5. **完成修复**：调用 `finish-implementation --repair-mode`
6. **如需重新验证批次**：调用 `revalidate-batch-compile`

示例对话流程：

```
用户: "T001 的用户登录逻辑有问题，验证码校验不正确"

助手:
1. 先查看 T001 的状态...
2. 从计划中获取 latestImplementationEvidenceId: "ev-20240101-..."
3. 启动任务修复...
4. [修改代码]
5. 完成修复并记录新的 evidence
6. 修复完成！新的 evidence ID: "ev-20240101-..."
```

#### 命令行执行

如果需要手动执行，使用以下命令：

```bash
# 1. 启动任务修复
python "${pluginPath}/hooks/task_runner.py" start-task-repair --feature "${feature}" --task-id "<TASK_ID>" --prior-evidence-id "<PRIOR_EVIDENCE_ID>" --code-workspace "<BUSINESS_REPO>"
```

`--prior-evidence-id` 是该 TASK 的 `latestImplementationEvidenceId`（可从 PLAN.json 或 PLAN.md 中查看）。

```bash
# 2. 修复完成后
python "${pluginPath}/hooks/task_runner.py" finish-implementation --feature "${feature}" --task-id "<TASK_ID>" --run-id "<RUN_ID>" --code-workspace "<BUSINESS_REPO>" --repair-mode
```

这会生成新的 implementation evidence，保留 `priorEvidenceId` 引用链，并自动更新计划中该 TASK 的 evidence 指针。

**重要**：支持修复 status = "implemented" 或 "done" 的任务。修复后，如果原状态是 "done"，会自动恢复为 "done" 状态。

### 批次重新验证

当批次的所有 TASK 都已修复完成，需要重新验证整批编译：

```bash
python "${pluginPath}/hooks/task_runner.py" revalidate-batch-compile --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

此命令与 `batch-compile` 类似，但用于修复场景，会检查所有 TASK 的最新 evidence 并重新执行编译验证。

### 回检与交接

本节完整协议由脚本渲染,必须先运行下面命令，并完整遵循其输出；不得凭记忆执行本节，也不得跳过该命令。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.code
```

推进 `code_done` 前先回填领域词汇表锚点：会话工作区 `CONTEXT.md` 中锚点为「规划中」且本轮已落地的词条，回填为实际类/表/枚举与相对路径（协议见 `${pluginPath}/skills/references/domain-context.md`；无该文件或无「规划中」词条则跳过）。

项目级验证收敛后：

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.code --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
```
## Workflow 并行执行模式

当 Code 阶段存在合法待执行 Batch 时，只启动仓库固定的 `workflows/code-batched-execution.workflow.js`。每个可写 Batch 必须通过平台 `agent(..., { isolation: "worktree" })` 获得原生 linked Git worktree；插件只负责调度、契约校验、提交和合并，绝不创建或删除 Batch worktree。不得生成、持久化、校验或以内联脚本替换 workflow 控制流。

先调用 launcher：

```bash
launcher_result=$(python "${pluginPath}/hooks/workflow_launcher.py" \
  --feature "${feature}" \
  --plugin-path "${pluginPath}" \
  --workspace "${pluginWorkspace}/${projectDir}" \
  --json)
```

launcher 必须从根 `plan.json` 的 `codeWorkspaces` 读取 `workspaceRef -> 绝对业务 Git 根` 映射，并返回 `codeWorkspaces`、`executionIsolation=platform_dynamic_worktrees` 与 `workflowHostGitRoot`。不得把 `artifactWorkspace` 当作代码仓库路径。旧 Plan 没有该字段时，只能显式补传映射，例如 `--code-workspace "RouYi=/absolute/path/to/RouYi"`；无法解析映射时必须阻断并回流 Plan，不得猜路径。

单一物理 Git 根时，只有 `useWorkflow=true`、`executionMode=fixed`、`canStartWorkflow=true`、`requiredAction=start_fixed_workflow` 且校验结果为 `parallel_plan_valid` 或 `single_batch_workflow_valid`，才使用 launcher 返回的固定脚本内容启动 Workflow。多个物理 Git 根时，只有 `executionMode=repository_coordinated`、`requiredAction=start_repository_coordinator` 且 `canStartWorkflow=true`，才执行协调器的 `prepare`/`next` 协议并启动其返回的子 Workflow。launcher 会把插件内固定脚本复制到 `artifactWorkspace/.cmbdevclaw/workflows/` 作为审计副本，并返回 `workflowScriptContent`、`workflowScriptSha256`、`workflowScriptSource` 与可直接透传的 `workflowArgs`；`workflowScript` / `workflowScriptPath` 仅是审计路径，不得传给平台做 `scriptPath`。任何其他结果都必须停止或回流 `/autodev-plan` 修复 Plan，禁止让模型临时编排或改写 workflow。

调用平台 `workflow` 的唯一允许形式是 `script=launcher.workflowScriptContent` 且 `args=JSON.stringify(launcher.workflowArgs)`。禁止自行拼接 JavaScript、禁止增删 phase、禁止从 launcher 输出以外重建参数。使用内联脚本是为了让平台把固定脚本持久化到当前对话 workspace；不得把 artifact workspace、业务仓库或插件目录的绝对路径作为平台 `scriptPath`。

单一物理 Git 根的启动参数必须包含 `feature`、`pluginPath`、launcher 返回的 `artifactWorkspace`、`workflowHostGitRoot` 和以逻辑 `workspaceRef` 为 key 的 `codeWorkspaces`。平台 Worktree 的源仓库由启动 Workflow 的宿主工作区决定，因此宿主 Git 根必须严格等于 `workflowHostGitRoot`；`artifactWorkspace` 只保存产物，不能作为 Workflow 宿主。固定脚本会在创建 scheduler run 前复核该条件。当前平台没有在单个 `agent()` 调用中指定其他 Git 隔离源的接口：若 `codeWorkspaces` 指向多个独立 Git 根，launcher 返回 `executionMode=repository_coordinated`、`requiredAction=start_repository_coordinator` 和协调器命令，不直接启动多根 Workflow。协调器 `prepare` 必须向共享 scheduler 传递 `allow_bootstrap=True`，使受控的脏仓库基线提交策略与单仓库固定 Workflow 一致；仍只排除平台运行时文件，并为每个物理 Git 根记录一个明确的基线提交。协调器只创建一个共享 scheduler run，并按当前 DAG 波次的物理 Git 根返回 `repositoryWorkflows`；调用方必须用同一份固定脚本内容并行启动这些子 Workflow，子 Workflow 的启动参数只包含本仓库映射、`repositoryRefs`、`batchIds` 和各自的 `workflowHostGitRoot`，且宿主必须严格等于该根。所有子 Workflow 完成后调用协调器 `next`，重复到 `allMerged=true`，最后只调用一次 `final-verify`。不得把多仓库映射强行塞进一个平台 Workflow，也不得为每个仓库创建独立 scheduler run。

固定脚本按以下顺序执行：

- Workflow 启动时先执行 scheduler `ensure`：没有活动 run 时创建一个；已有交付物完整的活动 run 时复用同一个 runId；`needs_resolution` 或缺失已密封交付物时 fail-closed 并保留现场。随后 scheduler 只选择依赖已经 `merged` 的 pending Batch；因此初始波次就是所有无依赖 Batch。
- scheduler 会先按 Batch 的 `executionStage` 和物理写集计算安全波次：`proto`、`global` 阶段逐 Batch 串行，普通 `parallel` 阶段按同一 Git 根的 `scope.paths`/`expectedFiles` 做文件级隔离（相同或父子路径、写集未知时串行），`integration` 阶段用于共享入口文件的串行收口。同一安全波内受 `maxParallel` 限制并行执行；每个 Batch agent 调用时精确声明 `isolation: "worktree"`，平台从冻结的宿主 Git base 创建独立 checkout。每个 Batch 获取 lease、采集平台分配的路径和分支、完成 TASK、batch compile，并由 `worktree_manager.py seal` 提交并持久化 delivery `commitSha`；只有随后 `lease release --final-status ready_to_merge` 验证到该 SHA 后才进入合并队列。
- 该波次全部完成后，shared workflow owner 立即运行 `batch_merger.py --conflict-mode native-rebase`。只有 commit 已真正合并为 `merged` 并写入 `mergeCommitSha`，才调用 scheduler `resume` 计算下一波次；下游 Batch 不会读取未合并改动。若 native rebase 发现真实冲突，固定 Workflow 自动启动单个集成 Agent 在保留的冲突 worktree 内执行语义 rebase、逐文件解决并运行该 Batch 的验证命令，然后调用 `batch_merger.py resolve` 做 clean/祖先关系/无冲突校验，最后重试真实 merge。解决失败则保留 `needs_resolution` 和 worktree，禁止标记完成或释放下游。
- 若 Git merge 已成功但 Plan writer 更新失败，`batch_merger.py` 会保留 `needs_resolution` 与 `resolution.kind=plan_state_update`，记录已合入的 commit；固定 Workflow 和后续 `ensure`/`resume` 会先校验源分支 HEAD，再幂等重试 Plan 更新，成功后才释放下游 Batch。
- 不具备并行条件的 Batch 自然在下一波次串行执行。所有 Batch 合并后才执行 `parallel_final_verify.py`。若固定 Workflow 无法启动，必须停止并报告，禁止手工顺序执行 Batch 或在共享工作区继续写代码。
- Workflow 只接受 launcher 返回的完整 `workflowArgs`；`feature`、`pluginPath`、artifact workspace、Workflow host 和每个 code workspace 都必须是非空、非 `undefined` 的绝对路径。任一参数无效时在创建 Batch agent 前阻断，禁止生成临时 workflow 或手工创建分支绕过平台 worktree。

并行模式的唯一调度状态源是 `.parallel-runs/<runId>/manifest.json`；平台 Workflow 面板仍是 Worktree 交付物的所有者。`batch_merger.py` 已经把成功 Batch 合入主分支后，面板条目仍会保留用于 Diff/恢复，不能再次点击平台「合并」造成重复集成；确认无误后按平台面板流程丢弃/清理该交付物。平台生成的 `.cmbdevclaw/workflows/**` journal、state 和 toolstream 文件不属于业务改动，合并和基线检查会精确排除它们；不得用 `git checkout -- .` 或 `git clean` 清理工作区。自动冲突收口同样禁止 `ours`、`theirs`、`git merge -s ours`、`--no-verify`、删除一侧变更或直接修改主工作区；失败时由 manifest 保留现场并等待受控恢复，不能自行丢弃 worktree。恢复使用 `parallel_batch_scheduler.py resume`、`parallel_batch_lifecycle.py monitor` 与 `batch_lease_manager.py reclaim`；`parallel_batch_lifecycle.py cleanup` 只记录清理，不删除平台 Worktree。

不得手工删除 `.parallel-runs/<runId>`、复制孤立 worktree 文件、创建 Batch 分支或把 Batch 标成 `merged`。只有 `batch_merger.py` 成功写入 `mergeCommitSha` 后，Batch 才完成并释放下游依赖；`ensure`/`resume` 会对缺失该证据、主仓库脏文件或 HEAD 漂移 fail-closed，并返回原 runId。

## 写入边界

允许：与当前任务需求闭环直接相关的生产代码和生产配置；能追溯到任务依据与队列的新增生产文件。测试目录和测试资产在 Code 阶段禁止写入。

同时允许：`artifactFeatureDir` 下 `design.md` 中经实现差异协议裁定后的对应行修订（注明「code 阶段修订」）；会话工作区 `CONTEXT.md` 的领域词汇表锚点回填。`plan.json`、批次 `plan.json`、`evidence/**` 与 `.task-runs/**` 仍只能由对应 hook 写入。

为完成任务必须改队列未直接提到的业务文件，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」，且都有 `action=implementation` evidence；任务级 evidence 继续记录真实生产文件变更，测试意图保留在 `validationTestPlan[].testIntent` 供 UTest/E2E 阶段消费。
- `evidence/EVIDENCE.jsonl`、`EVIDENCE.index.json` 与任务 implementation evidence 完整性和哈希校验通过；每个 Batch 的 `batchCompile` 状态绑定最新 implementation evidence 与 revision，没有新生成的 `ev_XXXX.json` sidecar。
- 每批 `batchCompile.status=passed`，且 `commandId` 绑定该批 required compile command 与最终 implementation digest；Batch 之间只通过 scheduler manifest 的依赖状态推进。

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
