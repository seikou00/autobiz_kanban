# Rollback 后旧 Run 恢复问题修复方案

日期：2026-10-29。本文是修复设计，尚未修改执行代码。

## 目标与当前缺口

当回退范围包含 `dev.code` 时，该 Feature 的旧 Code 执行必须失效。历史记录可以查看，但模型调用、界面恢复、通知恢复和原生启动接口都不能恢复旧执行，也不能利用旧 journal 返回过期的准备结果。

当前实现存在以下缺口：

- `hooks/rollback_stage.py` 归档的是产物工作区 `.cmbdevclaw/workflows/<feature>/`。平台实际在应用管理的项目/聊天目录保存 run，并兼容读取旧 `.cmbdevclaw/workflows/<threadId>/`；脚本副本的目录不能代表平台运行记录。
- 平台 `workflow/tool.ts` 可以根据 `resumeFromRunId` 加载保存的脚本、参数和 journal，没有校验插件执行是否已经因 rollback 失效。
- `workflow/engine.ts` 命中 journal 后直接返回历史 Agent 结果。把校验放在 Workflow 的第一个 Agent 中，校验本身仍可能被缓存跳过。
- Code 技能中的“恢复使用同一 runId”没有明确区分平台 Workflow run、插件 scheduler run，以及回滚后的新执行。
- 现有 rollback 测试把模拟 journal 写在 `<feature>/` 下，未覆盖平台的真实存储与恢复链路。

修复边界包含本仓库和 `CmbCoworkAgent` 平台仓库。插件不直接删除或改写平台的私有 run 数据。

## 1. 持久化 Feature 执行身份

新增 `hooks/execution_lifecycle.py`，统一负责执行身份、状态转换和校验。建议状态保存在：

```text
.autobizdevops/execution/<feature>/state.json
```

该目录位于 Feature 产物目录之外，不随 rollback 产物归档或 history prune 删除。最小字段：

```json
{
  "schemaVersion": "autobiz.execution-lifecycle.v1",
  "scopeId": "<项目与 Feature 对应的持久化唯一身份>",
  "featureId": "<feature>",
  "generation": 1,
  "phase": "active",
  "rollbackId": null
}
```

- `scopeId` 在创建执行域时生成，不能只使用 Feature 名称；项目重建、同名 Feature 或状态丢失不能误匹配旧 run。
- `generation` 表示 Code 执行代次，仅在回滚等使既有执行失效的操作中推进；普通中断、Batch 重试和重新启动平台 Workflow 不改变它。
- `phase` 使用 `active`、`rolling_back`、`rolled_back`、`recovery_required`。只有当前代次处于 `active` 时才能启动或恢复 Code 执行。
- 现有 Code Session `sessionId` 继续负责源码基线。执行代次独立管理，避免恢复源码基线文件时意外恢复旧 run 的有效性。
- 状态缺失、损坏或无法确认关联时返回具体错误，不从旧 Workflow 参数重建当前身份。

launcher 参数、scheduler manifest、TASK run 和必要的派发上下文均保存 `{scopeId, generation}`。`workflowInvocationId` 继续标识一次平台调用；它不能替代执行代次。

## 2. 平台在读取和回放 journal 前校验

平台新增通用的执行域绑定能力，概念字段为 `lifecycleBinding`。绑定由经过校验的插件入口建立，保存执行身份和受限的状态引用；不能只信任模型任意填写的 `args`。平台负责通用身份与代次校验，不硬编码 AutoBiz 阶段或目录约定。

状态引用必须经过工作区/插件授权根校验，采用固定结构的只读读取；不能运行模型提供的校验命令。跨产物工作区的引用也必须显式建立访问范围。

所有启动与恢复路径汇入同一准入层：

1. 读取待恢复 run 的持久化绑定；先校验身份与当前代次，再读取其 journal。普通历史查看仍可读取元数据。
2. 在真正 launch 前再次校验，覆盖用户审批等待期间发生 rollback 的情况。
3. 校验通过后才允许 journal 回放；不能把准入校验做成可缓存的 `agent()` 调用。
4. 新 Agent 派发和带副作用的工具执行也检查当前绑定；失效后停止继续派发。

过期时返回例如：

