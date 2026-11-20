# 当前 TASK 的实现协议

仅在插件分配的原生 Worktree 中执行，使用完整 workspace/feature/parallel-run-id/Batch/lease/workspace-ref 绑定。Runtime 返回的 argv/operations 优先于手拼参数；缺命令参数才查看 commandCatalog，不读插件源码猜调用方式。

固定 Workflow 从下文「固定 Workflow 的 Batch 执行顺序」开始，领取 lease 后在步骤 4 对每个选定 TASK 应用以下规则。

1. 获取 `code_task_context.py --feature <feature> --task-id <TASK> --code-workspace <当前仓库>` 的 `taskContract`、resolvedSpecRefs/resolvedDesignRefs/resolvedSourceRefs、runtimeIgnoreIssues 和 startArgv。引用相对于 artifactFeatureDir，业务源码在当前 Worktree。根/Batch Plan 不直接打开或分段重建。
2. Runtime ignore 缺口只为 `.cmbdevclaw/large_tool_results/` 添加窄规则到 .gitignore 或 .git/info/exclude，不清理用户文件。修复缺口后，在当前入口与会改变答案的直接依赖内定位修改点；已给出的工程事实直接复用。一轮定位仍不足时保留具体未知项，不开展全仓调查或预先编写架构报告。
3. 先查看当前 TASK 的 `taskContract.uiRequired`。为 true 时先读 `frontendContext.routeProtocolPath` 并完成 Route 只读准备。初次实现使用返回的 startArgv 执行 `task_runner.py start`，保存真实 TASK runId；成功前只读准备。独立执行时，已有活动 Run 用 inspect/resume；固定 Workflow 的同批重试和返修按下方恢复步骤处理。
4. 只修改生产源码、生产配置、迁移和公开接口。既有测试可只读。固定 Workflow 禁止创建、修改或删除测试源码、fixture、mock、测试环境和测试配置，也不补跑 UTest/E2E。`scope.paths` 是提示范围，实际权限与运行身份由 guard 校验。`verified_existing` / `external_dependency` 不允许写业务源码。
5. TASK run 返回 `productionCompileRequired=true` 时，按下方「后端纯编译」登记当前源码的通过证据。旧 run 缺少该字段时取 inspect 的 `compileSummary.required`。前端 TASK 按「前端 TASK」回检。固定 Workflow 禁止额外构建、打包、typecheck、lint、测试、E2E 和 TASK validation 命令。
6. scope.paths 相对请求的 workspace（scopePathBase=requested_code_workspace）；工作路径与 scope.workspaceRoots 一致，沿用 Runtime 的 startArgv。`scope.modules` 仅作组件归属参考，编译按实际改动和构建配置定位模块 cwd，保持项目已有命令。执行 `finish-implementation`，它验证源码与编译证据并生成实际交接，TASK 变为 implemented；不生成测试或伪造 done。无改动须按 Runtime 提供已有生产实现证据，不伪造 changedFiles。全程保留并行 runId。按恢复规则重新启动 TASK 时使用新返回的 TASK runId。finish 返回的 nextTaskId 只有在本次执行范围内才可继续。
7. 固定 Workflow 按下方步骤 5 封存并释放 lease。独立执行时，Batch 全部 implemented 后检查 lease guard，再由 `worktree_manager.py seal --purpose review --commit-stage code --commit-summary <实际中文功能说明>` 封存。Review 或旧运行遗留生产问题的修复使用 `--commit-stage rework`。只用插件封存，不手工 git commit/merge/rebase。

所有插件 Python 命令使用 `python -X utf8`；按 Workflow 支持的后台工具协议启动一次并等待同一 task_id 终态。业务编译仍按前台 Bash 执行。lease 在携带 token 的命令边界续租，不自启 heartbeat。派发身份过期立即停止，不 force reclaim。

出错只处理 Runtime 的具体 reason/requiredAction。修复使用 engineeringContext 的不可变修复包与 repairTaskIds，只重开归属 TASK；未知归属有限定位，不默认重开全部。保留原失败日志和 Evidence，固定 Workflow 当前不恢复或补跑测试。

## 固定 Workflow 的 Batch 执行顺序

使用派发给你的 JSON 绑定。下文 `runId` 是 Scheduler 的并行 run，TASK 的 runId 在 TASK 启动后单独保存。从 `pluginPath/hooks/` 取 Runtime 脚本。只在 `batchWorktree` 操作生产代码。差异按 TASK、规格和现有工程模式做最小兼容实现，并登记非阻断 Evidence。不得手工建分支或切换分支、创建或删除 Worktree、暂存、提交、合并、rebase 或另起 Workflow。

