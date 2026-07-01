---
name: autodev-verify
description: 读取上游阶段技能 autodev-utest 与 autodev-e2e 产出的单测、E2E 报告，以及 proposal/specs/design 契约，汇总生成 VERIFY_REPORT.md 并做最终 verify_done / needs_fix 分支决策；若当前 Feature 的实际代码改动涉及新增或修改接口，额外基于代码生成 FEATURE_API_DETAIL.md。不再自己生成测试、不再启动服务、不再执行命令验证。支持 --feature 多人协作。默认由当前会话内联执行。
version: v1.1.1604
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `{PLUGIN_ROOT}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-verify --feature "{FEATURE_ID}" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）。
- **方法优先**：每个 input 的 `extract.method` 是它在场时的专属指令，优先于技能正文的通用默认。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止。
- **不列即不存在**：bundle 未列出的 id 不属于本工作流，一律不予考虑——不读、不等、不索要，也不要为其设想任何分支。
- **降级语义**：`required: false` 的输入缺失时按其 `extract.degrade` 继续，不要因缺失而停止。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-verify — 验收汇总 + 分支决策


## 执行主体

本 skill 默认且只能由当前会话内联执行：

- 当前会话直接读取 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md`、`e2e-run.log`、`proposal.md`、`specs/**/*.md`、`design.md`，生成 `VERIFY_REPORT.md` 并做最终分支决策。
- 当前 Feature 的实际代码改动涉及新增或修改接口时，额外生成 `FEATURE_API_DETAIL.md`；该文件是可选额外交付物，不属于流程契约门禁。
- 不得把验收汇总或分支决策委派给下级 agent或子agent。

本 skill 负责记录失败事实、问题来源和建议回流阶段，不默认把问题绑定回 Biz。

---

## Step 1: 前置检查

**当前 Feature **

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## Step 2: 写入 Checkpoint（标记开始）

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint verify_in_progress
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## Step 3: 提取验收契约

按「流程契约」一节取本 Feature 的 Source Bundle，对其中每个输入按 `extract` 抽取；`required: false` 的输入缺失时按其降级读法处理。bundle 未列出的输入不读取：其所属阶段在契约 JSON 的 `workflow.workflowSkippedNodes` 中（中途跳过）或不在当前工作流链中时，在报告中标注「该阶段已跳过 / 不在本工作流」，而非「缺失」。

从 `{FEATURE_DIR}/proposal.md` 提取本轮能力边界、影响面和非目标。

从 `{FEATURE_DIR}/specs/**/*.md` 提取每个 Requirement / Scenario：

```
specs/[capability]/spec.md / Requirement / Scenario
...
```

共 M 项待裁决。

按各输入的 Method Bundle 提取验收验证项：

- 从行为契约 Requirement / Scenario 提取行为验证项 C1, C2, ...
- 从在场的设计决策提取接口/数据契约验证项；遇 `x-auto-no-http-api: true` / `x-auto-no-sql: true` 记录本轮无对应验证项。

### ⛔ 步骤完成检查 — Step 3
- [ ] 已从上游行为契约提取所有待验收行为并编号 1..m
- [ ] 设计决策在场时：已提取 API/数据契约验证项或确认 `x-auto-no-http-api: true` / `x-auto-no-sql: true`；缺失时按其 degrade 标注设计基准缺失
- [ ] 共 M 项已列出

---

## Step 4: 读取单测与 E2E 报告（证据源，不执行命令）

> ✋ **本步骤严格只读。** 不得运行 `npm test` / `pytest` / `mvn test` / Playwright 等任何测试命令，不得启动任何服务，不得再生成新的测试代码。所有测试证据来自上游阶段产物。

**证据文件（以 bundle 为准；bundle 未列出的证据文件不读取，按 Step 3 的约定在报告中标注所属阶段状态）：**

1. `{FEATURE_DIR}/UNIT_TEST_REPORT.md` — 上游阶段技能 `autodev-utest` 产出的结构化单测报告。
2. `{FEATURE_DIR}/test-output.log` — 单测执行的原始日志（通过/失败数量、失败堆栈；缺失时记录）。
3. `{FEATURE_DIR}/E2E_TEST_CASES.yaml` — 上游阶段技能 `autodev-e2e` 产出的结构化 E2E 用例。
4. `{FEATURE_DIR}/E2E_REPORT.md` — E2E 结果、失败归因、修复尝试与重跑摘要。
5. `{FEATURE_DIR}/e2e-run.log` — E2E 原始运行日志、服务/鉴权/UI 执行证据。

