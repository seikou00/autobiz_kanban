# Plan Implementation Scope Switch

## 背景

同一个需求可能同时包含前端和后端开发内容，但实际执行时用户可能只希望本轮生成前端任务、只生成后端任务，或全量生成任务。

当前 `UI_CONTEXT.json` 只表达“需求是否存在 UI 范围”，不能表达“本轮计划只做前端/后端”。如果为了“只做后端”把 `UI_CONTEXT.uiRequired` 改成 `false`，会污染需求事实源，也会影响后续 UI 验收与回归。

因此需要在 Plan 阶段增加一个独立的实现范围开关，让 `plan.json` 只生成本轮需要执行的任务。

## 目标

- 支持用户在 Plan 阶段选择：
  - 全量实现
  - 仅前端
  - 仅后端
- `plan.json` 只包含所选范围内的任务。
- 被排除的 SCN/API/DATA/Decision 不再触发覆盖缺失。
- 不修改 `UI_CONTEXT.json` 的 UI 事实含义。
- 不复用 `workflowDecisions`，避免把执行范围与工作流阶段编排混在一起。

## 非目标

- 不新增独立前端/后端 workflow 节点。
- 不改变 specs/design 的需求事实。
- 不绕过现有任务粒度规则。
- 不允许通过该开关隐藏未知或未确认的需求冲突。

## 推荐数据模型

在 `plan.json` 顶层新增：

```json
{
  "implementationScopeVersion": 1,
  "implementationScope": {
    "mode": "full",
    "source": "user_confirmed",
    "reason": "本轮按用户选择执行",
    "include": ["frontend", "backend"],
    "excludedScenarioRefs": [],
    "excludedApiIds": [],
    "excludedDataIds": [],
    "excludedDecisionIds": []
  }
}
```

`mode` 可选值：

| mode | 含义 | include |
| --- | --- | --- |
| `full` | 全量实现 | `["frontend", "backend"]` |
| `frontend` | 仅前端实现 | `["frontend"]` |
| `backend` | 仅后端实现 | `["backend"]` |

每个 task 新增：

```json
{
  "workstream": "frontend"
}
```

`workstream` 可选值：

| workstream | 含义 |
| --- | --- |
| `frontend` | 前端任务 |
| `backend` | 后端任务 |
| `fullstack` | 前后端强耦合任务，仅 `mode=full` 时允许 |

建议默认避免 `fullstack`，优先拆成前端任务和后端任务。

## Plan 阶段交互规则

进入 Plan 生成前必须确认实现范围：

1. 全量实现（默认）
2. 仅前端
3. 仅后端

如果用户已明确表达“只做前端”或“只做后端”，不得重复追问，直接写入：

```json
{
  "mode": "frontend",
  "source": "user_confirmed"
}
```

如果用户未明确选择，默认：

```json
{
  "mode": "full",
  "source": "default_full"
}
```

## 任务生成规则

### full

- 生成所有前端、后端、必要 fullstack 任务。
- `excludedScenarioRefs` / `excludedApiIds` / `excludedDataIds` / `excludedDecisionIds` 必须为空。
- 继续执行现有任务粒度门禁。

### frontend

- 只生成 `workstream=frontend` 的任务。
- 不生成后端实现任务。
- 后端 API 可以作为契约、mock 或联调依赖出现在 `scope.entrypoints` / `nonGoals` / `implementationPoints` 中，但不得生成后端代码任务。
- 后端场景/API/DATA/Decision 写入 excluded 列表。

### backend

- 只生成 `workstream=backend` 的任务。
- 不生成 UI task，不写非空 `uiRefs`。
- UI 场景写入 `excludedScenarioRefs`。
- UI_CONTEXT 仍保持原始 UI 事实，不因为只做后端而改成 `uiRequired=false`。

## Writer 改造

扩展 `plan_writer.py`。

### init

```bash
python hooks/plan_writer.py init --feature test2 --implementation-mode frontend
```

行为：

- 写入 `implementationScopeVersion: 1`
- 写入 `implementationScope.mode`
- 按 mode 自动生成 `include`
- 初始 excluded 列表为空

### set-implementation-scope

```bash
python hooks/plan_writer.py set-implementation-scope \
  --feature test2 \
  --mode backend \
  --reason "本轮只做后端"
```

行为：

- 如果 `tasks` 非空，拒绝切换：

```text
implementation_scope_change_requires_replan
```

- 需要切换时必须重新生成计划，使用 `init --force`。

### excluded refs

```bash
python hooks/plan_writer.py add-excluded-scenario-ref specs/cap/spec.md#SCN-001
python hooks/plan_writer.py add-excluded-api-id API-001
python hooks/plan_writer.py add-excluded-data-id DATA-001
python hooks/plan_writer.py add-excluded-decision-id D-001
```

