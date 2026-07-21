---
name: autodev-code
description: 进行代码实现。
version: v1.2.0703
---

## 前端 Route 强制闸门（必须优先执行）

当本轮任务是前端代码生成、HTML/DOM/设计导出稿转工程代码，或触发「前端 HTML 实现分支」时，`/autodev-code` 不得自行改写成普通前端编码任务。必须先解析内部 route。UI 范围以 `UI_CONTEXT.json` 和 active batch task 的 `uiRequired/uiRefs` 为机器事实源，Markdown 只作迁移兜底。

1. 推进到 `code_in_progress` 后，先解析并记录 route：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --start-route-run --json
```

如用户本轮直供了不在 `{FEATURE_DIR}/frontend-html/` 或流程文档中的 HTML 文件，追加 `--html-file "<HTML_PATH>"`，可重复。

2. 按输出的 `route` 读取 route SKILL 到 EOF：
   - `route=absolute-html`：完整读取 `skills/autodev/autodev-code/references/frontend-html/with-absolute-html/SKILL.md`
   - `route=standard-html`：完整读取 `skills/autodev/autodev-code/references/frontend-html/with-standard-html/SKILL.md`
   - `route=spec-driven-ui`：有 UI 任务但没有可读取的 HTML/设计稿输入，按 specs/design/plan 实现前端；不读取 HTML parser，不要求 route SKILL。如果输出含 `htmlSourceMissing=true` / `htmlRequestMessage`，先按提示引导用户提供 HTML；用户不提供时，不阻断本阶段，继续按无高保真流程实现。
   - `route=none`：`UI_CONTEXT.json` 标记 `uiRequired=false`，不得写前端业务代码。
   - 如果读取工具返回截断内容，继续续读直到 EOF；未确认 `routeSkillReadComplete=true` 前，不得读取 parser、不得读取 HTML、不得写前端代码。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-skill-read-complete --json
```

3. 把 route SKILL 中定义的 `write_todos` 主流程转成可见任务清单，逐项执行并更新状态，不能合并成一句“实现前端页面”。清单创建后立即记录机器证据：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-created --json
```

4. 只有 route SKILL 的清单推进到“转交 parser”步骤时，才能读取 parser：
   - `absolute-html` 只能由 `with-absolute-html/SKILL.md` 转交 `references/html-parser.md`
   - `standard-html` 只能由 `with-standard-html/SKILL.md` 转交 `references/standard-html-parser.md`
   - `/autodev-code` 根技能不得直接跳入 parser 文档。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark parser-read --json
```

5. route SKILL 的全部主流程清单完成后记录：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-completed --json
```

6. 统一前端回检后，把结果写入 `{FEATURE_DIR}/FRONTEND_ROUTE.json`：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --review-status passed --json
```

允许值：`passed`、`has-suggestions`、`skipped-by-user`、`failed`。`failed` 或未写明且未明确跳过时，`frontend_route_gate` 会阻断 `code_done`。

