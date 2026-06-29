# 端到端测试用例生成参考

## 输入优先级

1. `proposal.md`
2. `specs/**/*.md`
3. `design.md`
4. `UNIT_TEST_RESULT.json`
5. `plan.json`
6. `evidence/EVIDENCE.jsonl`
7. `test-output.log`（仅排查时补充）
8. 直接相关的代码与配置

各输入用途：

- `proposal.md`：提取能力边界、影响面、非目标
- `specs/**/*.md`：提取 Requirement / Scenario 行为契约，作为 pass/fail 的主要行为依据
- `design.md`：提取接口决策、数据决策、成功与失败路径
- `UNIT_TEST_RESULT.json`：提取当前单测 verdict、scenarioCoverage、target 结果和回归优先级
- `plan.json`：提供任务顺序、taskId、specRefs、designRefs 和已有验证提示
- `evidence/EVIDENCE.jsonl`：提供已执行验证的 evidenceId 与 spec/design 回链
- `test-output.log`：只在排查失败或跳过原因时补充阅读
- 代码与配置：补充路由、入口 URL、测试数据、稳定断言点

`plan.json`、evidence 和代码上下文可以影响优先级与可执行性，但永远不是 pass/fail 依据。行为是否通过以 `specs/**/*.md` 为准，测试结论以 `E2E_RESULT.json` 为机器事实源。

## 硬规则

- 每轮 E2E 都必须生成 `.autobizdevops/features/{slug}/E2E_TEST_CASES.yaml`
- 每条用例都必须对应用户可见或系统外部可观察的最小操作流程
- 不生成伪 E2E 检查，例如文件存在、函数存在、或“任务写在 PLAN 里”
- 必须为 `specs/**/*.md` 中每个用户可见 Requirement / Scenario 生成最少一条用例
- proposal 中每个本轮能力边界必须能追溯到至少一个 specs 场景，或明确标记不适合 E2E
- `specs/**/*.md` 中每个属于用户主链路的 Requirement / Scenario 至少要被一条用例覆盖；API Decision 或 Data Decision 作为执行和断言上下文
- `specs_contract` 里的 Requirement / Scenario 统一使用稳定 ID，例如 `specs/<capability>/spec.md#REQ-001` 和 `#SCN-001`
- 每个用例必须标注 `execution_mode: browser | api | mixed | database_assisted`
- 每个用例必须标注 `ui_required: true | false`
- 当 Requirement / Scenario 涉及页面、按钮、点击、弹窗、跳转、表单、前端组件、路由或用户可见流程时，必须设置 `ui_required: true`
- 涉及 UI 的 P0 主链路必须以浏览器步骤开头，并包含页面最终状态断言
- API/database 只能作为补充证据，不能替代 UI-required 主用户路径
- 禁止把“Controller 存在”“代码实现”“函数存在”“文件存在”作为 E2E pass 依据
- 优先级定义：
  - `P0`：主流程、收入/安全/权限/数据一致性高风险流程
  - `P1`：重要辅助流程、常见失败路径、关键回归路径
  - `P2`：边缘场景与低频异常
- 一条用例只验证一个清晰目标；过大的流程要拆分
- 一条用例可以混合 `ui`、`api`、`database` 三类验证步骤
- `database` 只作补充证据，不单独充当业务价值是否通过的唯一依据
- 新生成用例的 `status` 一律为 `pending`

## 允许的验证类型

`verification.type` 只允许：

- `ui`
- `api`
- `database`

使用方式：

- `ui`：页面状态、表单提交、跳转、弹窗流程、列表结果
- `api`：状态码、响应体、权限错误、辅助 setup 或补充确认
- `database`：持久化副作用确认，或 API / UI 无法完成的 cleanup

## YAML 结构

```yaml
id: E2E-{slug}-001
status: pending
title: 用一句话描述测试目标
description: >
  说明这个用例验证什么用户价值或系统行为。
priority: P0 | P1 | P2
execution_mode: browser | api | mixed | database_assisted
ui_required: true | false

source:
  feature: {slug}
  proposal_capability:
    - comments
  design_contract:
    - api_decision: API-001
    - data_decision: DATA-001
  specs_contract:
    - spec: specs/comments/spec.md
      requirement: Requirement [REQ-001]: comment creation
      scenario: Scenario [SCN-001]: create comment successfully
  regression_risks:
    - 评论创建与列表刷新链路

preconditions:
  - 用户已登录
  - 测试组织存在

test_data:
  - name: comment_body
    value: hello from e2e

steps:
  - step: 1
    action: 打开评论列表页面
    expected: 页面展示评论列表区域和发表评论入口
    verification:
      type: ui
      details: 评论列表容器和“发表评论”入口可见

  - step: 2
    action: 输入评论内容并提交
    expected: 提交成功，无错误提示
    verification:
      type: api
      details: POST /api/comments 返回 201，响应体包含评论 ID

  - step: 3
    action: 刷新页面并查看最新评论
    expected: 新评论出现在列表中
    verification:
      type: database
      details: comments 表中存在 body=hello from e2e 的记录

cleanup:
  - 删除本用例创建的评论数据
```

