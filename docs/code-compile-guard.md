# Code 阶段编译权限与状态诊断

Code/生产代码修复阶段允许当前 Batch/run 绑定的活动 TASK 执行生产纯编译。`activeStage=none` 只表示阶段指针为空，不能单独作为拒绝纯编译的理由。编译必须发生在对应 TASK 的 `finish-implementation` 成功之前。

## 阶段登记

`task_runner.py start`、`start-task-repair`、`resume` 在校验租约、原生 worktree 和 TASK 绑定后，由 Runtime 登记准备阶段并启动 `implement`，成功返回后才能写生产代码。后续 TASK 和中断恢复复用同一个阶段 attempt；Review/UTest 打回生产代码后重置阶段，再启动新 attempt。已进入 Review/UTest 的阶段不能被陈旧 Code run 重新打开。

全部 TASK 完成并封存后，Workflow 调用 `parallel_batch_stage.py finalize-implementation`。Runtime 检查当前 Batch/run 没有活动 TASK、所有 TASK 都有完成记录，并将实际封存的 commitSha 写入实现阶段 Evidence。旧 Run 缺失阶段登记时，仅在这些检查通过后兼容补齐。准备阶段 Evidence 绑定冻结的仓库基线；实现阶段 Evidence 绑定真实交付提交，避免提前准备后被误判为提交过期。

## 命令识别与 Windows 目录

guard `code-stage-v3` 静态分析脚本中的实际可执行程序，再按 Maven 目标和 TASK/Batch/run 绑定授权，不匹配某一行固定命令。支持 `mvn/mvn.cmd/mvnw/mvnw.cmd`、`-q/-o/-D`、字面量环境赋值和 `export`、`cd`、多行/分号/`&&`、输出重定向、`head/tail/tee`、`PIPESTATUS` 保存以及 `echo/printf/exit`。静态 `bash -c/-lc` 包装会递归检查；每个额外可执行命令也会单独检查，所以 `mvn compile && mvn test` 仍不属于 Code 生产编译。

Maven `compile` 和 `clean compile` 属于生产编译；`test/package/verify/install/deploy` 和其他插件目标不获得 Code 编译权限。正确区分 `-f/-P/-s/-pl/-D/-T` 等选项和值；`-f/--file` 的构建文件必须在授权工作区内。项目 POM/profile 中绑定的其他行为仍需从实际构建配置确认。环境版本、帮助和程序定位查询单独放行。

默认使用终端 PATH 中的 `mvn`，只有命令不存在或无法启动才检查安装。工具的 `cwd/workdir` 应指向实际业务模块；相对路径必须结合工具提供的绝对 `workspaceRoot`（兼容 `workspace_root`）解析，缺少根目录时报 `EXECUTION_CWD_UNRESOLVED`，不得猜根目录。也支持同一次执行中的 `cd "<模块目录>" && ...` 或 `cd "<模块目录>" || exit "$?"`。Git Bash `/d/...` 与 Windows `D:/...` 按相同路径比较；POSIX 路径保留大小写。目录比较依据执行目录与构建文件，日志路径不能授予编译权限。

命令替换、进程替换、未经检查的脚本、动态目标和后台进程不获得编译豁免；拒绝信息说明具体语句和原因。执行工具自身的 cwd 限制属于宿主校验，必须与插件 guard 拒绝分别报告。

## 后端编译结果登记

每个后端 `executionMode=code` TASK 在完成生产代码后执行以下流程；前端与非 code 模式不增加此门槛。Plan 不预先固定模块编译命令，运行时从当前工作区的项目配置选择目标。

1. 调用 `task_runner.py prepare-compile`，传完整 TASK 绑定、当前 TASK 的 `--run-id`、模块绝对 `--execution-cwd`，在 `--` 后传精确 compile-only argv。保存返回的真实 `attemptId`。该入口冻结业务文件内容快照，**不启动编译**。
2. 在 Bash 中运行同一 argv，实时显示完整 stdout/stderr，同时 tee 到业务工作树之外已有的临时日志目录。
3. 立即保存完整 `PIPESTATUS`，分别保留 compiler 和 tee 退出码；把两个值和日志位置传给 `task_runner.py record-compile`。该入口只读已有日志并登记结果，**不启动编译**。
4. 检查 `passed/exitCode/logExitCode`。`ok=true` 仅表示结果登记成功，包括失败编译的登记。失败编译应依据真实输出修复，再重新准备和编译；登记失败保留原日志，只重试登记。
5. `finish-implementation` 要求当前 TASK/run 最新尝试已有成功 Evidence、完整日志及匹配哈希，且业务文件快照仍与编译前一致。编译后改代码必须重编。日志和 Evidence 保存在 Feature 产物目录，不写 UTest 结果、不恢复 Batch compile 状态。已完成 TASK 的幂等完成仍可返回原结果；未完成旧 Run 必须补齐有效编译结果。