`{FEATURE_DIR}/FRONTEND_ROUTE.json` 是本闸门的机器证据。HTML 路线下，前端代码生成任务缺少该文件、route SKILL 未读完、route todos 未创建/未完成、parser 未读、回检未通过或未明确跳过时，不得推进 `code_done`。`spec-driven-ui` 不要求 route SKILL / HTML parser，但仍必须完成统一前端回检并写入 `reviewStatus`；`none` 不允许写前端业务代码。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```


# /autodev-code — 代码执行

## 前端 HTML 实现分支

HTML 转前端已经并入 `/autodev-code`。它不是独立 workflow 节点，也不再产生 `frontend_in_progress` / `frontend_done` checkpoint；完成后仍按本技能统一收尾推进到 `code_done`。本分支只处理 HTML/DOM/设计导出稿到真实工程代码的实现方式。
用户本轮直接提供的 HTML/DOM 素材、`{FEATURE_DIR}/frontend-html/` 素材和内部 route SKILL/references 由本文的前端 Route 强制闸门管理。

触发条件（任一满足即进入本分支）：

- `UI_CONTEXT.json` 中 `uiRequired=true`，或当前 plan task 中 `uiRequired=true`。
- active batch task 的 `uiRequired/uiRefs`、specs 或用户本轮任务明确要求根据 HTML、DOM 片段、设计导出 HTML 实现前端页面；`PLAN.md` 仅是计划的人类视图，不作为前端分流机器依据。
- 用户本轮直接粘贴或提供了可读取的 HTML/DOM 片段、设计导出稿或静态页面素材。

总优先级：

1. UI 范围以 `UI_CONTEXT.json` 为最高机器事实源。
2. 行为契约以 `specs/**/*.md` 为最高依据。
3. 技术边界以 `design.md` 与 `plan.json` 为实现依据。
4. HTML/DOM/设计导出稿只提供页面结构、视觉布局、组件槽位、文案内容和交互线索，不得覆盖 UI_CONTEXT/specs/design/plan.json。
5. PRD / specs / plan.json 与 HTML 同时存在时：业务字段、文案、交互和任务边界以流程契约为准；布局、结构、间距、视觉层级以 HTML 为准。

路径边界：上述 `specs/**/*.md`、`design.md`、`plan.json` 均指 feature 产物目录中的文件，不是业务代码仓库 cwd 下的同名路径；执行具体 task 时必须通过 `hooks/code_task_context.py` 解析并读取对应片段。
6. 如果前面阶段声明了高保真/HTML 但 resolver 输出 `htmlSourceMissing=true`，先向用户说明缺失路径并请求提供 HTML；若用户本轮不提供，则按 `spec-driven-ui` 的无高保真流程继续，不等待、不阻断，也不得假装读取 HTML。

内部分流：

HTML 分流规则：

| 输入形态 | 路线 |
| --- | --- |
| 标准 DOM、语义结构清晰、`form` / `table` / `button` / `label` / flex / grid / class 规则明显 | `references/frontend-html/with-standard-html/SKILL.md` |
| 普通静态 HTML、复制 DOM、小型静态站点、HTML 转 React，且页面主体不是绝对定位碎片结构 | `references/frontend-html/with-standard-html/SKILL.md` |
| 高保真 HTML、Figma/MasterGo/低代码导出稿、坐标稿、碎片 div、页面主体或关键分区由 `position:absolute` / `left/top` / 固定像素尺寸主导 | `references/frontend-html/with-absolute-html/SKILL.md` |
| 高保真但绝对定位仅局部、稀疏、装饰性存在，整体仍以标准 DOM / flex / grid 为主 | `references/frontend-html/with-standard-html/SKILL.md` |
| 有 UI 任务但没有 HTML/设计稿输入 | `spec-driven-ui`，按 specs/design/plan 直接实现 |

高保真 / 绝对定位强信号（命中且主导页面主体、关键分区或多个视觉块时，必须走 absolute 路线）：

- 用户明确标注“高保真 HTML”“设计导出 HTML”“Figma/MasterGo 导出”“绝对定位”“纯坐标还原稿”。
- 大量 `position:absolute`、`left/top`、固定像素宽高、`clip-path`、`data:image/svg+xml`、渐变、阴影。
- 页面主体由碎片 `div`、梯形块、迷你趋势图、像素级卡片矩阵、复杂壳层布局组成。

组件、图标与图表来源：

1. 先读项目说明，再扫真实源码；项目约束优先于本分支默认规则。
2. 组件来源优先级：公共组件库 -> `architecture/components` -> 项目本地组件 -> 已安装且真实使用的组件库 -> 用户提供兜底组件库 -> 相似页面 -> fidelity-only。
3. 图标来源优先级：项目图标规则 -> 本地 icon/svg/iconfont -> 已安装且真实使用的图标库 -> React + AntD `@ant-design/icons` 兜底；纯图标按钮补 `Tooltip` 与 `aria-label`。
4. 图表必须使用真实图表组件或图库实现；优先项目图表规则 / 本地图表组件 / 已安装且真实使用的图库，缺证据时按任务约束确认或默认 ECharts 兜底，不得用静态 SVG / CSS 图形假冒真实图表，除非用户明确只要静态占位。
5. 缺少需要新增的组件库、图标库或图表库时，按项目包管理器和用户确认流程处理；安装完成前不得把依赖相关能力标记为最终完成。

实现与收尾要求：

1. 先执行本文开头的「前端 Route 强制闸门」：运行 `resolve_frontend_html_route.py`，完整读取对应 route SKILL.md 到 EOF，再把该 route SKILL 中定义的 `write_todos` 主流程转成可见清单；未完成这一步，不得读取 parser、读取 HTML 或改前端代码。
2. 判断 HTML 路线并读取对应 SKILL：按上方内部分流规则选择 `with-standard-html/SKILL.md` 或 `with-absolute-html/SKILL.md`；最终 route 以 `{FEATURE_DIR}/FRONTEND_ROUTE.json` 为机器事实。
3. 标准 HTML 路线进入 `with-standard-html/SKILL.md` 后，必须按其 `write_todos` 完成路线判定、页面模块、转换、Ant Design 审计四类清单，并带 `routeType`、`absoluteSignalsCleared`、`moduleTodosReady`、`conversionTodosReady`、`uiLibraryTarget`、`antdMode`、`auditRequired` 交接状态转给 `references/standard-html-parser.md`。
4. 绝对定位高保真路线进入 `with-absolute-html/SKILL.md` 后，必须按其 `write_todos` 完成页面模块清单、独立脚本清单、上下文读取与 `references/html-parser.md` 转交；脚本清单至少覆盖参数确认、执行脚本、检查 `.frontend/html-analysis/<task-stem>.*` 产物、失败降级。脚本异常不阻塞主流程，降级后以原始 HTML 为唯一视觉源继续。
5. 主线结束前必须做样式细节收尾，补齐 padding、边框、圆角、阴影、字色、字号、字重、行高、内外边距、对齐、状态色、文本内容、hover / active / selected 等用户一眼能看出的差异。
6. 主线里完成页面拆分，以及函数、常量、类型、helper / hook、图表配置与同页公共内容抽取；不要把明显的可维护性工作留给后续 `/autodev-reviewer`。
7. 执行本分支验证，确认已回到 `/autodev-code` 主流程后，再按本文件的「执行协议」与「完成条件」收尾；在显式完成“回到 `/autodev-code` 主流程并按 code 节点收尾”前，不得把本分支视为完成。

分支返回契约：两个 HTML 路线完成后都必须返回 `/autodev-code` 主流程，由 code 根技能继续项目级验证、统一前端回检、 `code_done` checkpoint 推进。HTML 分支内部不得发起独立回检选择，不得调用或引用已移除的 `autodev-frontend`、`frontend_done` 或内部回检路线。返回主流程时必须带回回检输入：生成/修改的目标源码路径、原始 HTML 路径、可用的 `.frontend/html-analysis/*.json` 路径（没有则写 none）、`PLAN.md` 路径（没有则写 none）、`uiLibraryTarget`、`antdMode`、`auditRequired`。

## 准入检查

先读快照并捕获 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "{feature}")
```

准入只验证 Plan 声明的 workspace、validation cwd 与项目 manifest 是否匹配，不执行编译命令。批次质量模式和命令以 `batchValidation.mode` / `batchValidation.commands` 为唯一事实源。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "{feature}")
```

## 执行协议

### Code 会话入口

每次进入 Code 阶段或在新对话恢复 Code 时，第一条 runner 命令必须是：

```bash
python "${pluginPath}/hooks/task_runner.py" code-session --feature "${feature}"
```

该命令只读取根计划和批次摘要；若根计划处于 `awaiting_next_conversation`，它会校验并消费 `BATCH_HANDOFF.json`，自动激活 `nextBatchId`，无需用户提供 batch ID。必须严格按返回的 `action` 分支：

- `execute_active_batch`：只加载返回的 `activeBatchId` 对应批次，按下方 Task 协议执行。
- `run_batch_task_validation`：当前批次所有 TASK 实现均已收口为 `implemented`；创建 deferred validation run，并启动一个批次级只验证子代理，由它串行运行该批全部 TASK 与 batch 校验命令。
- `resume_batch_task_validation` / `recover_batch_task_validation_closure`：把返回的原 `activeRunId/currentTaskId` 和完整批次验证上下文交给一个批次级只验证子代理恢复，不得创建新实现 run 或跳到其他 TASK。
- `fix_or_retry_task_validation`：源码未变化时创建新的 task-validation run 从失败 TASK 重试；需要修改源码时先执行 `start-validation-repair`。
- `run_batch_check_in_validation_subagent`：deferred 批次的 TASK 已全部验证通过；把返回的 `validationContext` 交给一个批次级只验证子代理，由它执行下方「批次验证与重验证」，不得由主 agent 接管。
- `run_batch_check`：仅用于未启用 deferred policy 的旧计划；当前批次 TASK 已全部完成，执行下方「批次验证与重验证」。
- `recover_task_covered_batch`：仅用于未启用 deferred policy 的旧计划；最后一个 TASK evidence 已写入但批次收口尚未绑定时，inspect 并 recover 原 TASK run。
- `run_project_check`：所有批次验证已完成且配置了额外项目检查，跳过 Task 队列，执行「全部任务完成后的验证」。
- `code_done_ready`：批次和项目级最终校验都已完成，不重复执行 Task 或 project-check，继续 Code 完成门禁。

入口返回失败、活动批次缺失或 handoff 不一致时必须停止，不得猜测 batch ID、直接编辑计划或绕过入口启动 Task。`code-session` 只允许在 Code 会话入口调用；收到批次完成的 `stop_and_open_new_conversation` 后，不得在同一对话再次调用 `code-session`。

### 建立执行上下文与任务队列

- 只读取根 `plan.json` 的批次摘要和 `activeBatchId` 对应的一个 `plans/Bxxx/plan.json`，不得把其他批次完整 task 契约加载进当前对话。使用 `write_todos` 映射当前批次任务，状态用待做 / 进行中 / 实现已就绪 / 完成 / 失败；每次只置一个任务为进行中。根 plan 含 `tasks` 或缺少批次时回流 `/autodev-plan` 重建。

### 选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

### 执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。启动前必须确保每个业务仓库都通过 `.gitignore` 或 `.git/info/exclude` 忽略 `.cmbdevclaw/large_tool_results/`；runner 只校验该契约，不会代写业务仓库。未命中 ignore 时先配置窄规则，再执行 start。

在修改业务代码前必须启动任务运行并保存 Git 快照：

```bash
python "${pluginPath}/hooks/task_runner.py" start --feature "${feature}" --task-id "<TASK_ID>" --code-workspace "<TASK_WORKSPACE>"
```

保存输出中的 `runId`。同一 feature 同时只允许一个活动 task run；重复执行、异常中断或工具崩溃后，不得新建 run 绕过，必须使用 `inspect` / `recover` / `abort` 处理原 run。
`--code-workspace` 同时是 Git 仓库定位入口和 task workspace 基准，必须选择 task `workspaceRef` 指向的实际仓库；请求路径必须与 task `scope.workspaceRoots` 声明的位置完全一致。前端 TASK 不得传后端仓库，后端 TASK 也不得传前端仓库。即使传模块子目录，runner 也会解析并快照整个 Git 根；`scope.paths` 只作为相对该模块的提示性范围，不是实现文件白名单。start 会先验证 workspace 绑定、validation `repo/cwd` 目录和 Maven/Gradle/Node 等 manifest，再保存 `scopePathBase=requested_code_workspace`、`workspacePrefixes` 和 `resolvedScopePaths`；任一 workspace 或命令前置校验失败时尚未创建 run，不得先写代码再重试。TASK finish 会自动记录该 workspace 内全部有效 Git 变更，DTO/domain/test/resources/迁移/配置等同 workspace 文件无需补 scope 或重建 digest；跨 workspace 的变更才需要修复工作区并重试。`finish-implementation` / `abort` / `resume` 必须继续传相同请求路径并保持同一个 run；同一 Git 根下替换成其他模块会返回 `task_run_requested_workspace_mismatch`。快照比较 Git 可见文件的内容哈希，包含未跟踪且未忽略文件；`staging / unstaging` 不会制造内容变更，也不能恢复丢失的 start 基线。收到 `active_task_run_exists` / `active_feature_task_run_exists` 时必须 inspect 并继续现有 run，不得为了重新 start 而 abort。TASK 的实现 Evidence 使用该 TASK 从首次 start 到最终实现收口之间所有 run 的累计 `fileChanges/changedFiles`；abort 只结束一次 run，不清除已记录的变更。若累计变更非空，禁止用 `--no-code-change-why` 把实现伪装成已有代码。
`start` 会固化当前 task 契约哈希，并对请求 workspace、scope 投影和初始 Git 快照写入 `integritySha256`。run 活动期间不得修改该 task 的 goal/scope/AC/validationCommands 等计划字段，也禁止直接编辑 `plan.json`、批次 `plan.json` 或 `.task-runs/**/*.json`、手工重算 digest/hash；否则 runner 返回 `task_run_integrity_mismatch` 或计划完整性错误。确需修复 Plan contract 时按下方协议保存 patch、force abort、由 Plan writer 整体重建契约和基线后再应用 patch；普通新增 DTO/XML/测试文件不需要修 scope 或重建 digest。

每个 TASK 必须且只能绑定一个 `workspaceRef`；只向 runner 传该 TASK 的 workspace，禁止对 TASK start/finish/validate 重复传入其他仓库。若一个需求闭环需要修改多个业务仓库，Plan 必须拆成多个 TASK 并用 deps 串联；跨仓库集成检查只能放在 `projectValidationCommands`。Plan 中具名 workspace 的 validation command 必须用 `repo` 指明 Git 根目录名；changed/supporting 路径使用 `repoId:relative/path`。无论涉及多少仓库，`evidence/` 与 `.task-runs/` 只能写入 feature 产物目录，禁止写入任一业务仓库。

Batch 同样只能包含同一 lane 且同一 `workspaceRef` 的 TASK；前后端不会进入同一 Batch，同一 lane 的不同仓库也不会进入同一 Batch。启动批次验证、逐 TASK 验证和 `batch-check` 时只传该 Batch 的唯一 workspace。
2. 读唯一 `plan.json` 中的结构化执行契约。必须先运行任务上下文解析脚本：

```bash
python "${pluginPath}/hooks/code_task_context.py" --feature "${feature}" --task-id "<TASK_ID>" --code-workspace "<BUSINESS_REPO>"
```

该脚本输出是当前 task 的上游上下文事实源，必须读取其中的 `taskContract`、`resolvedSpecRefs`、`resolvedDesignRefs`、`explorationCaches` 和 `explorationPolicy`。只传 `taskContract.workspaceRef` 对应的一个 `--code-workspace`。`specRefs` / `designRefs` 一律按 `artifactFeatureDir`（`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}`）解析，不得按业务代码仓库 cwd 直接读取 `specs/...`、`design.md`、`PLAN.md`；业务代码仓库 cwd 只用于定位源码、测试和执行验证命令。若脚本返回 `missing_ref_file` / `missing_ref_anchor` / `invalid_plan_json` / `task_not_found`，停止编码并回流 `/autodev-plan` 修复产物引用，不得猜测补路径。

必须按每个仓库的缓存状态执行，不能只看总体最严 policy 后忽略其他仓库：

若 `explorationPolicy.status=unavailable`，说明没有传入业务仓库，必须停止并补传 `--code-workspace`，不得绕过缓存协议继续编码。

- `missing` / `stale`（`full_bounded_explore`、`requiresRecord=true`）：读取 `staleReasons` / `criticalHits`，完成一次有界探索；先读取 `code_exploration_writer.py contract`，并确认业务仓内存在的 `.autobizdevops/`、`.cmbdevclaw/` 等运行期目录已被 Git ignore，再对每个 `explorationCaches[]` 仓库分别调用一次仅含该仓库 `--code-workspace` 的 `record --expected-cache-sha256 <cacheSha256> --body-file <JSON>` 写入完整 findings。重新运行 `code_task_context.py`，对应仓库必须成为 `fresh` 后才能改业务代码。`headCommit` 变化即使文件哈希相同也按 `stale` 处理，这是避免 rebase/merge 后误复用的保守失效策略。
- `fresh`（`task_scope_only`）：直接使用缓存 findings，只定点读取当前 Task scope / entrypoint 涉及文件；不得重复探测项目框架、模块布局和测试运行器。
- `fresh_with_trusted_changes`（`task_scope_only`）：implementation evidence 已解释本次仓库变化，且缓存与当前 Task 属于同一 active batch；只定点读取当前 Task scope / entrypoint，不执行 `record` 或 `patch`。这是批次内的 `deferredCacheUpdate`，变化会在批次边界统一吸收。
- `reusable_with_changes`（`targeted_reread`、`requiresPatch=true`）：只读取 `changedPaths + 1-hop 依赖 + 当前 Task scope`，再对每个 `explorationCaches[]` 仓库分别调用一次仅含该仓库 `--code-workspace` 的 `patch --expected-cache-sha256 <cacheSha256> --body-file <JSON>` 确认；即使事实未变化，也必须传完整 `reviewedPaths` 和空 `findingUpdates`。`findingUpdates` 对每个字段采用整类替换语义，不得只传该列表的一部分；writer 会在合并后重新校验完整 findings。重新运行 context，成为 `fresh` 后才能改业务代码。
- 同一 active batch 内，普通且已被 evidence 解释的源文件变化不要求每个 Task 都 patch；进入下一批次时再统一 patch。即使仍在同一批次，命中 `shared/integration` 路径也必须立即 targeted reread/patch；构建配置、迁移/schema、HEAD 变化或无法由 evidence 解释的路径仍进入 `stale`，重新做有界 `record`。`transientValidationFiles` 不参与探索差异，但同一路径后续成为正式变更时必须重新纳入。

`fresh/reusable_with_changes 时禁止无边界全仓探索`：不得重新运行无范围全仓 `rg`、递归目录 listing 或框架发现。缓存只能通过 `code_exploration_writer.py` 修改，禁止直接编辑 `cache/code-exploration/**/*.json`。共享路径只更新当前 executionLane；另一 lane 在后续 inspect 时独立判定。runner 能返回 machine policy，但宿主未提供工具调用遥测，无法独立证明 Agent 执行过多少次搜索，这是协议层约束。

必须读取当前 task 的 `workspaceRef`、`goal`、`scope`、`validationBoundary`、`implementationPoints`、`acceptanceCriteria`、`nonGoals`、`splitRationale`（若存在）、`specRefs`、`designRefs`、`validationCommands`；不得只根据 `title` / `specRefs` 脑补实现范围。缺少 `workspaceRef` / `goal` / `scope` / `validationBoundary` / `implementationPoints` / `acceptanceCriteria` / `nonGoals` 时停止编码，回到 `/autodev-plan` 补齐，不得边做边猜。先依各输入的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。`splitRationale` 只用于理解合并背景，不得作为扩大 scope 的理由。
3. 改代码前按 `explorationPolicy` 进行首次有界探索或缓存定点复核；先识别或复用项目分层、命名、错误处理、校验、日志、测试风格，并读取测试插件/provider 版本（例如 Maven Surefire 与 JUnit 4/5 兼容性）、Spring 测试启动入口和现有可运行测试，形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。不得在测试失败后才猜测框架版本；计划为 `integration_test` 时不得为了通过验证降级成只 mock service 的 controller 单元测试。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：只实现 `scope` / `implementationPoints` / `acceptanceCriteria` 指向的业务范围；`scope.paths` 只是相对 workspace 的文件提示，不是逐文件白名单，因实现需要新增的 DTO/domain/test/resources/迁移/配置会由 runner 自动归集。不得实现 `nonGoals` 中列出的内容。观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 任务需要写 / 改测试时，遵循 `${pluginPath}/skills/references/test-quality.md`：站在 seam 上验证、期望值来自独立事实源（勿同义反复）、mock 只在系统边界。
   - 只为本轮实现验证而临时创建、任务完成后不提交的测试文件，必须保持未跟踪且未暂存，不得 `git add`。runner 仅将同时满足“本轮新建、位于请求 workspace 下的 `src/test`、`test`、`tests` 测试根、`finish-implementation` 时仍未跟踪且未暂存”的文件归入 `transientValidationFiles`；这些文件保留到批次 TASK 验证结束，但不进入正式 `changedFiles`。已跟踪、已暂存或 start 前已存在的测试文件仍是正式代码变更。
   - TASK 实现期间不得手工执行 `validationCommands`，也不得执行 compile/build/typecheck/lint 或当前 `batchValidation.commands`。所有 TASK 命令统一在批次实现结束后由独立验证子代理通过 runner 执行。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 实现完成必须只走 `finish-implementation`。该命令检查 scope 和 start 快照、写 `action=implementation` Evidence，并把 TASK 从 `in_progress` 置为 `implemented`；它不运行 `validationCommands`，不写 `completionEvidenceIds`，也不把 TASK 置为 done。deferred 计划调用旧 `complete` 会被明确拒绝：

```bash
python "${pluginPath}/hooks/task_runner.py" finish-implementation --feature "${feature}" --task-id "<TASK_ID>" --run-id "<RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

