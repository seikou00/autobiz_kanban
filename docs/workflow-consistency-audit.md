# 工作流新旧契约一致性检查

检查日期：2026-10-19。范围为当前本地仓库；没有修改 Windows 上已安装的插件或正在运行的 Workflow。

## 已修正的不一致

| 位置 | 遗漏及影响 | 修正 |
| --- | --- | --- |
| Workflow 恢复状态 | 失败隔离只按 Batch ID 记录；旧结果或旧快照可能重新挡住已恢复的新代次 | 隔离绑定 generation，拒绝旧快照；普通 status 也能释放旧代次隔离 |
| 恢复与最终修复调用方 | `applySchedulerState` 已返回被接受的快照，但调用方仍可能继续使用原对象 | 两个调用方均接收返回值，避免继续消费已拒绝的旧状态 |
| 派发返回 schema | `claim/defer` 重放会返回 `reused`，严格 schema 未声明；CLI 错误又缺少 schema 要求的身份字段 | 声明重放字段；CLI 错误保留 Batch、generation 和真实 reasonCode |
| 控制命令失败返回 | scheduler、Review 验证、UTest 准备与单次修复登记可能只返回 `{ok:false,error}`；旧 schema 强制要求成功字段 | 允许原样传递真实失败；成功快照、Batch 绑定和修复 Evidence 继续由消费端严格校验 |
| Worktree 返回 | provision 失败缺少成功路径的 Batch/repository 字段，且错误诊断可能带 readiness | schema 接收真实失败；成功时额外校验 Batch/repository 绑定和实际路径 |
| 部署版本指纹 | 旧清单未包含派发、恢复、集成、TASK context 等入口及其依赖；部分升级可能未被发现 | 覆盖全部固定 Workflow Python 入口，并递归追踪本地 Python 导入；缺文件或语法损坏时拒绝通过 |
| Code 技能活动 TASK 边界 | 文档仍称同一 Feature 只能有一个活动 TASK，与按 Batch 并行的 Runtime 相冲突 | 区分非并行 Feature 与 `parallelRunId + batchId`；说明旧 run 已 aborted 后，未完成 TASK 可以重新 start |
| Specs 模板引用 | spec 模板路径重复了一层 `templates/` | 修正路径，并加入技能显式路径存在性检查 |
| 预检修复注册表 | 当前在用校验器新增的三个 JSON/pipeline reason 没有对应修复指引 | 补齐修复指引；覆盖测试从 board_config 的实际 validator 注册表推导，不再检查已退出门禁的旧 validator |
| 提问共享协议 | 共享文件只剩候选数量说明，缺少工具字段、回答吸收与未决项消解规则；Code 启动前看板 ID 提问没有引用协议 | 补齐共享规则；启动前提问使用共享协议，启动后的自主执行边界保持不询问 |
| 旧测试契约 | 仍按裸 python、旧 `extra_missing_inputs` 参数、旧输入展示、旧 lean 下线状态或重复技能文案断言 | 对齐当前 UTF-8 命令、参数与渲染接口；保留实际权限、路径、顺序、必需字段和回答处理检查 |
| 测试发现 | tests 目录不是可导入包，标准 unittest discover 命令无法运行 | 补齐测试包入口，让根目录标准发现覆盖集成与单元测试 |

空响应专用重试和旧快照降级派发已移除，模型传输兜底交给平台。正常 Batch 失败仍走调度器的有限重试协议。

## 仍需统一的产品契约：lean 路线

`board_core/board_config.json` 当前公开并允许新建 `lean`：

```text
dev.specs → dev.code → ops.archive
```

其说明明确声称不包含 design/PLAN、Code 直接从 proposal/specs 推导任务。但是 `workflow_launcher.py`、Code 技能与 TASK Runtime 仍要求 Plan 和固定 Batch DAG，没有对应的无 Plan 执行入口。

实际复现：新建合法 lean Feature，准备 proposal 后调用当前 launcher，返回：

```json
{
  "strategy": "blocked",
  "reason": "plan_not_found",
  "requiresPlanRepair": true,
  "requiredAction": "repair_plan"
}
```

这属于可执行流程与公开模板承诺不一致。不能靠放宽 Plan 校验或绕过固定 Workflow 修复。需要在“保留无 Plan 路线并实现完整接入”和“统一要求 Plan 并调整模板”之间明确选择。本轮保留现行模板与旧 Feature 兼容行为，未擅自改变路线。

## 验证

- 已跟踪的 234 个 Python 文件与 9 个 JSON 文件语法检查通过；技能、Agent 中的显式插件脚本和参考路径均存在。
- 真实 Runtime 输出与 Workflow schema 的交叉检查覆盖首次派发、重放、失败收口、恢复、状态视图、Worktree 和控制命令错误。
- 部署检查覆盖 90 个实际入口及依赖文件，并验证新导入、缺失依赖、损坏 Python 文件及依赖更新都会影响检查结果。
- `python3 -m unittest discover -s tests -t .`：1,310 项测试，1,309 项通过，1 项因本机文件系统不区分大小写而跳过，无失败或错误。
- `node --test tests/test_workflow_*.mjs`：70 项全部通过。
- `python3 tests/test_code_workflow_integration.py`：11 组全部通过。
- 另行执行未由 unittest 发现的 3 个独立函数测试，全部通过。
- `git diff --check` 通过。首轮全量检查发现的 17 项断言失败与 5 项错误均已处理；增加了 10 项真实 Runtime/schema/部署一致性回归。

## 生效范围

这些修正位于本地仓库。更新插件时需要同步部署完整版本，并通过 launcher 继续同一 run；已启动的旧 Workflow 不会自动换成新脚本。若 Batch 已进入 `retry_exhausted/blocked`，仍需 `manual-resume` 重新开放本轮重试预算。
