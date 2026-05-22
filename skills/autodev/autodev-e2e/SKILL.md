---
name: autodev-e2e
description: 对单个 feature 执行端到端测试。作为 Autodev 根流程中的正式阶段，承接 autodev-utest 产物，输出 E2E_REPORT.md / e2e-run.log，并按 checkpoint 做 e2e_done / needs_fix 分支决策。默认由当前会话内联执行；可使用后台进程启动服务或运行长时间测试命令。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

# autodev-e2e — E2E 阶段技能

```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```

## 阶段定位

`autodev-e2e` 是 Dev 根流程中的正式阶段。根路由器只允许按以下顺序接入：

```text
unit_test_done
→ e2e_in_progress
→ e2e_done / needs_fix
→ verify_in_progress
```

本 skill 负责：

- 围绕单个 feature 的文档与直接相关代码，完成一轮可落盘、可执行、可追踪的 E2E 测试
- 对失败做 `code_fixable` / `environment_blocked` / `auth_blocked` / `data_blocked` / `unclear` 归因
- 仅当失败明确属于当前 feature 代码问题时，执行最小修复、运行 autodev-utest 生成的轻量单测、再重跑受影响 E2E
- 生成 `{工作目录}/E2E_REPORT.md`
- 生成 `{工作目录}/e2e-run.log`
- 通过统一脚本在 `STATE.md` 中把阶段推进到 `e2e_done` 或 `needs_fix`

## Checkpoint 契约

`.autobizdevops/STATE.md` 只允许更新当前 `{slug}` 对应的 Feature 行，不得改写其他 slug 的状态。
若 checkpoint 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

`needs_fix` 下必须在 `E2E_REPORT.md` 明确：

- 问题来源
- 建议回流阶段
- 下一步需要的人工动作

## 执行主体

本 skill 默认且只能由当前会话内联执行：

- 当前会话直接完成测试上下文建立、用例生成、服务启动、执行、最小修复闭环与报告更新。
- 不得把 E2E 阶段判断、测试设计、代码修复或报告编写委派给下级 agent或子代理。
- 启动前后端服务、运行 Playwright/浏览器或执行长时间测试命令时，允许使用后台进程；后台进程只用于运行服务或工具，不承担 agent 工作。

## 产物

对外编排契约中的主产物是：

- `{工作目录}/E2E_TEST_CASES.yaml`
- `{工作目录}/E2E_REPORT.md`
- `{工作目录}/e2e-run.log`
- `.autobizdevops/STATE.md`

以下文件如果保留，只视为 skill 内部实现细节，不参与根流程路由判断：

- `.autobizdevops/issues/active/ISSUE-{编号}-{简述}.md`
- `.autobizdevops/issues/completed/ISSUE-{编号}-{简述}.md`

## 输入边界

### 必读输入

读取以下 feature 文档：

- `.autobizdevops/features/{slug}/PRD.md`
- `.autobizdevops/features/{slug}/design.md`
- `.autobizdevops/features/{slug}/PLAN.md`
- `.autobizdevops/features/{slug}/REQUIREMENTS_EVAL.md`
- `.autobizdevops/features/{slug}/UNIT_TEST_REPORT.md`
- `.autobizdevops/features/{slug}/test-output.log`（可选；缺失时必须在 E2E_REPORT.md 中记录）

生成测试用例前，必须额外读取：

- [testcase-generation.md](reference/testcase-generation.md)

### 按需读取

- 与当前 feature 相关的 controller、service、repository、DTO、page、component 等代码与配置文件
- 如用户明确要求把结构化用例落成 Playwright/Cypress 等可执行测试资产，再额外读取 [test-playwright-script.md](reference/test-playwright-script.md)

### 项目约束预加载

生成测试上下文和启动服务前，直接从仓库代码、配置、流程产物和用户输入中推导项目约束：

1. 读取与当前 feature 相关的启动脚本、测试配置、路由、鉴权、登录态和浏览器访问方式
2. 优先参考 `.autobizdevops/features/{slug}/` 下已有流程产物中的约束和验证证据
3. 若用户提供了额外运行方式、环境变量或账号信息，按用户输入补充测试上下文
4. 对缺失信息基于代码和配置推导；无法可靠推导时，在 E2E 产物中记录假设或阻断原因

