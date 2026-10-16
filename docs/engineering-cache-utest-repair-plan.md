# 工程探索缓存与 UTest 修复方案

本文记录修复设计；2026-10-16 已在当前仓库实施，并新增跨 Batch 真实 Runtime 入口、声明自动发布、结构化诊断恢复、预算和资产封存回归测试。Windows 原始流水和目标业务 Maven 模块未在本机回放，平台模型硬取消未确认；当前实现采用独立预算检查与工具边界协作停止。

## 目标与问题边界

让 Code、Review、UTest、Fix、Rework 继承内容有效的工程事实与交接，在未知或失效处补充探索；当前任务的测试必须有绑定当前实现的实际执行证据。

当前主要问题：

1. Workflow/runtime 为 1800 秒，scheduler 为 900 秒，首次真实测试期限另为 600 秒。
2. Code SKILL 同时保留“不得持久化共享认知”和“发布共享事实”；阅读入口在 TASK start 前，缓存协议却主要要求 start 后执行。
3. handoff 自动生成，语义事实依赖 agent 主动发布；finish 没有检查悬而未决的探索 claim。
4. 测试 agent 主指令没有明确说明 engineeringContext 的优先级与读取步骤；部分限读、环境检查规则容易被理解为必须重探。
5. JAR 解析只有经专用入口才缓存；lookup-class 与 inspect-class 没有在实际派发提示中完整串联，Code 阶段还缺少同等的只读依赖入口。
6. 环境检查按 Batch 保存、Maven 诊断 key 包含实际 worktree cwd，跨 Batch 复用范围有限。
7. scheduler 超时只收口状态与 lease，没有在该路径中取消 agent；普通探索工具不会持续触发 runtime 期限检查。

## 一、统一所有入口的复用规则

同步修改：

- `agents/test-engineer.md`
- `skills/autodev/autodev-code/SKILL.md`
- `skills/autodev/autodev-code/references/batch-workflow-guide.md`
- `skills/autodev/autodev-utest/SKILL.md`
- `skills/autodev/autodev-utest/reference/test-engineer-task-protocol.md`
- `workflows/code-batched-execution.workflow.js` 的 Code、Review、UTest、修复派发提示
- 对应工程上下文和 UTest 文档

UTest 的统一核心指令：

> 优先读取 Runtime 提供的当前实现交接、有效工程事实、依赖解析与已有测试配方；复用版本有效的探索结论。只对缺失、失效或与当前任务行为相关但未解释的部分做有限探索。按当前 testPoints/verificationIntent 独立设计断言，并在当前绑定下实际执行测试。历史结论、他处 PASS、依赖声明都不能代替本次执行证据。

具体消除歧义：

- “自主验证”约束测试目标、断言及执行证据，不要求重新发现所有签名与依赖。
- “不复用历史 PASS”不禁止复用 API、用法、环境诊断和测试源码；同一 attempt 的恢复只补登记已有原始输出，另一次实现验证仍按原状态机执行。
- “不从缓存推断仓库”只限制仓库/模块权威绑定；绑定由 manifest、Plan 和 inspector 确定后，可读取通过该绑定验证的缓存。
- 限读范围明确包含 Runtime 返回的共享上下文与事实路径；不要求跨 Batch 自行枚举文件。
- 缺失测试资产时才生成；已有精确匹配的测试先评估并补齐覆盖，不按阶段重新创建。
- Code 的 start 前只读准备也先查已有缓存；生产源码修改继续要求 TASK start 成功。
- Review 可只读命中事实，不领取或发布探索 claim。

当前仓库未检索到明确的“从 0 开始”原句。发布时还须核对实际安装插件、部署副本和 agent 定义，防止远端执行旧文本。

## 二、让缓存进入 Runtime 的正常路径

### 2.1 分离只读命中查询与生产者领取

调整 `engineering_context.py`，提供无 claim、无写入的命中查询。它可在当前身份绑定下用于 TASK start 前准备和 Review。

需要解析并发布新事实时，单独验证当前 Batch 生命周期、lease 和元数据写权限。工程元数据写权限不能授予生产源码写权限，也不能为了使用缓存提前开始业务修改。

调用方只提供准确符号和已知有限来源。对未知入口保留 locate；不建立全仓扫描索引，不新增 Workflow 前探索。

