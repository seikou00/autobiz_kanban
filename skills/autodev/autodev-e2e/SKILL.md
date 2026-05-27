---
name: autodev-e2e
description: 对单个 feature 执行端到端测试。作为 Autodev 根流程中的正式阶段，承接 autodev-utest 产物，输出 E2E_REPORT.md / e2e-run.log，并按 checkpoint 做 e2e_done / needs_fix 分支决策。默认由当前会话内联执行；可使用后台进程启动服务或运行长时间测试命令。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

# autodev-e2e — E2E 阶段技能

```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```

<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->
## 流程契约（由 board_config.json 生成）

本区块由 `board_core/board_config.json` 静态编译生成，请勿手工修改；修改流程契约后运行 `python "{PLUGIN_DIR}/hooks/compile_skill_contracts.py" --write` 重新生成。

- **唯一事实来源:** `{PLUGIN_DIR}/board_core/board_config.json` 中 `skill: "autodev-e2e"` 的节点。
- **节点:** `dev.e2e`
- **阶段:** E2E 测试
- **分组:** Dev
- **Checkpoints:** `e2e_in_progress`, `e2e_done`

### 输入产物
- `PRD.md`：PRD文档（必需）
- `design.md`：设计契约（必需）
- `PLAN.md`：执行计划（必需）
- `REQUIREMENTS_EVAL.md`：需求实现评审报告（必需）
- `UNIT_TEST_REPORT.md`：单元测试报告（必需）

### 输出产物
- `E2E_TEST_CASES.yaml`：E2E 测试用例（必需）
- `E2E_REPORT.md`：E2E 测试报告（必需）
- `e2e-run.log`：E2E 运行日志（必需）

### Validators
- 无
<!-- AUTOBIZDEVOPS_CONTRACT:END -->

## State 快照读取

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把返回 JSON 记为 `STATE`：

```bash
python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}"
```

后续准入、恢复和分支决策直接取用 `STATE.checkpoint` / `STATE.record`。若 `STATE.checkpoint` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## Checkpoint 写入

开始 E2E 前推进到 `e2e_in_progress`，写入后立即刷新 `STATE`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint e2e_in_progress
python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}"
```

E2E 通过后推进到 `e2e_done`；若存在明确失败并需要回流，推进到 `needs_fix`。每次写入后都必须刷新 `STATE`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint e2e_done
python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}"
```

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint needs_fix
python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}"
```
