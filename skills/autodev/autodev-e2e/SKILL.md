---
name: autodev-e2e
description: 对单个 feature 执行端到端测试。作为 Autodev 根流程中的正式阶段，承接 autodev-utest 产物，输出 E2E_REPORT.md / e2e-run.log，并按 checkpoint 做 e2e_done / needs_fix 分支决策。默认由当前会话内联执行；可使用后台进程启动服务或运行长时间测试命令。
version: v1.1.1604
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

# autodev-e2e — E2E 阶段技能

```
FEATURE_DIR = {PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}
```

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-e2e --feature "$FEATURE_ID" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件，不要读取清单之外的阶段产物作为硬依赖。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）；按它决定读哪些部分、如何提取上下文。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止；bundle 未列出的产物不属于本工作流，不要读取、不要等待，也不要要求用户提供。
- **降级语义**：`required: false` 的输入是可选参考，缺失时按其 `extract.degrade` 的退化读法继续执行，不要因缺失而停止。上游节点不在当前工作流时，其产物已从 bundle 中移除，按本文对应的「bundle 不含 X」分支处理。

无 `$FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


## State 快照读取

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续准入、恢复和分支决策直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 输入与行为依据

消费 Source Bundle：按「流程契约」一节取本 Feature 的契约，读取 `sourceBundle` 列出的产物原件（标准链为 proposal.md、specs/**/*.md、design.md、PLAN.md、REQUIREMENTS_EVAL.md、UNIT_TEST_REPORT.md、test-output.log），按各自 `extract`（focus/method/degrade）决定读取重点；契约未提供的输入按降级读法继续，不要硬等。

标准链下的用途约束：

- `proposal.md`：本轮能力边界、影响面、非目标。
- `specs/**/*.md`：Requirement / Scenario 行为契约，是 E2E pass/fail 的主要行为依据。
- `design.md`（如在 bundle 中）：接口决策、数据决策、成功与失败路径、风险与待确认项。
- `UNIT_TEST_REPORT.md` / `test-output.log`（如在 bundle 中）：上游单测覆盖、轻量单测命令线索和回归风险。
- `REQUIREMENTS_EVAL.md`（如在 bundle 中）：需求覆盖、遗漏与风险提示。

禁止写入：

- 不要修改 `{FEATURE_DIR}/PRD.md`（如果存在）。
- 不要修改 `{FEATURE_DIR}/proposal.md`。
- 不要修改 `{FEATURE_DIR}/specs/**/*.md`。
- 不要修改 `{FEATURE_DIR}/design.md`。
- 不要修改 `{FEATURE_DIR}/PLAN.md`。
- 不要修改 `{FEATURE_DIR}/UNIT_TEST_REPORT.md`、`test-output.log`、`REQUIREMENTS_EVAL.md`。
- 不要为通过 E2E 而弱化断言、删除用例、伪造报告。

每轮 E2E 必须优先以 specs 中属于用户主链路的 Requirement / Scenario 生成结构化测试用例；相关 API Decision 或 Data Decision 只作为执行和断言上下文。涉及页面、按钮、点击、弹窗、跳转、表单、前端组件、路由、用户可见流程的 P0/P1 用例必须标记 `ui_required: true`。

## Checkpoint 写入

开始 E2E 前推进到 `e2e_in_progress`，写入后立即刷新 `CHECKPOINT`：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint e2e_in_progress
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

E2E 通过后推进到 `e2e_done`；若存在明确失败并需要回流，推进到 `needs_fix`。每次写入后都必须刷新 `CHECKPOINT`：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint e2e_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```
