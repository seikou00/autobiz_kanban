# Evidence 详细化改造方案

## 1. 背景

当前 `evidence/EVIDENCE.jsonl` 已经能支撑 `code_done` 硬门禁：

- 每条 evidence 有稳定 `evidenceId`
- 每条 validation evidence 记录 `taskId`
- `validation.command / exitCode / result` 可证明验证是否通过
- `changedFiles` 可记录相关文件列表
- `check_code_done` 可以判断每个 task 是否有通过的 validation evidence

但现有 evidence 更偏“命令结果记录”，信息粒度偏薄。它能回答“某个任务有没有通过验证”，但很难直接回答：

- 这个 task 具体做了什么？
- 为什么这样改？
- 每个文件分别改了什么？
- 哪些行为被验证覆盖？
- 哪些相关范围明确没有改？
- 还有哪些风险或后续事项？

因此需要把 evidence 从“验证结果记录”升级为“任务级实现事实记录”。

## 2. 目标

### 2.1 必须达成

- `EVIDENCE.jsonl` 仍然是 append-only 机器事实源。
- `code_done` 仍只根据 `action=validation` 且 `validation.result=pass` 的 evidence 判断 task 完成。
- 不让 smoke / review / verify evidence 反向满足 code_done。
- 保留现有 `changedFiles` 兼容字段。
- 新增结构化字段表达“做了什么、改了什么文件、验证了什么”。
- Markdown 只做人类视图，不作为 evidence 的事实源。

### 2.2 不做的事

- 不把 evidence 详细程度直接混入 `code_done` 的通过条件。
- 不要求 review / utest / e2e / verify 阶段反写 code 阶段 evidence。
- 不用 Markdown 摘要和 JSON evidence 做文本对账。
- smoke 结果本身不决定 task 是否完成；但 smoke fail/blocked 未完成 triage 会阻断 `code_done` checkpoint。

## 3. 现有结构

当前一条 validation evidence 的核心结构大致是：

```json
{
  "version": 1,
  "evidenceId": "ev_0001",
  "featureId": "alpha",
  "checkpoint": "code_in_progress",
  "nodeId": "dev.code",
  "skill": "autodev-code",
  "taskId": "T001",
  "action": "validation",
  "specRefs": ["specs/order/spec.md#REQ-001", "specs/order/spec.md#SCN-001"],
  "designRefs": ["design.md#D-001"],
  "changedFiles": ["src/main/java/example/OrderService.java"],
  "validation": {
    "command": "mvn test -Dtest=OrderServiceTest",
    "exitCode": 0,
    "result": "pass"
  },
  "createdAt": "2026-07-07T10:00:00Z"
}
```

主要缺口是：

- `changedFiles` 只有路径，没有文件级说明。
- 没有稳定字段描述 task 的实现内容。
- 没有字段说明验证命令实际覆盖了哪些行为。
- 没有字段记录范围约束、风险和后续事项。

## 4. 推荐新增字段

### 4.1 顶层 `summary`

一句话说明这条 evidence 完成了什么。

```json
{
  "summary": "完成订单取消状态校验，并补充对应单元测试。"
}
```

要求：

- validation evidence 建议必填。
- 只写结论，不堆命令输出。
- 不替代 `validation.command`。

### 4.2 `implementation`

描述实现层面的事实。

```json
{
  "implementation": {
    "whatChanged": [
      "在 OrderService.cancel 中增加已支付订单不可取消的状态校验",
      "复用现有 ORDER_STATE_INVALID 错误码",
      "新增已支付订单取消失败的单元测试"
    ],
    "why": "满足 SCN-004：已支付订单不可直接取消。",
    "notChanged": [
      "未调整退款流程",
      "未调整库存回滚逻辑"
    ],
    "risks": [
      "旧调用方若依赖直接取消已支付订单，会收到新的业务错误"
    ],
    "followUps": []
  }
}
```

字段说明：

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `whatChanged` | string[] | 行为级改动，不只是文件名 |
| `why` | string | 改动原因，绑定需求、场景或设计决策 |
| `notChanged` | string[] | 明确没有改的相关范围，防止 scope 漂移 |
| `risks` | string[] | 已知风险 |
| `followUps` | string[] | 后续事项，不作为本轮完成条件 |

### 4.3 `fileChanges`

作为 `changedFiles` 的详细版，描述每个文件改了什么。

