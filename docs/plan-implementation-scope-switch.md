# Implementation Scope Switch 优化方案

## 背景

同一个需求经常同时包含前端页面、交互、后端接口、数据和定时任务等内容，但实际执行时用户可能只希望当前 Feature 先做后端，或只做前端。如果只在 Plan 阶段过滤任务，会出现几个问题：

- PRD/specs 仍然生成全量前后端行为，后续 `plan_scenario_coverage` 会把被剥离的场景重新拉回来。
- 如果到 plan/code 阶段才手工把 `UI_CONTEXT.json` 改成非 UI，会污染上游事实源并造成阶段漂移。
- Code 阶段的前端 route、后端 compile guard、review/e2e/verify 门禁仍按全量实现理解。

因此实现范围开关必须从 discuss 阶段开始生效，并贯穿 PRD、specs、plan、code、validator 和 guard。

## 目标

- 从 discuss 阶段确定当前 Feature 的实现范围：全量、只后端、只前端。
- 被剥离的范围不丢失，沉淀到剥离清单，供后续 Feature 继续使用。
- specs 的场景分母只包含当前实现范围内的行为，避免 Plan 阶段反复补无关场景。
- plan.json 只生成当前范围内的任务。
- code 阶段只进入当前范围需要的 route、验证和 compile guard。
- board 节点不拆，validator / guard 通过 `implementationScope` 条件化执行。

## 非目标

- 不复制三套 board 流程。
- 不允许到 plan/code 阶段手工篡改 `UI_CONTEXT.uiRequired=false` 来伪装“只做后端”；`UI_CONTEXT` 必须由 discuss/specs 阶段的 scope 契约驱动生成。
- 不删除被剥离需求，只把它移出当前 Feature 的实现范围。
- 不绕过任务粒度、traceability、scenario coverage 等现有门禁。
- 第一版不强制解决所有 frontend_only 的后端 mock / 联调问题，可分阶段落地。

## Scope 契约

在 `state.json.features[feature].workflowDecisions` 增加：

```json
{
  "implementationScope": "full_stack"
}
```

可选值：

| 值 | 含义 |
| --- | --- |
| `full_stack` | 当前 Feature 前端 + 后端都做 |
| `backend_only` | 当前 Feature 只做后端，前端剥离 |
| `frontend_only` | 当前 Feature 只做前端，后端剥离 |

默认值为 `full_stack`，保持老 Feature 兼容。

同时建议生成 feature 级产物：

```text
.autobizdevops/features/<feature>/IMPLEMENTATION_SCOPE.json
```

示例：

```json
{
  "version": 1,
  "featureId": "test2",
  "implementationScope": "backend_only",
  "selectedAtCheckpoint": "discuss_in_progress",
  "source": "user_confirmed",
  "reason": "本轮只实现后端能力，前端另行排期",
  "splitArtifacts": {
    "frontend": "SCOPE_SPLIT.md"
  }
}
```

## 剥离清单

新增：

```text
SCOPE_SPLIT.md
```

用途是保留当前 Feature 不做、但需求仍然存在的内容。

backend_only 示例：

```md
# Scope Split

## 当前实现范围

backend_only

## 本轮保留

- API 行为
- 数据模型
- 权限校验
- 后端状态流
- 后端自动化验证

## 剥离到后续

- 页面布局
- 前端交互
- HTML/设计稿还原
- 前端路由与回检
```

frontend_only 示例：

```md
# Scope Split

## 当前实现范围

frontend_only

## 本轮保留

- 页面结构
- 前端交互
- 前端状态展示
- 前端校验和回检

## 剥离到后续

- 后端 API 实现
- 数据表/迁移
- 后端权限与审计逻辑
```

## 阶段行为

### Discuss 阶段

`autobiz-requirement-discuss` 进入时，如果没有 `implementationScope`，先确认实现范围：

1. `full_stack`：全量实现
2. `backend_only`：本轮只做后端
3. `frontend_only`：本轮只做前端

