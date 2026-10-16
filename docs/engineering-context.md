# Code Workflow 工程事实与阶段交接

本机制在 Code Workflow 内按需运行。Design 阶段、Design 产物格式和 Plan V2
不变；Workflow 启动前不增加探索、索引或全仓扫描。首次 Code 遇到未知 API
才查询，得到结论立即发布，后续 Batch、UTest、Fix、Rework 复用相同版本的事实。

## 产物与职责

Runtime 返回所有准确路径，模型不得推导或手改下列文件：

| 产物 | 内容 | 所有者 |
| --- | --- | --- |
| Feature `.engineering/facts/<repositoryId>/` | 带来源的 API、用法、前置条件、测试配方和能力结果 | engineering_facts |
| Feature `.engineering/dependencies/<repositoryId>/` | 精确 classpath 中依赖类与 source-jar/javap 读取结果 | utest_runtime |
| Feature `.engineering/commands-<digest>.json` | 从当前 argparse parser 生成的命令 schema | runtime_guidance |
| Feature `.engineering/observations/<repositoryId>.json` | 缓存命中、等待、接管和版本失效计数及有限事件 | engineering_observability |
| Feature `.engineering/deployments/<digest>.json` | 启动器实际源码、执行副本路径和 SHA | workflow_launcher |
| Run `dispatch/<Batch>.json` | 当前派发代次与私有 token | parallel_dispatch |
| Run `engineering/<Batch>/handoffs/<TASK>.json` | 实际变更、任务摘要、实现/编译 Evidence、相关事实 | task_runner finish |
| Run `engineering/<Batch>/contexts/` | 当前阶段的最小上下文、准确引用、恢复位置 | engineering_context |
| Run `engineering/<Batch>/repairs/<digest>.json` | 失败瞬间的不可变修复包 | stage/test runtime |
| Run `engineering/<Batch>/REPAIR_CONTEXT.json` | 当前修复上下文及有限定位/尝试记录 | engineering_context |

工程事实都是数据，不授予执行权限，不替代规格、当前源码或通过 Evidence。
`source_observed` 表示从源码观察，不表示已编译或已测试。测试配方必须引用实际
测试文件；runner 只有在这些文件真实执行、退出码和日志捕获正常且有用例计数及
Evidence 时，才追加 `referenced_test_executed` 与目标证据。它仍不证明所有分支覆盖。

## 唯一查询入口

`hooks/engineering_context.py` 的每个操作都携带：

```text
--workspace <artifact workspace> --feature <feature>
--run-id <parallel run> --batch-id <Batch>
--batch-worktree <native worktree> --stage <code|review|utest|fix|rework>
--task-id <当前 TASK，可重复>
```

写事实、确认修复归属或登记尝试还必须携带当前 `--owner-token`。查询与发布必须
使用相同 TASK 集合。只读上下文可在阶段准备/恢复时加载；写事实验证当前 Batch 与 lease；Code/rework 的 start 前准备可写工程元数据，不能借此写生产源码。UTest 写事实只在有效 test 预算内进行。metadata-only 的 rework 定位允许 implement=pending，
不允许借此提前写业务文件。UTest 查询也检查已有期限，不续预算。

0. `lookup --symbol <符号> [--kind <类型>] [--source <准确文件>]`：纯只读命中查询，不创建 claim；允许 Code start 前及 Review 使用。
1. `prepare`：只加载并验证既有材料。返回 `contextPath/contextDigest/allowedReads`、
   `commandCatalogPath`、`operations`、`repairTaskIds`。`operations` 使用当前身份，
   缺业务输入时返回 `missingArguments`，不会编造 compile argv、提交摘要或退出码。
   租约参数只在本次 stdout 返回，不写入缓存或上下文文件。
2. `locate --symbol <完整类名> --module <已选模块相对路径>`：只查 Git tracked
   文件名，不读每个源码。多候选返回 ambiguous，不能任意选第一个；未提交新增类
   使用当前 TASK 已知准确文件路径。依赖 JAR 使用现有 UTest lookup-class/inspect-class。
3. `resolve --symbol <完整符号，可用 Class#method> --source <相对文件，可重复>`：
   对 api_signature 做一次保守源码片段读取。它不是 AST 或语义正确性判断。
