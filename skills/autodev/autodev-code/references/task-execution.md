# 当前 TASK 的实现协议

仅在插件分配的原生 Worktree 中执行，使用完整 workspace/feature/parallel-run-id/Batch/lease/workspace-ref 绑定。Runtime 返回的 argv/operations 优先于手拼参数；缺命令参数才查看 commandCatalog，不读插件源码猜调用方式。

1. 获取 `code_task_context.py --feature <feature> --task-id <TASK> --code-workspace <当前仓库>` 的 `taskContract`、resolvedSpecRefs/resolvedDesignRefs/resolvedSourceRefs、runtimeIgnoreIssues 和 startArgv。引用相对于 artifactFeatureDir，业务源码在当前 Worktree。根/Batch Plan 不直接打开或分段重建。
2. Runtime ignore 缺口只为 `.cmbdevclaw/large_tool_results/` 添加窄规则到 .gitignore 或 .git/info/exclude，不清理用户文件。修复缺口后，在当前入口与会改变答案的直接依赖内定位修改点；已给出的工程事实直接复用。一轮定位仍不足时保留具体未知项，不开展全仓调查或预先编写架构报告。
3. 使用返回的 startArgv 执行 `task_runner.py start`，保存真实 runId；成功前只读准备，不生成分析产物、复制资源或修改源码。已有活动 Run 用 inspect/resume，不重新 start 掩盖基线。当前派发代次关闭的旧 Run 按 Runtime 返回重新启动。
4. 只修改生产源码、生产配置、迁移和公开接口；既有测试可只读。测试/fixture/mock/测试环境由 UTest 修改。`scope.paths` 是提示范围，实际权限与运行身份由 guard 校验。`verified_existing` / `external_dependency` 不允许写业务源码。
5. 后端实现或 Rework 完成后执行必要纯编译：先 `task_runner.py prepare-compile`，带同一 TASK/run/lease、实际 execution-cwd 和 `-- <production-only compile argv>`，保存 attemptId；再由前台 Bash 原样执行，用 tee 保存工作树外的完整日志，立即捕获整个 PIPESTATUS，同时检查编译和 tee 退出码；最后 `record-compile --attempt-id <真实ID> --exit-code <真实退出码> --log-exit-code <tee退出码> --output-file <完整日志>`。登记成功不等于编译通过；登记失败只补登记。源码又变化必须重新编译。不能运行 test/package/verify/build/check/lint/typecheck 等额外验证或后台业务命令，不要求寻找 mvn.exe。
6. scope.paths 相对请求的 workspace（scopePathBase=requested_code_workspace），不自行改成 Git 根；工作路径与 scope.workspaceRoots 一致，沿用 Runtime 的 startArgv。执行 `finish-implementation`，它验证源码与编译证据并生成实际交接，TASK 变为 implemented；不生成测试或伪造 done。无改动须按 Runtime 提供已有生产实现证据，不伪造 changedFiles。中断恢复保留同一 runId；finish 返回 continueCurrentBatch/nextTaskId 时立即继续同批下一 TASK，不额外询问。
7. Batch 全部 implemented 后检查 lease guard，再由 `worktree_manager.py seal --purpose review --commit-stage code --commit-summary <实际中文功能说明>` 封存。Review/UTest 来源生产修复使用 `--commit-stage rework`；测试资产使用 utest。只用插件封存，不手工 git commit/merge/rebase。

所有插件 Python 命令使用 `python -X utf8`；按 Workflow 支持的后台工具协议启动一次并等待同一 task_id 终态。业务编译仍按前台 Bash 执行。lease 在携带 token 的命令边界续租，不自启 heartbeat。派发身份过期立即停止，不 force reclaim。

出错只处理 Runtime 的具体 reason/requiredAction。修复使用 engineeringContext 的不可变修复包与 repairTaskIds，只重开归属 TASK；未知归属有限定位，不默认重开全部。保留原失败日志和 Evidence，后续测试由原 assignment 精确续跑。
