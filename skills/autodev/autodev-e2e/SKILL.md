---
name: autodev-e2e
description: E2E 验证单个 Autodev feature 的真实用户主链路。用于 autodev-utest 完成后进入 E2E 阶段，或从 e2e_in_progress 恢复执行；生成 E2E_TEST_CASES.yaml、E2E_REPORT.md、e2e-run.log，并裁定 e2e_done 或 needs_fix。
---

# /autodev-e2e - 端到端测试

以**证据闭环**为完成标准：从 specs 推导用例，真实执行外部可观察链路，记录可复现证据，再更新 checkpoint。静态文件、函数或 Controller 的存在只能帮助定位，不能证明 E2E 通过。

## 运行契约

```text
FEATURE_DIR=${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}
```

写入：

- `${FEATURE_DIR}/E2E_TEST_CASES.yaml`
- `${FEATURE_DIR}/E2E_REPORT.md`
- `${FEATURE_DIR}/e2e-run.log`
- 当前 feature 直接相关的最小代码修复，仅限 `code_fixable`

保持上游产物只读，保留核心用例与断言，并以真实命令、退出码和执行结果形成证据。由当前会话完成上下文建立、用例生成、执行、归因、修复和报告；后台进程只运行服务或测试命令。

## 建立上下文

检查缺失输入并读取状态：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-e2e --feature "${feature}" --plain
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

按检查脚本给出的降级方式处理缺失输入。读取现有产物、项目约束和当前 feature 直接相关的代码与配置；以 `specs/**/*.md` 的 Requirement / Scenario 作为 pass/fail 的主要行为依据。

当 `CHECKPOINT=e2e_in_progress` 时进入恢复模式：读取已有三个 E2E 产物，恢复未完成用例和修复轮次，只覆盖 E2E 产物并继续已记录的修复闭环。

开始或恢复执行后写入并刷新状态：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 生成结构化用例

写入 `E2E_TEST_CASES.yaml` 前，读取 [`${pluginPath}/skills/autodev/autodev-e2e/reference/testcase-generation.md`](reference/testcase-generation.md)，按其中的字段、追溯和优先级规则生成或更新用例。

保持以下核心约束：

- 每个用例追溯到 specs Requirement / Scenario；API Decision 和 Data Decision 只提供执行与断言上下文。
- 涉及页面、按钮、表单、弹窗、跳转、路由或其他用户可见流程的 P0/P1 用例设置 `ui_required: true`。
- `ui_required: true` 的 P0 主链路从浏览器入口开始，以页面最终状态断言结束。
- API 和数据库步骤只补充证据，不替代 UI 主链路。

**完成标准：** 用户主链路的每个 Requirement / Scenario 均有用例覆盖；每条用例结构完整、状态明确；所有 UI-required P0 用例都包含浏览器动作和最终 UI 断言。

## 准备可执行环境

按以下优先级确定启动与鉴权方案：

1. 使用系统提示词给出的启动命令、测试账号、登录态注入和浏览器访问方式。
2. 使用项目已有的 E2E 配置、脚本和 local/dev/st/test 配置。
3. 仅在前两项未覆盖时，从代码和配置推导最小方案。

先检查服务是否已存在；需要启动时先后端、再前端。用端口监听、健康检查、API 探测或首屏响应验证可用性。服务状态只使用：

- `pre_existing`：启动前已存在且探测成功。
- `started`：本轮启动且探测成功。
- `failed`：启动或探测失败。
- `not_required`：用例不依赖该服务。
- `not_verified`：缺少可用性证据。

需要认证时，执行项目约定的登录或绕过方案，再通过页面加载、关键元素或 API 响应验证登录态。鉴权状态只使用 `bypassed`、`pre_authenticated`、`not_required`、`failed`、`not_verified`。

当项目缺少当前用例所需的 Playwright spec、fixture、helper 或 page object 时，读取 `reference/test-playwright-script.md`，只补齐最小可执行资产；将 `E2E_TEST_CASES.yaml` 视为其中所称的结构化输入。

