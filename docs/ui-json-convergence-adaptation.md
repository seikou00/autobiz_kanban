# UI JSON 收口适配方案

## 目标

将“是否需要前端 UI”从 Markdown 关键词判断收口为结构化 JSON 判断。

核心变化：

- 新增 `UI_CONTEXT.json` 作为 UI 范围唯一机器事实源。
- `PRD.md`、`proposal.md`、`specs/**/*.md` 继续描述业务和行为契约，但不作为 UI 范围的机器判断入口。
- `/autodev-frontend` 不恢复；前端实现继续并入 `/autodev-code` 的内部 route。
- `FRONTEND_ROUTE.json` 只表示 code 阶段实现路线，不负责判断 feature 是否需要 UI。
- `VERIFY_DECISION.json` 最终明确区分 `ui_required`、`not_applicable`、`missing` 和 `manual`。

## 总体原则

1. `uiRequired` 是需求范围判断，不是 code 阶段临时判断。
2. HTML、设计稿、原型链接是实现输入，不是需求正文，也不是行为契约源。
3. specs 仍是行为契约源；UI 范围、页面、交互、视觉输入进入 JSON。
4. 非 UI capability 不生成前端任务，不走 UI 验收。
5. UI capability 必须在 E2E/verify 中显式闭环，不能只靠单测或 Markdown 报告宣称通过。

## 核心产物

路径：

```text
.autobizdevops/features/{feature}/UI_CONTEXT.json
```

最小结构：

```json
{
  "version": 1,
  "featureId": "feature-demo",
  "uiRequired": true,
  "decisionStatus": "locked",
  "decisionSource": "user_confirmed",
  "confirmedAtCheckpoint": "prd_done",
  "lockedAtCheckpoint": "specs_done",
  "notApplicableReason": "",
  "pages": [],
  "interactions": [],
  "visualSources": [],
  "capabilities": []
}
```

字段说明：

| 字段 | 作用 |
| --- | --- |
| `version` | schema 版本，当前固定为 `1`。 |
| `featureId` | 当前 feature 标识。 |
| `uiRequired` | 当前 feature 是否包含 UI 范围。 |
| `decisionStatus` | UI 范围决策状态：`defaulted`、`confirmed`、`locked`。 |
| `decisionSource` | 决策来源：`user_confirmed`、`prd_inferred`、`default_false`、`legacy_import`。 |
| `confirmedAtCheckpoint` | UI 范围在哪个 checkpoint 完成确认。 |
| `lockedAtCheckpoint` | UI 范围在哪个 checkpoint 固化。进入 specs 后应固化。 |
| `notApplicableReason` | `uiRequired=false` 时说明为什么 UI 不适用。 |
| `pages[]` | UI 页面分母。 |
| `interactions[]` | UI 交互分母。 |
| `visualSources[]` | HTML、设计稿、原型链接等实现输入。 |
| `capabilities[]` | UI capability 到 page、interaction、specRefs 的映射。 |

建议扩展示例：

```json
{
  "version": 1,
  "featureId": "order-create",
  "uiRequired": true,
  "decisionStatus": "locked",
  "decisionSource": "user_confirmed",
  "confirmedAtCheckpoint": "prd_done",
  "lockedAtCheckpoint": "specs_done",
  "notApplicableReason": "",
  "pages": [
    {
      "pageId": "PAGE-001",
      "name": "订单创建页",
      "goal": "让用户创建订单",
      "routeHint": "/orders/new",
      "states": ["loading", "empty", "error", "success"]
    }
  ],
  "interactions": [
    {
      "interactionId": "UIX-001",
      "pageId": "PAGE-001",
      "summary": "提交订单表单",
      "stateRefs": ["loading", "error", "success"],
      "specRefs": ["specs/order-create-ui/spec.md#SCN-001"]
    }
  ],
  "visualSources": [
    {
      "sourceId": "VIS-001",
      "type": "high_fidelity_html",
      "path": ".autobizdevops/features/order-create/frontend-html/order.html",
      "route": "absolute-html",
      "required": true
    }
  ],
  "capabilities": [
    {
      "capabilityId": "order-create-ui",
      "uiRequired": true,
      "pageRefs": ["PAGE-001"],
      "interactionRefs": ["UIX-001"],
      "specRefs": [
        "specs/order-create-ui/spec.md#REQ-001",
        "specs/order-create-ui/spec.md#SCN-001"
      ]
    }
  ]
}
```