若返回 scope/workspace 错误，仍按原 run 修正或回流 Plan，不得重新 start 掩盖基线。`implemented` 是实现终态，不等于业务完成；同批后继 TASK 可以依赖它，跨批依赖、handoff、project-check 和 code-done 只接受 `done`。

实现 Evidence 尚未落盘时发生进程中断，才使用原 runId 恢复：

```bash
python "${pluginPath}/hooks/task_runner.py" resume --feature "${feature}" --task-id "<TASK_ID>" --run-id "<ORIGINAL_RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

确实没有文件变更时，不得伪造 changedFiles，也不得把空 diff 当遗漏。必须说明原因并提供至少一个仓库内已有实现/测试文件；该任务还必须有 required 的行为、集成、E2E 或静态验证，compile/build/typecheck/lint 只属于批次验证，不能配置在 TASK：

```bash
python "${pluginPath}/hooks/task_runner.py" finish-implementation --feature "${feature}" --task-id "<TASK_ID>" --run-id "<RUN_ID>" --code-workspace "<BUSINESS_REPO>" --no-code-change-why "<WHY_EXISTING_IMPLEMENTATION_IS_SUFFICIENT>" --supporting-file "<RELATIVE_PATH>"
```

`--supporting-file` 必须是仓库根相对路径；多仓库时使用 `repoId:relative/path`。`--no-code-change-why` 只用于 start 前已经存在且经行为验证确认满足契约的实现，不得用它绕过误 abort、重启 run 或 staging 操作造成的空 diff；runner 会拒绝与历史 aborted run 变更冲突的 no-code claim。

`finish-implementation` 成功后，把该 TASK 在 `write_todos` 标记为“实现已就绪/待验证”，不是完成；返回 `continue_active_batch`、`continueCurrentBatch=true` 和 `nextTaskId` 时，同批仍有可执行任务时禁止询问用户是否继续，立即进入下一个 Task。最后一个 TASK 返回 `run_batch_task_validation` 后立即进入下方流程。

### 独立子代理执行批次全部校验

主 agent 先创建验证 run：

```bash
python "${pluginPath}/hooks/task_runner.py" start-batch-task-validation --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