4. `query --kind <类型> --symbol <符号> --source <相对文件，可重复>`：用于
   usage_example/runtime_precondition/test_recipe/capability_result，也可人工解析签名。
   来源必须包括支持结论的定义、调用示例及相关 fixture/test；默认加入各级构建输入。
   结论依赖解析后的第三方能力时，附加 `--classpath-file <Worktree相对快照>`，
   把准确 JAR 顺序与内容指纹纳入失效条件。没有该快照的执行证明标为
   dependencyScope=build_inputs_only，不证明解析后依赖完全相同。
5. claimed 后，在 Worktree 或系统临时目录准备 JSON，通过
   `publish --fact-id <ID> --claim-token <token> --content-file <JSON>` 立即发布。
   不得把口头结论升级为测试通过。失败用 `fail`；临时错误加 `--transient`。

状态处理：

| status | 动作 |
| --- | --- |
| hit / ready | 复用来源与结论，按需核实语义 |
| claimed | 只有领取者解析，完成立即发布 |
| in_progress | 先做独立工作，按 retryAfterSeconds 有限重查，不重复 grep |
| cached_failure | 环境未变化时复用失败诊断，不重试相同方案 |
| blocked | 修复具体身份、输入或预算错误，不查插件源码绕过 |

查询所有权默认 120 秒，过期可接管；旧 token 无法覆盖新结果。领取者崩溃后
不会永久占用。等待和解析都消耗原阶段时间，不延长 lease/stage 预算。

## 共享边界与失效

repositoryId 取 Git common-dir 身份，避免以 worktree 绝对路径隔离知识。
同问题、同来源内容、同构建输入才能共享；实际文件内容包含未提交修改，不能仅看 HEAD。
source/test/fixture 任一变化都使依赖它的事实失效。缺失构建文件也记录，新增 POM
会失效。外部 symlink、嵌套 Git 根、越界路径和超大源文件拒绝进入解析。

测试配方与能力结果还绑定 JAVA_HOME、JAVA_TOOL_OPTIONS、MAVEN_OPTS、PATH。
临时失败有效期 60 秒；稳定失败随来源/环境变化失效。调用者必须完整声明支持结论
的来源；无法自动发现的外部环境变化仍需现有 refresh-diagnostics，不据旧缓存宣称可用。

依赖查询仅访问当前模块提供的 classpath，保留顺序与重复类。共享缓存按构建内容、
环境、classpath 与依赖内容校验。源码 JAR 缺失时才走有限 javap。不会递归扫描 .m2。
有副作用的 setup、实际测试、编译以及 Evidence 绑定仍保留原工作树/目标身份；
不能把另一 Batch 的通过结果作为当前 Batch 的结果。

## 修复和恢复

`finish-implementation` 成功后从完成快照自动生成有限声明片段事实并关联交接；sourceOnly 声明片段只绑定来源内容，不随无关测试 POM 变化失效。语义事实和依赖能力继续校验完整必要来源。未发布 claim 在完成时记为显式失败，不能默认为 ready。已完成任务重复 finish 只补交接，
不会重新实现或重新编译。UTest prepare 附加独立 engineeringContext，不改 router
的 UTEST_ASSIGNMENT 或 Plan 投影。只注入当前任务相关的有限事实与引用。

runner 记录失败时立即保存测试修复上下文；stage fail 在重置阶段状态前保存生产
修复上下文。修复包包含原 finding、当前 worktree/commit、准确命令/cwd/testFiles、
目标/Evidence、独立证据日志片段、任务候选、期限和已尝试方案。原 failure 对象不改形状。

Rework 先检查全 Batch 的旧活动 run，但仅重开 repairTaskIds。无法归属时，按
交接和精确失败做一次有限定位，再用 `repair-scope --content-file <JSON>` 登记：

```json
{"affectedTaskIds": ["T001"], "reason": "失败位置属于该 TASK 的实际变更"}
```

禁止默认为全部 TASK。用 `record-attempt --content-file <JSON>` 保存 hypothesis/result，
恢复不再重复已排除方案。新错误继续保留先前尝试。上下文暴露 Runtime 的
resumeCursor 和原 deadlines；awaiting_record 只补记原输出，不启动 Maven。