## 任务 1：新增 UI_CONTEXT.json 契约

目标：建立 UI 范围唯一机器事实源。

改动内容：

- 新增 `UI_CONTEXT.json` schema validator。
- discuss 阶段生成 `UI_CONTEXT.json` 初稿。
- PRD 阶段确认 `uiRequired`，将 `decisionStatus` 推进到 `confirmed`。
- specs 阶段固化 UI 决策，将 `decisionStatus` 推进到 `locked`。
- `uiRequired=true` 时要求至少存在页面、交互或 UI capability。
- `uiRequired=false` 时要求写 `notApplicableReason`。

阶段规则：

- `PRD_DISCUSS.md` 可以记录人类讨论，包括是否有页面、页面数、核心交互、空态/错误态、高保真 HTML 是否存在。
- 高保真 HTML、标准 HTML、设计稿、原型链接只进入 `visualSources[]`，不混入 PRD 正文。
- PRD 只描述 UI 行为范围，不描述实现。
- specs 负责沉淀可观察行为，UI 范围由 `UI_CONTEXT.json` 表达。

验收标准：

- 缺失 `UI_CONTEXT.json` 时，进入 specs/plan/code 应提示补齐。
- `decisionStatus=defaulted` 不允许直接进入 code。
- UI 范围不再靠 `PRD.md` 或 specs 文本关键词判断。

## 任务 2：Plan 阶段投影 UI 字段

目标：让 `plan.json` task 明确区分 UI 任务和非 UI 任务。

扩展 `plan.json.tasks[]`：

```json
{
  "id": "T003",
  "title": "实现订单创建页",
  "status": "todo",
  "uiRequired": true,
  "uiRefs": {
    "pageRefs": ["PAGE-001"],
    "interactionRefs": ["UIX-001"],
    "visualSourceRefs": ["VIS-001"],
    "frontendRoute": "absolute-html"
  },
  "specRefs": [
    "specs/order-create-ui/spec.md#REQ-001",
    "specs/order-create-ui/spec.md#SCN-001"
  ],
  "designRefs": ["design.md#API-001"],
  "apiIds": ["API-001"],
  "dataIds": ["DATA-001"],
  "decisionIds": ["D-001"],
  "evidenceIds": [],
  "expectedFiles": ["src/pages/orders/CreateOrder.tsx"],
  "blockers": [],
  "validationCommands": [
    { "command": "npm test -- order-create" }
  ]
}
```

改动内容：

- 扩展 `plan.json` validator。
- 新增 `plan_ui_projection` validator。
- `tasks[].uiRequired=true` 时，`uiRefs` 必须能回链 `UI_CONTEXT.json`。
- `UI_CONTEXT.uiRequired=false` 时，不允许出现 UI task。
- 纯后端任务不得夹带前端实现工作。

`uiRefs.frontendRoute` 推荐取值：

| 值 | 含义 |
| --- | --- |
| `none` | 无 UI。 |
| `spec-driven-ui` | 有 UI，但没有 HTML/设计稿，按 specs/design 实现。 |
| `absolute-html` | 高保真、绝对定位或 Figma/MasterGo 导出类 HTML。 |
| `standard-html` | 普通静态 HTML、复制 DOM、小型静态站点或 HTML 转 React。 |
| `missing-html` | 明确要求 HTML 输入，但 HTML 缺失或不可读。 |

验收标准：

- UI feature 只生成对应 UI task。
- 非 UI feature 不生成前端任务。
- plan task 的 `uiRefs` 都能在 `UI_CONTEXT.json` 中找到。

## 任务 3：Code 阶段融合前端 Route

目标：让 `/autodev-code` 根据结构化 UI 信息选择实现路线。

当前 `FRONTEND_ROUTE.json` 继续保留，但职责调整为 code 阶段 route evidence。

建议结构：