**从 `UNIT_TEST_REPORT.md` 中抽取（按 Method Bundle 的 `extract` 抽取）：**

- 每个 Requirement / Scenario 对应的测试方法名 / 文件路径 / 执行结果（PASS/FAIL/SKIP）
- 每个 specs/design 契约验证项 `Cn` 对应的验证结果（如报告涵盖）
- 整体通过率（P/M、P/K）

**从 `E2E_REPORT.md` 与 `e2e-run.log` 中抽取（按 Method Bundle 的 `extract` 抽取）：**

- 每个 E2E 用例的执行结果（PASS/FAIL/BLOCKED/SKIP）
- 服务启动证据、鉴权处理证据、UI Execution Evidence / UI执行证据
- 失败归因、问题来源、建议回流阶段
- 如执行过代码修复，轻量单测命令、轻量单测结果、E2E 重跑命令与重跑结果

**从 test-output.log 中抽取：**

- 通过测试数、失败测试数、跳过数
- 每个失败测试的错误摘要（首行 + 堆栈关键行）

**映射规则：**

| 上游报告中的结论 | 本 skill 对应验收项的裁定 |
|-------------------------|------------------------|
| PASS | ✓ 通过 |
| FAIL / BLOCKED（有明确失败或阻断证据） | ✗ 失败 |
| SKIP / NO_TEST（该 Requirement / Scenario 无自动化测试） | ⚠ 需人工验证 |

> 若上游报告存在但格式与约定不符（找不到结论字段等），**不要尝试用测试命令补齐**，直接在报告中标注"报告格式异常"并将相关项置为 "⚠ 需人工验证"。

### ⛔ 步骤完成检查 — Step 4
- [ ] 已读取 `UNIT_TEST_REPORT.md`（若存在）
- [ ] 已读取 `{FEATURE_DIR}/test-output.log`（若存在；缺失已记录）
- [ ] 已读取 `E2E_TEST_CASES.yaml`、`E2E_REPORT.md` 与 `e2e-run.log`
- [ ] 已为每个步骤建立 PASS / FAIL / 需人工验证 的裁定
- [ ] 未执行任何测试命令、未启动任何本地服务、未生成任何测试文件

---

## Step 5: 生成 VERIFY_REPORT.md（纯汇总）

将裁定结果写入 `{FEATURE_DIR}/VERIFY_REPORT.md`。**不得**在 VERIFY_REPORT.md 中夹带新的命令输出、新的测试代码、新的 HTTP 响应证据——这些应由 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md` 与 `e2e-run.log` 提供，VERIFY_REPORT.md 只做"映射 + 归档"。

**模板：**

```markdown
# 验证报告

- **Feature:** {slug}
- **验证时间:** [当前时间]
- **上游单测报告:** {FEATURE_DIR}/UNIT_TEST_REPORT.md
- **上游单测日志:** {FEATURE_DIR}/test-output.log
- **上游 E2E 报告:** {FEATURE_DIR}/E2E_REPORT.md
- **上游 E2E 日志:** {FEATURE_DIR}/e2e-run.log

## 验证总览

| # | Specs Requirement / Scenario | 裁定 | 证据来源 |
|---|---------|------|---------|
| 1 | [Requirement / Scenario 1] | ✓ 通过 | UNIT_TEST_REPORT + E2E_REPORT |
| 2 | [Requirement / Scenario 2] | ✗ 失败 | E2E_REPORT（FAIL: AssertionError ...） |
| 3 | [Requirement / Scenario 3] | ⚠ 需人工验证 | 报告未覆盖（UI 类） |

通过: N/M | 失败: K/M | 需人工验证: J/M

## Specs / Design Contract 验证（若适用）

| # | Contract Item | 裁定 | 证据来源 |
|---|-----------|------|---------|
| 1 | specs/[capability]/spec.md / Requirement / Scenario / API-01 / DATA-01 | ✓ 通过 | UNIT_TEST_REPORT §C1 + E2E_REPORT §E2E-001 |

或：本轮 `x-auto-no-http-api: true`，无 HTTP/API 契约验证项；`x-auto-no-sql: true`，无数据库变更验证项。

## 失败详情（如有）

### [Requirement / Scenario]
- **上游测试方法:** [文件路径::方法名]
- **失败摘要:** [错误首行 + 堆栈关键行]
- **预期 vs 实际:** [若上游报告给出则直接引用]
- **建议修复方向:** [由本 skill 结合 specs/design 给出简短方向]

