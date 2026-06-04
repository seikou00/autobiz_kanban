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
## 流程契约

当前 skill 的 checkpoint、输入/输出产物和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 为唯一事实来源。
运行前如需查看当前契约，执行：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-e2e --json
```
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


## State 快照读取

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续准入、恢复和分支决策直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 输入与行为依据

读取以下 feature 文档：

- `{FEATURE_DIR}/proposal.md`
- `{FEATURE_DIR}/specs/**/*.md`
- `{FEATURE_DIR}/design.md`
- `{FEATURE_DIR}/PLAN.md`
- `{FEATURE_DIR}/REQUIREMENTS_EVAL.md`
- `{FEATURE_DIR}/UNIT_TEST_REPORT.md`
- `{FEATURE_DIR}/test-output.log`

用途约束：

- `proposal.md`：本轮能力边界、影响面、非目标。
- `specs/**/*.md`：Requirement / Scenario 行为契约，是 E2E pass/fail 的主要行为依据。
- `design.md`：接口决策、数据决策、成功与失败路径、风险与待确认项。
- `UNIT_TEST_REPORT.md` / `test-output.log`：上游单测覆盖、轻量单测命令线索和回归风险。
- `REQUIREMENTS_EVAL.md`：需求覆盖、遗漏与风险提示。

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