```json
{
  "version": 1,
  "feature": "order-create",
  "uiRequired": true,
  "route": "absolute-html",
  "source": "UI_CONTEXT.json",
  "visualSourceIds": ["VIS-001"],
  "htmlSourcePaths": [
    ".autobizdevops/features/order-create/frontend-html/order.html"
  ],
  "routeSkillPath": "skills/autodev/autodev-code/deps/frontend-html/with-absolute-html/SKILL.md",
  "parserPath": "skills/autodev/autodev-code/deps/frontend-html/with-absolute-html/deps/html-parser.md",
  "routeSkillRead": true,
  "routeSkillReadComplete": true,
  "routeTodosCreated": true,
  "routeTodosCompleted": true,
  "parserRead": true
}
```

改动内容：

- 修改 `resolve_frontend_html_route.py`：
  - 优先读取 `UI_CONTEXT.json`。
  - 再读取 `plan.json.tasks[].uiRefs`。
  - Markdown 关键词扫描只作为迁移兜底。
- 扩展 `FRONTEND_ROUTE.json`：
  - 增加 `uiRequired`。
  - 增加 `source`。
  - 增加 `visualSourceIds`。
- 修改 `frontend_route_write_guard.py`：
  - `uiRequired=false` 时阻断前端业务代码写入。
  - `spec-driven-ui` 允许写前端代码，但不要求 HTML parser。
  - `absolute-html` / `standard-html` 继续要求 route skill、todos、parser。
  - `missing-html` 阻断，要求补充 HTML 或回到需求/plan 修改输入。

验收标准：

- 有高保真 HTML 时走 `absolute-html`。
- 有标准 HTML 时走 `standard-html`。
- 有 UI 但无 HTML 时走 `spec-driven-ui`。
- 无 UI 时不能误触发前端 route。

## 任务 4：Review / Test / E2E 补 UI 投影

目标：让评审和测试明确知道哪些任务和场景属于 UI。

`REVIEW_FINDINGS.json.findings[]` 建议扩展：

```json
{
  "id": "R001",
  "taskId": "T003",
  "specRefs": ["specs/order-create-ui/spec.md#SCN-001"],
  "evidenceIds": ["ev_0003"],
  "severity": "high",
  "message": "订单创建页错误态未实现",
  "uiRequired": true,
  "pageRefs": ["PAGE-001"],
  "interactionRefs": ["UIX-001"],
  "visualSourceRefs": ["VIS-001"],
  "suggestedCheckpoint": "code_in_progress"
}
```

`UNIT_TEST_RESULT.json.targets[]` 建议扩展：

```json
{
  "targetId": "UT-003",
  "taskId": "T003",
  "uiRequired": true,
  "specRefs": ["specs/order-create-ui/spec.md#SCN-001"],
  "evidenceIds": ["ev_0004"],
  "result": "PASS",
  "command": "npm test -- order-create",
  "coverage": { "lines": 84 }
}
```

`E2E_RESULT.json.cases[]` 建议扩展：

```json
{
  "caseId": "E2E-order-001",
  "taskId": "T003",
  "uiRequired": true,
  "pageRefs": ["PAGE-001"],
  "interactionRefs": ["UIX-001"],
  "visualSourceRefs": ["VIS-001"],
  "specRefs": ["specs/order-create-ui/spec.md#SCN-001"],
  "evidenceIds": ["ev_0005"],
  "executionMode": "automated",
  "steps": [
    { "action": "open /orders/new", "expected": "form visible", "result": "PASS" }
  ],
  "verdict": "PASS"
}
```

改动内容：

- `REVIEW_FINDINGS.json.findings[]` 支持 `uiRequired`、`pageRefs`、`interactionRefs`、`visualSourceRefs`。
- `UNIT_TEST_RESULT.json.targets[]` 支持 `uiRequired`。
- `E2E_RESULT.json.cases[]` 的 `uiRequired` 与 `UI_CONTEXT.json`、`plan.json` 做一致性校验。
- UI capability 必须进入 E2E coverage，或显式标记 `manual` / `missing`。

验收标准：