## 需人工验证详情（如有）

### [Requirement / Scenario]
- **原因:** UI/UX 或业务语义类标准，自动化覆盖不足
- **请用户手动验证:** [给出具体操作建议]

## 结论

- 通过率: XX%
- 分支决策: verify_done / needs_fix / 等待用户人工确认

## 接口详细说明文档

- 本 Feature 涉及新增或修改接口，已生成：`FEATURE_API_DETAIL.md`

或：

- 未从当前 Feature 实际代码改动中发现新增或修改接口，因此未生成 `FEATURE_API_DETAIL.md`。

或：

- 上游设计提到接口变更，但当前代码改动中未能确认接口入口或请求响应定义，因此未生成 `FEATURE_API_DETAIL.md`。请人工确认是否需要补充接口详细说明。
```

### ⛔ 步骤完成检查 — Step 5
- [ ] `{FEATURE_DIR}/VERIFY_REPORT.md` 已写入
- [ ] 报告中每项都标注了证据来源（指向 UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log 的段落或说明为何需人工验证）
- [ ] 报告**不包含**本 skill 自行执行的测试命令输出或服务启动日志
- [ ] 报告已展示给用户

---

## Step 6: 生成 FEATURE_API_DETAIL.md（可选）

仅当当前 Feature 的实际代码改动涉及新增或修改接口时执行。该文件是可选额外交付物，不影响 `verify_done` / `needs_fix` 分支判断。

> 本步骤允许执行只读 git 命令和读取代码文件；仍然不得运行测试、不得启动服务、不得修改业务代码。

### 6.1 判定是否需要生成

必须先查看当前 Feature 的实际代码改动，不得只根据 `PRD.md`、`proposal.md`、`specs/**/*.md`、`design.md` 或 `PLAN.md` 推断接口细节。

在实际业务代码仓库中执行只读检查；如果当前 Feature 涉及多个代码仓库，逐个仓库检查：

```bash
git status --short
git diff --name-only
git diff --cached --name-only
git diff
git diff --cached
```

满足以下任一情况，才生成 `FEATURE_API_DETAIL.md`：

- 新增接口入口、修改接口路径或请求方式。
- 修改请求参数、响应字段、字段类型、字段是否必填、默认值、枚举值或错误码。
- 修改接口内部数据源、SQL、分页、排序、过滤、外部接口调用、权限校验或异常处理。
- 修改影响接口行为的 Service / Mapper / Repository / DAO 逻辑。

如果没有发现接口新增或修改，不生成该文件，并在 `VERIFY_REPORT.md` 中说明原因。

如果上游设计提到接口变更，但代码中无法确认接口入口或请求响应定义，也不生成该文件，并在 `VERIFY_REPORT.md` 中说明原因。

### 6.2 生成要求

如果需要生成 `FEATURE_API_DETAIL.md`，必须先读取同级 reference：

```text
{PLUGIN_ROOT}/skills/autodev/autodev-verify/references/feature-api-detail.md
```

生成时必须遵守：

- 只基于实际代码生成，不根据需求文档编造字段、SQL、错误码、枚举或内部逻辑。
- 尽量定位 Controller / Router、DTO / VO、Service 实现、Mapper / Repository、错误码 / 枚举 / 统一异常处理。
- 复杂入参 / 出参必须展开到字段级，不能停留在 `List<XxxVO>`、`Result<XxxVO>`、`XxxDTO` 类型名。
- 没有找到 Service 实现类或核心处理方法时，不得编写实现类内部逻辑。
- 正文写到的字段、SQL、错误码、枚举、外部调用或特殊逻辑，必须能在“代码依据”中找到支撑。
- 文档结构和兜底写法按 reference 模板执行。

### ⛔ 步骤完成检查 — Step 6
- [ ] 已通过只读 git 命令或实际代码文件检查接口新增/修改情况
- [ ] 若生成 `FEATURE_API_DETAIL.md`：复杂入参 / 出参已经展开到字段级
- [ ] 若生成 `FEATURE_API_DETAIL.md`：每个核心结论都有代码依据
- [ ] 若未生成 `FEATURE_API_DETAIL.md`：已在 `VERIFY_REPORT.md` 说明原因

---

## Step 7: 分支决策

### 路径 A：全部通过 → `verify_done`

使用统一脚本将当前 Feature 的 checkpoint 推进为 `verify_done`。本轮验收摘要与历史证据写入 `VERIFY_REPORT.md`。

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint verify_done
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "FEATURE_ID")
```

