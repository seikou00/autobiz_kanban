# Code Workflow 提交说明

插件生成的提交统一使用 `<看板ID> #comment cmbdevcalw提交 <说明>`。看板 ID 继续从当前 Run 的 manifest 读取。

| 提交阶段 | 首行 | 正文 |
| --- | --- | --- |
| Code | `Code：<本次实现的功能>` | 当前 Batch 的 TASK 编号、标题和任务目标 |
| Rework | `Rework：<本次修复的问题>` | 无 TASK |
| UTest | `UTest：<本次新增或修复的测试>` | 无 TASK |

```text
Z990692-294 #comment cmbdevcalw提交 Code：实现订单查询与状态筛选

TASK T001：订单列表查询
任务目标：支持分页查询订单，展示订单编号、金额和状态。

TASK T002：订单状态筛选
任务目标：支持按订单状态筛选，返回符合条件的订单。
```

```text
Z990692-294 #comment cmbdevcalw提交 Rework：修复订单状态筛选失效
Z990692-294 #comment cmbdevcalw提交 UTest：补充订单分页与状态筛选测试
```

Code 正文由插件依据 manifest 中当前 Batch 的 `taskIds` 读取 Plan，保持任务目标原文和换行。正文不包含实现要点、验收要求、关联需求、Feature 或 Batch 等额外内容。提交前校验计划一致性和任务绑定；缺少详情时明确报错。

`worktree_manager.py seal` 支持 `--commit-stage code|rework|utest` 和 `--commit-summary "<实际变更的中文说明>"`。Workflow 显式传参，插件核对持久化阶段状态。摘要占位符必须替换为实际说明，不把失败日志或命令整段复制进摘要。

`--purpose review` 仅表示封存生产代码供 Review 使用，首次实现仍是 Code，返修是 Rework。Review 只读审查，不创建额外提交；生产问题交回 Rework。普通中断恢复继续使用 Code，不按尝试次数判断为返修。

UTest 成功或失败时封存测试资产均使用 UTest 格式。生产修复必须回到 Rework；摘要描述测试变更，不自动宣称测试通过。没有代码变更时复用现有 commitSha，不产生空提交。

旧调用省略提交阶段和摘要时，插件根据已有阶段状态和 Review/UTest 的实现返修记录确定格式，并提供默认摘要。初始化、合并、冲突解决和回滚继续使用原操作说明，统一加入 `cmbdevcalw提交`，不带 TASK。已有 Git 历史不改写。