### 2.2 自动保存可机械提取的事实

在 `finish-implementation` 记录完成快照时，自动生成有明确来源的变更索引和保守声明片段，写入事实区并关联 handoff。

- 输入限于当前 TASK 实际 changedFiles、supportingFiles 及已声明使用的来源。
- 内容标为 source_observed，声明片段不冒充完整 AST、语义解释或测试通过。
- 对二进制、配置或不支持的语言记录具体 gap，不强制每个 TASK 的 factIds 非空。
- 来源使用完成快照及其哈希；不得在幂等 finish 时把后续脏内容标为原始实现。
- 缓存步骤可幂等恢复；失败不重复编译、实现或创建 implementation Evidence。

语义事实，例如静态链路隔离方式、真实用法和测试配方，保留 agent 发布，但查询结果直接返回绑定好的 publish/fail 操作和缺失参数，减少翻阅命令目录。

### 2.3 闭合探索 claim

TASK/阶段收尾检查本所有者的未完成 claim：

- 有真实解析结果则发布；无法完成则保存具体失败原因并释放/终结 claim。
- 不把未完成探索默认为 ready，不把“没有 publish”隐去。
- claim 被接管、输入变化、超时或旧 token 发布均产生可诊断结果。
- 不以“全局 factCount 必须大于零”作为业务通过条件。

涉及文件：`engineering_context.py`、`engineering_facts.py`、`runtime_guidance.py`、`task_runner.py`。

## 三、派发 UTest 前准备好上下文

在现有 prepare-test/UTest prepare 路径中组装当前任务的有界上下文，并由 Workflow 原样传递给测试 agent：

- 当前实现文件、完成快照哈希、implementation/compile Evidence 引用。
- 相关 API 片段、已发布用法和静态隔离配方，附来源和验证等级。
- 当前依赖解析结果、能力状态和已失败的准备方案。
- 已有精确测试文件、原命令、原输出和 resumeCursor。
- 明确的 cacheGaps、剩余预算、下一步操作及准确读取路径。

小摘要直接进入派发提示；大内容通过准确引用按需读取，不把完整命令 catalog 或整个 Task Run 注入上下文。

派发时重新校验来源版本；stale 内容保留诊断但不作为有效事实。handoff 的事实引用不是唯一发现途径：后续 prepare 可纳入当前有效的新事实。

缓存为空时给出准确缺口和有限补查路线，而不是笼统要求重新探索工程。

## 四、补齐跨阶段的依赖与环境复用

### 4.1 统一只读依赖解析服务

将现有 lookup-class/inspect-class 的内容解析与缓存逻辑提取成可由 Code、UTest 和修复阶段调用的共享服务；现有 UTest 命令可保留为兼容适配层。

- 源码符号走 locate/resolve；依赖类走准确 classpath 的 lookup-class → inspect-class。
- source JAR 优先，缺失时仅做有期限的 javap；不递归扫描本地 Maven 仓库。
- 相同 key 只有一个解析生产者；其他 worker 复用或做独立工作。
- Code 优先使用已有编译信息/classpath；缺少 snapshot 时记录缺口，由 UTest 受控 setup 补齐，不为缓存额外运行测试或违反 Code 的 compile-only 边界。
- 同时支持精确查询，不要求 agent 先了解全部 CLI schema。

### 4.2 缓存身份与结果分层

跨 worktree 共享的身份包含 repositoryId、模块相对路径、所需构建内容、实际 profiles/诊断参数、JDK/Maven 与相关环境、已解析依赖内容。不得只把 cwd 删除就视为同一环境。

- 签名、JAR/source/javap 结果、依赖清单和经过验证的能力可共享。
- manifest、lease、真实编译/测试的执行记录与 Evidence 继续绑定当前 Batch。
- 含当前 worktree 输出目录的结果必须重绑定并校验，不盲目复用另一个 worktree 的 target/classes 或临时文件。
- classpath 缓存存结构化内容；当前 Batch 缺少实际 snapshot 文件时由 Runtime 物化，不因为旧临时路径失效就重跑 Maven。
- setup 的实际副作用不通过缓存伪装为已经在另一环境执行；只共享经允许、内容有效的解析与诊断数据。

