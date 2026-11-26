# 当前 TASK 的实现协议

在 `next-code-batch` 返回的业务 checkout 工作。按 TASK 依赖顺序执行，保留已有源码和 Git 暂存状态。

1. 用 `code_task_context.py --checkout-session --task-id <TASK> --code-workspace <checkout>` 获取规格、设计和明确的实施范围。仅前端 TASK 需要先按 [Route 与回检](frontend-route.md) 做准备。
2. 用 `task_runner.py start --task-id <TASK> --code-workspace <checkout>` 开工。已有活动 Run 时先用同一 Runner 的 `inspect`，恢复本 TASK 的真实 Run ID。源码使用普通文件工具修改。
3. 后端在同一 checkout 的实际模块目录运行生产编译，按下文登记。前端按现有回检脚本验证当前源码。禁止补跑 UTest/E2E 或生成测试资产。
4. 用 `task_runner.py finish-implementation --task-id <TASK> --code-workspace <checkout> --run-id <TASK runId>` 登记证据。现有代码已满足验收时同时提供 `--no-code-change-why` 和实际 `--supporting-file`，仍须完成要求的验证。
5. 本批全部实现后检查实际差异和规格。发现问题时对相关 TASK 调用 `start-task-repair --prior-evidence-id <最新实现证据ID>`，修复后复验，并用新 Run ID 和 `--repair-mode` 执行 `finish-implementation`。

所有命令使用 `python -X utf8` 和插件 `hooks/` 下现有入口。每个 TASK 命令携带当前真实 TASK ID 与 `--code-workspace`，需要时再传真实 TASK Run ID。保留平台注入的 Feature 身份。

## 后端生产编译

1. `prepare-compile --run-id <TASK runId> --execution-cwd <当前模块绝对路径> -- <实际编译 argv>`，保存 attemptId。
2. 在模块目录前台运行同一命令，例如 `mvn -B -DskipTests compile`。用 Bash `2>&1 | tee <临时日志>` 捕获完整日志，管道后立即保存 `PIPESTATUS` 中的编译和日志退出码。
3. `record-compile --run-id <TASK runId> --attempt-id <attemptId> --exit-code <编译退出码> --log-exit-code <tee 退出码> --output-file <完整日志>`。编译失败直接修复当前 TASK 的问题，再准备并运行新一轮编译。