如果用户已经明确表达“只做后端”或“只做前端”，不要重复追问，直接写入 scope。

需要写入：

- `state.json.features[feature].workflowDecisions.implementationScope`
- `IMPLEMENTATION_SCOPE.json`
- `PRD_DISCUSS.md` 的“实现范围”章节
- `SCOPE_SPLIT.md`

### PRD 阶段

`autobiz-prd-generate` 读取 `implementationScope`。

| Scope | PRD 行为 |
| --- | --- |
| `full_stack` | 现有逻辑不变 |
| `backend_only` | PRD 只保留后端可交付内容；前端描述进入 `SCOPE_SPLIT.md` |
| `frontend_only` | PRD 只保留页面、交互、展示、前端校验；后端作为外部依赖 |

PRD 必须包含：

```md
## 当前实现范围

backend_only / frontend_only / full_stack
```

### Specs 阶段

scope 必须在 specs 阶段生效，因为 specs 是后续 scenario coverage 的分母。

#### backend_only

只生成后端可实现、可验证的场景：

- API
- 数据
- 权限
- 状态流
- 定时任务
- 后端异常分支

不生成 UI 页面、交互、视觉、前端路由场景。

`UI_CONTEXT.json` 固定写成非 UI：

```json
{
  "version": 1,
  "featureId": "test2",
  "uiRequired": false,
  "decisionStatus": "locked",
  "decisionSource": "implementation_scope",
  "confirmedAtCheckpoint": "prd_done",
  "lockedAtCheckpoint": "specs_done",
  "notApplicableReason": "frontend_split_by_implementationScope",
  "pages": [],
  "interactions": [],
  "visualSources": [],
  "capabilities": []
}
```

#### frontend_only

只生成 UI/前端行为场景：

- 页面状态
- 前端交互
- 前端校验
- 前端展示规则
- 前端错误态/空态/加载态

后端 API 作为外部依赖或 mock seam，不生成后端实现 Scenario。

### Design 阶段

`design.md` 需要区分“本期实现”和“外部依赖”。

API Decisions 示例：

```md
| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |
|----|--------|--------------|---------|----------|--------|-------------------|--------|
| API-001 | POST | /v1/foo/list | FooReq | FooRsp | 参数错误 | 登录态 | 本期实现 |
| API-002 | POST | /v1/bar/query | BarReq | BarRsp | 服务异常 | 登录态 | 外部依赖 |
```

规则：

- `本期实现`：必须由 plan task 的 `apiIds` 覆盖。
- `外部依赖`：可由前端任务的 `consumedApiIds` 覆盖，不要求后端实现。
- `frontend_only` 下通常应写 `x-auto-no-sql: true`，避免生成 SQL/Data 实现决策。

### Plan 阶段

`plan.json` 顶层增加：

```json
{
  "implementationScope": "backend_only"
}
```

每个 task 增加：

```json
{
  "implementationLayer": "backend"
}
```

可选值：

| implementationLayer | 含义 |
| --- | --- |
| `backend` | 后端实现任务 |
| `frontend` | 前端实现任务 |
| `full_stack` | 前后端强耦合任务，仅 `full_stack` 允许 |
| `non_code` | 配置、删除任务、文档同步等非代码层任务 |

API 字段建议拆分：

```json
{
  "apiIds": ["API-001"],
  "consumedApiIds": []
}
```

语义：

- `apiIds`：本任务要实现的 API。
- `consumedApiIds`：本任务只消费、mock 或对接的外部 API。

frontend_only 下：

```json
{
  "apiIds": [],
  "consumedApiIds": ["API-001"]
}
```

#### 允许矩阵

| Scope | 允许的 implementationLayer |
| --- | --- |
| `full_stack` | `backend` / `frontend` / `full_stack` / `non_code` |
| `backend_only` | `backend` / `non_code` |
| `frontend_only` | `frontend` / `non_code` |

#### backend_only 规则

必须满足：

