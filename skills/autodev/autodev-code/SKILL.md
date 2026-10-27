---
name: autodev-code
description: 启动或恢复固定 Code Workflow，在隔离工作树完成实现、Review、UTest 和最终 E2E。
version: v2.0
allowed-tools: execute task_output read_file grep glob write_file edit_file request_user_input
---

# /autodev-code

主会话只负责启动、恢复与展示最终交付；固定 Workflow 管 Batch DAG、原生 Worktree、每个 Batch `review` 子阶段、UTest、Merge Train 和 B-E2E。不要生成替代 Workflow 或在共享 checkout 手工执行 Batch。

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
- Review 独立且只读，只审当前 Batch 生产实现。UTest 生成/复用测试并实际执行。既有一次定向生产修复后完成纯编译、封存和 `single_repair_accepted`，直接续测，不派发第二次 Review。
- 前端 UTest 当前明确 `skipped`，原因 `frontend_utest_temporarily_disabled`；不生成测试资产、不初始化环境、不编译、不把跳过记为 PASS。后端按真实测试结果收口，失败/deferred 保留 Evidence。
- 固定 Workflow 启动后不调用 `request_user_input` 或额外等待确认。内部差异按已有契约与现有依赖最小兼容处理，超出权限/契约时报告具体阻断，不伪造成功。

## 收口与合入

Review/UTest 处理后的交付由 Merge Train 集成；全部 Batch 交付后运行 B-E2E。最终 aggregate 只校验已有证据，不重复编译或测试，也不增加独立 Verify。`implemented` / `sealed` 不等于 `done`；实际集成和 Evidence 由 Runtime 推进。

`review_integration_and_confirm_promotion` 是最终交付入口：展示 `mainPromotion` 报告中的各仓库代码位置、集成分支、冻结的基线分支、候选 SHA、diff、验证结果和遗留问题。`validationStatus` / `evidencePassed` 为失败时仍如实展示，report-only 不是 PASS。

先展示结果，再按共享提问协议请求用户确认具体报告；已有同一有效 `approvalDigest` 的明确授权直接复用。用户确认后才调用 `parallel_integration.py promote --approval-digest <报告原值> --approved-by <用户标识> --confirm`；暂不合入调用 `keep`。目标或候选变化必须重新准备报告。准确参数和冲突恢复见 `${pluginPath}/docs/feature-integration-worktree.md`。

`code_done` 由固定 Workflow 交付与门禁收口，不手改 Plan、manifest、Evidence 或 checkpoint 绕过。Code/Rework/UTest 提交均由封存器生成，格式见 `${pluginPath}/docs/code-commit-messages.md`。简报实际结果并按 `${pluginPath}/skills/references/ui-continuation-guide.md` 交接。
