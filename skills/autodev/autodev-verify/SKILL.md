---
name: autodev-verify
description: 读取上游阶段技能 autodev-utest 与 autodev-e2e 产出的单测、E2E 报告和 PRD 验收标准，汇总生成 VERIFY_REPORT.md 并做最终 verify_done / needs_fix 分支决策。不再自己生成测试、不再启动服务、不再执行命令验证。支持 --feature 多人协作、--auto（路径 C 仍需暂停；迭代上限由 max_iterations 控制）。默认由当前会话内联执行。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。
```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```

<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->
## 流程契约（由 board_config.json 生成）

本区块由 `board_core/board_config.json` 静态编译生成，请勿手工修改；修改流程契约后运行 `python "{PLUGIN_DIR}/hooks/compile_skill_contracts.py" --write` 重新生成。

- **唯一事实来源:** `{PLUGIN_DIR}/board_core/board_config.json` 中 `skill: "autodev-verify"` 的节点。
- **节点:** `dev.verify`
- **阶段:** 验收汇总
- **分组:** Dev
- **Checkpoints:** `verify_in_progress`, `verify_done`

### 输入产物
- `PRD.md`：PRD文档（必需）
- `design.md`：设计契约（必需）
- `PLAN.md`：执行计划（必需）
- `UNIT_TEST_REPORT.md`：单元测试报告（必需）
- `E2E_TEST_CASES.yaml`：E2E 测试用例（必需）
- `E2E_REPORT.md`：E2E 测试报告（必需）
- `e2e-run.log`：E2E 运行日志（必需）

### 输出产物
- `VERIFY_REPORT.md`：验收报告（必需）

### Validators
- 无
<!-- AUTOBIZDEVOPS_CONTRACT:END -->

# /autodev-verify — 验收汇总 + 分支决策


## 执行主体

本 skill 默认且只能由当前会话内联执行：

- 当前会话直接读取 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md`、`e2e-run.log`、`PRD.md`、`design.md`，生成 `VERIFY_REPORT.md` 并做最终分支决策。
- 不得把验收汇总或分支决策委派给下级 agent或子agent。

本 skill 负责记录失败事实、问题来源和建议回流阶段，不默认把问题绑定回 Biz。

---

## Step 1: 前置检查

**当前 Feature **

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}")
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
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint verify_in_progress
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}")
```

## Step 3: 提取验收标准

从 `{工作目录}/PRD.md` 提取「验收标准」段每一项：

```
[标准1]
[标准2]
...
```

共 M 项待裁决。

同时读取 `{工作目录}/design.md`：

- 从 Behavior Specs、API Decisions、Data Decisions 提取契约验证项 C1, C2, ...
- 如果 API Decisions 包含 `x-auto-no-http-api: true` → 记录本轮无 HTTP/API 契约验证项。
- 如果 Data Decisions 包含 `x-auto-no-sql: true` → 记录本轮无数据库变更验证项。

### ⛔ 步骤完成检查 — Step 3
- [ ] 已从 PRD.md 提取所有验收标准并编号 1..m
- [ ] 已从 design.md 提取行为/API/数据契约验证项，或确认 `x-auto-no-http-api: true` / `x-auto-no-sql: true`
- [ ] 共 M 项已列出

---

## Step 4: 读取单测与 E2E 报告（证据源，不执行命令）

> ✋ **本步骤严格只读。** 不得运行 `npm test` / `pytest` / `mvn test` / Playwright 等任何测试命令，不得启动任何服务，不得再生成新的测试代码。所有测试证据来自上游阶段产物。

**必读文件：**

1. `{工作目录}/UNIT_TEST_REPORT.md` — 上游阶段技能 `autodev-utest` 产出的结构化单测报告。
2. `{工作目录}/test-output.log` — 单测执行的原始日志（通过/失败数量、失败堆栈；缺失时记录）。
3. `{工作目录}/E2E_TEST_CASES.yaml` — 上游阶段技能 `autodev-e2e` 产出的结构化 E2E 用例。
4. `{工作目录}/E2E_REPORT.md` — E2E 结果、失败归因、修复尝试与重跑摘要。
5. `{工作目录}/e2e-run.log` — E2E 原始运行日志、服务/鉴权/UI 执行证据。

**从 UNIT_TEST_REPORT.md 中抽取（按 autodev-utest 的输出约定）：**

- 每个验收标准对应的测试方法名 / 文件路径 / 执行结果（PASS/FAIL/SKIP）
- 每个 design.md 契约验证项 `Cn` 对应的验证结果（如报告涵盖）
- 整体通过率（P/M、P/K）

**从 E2E_REPORT.md 与 e2e-run.log 中抽取（按 autodev-e2e 的输出约定）：**

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
| SKIP / NO_TEST（该验收标准无自动化测试） | ⚠ 需人工验证 |

> 若上游报告存在但格式与约定不符（找不到结论字段等），**不要尝试用测试命令补齐**，直接在报告中标注"报告格式异常"并将相关项置为 "⚠ 需人工验证"。

### ⛔ 步骤完成检查 — Step 4
- [ ] 已读取 `UNIT_TEST_REPORT.md`（若存在）
- [ ] 已读取 `{工作目录}/test-output.log`（若存在；缺失已记录）
- [ ] 已读取 `E2E_TEST_CASES.yaml`、`E2E_REPORT.md` 与 `e2e-run.log`
- [ ] 已为每个步骤建立 PASS / FAIL / 需人工验证 的裁定
- [ ] 未执行任何测试命令、未启动任何本地服务、未生成任何测试文件

---

## Step 5: 生成 VERIFY_REPORT.md（纯汇总）