### task workstream

`add-task` 增加：

```bash
--workstream frontend
```

`--body-stdin` / `--body-file` / `--task-json` 也必须包含 `workstream`。复杂 task 优先用 `--body-stdin`，避免为每个 task 落盘中间 JSON 文件。

## Validator 改造

新增 validator：`plan_implementation_scope`，挂载到 `dev.plan`，建议放在 `plan_json_contract` 之后、`plan_scenario_coverage` 之前。

校验规则：

- `implementationScopeVersion` 必须为 `1`。
- `implementationScope.mode` 必须是 `full/frontend/backend`。
- `implementationScope.include` 必须与 mode 对齐。
- 每个 task 必须有合法 `workstream`。
- `mode=frontend` 时，只允许 `workstream=frontend`。
- `mode=backend` 时，只允许 `workstream=backend`。
- `mode=full` 时允许 `frontend/backend/fullstack`，但 excluded 列表必须为空。
- excluded SCN/API/DATA/Decision 必须真实存在。
- excluded SCN 必须使用完整路径，例如 `specs/cap/spec.md#SCN-001`。
- `backend` 模式下 task 不得带非空 `uiRefs`。

## 现有覆盖校验调整

### plan_scenario_coverage

当前分母是 specs 中所有 SCN。

调整为：

```text
expected_refs = all_spec_scenarios - implementationScope.excludedScenarioRefs
```

### plan_json_traceability

当前要求 design.md 中所有 API/DATA 都被任务覆盖。

调整为：

```text
required_api_ids = design_api_ids - excludedApiIds
required_data_ids = design_data_ids - excludedDataIds
```

Decision 覆盖如果后续也做全量门禁，则同样排除 `excludedDecisionIds`。

## Skill 文档改造

`autodev-plan/SKILL.md` 增加规则：

- Plan 生成前必须确定 implementation scope。
- 不得通过修改 `UI_CONTEXT.uiRequired=false` 表达“只做后端”。
- `frontend` 模式下只生成前端任务，后端 API 只能作为契约或 mock 依赖。
- `backend` 模式下只生成后端任务，不生成 UI task。
- `full` 模式仍受现有任务粒度门禁约束。
- 被排除的 SCN/API/DATA/Decision 必须写入 `implementationScope.excluded*`，不得静默遗漏。

## 对任务粒度的影响

该开关只负责缩小任务分母，不替代任务粒度规则。

执行顺序：

1. 按 `implementationScope.mode` 确定本轮任务范围。
2. 只为范围内的 SCN/API/DATA/Decision 生成任务。
3. 再执行现有粒度门禁：
   - `SCN > 5` 需要合格 `splitRationale`
   - `SCN > 8` 必须拆
   - `apiIds > 3` 必须拆
   - `pageRefs > 2` 必须拆
   - `interactionRefs > 4` 必须拆

这样“仅前端”不会被后端 API/Data 拉大任务，“仅后端”也不会生成 UI 任务。

## 测试清单

- `frontend` 模式接受 `workstream=frontend` task。
- `frontend` 模式拒绝 `workstream=backend/fullstack` task。
- `backend` 模式接受 `workstream=backend` task。
- `backend` 模式拒绝 `workstream=frontend/fullstack` task。
- `backend` 模式拒绝非空 `uiRefs`。
- `full` 模式允许三类 workstream。
- `full` 模式拒绝非空 excluded 列表。
- `frontend` 模式下 excluded 后端 SCN 不触发 `missing_plan_scenario_coverage`。
- `backend` 模式下 excluded UI SCN 不触发 `missing_plan_scenario_coverage`。
- excluded API/DATA 不触发 design coverage 缺失。
- unknown excluded SCN/API/DATA/Decision 会失败。
- `plan_writer init --implementation-mode frontend` 正确写入 scope。
- 已有 tasks 时 `set-implementation-scope` 被拒绝。

## 推荐落地顺序

1. 扩展 `plan.json` schema：`implementationScopeVersion`、`implementationScope`、`tasks[].workstream`。
2. 扩展 `plan_writer.py`：`--implementation-mode`、`set-implementation-scope`、excluded refs、`--workstream`。
3. 新增 `plan_implementation_scope` validator。
4. 调整 `plan_scenario_coverage` 分母。
5. 调整 design API/DATA 覆盖校验。
6. 更新 `autodev-plan/SKILL.md`。
7. 更新 `templates/plan.json`。
8. 补测试。
9. 用一个包含前后端的 fixture 跑三种模式回归。