固定 Batch Workflow 遵循当前一次性生产修复规则：在原 Worktree 修复、完成所需
后端纯编译和封存，由 Runtime 登记 single_repair_accepted 后直接继续 UTest，
不再派发第二次 Review；UTest 来源重跑原失败命令。测试自身修复和生产修复各按
现有有限次数执行。耗尽后按现有 deferred/阻断策略保留真实结果，绝不称为通过。
独立手动流程仍按其自身阶段契约执行。前端 UTest 暂时跳过的策略不变。

所有编译/测试继续前台 Bash、实时合并输出、保存真实进程与 tee 退出码，runner
只做绑定和 record-only 登记。Runtime guard 允许读取准确引用，阻止直接改写上下文、
事实、命令 schema、修复包和执行文件。命令参数错误直接返回实际 parser 的
argumentSchema，不要求重复 --help。

## 验证

`tests/test_engineering_context.py` 覆盖真实 Git worktree 并发、跨分支/脏内容隔离、
构建/环境失效、过期接管、防旧生产者覆盖、缓存损坏与负结果、自动交接、修复归属、
真实 CLI 和 guard。既有 UTest、任务、阶段状态机和 Workflow 测试负责执行/登记与
一次性修复边界。业务项目真实回放和收益评估属于后续交付，不预估节省比例。


## 路径契约与并发写入

`worktreePath` 始终是 manifest 绑定的 Batch Git 根；`taskWorkspace` 是 TASK 请求的
模块目录，`executionCwds` 是真实编译 cwd。变更和快照以 Batch Git 根为基准，中文、
空格和多层模块路径不改变这个约定。交接读取完成时冻结的 snapshot，幂等 finish
不能把后续脏文件当成原实现。当前交接由 TASK Evidence/revision 防止旧生产者覆盖；
每次内容另存 `handoffs/snapshots/<TASK>-<digest>.json`。

共享目录位于 artifact workspace，不随 Batch worktree 创建或清理。只有可验证的
工程事实和只读依赖解析结果跨 Batch 共享；TASK 运行、真实编译/测试结果、日志和
修复上下文继续按 run/Batch 隔离。

| 写入对象 | 并发控制 |
| --- | --- |
| 同一个 API 内容版本 | 每个 factId 的短锁、过期 claim、token 校验、原子写入 |
| 同一个依赖解析版本 | 每个 cache key 的短锁；JAR/source/javap 解析在锁外；发布前重验输入与 token |
| 不同 API 或依赖版本 | 独立 key 并行；不持有整个 run 锁解析依赖 |
| 同 TASK 交接 | 每 TASK 锁、Evidence/revision 校验和不可变历史 |
| 同 Batch 修复记录 | record_repair、repair-scope、record-attempt 共用 `.repair.lock` |
| 同事实的多个测试证明 | 每 fact 锁内按 run/Evidence/command/target 合并 `testProofs` |

计数采用 repository 分片的短锁，保存最近 200 个事件和有限版本索引，诊断最多展示
最近 50 个事件；不保存 token、原始命令或源码。来源版本变化计入 invalidated，
不是删除旧版本。旧版本仍有自己的内容 key，不会被另一个 Batch 的新版本覆盖。

## 派发与恢复

Scheduler 返回 `dispatchGrants[Batch]=generation`；普通 status/poll 不生成新代次，
只有 retry_pending 的实际重新入队才递增。Workflow 本地按 `Batch:generation`
去重，并在开始生命周期前用 `parallel_dispatch claim` 在 run 锁内检查当前状态、
依赖、并发容量和当前持有者。一次代次已收口后，新建 drain 也不能重放它。claim 命令携带本次调用的唯一
requestId（launcher 每次生成唯一 workflowInvocationId，脚本追加确定性序号），空响应后的同一命令可取回原许可；另一个 Workflow/worker 的请求不会
获得该 token。重复 defer 只读回既有收口，不重复扣重试次数。

`finish/defer` 都携带准确代次和 token。过期持有者返回 stale_owner_ignored，不清理
新租约。阶段 CLI 的 dispatch-generation/dispatch-token 与 acquire 的相同身份检查
阻止旧 Review 或旧任务子进程进入新一轮；业务 lease 也绑定派发身份。
这些参数由 Workflow 注入，不能代替业务 lease-token/owner-token。
阶段切换续派发所有权，不重置 UTest 的首次命令期限或总期限。

