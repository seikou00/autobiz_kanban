---
name: autodev-code
description: 启动或恢复固定 Code Workflow，在隔离工作树完成实现、Review、集成和最终证据汇总。
version: v2.0
allowed-tools: execute task_output read_file grep glob write_file edit_file request_user_input
---

# /autodev-code

主会话只负责启动、恢复与展示最终交付；固定 Workflow 管 Batch DAG、原生 Worktree、每个 Batch `review` 子阶段、Merge Train 和最终证据汇总。前后端 UTest 和 E2E 当前全部移出 Workflow，不另外调用测试技能或命令补跑，不生成测试资产或初始化测试环境。不要生成替代 Workflow 或在共享 checkout 手工执行 Batch。

## 启动与恢复

1. 复用已提供的 `taskCardId` 或当前 Run 中保存的 ID；仅首次缺失时按 `${pluginPath}/skills/references/ask-user-question.md` 收集。去除首尾空白后匹配 `^[A-Za-z0-9][A-Za-z0-9._-]*$`，不猜测。
2. 读取 Feature 状态和 `IMPLEMENTATION_SCOPE.json`。缺产物时调用 `inspect_skill_contract.py autodev-code --feature "${feature}" --plain`，按缺口处理，不提前探索工程。

```bash
python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
python -X utf8 "${pluginPath}/hooks/rollback_stage.py" --capture-code-session --feature "${feature}" --code-workspace "<Plan 声明的实际业务 Git 根，逐仓库执行>" --json
python -X utf8 "${pluginPath}/hooks/workflow_launcher.py" --feature "${feature}" --task-card-id "<有效ID>" --json
```

基线按每个业务 Git 根首次捕获一次，已有有效基线复用，不因后续 Batch 重捕。启动前展示 launcher 的 batchExecutionPlan（Batch、TASK 数、lane、仓库、依赖及 waves）。按 launcher 返回的脚本路径和完整 `workflowArgs` 启动一次固定 Workflow。只接受 `useWorkflow=true`、`executionMode=fixed`、`canStartWorkflow=true` 以及 `start_fixed_workflow` / `resume_fixed_workflow`。恢复使用同一 runId，不新建 Run 绕过失败。其他结果按 `requiredAction` 处理；配置与特殊恢复按需读取 `references/batch-workflow-guide.md`。

## Batch 实现

- 只在插件提供的 Worktree 执行分配的 TASK，按 Runtime 返回的当前派发代次与 lease 身份操作，不自行创建、删除或合并 Worktree。
- 实现前读取 `references/task-execution.md`；当前 Task 的上游事实通过 `code_task_context.py` 获取，不重读整个 Plan。已有有效工程事实直接复用，未知或失效的 API 才定向 lookup/resolve/query。
- 当前 TASK `uiRequired=true` 时才读取 `references/frontend-route.md`。后端不加载 HTML/parser，不在 Workflow 启动前解析全局前端 Route。
- 生产代码在 TASK start 成功后修改；Code 不生成测试、不执行行为测试。后端每个 TASK 必须完成当前源码的 production-only compile 并登记真实输出，才能 `finish-implementation`。前端不新增编译要求。
- Review 独立且只读，只审当前 Batch 生产实现。既有一次定向生产修复后完成纯编译、封存和 `single_repair_accepted`，直接进入候选集成，不派发第二次 Review。
- Review checkpoint 和交付 gate 使用 `--skip-tests`，按当前提交登记前后端测试 `skipped`，原因 `utest_temporarily_disabled`；不准备 assignment、测试环境或测试工具，不把跳过记为 PASS。
- 固定 Workflow 启动后不调用 `request_user_input` 或额外等待确认。内部差异按已有契约与现有依赖最小兼容处理，超出权限/契约时报告具体阻断，不伪造成功。

## 收口与合入

Review 收口后的交付由 Merge Train 集成；全部 Batch 交付后直接执行 aggregate `--skip-e2e --finish-workflow`，登记 E2E `skipped` 和原因，直接报告完成及遗留问题，不再请求合并或验收确认。不派发独立的测试 Agent，不创建验证 Worktree、不重复编译或测试。`implemented` / `sealed` 不等于 `done`；实际集成和 Evidence 由 Runtime 推进。

展示 `workspaceSync` 中用户原目录 `sourcePath`、分支、`lastSyncedSha`、备份及待同步问题。每个 Batch 的成果以未提交文件写回，用户分支 HEAD 和原暂存区不变，用户可直接编辑。`workspaceSyncPending` 非空时如实报告并保留恢复入口；不请求验收，不调用 `promote`、commit、reset 或清理用户修改。

旧的等待确认 run 由固定 Workflow 恢复并完成报告，不再走旧合并确认入口。`validationStatus` / `evidencePassed` 为失败时仍如实展示，report-only 不是 PASS。每个已合并 Batch 随即触发有预算的清理，最终再重试一次；用户原目录及需求集成 Worktree 保留。清理仍失败仅记录 pending，不阻止 Workflow 结束、不重新拉起已完成 run、不阻止状态推进。

只有用户另外明确要求正式 Git 合并时，才使用独立的 `parallel_integration.py prepare/promote` 工具；按现有授权执行，不主动询问是否需要合并。准确参数和冲突恢复见 `${pluginPath}/docs/feature-integration-worktree.md`。

固定 Workflow 汇总完成后自动执行 `update_checkpoint.py --feature "${feature}" --checkpoint code_done --json`，通过现有门禁后推进状态。`continue_next_stage` 表示已完成 Code，可按共享交接协议读取下一阶段；不在本 Workflow 启动下一技能。launcher 返回 `code_done_ready` 或 Workflow 返回 `retry_code_checkpoint` 时，读取真实状态；仍为 `code_in_progress` 则自动执行上述命令补齐，不要求合并或验收确认。状态推进失败必须如实报告，不声称已完成、不手改 Plan、manifest、Evidence 或 checkpoint 绕过。Code/Rework 提交均由封存器生成，格式见 `${pluginPath}/docs/code-commit-messages.md`。简报实际结果并按 `${pluginPath}/skills/references/ui-continuation-guide.md` 交接。