保存输出的完整 `validationContext`，其中必须包含 `featureId/batchId/runId/taskOrder/currentTaskId/requestedCodeWorkspaces/batchSnapshotSha256`。主 agent 只启动一个全新的批次验证子代理，并把该对象原样放入启动 prompt；不得只传自然语言摘要、遗漏 batch 上下文或自行改写 `code_workspace`。子代理禁止修改源码、测试、配置、Plan 和 Evidence，只能按 `currentTaskId` 串行调用：

```bash
python "${pluginPath}/hooks/task_runner.py" validate-batch-task --feature "${feature}" --batch-id "<BATCH_ID>" --task-id "<CURRENT_TASK_ID>" --run-id "<VALIDATION_RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

runner 按计划顺序执行该 TASK 全部命令，验证前后都校验同一 `batchSnapshotSha256`，并写 `validationTarget=batch_final_snapshot` Evidence。返回 `requiredAction=continue_batch_validation_subagent` 和新的 `currentTaskId` 时，同一个子代理继续下一个 TASK；禁止主 agent 接管命令或并行启动其他验证子代理。

最后一个 TASK 通过且返回 `requiredAction=run_batch_check_in_validation_subagent` 时，仍由同一个子代理执行批次级 compile/build/typecheck/lint：

```bash
python "${pluginPath}/hooks/task_runner.py" batch-check --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

