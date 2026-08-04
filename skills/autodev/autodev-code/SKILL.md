---
name: autodev-code
description: 进行代码实现。
version: v1.7.0804
---

# /autodev-code — 代码执行

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```

## 准入检查


```bash
python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}"
```

准入只验证 Plan 声明的 workspace、validation cwd 与项目 manifest 是否匹配，不执行编译命令。批次质量模式和命令以 `batchValidation.mode` / `batchValidation.commands` 为唯一事实源。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_in_progress
```

## 执行协议


### Code 会话入口

每次进入 Code 阶段或在新对话恢复 Code 时，第一条 runner 命令必须是：
```bash
python "${pluginPath}/hooks/task_runner.py" code-session --feature "${feature}"
```

若根计划处于 `awaiting_next_conversation`，它会校验并消费 `BATCH_HANDOFF.json`，自动激活 `nextBatchId`，无需用户提供 batch ID。必须严格按返回的 `action` 分支：

- `execute_active_batch`：只加载返回的 `activeBatchId` 对应批次，按下方 Task 协议执行。
- `run_batch_task_validation`：当前批次所有 TASK 实现均已收口为 `implemented`；创建 deferred validation run，并启动一个批次级只验证子代理，由它串行运行该批全部 TASK 与 batch 校验命令。
- `spawn_batch_validation_subagent`：立即用一次“子任务执行”创建批次级只验证子代理，并把 `validationContext` 原样传入；`validationSubagentMode=start/resume/recover_closure/batch_check` 只说明子代理从哪个阶段接续。完整协议见下方「独立子代理执行批次全部校验」，不要在此就地展开。
- `fix_or_retry_task_validation`：源码未变化时可创建新的 task-validation run 从 `failedValidationTaskId` 重试；需要修改源码时，必须先对 runner 返回的 `repairOwnerTaskIds` 之一执行 `start-validation-repair`，不得默认把验证游标当成修复责任 TASK。若源码已在 repair 启动前被修改，显式执行 `start-validation-repair --adopt-workspace-changes`；runner 只采用请求代码工作区内的变化，并把父 validation run、采用文件与 repair 次数写入新 run 和后续 implementation Evidence。每个责任 TASK 最多做 2 次 validation repair；第 2 次修复后仍失败时 runner 自动记录延期问题并继续队列。
- `run_batch_check`：仅用于未启用 deferred policy 的旧计划；当前批次 TASK 已全部完成，执行下方「批次验证与重验证」。
- `recover_task_covered_batch`：仅用于未启用 deferred policy 的旧计划；最后一个 TASK evidence 已写入但批次收口尚未绑定时，inspect 并 recover 原 TASK run。
- `run_project_check`：所有批次验证已完成且配置了额外项目检查，跳过 Task 队列，执行「全部任务完成后的验证」。
- `code_done_ready`：批次和项目级最终校验都已完成，不重复执行 Task 或 project-check，继续 Code 完成门禁。

入口返回失败、活动批次缺失或 handoff 不一致时必须停止，不得猜测 batch ID、直接编辑计划或绕过入口启动 Task。`code-session` 只允许在 Code 会话入口调用；收到批次完成的 `stop_and_open_new_conversation` 后，不得在同一对话再次调用 `code-session`。

### 建立执行上下文与任务队列

- 只读取根 `plan.json` 的批次摘要和 `activeBatchId` 对应的一个 `plans/Bxxx/plan.json`，不得把其他批次完整 task 契约加载进当前对话。使用 `write_todos` 映射当前批次任务，状态用待做 / 进行中 / 实现已就绪 / 完成 / 失败；每次只置一个任务为进行中。根 plan 含 `tasks` 或缺少批次时回流 `/autodev-plan` 重建。

### Batch 级代码探索闸门

每个 active Batch 只允许在首个 TASK run 启动前建立一次仓库认知。选择该 Batch 第一个依赖已满足的待做 TASK，先运行 `code_task_context.py`，读取输出中的 `batchExplorationScope`、`explorationCaches`、`explorationPolicy` 和 `explorationDirective`。`batchExplorationScope` 是本批全部 TASK 的 `modules/entrypoints/paths/pages/dataObjects/validationCommands` 并集，首次有界探索必须以该并集为边界，不得只探索第一个 TASK，也不得扩展到其他 Batch。

`explorationDirective.phase=batch_bootstrap` 时按 `scopeSource=batchExplorationScope` 完成本批探索接续；`phase=task_guard` 且 `fullExplorationAllowed=false` 时只能使用 `taskContract.scope` 做定点复核，不得把仍然返回的 Batch scope 当成重新全探授权。按下文缓存状态完成 `record` 或 `patch`，并重新运行 context，直到所有仓库均成为 `fresh` 后，才能启动本批第一个 TASK run。进入后续 TASK 时仍要重跑 context 作为轻量快照守卫；同批前序带有效 `latestImplementationEvidenceId` 的 `implemented/validating/failed` TASK，以及 validation repair 中带该证据的 `in_progress` TASK，能完整解释且最终快照匹配时必须返回 `fresh_with_trusted_changes`，不得重新探测项目框架、模块布局或测试运行器。若后续 TASK 返回 `stale`，先根据 `staleReasons/criticalHits/unexplainedPaths` 处理异常漂移，不得把每个 TASK 的正常实现变化当成新的全量探索机会。