**完成标准：** 每项依赖服务都有状态和探测证据；需要鉴权的用例已有验证成功的登录态；测试命令与入口 URL 已确定。依赖项为 `failed` 或 `not_verified` 时转入闭合失败步骤。

## 执行并记录

按 `E2E_TEST_CASES.yaml` 顺序执行用例，并持续写入 `e2e-run.log`：

- 记录时间、工作目录、命令、退出码、case id、关键输出和结果。
- 记录 backend/frontend 的状态、端口、PID 或任务 ID、探测证据。
- 涉及鉴权时记录 auth status、方法、身份和验证证据。
- `ui_required: true` 的用例使用 Playwright、浏览器插件或等价浏览器自动化，记录 URL、关键动作、断言和结果。
- 将每条已执行用例的 `status` 更新为 `passed`、`failed` 或 `blocked`。

**完成标准：** 每条计划用例均有执行状态和日志位置；每条 UI-required P0 用例都有浏览器执行证据，或有明确的阻断分类与复现证据。

## 闭合失败

先复现并归因，再选择动作：

| 分类 | 判定 | 动作 |
|------|------|------|
| `code_fixable` | 当前 feature 代码不满足已确认契约 | 做最小修复，运行相关轻量单测，再重跑受影响 E2E |
| `environment_blocked` | 服务、依赖、网络或执行环境阻断 | 记录探测与复现证据，给出 Ops 回流建议 |
| `auth_blocked` | 账号、权限或登录态阻断 | 记录身份、方法与失败证据，说明所需认证条件 |
| `data_blocked` | 测试数据或数据状态阻断 | 记录缺失数据与最小准备条件 |
| `unclear` | 无法可靠定位 | 停止猜测性修复，记录已排除项和下一步定位建议 |

同时存在代码问题和非代码阻断时，先排除阻断，再判定代码问题。需要用户提供无法自行取得的信息时，读取 `${pluginPath}/skills/references/ask-user-question.md` 后提问。

对 `code_fixable` 执行闭环：

1. 只修改当前 feature 直接相关的最小代码区域。
2. 从 `UNIT_TEST_REPORT.md` 或 `test-output.log` 定位相关测试方法；没有方法级入口时使用相关测试类。
3. 轻量单测通过后重跑受影响 E2E。
4. 在报告中记录轮次、分类、改动、两条命令及结果。
5. 重复至通过；达到 50 轮、风险超出当前 feature、根因仍不清或阻断转为非代码类型时停止。

**完成标准：** 每个失败均已通过单测和 E2E 证据闭合，或已形成稳定分类、复现证据和回流建议；不存在未经归因的失败。

## 报告并裁定

覆盖更新 `E2E_REPORT.md`，至少包含：

- Feature、执行范围、verdict 和用例摘要。
- specs 到用例与结果的追溯。
- 服务启动、鉴权和 UI 执行证据；不适用项明确写 `not_required`。
- 失败归因、修复尝试、单测与 E2E 重跑结果。
- 问题来源、建议回流阶段和下一步人工动作。

仅当以下条件全部满足时推进 `e2e_done`：

- 三个 E2E 产物已写入且互相一致。
- 所有 P0 用例通过，P1 没有未解释的失败。
- 所需服务状态为 `pre_existing`、`started` 或 `not_required`，且证据完整。
- 鉴权状态为 `bypassed`、`pre_authenticated` 或 `not_required`。
- UI-required feature 至少一条 P0 浏览器主链路通过。
- 每次代码修复都有相关轻量单测与 E2E 重跑通过证据。

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

存在失败、阻断或未闭合修复时，在报告中记录问题来源与建议回流阶段后推进 `needs_fix`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**完成标准：** `E2E_REPORT.md` 和 `e2e-run.log` 支撑最终 verdict；刷新后的 checkpoint 与 verdict 一致。

## 完成交接

技能完成后，提醒用户回到特性面板新开对话。若用户随后在当前对话输入“继续”或“下一步”，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；技能未完成时继续执行上述步骤。