只有该子代理拿到 `stop_and_open_new_conversation`、`code_done_ready` 或 `run_project_check`，才把最终结果交回主 agent 并结束。这样 TASK 与 batch 两层所有强校验命令都由同一个独立子代理运行。

`taskValidation.status=running` 期间工作区处于硬冻结：runner 拒绝启动/收口实现任务，Plan Writer 拒绝写计划或渲染产物，Evidence Store 拒绝 validation runner 之外的任何追加。子代理不能通过直接调用其他 writer 绕过冻结。

子代理/进程中断时使用同一 runId 和 currentTaskId 恢复，已写 Evidence 的命令不会重复。若 runner 返回 `requiredAction=fix_validation_environment_and_retry_batch_validation` 或 `fix_validation_environment_and_retry_same_run`，必须停止并把 `userMessage` 原样提示用户；用户修复环境后重新运行校验，已有 run 时必须继续使用同一 runId/currentTaskId，不能 abort、改代码或重建 digest。只有普通 required 校验失败才创建新的 task-validation run；需要修改源码时才执行 `start-validation-repair --task-id <FAILED_TASK_ID>`，修复后重新 `finish-implementation`。任何源码修复都会清空整批当前 completion 指针并从 T001 重验，历史 Evidence 保留。

全部 TASK 验证通过后才把 TASK 置 done。`task_covered` 此时生成 `batch_closure`；`commands` 模式返回 `requiredAction=run_batch_check_in_validation_subagent`，由同一个子代理继续下方额外质量门禁。