###  选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目。

###  执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。启动前必须确保每个业务仓库都通过 `.gitignore` 或 `.git/info/exclude` 忽略 `.cmbdevclaw/large_tool_results/`；runner 只校验该契约，不会代写业务仓库。未命中 ignore 时先配置窄规则，再执行 start。

在修改业务代码前必须启动任务运行并保存 Git 快照：

```bash
python "${pluginPath}/hooks/task_runner.py" start --feature "${feature}" --task-id "<TASK_ID>" --code-workspace "<TASK_WORKSPACE>"
```

保存输出中的 `runId`。同一 feature 同时只允许一个活动 task run；重复执行、异常中断或工具崩溃后，不得新建 run 绕过，必须使用 `inspect` / `recover` / `abort` 处理原 run。收到 `active_task_run_exists` / `active_feature_task_run_exists` 时必须 inspect 并继续现有 run，不得为了重新 start 而 abort。业务源码写入前，写入闸门会严格校验完整 v2 run、路径身份、`executionMode=code` 和密封探索证明；`verified_existing` / `external_dependency` 不允许写业务源码。每次普通 start 都会重新检查当前探索缓存；abort 后不得复用过期 `fresh`。仅验证修复或纯 Evidence 状态导致的受控重试允许在重新检查后继承旧证明，并必须记录 `inheritedFromRunId`、本次观察状态和 stale 原因。
`--code-workspace` 同时是 Git 仓库定位入口和 task workspace 基准，必须选择 task `workspaceRef` 指向的实际仓库；请求路径必须与 task `scope.workspaceRoots` 声明的位置完全一致。即使传模块子目录，runner 也会解析并快照整个 Git 根；`scope.paths` 只作为相对该模块的提示性范围，不是实现文件白名单。start 会先验证 workspace 绑定、validation `repo/cwd` 目录和 Maven/Gradle/Node 等 manifest，再保存 `scopePathBase=requested_code_workspace`、`workspacePrefixes` 和 `resolvedScopePaths`；任一 workspace 或命令前置校验失败时尚未创建 run，不得先写代码再重试。TASK finish 会自动记录该 workspace 内全部有效 Git 变更，DTO/domain/test/resources/迁移/配置等同 workspace 文件无需补 scope 或重建 digest；跨 workspace 的变更才需要修复工作区并重试。`finish-implementation` / `abort` / `resume` 必须继续传相同请求路径并保持同一个 run；同一 Git 根下替换成其他模块会返回 `task_run_requested_workspace_mismatch`。快照比较 Git 可见文件的内容哈希，包含未跟踪且未忽略文件；`staging / unstaging` 不会制造内容变更，也不能恢复丢失的 start 基线。TASK 的实现 Evidence 使用该 TASK 从首次 start 到最终实现收口之间所有 run 的累计 `fileChanges/changedFiles`；abort 只结束一次 run，不清除已记录的变更。
`start` 会固化当前 task 契约哈希，并对请求 workspace、scope 投影和初始 Git 快照写入 `integritySha256`。Batch 级探索和首次 `record/patch` 必须发生在首个 TASK `start` 前；后续 TASK 的 context 复核同样应在对应 `start` 前完成。run 活动期间不得修改该 task 的 goal/scope/AC/validationCommands 等计划字段，也禁止直接编辑 `plan.json`、批次 `plan.json` 或 `.task-runs/**/*.json`、手工重算 digest/hash；否则 runner 返回 `task_run_integrity_mismatch` 或计划完整性错误。确需修复 Plan contract 时按下方协议保存 patch、force abort、由 Plan writer 整体重建契约和基线后再应用 patch；普通新增 DTO/XML/测试文件不需要修 scope 或重建 digest。

每个 TASK 必须且只能绑定一个 `workspaceRef`；只向 runner 传该 TASK 的 workspace，禁止对 TASK start/finish/validate 重复传入其他仓库，前端 TASK 不得传后端仓库、后端 TASK 也不得传前端仓库。若一个需求闭环需要修改多个业务仓库，Plan 必须拆成多个 TASK 并用 deps 串联；跨仓库集成检查只能放在 `projectValidationCommands`。Plan 中具名 workspace 的 validation command 必须用 `repo` 指明 Git 根目录名；changed/supporting 路径使用 `repoId:relative/path`。无论涉及多少仓库，`evidence/` 与 `.task-runs/` 只能写入 feature 产物目录，禁止写入任一业务仓库。