用途约束：

- `PRD.md`：验收标准、用户路径、边界、非目标
- `design.md`：行为规格、接口决策、数据决策、成功与失败路径、风险与待确认项
- `STATE.md`：当前 checkpoint
- `UNIT_TEST_REPORT.md` / `test-output.log`：上游单测覆盖、轻量单测命令线索和回归风险
- `REQUIREMENTS_EVAL.md`：需求覆盖、遗漏与风险提示
- `PLAN.md` 与代码上下文：只影响优先级与可执行性，不能单独充当 pass/fail 依据

## 写入边界

### 允许写入

- `.autobizdevops/features/{slug}/E2E_REPORT.md`
- `.autobizdevops/features/{slug}/e2e-run.log`
- `.autobizdevops/STATE.md`（`--feature` 模式）
- `.autobizdevops/features/{slug}/E2E_TEST_CASES.yaml`
- 可选内部产物：`.autobizdevops/issues/active/ISSUE-{编号}-{简述}.md`
- 可选内部产物：`.autobizdevops/issues/completed/ISSUE-{编号}-{简述}.md`
### 禁止写入

- 不要修改 `.autobizdevops/features/{slug}/PRD.md`
- 不要修改 `.autobizdevops/features/{slug}/design.md`
- 不要修改 `.autobizdevops/features/{slug}/PLAN.md`
- 不要修改 `.autobizdevops/features/{slug}/UNIT_TEST_REPORT.md`
- 不要修改 `.autobizdevops/features/{slug}/test-output.log`
- 不要修改 `.autobizdevops/features/{slug}/REQUIREMENTS_EVAL.md`
- 不要生成截图、trace、录像等二进制产物
- 默认不要改配置文件；允许做当前 feature 直接相关的最小代码修复
- 不要为通过 E2E 而弱化断言、删除用例、伪造报告，或改写 `PRD.md`、`design.md`、`PLAN.md`、`UNIT_TEST_REPORT.md`、`test-output.log`、`REQUIREMENTS_EVAL.md`

> 编排状态由 checkpoint 驱动，不再把旧的 E2E 局部状态块作为根流程主路由依据。

## 执行流程

### 1. 前置检查

先定位 feature 目录：`.autobizdevops/features/{slug}/`。


如果任一文件缺失：

1. 明确告诉用户缺失了哪些文件
2. 保持当前 checkpoint 不变
3. 立即结束 skill，不继续生成用例、不启动服务、不执行自动化

### 2. 写入阶段 checkpoint

