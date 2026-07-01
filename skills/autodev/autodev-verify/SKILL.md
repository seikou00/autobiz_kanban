---
name: autodev-verify
description: 读取上游阶段技能 autodev-utest 与 autodev-e2e 产出的单测、E2E 报告，以及 proposal/specs/design 契约，汇总生成 VERIFY_REPORT.md 并做最终 verify_done / needs_fix 分支决策。不再自己生成测试、不再启动服务、不再执行命令验证。支持 --feature 多人协作。默认由当前会话内联执行。
version: v1.1.1604
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（执行清单）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，取当前 Feature 的执行清单（脚本已按 feature 目录的真实产物状态，把每个 input 解析成一条确定指令）：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-verify --feature "${feature}" --plain
```

- **逐条执行**：`## 输入产物` 下每个 input 只有一行确定指令，按序执行即可，不需要自己判断产物是否存在或该走哪个分支。
- **已生成**：按其 `读取方式` 读原件并纳入上下文；`读取方式` 是该 input 在场时的专属指令，优先于技能正文的通用默认。
- **未生成**：按其 `缺失处理` 执行——必需 input 停止并回流上游补齐；可选 input 按其降级动作继续，不因缺失而停止。
- **不列即不存在**：清单未列出的 id 不属于本 workflow 的正式流程产物 input，不要把它当作上游阶段产物读取、等待或索要。
- **适用边界**：上一条只约束正式流程产物 input；不限制用户本轮直接提供的材料、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 或技能正文明确要求读取的辅助素材。
- **输出与校验**：`## 输出产物` 是本节点应产出的产物；`## Validators`/`## Guards` 是推进 checkpoint 的校验项。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线清单（此时按 `读取方式` 预览，不含产物状态）。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-verify — 验收汇总 + 分支决策


## 执行主体

本 skill 默认且只能由当前会话内联执行：

- 当前会话直接读取执行清单列出的结构化事实源，使用 `UNIT_TEST_RESULT.json`、`E2E_RESULT.json`、`REVIEW_FINDINGS.json`、`evidence/EVIDENCE.jsonl` 与 `plan.json` 做机器裁决；Markdown 报告只做人类叙述补充，生成 `VERIFY_DECISION.json`，并可同步生成 `VERIFY_REPORT.md`。
- 不得把验收汇总或分支决策委派给下级 agent或子agent。

本 skill 负责记录失败事实、问题来源和建议回流阶段，不默认把问题绑定回 Biz。

---

## Step 1: 前置检查

**当前 Feature **

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入、恢复和分支决策直接取用 `CHECKPOINT`：

| Checkpoint | 行为 |
|-----------|------|
| `e2e_done` | ✓ 正常开始最终验收汇总 |
| `verify_in_progress` | → 恢复模式（重新汇总并决策，只读操作） |
| `unit_test_done` | ✗ 错误：E2E 阶段未执行，请先让根路由器调用上游阶段技能 `autodev-e2e` |
| 其他 | ✗ 错误：checkpoint 异常，请检查 `state.json` |

若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

---

## Step 2: 写入 Checkpoint（标记开始）

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint verify_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## Step 3: 提取验收契约

按「流程契约」一节取本 Feature 的执行清单，对其中每个输入按 `读取方式` 抽取；标『未生成』的可选输入按其 `缺失处理`（降级读法）处理。清单未列出的输入不读取：其所属阶段被跳过或不在当前工作流链，在报告中统一标注「不在本工作流产物清单」，而非「缺失」。

从 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md` 提取本轮能力边界、影响面和非目标。

从 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/**/*.md` 提取每个 Requirement / Scenario：

```
specs/[capability]/spec.md / Requirement / Scenario
...
```

共 M 项待裁决。

要求在验收报告里使用稳定 ID 回链：

- Requirement: `specs/<capability>/spec.md#REQ-001`
- Scenario: `specs/<capability>/spec.md#SCN-001`
- Task: `T001`
- Evidence: `ev_0001`
- Eval: `eval_0001`

按各输入的 `读取方式` 提取验收验证项：

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

1. `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_REPORT.md` — 上游阶段技能 `autodev-utest` 产出的结构化单测报告。
2. `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log` — 单测执行的原始日志（通过/失败数量、失败堆栈；缺失时记录）。
3. `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/E2E_TEST_CASES.yaml` — 上游阶段技能 `autodev-e2e` 产出的结构化 E2E 用例。
4. `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/E2E_REPORT.md` — E2E 结果、失败归因、修复尝试与重跑摘要。
5. `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/e2e-run.log` — E2E 原始运行日志、服务/鉴权/UI 执行证据。

