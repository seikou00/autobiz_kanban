# Plan 阶段阻断规则治理登记

## 为什么要这份表

两次 `/autodev-plan` 会话里，`plan_writer.py` 被调用 91 / 122 次，约九成消息花在门禁循环上，其中一次没能走到 `plan_done`。根因不是规则太多，而是有规则自相矛盾、前提不成立，以及大量规则拦的是"写法"而非"契约"。

治理约定：**一条规则要以 blocker 身份存在，必须能回答四个问题**——判定模块、放行后哪个下游会失败、哪条回归测试证明了这个失败、规则码是什么。举不出下游失败的，登记为待降级。

登记表在 `hooks/blocker_registry.py`，由 `tests/test_blocker_registry.py` 强制执行：在受治理模块里新增一条 blocker 而不登记，测试直接失败。

与 `skills/autodev/hooks/repair_registry.py` 分工不同——那份回答"怎么修"，这份回答"凭什么拦"。

## 当前受治理范围

`hooks/plan_granularity.py`（任务粒度门禁）。其余模块逐批纳入，扩围只需往 `GOVERNED_MODULES` 加路径，未登记的规则会立刻暴露。

## 已举证的阻断规则

| 规则码 | 下游失败 | 回归测试 |
|---|---|---|
| `invalid_plan_task_scenario_reference` | 覆盖门禁按 `path#SCN-NNN` 精确比对。一条 `#SCN-001~SCN-003` 区间引用会被计成**两个**已覆盖场景，于是覆盖检查对没人实现的场景放行 | `test_range_reference_creates_false_scenario_coverage` |
| `invalid_plan_task_matrix_validation` | 任务契约要求每条 acceptanceCriteria 都被某条 required 命令覆盖，漏覆盖时下游报 `acceptanceCriteria_uncovered` | `test_plan_requires_required_commands_to_cover_every_acceptance_criterion` |

区间引用那条是实测的：三个场景的 spec，一条区间引用让 SCN-001 和 SCN-003 同时算作已覆盖，只剩 SCN-002 报缺失。这不是理论风险。

## 举不出下游失败的阻断规则

| 规则码 | 现状 | 举证 |
|---|---|---|
| `oversized_plan_task_must_split` | 保留为 blocker | `test_hard_caps_have_no_downstream_contract_behind_them` |

四条粒度硬上限（scenarios>12 / apis>3 / pages>2 / interactions>4）**背后没有任何下游契约**。实测：13 个场景、4 个 API 的任务，`validate_task_collection` 返回 `[]`，下游完全接受；唯一拒绝它的就是粒度门禁自己。

在 `plan_json` / `task_runner` / `utest_plan_contract` 中与任务粒度相关的下游限制只有 `MAX_BATCH_TASKS = 5` 和 `implementationPoints > 6`，都与这四个维度无关。

按治理约定，这条是降级候选。暂时保留，因为它承载的是规划偏好（避免任务过粗），降级需要先确认这个偏好靠 warning 是否还成立。

## `invalid_plan_task_matrix_validation` 的半条未举证

该规则包含两个子句：

1. **每条 AC 都被 required 命令覆盖** —— 有下游契约，已举证
2. **恰好一条 required behavior 命令** —— 没有下游依据

第 2 条的"恰好一条"是矩阵例外的表达约定，不是下游消费方的要求。下次收敛可以把它拆成独立规则并降级，保留第 1 条为 blocker。

## 已完成的降级

以下规则在本轮降为 warning，不再阻断：

- `missing_plan_task_split_rationale` / `invalid_plan_task_split_rationale`（长度、固定短语白名单、禁用措辞、重复点名 ID）
- `missing_plan_task_merged_scenario_refs` / `invalid_plan_task_merged_scenario_refs`
- `backend_task_after_frontend`（任务级）

注意：车道顺序在**批次级**仍然阻断（`backend_batch_after_frontend`）。批次执行顺序目前由数组位置线性推导（`plan_writer.py` 的 batch 串联，`add-task-contract` 对外公布 `executionOrder: root_batch_order_then_task_order`），在改成依赖 DAG 推导之前不能降级，否则会产出批次链与已声明执行顺序矛盾的 plan。**对模型来说交叉排布车道仍然被拦**，本轮去掉的只是任务级那条重复抱怨。

## 下一步

1. 把 `GOVERNED_MODULES` 扩到 `plan_writer.py` / `plan_json.py` / `artifact_ref_validator.py`，按同一判据逐条举证或登记待降级
2. 拆分 `invalid_plan_task_matrix_validation`，降级"恰好一条"子句
3. 批次顺序改为依赖 DAG 推导后，降级 `backend_batch_after_frontend`
4. 依据举证结论决定四条粒度硬上限是否降为 warning