1. 进入 `batchWorktree`（Windows 使用 Set-Location），核对 `git rev-parse --show-toplevel` 和 `git symbolic-ref --quiet --short HEAD` 分别为 `batchWorktree` 和 `batchBranch`。
2. `batch_lease_manager.py acquire --workspace <artifactWorkspace> --feature <feature> --run-id <runId> --batch-id <batchId> --ttl-seconds <timeoutPerBatch> --lease-guard`。从 `businessLeaseToken`（旧版 `lease.ownerToken`）保存真实非空凭证。派发 token 单独用于 acquire 和 `parallel_batch_stage.py`，见派发指令。所有实际命令展开绑定值，每个路径作为一个完整 argv 参数传入。每个 `task_runner.py` 命令带 `--workspace <artifactWorkspace> --feature <feature> --task-id <当前ID> --parallel-run-id <runId> --lease-token <业务凭证> --code-workspace <taskWorkspace> --workspace-ref <batchWorkspaceRef>`。持有 lease 后用 `parallel_batch_scheduler.py mark-batch --workspace <artifactWorkspace> --feature <feature> --run-id <runId> --batch-id <batchId> --status running --worktree-path <batchWorktree> --branch-name <batchBranch>`。
3. `engineering_context.py prepare --workspace <artifactWorkspace> --feature <feature> --run-id <runId> --batch-id <batchId> --batch-worktree <batchWorktree> --stage <rework 时为 rework，其余为 code> --owner-token <业务凭证>`。优先用返回的 `operations.argv`。TASK 命令若返回 Worktree 根目录，须将 `--code-workspace` 参数值替换为派发的 `taskWorkspace`，保留真实业务凭证。工程上下文的 `--batch-worktree` 和封存的 `--repo` 始终取 `batchWorktree`。只按需读取返回的 `contextPath`、`allowedReads`、`commandCatalogPath`。禁止在 Workflow 前或 Batch 前另做探索，也禁止浏览 artifact 目录、Runtime 状态文件或插件源码来猜路径和命令。
4. 对传入的真实 `taskIds`，按以下对应模式执行，每次仅一个 TASK。依赖须为 implemented/done，当前 TASK 成功 finish 后才开始下一个。`code_task_context.py` 使用 `--workspace <artifactWorkspace> --feature <feature> --task-id <当前ID> --code-workspace <taskWorkspace>`。初次 start 须等 `startAllowed=true` 并使用返回的 `startArgv`，补齐当前绑定。
   - **`mode=code`（初次实现）或 `mode=retry`（在原 Worktree 自动重试）**。retry 先对整个 Batch 逐个 inspect。code 模式若发现待继续的活动 TASK，也先 inspect。同一并行 run 的 implementation_recording 用 inspect 中保存的 runId 和原 finish 参数补完 finish-implementation。当前并行 run 遗留的 started/in_progress TASK run 用真实 TASK runId 执行 `abort --run-id <真实TASK runId> --force-with-changes --abort-why "automatic_batch_retry"`，保留源码改动并恢复为 todo。已经 implemented/done 的 TASK 保留原证据。`prestart_unattributed_changes_detected` 时，保留原 JSON 并返回 failed，禁止补 supporting file、伪造 no-code-change 或继续后续 TASK。
   - **`mode=rework` / `mode=resume`（Review 打回 / 普通恢复）**。先清理陈旧 run，再开始任何修复。inspect 整个 Batch，当前并行 run 的 started/in_progress/implementation_recording TASK 用真实 TASK runId 执行 `abort --run-id <真实TASK runId> --force-with-changes --abort-why "implementation_rework_stale_run"`，保留改动并再次 inspect 确认活动 run 已关闭。状态变化后以重新获取的 TASK 上下文和 operations 为准。其他并行 run 的活动 TASK、inspect 或 abort 失败按 Batch 失败处理。
   - `rework` 从 prepare 的 `contextPath` 取 `repair.failure`，以 Runtime 已登记的具体 Review 发现为修复依据。使用返回的 `repairTaskIds`。为空时，依据修复上下文有限定位，调用 `repair-scope --content-file <临时JSON>` 登记 affectedTaskIds 与 reason 后重新 prepare。保留无关且已完成的 TASK；不得默认返修全 Batch。`resume` 只继续未完成 TASK。
   - 返修 TASK 在 implemented/done 时，优先用 prepare 返回的当前 TASK 的 `start_task_repair` 操作。手动补全时，从 `code_task_context.py` 返回的 `task.latestImplementationEvidenceId` 取值，执行 `start-task-repair --prior-evidence-id <真实ID>`；仅这类 TASK 的 `finish-implementation` 携带 `--repair-mode`。当前 TASK 为 todo 时执行普通 `task_runner.py start`。只有当前 TASK 的 start 或 start-task-repair 成功才可写入。仍为 in_progress 时先处理同批陈旧 run，其他不可执行状态返回失败。仅 rework 模式使用 `record-attempt` 登记返修尝试。