```text
WORKFLOW_EXECUTION_INVALIDATED
reason=feature_rolled_back
action=rerun_launcher_and_start_new_workflow
```

旧 run 的绑定不可通过更换 `args`、脚本或清空 journal 改成新代次；该操作必须启动新的平台 run。使用旧参数创建一个新平台 run 也必须被准入层拒绝。

平台保存 `invalidatedByRollbackId`、失效原因与恢复资格，保留原来的运行结果和工具流供审计。界面显示“已因回滚失效”，禁用恢复；通知也不再提示继续恢复该 run。后端拒绝仍是最终约束。

## 3. Rollback 先使旧执行失效，再清理

只在清理范围包含 `dev.code` 时执行以下协议。回退到 Code 之后的阶段，不无条件废弃整个 Code 执行。

顺序如下：

1. 在 Feature 生命周期锁下重新校验 rollback 计划，将 `generation` 推进并写入 `rolling_back`、`rollbackId`。旧平台调用、旧 launcher 结果和旧 Runtime 请求从此失效。
2. 通过平台生命周期接口，使所有绑定该执行域旧代次的 run 不再可恢复；取消所有相关聊天中仍在执行的 Workflow、子 Agent 和命令，等待它们停止副作用操作。
3. 确认停止后，执行现有 Worktree/lease 清理、产物归档、Plan 重置和可选源码恢复。
4. 成功后写入 `rolled_back`。下一次正常 Code 入口完成基线和准入准备后，激活当前代次，并创建新的平台 Workflow 和 scheduler run。

需要处理的边界：

- 当前平台 `cancelAndWait()` 有等待超时后返回的语义。rollback 不能据此认定所有执行已经退出；需新增返回明确停止结果的按执行域取消接口。
- 超时、平台不可用且无法确认有无活动执行、清理失败时，返回未完成并保持 `rolling_back` 或 `recovery_required`；不得报告成功并继续删除正在使用的 Worktree。
- 生命周期锁仅用于短事务和登记，等待平台停止时释放锁；重新进入后校验同一 `rollbackId` 和代次，避免死锁或并发回滚串线。
- 平台准入登记与执行域失效登记需要串行化，防止失效操作漏掉同时启动的 run。
- 失效已经生效后，后续失败不能回退 `generation`。可恢复 Plan、状态和文件，但保留恢复阻断；完成恢复后以有效的新代次继续，不能复活旧执行。
- 进程崩溃后，可用同一 `rollbackId` 幂等继续事务。dry-run 不推进代次、不取消执行。
- 正在进行最终推广的特殊状态仍遵守已有恢复限制；不能用执行失效绕过现有 promotion 事务保护。

## 4. 插件 Runtime 拒绝迟到请求

新增统一的执行准入检查，应用于 scheduler 的 create/ensure/resume/manual-resume/dispatch、TASK 启动与完成、lease、阶段结果、Evidence、封存以及集成推广等会改变运行状态或代码的入口。

关键要求：

- 旧代次请求必须在产生文件、Git 或源码副作用之前失败。
- 校验与对应写入使用同一套生命周期并发协议，避免“校验通过后刚好发生 rollback”的竞态；锁顺序需要固定。
- 特别检查 `run_lock`、`append_event` 等会创建目录的函数：迟到回调不能先重建 `.parallel-runs/<旧runId>/` 再发现失效。
- 文件写入完成、外部命令退出、Agent 完成等异步结果登记时再次校验。失效结果不能进入新代次的 Plan、Evidence 或 manifest。
- 清理和事务恢复使用独立、受限的维护入口，可操作已失效代次，不重新赋予其执行资格。
- 更新 TASK 完整性字段及相关 schema，避免新增代次字段未被完整性检查覆盖。

## 5. 明确启动、恢复和历史兼容规则

| 场景 | 平台 Workflow | 插件 scheduler |
| --- | --- | --- |
| 同一代次，平台中断且允许恢复 | 准入通过后可恢复原平台 run | 保持原 scheduler run |
| 同一代次，平台已经返回终态，scheduler 仍有可恢复任务 | 启动新的平台 run，不带 `resumeFromRunId` | 复用有效的 scheduler run |
| Code 或更早阶段 rollback 成功后重新执行 | 新平台 run，使用本次 launcher 的完整参数 | 新 scheduler run |
| 历史 run 已失效 | 允许查看，拒绝恢复 | 禁止重新调度或登记结果 |