Batch 同样只能包含同一 lane 且同一 `workspaceRef` 的 TASK；前后端不会进入同一 Batch，同一 lane 的不同仓库也不会进入同一 Batch。启动批次验证、逐 TASK 验证和 `batch-check` 时只传该 Batch 的唯一 workspace。
2. 读唯一 `plan.json` 中的结构化执行契约。必须先运行任务上下文解析脚本：

```bash
python "${pluginPath}/hooks/code_task_context.py" --feature "${feature}" --task-id "<TASK_ID>" --code-workspace "<BUSINESS_REPO>"
```

该脚本输出是当前 task 的上游上下文事实源，必须读取其中的 `batchExplorationScope`、`taskContract`、`resolvedSpecRefs`、`resolvedDesignRefs`、`explorationCaches`、`explorationPolicy` 和 `explorationDirective`；探索范围按上方 Batch 级探索闸门服从 `explorationDirective`。只传 `taskContract.workspaceRef` 对应的一个 `--code-workspace`。`specRefs` / `designRefs` 一律按 `artifactFeatureDir`（`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}`）解析，不得按业务代码仓库 cwd 直接读取 `specs/...`、`design.md`、`PLAN.md`；业务代码仓库 cwd 只用于定位源码、测试和执行验证命令。脚本只要返回 `ok=false`，无论是引用、计划、Git 快照还是探索缓存错误，都必须停止编码、不得读取 HTML/调用 parser/修改业务代码；先按 `requiredAction` 修复并重新运行，直到返回 `ok=true`。其中 `explorationBlocked=true` 或 `implementationAllowed=false` 是机器阻断证据，不得被自然语言解释覆盖。若脚本返回 `missing_ref_file` / `missing_ref_anchor` / `invalid_plan_json` / `task_not_found`，停止编码并回流 `/autodev-plan` 修复产物引用，不得猜测补路径。

必须按每个仓库的缓存状态执行，不能只看总体最严 policy 后忽略其他仓库：

若 `explorationPolicy.status=unavailable`，说明没有传入业务仓库，必须停止并补传 `--code-workspace`，不得绕过缓存协议继续编码。

- `missing` / `stale`（`full_bounded_explore`、`requiresRecord=true`）：读取 `staleReasons` / `criticalHits`，完成一次有界探索；先读取 `code_exploration_writer.py contract`，并确认业务仓内存在的 `.autobizdevops/`、`.cmbdevclaw/` 等运行期目录已被 Git ignore，再对每个 `explorationCaches[]` 仓库分别调用一次仅含该仓库 `--code-workspace` 的 `record --expected-cache-sha256 <cacheSha256> --body-file <JSON>` 写入完整 findings。重新运行 `code_task_context.py`，对应仓库必须成为 `fresh` 后才能改业务代码。`headCommit` 单独变化但 Git 可见文件快照未变，或当前文件快照与可信 TASK/Batch Evidence 的最终快照一致时允许复用；HEAD 变化且缺少可信最终快照时仍按 `stale` 处理。
- `fresh`（`task_scope_only`）：直接使用缓存 findings，只定点读取当前 Task scope / entrypoint 涉及文件；不得重复探测项目框架、模块布局和测试运行器。
- `fresh_with_trusted_changes`（`task_scope_only`）：implementation evidence 已解释本次仓库变化，且缓存与当前 Task 属于同一 active batch；只定点读取当前 Task scope / entrypoint，不执行 `record` 或 `patch`。这是批次内的 `deferredCacheUpdate`，变化会在批次边界统一吸收。
- `reusable_with_changes`（`targeted_reread`、`requiresPatch=true`）：只读取 `changedPaths + 1-hop 依赖 + 当前 Task scope`，再对每个 `explorationCaches[]` 仓库分别调用一次仅含该仓库 `--code-workspace` 的 `patch --expected-cache-sha256 <cacheSha256> --body-file <JSON>` 确认；即使事实未变化，也必须传完整 `reviewedPaths` 和空 `findingUpdates`。`findingUpdates` 对每个字段采用整类替换语义，不得只传该列表的一部分；writer 会在合并后重新校验完整 findings。重新运行 context，成为 `fresh` 后才能改业务代码。
- 同一 active batch 内，普通且已被 implementation Evidence 解释的源文件变化不要求每个 Task 都 patch；进入下一批次时在 Batch 级探索闸门统一 patch。即使仍在同一批次，命中 `shared/integration` 路径也必须立即 targeted reread/patch；构建配置、迁移/schema、HEAD 变化且缺少可信最终快照，或无法由 Evidence 解释的路径仍进入 `stale`，重新做有界 `record`。`transientValidationFiles` 不参与探索差异，但同一路径后续成为正式变更时必须重新纳入。