空闲 worker 在本地生命周期完成时立即唤醒，其余外部变化以 30 分钟轮询兜底；
Scheduler 读取合并同一时刻的请求。空闲超时依据 Runtime 返回的 observedEpochMs，
本地唤醒不计作完整轮询；读取失败时保留有限轮询兜底，脚本不访问环境时钟或随机全局。
launcher 在复制固定模板前检查已知禁用全局，并校验发布副本 SHA；新启动必须原样传递
完整 workflowArgs，不能手工拼造 invocationId 或修改副本绕过检查。
ready 但没有可领取许可时报告
ready_batches_not_dispatchable 及原因，不再无提示等待数小时。

Review 只在 durable decision 明确 passed 时放行 UTest；success=true 和
needs_triage 的组合仍阻断。handoff/路径/契约/阶段顺序类错误记录 precondition_blocked，
相同前置条件下重复错误记录 unchanged_failure，不反复消耗模型重跑。
暂时失败仍用原有限重试预算；final-repair 和新建 drain 不重置该预算。
本交付不新增旧运行 handoff 重建、路径迁移或批量修复入口。

## 查看缓存与只读诊断

从插件目录执行（workspace 必须是 artifact workspace）：

```bash
python3 hooks/engineering_context.py inspect \
  --workspace /absolute/artifact-workspace --feature FEATURE \
  --run-id RUN_ID --batch-id B001
```

省略 batch-id 查看整个 run。此命令只读 manifest、交接、部署回执和计数，不需要
业务 lease，也不调用 prepare/schedule，不创建目录、锁或缓存。
`paths.factsRoot/dependencyRoot/observationsRoot` 是当前准确位置；
`metrics.counts` 包括 fact/dependency 的 hit、wait、claimed、takeover、invalidated
及 publication/rejection 计数。没有事件的项目可以缺省，不代表已观测到零次失败。
`batches.*.reasonCodes`、阶段状态、派发代次和失败指纹用于定位卡点；
`runtimeVersions/workflowSource/workflowArtifact/deployments` 用实际文件 SHA 核对
加载的插件及执行副本。没有部署回执时不会推断实际执行脚本。


## 共享依赖、环境诊断与阶段停止

`engineering_context.py lookup-class/inspect-class` 提供跨阶段依赖入口，携带当前身份、
`--module <相对模块>`、`--classpath-file <准确snapshot>` 和 `--class-name <完整类名>`。
前者定位，后者共享 source JAR/有限 javap 结果。缺少 snapshot 时由 UTest 受控 setup
生成一次；不为缓存新增 Workflow 前探索或绕过 Code compile-only 权限。

环境声明缓存位于 `.engineering/environments/<repositoryId>/`；受支持 Maven 版本、依赖树、
classpath/effective-pom 诊断位于 `.engineering/diagnostics/<repositoryId>/`。
key 包含模块相对路径、构建内容、工具/环境、Maven settings 与参数；不以 Batch cwd 隔离。
诊断执行有短期 claim，过期旧脚本不能启动；classpath 结构化内容可物化到当前工作树的非源码、
非 tracked 输出文件，或 Runtime 命名前缀的临时文件，不重用另一个工作树的 target/classes。
暂态失败缓存 60 秒；外部依赖变化可 refresh-diagnostics，不能从声明或旧 PASS 推断本次通过。

政策由 `utest_policy.py` 唯一维护；test attempt 开始时固化版本、摘要和绝对期限，默认总计
2700 秒（45 分钟）、首次真实测试 1800 秒（30 分钟）、命令最多 300 秒。首次真实测试启动后继续使用阶段剩余预算。旧运行缺少政策快照时保留已知更早期限，
不自动从旧 900 秒获得 2700 秒。恢复/续租/修复不能延长已保存期限。

Workflow 独立于 drain-poll 执行 check-test-budget，工具 hook 也在有当前 worktree 绑定时
检查期限并阻断普通读写/探索。到期登记 stopRequested/utestClosurePending，保留封存所需 lease；
只能补记原输出、封存有效资产、record-test-failure、release。封存前禁止收口为通过或进入候选。
这是工具边界的协作停止；当前平台没有在此脚本中可调用的模型硬取消接口，不声称停止无工具推理。

prepare-test 在派发前返回有界 engineeringContext brief 和固化政策；未知/失效处通过 cacheGaps
给出补查理由。部署回执包括实际 Workflow、policy、Runtime 和测试 agent 定义的内容哈希；
派发前核对 launcher 与当前 Runtime 摘要，更新插件后需重新运行 launcher。