## 字段约束

- `id`：必填，格式 `E2E-{slug}-{三位序号}`
- `status`：必填，新生成用例固定为 `pending`
- `title`：必填，且只能表达一个清晰测试目标
- `description`：必填，1 到 3 句话
- `priority`：必填，`P0 | P1 | P2`
- `execution_mode`：必填，`browser | api | mixed | database_assisted`
- `ui_required`：必填，`true | false`
- `source`：必填
- `preconditions`：必填；没有就写 `[]`
- `test_data`：可选
- `steps`：必填，至少 1 步
- `cleanup`：可选

### `source`

`source` 用于建立用例和 feature 输入之间的追溯关系。

- `feature`：必填
- `proposal_capability`：建议填写
- `specs_contract`：建议填写，指向 specs 文件、Requirement 和 Scenario；优先写稳定 ID
- `design_contract`：涉及 HTTP/API 或数据变更时建议填写 API/Data Decision
- `regression_risks`：当该用例来自当前回归风险时建议填写

### `steps`

每一步必须使用以下结构：

```yaml
- step: 1
  action: 执行动作
  expected: 可验证的预期结果
  verification:
    type: ui | api | database
    details: 如何机械地验证
```

约束：

- `step` 必须递增
- `action` 必须具体
- `expected` 必须是可观察结果
- `verification.details` 必须说明清楚如何验证
- 当 `ui_required: true` 时，至少一个步骤的 `verification.type` 必须为 `ui`
- 当 `execution_mode: browser` 或 `mixed` 时，第一步必须是打开目标页面或进入可观察 UI 入口

### `ui_required` 判定

以下任一情况必须写 `ui_required: true`：

- Requirement / Scenario 要求用户看到页面、卡片、列表、弹窗、表单、标题、提示或结果
- Requirement / Scenario 要求点击、输入、选择、提交、返回、关闭、跳转或路由变化
- 当前 feature 修改了前端页面、组件、路由、状态管理或 API client 且用户路径依赖这些改动

以下情况才允许写 `ui_required: false`：

- 当前 feature 是纯后端/API 能力，specs 没有用户可见页面或交互验收
- 当前用例只验证外部 API、批处理、定时任务或数据库副作用

即使 `ui_required: false`，用例仍必须对应外部可观察行为，不能退化成静态代码检查。

## 用例设计检查清单

生成完成前，确认：

- 目标是用户可见或外部可观察行为
- 用例能追溯到 specs Requirement / Scenario、API operation 或当前风险点
- 前置条件和测试数据写清楚了
- 每一步都有可执行断言
- 用例足够聚焦，失败时有单一主要原因
- 优先级符合业务影响和回归价值

## 最小正例

```yaml
id: E2E-comments-001
status: pending
title: 用户可成功创建评论并在列表中看到最新评论
description: >
  验证评论创建主流程，确保提交成功且最新评论可见。
priority: P0
execution_mode: mixed
ui_required: true
source:
  feature: comments
  proposal_capability:
    - comments
  specs_contract:
    - spec: specs/comments/spec.md
      requirement: Requirement [REQ-001]: comment creation
      scenario: Scenario [SCN-001]: create comment successfully
  design_contract:
    - api_decision: API-001
preconditions:
  - 用户已登录
test_data:
  - name: body
    value: hello from e2e
steps:
  - step: 1
    action: 打开评论页面并输入评论内容
    expected: 提交按钮可见且可点击
    verification:
      type: ui
      details: 评论输入框和提交按钮可见
  - step: 2
    action: 提交评论
    expected: 评论提交成功
    verification:
      type: api
      details: 创建评论请求返回 201
  - step: 3
    action: 刷新评论列表
    expected: 列表中出现最新评论
    verification:
      type: ui
      details: 列表中可见 hello from e2e
```