`fresh/reusable_with_changes 时禁止无边界全仓探索`：不得重新运行无范围全仓 `rg`、递归目录 listing 或框架发现。缓存只能通过 `code_exploration_writer.py` 修改，禁止直接编辑 `cache/code-exploration/**/*.json`。共享路径只更新当前 executionLane；另一 lane 在后续 inspect 时独立判定。runner 能返回 machine policy，但宿主未提供工具调用遥测，无法独立证明 Agent 执行过多少次搜索，这是协议层约束。

必须读取当前 task 的 `workspaceRef`、`goal`、`scope`、`validationBoundary`、`implementationPoints`、`acceptanceCriteria`、`nonGoals`、`splitRationale`（若存在）、`specRefs`、`designRefs`、`validationCommands`；不得只根据 `title` / `specRefs` 脑补实现范围。缺少 `workspaceRef` / `goal` / `scope` / `validationBoundary` / `implementationPoints` / `acceptanceCriteria` / `nonGoals` 时停止编码，回到 `/autodev-plan` 补齐，不得边做边猜。先依各输入的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。`splitRationale` 只用于理解合并背景，不得作为扩大 scope 的理由。
3. 改代码前按 `explorationPolicy` 进行首次有界探索或缓存定点复核；先识别或复用项目分层、命名、错误处理、校验、日志、测试风格，并读取测试插件/provider 版本（例如 Maven Surefire 与 JUnit 4/5 兼容性）、Spring 测试启动入口和现有可运行测试，形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。不得在测试失败后才猜测框架版本；计划为 `integration_test` 时不得为了通过验证降级成只 mock service 的 controller 单元测试。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：只实现 `scope` / `implementationPoints` / `acceptanceCriteria` 指向的业务范围；`scope.paths` 只是相对 workspace 的文件提示，不是逐文件白名单，因实现需要新增的 DTO/domain/test/resources/迁移/配置会由 runner 自动归集。不得实现 `nonGoals` 中列出的内容。观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 任务需要写 / 改测试时，遵循 `${pluginPath}/skills/references/test-quality.md`：站在 seam 上验证、期望值来自独立事实源（勿同义反复）、mock 只在系统边界。
   - 先读取 `start` 返回的 `validationTestTargets` 及 Task 的 `validationTestPlan`。标为 `reuse_existing` 的目标必须复用已有测试类，不得创建同名或重复测试；只有 `executionMode=code` 且目标标为 `create_in_code` 时才新增最小测试类。内部 code Task 缺少该测试时，runner 返回 `code_task_validation_test_creation_required / continue_current_implementation_and_create_validation_tests`，主 agent 必须留在当前实现 run 创建测试后再收口，不得把它延期或改写成 no-code。`verified_existing` 遇到 `create_in_code` 必须返回 Plan 改为可复用验证；`external_dependency` 不得创建源码、测试或本地验证命令。Runner 在执行 Maven 验证前要求 code 模式的目标测试源文件已存在，并要求新鲜的 Surefire/Failsafe 报告证明至少一个对应测试实际执行。
   - Code 阶段新建、位于请求 workspace 下的 `src/test`、`test`、`tests` 测试根的测试文件一律归入 `transientValidationFiles`，不进入正式 `changedFiles`，即使被暂存也不改变该分类；文件保留到批次 TASK 验证结束。start 前已有的测试文件若被修改，仍是正式代码变更。不得用 `skipTests`、`maven.test.skip` 或允许零匹配测试通过的参数规避验证。
   - TASK 实现期间不得手工执行 `validationCommands`，也不得执行 compile/build/typecheck/lint 或当前 `batchValidation.commands`。所有 TASK 命令统一在批次实现结束后由独立验证子代理通过 runner 执行。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. **实现差异协议**：实现中遇到以下任一情况，停下用 `request_user_input` 单次确认，展示「design/spec 说 X，代码/实现是 Y」，拿到裁定前不得继续，也不得先调用 `finish-implementation` 收口：
   - **`EVD` / design 依据与代码现实不符** → 裁定后回写 `artifactFeatureDir` 下 `design.md` 的对应行（注明「code 阶段修订」）再继续；不得改写业务代码仓库 cwd 下的同名文件。
   - **必须偏离 `API` / `DATA` / `D` 已定形态**（定的做不了或明显更差）→ 同上，偏离经裁定回写 `design.md` 后才可按新形态实现；「实现细节自由度」不覆盖已定的接口/数据/技术决策形态。
   - **实现将违反 `REQ` / `SCN` 行为契约** → 停止编码，不得实现一个违反行为契约的版本；按下方阻断口径记录原因与建议回流阶段（specs/plan），回流 `/autodev-plan` 修订契约后重新进入。TASK 状态由 runner 负责流转，不得手工置「失败」。
7. 实现完成必须只走 `finish-implementation`。该命令检查 scope 和 start 快照、写 `action=implementation` Evidence，并把 TASK 从 `in_progress` 置为 `implemented`；它不运行 `validationCommands`，不写 `completionEvidenceIds`，也不把 TASK 置为 done。deferred 计划调用旧 `complete` 会被明确拒绝：