修改 Code 技能、batch workflow guide 和 rollback 技能，明确上述规则。launcher 返回独立的 `schedulerRunId`、执行身份和启动策略，避免用含义不明的 `runId` 推断平台恢复目标。

历史兼容不采用“给旧 run 补上当前 generation”的方式：

- 可由保存的来源与规范化项目/Feature 绑定确认属于 AutoBiz 的历史 run，迁移其恢复资格；无法证明仍属于当前执行代次时，禁止 journal 恢复。
- 对已有有效 scheduler，只有能可靠建立执行归属时才迁移。然后启动新平台 Workflow，重新读取当前 scheduler 状态；归属不明时报告具体迁移阻断，不静默丢弃工作成果。
- 未绑定 AutoBiz 执行域的其他 Workflow 保持现有行为；不按名称或时间猜测归属并批量删除。
- 插件/平台能力不匹配时明确阻断受保护的启动或 rollback，不静默退化为只靠提示词。

## 6. 实施位置

| 仓库 | 文件或模块 | 改动 |
| --- | --- | --- |
| 插件 | 新增 `hooks/execution_lifecycle.py` | 执行域身份、代次、短事务锁、准入及恢复协议 |
| 插件 | `hooks/rollback_stage.py` | 先失效与停止执行，再做现有回滚事务；支持失败和崩溃恢复 |
| 插件 | `hooks/workflow_launcher.py` | 输出执行绑定、明确的 scheduler 身份和启动策略 |
| 插件 | `hooks/parallel_runtime.py`、`hooks/parallel_batch_scheduler.py` | 持久化代次、ensure/resume 准入及禁止旧目录重建 |
| 插件 | TASK、lease、Evidence、stage、Worktree、集成与相关 guard | 统一校验副作用入口和迟到结果 |
| 插件 | Code/rollback 技能与执行指南 | 统一恢复规则，修正平台存储目录描述 |
| 平台 | 新增 Workflow 生命周期服务 | 受校验绑定、执行域失效登记、跨聊天取消与明确停止结果 |
| 平台 | `workflow/tool.ts`、`run-manager.ts`、`engine.ts`、工具副作用入口 | 缓存前准入、launch 前复核、派发和工具执行检查 |
| 平台 | `run-store.ts`、类型、UI、通知与 native launch | 持久化失效信息，所有恢复路径共享约束 |

## 7. 验收标准与落地顺序

必须覆盖以下有实际行为断言的测试：

1. 正常中断恢复：同一代次保留 scheduler 进度；平台终态后重新启动也能继续。
2. 完整复现：创建平台 run 并缓存准备结果 → rollback → 仅传旧 `resumeFromRunId`，在 journal 回放前被拒绝。
3. 修改参数后恢复旧平台 run、使用旧 launcher 参数新建平台 run，均无法绕过代次校验。
4. 真正处于应用管理目录、旧 threadId 目录或其他 Workflow host 的历史 run，均按绑定失效；其他 Feature 不受影响。
5. 多聊天启动与 rollback 并发、旧 lease、迟到 TASK/Evidence/封存/推广登记，均不能产生新副作用或重建旧目录。
6. 取消超时、外部命令尚未退出、事务中途异常、进程重启后续做，不能误报 rollback 成功或恢复旧代次。
7. dry-run 和只回退 Code 之后阶段，不误伤有效 Code 执行。
8. 历史 run 缺绑定、绑定损坏、Feature 同名重建、history prune，不会使旧 run 获得当前执行资格。

建议作为一套完整能力交付：先实现执行生命周期和平台准入，再接入 rollback/Runtime 与历史迁移，最后统一技能、UI 和通知。插件与平台先做能力握手，避免发布期间出现一侧启用了新协议、另一侧仍接受旧恢复的空窗。

验收终点：rollback 成功后，旧 run 可查阅但不可执行；下一次 Code 从当前 Plan 启动新执行；普通中断与合法重试仍可保留进度。