### 批次验证与重验证

本节只适用于 `batchValidation.mode=commands`。deferred policy 下只能由上方已经启动的批次验证子代理执行；主 agent 不得接管。第一次执行当前批次验证时不传 run ID；runner 创建 `.batch-runs/<BATCH_ID>/<RUN_ID>.json`，按该 lane 的 `batchValidation.commands` 运行真正补充 TASK 盲区的 compile/build/typecheck/lint，并分别写入 `action=batch_validation` evidence：

```bash
python "${pluginPath}/hooks/task_runner.py" batch-check --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

- 返回 `requiredAction=fix_batch_and_retry_same_run` 时，保留返回的 `runId`，只在当前批次请求 workspace 内修复问题，然后用完全相同的 workspace 和 `--run-id "<RUN_ID>"` 重跑 `batch-check`。`scope.paths` 不构成修复白名单；批次失败 evidence 只追加，不覆盖；不得新建 run 隐藏失败历史。验证命令若修改 Git 可见文件会被拒绝。
- runner 在首条命令前把 `activeRunId` 投影到 plan；命令 evidence 全部写完后先保存 `status=evidence_written`，再幂等绑定 plan。进程在 evidence append、TASK 重验证请求或最终批次绑定后中断时，重新调用 `code-session` 取得原 `activeRunId`，再以同一 `--run-id` 执行 `batch-check`；runner 会采用已写入 stream 的 evidence，不重复执行已完成命令。
- required 命令决定批次成败及 `latestPassEvidenceIds`；optional 成功或 optional 失败 evidence 都只追加到批次历史和 run attempt，不得让 optional 失败阻断 code-done，也不得把 optional 记录伪装为 required latest pass。
- 修复路径超出当前批次请求 workspace 时返回 `batch_fix_outside_workspace`，必须修复工作区后用同一 batch run 重试；同 workspace 内的任何修复都会改变批次最终快照，因此无论修改了哪个文件，都清空整批当前 completion 指针并按稳定顺序重验全部 TASK，不再按 `scope.paths` 归因。
- 修复后的 batch-check 返回 `requiredAction=run_batch_task_validation` 时，保留原 batch run ID，整批 TASK 当前 validation completion 指针失效；创建新的 deferred task-validation run，启动一个新的批次级验证子代理串行重验全部 TASK。全部通过后仍由该子代理使用原 batch run ID 再跑最终 batch-check。`pendingRevalidation` 只保存触发和被替代 Evidence 关联，不再启动另一套 `start/complete` 重验。
- batch-check 直接通过且没有批次修复，或重验证后的最终 batch-check 通过时，runner 才把批次置为完成。非末批会返回 `stop_and_open_new_conversation` 与 `batchHandoff`；末批则进入可选项目检查或完成门禁。

若 `batch-check` 返回 `requiredAction=stop_and_open_new_conversation`、`stopAfterBatch=true` 和 `batchHandoff`，当前批次已经结束。必须原样输出 `userMessage` 提醒用户打开新对话，然后立即结束当前回复；不得继续读取或实现下一批，不得运行 smoke/project-check/checkpoint 命令，也不得在同一对话再次调用 `code-session`。新对话重新进入 Code 后由会话入口自动检查并激活下一批。`BATCH_HANDOFF.json` 始终保存在 feature 产物目录，入口激活时消费并删除。

宿主未提供 conversation ID，因此 runner 无法从进程参数中证明调用来自新对话；`requiresNewConversation` 是供宿主和 Agent 执行的协议层约束，不是 runner 可独立验证的身份凭据。`activate-batch` CLI 仅保留给兼容或诊断场景，正常 Code 流程不得直接调用。
7. 若 `SMOKE_TEST_PLAN.json`存在，按其中 `tests[]` 生成或补齐旁路冒烟测试源码/脚本。每条 smoke 必须按计划中的 `seam` 站在公开边界上验证，不测私有方法、不查内部实现细节；按 `verticalSlice` 一次只实现一个最小闭环，不把多个场景合成一条大烟测；按 `mockPolicy` 只 mock 系统边界，不 mock 自有模块或内部协作者。冒烟测试必须是 opt-in：Java/Spring 可用 `*SmokeIT` + `-Psmoke`，前端可用 `tests/smoke/` + 单独 smoke script，CLI/API 可用 `scripts/smoke/`；这些源码/脚本只用于本地验证，可以放在业务项目测试目录，但不得进入业务项目 Git 托管。生成后必须确保 `sourcePath` 被目标项目 Git 忽略，优先把精确路径或窄范围 AutoDev smoke 模式写入 `.git/info/exclude`，不要把 smoke 源码 `git add`，也不得让默认 `validationCommands` 无意中跑到慢/脆的冒烟。全部强 validation 通过后，运行：

```bash
python "${pluginPath}/hooks/run_advisory_smoke.py" --feature "${feature}"
```

`run_advisory_smoke.py` 会写入 `SMOKE_RESULT.json` 并向 `EVIDENCE.jsonl` 追加 `action=smoke` evidence。冒烟 PASS/FAIL/BLOCKED/SKIPPED 都只作为旁路风险信号：不得把 smoke evidence 写入 batch task 的 `evidenceIds`，不得把冒烟失败改成任务失败，不得因为 `SMOKE_RESULT.json.verdict` 非 PASS 而阻断 `code_done`。

若 `run_advisory_smoke.py` 在执行前置检查阶段返回非 0（例如 `sourcePath` 对应测试源码不存在、测试条目非法、命令缺失、sourcePath 已被 Git 跟踪或未被 Git ignore 命中），这表示 Code 阶段尚未按 `SMOKE_TEST_PLAN.json` 补齐本地冒烟测试资产；必须先补齐测试源码/修正计划/更新 `.git/info/exclude` 后重跑。只有冒烟命令已经实际执行后的 PASS/FAIL/BLOCKED/SKIPPED 结果才属于不阻断流转的旁路风险信号。

策略边界：TASK `validationCommands`、`task_covered` 的 `action=batch_closure`、命令模式的 `batchValidation.commands` / `action=batch_validation` evidence 与 `code_done_gate` 都是强门禁；`SMOKE_TEST_PLAN.json` / `SMOKE_RESULT.json` 只表达旁路冒烟风险。

> 一致性：任务的依据在对应上游产物里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

###  全部任务完成后的验证

只有 Code 会话入口返回 `run_project_check`，即全部批次验证通过且根 plan 配置了非空 `projectValidationCommands` 后，才通过 runner 跑额外的跨 lane/跨批次项目检查。其 kind 只允许 `integration_test/e2e_test/static_check`，不得重复 batch profile 的 `argv + cwd + repo`；提前执行会被拒绝。项目检查单独写 `action=project_check` evidence，不参与任一 TASK 的验收覆盖。未配置项目检查时入口直接返回 `code_done_ready`，不要求伪造一轮最终编译：

```bash
python "${pluginPath}/hooks/task_runner.py" project-check --feature "${feature}" --code-workspace "<BUSINESS_REPO>"
```

如本轮触发 HTML 分支，或变更了前端源码（`.tsx` / `.jsx` / `.ts` / `.js` / `.vue` 及相关样式文件），全部批次验证以及配置了的项目级验证通过后必须运行统一前端回检；只有用户明确要求“跳过回检 / 先不回检 / 不要跑回检 / 先不验证”时才跳过，并在最终摘要写 `reviewStatus=skipped-by-user`。默认命令：

```bash
python "{PLUGIN_ROOT}/skills/autodev/autodev-code/references/frontend-html/scripts/review_runner.py" --target "<file-or-dir>" --antd-audit auto --format markdown
```

- `--target` 指向本轮生成/修改的前端页面、组件文件或包含它们的目录；`--source-html`、`--analysis`、`--plan` 只在对应文件真实存在时追加，标准 HTML 路线没有 analysis JSON 时不要传 `--analysis`。
- 退出码 `0`：回检通过，记 `reviewStatus=passed`。
- 退出码 `1`：读取 findings；`must-fix` 是阻塞项，按最小修复同轮重跑，默认最多 2 轮；仅剩 `suggestion` 时允许推进，但必须记录 `reviewStatus=has-suggestions` 和建议项。
- 退出码 `2`：回检执行异常，记 `reviewStatus=failed`，不得声称完整验证通过，不得推进 `code_done`，除非用户明确选择跳过回检。
- 本回检只属于 code 阶段前端生成质量自检，不替代后续 `/autodev-reviewer` 的独立需求实现评审。

项目级验证与必要的统一前端回检均收敛后：

```bash
python "{PLUGIN_ROOT}/hooks/stage_gate.py" validate --stage dev.code --feature "{FEATURE_ID}"
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件