```bash
python "${pluginPath}/hooks/task_runner.py" finish-implementation --feature "${feature}" --task-id "<TASK_ID>" --run-id "<RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

若返回 scope/workspace 错误，仍按原 run 修正或回流 Plan，不得重新 start 掩盖基线。`implemented` 是实现终态，不等于业务完成；同批后继 TASK 可以依赖它，跨批依赖、handoff、project-check 和 code-done 只接受 `done`。

实现 Evidence 尚未落盘时发生进程中断，才使用原 runId 恢复：

```bash
python "${pluginPath}/hooks/task_runner.py" resume --feature "${feature}" --task-id "<TASK_ID>" --run-id "<ORIGINAL_RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

确实没有文件变更时，不得伪造 changedFiles，也不得把空 diff 当遗漏。必须说明原因并提供至少一个仓库内已有实现/测试文件；backend 任务还必须有 required 的行为、集成、E2E 或静态验证，frontend 任务可按 frontend validation profile 使用 required 的 compile/build/typecheck。lint 仍只属于批次补充验证：

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

保存输出的完整 `validationContext`，其中必须包含 `runType/featureId/batchId/runId/taskOrder/currentTaskId/requestedCodeWorkspaces/batchSnapshotSha256/allowedCommands/commandAudience/executorDirective/subagentProtocol/executionGroups`。runner 返回 `action/requiredAction=spawn_batch_validation_subagent` 后，主 agent 的下一次工具调用必须是一次“子任务执行”；禁止在两者之间调用 `code_task_context.py`、`inspect`、`validate-batch-task`、`batch-check` 或任何底层构建命令。主 agent 只启动一个全新的批次验证子代理，并把该对象原样放入启动 prompt；必须把 `executorDirective/subagentProtocol` 作为机器协议执行，不得用旧模板覆盖或省略其中的执行角色、异步轮询、单子代理和终态规则，不得只传自然语言摘要、遗漏 batch 上下文或自行改写 `code_workspace`。子代理禁止修改源码、测试、配置、Plan 和 Evidence，只能执行 `allowedCommands` 中列出的 runner 命令——该列表按 phase 严格收窄（`task_validation` 只能 `validate-batch-task`，`batch_check` 只能 `batch-check`，失败态 `failed_handoff` 为空），并按 `currentTaskId` 调用：

```bash
python "${pluginPath}/hooks/task_runner.py" validate-batch-task --feature "${feature}" --batch-id "<BATCH_ID>" --task-id "<CURRENT_TASK_ID>" --run-id "<VALIDATION_RUN_ID>" --code-workspace "<BUSINESS_REPO>"
```

runner 把逻辑 TASK 命令先规划成 `executionGroups`：同仓库、同 cwd、同 Maven 配置的定向测试合并为一次 `-Dtest=A,B` 物理执行；完全相同的前端 compile/build/typecheck 命令只执行一次；不兼容命令保持独立。同一物理执行根据 Surefire/Failsafe XML 拆回每个 TASK 的逻辑结果和独立 Evidence，因此一次 Maven 失败不会把所有测试笼统归为同一个错误。物理组完成后，后续 TASK 只采用已有 Evidence，不重复执行命令。

同一 workspace 的验证命令由 runner 串行调度，禁止为每个 TASK 并行启动 Maven/Gradle/Node 进程；这会争用 `target`、缓存和报告目录，也会破坏冻结快照。子代理隔离保留在逻辑 Task/Evidence 层，耗时优化来自物理命令合并和去重。返回 `requiredAction=continue_batch_validation_subagent` 和新的 `currentTaskId` 时，同一个子代理继续下一个 TASK；禁止主 agent 接管命令或并行启动其他验证子代理。

#### Claw 异步 Bash 执行规则

`validate-batch-task`、`batch-check`、`project-check` 以及它们内部触发的 Maven/Gradle/npm 构建和测试可能超过 Claw 默认 30 秒超时。Agent 必须通过 Claw 原生工具异步执行整个 runner 命令；插件 Python 脚本不得 import 或调用 Claw Bash API。

1. 调用 `execute` 并设置 `run_in_background: true`，保存返回的 `task_id`。
2. 使用 `task_output({task_id, timeout: 120000})` 等待结果。这里的 `timeout` 只是一次结果拉取的最长等待时间，不是验证命令失败，也不等于 runner 的 `command_timeout`。
3. 返回 `retrieval_status: "timeout"` 时，用同一个 `task_id` 继续调用 `task_output`；不得重新启动 runner、不得新建验证子代理。runner 会在 `stderr` 输出 `validation_process_started/running/finished` 进度事件，最终机器结果仍只写到 `stdout`。
4. 只查询状态时使用 `task_output({task_id, block: false})`。
5. 退出码非 0 时读取 runner 的完整 JSON 结果并按 `requiredAction` 处理；不得把超时或非零退出码伪装成通过。
6. 禁止使用 shell `&`、`nohup` 或另开不可追踪进程；Claw 后台任务最长可运行 2 小时并能持续返回部分输出。