- `uiRequired=false`
- `uiRefs` 不存在或为空
- `scope.pages=[]`
- `implementationLayer=backend` 或 `non_code`

禁止：

- `uiRequired=true`
- `PAGE/UIX/VIS` refs
- 前端 route
- 前端回检任务

#### frontend_only 规则

必须满足：

- `implementationLayer=frontend` 或 `non_code`
- 后端 API 只允许作为 `consumedApiIds`

禁止：

- `dataIds` 非空
- `apiIds` 非空
- `implementationLayer=backend/full_stack`
- SQL / Mapper / Service 实现任务

### Code 阶段

`autodev-code` 读取 `implementationScope`。

#### backend_only

- 不进入 HTML route。
- 不生成 `FRONTEND_ROUTE.json`。
- 不跑前端 `review_runner`。
- 只执行 `backend` / `non_code` task。
- 强验证以后端 compile/test/API smoke 为主。

#### frontend_only

- 进入前端 route。
- 不实现后端 API。
- 后端 API 通过 `consumedApiIds` / mock seam 表示。
- 强验证以前端 lint/build/UI test/route smoke 为主。

#### full_stack

保持现有逻辑。

## Board 与门禁

不建议拆 board 节点。保持现有流程：

```text
discuss -> prd -> specs -> plan -> code -> review/utest/e2e/verify
```

新增 validator：

```text
implementation_scope_contract
```

建议挂载到：

- `dev.specs`
- `dev.plan`
- `dev.code`

可选挂到：

- `dev.review`
- `dev.utest`
- `dev.e2e`
- `dev.verify`

### implementation_scope_contract

读取：

```json
workflowDecisions.implementationScope
```

校验：

- scope 缺失时默认 `full_stack`。
- scope 必须是 `full_stack/backend_only/frontend_only`。
- `IMPLEMENTATION_SCOPE.json` 与 state 中的 scope 一致。
- `backend_only`：
  - `UI_CONTEXT.uiRequired=false`
  - `UI_CONTEXT.notApplicableReason` 包含 `frontend_split_by_implementationScope`
  - `plan.json.tasks[]` 不允许 `uiRequired=true`
  - 不允许非空 `uiRefs`
  - 不允许 `implementationLayer=frontend/full_stack`
- `frontend_only`：
  - `UI_CONTEXT.uiRequired=true`
  - `plan.json.tasks[]` 不允许 `implementationLayer=backend/full_stack`
  - 不允许 `dataIds` 非空
  - 不允许 `apiIds` 非空
  - 允许 `consumedApiIds`
- `full_stack`：
  - 保持现有逻辑。

### 现有 validator 调整

#### ui_context_json

`backend_only` 时允许并要求：

```json
{
  "uiRequired": false,
  "notApplicableReason": "frontend_split_by_implementationScope"
}
```

#### plan_json_contract

需要识别：

- `implementationScope`
- `implementationLayer`
- `consumedApiIds`
- design API Status = `本期实现` / `外部依赖`

规则：

- `本期实现` API 必须由 `apiIds` 覆盖。
- `外部依赖` API 可由 `consumedApiIds` 覆盖，不要求 `apiIds` 覆盖。

#### plan_scenario_coverage

如果 specs 已经按 scope 生成，通常不需要排除列表。

如果为了兼容旧 specs 需要保留全量 specs，则需要 `excludedScenarioRefs`：

```text
expected_refs = all_spec_scenarios - excludedScenarioRefs
```

推荐第一版不走排除列表，而是在 specs 阶段直接生成 scope-specific specs。

## Guard 改造

当前 `dev.code` 有：

```json
"guards": ["code_compile"]
```

建议改为：

```json
"guards": ["scope_compile"]
```

`scope_compile` 行为：

| Scope | 编译/验证策略 |
| --- | --- |
| `backend_only` | 后端 compile/test |
| `frontend_only` | 前端 build/lint/test |
| `full_stack` | 后端 + 前端，或按 plan 涉及模块选择 |