**从 `UNIT_TEST_RESULT.json` 中抽取（按其 `读取方式` 抽取）：**

- 每个 Requirement / Scenario 对应的测试方法名 / 文件路径 / 执行结果（PASS/FAIL/SKIP）
- 每个 specs/design 契约验证项 `Cn` 对应的验证结果（如报告涵盖）
- 整体通过率（P/M、P/K）

**从 `E2E_RESULT.json` 中抽取（按其 `读取方式` 抽取）：**

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
- [ ] 已读取 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log`（若存在；缺失已记录）
- [ ] 已读取 `E2E_TEST_CASES.yaml`、`E2E_REPORT.md` 与 `e2e-run.log`
- [ ] 已为每个步骤建立 PASS / FAIL / 需人工验证 的裁定
- [ ] 未执行任何测试命令、未启动任何本地服务、未生成任何测试文件

---

## Step 5: 生成 VERIFY_REPORT.md（纯汇总）

将裁定结果写入 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/VERIFY_REPORT.md`。**不得**在 VERIFY_REPORT.md 中夹带新的命令输出、新的测试代码、新的 HTTP 响应证据——这些应由 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md` 与 `e2e-run.log` 提供，VERIFY_REPORT.md 只做"映射 + 归档"。

**模板：**

```markdown
# 验证报告

- **Feature:** {slug}
- **验证时间:** [当前时间]
- **上游单测报告:** ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_REPORT.md
- **上游单测日志:** ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log
- **上游 E2E 报告:** ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/E2E_REPORT.md
- **上游 E2E 日志:** ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/e2e-run.log

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
```

### ⛔ 步骤完成检查 — Step 5
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/VERIFY_REPORT.md` 已写入
- [ ] 报告中每项都标注了证据来源（指向 UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log 的段落或说明为何需人工验证）
- [ ] 报告**不包含**本 skill 自行执行的测试命令输出或服务启动日志
- [ ] 报告已展示给用户

---

## Step 6: 分支决策

### 路径 A：全部通过 → `verify_done`

使用统一脚本将当前 Feature 的 checkpoint 推进为 `verify_done`。本轮验收摘要与历史证据写入 `VERIFY_REPORT.md`。

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint verify_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature ${feature})
```

**输出提示：**

```
## ✓ Verify 通过

所有 specs 行为契约均通过（证据由上游阶段技能 autodev-utest 与 autodev-e2e 提供）。
VERIFY_REPORT.md 已生成。

checkpoint=verify_done → Dev 阶段结束
```

> ⚠️ **归档不在本 skill 执行**。归档由 Ops 阶段处理。

**Skill 完成。** 下一步以 `resolve_next_skill.py` 为准（不假设固定下一技能）：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py"
```

---

### 路径 B：存在失败项 → `needs_fix`

使用统一脚本将当前 Feature 的 checkpoint 推进为 `needs_fix`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
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

### ⛔ 步骤完成检查 — Step 6
- [ ] 通过：验收摘要已写入 `VERIFY_REPORT.md`
- [ ] 失败：已知问题已更新，失败详情已记录（引用 UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log 段落）

---

## 输出清单

Skill 完成前必须满足：

- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/VERIFY_REPORT.md` 已生成
- [ ] 报告中每项裁定都指向 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md` 或 `e2e-run.log` 的证据段落，或标注"需人工验证"
- [ ] 刷新后的 `CHECKPOINT` = `verify_done` / `needs_fix`（或路径 C 等待）
- [ ] 验收摘要已写入报告（通过时）
- [ ] 已知问题已更新（失败时）

---

---

## 崩溃恢复详解

本 skill 是**纯只读 + 汇总**操作：

1. 刷新后的 `CHECKPOINT` 停留在 `verify_in_progress`
2. 重新读取 UNIT_TEST_REPORT.md、test-output.log、E2E_REPORT.md 和 e2e-run.log，重新生成 VERIFY_REPORT.md（允许覆盖）
3. 重新做分支决策

恢复完全幂等：不会破坏业务代码、不会重复启动服务、不会重复写测试。

---