使用统一脚本只更新当前 `{slug}` 对应行的 checkpoint 为 `e2e_in_progress`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint e2e_in_progress
```

### 3. 建立测试上下文并生成 E2E_TEST_CASES.yaml

按以下优先级建立上下文：

1. 已加载的项目约束
2. `PRD.md`
3. `design.md`
4. `UNIT_TEST_REPORT.md` / `test-output.log`
5. `REQUIREMENTS_EVAL.md`
6. `PLAN.md`
7. 与 feature 直接相关的代码与配置

每轮 E2E 都必须先生成或更新结构化测试用例，并写入：

```text
.autobizdevops/features/{slug}/E2E_TEST_CASES.yaml
```

用例生成规则：

- 每个用例必须包含 `id`、`status`、`title`、`priority`、`execution_mode`、`ui_required`、`source`、`preconditions`、`steps`。
- `execution_mode` 只能是 `browser`、`api`、`mixed`、`database_assisted`。
- `ui_required` 只能是 `true` 或 `false`。
- 当 `PRD.md`、`design.md`、`PLAN.md`、`UNIT_TEST_REPORT.md`、`REQUIREMENTS_EVAL.md` 或相关代码涉及页面、按钮、点击、弹窗、跳转、表单、前端组件、路由、用户可见流程时，相关 P0/P1 用例必须标记 `ui_required: true`。
- `design.md` 中属于用户主链路的 Behavior Spec、API Decision 或 Data Decision，至少要被一条 E2E 用例覆盖；无 API / 无数据库场景分别以 `x-auto-no-http-api: true`、`x-auto-no-sql: true` 作为上下文说明。
- `ui_required: true` 的 P0 主链路用例必须以浏览器步骤开始，并包含页面最终状态断言。
- API 和 database 步骤只能作为补充证据，不能替代用户路径。
- 不得把“Controller 存在”“代码已实现”“函数存在”“文件存在”作为 E2E 通过依据。


### 4. 推导启动方案与执行测试

启动项目时，必须优先使用项目约束中的启动命令；只有没有项目约束或项目约束没有覆盖启动方式时，才允许从代码和配置推导。

项目约束执行顺序：

1. 先执行项目约束中的后端/前端启动命令
2. 如项目约束定义了 E2E 鉴权、登录态注入、测试账号或浏览器访问方式，必须先完成该鉴权处理
3. 再执行 UI、API 或混合链路验证
4. 如上述项目约束缺失，才基于代码和配置推导启动与鉴权方案

无项目约束时，才按以下顺序推导：

- Java 后端：`mvn spring-boot:run`、`./mvnw spring-boot:run`、`./gradlew bootRun`
- 前端：`npm run dev`、`pnpm dev`、`yarn dev`
- 优先使用项目中的 local/dev/st/test 配置文件

启动顺序：

1. 先启动后端
2. 再启动前端
3. 等待端口、健康检查或首屏响应稳定
4. 按项目约束完成鉴权处理或确认当前用例不需要鉴权
5. 按 `E2E_TEST_CASES.yaml` 顺序执行 E2E 用例
6. `ui_required: true` 的用例必须使用 Playwright、浏览器插件或等价浏览器自动化能力执行主链路
7. `execution_mode: api` 的用例只能覆盖 API-only feature 或作为 UI 主链路的补充验证

UI 执行硬规则：

- 只要 `E2E_TEST_CASES.yaml` 中存在 `ui_required: true` 或 `verification.type: ui`，至少一条 P0 主链路必须有浏览器执行证据。
- 对 UI-required feature，只验证接口、curl、Python 请求或后端响应，不允许进入 `e2e_done`。
- 浏览器执行必须记录目标 URL、鉴权/登录态处理方式、关键动作、关键断言和结果。

如果测试、启动、鉴权、数据或执行环境失败，不要直接推进 `needs_fix`。只有明确不是可自动修复的代码问题，或修复闭环失败后，才进入 `needs_fix`。

服务启动证据规则：

- “已启动”只能在有证据时写入报告；执行过启动命令不等于已启动。
- 后端证据至少包含：启动命令、工作目录、后台任务 ID 或 PID、端口监听证据，或健康检查/API 探测成功记录。
- 前端证据至少包含：启动命令、工作目录、后台任务 ID 或 PID、端口监听证据，或首屏访问成功记录。
- 如果只是启动命令已发出，但端口未监听、日志未确认、探测未成功，状态必须写成 `not_verified` 或 `failed`，不能写“已启动”。
- 只有依赖的服务状态为 `pre_existing` 或 `started` 且有证据时，才允许继续执行依赖该服务的 E2E 用例。

鉴权处理证据规则：

- 如果项目约束或测试上下文要求认证、登录、SSO、token、cookie、session 或鉴权绕过，报告必须包含鉴权处理证据。
- 鉴权处理只有在页面加载成功、接口不再返回认证失败、关键元素可见或 API 探测成功时，才能写成 `bypassed` 或 `pre_authenticated`。
- 如果页面或接口因认证失败无法验证必须进入 `needs_fix`，或明确记录需要人工提供认证条件。

### 4.5 Failure Triage / 代码修复闭环

任何失败都必须修复。

- 如果一个失败同时包含代码问题和非代码阻断，优先按非代码阻断处理，直到阻断被可靠排除。
- 不得为了制造通过而弱化断言、删除覆盖、跳过核心路径、伪造启动/鉴权/执行证据。

执行准则：

1. 每轮必须读取 `E2E_REPORT.md`、`e2e-run.log` 和相关前端/后端代码。
2. 只做当前 feature 直接相关的最小代码修复。
3. 从 `UNIT_TEST_REPORT.md` 或 `test-output.log` 定位由 `/autodev-utest` 生成的相关轻量单测；优先精确到测试方法，其次运行相关测试类。
4. 修复后必须先运行该轻量单测；轻量单测失败时继续修复并重跑单测，不得进入 E2E 通过态。
5. 轻量单测通过后，重新执行受影响的 E2E 用例或最小可证明链路。
6. 覆盖更新 `E2E_REPORT.md` 与 `e2e-run.log`。
7. 在 `E2E_REPORT.md` 追加本轮修复尝试：轮次、失败分类、改动摘要、轻量单测命令、轻量单测结果、E2E 重跑命令、E2E 重跑结果。

停止规则：

- 修复后轻量单测与受影响 E2E 均真实重跑通过：进入 `e2e_done`。
- 达到 50 轮仍失败：进入 `needs_fix`。
- 修复风险过大、影响范围超出当前 feature、无法定位根因：进入 `needs_fix`。
- 重跑后失败变成非代码阻断：按新的失败分类进入 `needs_fix`。

### 5. 产出报告并做最终分支决策

`E2E_REPORT.md` 至少应包含：

- 执行范围
- E2E_TEST_CASES.yaml 摘要
- 服务启动证据
- 鉴权处理证据
- UI Execution Evidence / UI执行证据，如存在 `ui_required: true`
- Fix Attempts / 修复尝试
- 关键用例或链路摘要
- 通过项 / 失败项
- 失败对应的问题来源
- 建议回流阶段
- 下一步人工动作

服务启动证据小节必须分别记录 backend/frontend：

```text
## Service Startup Evidence / 服务启动证据