若不想改 guard 名，也可以让 `code_compile` scope-aware，但长期语义不如 `scope_compile` 清晰。

## Writer 设计

新增：

```text
hooks/implementation_scope_writer.py
```

命令：

```bash
python hooks/implementation_scope_writer.py set \
  --feature test2 \
  --scope backend_only \
  --reason "本轮只做后端"
```

职责：

- 写 `IMPLEMENTATION_SCOPE.json`
- 写或更新 `SCOPE_SPLIT.md`
- backend_only 时生成或重置非 UI 的 `UI_CONTEXT.json`
- 写 `state.json.features[feature].workflowDecisions.implementationScope`
- 如果当前 checkpoint 已超过 specs，提示必须回流 specs/plan（后续可增强为硬校验）

辅助命令：

```bash
python hooks/implementation_scope_writer.py show --feature test2
python hooks/implementation_scope_writer.py validate --feature test2
```

## Scope 切换规则

| 当前阶段 | 是否允许切换 |
| --- | --- |
| discuss / prd 前 | 允许 |
| specs_done 后 | 不直接允许，必须回流 specs |
| plan_done 后 | 不直接允许，必须重跑 specs + plan |
| code_in_progress 后 | 不允许直接切换，建议新建 Feature 或回滚到 plan 前 |

原因：scope 会影响 specs 分母，后置切换容易造成场景覆盖和任务范围漂移。

切换后必须记录风险：

```md
R-xx: implementationScope changed after specs_done, specs/plan must be regenerated.
```

## 测试清单

### Scope 基础

- scope 缺失时默认 `full_stack`。
- state 与 `IMPLEMENTATION_SCOPE.json` 不一致时失败。
- 非法 scope 值失败。

### backend_only

- `UI_CONTEXT.uiRequired=false` 且 reason 为 `frontend_split_by_implementationScope` 通过。
- `plan task uiRequired=true` 失败。
- `uiRefs` 非空失败。
- `scope.pages` 非空失败。
- `implementationLayer=frontend/full_stack` 失败。
- `implementationLayer=backend/non_code` 通过。
- 不生成 `FRONTEND_ROUTE.json`。
- 不触发前端 review_runner 强制回检。

### frontend_only

- `implementationLayer=frontend/non_code` 通过。
- `implementationLayer=backend/full_stack` 失败。
- `dataIds` 非空失败。
- `apiIds` 非空失败。
- `consumedApiIds` 非空通过。
- 后端 API Status = `外部依赖` 时不要求后端实现覆盖。

### full_stack

- 保持老流程兼容。
- 允许 `backend/frontend/full_stack/non_code`。
- 仍执行现有 UI、plan、scenario、ref、smoke 门禁。

### Board / Guard

- `dev.specs/dev.plan/dev.code` 保留 `implementation_scope_contract`。
- `scope_compile` 在三种 scope 下选择正确验证策略。
- specs_done 后切换 scope 提示必须回流。

## 推荐落地顺序

1. 新增 `implementationScope` 契约与默认值。
2. 新增 `implementation_scope_writer.py`。
3. discuss / prd skill 接入 scope 选择与沉淀。
4. specs skill 接入 scope，生成 scope-specific specs 和 UI_CONTEXT。
5. plan.json schema 增加 `implementationScope`、`implementationLayer`、`consumedApiIds`。
6. 新增 `implementation_scope_contract` validator。
7. board_config 挂 validator。
8. autodev-code 接入 scope。
9. `code_compile` 改造成 `scope_compile` 或 scope-aware guard。
10. 补测试。
11. 用一个 `backend_only` 样例跑完整链路。

## 第一版建议

第一版优先支持：

```text
full_stack
backend_only
```

原因：

- 当前主要痛点是“前端剥离，只做后端”。
- `backend_only` 对现有系统影响更小，只需要阻止 UI task / frontend route / 前端回检。
- `frontend_only` 涉及 API 外部依赖、mock seam、后端 compile guard 降级，复杂度更高，建议第二阶段落地。