将裁定结果写入 `{工作目录}/VERIFY_REPORT.md`。**不得**在 VERIFY_REPORT.md 中夹带新的命令输出、新的测试代码、新的 HTTP 响应证据——这些应由 `UNIT_TEST_REPORT.md`、`E2E_REPORT.md` 与 `e2e-run.log` 提供，VERIFY_REPORT.md 只做"映射 + 归档"。

**模板：**

```markdown
# 验证报告

- **Feature:** {slug}
- **验证时间:** [当前时间]
- **上游单测报告:** {工作目录}/UNIT_TEST_REPORT.md
- **上游单测日志:** {工作目录}/test-output.log
- **上游 E2E 报告:** {工作目录}/E2E_REPORT.md
- **上游 E2E 日志:** {工作目录}/e2e-run.log

## 验证总览

| # | 验收标准 | 裁定 | 证据来源 |
|---|---------|------|---------|
| 1 | [标准1] | ✓ 通过 | UNIT_TEST_REPORT + E2E_REPORT |
| 2 | [标准2] | ✗ 失败 | E2E_REPORT（FAIL: AssertionError ...） |
| 3 | [标准3] | ⚠ 需人工验证 | 报告未覆盖（UI 类） |

通过: N/M | 失败: K/M | 需人工验证: J/M

## Design Contract 验证（若适用）

| # | Contract Item | 裁定 | 证据来源 |
|---|-----------|------|---------|
| 1 | REQ-01 / API-01 / DATA-01 | ✓ 通过 | UNIT_TEST_REPORT §C1 + E2E_REPORT §E2E-001 |

或：本轮 `x-auto-no-http-api: true`，无 HTTP/API 契约验证项；`x-auto-no-sql: true`，无数据库变更验证项。

## 失败详情（如有）

### [标准原文]
- **上游测试方法:** [文件路径::方法名]
- **失败摘要:** [错误首行 + 堆栈关键行]
- **预期 vs 实际:** [若上游报告给出则直接引用]
- **建议修复方向:** [由本 skill 结合 PRD 给出简短方向]

## 需人工验证详情（如有）

### [标准原文]
- **原因:** UI/UX 或业务语义类标准，自动化覆盖不足
- **请用户手动验证:** [给出具体操作建议]

## 结论

- 通过率: XX%
- 分支决策: verify_done / needs_fix / 等待用户人工确认
```

### ⛔ 步骤完成检查 — Step 5
- [ ] `{工作目录}/VERIFY_REPORT.md` 已写入
- [ ] 报告中每项都标注了证据来源（指向 UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log 的段落或说明为何需人工验证）
- [ ] 报告**不包含**本 skill 自行执行的测试命令输出或服务启动日志
- [ ] 报告已展示给用户

---

## Step 6: 分支决策

### 路径 A：全部通过 → `verify_done`

使用统一脚本将当前 Feature 的 checkpoint 推进为 `verify_done`。本轮验收摘要与历史证据写入 `VERIFY_REPORT.md`。

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint verify_done
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}")
```

**输出提示：**

```
## ✓ Verify 通过

所有验收标准均通过（证据由上游阶段技能 autodev-utest 与 autodev-e2e 提供）。
VERIFY_REPORT.md 已生成。

checkpoint=verify_done → Dev 阶段结束，Ops 阶段可继续调用 autoops-cicd
```

> ⚠️ **归档不在本 skill 执行**。归档由 Ops 阶段处理。

**Skill 完成。**

---

### 路径 B：存在失败项 → `needs_fix`

使用统一脚本将当前 Feature 的 checkpoint 推进为 `needs_fix`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint needs_fix
CHECKPOINT=$(python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}")
```

在 `VERIFY_REPORT.md` 的失败详情中追加：

```markdown
## 已知问题
- 1 [标准简述]: 待修复 — 上游测试 [文件::方法] FAIL，[失败摘要]
- 建议回流阶段: Plan / Code / Biz / Ops（按问题来源填写）
- 问题来源: UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log / PRD / design.md / 人工补充说明
```

**输出提示：**

```
## ✗ 需要修复

K 个验收标准未通过（来源：UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log）。问题已记录到 VERIFY_REPORT.md。

→ 根路由器将读取 VERIFY_REPORT.md 中记录的问题来源与建议回流阶段，再决定回到 Biz / Plan / Code / Ops
```

**Skill 完成。** 下一步由路由器决定：`needs_fix` → 按 `VERIFY_REPORT.md` 中的建议回流阶段处理。

> 🚀 **--auto 例外：**
> - 直接返回路由器，由路由器根据 `VERIFY_REPORT.md` 中的建议回流阶段决定下一跳，不等用户输入。

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

等待用户回复：
- 回复"通过" → 标记为通过；若全部通过 → 路径 A
- 回复问题描述 → 标记为失败 → 路径 B

> ⚠️ **--auto 下本路径仍必须暂停。** 这是安全边界：AI 不能替代人眼验证 UI/手感/业务语义类标准。
> `--auto` 模式下打印：
> ```
> ⚠️ --auto 模式遇到需人工验证的验收项，已暂停。
>    请逐项回复「通过」或描述实际问题。
> ```
> 然后停止输出等待用户回复。

### ⛔ 步骤完成检查 — Step 6
- [ ] 通过：验收摘要已写入 `VERIFY_REPORT.md`
- [ ] 失败：已知问题已更新，失败详情已记录（引用 UNIT_TEST_REPORT / E2E_REPORT / e2e-run.log 段落）

---

## 输出清单

Skill 完成前必须满足：

- [ ] `{工作目录}/VERIFY_REPORT.md` 已生成
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