负结果按原因处理：确定缺失随输入/依赖环境版本变化失效；网络、超时等暂态失败使用有限 TTL。下载补齐、settings/JDK 等外部环境变化需要明确刷新信号，避免永久缓存一次缺失。

API 源码事实与依赖能力采用各自必要的失效输入；测试依赖 POM 的无关变化不应无条件淘汰纯源码声明片段，涉及类型解析/依赖版本的事实仍须失效。

涉及文件：`engineering_facts.py`、`engineering_cache.py`、`utest_runtime.py`、`utest_presets.py`、`run_utest_command.py`。

## 五、统一预算与状态机

默认政策继续明确为：

| 项目 | 默认值 | 起点与范围 |
| --- | --- | --- |
| UTest 总预算 | 2700 秒（45 分钟） | 当前 test attempt 开始，包含全部准备、探索、生成、执行和修复 |
| 首次真实测试启动期限 | 1800 秒 | 同一 attempt 起点；setup 不满足该条件 |
| 单条测试命令 | 300 秒 | 实际开始执行；取剩余阶段预算的较小值 |

将这些值收敛到一个 Runtime policy 来源。创建/开始阶段时将 policy 版本、摘要、startedAt 和绝对 deadlines 固化；Workflow、scheduler、preflight、生成脚本和 agent 均读取相同政策。

- 删除 JS/Python 各处独立的 900/1800 秒常量；命令行传入值只能明确收窄，不能悄悄续期。
- 多 TASK Batch 的首次启动期限按 Batch attempt 计算，不能每个 TASK 重置。
- lease 续期、换 agent、重复 prepare、修复恢复都不能重置阶段时间。
- 修复重跑原命令，并保留原阶段预算；显式开启新 attempt 才能按状态机领取新预算。
- 首次期限到达仍未运行测试时立即记录准备失败/环境阻断和未覆盖目标，避免继续写测试到总期限结束。
- 确实需要更大预算的项目使用显式配置；保留有效预算与来源，不靠后台执行或刷新延后达成。

`drain-poll` 按用户要求保持 30 分钟。它负责调度刷新，不作为精确阶段超时计时器；阶段超时必须有独立机制。

涉及文件：`utest_presets.py`、`parallel_batch_stage.py`、`parallel_batch_scheduler.py`、`utest_runtime.py`、`run_utest_command.py`、固定 Workflow。

## 六、超时后真正停止工作并完成收尾

实现独立于 drain-poll 的当前阶段期限监控：

1. 到期原子登记 stopRequested、具体原因、attempt/dispatch 身份。
2. 平台若提供真正的子 agent 取消能力，由 Workflow 调用；验证 timeoutMs 的实际语义，不假定传参等于已取消。
3. 插件工具边界检查当前停止状态；过期 agent 不再领取新事实、启动新命令或改业务/测试资产。停止提示明确给出下一步收尾操作。
4. 只终止当前身份所属的测试子进程组，不误伤其他 Batch 或用于保存输出的父进程。
5. 允许补登记原始输出、保存诊断、受控封存已有资产、释放 lease；停止检查不能把收尾本身阻断。
6. 收尾由当前合法所有者或 Runtime 协调器执行；不能靠旧 agent 的已撤销 token 强行 seal，也不能先清 lease 再要求该 token 完成封存。
7. 明确资产安全与在途写入处理完成后，才允许后续候选推广；旧 agent 不得在推广后继续修改同一交付。

无法确认平台支持硬取消时，交付应明确为“Runtime 阻断后续动作并请求停止”，不能声称已保证停止模型推理。强制停止和 Windows 进程树行为必须在真实平台回放验证。

## 七、诊断与验收

每个 Batch 输出有界的 cacheReadSummary/cacheWriteSummary 与时间消耗：

- 查询、命中、领取、发布、失败、未发布 claim 和失效原因。
- 自动生成事实数、交接数、当前上下文缺口及实际加载的 contextDigest。
- 环境准备、JAR 解析、测试生成、首次真实执行、修复和收尾的起止与耗时。
- 使用的 policy/deadlines、部署源码及 agent 定义版本。

这些数据与平台 agentsCached 分开，不用 agent 结果缓存指标衡量工程事实复用。

验收场景：