为完成任务必须改队列未直接提到的业务文件时，先确认与各输入产物确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 所有任务都有 `action=implementation` evidence，且批次最终快照上的 required TASK validation 全部通过；`checkedCriteria` 并集覆盖全部 AC；completion evidence 与命令、validation runId、`batchSnapshotSha256`、当前 implementation revision/pointer 一致。未启用 deferred policy 的旧计划只走旧 runner 路径，不允许两种模式混跑。
- `evidence/EVIDENCE.jsonl`、`EVIDENCE.index.json` 与每条 task/batch/project validation evidence 的 `ev_XXXX.log` 完整性和哈希校验通过；没有新生成的 `ev_XXXX.json` sidecar。
- 每个批次最多 5 个任务；该批 `taskValidation.status=passed` 后额外批次质量门禁已通过。存在批次修复时整批 TASK completion 指针先失效，再产生 `attemptType=batch_revalidation` 的新完成 evidence，随后原 batch-check run 再次通过；非末批之后才停止当前对话并生成 `BATCH_HANDOFF.json`。
- 若 `SMOKE_TEST_PLAN.json`存在：已按计划生成/补齐冒烟测试源码并确认其被目标项目 Git 忽略，已运行 `run_advisory_smoke.py`；`SMOKE_RESULT.json` 已写入。`SMOKE_RESULT.json.verdict` 为 `FAIL` / `BLOCKED` / `SKIPPED` 时，记录为风险但不阻断本阶段流转。
- 必要验证通过；每批最新 batch-check 晚于该批当前 TASK 完成 evidence，且所有 required 项通过；配置了项目检查时，最新 `project-check` 还必须晚于全部当前 TASK/batch evidence（`code_done` 会再次校验 plan/evidence/run 闭环）。
- HTML 分支或前端源码变更已完成统一前端回检，或用户明确跳过；仍有 `must-fix` / 执行异常时不得推进 `code_done`。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