```json
{
  "fileChanges": [
    {
      "path": "src/main/java/example/OrderService.java",
      "operation": "modified",
      "kind": "source",
      "summary": "增加取消前的订单状态校验",
      "symbols": ["OrderService#cancel"],
      "reason": "满足 SCN-004：已支付订单不可直接取消"
    },
    {
      "path": "src/test/java/example/OrderServiceTest.java",
      "operation": "modified",
      "kind": "test",
      "summary": "新增已支付订单取消失败用例",
      "symbols": ["OrderServiceTest#cancelPaidOrderFails"],
      "reason": "覆盖 T002 的 validationCommands"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `path` | string | 文件路径，必须相对项目根或 workspace 根 |
| `operation` | string | `created` / `modified` / `deleted` / `renamed` |
| `kind` | string | `source` / `test` / `config` / `docs` / `generated` / `smoke` |
| `fromPath` | string | 仅 `operation=renamed` 时使用，表示重命名前路径 |
| `summary` | string | 该文件的具体改动 |
| `symbols` | string[] | 可选，类、函数、组件、配置 key 等定位信息 |
| `reason` | string | 可选，该文件为什么需要改 |

兼容规则：

- `changedFiles` 继续保留。
- `changedFiles` 应等于 `fileChanges[].path` 的投影。
- 迁移期可以允许只有 `changedFiles`，后续再收紧。

### 4.4 扩展 `validation`

现有 `validation.command / exitCode / result / outputTailPath` 继续保留，新增验证语义字段。

```json
{
  "validation": {
    "command": "mvn test -Dtest=OrderServiceTest",
    "exitCode": 0,
    "result": "pass",
    "scope": "task",
    "checkedBehavior": [
      "已支付订单取消返回 ORDER_STATE_INVALID",
      "待支付订单仍可取消"
    ],
    "expected": "OrderServiceTest 全部通过"
  }
}
```

字段说明：

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `scope` | string | `task` / `module` / `project` |
| `checkedBehavior` | string[] | 这条命令实际验证了哪些行为 |
| `expected` | string | 可选，期望信号 |

`validation.result` 仍是 code_done 的核心判断字段。

## 5. 完整示例

```json
{
  "version": 1,
  "detailVersion": 1,
  "evidenceId": "ev_0007",
  "featureId": "order-cancel",
  "checkpoint": "code_in_progress",
  "nodeId": "dev.code",
  "skill": "autodev-code",
  "taskId": "T002",
  "action": "validation",
  "specRefs": [
    "specs/order/spec.md#REQ-002",
    "specs/order/spec.md#SCN-004"
  ],
  "designRefs": [
    "design.md#D-002"
  ],
  "changedFiles": [
    "src/main/java/example/OrderService.java",
    "src/test/java/example/OrderServiceTest.java"
  ],
  "summary": "完成订单取消状态校验，并补充对应单元测试。",
  "implementation": {
    "whatChanged": [
      "在 OrderService.cancel 中增加已支付订单不可取消的状态校验",
      "复用现有 ORDER_STATE_INVALID 错误码",
      "新增已支付订单取消失败的单元测试"
    ],
    "why": "满足 SCN-004：已支付订单不可直接取消。",
    "notChanged": [
      "未调整退款流程",
      "未调整库存回滚逻辑"
    ],
    "risks": [],
    "followUps": []
  },
  "fileChanges": [
    {
      "path": "src/main/java/example/OrderService.java",
      "operation": "modified",
      "kind": "source",
      "summary": "增加取消前的订单状态校验",
      "symbols": ["OrderService#cancel"],
      "reason": "满足 SCN-004"
    },
    {
      "path": "src/test/java/example/OrderServiceTest.java",
      "operation": "modified",
      "kind": "test",
      "summary": "新增已支付订单取消失败用例",
      "symbols": ["OrderServiceTest#cancelPaidOrderFails"],
      "reason": "覆盖 T002 validationCommands"
    }
  ],
  "validation": {
    "command": "mvn test -Dtest=OrderServiceTest",
    "exitCode": 0,
    "result": "pass",
    "scope": "task",
    "checkedBehavior": [
      "已支付订单取消返回 ORDER_STATE_INVALID"
    ],
    "expected": "OrderServiceTest 全部通过"
  },
  "createdAt": "2026-07-07T10:00:00Z"
}
```

## 6. Validator 设计

### 6.1 第一阶段：兼容扩展

在 `hooks/evidence_store.py` 中增加新字段校验，但全部作为可选字段：

- `summary` 若存在必须是非空字符串。
- `implementation` 若存在必须是对象。
- `implementation.whatChanged / notChanged / risks / followUps` 若存在必须是字符串数组。
- `implementation.why` 若存在必须是非空字符串。
- `fileChanges` 若存在必须是对象数组。
- `fileChanges[].path / operation / kind / summary` 必须是非空字符串；`operation=renamed` 时还必须有 `fromPath`。
- `validation.scope` 若存在必须在允许枚举内。
- `validation.checkedBehavior` 若存在必须是字符串数组。

这一阶段不改变 code_done 通过条件。

### 6.2 第二阶段：引导生成

更新 `skills/autodev/autodev-code/SKILL.md`：

- 每次 task validation 后，必须写详细 evidence。
- 要求记录 `summary`。
- 要求记录 `implementation.whatChanged`。
- 要求记录 `fileChanges`。
- 要求记录 `validation.checkedBehavior`。
- `changedFiles` 必须从 `fileChanges[].path` 投影。

建议优先使用完整 JSON record append，而不是只用 CLI 参数拼装。

### 6.3 第三阶段：逐步收紧

待真实流程稳定后，再把以下规则变成硬校验：

- `action=validation` 必须有 `summary`。
- `action=validation` 必须有 `implementation.whatChanged`。
- `action=validation` 必须有 `fileChanges`，除非明确是“仅验证无代码变更”。
- `changedFiles` 与 `fileChanges[].path` 必须一致。
- `validation.checkedBehavior` 必须非空。

这类收紧可以放到独立 validator，例如 `evidence_detail_quality`，先挂到 `dev.code` 契约层，不直接塞进 `code_done_gate`。

## 7. code_done 边界

`code_done` 仍只认通过的 validation evidence。

有效完成证据：

```json
{
  "action": "validation",
  "validation": {
    "result": "pass"
  }
}
```

不应满足 code_done 的证据：

```json
{
  "action": "smoke",
  "smoke": {
    "result": "pass"
  }
}
```

```json
{
  "action": "review",
  "verdict": "PASS"
}
```

新增 detail 字段只提高追溯质量，不改变现有强门禁语义。

## 8. CLI 与写入方式

### 8.1 推荐方式

详细 evidence 建议通过完整 JSON record 写入。

原因：

- `fileChanges` 是数组对象，CLI 参数不适合表达。
- `implementation` 有多层结构，JSON record 更稳定。
- 能避免 `--command / --exit-code` 自动注入 validation 的副作用。

### 8.2 CLI 可扩展字段

可以给简单场景补充：

- `--summary`
- `--changed-file`

但不建议用 CLI 表达完整 `fileChanges`。

## 9. 与其他 JSON 产物的关系

| 产物 | 职责 | 是否替代 evidence |
| --- | --- | --- |
| `plan.json` | 任务 DAG、状态、validationCommands | 否 |
| `EVIDENCE.jsonl` | append-only 执行事实流 | 是 code 阶段证据源 |
| `SMOKE_RESULT.json` | 旁路冒烟结果 | 否 |
| `REVIEW_FINDINGS.json` | 评审发现 | 否 |
| `UNIT_TEST_RESULT.json` | 单测阶段结果 | 否 |
| `E2E_RESULT.json` | E2E 阶段结果 | 否 |
| `VERIFY_DECISION.json` | 最终验收裁决 | 否 |

`EVIDENCE.jsonl` 记录执行事实，其他 JSON 读取并引用 evidenceId，不反向改写 code 阶段 evidence。

## 10. 推荐落地任务

本节保留为高层摘要，具体实施顺序、schema 细节、validator 算法与测试矩阵以 §14.20 为准。不要同时维护两套 checklist。

高层任务只有三类：

1. Evidence detail：扩展 evidence record，让完成证据能说明“做了什么、为什么做、改了哪些文件、验证了什么”。
2. Smoke triage：让 smoke fail/blocked 保持旁路语义，但必须被诊断、修复或明确说明无法修复。
3. Board / Skill 收口：把 validator、board_config 与 `autodev-code` skill 的完成条件统一到同一套机器契约。

## 11. 风险与注意事项

- 不要把详细描述当成 pass evidence；pass 仍只来自 `validation.result=pass`。
- 不要要求每条 evidence 都有文件变更；verify 汇总、review 汇总可能没有代码文件。
- 对 `action=smoke` 仍使用 `smoke` 字段，不要塞进 `validation`。
- 文件级 summary 要写真实改动，不要只复制文件名。
- `implementation.notChanged` 用于约束范围，不应变成免责说明。

## 12. 最终效果

改造完成后，可以从一条 evidence 直接读出：

- 这个 task 做了什么
- 为什么做
- 改了哪些文件
- 每个文件分别改了什么
- 运行了什么命令
- 命令验证了哪些行为
- 还有哪些已知风险

同时，现有 `code_done` 门禁语义保持稳定，不会因为 evidence 更详细而误伤流程流转。

## 13. Smoke 失败 triage 与 repair 方案

### 13.1 背景

当前 smoke 设计是旁路风险信号：

- `SMOKE_TEST_PLAN.json` 在 Plan 阶段生成。
- Code 阶段生成或补齐本地 smoke 测试源码。
- `run_advisory_smoke.py` 执行 smoke，并写入 `SMOKE_RESULT.json`。
- smoke evidence 使用 `action=smoke`。
- `SMOKE_RESULT.json.verdict` 本身不决定 `code_done` 是否通过。
- `code_done` 仍只认 `action=validation` 且 `validation.result=pass` 的 evidence。

这个边界是正确的，但真实环境中会出现一个问题：smoke 失败后，agent 可能因为“smoke 不阻断流转”而直接忽略失败。例如：

- smoke 脚本缺少本地依赖包。
- smoke 命令不存在。
- smoke 测试源码没有生成完整。
- 应用启动缺环境变量。
- 业务实现问题导致主链路失败。

因此需要把 smoke 从“可忽略旁路”升级为“必须 triage 的旁路”。

核心规则：

```text
SMOKE_RESULT 的 FAIL/BLOCKED 结果不阻断 code_done；
但 FAIL/BLOCKED 未完成 triage 会阻断 code_done checkpoint。
```

### 13.2 目标

必须达成：

- smoke 仍然不作为 `code_done` 的 pass/fail 门禁。
- `SMOKE_RESULT.verdict=FAIL/BLOCKED` 且 triage 完整时允许推进 `code_done`。
- `SMOKE_RESULT.verdict=FAIL/BLOCKED` 但缺少 triage 时必须阻断 checkpoint。
- smoke 失败不能被静默忽略。
- smoke 失败必须有结构化 `failureCategory`、`failureSummary` 与 `resolution`。
- 可修复问题必须记录 `repairAttempts`。
- 修业务代码后必须重新跑强 validation，并追加新的 `action=validation` evidence。
- 修 smoke 本地测试资产后必须重跑 smoke。
- 重跑 smoke 不能抹掉已有 triage 信息。
- 最终 PASS 也要保留历史失败与修复记录，防止“静默修好”。

不做的事：

- 不让 `SMOKE_RESULT.verdict=FAIL/BLOCKED` 本身阻断 `code_done`；但 triage 缺失必须阻断 checkpoint。
- 不让 smoke evidence 写入 `plan.json.tasks[].evidenceIds`。
- 不让 smoke 失败自动升级成 task failed。
- 不要求为了 smoke 依赖随意修改业务项目依赖文件。

### 13.3 分工边界

必须明确脚本与 agent 的职责。

| 字段 / 行为 | 写入方 | 说明 |
| --- | --- | --- |
| `verdict` | `run_advisory_smoke.py` | 基于最后一次执行结果聚合 |
| `results[].result` | `run_advisory_smoke.py` | `pass` / `fail` / `blocked` / `skipped` |
| `results[].exitCode` | `run_advisory_smoke.py` | 命令退出码 |
| `results[].failureSummary` | `run_advisory_smoke.py` | 从输出中提取失败摘要 |
| `results[].failureCategorySuggested` | `run_advisory_smoke.py` | 脚本根据输出做初猜，不作为最终判断，也不作为 validator 兜底 |
| `results[].categoryConfidence` | `run_advisory_smoke.py` | `low` / `medium` / `high` |
| `results[].failureCategory` | agent | agent triage 后写最终分类 |
| `results[].repairAttempts` | agent | agent 尝试修复后写入 |
| `results[].resolution` | agent | agent 写最终处理结论 |

`run_advisory_smoke.py` 是 runner 与初步分类器，不负责自动修复。triage loop 由 `autodev-code` agent 执行。

### 13.4 SMOKE_RESULT.json schema 扩展

建议在 `SMOKE_RESULT.json.results[]` 中新增字段：

```json
{
  "testId": "SMK-001",
  "taskId": "T001",
  "command": "python tests/smoke/cap_smoke.py",
  "exitCode": 1,
  "result": "fail",
  "evidenceId": "ev_0009",
  "outputTailPath": "evidence/ev_0009.log",
  "failureSummary": "ModuleNotFoundError: No module named 'requests'",
  "failureCategorySuggested": "missing_smoke_dependency",
  "categoryConfidence": "medium",
  "failureCategory": "missing_smoke_dependency",
  "repairAttempts": [
    {
      "attempt": 1,
      "action": "rewrite_smoke_to_stdlib",
      "summary": "将 smoke 脚本中的 requests 调用改为 urllib，避免引入额外依赖",
      "changedFiles": ["tests/smoke/cap_smoke.py"],
      "rerun": "smoke",
      "result": "pass",
      "evidenceIds": ["ev_0010"]
    }
  ],
  "resolution": {
    "status": "fixed",
    "summary": "已修复 smoke 脚本依赖问题并重跑通过"
  }
}
```

失败但无法修复时：

```json
{
  "testId": "SMK-002",
  "taskId": "T003",
  "result": "blocked",
  "failureSummary": "command timed out after 60 seconds",
  "failureCategory": "environment_unavailable",
  "repairAttempts": [],
  "resolution": {
    "status": "manual_required",
    "summary": "当前环境缺少联调服务地址，无法在本会话内补齐；已保留失败日志，后续需人工提供环境变量。",
    "humanActionRequired": true
  }
}
```

### 13.5 failureCategory 枚举

建议固定枚举：

| 分类 | 说明 | 是否应尝试修复 |
| --- | --- | --- |
| `missing_smoke_dependency` | smoke 测试资产自身缺依赖 | 是 |
| `missing_app_dependency` | 业务应用缺依赖 | 是 |
| `test_asset_error` | smoke 脚本、命令、路径、断言写错 | 是 |
| `implementation_failure` | 业务实现不符合 smoke 预期 | 是 |
| `environment_unavailable` | 本地环境缺变量、端口、服务或权限 | 能在本地补齐则修；不能补齐则 `manual_required` |
| `external_service_unavailable` | 外部依赖服务不可用 | 通常不能自动修 |
| `command_error` | 命令不存在、shell 失败、参数错误 | 是 |
| `timeout` | 执行超时 | 先检查命令、等待条件和超时设置；无法确认则 `manual_required` |
| `unknown` | 暂无法分类 | 必须解释 |

脚本只能写 `failureCategorySuggested`。最终 `failureCategory` 必须由 agent triage 后显式写入；validator 不接受 `failureCategorySuggested` 自动兜底。

`unknown` 不应成为常态。若使用 `unknown`，`resolution.summary` 必须解释为什么无法进一步分类。

### 13.6 resolution.status 枚举

建议固定枚举：

| status | 含义 | 约束 |
| --- | --- | --- |
| `fixed` | 已修复并重跑 | 必须有 `repairAttempts`，最终 result 必须为 `pass` |
| `manual_required` | 需要人工或环境补齐 | 必须写 `summary`，建议写 `humanActionRequired=true` |
| `accepted_risk` | 接受该旁路风险继续流转 | 只能用于环境或外部服务类问题，不得用于 `implementation_failure` |
| `wont_fix` | 明确不修 | 必须解释原因，不能用于掩盖实现失败 |

限制：

- `implementation_failure` 不允许直接写 `accepted_risk`。
- `missing_smoke_dependency` / `test_asset_error` 默认必须尝试修复。
- `fixed` 必须有至少一条 `repairAttempts`。
- `manual_required` 可以没有 `repairAttempts`，但必须说明为什么当前会话无法修复。

### 13.7 repairAttempts schema

建议固定结构：

```json
{
  "attempt": 1,
  "action": "rewrite_smoke_to_stdlib",
  "summary": "将 requests 改为 urllib，避免额外依赖",
  "changedFiles": ["tests/smoke/cap_smoke.py"],
  "rerun": "smoke",
  "result": "pass",
  "evidenceIds": ["ev_0010"]
}
```

`repair-attempt.json` 文件就是单个 repair attempt object，不是数组，不带外层 wrapper。示例：

```json
{
  "attempt": 1,
  "action": "fix_implementation",
  "summary": "修正订单取消状态判断并重跑 validation 与 smoke",
  "changedFiles": [
    "src/main/java/example/OrderService.java"
  ],
  "rerun": "validation+smoke",
  "result": "pass",
  "evidenceIds": [
    "ev_0011",
    "ev_0012"
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `attempt` | number | 从 1 开始递增 |
| `action` | string | 修复动作枚举或短标识 |
| `summary` | string | 本次尝试做了什么 |
| `changedFiles` | string[] | 本次尝试修改的文件 |
| `rerun` | string | `smoke` / `validation+smoke` / `none` |
| `result` | string | `pass` / `fail` / `blocked` / `skipped` |
| `evidenceIds` | string[] | 本次重跑产生的 evidence |

建议 `action` 枚举：

- `rewrite_smoke_to_stdlib`
- `fix_smoke_command`
- `fix_smoke_source`
- `add_smoke_env_dep`
- `fix_app_dependency`
- `fix_implementation`
- `set_required_env`
- `adjust_timeout`
- `document_manual_requirement`

### 13.8 重跑 merge 策略

当前 runner 每次运行会重写 `SMOKE_RESULT.json`。如果 agent 在第一次失败后写入了 `repairAttempts` / `resolution`，第二次重跑会覆盖这些字段。

推荐采用 merge 策略：

```text
run_advisory_smoke.py 重跑时：
1. 先读取已有 SMOKE_RESULT.json。
2. 按 testId 建立 previousResults。
3. 生成新的 raw result。
4. 对同一个 testId，把旧的 repairAttempts / resolution / failureCategory 合并回新 result。
5. 新执行字段以本次 runner 输出为准：result / exitCode / evidenceId / outputTailPath / failureSummary。
6. triage 字段以 agent 已写字段为准，除非 agent 后续覆盖。
```

合并规则：

| 字段 | 合并策略 |
| --- | --- |
| `result` | 使用最新 runner 结果 |
| `exitCode` | 使用最新 runner 结果 |
| `evidenceId` | 使用最新 runner 结果 |
| `outputTailPath` | 使用最新 runner 结果 |
| `failureSummary` | 使用最新 runner 结果；pass 时可保留 `lastFailureSummary` |
| `failureCategorySuggested` | 使用最新 runner 初猜 |
| `categoryConfidence` | 使用最新 runner 初猜 |
| `failureCategory` | 保留 agent 写入值，除非 agent 修改 |
| `repairAttempts` | 追加保留 |
| `resolution` | 保留 agent 写入值，除非 agent 修改 |

为了避免最终 PASS 后丢失失败历史，可以新增：

```json
{
  "previousFailures": [
    {
      "evidenceId": "ev_0009",
      "result": "fail",
      "failureCategory": "missing_smoke_dependency",
      "failureSummary": "ModuleNotFoundError: No module named 'requests'"
    }
  ]
}
```

或者通过 `EVIDENCE.jsonl` 反查历史失败 evidence。更推荐以后者为准，`previousFailures` 只做人类可读摘要。

### 13.9 防止静默修好

如果第一次 smoke FAIL，agent 修完后第二次 smoke PASS，最终 `SMOKE_RESULT.json` 可能只有 PASS，导致看不到曾经失败与修复过程。

必须增加规则：

```text
若 EVIDENCE.jsonl 中同一 testId 存在历史 smoke fail/blocked evidence，
则最终 SMOKE_RESULT.json 对应 result 即使是 pass，
也必须有 repairAttempts 或 resolution.status=fixed。
```

可选替代方案是限制每个 `testId` 只能有一条 smoke evidence，但这会损害重跑能力，不推荐。

### 13.10 业务代码修复后的 validation evidence 要求

smoke 失败如果是 `implementation_failure` 或 `missing_app_dependency`，agent 可能需要修改业务代码或业务依赖文件。

这类修复必须重新跑强 validation，而不仅是重跑 smoke。

规则：

```text
repairAttempts[].changedFiles 命中非 smoke 测试资产路径
  → repairAttempts[].rerun 必须为 validation+smoke
  → 必须存在同 taskId 的新增 action=validation evidence
```

smoke 测试资产路径建议沿用现有白名单：

- `tests/smoke/`
- `scripts/smoke/`
- `src/test/` 中明确 opt-in 的 smoke 文件
- 其他由 `SMOKE_TEST_PLAN.json.tests[].sourcePath` 明确声明的路径

不在这些路径里的 `changedFiles` 视为业务代码或业务配置变更。

### 13.11 smoke-env 约定

如果失败是 `missing_smoke_dependency`，优先级如下：

1. 优先改 smoke 脚本，使用项目已有测试栈或标准库。
2. 不随意修改业务项目的 `package.json` / `pom.xml` / `requirements.txt`。
3. 如果确实需要 smoke 专属依赖，放入本地 smoke 环境，并确保不进入业务项目 Git 托管。

建议约定：

```text
.autobizdevops/smoke-env/
```

要求：

- `smoke-env` 必须被目标项目 Git 忽略。
- smoke 命令必须显式激活该环境。
- validator 不要求业务依赖文件为 smoke 增加依赖。
- 如果无法建立 smoke-env，写 `resolution.status=manual_required`。

### 13.12 blocked / skipped 处理边界

当前 runner 中常见 `blocked` 来源：

- command timeout
- command not found
- OSError
- sourcePath preflight 失败

处理规则：

| result | 是否必须 triage | 说明 |
| --- | --- | --- |
| `pass` | 通常不需要 | 若历史 evidence 有 fail/blocked，则必须保留 repair 记录 |
| `fail` | 必须 | 必须有 `failureCategory` 与 `resolution` |
| `blocked` | 必须 | 必须区分 timeout / command_error / environment_unavailable |
| `skipped` | 按规则 | 计划 `tests=[]` 可不 triage；单条 case skipped 必须有 `skipReason` 或 `resolution` |

preflight 失败仍然是 Code 阶段未完成测试资产准备，不属于“旁路 FAIL”。例如：

- `sourcePath` 不存在
- smoke 源码未被 Git ignore
- smoke 源码已被 Git 跟踪
- 测试条目非法

这类问题 `run_advisory_smoke.py` 可以继续返回非 0，要求 agent 先补齐测试资产后再跑。

### 13.13 Validator 规则

保持原则：

```text
不拦 smoke fail；
但拦 smoke fail 无处理。
```

建议规则：

1. `SMOKE_RESULT.verdict=FAIL/BLOCKED` 合法。
2. `results[].result in {"fail", "blocked"}` 时，必须有：
   - `failureSummary`
   - `failureCategory`
   - `resolution.status`
   - `resolution.summary`
3. `failureCategory` 必须在枚举内。
4. `resolution.status=fixed` 必须有 `repairAttempts`。
5. `implementation_failure` 不允许 `accepted_risk`。
6. `missing_smoke_dependency` / `test_asset_error` 若无 `repairAttempts`，必须 `manual_required` 并说明原因。
7. 若 evidence 中同一 `testId` 有历史 fail/blocked，而最终 result 为 pass，则必须有 `repairAttempts` 或 `resolution.status=fixed`。
8. 若 `repairAttempts[].changedFiles` 包含业务路径，则必须存在同 taskId 的新增 `action=validation` evidence。

### 13.14 Skill 文档要求

`autodev-code` 需要补充 smoke triage loop：

1. 强 validation 通过后运行 smoke。
2. 如果 smoke PASS，记录风险为空并继续。
3. 如果 smoke FAIL/BLOCKED：
   - 读取 `SMOKE_RESULT.json` 与对应 evidence tail。
   - 分类失败原因。
   - 对可修复问题最多尝试 1 到 2 轮最小修复。
   - 修 smoke 测试资产后重跑 smoke。
   - 修业务代码或业务依赖后，先重跑 validation，再重跑 smoke。
   - 修不了时写 `resolution.status=manual_required` 或合规的 `accepted_risk`。
4. 不得把 smoke evidence 写入 `plan.json.tasks[].evidenceIds`。
5. 不得因为 smoke verdict 非 PASS 阻断 `code_done`；但必须先补齐 triage，否则 `smoke_result_json` validator 应阻断 checkpoint。
6. 不得用 `accepted_risk` 掩盖实现失败。

### 13.15 测试清单

建议补以下回归测试：

- `SMOKE_RESULT` FAIL 且无 `resolution`：拒绝。
- `SMOKE_RESULT` BLOCKED 且无 `failureCategory`：拒绝。
- `missing_smoke_dependency` 有 `repairAttempts` 且最终 PASS：通过。
- FAIL 后写 repair，再重跑 PASS，merge 后 repair 信息仍保留。
- `EVIDENCE.jsonl` 有同 testId 历史 fail，最终 PASS 但无 repair：拒绝。
- `implementation_failure` + 业务文件变更 + 无新 validation evidence：拒绝。
- `implementation_failure` + `accepted_risk`：拒绝。
- `environment_unavailable` + `manual_required`：通过。
- `accepted_risk` 用于外部服务不可用，且 `humanActionRequired=true`：通过。
- preflight 失败仍返回非 0，不写“看似完成”的 `SMOKE_RESULT`。

### 13.16 推荐落地顺序

1. 定稿 `SMOKE_RESULT.json` triage schema。
2. 修改 `run_advisory_smoke.py`，支持重跑 merge 已有 triage 信息。
3. 增加脚本初步分类字段：`failureCategorySuggested` 与 `categoryConfidence`。
4. 更新 `validate_smoke_result_json`：
   - 失败必须有 triage。
   - 最终 PASS 但历史失败必须有 repair 记录。
   - 业务修复必须有后续 validation evidence。
5. 更新 `autodev-code/SKILL.md`，加入 smoke triage loop。
6. 补测试。
7. 真实项目试跑后，再决定是否把部分规则收紧。

### 13.17 最终效果

改造完成后，smoke 的语义变成：

```text
validationCommands 决定 code_done 是否可通过；
smoke 决定主链路风险是否被发现、处理和留痕。
```

这样既不会把脆弱或依赖环境的 smoke 升级成硬门禁，也不会让真实 smoke 失败被完全忽略。

## 14. 落地前契约补强

本节用于把 §1-13 的方向方案收口成可实现契约。若本节与前文存在细节冲突，落地实现时以本节为准。

### 14.1 Evidence schema 版本策略

不建议第一轮直接把 `EVIDENCE_VERSION` 从 `1` 升到 `2`。

原因：

- 当前 reader / validator / gate 已经围绕 `version: 1` 工作。
- 老 evidence 仍需要可读。
- 详细化字段是增量扩展，不改变 append-only 流格式。

推荐策略：

```json
{
  "version": 1,
  "detailVersion": 1
}
```

含义：

| 字段 | 作用 |
| --- | --- |
| `version` | evidence 流基础格式版本，继续保持 `1` |
| `detailVersion` | 详细证据扩展契约版本；存在且为 `1` 时启用 detail 校验 |

迁移阶段：

- 旧 evidence 可以没有 `detailVersion`。
- 新生成的 validation pass evidence 应写 `detailVersion: 1`。
- `evidence_detail_quality` 初期只强校验带 `detailVersion` 的记录。
- 后期再要求完成 evidence 必须带 `detailVersion: 1`。

### 14.2 fileChanges 标准结构

将文件操作与文件用途拆成两个维度，避免语义混杂。

```json
{
  "fileChanges": [
    {
      "path": "src/main/java/example/OrderService.java",
      "operation": "modified",
      "kind": "source",
      "summary": "增加订单取消前状态校验",
      "symbols": ["OrderService#cancel"],
      "reason": "满足 SCN-004：已支付订单不可直接取消"
    }
  ]
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `path` | string | 是 | 当前文件路径；删除时为被删除路径，重命名时为新路径 |
| `operation` | string | 是 | `created` / `modified` / `deleted` / `renamed` |
| `kind` | string | 是 | `source` / `test` / `config` / `docs` / `generated` / `smoke` |
| `fromPath` | string | 仅 renamed | 重命名前路径 |
| `summary` | string | 是 | 文件级改动说明 |
| `symbols` | string[] | 否 | 类、函数、组件、配置 key 等定位信息 |
| `reason` | string | 否 | 该文件为什么需要改 |

`renamed` 示例：

```json
{
  "path": "src/new/OrderService.java",
  "fromPath": "src/old/OrderService.java",
  "operation": "renamed",
  "kind": "source",
  "summary": "按模块结构调整服务类位置"
}
```

### 14.3 changedFiles 投影规则

`changedFiles` 继续保留，但它只是兼容投影。

规则：

```text
普通 created / modified / deleted：
  changedFiles 去重集合必须包含 fileChanges[].path

renamed：
  changedFiles 去重集合必须同时包含 fromPath 和 path

比较方式：
  不比较顺序，只比较去重后的集合
```

示例：

```json
{
  "changedFiles": [
    "src/old/OrderService.java",
    "src/new/OrderService.java"
  ],
  "fileChanges": [
    {
      "fromPath": "src/old/OrderService.java",
      "path": "src/new/OrderService.java",
      "operation": "renamed",
      "kind": "source",
      "summary": "移动服务类到新模块"
    }
  ]
}
```

### 14.4 无代码变更的显式标记

如果一条 validation evidence 只是验证已有实现，没有修改代码，不能靠缺失 `fileChanges` 表达。

必须显式写：

```json
{
  "changedFiles": [],
  "fileChanges": [],
  "implementation": {
    "noCodeChange": true,
    "whatChanged": [],
    "why": "本条 evidence 仅验证 T001 已有实现满足 SCN-001"
  }
}
```

validator 规则：

- `fileChanges` 为空时，必须有 `implementation.noCodeChange=true`。
- `implementation.noCodeChange=true` 时，`changedFiles` 与 `fileChanges` 都必须为空。
- 如果 `noCodeChange=true` 但 `whatChanged` 非空，应拒绝。

### 14.5 哪些 evidence 必须 detail

分阶段策略：

| evidence 类型 | 迁移期 | 收紧后 |
| --- | --- | --- |
| `plan.json.tasks[].evidenceIds` 引用的 pass validation | 建议 detail | 必须 detail |
| smoke repair 后产生的业务 validation | 建议 detail | 必须 detail |
| 其他 pass validation | 建议 detail | 建议 detail |
| fail validation | 不强制 detail，但必须有 command / exitCode / result / outputTailPath | 同左 |
| smoke evidence | 使用 `smoke` 字段；可有 summary，但不要求 fileChanges | 同左 |
| verify / review 汇总 evidence | 可有 summary；通常不要求 fileChanges | 同左 |

完成 evidence 的定义：

```text
被 plan.json.tasks[].evidenceIds 引用，且 action=validation，且 validation.result=pass。
```

无 `plan.json` 的 lean / custom 流程：

```text
Phase 2：
  每个 distinct taskId 的最后一条 pass validation 建议带 detailVersion=1。

Phase 3：
  每个 distinct taskId 至少一条 pass validation 必须带 detailVersion=1，
  且满足 summary / implementation / fileChanges 或 noCodeChange 规则。
```

### 14.6 action 字段矩阵

| action | 必填结构 | 可选 detail | 是否满足 code_done |
| --- | --- | --- | --- |
| `validation` | `validation.command/exitCode/result` | `summary` / `implementation` / `fileChanges` / `validation.checkedBehavior` | 是，且仅 pass 时满足 |
| `smoke` | `smoke.testId/command/exitCode/result` | `summary` | 否 |
| `review` | 评审结论字段 | `summary` / `risks` | 否 |
| `verify` | 汇总 verdict / evidenceIds | `summary` | 否 |
| `smoke_triage` | 暂不启用 | 暂不启用 | 否 |

第一轮不新增 `action=smoke_triage`。triage 事实源先放在 `SMOKE_RESULT.json`，并引用 `EVIDENCE.jsonl` 中的 smoke / validation evidenceIds。

### 14.7 evidence_detail_quality validator 挂载策略

新增 validator：

```text
evidence_detail_quality
```

职责：

- 检查 detailed validation evidence 的字段质量。
- 不判断 task 是否完成。
- 不替代 `code_done_gate`。

推荐挂载阶段：

| 阶段 | board_config 挂载 | 行为 |
| --- | --- | --- |
| Phase 1 | 不挂或仅测试调用 | 只验证 schema helper |
| Phase 2 | 挂 `dev.code`，但只检查 `detailVersion=1` 记录 | 迁移兼容 |
| Phase 3 | 挂 `dev.code`，并强制完成 evidence 必须 detail | 收紧 |

推荐顺序：

```json
[
  "ui_context_json",
  "plan_json_contract",
  "plan_finished_tasks",
  "smoke_result_json",
  "frontend_route_gate",
  "evidence_detail_quality",
  "code_done_gate",
  "evidence_integrity"
]
```

`evidence_detail_quality` 应在 `code_done_gate` 前运行，便于 agent 先修证据质量；但它不改变 `code_done_gate` 的 pass 判定。

### 14.8 evidence 写入工具

详细 evidence 不建议只靠 CLI 参数拼装。优先复用现有稳定写入路径：

```text
hooks/evidence_store.py append --record evidence-record.json
```

要求：

- record 文件必须是完整 JSON object。
- append 工具继续负责分配 `evidenceId`、写 `createdAt`、维护 `EVIDENCE.index.json`。
- agent 不得手工编辑 `EVIDENCE.jsonl`。
- skill 文档中给出最小 detailed validation record 示例。

### 14.9 Smoke checkpoint 语义

必须统一表述：

```text
smoke verdict 本身不阻断 code_done；
smoke fail/blocked 未完成 triage 会阻断 code_done checkpoint。
```

也就是说：

| SMOKE_RESULT 状态 | 是否允许推进 code_done |
| --- | --- |
| `PASS` | 允许 |
| `FAIL` + triage 完整 | 允许 |
| `BLOCKED` + triage 完整 | 允许 |
| `FAIL` + triage 缺失 | 不允许 |
| `BLOCKED` + triage 缺失 | 不允许 |
| `NOT_APPLICABLE` 且 `SMOKE_TEST_PLAN.json.tests=[]` | 允许 |
| `SKIPPED` + 每条 skipped case 有 `skipReason` 或合规 `resolution` | 允许 |
| `SKIPPED` + skipped case 无说明 | 不允许 |
| preflight 失败导致无有效 `SMOKE_RESULT` | 不允许，说明 Code 阶段测试资产未准备好 |

### 14.10 SMOKE_RESULT.version 策略

建议与 evidence 类似，先不直接破坏旧结构。

```json
{
  "version": 1,
  "triageVersion": 1
}
```

含义：

- `version=1`：保留当前结果文件基础格式。
- `triageVersion=1`：启用 triage 扩展字段。

迁移期：

- 没有失败结果时，可不要求 `triageVersion`。
- 有 fail/blocked 且需要 triage 时，写 `triageVersion: 1`。
- 后续再考虑将 SMOKE_RESULT 主版本升级到 2。

### 14.11 Smoke triage 写入协议

agent 不应直接手改 `SMOKE_RESULT.json`。

推荐新增工具：

```text
hooks/smoke_result_triage.py
```

建议命令：

```bash
python hooks/smoke_result_triage.py \
  --feature-dir "$FEATURE_DIR" \
  --test-id SMK-001 \
  --failure-category missing_smoke_dependency \
  --resolution-status fixed \
  --resolution-summary "已改用标准库并重跑通过" \
  --repair-attempt repair-attempt.json
```

职责：

- 校验 `SMOKE_RESULT.json` 存在。
- 校验 `testId` 存在。
- patch `failureCategory`。
- append `repairAttempts`。
- 校验 `repair-attempt.json` 是单个 object，不是数组。
- 校验 `repairAttempts[].attempt` 从 1 递增且不可重复。
- 校验 `repairAttempts[].evidenceIds` 引用存在于 `EVIDENCE.jsonl`。
- patch `resolution`。
- 保留 runner 字段。
- 输出规范化 JSON。

禁止：

- 直接重写整个 `SMOKE_RESULT.json`。
- 删除历史 `repairAttempts`。
- 删除已有 `evidenceId`。

### 14.12 run_advisory_smoke merge 状态机

runner 每次重跑必须 merge 旧 triage 字段。

核心算法：

```text
previous = read SMOKE_RESULT.json if exists else {}
previousByTestId = previous.results keyed by testId

for each newResult from current run:
  old = previousByTestId[testId]
  merged = newResult

  runner-owned fields use newResult:
    result, exitCode, evidenceId, outputTailPath, failureSummary,
    failureCategorySuggested, categoryConfidence

  agent-owned fields preserve old unless absent:
    failureCategory, repairAttempts, resolution

  if old has fail/blocked and newResult is pass:
    preserve old failure summary into previousFailures summary
```

字段归属：

| 字段 | owner | merge 策略 |
| --- | --- | --- |
| `result` | runner | 最新覆盖 |
| `exitCode` | runner | 最新覆盖 |
| `evidenceId` | runner | 最新覆盖 |
| `outputTailPath` | runner | 最新覆盖 |
| `failureSummary` | runner | 最新覆盖；PASS 时可为空 |
| `failureCategorySuggested` | runner | 最新覆盖 |
| `categoryConfidence` | runner | 最新覆盖 |
| `failureCategory` | agent | 保留 |
| `repairAttempts` | agent | 保留并追加 |
| `resolution` | agent | 保留，除非 agent patch |
| `previousFailures` | runner | 追加摘要，不作为主事实源 |

`previousFailures` 只是可读摘要，最终历史判定以 `EVIDENCE.jsonl` 为准。

### 14.13 merge 后字段状态

| 场景 | 要求 |
| --- | --- |
| 最新 result=`pass`，无历史 fail/blocked | 不要求 triage |
| 最新 result=`pass`，历史有 fail/blocked | 必须有 `repairAttempts` 或 `resolution.status=fixed` |
| 最新 result=`fail` | 必须有 `failureCategory`、`failureSummary`、`resolution` |
| 最新 result=`blocked` | 必须有 `failureCategory`、`failureSummary`、`resolution` |
| `resolution.status=fixed` | 最终 result 必须为 `pass` |
| `resolution.status=accepted_risk` | 仅允许环境或外部服务类 failureCategory |
| `resolution.status=manual_required` | 必须有明确人工动作说明 |

### 14.14 smoke_result_json validator 伪代码

```python
for result in smoke_result["results"]:
    test_id = result["testId"]
    current = result["result"]
    historical_smoke = evidence_records(action="smoke", smoke.testId == test_id)
    had_fail_or_blocked = any(r.smoke.result in {"fail", "blocked"} for r in historical_smoke)

    if current in {"fail", "blocked"}:
        require(result.failureSummary)
        # failureCategorySuggested is only a runner hint; agent must write final failureCategory explicitly.
        reject(not result.failureCategory and result.failureCategorySuggested)
        require(result.failureCategory in FAILURE_CATEGORIES)
        require(result.resolution.status in RESOLUTION_STATUSES)
        require(result.resolution.summary)

    if current == "pass" and had_fail_or_blocked:
        require(result.repairAttempts or result.resolution.status == "fixed")

    if result.resolution.status == "fixed":
        require(current == "pass")
        require(result.repairAttempts)

    if result.failureCategory == "implementation_failure":
        reject(result.resolution.status == "accepted_risk")

    if result.resolution.status == "accepted_risk":
        require(result.failureCategory in {"environment_unavailable", "external_service_unavailable"})

    for attempt in result.repairAttempts:
        validate_repair_attempt(attempt)
        if touches_business_file(attempt.changedFiles, smoke_plan_source_paths):
            require(attempt.rerun == "validation+smoke")
            require(has_pass_validation_evidence_for_task(result.taskId, attempt.evidenceIds))
            require(has_smoke_evidence_for_test(test_id, attempt.evidenceIds))
```

### 14.15 业务文件判定算法

判断 `repairAttempts[].changedFiles` 是否触碰业务文件时，优先使用 `SMOKE_TEST_PLAN.json`。

```text
smokeAssetPaths =
  { tests[].sourcePath from SMOKE_TEST_PLAN.json }
  + explicit generated smoke helper paths if declared

if changedFile in smokeAssetPaths:
  smoke asset
elif changedFile startswith tests/smoke/ or scripts/smoke/:
  smoke asset fallback
else:
  business file or project config
```

不要只靠路径前缀判断。`SMOKE_TEST_PLAN.json.tests[].sourcePath` 是事实源。

以下文件若出现在 `repairAttempts[].changedFiles`，默认视为业务依赖或项目配置变更，不视为 smoke asset：

- `pom.xml`
- `build.gradle`
- `settings.gradle`
- `package.json`
- `package-lock.json`
- `pnpm-lock.yaml`
- `yarn.lock`
- `requirements.txt`
- `pyproject.toml`
- `poetry.lock`
- `.env.example`
- 业务项目的 `src/main/**`、`src/**`、`app/**`、`server/**`

### 14.16 repairAttempts 与 validation evidence 交叉校验

如果 repair attempt 触碰业务文件或业务依赖文件：

```json
{
  "repairAttempts": [
    {
      "attempt": 1,
      "action": "fix_implementation",
      "changedFiles": ["src/main/java/example/OrderService.java"],
      "rerun": "validation+smoke",
      "result": "pass",
      "evidenceIds": ["ev_0011", "ev_0012"]
    }
  ]
}
```

要求：

- `evidenceIds` 至少包含一条同 `taskId` 的 `action=validation` 且 pass 的 evidence。
- 该 validation evidence 应满足 §14.5 的 detail 要求。
- `evidenceIds` 还应包含或关联最新 smoke rerun evidence。

不使用“时间晚于 attempt”作为第一判断依据，因为 attempt 本身在 mutable `SMOKE_RESULT.json` 里没有稳定 append 时间。以 `evidenceIds` 显式引用为准。

### 14.17 skipped 规则

`skipped` 必须可执行，不使用“视情况”。

规则：

| 场景 | 要求 |
| --- | --- |
| `SMOKE_TEST_PLAN.json.tests=[]` | `SMOKE_RESULT.verdict=NOT_APPLICABLE`，允许无 triage |
| 单条 case `result=skipped` | 必须有 `skipReason` |
| 单条 case `result=skipped` 且原本应执行 | 必须有 `resolution.status=manual_required` 或 `accepted_risk` |
| skipped 被用于掩盖 command failure | 拒绝 |

### 14.18 smoke-env 执行链

`.autobizdevops/smoke-env/` 只是推荐本地环境目录，不由 runner 自动激活。

约定：

- Plan 阶段的 `SMOKE_TEST_PLAN.json.tests[].command` 必须显式激活 smoke env。
- Code 阶段如果创建 smoke env，必须确保其被目标项目 Git 忽略。
- runner 只执行 command，不推断 env。

示例：

```json
{
  "command": ". .autobizdevops/smoke-env/bin/activate && python tests/smoke/cap_smoke.py"
}
```

或：

```json
{
  "command": "PYTHONPATH=.autobizdevops/smoke-env python tests/smoke/cap_smoke.py"
}
```

不应因为 smoke 缺依赖就默认修改业务项目依赖文件。只有确认是 `missing_app_dependency` 时，才允许修改业务依赖，并必须重跑 validation。

### 14.19 人类视图投影

Markdown 不作为事实源，但可以从 JSON 投影摘要。

建议：

- `PLAN.md` 只投影 plan task、validationCommands、evidenceIds、smoke 计划摘要。
- `SMOKE_RESULT.md` 暂不新增，避免新的双源漂移。
- 如需人类摘要，可在 verify/report 阶段从 `SMOKE_RESULT.json` 摘要风险。
- 不做 Markdown ↔ JSON 文本对账。

### 14.20 实施 checklist

按三阶段落地，避免 validator 先收紧但写入工具、merge 协议或 skill 还没准备好。

#### Phase A：schema、写入工具、runner merge

目标：先让 agent 有稳定写入路径，并让 runner 不覆盖 triage。

1. `evidence_store.py`
   - 支持 `detailVersion`。
   - 支持标准 `fileChanges.operation/kind/fromPath/summary`。
   - 支持 `implementation.noCodeChange`。
   - 文档化现有 `append --record evidence-record.json` 用法，不新增重复 helper。
2. `run_advisory_smoke.py`
   - 支持 `triageVersion`。
   - 写 `failureCategorySuggested` 与 `categoryConfidence`。
   - 重跑时按 §14.12 merge 旧 `failureCategory / repairAttempts / resolution`。
   - 写入可选 `previousFailures` 摘要，但历史判定仍以 `EVIDENCE.jsonl` 为准。
3. `hooks/smoke_result_triage.py`
   - patch 指定 `testId` 的 `failureCategory`、`repairAttempts`、`resolution`。
   - 校验 `repair-attempt.json` 是单个 object。
   - 校验 `attempt` 从 1 递增且不可重复。
   - 禁止删除既有 `repairAttempts`、`evidenceId`、runner 字段。
4. 单元测试：
   - detailed evidence 合法 / 非法。
   - `noCodeChange=true` 合法。
   - `fileChanges` renamed 投影到 `changedFiles`。
   - runner 重跑 PASS 后仍保留旧 triage。
   - duplicate `repairAttempts[].attempt` 被拒。

#### Phase B：validators、board_config、契约测试

目标：开始机器校验，但保持迁移兼容。

1. `artifact_check.py`
   - 注册 `evidence_detail_quality` 到 `VALIDATORS`。
   - `evidence_detail_quality` Phase B 只检查 `detailVersion=1` 的记录。
   - 更新 `validate_smoke_result_json`，实现 §14.14 规则。
   - 明确错误码：
     - `invalid_evidence_detail_*`
     - `invalid_smoke_triage_*`
     - `missing_smoke_triage_*`
2. `board_core/board_config.json`
   - `smoke_result_json` 继续留在 `dev.code` validators。
   - `evidence_detail_quality` 加入 `dev.code` validators，但只按 Phase B 规则检查。
   - 推荐顺序：先 `smoke_result_json`，再 `evidence_detail_quality`，再 `code_done_gate`。
3. validator 回归测试：

| 测试 | 建议文件 | 阶段 |
| --- | --- | --- |
| detail record 合法 / 非法 | `tests/test_plan_json_and_evidence.py` | A/B |
| `noCodeChange` 边界 | `tests/test_plan_json_and_evidence.py` | A/B |
| merge 保留 triage | `tests/test_advisory_smoke.py` | A/B |
| fail 无 triage 拒绝 | `tests/test_artifact_check_id_contracts.py` | B |
| fail 有 triage 允许 | `tests/test_artifact_check_id_contracts.py` | B |
| fail 后 pass 无 repair 拒绝 | `tests/test_artifact_check_id_contracts.py` | B |
| fail 后 pass 有 repair 允许 | `tests/test_artifact_check_id_contracts.py` | B |
| implementation repair 无 validation evidence 拒绝 | `tests/test_artifact_check_id_contracts.py` | B |
| implementation_failure + accepted_risk 拒绝 | `tests/test_artifact_check_id_contracts.py` | B |
| environment_unavailable + manual_required 允许 | `tests/test_artifact_check_id_contracts.py` | B |
| wont_fix 合法 / 非法组合 | `tests/test_artifact_check_id_contracts.py` | B |
| SKIPPED 无 skipReason 拒绝 | `tests/test_artifact_check_id_contracts.py` | B |
| NOT_APPLICABLE + tests=[] 允许 | `tests/test_artifact_check_id_contracts.py` | B |
| lean 无 plan 的 detail 规则 | `tests/test_plan_json_and_evidence.py` | B/C |

#### Phase C：skill 同步、真实项目试跑、收紧

目标：让 agent 行为和 validator 完全一致，再逐步收紧完成 evidence。

1. `skills/autodev/autodev-code/SKILL.md`
   - 把完成条件改成：smoke FAIL/BLOCKED 结果本身不阻断，缺 triage 会阻断。
   - 加入 triage loop：分类、最多 1-2 轮修复、重跑 smoke。
   - 修业务代码或业务依赖后，必须先重跑 validation，再重跑 smoke。
   - 强调不得手工编辑 `EVIDENCE.jsonl`，不得手工整体重写 `SMOKE_RESULT.json`。
2. `skills/autodev/autodev-plan/SKILL.md`
   - smoke-env 只由 command 显式激活，runner 不自动推断。
   - smoke 缺依赖优先改 smoke 脚本或本地 ignored smoke-env，不默认改业务依赖。
3. Phase C 收紧 `evidence_detail_quality`
   - 有 `plan.json` 时：完成 evidence 必须带 `detailVersion=1`。
   - 无 `plan.json` 时：每个 distinct taskId 至少一条 pass validation 必须带 `detailVersion=1`。
   - smoke repair 触碰业务文件时：引用的 pass validation evidence 必须满足 detail 要求。
4. 真实项目试跑
   - 覆盖 smoke PASS。
   - 覆盖 smoke FAIL 但 triage 完整。
   - 覆盖 smoke FAIL 后修 smoke 脚本并 PASS。
   - 覆盖 smoke FAIL 后修业务代码、validation+smoke 重跑。
   - 覆盖环境不可用的 `manual_required`。

#### Agent 修错优先级

当多个 validator 同时失败，agent 应按以下顺序处理：

1. `missing_smoke_triage_*` / `invalid_smoke_triage_*`：先补 `SMOKE_RESULT.json` triage。
2. `invalid_evidence_detail_*`：再补新的 detailed validation evidence。
3. `invalid_code_done_gate`：最后处理 task pass evidence、blocker、plan 完成状态。

注意：

- `EVIDENCE.jsonl` 是 append-only，已经写入的 evidence 不应 patch。
- 如果完成 evidence 缺 detail，正确做法是追加新的 pass validation evidence，并把新的 evidenceId 写回 `plan.json.tasks[].evidenceIds`。
- Phase B 前应尽量一次写对 detailed evidence，避免后续靠补写造成证据流噪音。

### 14.21 最终收口语义

最终系统应满足：

```text
code_done_gate:
  只判断 plan 完成、blocker 清空、每个 task 有 pass validation evidence。

evidence_detail_quality:
  判断完成 evidence 是否足够说明“做了什么、改了什么、验证了什么”。

smoke_result_json:
  不要求 smoke pass；
  要求 smoke fail/blocked 被诊断、修复或明确说明无法修复。
```

这三者职责必须分开，避免重新把 smoke 或 evidence detail 混进 `code_done_gate`。