1. 冷启动：首次解析真实产生事实；下一 Batch/阶段同版本命中，不重复 javap 或同一解析。
2. 并发：两个 Batch 同问一个符号只有一个生产者；领取者退出后可接管，旧 token 不能覆盖。
3. 已有测试：UTest 复用当前适用测试并补齐缺口，实际执行有当前绑定的 Evidence。
4. 失效：生产源码/JAR/有效构建环境变化时应失效；路径变化、无关测试配置变化不造成不必要失效。
5. 缓存为空：准确展示 gaps，只探测所需目标；不做全仓或 Maven 仓库扫描。
6. 预算边界：第 900 秒不被旧 scheduler 收口；1800 秒首次启动期限、2700 秒总期限及命令期限使用可控时钟测试，恢复不续期。
7. 超时安全：普通探索中到期后停止新动作，保留已有测试/输出，按合法身份收尾，旧 agent 不能继续写入候选交付。
8. 恢复：awaiting_record 只补登记；Code finish 的缓存补写不重复编译或 implementation Evidence；source repair 保留既有次数与期限。
9. 真实回放：在 Windows/Git Bash 与目标 Maven 模块上覆盖静态类、离线缺依赖、中文路径、多 Batch 和异常退出，核对 toolstream 与缓存计数。

静态提示校验只作为补充；核心验收需要经过实际派发入口，验证上下文确实传入，并在真实回放中确认 agent 使用。组件测试通过不能替代该验收。

## 八、实施顺序与旧运行兼容

1. 第一批：政策单源化、15/30 分钟冲突、所有主指令及限读规则统一、部署版本核验。此批只是必要基础，不宣称缓存问题已完全解决。
2. 第二批：只读查询与领取分离、完成快照自动事实、claim 收尾、UTest 上下文自动传递。
3. 第三批：跨阶段依赖入口、结构化 classpath、跨 worktree 的诊断/能力复用及正确失效。
4. 第四批：独立超时监控、取消/工具边界协作、合法收尾与推广协调、真实 Windows 回放。

旧运行处理：

- 不手改已有 execution deadlines，不静默复活已 deferred 的阶段，不自动清空 journal。
- 旧缓存缺少新版本身份时标为 legacy/unverified；只复用可校验内容，否则有限重新解析。
- 没有旧政策快照的活跃阶段不得直接假定获得新 45 分钟预算；保留已知期限或标明需要受控新 attempt。
- 从既有完成快照补机械事实可以提供幂等入口；原始 read_file/javap 输出未归档时不凭空补成事实。
- 发布回执覆盖实际执行的 Workflow、policy、相关 Runtime 与 agent 定义；发现混用版本时给出明确错误。
- 回滚保留新生成缓存与诊断为可恢复数据，不修改历史 Evidence；对活跃 attempt 保持其既有政策，不因版本切换获得额外预算。

全部验收通过后，才将“探索缓存已接入真实工作流”作为交付结论。


## 本地交付验证记录（2026-10-16）

- 16 个相关 Python 模块合计运行 307 项测试：306 通过，1 项因当前文件系统特性跳过。
- 三个 Workflow JavaScript 测试文件合计 39 项通过。
- `python3 tests/test_code_workflow_integration.py` 的 11 项集成检查通过。
- 新增真实 Runtime 入口测试覆盖 B001 发布 → B002 Code start 前无 lease 只读命中、
  Code 阶段共享 JAR 检查、自动声明交接 → UTest brief、失效、诊断输出物化、并发接管、
  部署核验、独立预算检查、工具 hook 阻断及封存后 deferred。
- 阶段完成/封存也检查绝对期限；scheduler 超时事件分别报告首次启动/总预算及具体原因。
- `git diff --check` 通过。

生效方式：将当前插件版本同步到实际运行环境，并重新通过 launcher 取得新的脚本与完整
workflowArgs 后启动或恢复。旧运行不自动延长期限，不手改历史 Evidence 或 journal。
Windows/Git Bash 目标业务模块真实回放尚未执行；模型推理本身的硬取消未作保证。


首次真实测试启动期限已按用户要求调整为 1800 秒（30 分钟）；UTest 总预算调整为 2700 秒（45 分钟），单命令仍最多 300 秒。已有 attempt 的政策快照与期限不回写。