- service: backend
  status: pre_existing | started | failed | not_required | not_verified
  command: ...
  cwd: ...
  port: ...
  pid/task_id: ...
  evidence: ...

- service: frontend
  status: pre_existing | started | failed | not_required | not_verified
  command: ...
  cwd: ...
  port: ...
  pid/task_id: ...
  evidence: ...
```

状态含义：

- `pre_existing`: E2E 前服务已存在，且通过端口或 HTTP 探测确认可用。
- `started`: 本轮 E2E 启动了服务，且通过端口、日志或 HTTP 探测确认可用。
- `failed`: 启动或探测失败。
- `not_required`: 当前 E2E 用例不依赖该服务。
- `not_verified`: 无法确认服务可用。

如本轮涉及认证、登录态或鉴权绕过，必须增加鉴权处理证据小节：

```text
## Authentication Evidence / 鉴权处理证据

- auth_status: bypassed | pre_authenticated | not_required | failed | not_verified
  method: query | cookie | header | login endpoint | existing session | project constraint
  target user or identity: ...
  evidence: ...
```

状态含义：

- `bypassed`: 已按项目约束完成本地或测试环境鉴权绕过，并有页面/API 证据。
- `pre_authenticated`: E2E 前已存在可用登录态，并通过页面/API 证据确认。
- `not_required`: 当前 E2E 用例不依赖认证。
- `failed`: 鉴权处理失败，页面/API 因认证无法继续。
- `not_verified`: 无法确认鉴权处理是否成功。

如 `E2E_TEST_CASES.yaml` 中存在 `ui_required: true` 或 `verification.type: ui`，必须增加 UI 执行证据小节：

```text
## UI Execution Evidence / UI执行证据

- case_id: E2E-{slug}-001
  tool: Playwright | browser plugin | browser automation
  target_url: ...
  auth_method: ...
  actions: ...
  assertions: ...
  result: passed | failed | blocked | not_verified
```

状态含义：

- `passed`: 浏览器已执行关键用户动作，且页面最终状态断言通过。
- `failed`: 浏览器执行完成但断言失败。
- `blocked`: 浏览器、环境、鉴权或数据阻断导致无法执行。
- `not_verified`: 无法证明浏览器动作或断言真实执行。

如本轮执行过代码修复，必须增加修复尝试小节：

```text
## Fix Attempts / 修复尝试

- attempt: 1
  classification: code_fixable
  files_changed: ...
  fix_summary: ...
  lightweight_unit_test_command: ...
  lightweight_unit_test_result: passed | failed
  e2e_rerun_command: ...
  e2e_rerun_result: passed | failed