**输出提示：**

```
## ✓ Verify 通过

所有 specs 行为契约均通过（证据由上游阶段技能 autodev-utest 与 autodev-e2e 提供）。
VERIFY_REPORT.md 已生成。
如当前 Feature 涉及新增或修改接口，FEATURE_API_DETAIL.md 已生成；如未涉及，VERIFY_REPORT.md 已记录未生成原因。

checkpoint=verify_done → Dev 阶段结束，Ops 阶段可继续调用 autoops-cicd
```

> ⚠️ **归档不在本 skill 执行**。归档由 Ops 阶段处理。

**Skill 完成。**

---

### 路径 B：存在失败项 → `needs_fix`

使用统一脚本将当前 Feature 的 checkpoint 推进为 `needs_fix`：

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

在 `VERIFY_REPORT.md` 的失败详情中追加：

```markdown
## 已知问题
- 1 [标准简述]: 待修复 — 上游测试 [文件::方法] FAIL，[失败摘要]
- 建议回流阶段: Plan / Code / Biz / Ops（按问题来源填写）
- 问题来源: UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log / proposal.md / specs/**/*.md / design.md / 人工补充说明
```

**输出提示：**

```
## ✗ 需要修复

K 个 specs 行为契约未通过（来源：UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log）。问题已记录到 VERIFY_REPORT.md。

→ 根路由器将读取 VERIFY_REPORT.md 中记录的问题来源与建议回流阶段，再决定回到 Biz / Plan / Code / Ops
```

**Skill 完成。** 下一步由路由器决定：`needs_fix` → 按 `VERIFY_REPORT.md` 中的建议回流阶段处理。

---

### 路径 C：存在需人工验证项

```
## ⚠ 部分标准需要人工验证

自动通过: N/M
自动失败: K/M（已进路径 B 条件）
需人工确认: J/M
```

> 若同时存在自动失败（K>0）与需人工验证（J>0）：**优先按路径 B 处理**（写 `needs_fix`），需人工验证项转为「待回归后再人工确认」记入 VERIFY_REPORT.md。
>
> 若只有需人工验证（K=0, J>0）：等待用户逐项回复。

等待用户逐项裁定：若当前运行模式支持 `request_user_input`，必须优先用它就每个待人工验证项（或整体）发起 `通过` / `有问题（需说明）` 选择；若不支持，必须显式追问用户逐项回复"通过"或问题描述。
- 选择"通过" → 标记为通过；若全部通过 → 路径 A
- 选择"有问题" / 回复问题描述 → 标记为失败 → 路径 B
未拿到用户裁定前，保持 `verify_in_progress`，不得擅自判 `verify_done` 或 `needs_fix`。

### ⛔ 步骤完成检查 — Step 7
- [ ] 通过：验收摘要已写入 `VERIFY_REPORT.md`
- [ ] 失败：已知问题已更新，失败详情已记录（引用 UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log 段落）

---

## 输出清单

Skill 完成前必须满足：

- [ ] `{FEATURE_DIR}/VERIFY_REPORT.md` 已生成
- [ ] 当前 Feature 涉及新增或修改接口时，`{FEATURE_DIR}/FEATURE_API_DETAIL.md` 已生成；不涉及或代码依据不足时，`VERIFY_REPORT.md` 已说明原因
- [ ] 报告中每项裁定都指向 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md` 或 `e2e-run.log` 的证据段落，或标注"需人工验证"
- [ ] 刷新后的 `CHECKPOINT` = `verify_done` / `needs_fix`（或路径 C 等待）
- [ ] 验收摘要已写入报告（通过时）
- [ ] 已知问题已更新（失败时）

---

---

## 崩溃恢复详解

本 skill 对业务代码是**只读 + 汇总**操作：

1. 刷新后的 `CHECKPOINT` 停留在 `verify_in_progress`
2. 重新读取 UNIT_TEST_REPORT.md、test-output.log、E2E_REPORT.md 和 e2e-run.log，重新生成 VERIFY_REPORT.md（允许覆盖）
3. 重新检查实际代码改动；若涉及新增或修改接口，重新生成 FEATURE_API_DETAIL.md（允许覆盖）
4. 重新做分支决策

恢复完全幂等：不会破坏业务代码、不会重复启动服务、不会重复写测试。

---