```text
execute({
  command: "python \"${pluginPath}/hooks/task_runner.py\" validate-batch-task --feature \"${feature}\" --batch-id \"<BATCH_ID>\" --task-id \"<CURRENT_TASK_ID>\" --run-id \"<VALIDATION_RUN_ID>\" --code-workspace \"<BUSINESS_REPO>\"",
  cwd: "<WORKSPACE>",
  run_in_background: true
})

task_output({task_id: "<TASK_ID_FROM_EXECUTE>", timeout: 120000})
```

Coordinator 模式必须使用具备 `execute/task_output` 的 verify worker，或没有 `owned_files` 限制的全工作区 write worker承载验证；read_only worker 会拒绝 runner 的 Evidence/Plan 状态写入，局部 `owned_files` worker 可能没有异步 Bash 工具。`allowed-tools` 只声明建议工具，不绕过沙箱或自动提权。

最后一个 TASK 通过且返回 `requiredAction=run_batch_check_in_validation_subagent` 时，仍由同一个子代理执行批次级 compile/build/typecheck/lint：

```bash
python "${pluginPath}/hooks/task_runner.py" batch-check --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

该子代理拿到 `stop_and_open_new_conversation`、`code_done_ready` 或 `run_project_check` 时，把最终结果交回主 agent 并结束；拿到 `handoff_validation_failure_to_main_agent` 时也必须立即结束，但此时不得执行 `batch-check`，由主 agent 启动 repair。这样 TASK 与 batch 两层所有强校验命令仍由同一个独立子代理运行，源码修复始终由主 agent 负责。

`taskValidation.status=running` 期间工作区处于硬冻结：runner 拒绝启动/收口实现任务，Plan Writer 拒绝写计划或渲染产物，Evidence Store 拒绝 validation runner 之外的任何追加。子代理不能通过直接调用其他 writer 绕过冻结。

子代理/进程中断时使用同一 runId 和 currentTaskId 恢复，已写 Evidence 的物理组和逻辑命令不会重复。每次失败都必须原样返回 runner 的完整机器结果：`runType/runId/batchId/failedValidationTaskId/failedCommandId/errorCategory/requiredAction/evidenceIds/batchSnapshotSha256/allowedCommands/validationFailures`；存在编译诊断时还必须返回 `diagnosticPaths/repairOwnerTaskIds`。普通失败的 `requiredAction=handoff_validation_failure_to_main_agent`、`nextActor=main_agent`、`validationSubagentTerminal=true` 且 `allowedCommands=[]`；子代理不得自行调用 `start-validation-repair`，也不得在 TASK 未完成时尝试 `batch-check`。`validationFailures[]` 按逻辑 TASK/command 列出 selectors、errorCategory、diagnosticPaths、repairOwnerTaskIds 和 `testFailures`；`testFailures.failureKind` 区分 `assertion_failure` 与 `unexpected_exception`。`failedValidationTaskId` 只表示当时正在执行校验命令的游标，真正允许修改源码的 TASK 以 `repairOwnerTaskIds` 为准，禁止把两者混为一谈。

多个测试同时失败时，先按 `validationFailures[].repairOwnerTaskIds` 分组：同一 owner 的失败可在一次 repair 中一起修复；不同 owner 必须选择一个允许的 owner 启动 repair，完成实现收口并整批重验，剩余失败在后续轮次继续处理。不得把多个 owner 的修改全部记到一个 TASK，也不得因某个 selector 已通过而重跑或改写其历史 Evidence。

runner 将 `environment_failure` 直接记录为 `validation.result=blocked`，并在 `plan.json.deferredValidationIssues[]`、TASK `validationDisposition` 和对应 task/batch/project validation 状态中写入 `deferred` 事实；验证子代理继续下一个 TASK 或后续门禁，不得停止主流程、重复启动子代理或绕过 runner 手工执行底层 Maven/Gradle/npm。`executionMode=external_dependency` 同样不运行本地命令，直接记录 `errorCategory/reason=external_dependency` 的 BLOCKED Evidence，repairAttempts/maxRepairAttempts 均为 0，再继续 Batch；不得为它创建本地测试。普通 required 校验失败仍先交还主 agent 修复：主 agent 必须在任何源码、测试或配置改动之前执行 `start-validation-repair --task-id <REPAIR_OWNER_TASK_ID>`。runner 会校验工作区仍等于失败 run 的冻结快照；若改动已提前发生，可执行 `start-validation-repair --task-id <REPAIR_OWNER_TASK_ID> --adopt-workspace-changes`，让 runner 在工作区边界校验后采用变化并记录审计上下文；不希望采用时则恢复失败快照。每个责任 TASK 最多 2 次 repair，第 2 次修复后重验仍失败则以 `reason=repair_attempts_exhausted` 延期并继续；历史 FAIL/BLOCKED Evidence 全部保留。**环境失败立即延期、普通失败最多 2 次修复后延期**，下称延期策略；batch 与 project 两级验证同样适用，只是写入各自的 disposition 字段。

runner 会先解析验证工具的真实可执行文件。Windows `.cmd/.bat`（包括 `mvn.cmd`、`npm.cmd`）通过临时 `.cmd` 包装文件和 `%COMSPEC% /D /S /C` 原始命令行启动；包装文件先写入 `validation_windows_wrapper_started` 哨兵，再在命令侧把 Maven/npm 的 stdout/stderr 追加到临时日志。其他平台直接把子进程 stdout/stderr 写入同类日志。runner 每 200ms tail 日志，避免 `mvn.cmd -> cmd.exe -> java.exe` 进程链丢失匿名管道输出。发现明确的业务源码或测试源码编译诊断后，先短暂收集完整诊断，再终止验证进程树并分别返回 `source_compile_failure` 或 `test_compile_failure`，要求 `start_validation_repair`。测试进程在总超时点仍存活但已生成新的 Surefire/Failsafe 失败报告时，也必须返回普通测试失败并进入 repair，不能归类为环境超时。

环境失败仍使用窄分类：可执行文件/`cmd.exe` 无法启动、Java 工具链不可用、依赖仓库网络不可达、依赖认证或证书失败，以及没有任何编译诊断、测试失败报告或其他代码失败证据的真实硬超时。普通非零退出码、源码编译错误、测试编译错误、断言失败和异常测试结果都属于代码验证失败，先按 repair owner 修复。延期不是 PASS，最终摘要必须列出 issueId、scope、reason、commandId、evidenceIds、repairAttempts 和交接阶段 `dev.utest/dev.e2e`。

全部 TASK 验证通过或已按上述策略记录延期后，才把 TASK 置 done。frontend `task_covered` 此时生成 `batch_closure` 或 deferred closure；backend 固定使用 `commands`，返回 `requiredAction=run_batch_check_in_validation_subagent` 后由同一个子代理执行 compile/build 收口。frontend `commands` 模式也由该子代理继续下方额外质量门禁。

### 批次验证与重验证

本节只适用于 `batchValidation.mode=commands`。deferred policy 下只能由上方已经启动的批次验证子代理执行；主 agent 不得接管。第一次执行当前批次验证时不传 run ID；runner 创建 `.batch-runs/<BATCH_ID>/<RUN_ID>.json`，按该 lane 的 `batchValidation.commands` 运行真正补充 TASK 盲区的 compile/build/typecheck/lint，并分别写入 `action=batch_validation` evidence：

```bash
python "${pluginPath}/hooks/task_runner.py" batch-check --feature "${feature}" --batch-id "<BATCH_ID>" --code-workspace "<BUSINESS_REPO>"
```

- 返回 `requiredAction=fix_batch_and_retry_same_run` 时，保留返回的 `runId`，只在当前批次请求 workspace 内修复问题，然后用完全相同的 workspace 和 `--run-id "<RUN_ID>"` 重跑 `batch-check`。`scope.paths` 不构成修复白名单；批次失败 evidence 只追加，不覆盖；不得新建 run 隐藏失败历史。验证命令若修改 Git 可见文件会被拒绝。
- Batch 验证按延期策略处理，延期写入 `batchValidation.status=deferred`；无论 PASS 还是延期都正常完成 batch handoff，不阻断下一批。
- runner 在首条命令前把 `activeRunId` 投影到 plan；命令 evidence 全部写完后先保存 `status=evidence_written`，再幂等绑定 plan。进程在 evidence append、TASK 重验证请求或最终批次绑定后中断时，重新调用 `code-session` 取得原 `activeRunId`，再以同一 `--run-id` 执行 `batch-check`；runner 会采用已写入 stream 的 evidence，不重复执行已完成命令。
- required 命令决定批次成败及 `latestPassEvidenceIds`；optional 成功或 optional 失败 evidence 都只追加到批次历史和 run attempt，不得让 optional 失败阻断 code-done，也不得把 optional 记录伪装为 required latest pass。
- 修复路径超出当前批次请求 workspace 时返回 `batch_fix_outside_workspace`，必须修复工作区后用同一 batch run 重试；同 workspace 内的任何修复都会改变批次最终快照，因此无论修改了哪个文件，都清空整批当前 completion 指针并按稳定顺序重验全部 TASK，不再按 `scope.paths` 归因。
- 修复后的 batch-check 返回 `requiredAction=run_batch_task_validation` 时，保留原 batch run ID，整批 TASK 当前 validation completion 指针失效；创建新的 deferred task-validation run，启动一个新的批次级验证子代理串行重验全部 TASK。全部通过后仍由该子代理使用原 batch run ID 再跑最终 batch-check。`pendingRevalidation` 只保存触发和被替代 Evidence 关联，不再启动另一套 `start/complete` 重验。
- batch-check 直接通过且没有批次修复，或重验证后的最终 batch-check 通过时，runner 才把批次置为完成。非末批会返回 `stop_and_open_new_conversation` 与 `batchHandoff`；末批则进入可选项目检查或完成门禁。

若 `batch-check` 返回 `requiredAction=stop_and_open_new_conversation`、`stopAfterBatch=true` 和 `batchHandoff`，当前批次已经结束。必须原样输出 `userMessage` 提醒用户打开新对话，然后立即结束当前回复；不得继续读取或实现下一批，不得运行 project-check/checkpoint 命令，也不得在同一对话再次调用 `code-session`。新对话重新进入 Code 后由会话入口自动检查并激活下一批。`BATCH_HANDOFF.json` 始终保存在 feature 产物目录，入口激活时消费并删除。

宿主未提供 conversation ID，因此 runner 无法从进程参数中证明调用来自新对话；`requiresNewConversation` 是供宿主和 Agent 执行的协议层约束，不是 runner 可独立验证的身份凭据。`activate-batch` CLI 仅保留给兼容或诊断场景，正常 Code 流程不得直接调用。

策略边界：TASK `validationCommands`、frontend `task_covered` 的 `action=batch_closure`、命令模式的 `batchValidation.commands` / `action=batch_validation` evidence 与 `code_done_gate` 都是强门禁。旧 Feature 若已有 `SMOKE_TEST_PLAN.json`，可按需手动运行独立 smoke 工具诊断，但新 Plan/Code 流程不生成、不读取，也不要求其结果。

> 一致性：任务的依据在对应上游产物里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

###  全部任务完成后的验证

只有 Code 会话入口返回 `run_project_check`，即全部批次验证已进入 PASS 或 deferred 终态且根 plan 配置了非空 `projectValidationCommands` 后，才通过 runner 跑额外的跨 lane/跨批次项目检查。其 kind 只允许 `integration_test/e2e_test/static_check`，不得重复 batch profile 的 `argv + cwd + repo`；提前执行会被拒绝。项目检查单独写 `action=project_check` evidence，不参与任一 TASK 的验收覆盖。未配置项目检查时入口直接返回 `code_done_ready`，不要求伪造一轮最终编译：

```bash
python "${pluginPath}/hooks/task_runner.py" project-check --feature "${feature}" --code-workspace "<BUSINESS_REPO>"
```

Project 验证同样遵循延期策略，延期写入 `projectValidationDisposition`，随后入口返回 `code_done_ready`。

### 回检与交接

本节完整协议由脚本渲染,必须先运行下面命令，并完整遵循其输出；不得凭记忆执行本节，也不得跳过该命令。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.code
```