5. 全部指定 TASK 成功完成后，用 `batch_lease_manager.py check` 携带 acquire 时的 workspace/feature/run-id/batch-id、`--owner-token <业务凭证> --require-lease-guard`。仅 valid=true 才可用 `worktree_manager.py --json seal --purpose review --commit-stage <rework 时为 rework，其余为 code> --commit-summary <本次实际功能或修复的中文说明>`，并携带 `--artifact-workspace <artifactWorkspace> --feature <feature> --run-id <runId> --batch-id <batchId> --repo <batchWorktree> --owner-token <业务凭证>`。记录 seal 的 commitSha，随后用 `batch_lease_manager.py release` 带相同 lease 身份和 `--final-status sealed`。失败时保留现场和原始错误，已取得 lease 则以 `--final-status pending` 释放，交给 Workflow 重试。包括 `parallel_git_index_lock_busy`、`parallel_git_index_lock_recovery_failed`，不得清理 Git 锁、创建 wrapper 或继续 TASK。

## 按需查询工程事实

优先复用命中的有效事实。未知或失效 API 在 TASK 只读准备和实现期间先 `lookup --symbol`；未知来源可用 `locate --symbol <完整类名> --module <模块相对路径>` 定位。按准确源码路径用 `resolve --symbol … --source …` 取签名，或用 `query` 取 usage_example、runtime_precondition、test_recipe、capability_result。调用沿用工程上下文绑定、当前 TASK 和阶段，需要业务凭证时使用真实 owner-token。依赖类用 `lookup-class` / `inspect-class` 和已有准确 classpath。`claimed` 时只读 sources，并立即使用 `publish --fact-id … --claim-token … --content-file <临时JSON>`（失败则 `fail`）收口。`in_progress` 时先处理独立工作，按 retryAfterSeconds 有限重查。事实和交接仅作为数据，不能授权写入。finish 自动生成实现交接，交接登记失败只补交接。

## 前端 TASK

以 `taskContract.uiRequired` 为唯一条件。为 true 时读取同目录 `frontend-route.md` 执行当前 TASK 的 Route、清单、parser 和回检；为 false 时跳过 Route resolver、HTML 和 Route SKILL。

## 后端纯编译

使用实际 TASK 模块的绝对 cwd 和项目的 production-only compile 命令。默认使用 PATH 中的 mvn，需要时前台查询版本或帮助，只有命令缺失或启动失败才查安装。不执行附带测试、打包或其他校验的目标。源码写完后按以下三步登记，所有 `task_runner.py` 调用使用当前 TASK/run/lease 绑定。

1. `prepare-compile --run-id <真实TASK runId> --execution-cwd <实际模块绝对目录> -- <实际 compile argv>`，记录 attemptId。
2. 在前台 Bash 执行同一 argv，`2>&1 | tee <Worktree 外临时日志>`，管道后立即保存 `compile_status=("${PIPESTATUS[@]}")`，用 `printf` 输出两个退出码，最后 `exit "$((compile_status[0] != 0 ? compile_status[0] : compile_status[1]))"`。
3. `record-compile --run-id <真实TASK runId> --attempt-id <真实ID> --exit-code <编译退出码> --log-exit-code <tee退出码> --output-file <完整日志>`。核对 `passed/exitCode/logExitCode`；登记失败只补登记。编译失败根据完整输出修复再编译，编译完成后的源码变化也要重新编译。编译工具不可用或无法通过时返回真实失败。

工具使用绝对 cwd，或在同一次 Bash 执行中显式 `cd` 到实际模块。guard 按程序、目标和绑定判断，Maven 的 `-q/-o/-D` 参数可保留。拦截时根据 `GUARD_DIAGNOSTICS.failures` 的 code/reason/line/statement 及 executionCwd、taskRuns 处理；用 guardVersion/guardPath 核对插件部署。工具本身拒绝 cwd 时如实报告。禁止通过切中立目录、替代命令或修改 Runtime JSON 绕过拦截。