- UI task review 会检查页面行为、状态、交互边界和视觉来源引用。
- 非 UI task 不触发 UI 检查。
- UI scenario 不允许只靠单测宣称最终通过。
- E2E 的 `uiRequired` 必须和 `UI_CONTEXT.json` / `plan.json` 一致。

## 任务 5：Verify 收口 UI 验收结果

目标：最终验收明确区分 UI 必需、UI 不适用、UI 缺失、人工验证。

扩展 `VERIFY_DECISION.json`：

```json
{
  "version": 1,
  "verdict": "pass",
  "nextCheckpoint": "verify_done",
  "passedScenarioRefs": ["SCN-001"],
  "failedScenarioRefs": [],
  "manualVerificationRefs": [],
  "missingScenarioRefs": [],
  "evidenceIds": ["ev_0005"],
  "scenarioCoverage": [
    {
      "scenarioRef": "SCN-001",
      "verdict": "pass",
      "evidenceIds": ["ev_0005"],
      "uiApplicability": "required"
    },
    {
      "scenarioRef": "SCN-010",
      "verdict": "pass",
      "evidenceIds": ["ev_0002"],
      "uiApplicability": "not_applicable"
    }
  ],
  "uiSummary": {
    "uiRequired": true,
    "passedUiScenarioRefs": ["SCN-001"],
    "failedUiScenarioRefs": [],
    "manualUiScenarioRefs": [],
    "missingUiScenarioRefs": [],
    "notApplicableScenarioRefs": ["SCN-010"]
  }
}
```

`scenarioCoverage[].uiApplicability` 取值：

| 值 | 含义 |
| --- | --- |
| `required` | 该场景需要 UI 验证。 |
| `not_applicable` | 该场景不适用 UI 验证。 |
| `manual` | 该 UI 场景需要人工验证。 |
| `missing` | 该 UI 场景缺少覆盖或证据。 |

失败回流时，`FIX_REQUEST.json` 建议增加 `failedUiRefs`：

```json
{
  "failedUiRefs": {
    "pageRefs": ["PAGE-001"],
    "interactionRefs": ["UIX-001"],
    "visualSourceRefs": ["VIS-001"]
  }
}
```

改动内容：

- 新增 `verify_ui_summary` validator。
- `VERIFY_DECISION.json.uiSummary.uiRequired` 必须等于 `UI_CONTEXT.uiRequired`。
- UI scenario pass 必须有 E2E 或人工验证 evidence。
- 非 UI scenario 进入 `notApplicableScenarioRefs`。
- `FIX_REQUEST.json` 增加 `failedUiRefs`，用于失败回流定位。

验收标准：

- `VERIFY_DECISION.json` 可以明确回答哪些 UI 场景通过、失败、缺失、需人工验证。
- `uiRequired=false` 的 feature 输出 `uiSummary.uiRequired=false`。
- 非 UI 场景不会被误判为 UI missing。
- CI/CD 继续只读 `VERIFY_DECISION.json` 判断准入。

## 推荐实施顺序

1. 新增 `UI_CONTEXT.json` schema、validator 与阶段产物契约。
2. 扩展 `plan.json`，增加 UI task 投影和 `plan_ui_projection` validator。
3. 改造 `/autodev-code` route，优先读取 `UI_CONTEXT.json` 与 `plan.json`。
4. 扩展 review、utest、e2e JSON，补 UI 字段和一致性校验。
5. 扩展 verify、fix request、cicd 准入摘要，完成 UI 验收闭环。

## 最终验收清单

- `UI_CONTEXT.json` 是 UI 范围唯一机器事实源。
- `FRONTEND_ROUTE.json` 只表示 code 阶段 route evidence。
- `uiRequired=false` 不生成 UI capability、UI task 或 UI E2E。
- `uiRequired=true` 且有高保真 HTML 时走 `absolute-html`。
- `uiRequired=true` 且有标准 HTML 时走 `standard-html`。
- `uiRequired=true` 且无 HTML 时走 `spec-driven-ui`。
- UI scenario pass 必须有能覆盖同一 SCN 的 evidence。
- verify 输出 `uiSummary`，并能区分 `required`、`not_applicable`、`manual`、`missing`。
- Markdown 报告只做人类说明，不作为 UI 范围或最终准入的机器事实源。