推进 `code_done` 前先回填领域词汇表锚点：会话工作区 `CONTEXT.md` 中锚点为「规划中」且本轮已落地的词条，回填为实际类/表/枚举与相对路径（协议见 `${pluginPath}/skills/references/domain-context.md`；无该文件或无「规划中」词条则跳过）。

项目级验证收敛后：

```bash
python "{PLUGIN_ROOT}/hooks/stage_gate.py" validate --stage dev.code --feature "{FEATURE_ID}"
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```
## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件

同时允许：`artifactFeatureDir` 下 `design.md` 中经实现差异协议裁定后的对应行修订（注明「code 阶段修订」）；会话工作区 `CONTEXT.md` 的领域词汇表锚点回填。`plan.json`、批次 `plan.json`、`evidence/**` 与 `.task-runs/**` 仍只能由对应 hook 写入。

为完成任务必须改队列未直接提到的业务文件，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」，且都有 `action=implementation` evidence。每个 TASK 的 required validation 要么全部通过并覆盖全部 AC，要么具有结构完整、Evidence 可回链的 `validationDisposition.status=deferred`——延期必须保留真实 FAIL/BLOCKED evidence、失败原因、repair 次数和 UTEST/E2E 交接阶段，不得伪造成 completion pass。未记录的失败仍阻断。task/batch/project 三级同此判定：PASS 校验 evidence 顺序，deferred 校验 issue、失败 evidence 与 run 终态闭环。
- `evidence/EVIDENCE.jsonl`、`EVIDENCE.index.json` 与每条 task/batch/project validation evidence 的 `ev_XXXX.log` 完整性和哈希校验通过；没有新生成的 `ev_XXXX.json` sidecar。
- 每批 `taskValidation.status` 已进入 `passed` 或 `passed_with_deferred`，且额外批次质量门禁已通过或记录为 deferred；存在批次修复时按上方重验证流程产生 `attemptType=batch_revalidation` 的新完成 evidence。非末批之后才停止当前对话并生成 `BATCH_HANDOFF.json`。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
