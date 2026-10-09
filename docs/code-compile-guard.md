# Code 阶段编译权限与状态诊断

Code/生产代码修复阶段允许当前 Batch/run 绑定的活动 TASK 执行生产纯编译。`activeStage=none` 只表示阶段指针为空，不能单独作为拒绝纯编译的理由。编译必须发生在对应 TASK 的 `finish-implementation` 成功之前。

## 阶段登记

`task_runner.py start`、`start-task-repair`、`resume` 在校验租约、原生 worktree 和 TASK 绑定后，由 Runtime 登记准备阶段并启动 `implement`，成功返回后才能写生产代码。后续 TASK 和中断恢复复用同一个阶段 attempt；Review/UTest 打回生产代码后重置阶段，再启动新 attempt。已进入 Review/UTest 的阶段不能被陈旧 Code run 重新打开。

全部 TASK 完成并封存后，Workflow 调用 `parallel_batch_stage.py finalize-implementation`。Runtime 检查当前 Batch/run 没有活动 TASK、所有 TASK 都有完成记录，并将实际封存的 commitSha 写入实现阶段 Evidence。旧 Run 缺失阶段登记时，仅在这些检查通过后兼容补齐。准备阶段 Evidence 绑定冻结的仓库基线；实现阶段 Evidence 绑定真实交付提交，避免提前准备后被误判为提交过期。

## Windows 编译

默认直接使用执行终端 PATH 中的 `mvn`，不要求查找或编译 `mvn.exe`。只有命令不存在或无法启动才检查安装/PATH。执行工具的 `cwd/workdir` 应指向当前 TASK 的业务模块目录；也支持 `cd "<模块目录>" && ...`。Git Bash 的 `/d/...` 和 Windows 的 `D:/...` 会按相同 workspace 匹配。

```bash
set -o pipefail && mvn clean compile -DskipTests 2>&1 | tee "compile.log"
```

guard 支持生产编译的输出重定向以及 `head/tail/tee` 管道，但不能只凭截断输出判断编译通过；必须保留完整输出和真实退出码。版本/帮助、`which mvn`、`command -v mvn` 等环境查询不需要切到其他目录。追加 `test/package/verify`、其他验证命令、命令替换或后台执行不会获得生产纯编译豁免。

## 拦截诊断

新版报错附带 `GUARD_DIAGNOSTICS` JSON，其 `guardVersion` 为 `code-stage-v2`，包含实际 `executionCwd`、读取的 Feature 目录、Batch 阶段/状态、TASK 状态和绑定，以及以下失败条件。报错没有此字段时，先确认 Windows 实际加载的插件已经更新。

| failedChecks | 含义 |
| --- | --- |
| `COMMAND_FORM_UNSUPPORTED` | shell 形式不在纯编译识别范围内，例如后台进程或额外命令 |
| `VALIDATION_GOALS_NOT_ALLOWED_IN_CODE` | 命令目标包含测试、打包等其他验证 |
| `ACTIVE_TASK_RUN_MISSING` | 当前 Batch/run 没有可用活动 TASK |
| `TASK_RUN_INACTIVE` | 找到了绑定的 TASK，但其状态已结束或中止 |
| `TASK_BATCH_BINDING_MISMATCH` | 编译目录有活动 TASK，但属于其他 Batch/run |
| `TASK_WORKSPACE_MISMATCH` | 当前 Batch/run 有活动 TASK，但其 workspace 与编译目录不匹配 |
| `BATCH_STAGE_NOT_CODE_OR_REPAIR` | 已进入 Review、UTest 等其他阶段 |
| `CODE_STAGE_STATE_INCONSISTENT` | 指针为 Code/none，但 Review/UTest 仍显示运行中 |
| `IMPLEMENTATION_ALREADY_FINISHED` | 实现阶段已结束，不能继承陈旧 TASK 的编译权限 |
| `BATCH_NOT_EXECUTABLE` | Batch 已阻断、等待重试、失败或交付结束 |

根据这些字段和 `task_runner.py inspect` 的实际输出恢复流程，不要把 `none` 当作缺少 Maven，不要切换中立目录绕过 guard，也不要直接修改 Runtime JSON。Review 继续禁止验证；UTest 只有 Review 通过且实际 test 阶段运行时才能运行其他验证。