```

`e2e-run.log` 至少应包含：

- `E2E_TEST_CASES.yaml` 中执行过的 case id 与结果
- 服务启动或探测摘要
- 鉴权处理摘要，如本轮涉及认证、登录态或鉴权绕过
- UI 执行摘要，如本轮存在 `ui_required: true`
- 修复尝试摘要，如本轮执行过 `code_fixable` 修复，包含轻量单测与 E2E 重跑结果
- 关键执行日志
- 最终结果摘要

`e2e-run.log` 也必须包含 `Service Startup Evidence` 或 `服务启动证据`，并记录 backend/frontend 的 status、port、pid/task_id 或探测证据。

如本轮涉及认证、登录态或鉴权绕过，`e2e-run.log` 也必须包含 `Authentication Evidence` 或 `鉴权处理证据`，并记录 auth_status、method 和验证证据。

如 `E2E_TEST_CASES.yaml` 中存在 `ui_required: true` 或 `verification.type: ui`，`e2e-run.log` 也必须包含 `UI Execution Evidence` 或 `UI执行证据`，并记录 case id、browser/Playwright、target URL、action、assertion 和 result。


然后按结果分支：

#### 路径 A：全部通过 → `e2e_done`

- 只有所有依赖服务均为 `pre_existing` 或 `started` 且启动证据完整时，才允许进入本路径
- 如本轮涉及认证、登录态或鉴权绕过，只有鉴权处理证据为 `bypassed`、`pre_authenticated` 或 `not_required` 时，才允许进入本路径
- 如果 `E2E_TEST_CASES.yaml` 存在 `ui_required: true` 或 `verification.type: ui`，只有 UI 执行证据完整且至少一条 P0 浏览器主链路通过时，才允许进入本路径
- 如果经历过 `code_fixable` 修复闭环，最后一次相关轻量单测与受影响 E2E 重跑必须真实通过
- 使用统一脚本更新 `.autobizdevops/STATE.md` 中当前 `{slug}` 对应行：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint e2e_done
```

输出提示：

```text
## ✓ E2E 通过

E2E 测试已完成，报告与运行日志已写入工作目录。

checkpoint=e2e_done → 根路由器将继续调用下游阶段技能 autodev-verify
```

#### 路径 B：失败或阻断 → `needs_fix`

使用统一脚本将当前 `{slug}` 对应行更新为 `needs_fix`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint needs_fix
```

- 在 `E2E_REPORT.md` 中必须记录：
  - 问题来源
  - 建议回流阶段
  - 下一步需要的人工动作

输出提示：

```text
## ✗ E2E 需要修复

E2E 过程中发现失败或阻断，问题已记录到 E2E_REPORT.md。

→ 根路由器将读取 E2E_REPORT.md 中记录的问题来源与建议回流阶段，再决定回到 Biz / Plan / Code / Ops
```


## 崩溃恢复

恢复模式下：

1. checkpoint 停留在 `e2e_in_progress`
2. 用户重新调用 `/autodev-e2e`
3. 重新读取上游输入与已有日志
4. 允许覆盖 `E2E_REPORT.md` 与 `e2e-run.log`
5. 如已有失败记录，先恢复修复轮次计数
6. 再次做 `e2e_done / needs_fix` 分支决策

恢复必须保持幂等：除继续执行已记录的 `code_fixable` 修复闭环外，不会修改业务代码；任何情况下都不会改写上游只读产物。


## 成功标准

- 已完成前置检查
- 已生成 `E2E_TEST_CASES.yaml`
- 已生成 `E2E_REPORT.md`
- 已生成 `e2e-run.log`
- 已把状态写回 `.autobizdevops/STATE.md`
- 成功时推进到 `e2e_done`
- 如存在 UI-required 用例，报告和日志已记录浏览器执行证据
- 如发生失败，`E2E_REPORT.md` 与 `e2e-run.log` 记录
- 如执行过代码修复，`E2E_REPORT.md` 与 `e2e-run.log` 已记录修复尝试、轻量单测结果和 E2E 重跑结果
- 失败时推进到 `needs_fix`，且 `E2E_REPORT.md` 已记录问题来源、建议回流阶段、下一步人工动作