下面是 Bash 模板，guard 同时接受其他等价包装写法：

```bash
export MAVEN_OPTS="-Dfile.encoding=UTF-8"
cd "<模块绝对目录>" || exit "$?"
set -o pipefail
mvn compile -o 2>&1 | tee "<临时完整日志>"
compile_status=("${PIPESTATUS[@]}")
printf 'MAVEN_EXIT=%s LOG_EXIT=%s\n' "${compile_status[0]}" "${compile_status[1]}"
exit "$((compile_status[0] != 0 ? compile_status[0] : compile_status[1]))"
```

准备和登记是独立工具调用，全部传同一 `--workspace/--feature/--task-id/--code-workspace/--workspace-ref`，并在并行模式传真实 `--parallel-run-id/--lease-token`；不得把 parallel runId 当作 TASK runId。

```bash
python -X utf8 "<插件目录>/hooks/task_runner.py" prepare-compile <完整绑定参数> --run-id "<TASK runId>" --execution-cwd "<模块绝对目录>" -- mvn compile -o
python -X utf8 "<插件目录>/hooks/task_runner.py" record-compile <同一完整绑定参数> --run-id "<同一 TASK runId>" --attempt-id "<实际 attemptId>" --exit-code <实际 compiler 退出码> --log-exit-code <实际 tee 退出码> --output-file "<临时完整日志>"
```

`inspect --run-id` 返回 `productionCompileRequired`、`latestCompileAttemptId` 和 `compileAttempts` 和 `compileSummary`，后者明确显示 missing/prepared/passed/failed/stale 及源码是否仍一致；记录包含准备状态、精确 argv、目录、两个退出码、源码哈希、完整日志路径和 Evidence ID。重新准备后旧 attempt 不能覆盖最新结果；登记可幂等重试，不能用不同结果覆盖同一 attempt。

## 拦截诊断

**每次被拦截都必须展示具体原因，不能只报告 guard rejected。** 拒绝文本和 `GUARD_DIAGNOSTICS.failures` 都返回原因；命令语法/目标错误附 `line/statementIndex/statement`，并遮盖环境变量值和敏感参数。JSON 保留兼容字段 `failedChecks`，同时返回 `guardVersion/guardPath/toolCwd/executionCwd/featureDir/batch/taskRuns`。`guardPath` 用于核对 Windows 实际加载的插件。没有该字段或版本仍是 v2 时，先更新实际安装插件。

| failedChecks | 具体含义 |
| --- | --- |
| `COMMAND_FORM_UNSUPPORTED` | 原因明确指出引号、命令替换、动态变量、非法管道、后台执行或 cd 失败保护等问题，并给出所在语句 |
| `VALIDATION_GOALS_NOT_ALLOWED_IN_CODE` | 返回实际可执行程序、Maven 目标或缺少参数值的选项 |
| `EXECUTION_CWD_UNRESOLVED` | 相对 cwd 缺少工具真实绝对 workspaceRoot，无法核对目录 |
| `BUILD_FILE_WORKSPACE_MISMATCH` | 返回越界的构建文件路径和授权根目录 |
| `ACTIVE_TASK_RUN_MISSING` | 当前 Batch/run 没有登记可用 TASK |
| `TASK_RUN_INACTIVE` | 当前 Batch/run 的 TASK 已完成或中止 |
| `TASK_BATCH_BINDING_MISMATCH` | 目录的活动 TASK 属于其他 Batch/run |
| `TASK_WORKSPACE_MISMATCH` | 返回实际执行目录与不匹配的 TASK 工作区 |
| `BATCH_STAGE_NOT_CODE_OR_REPAIR` | 返回当前阶段及允许验证所需的阶段条件 |
| `CODE_STAGE_STATE_INCONSISTENT` | 返回阶段指针及冲突的 review/test 状态 |
| `IMPLEMENTATION_ALREADY_FINISHED` | 返回已结束的 implement 状态 |
| `BATCH_NOT_EXECUTABLE` | 返回 blocked、retry_pending、failed 等实际 Batch 状态 |

按照 `failures` 和 `task_runner.py inspect` 修复对应问题；`-o`、`-q`、`pipefail`、`tee` 本身不是拒绝理由。不得切到中立目录绕过 guard 或手改 Runtime JSON。Review 仍只读；其他验证须由 Review 通过且实际 test stage 正在运行的 UTest 执行。
