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

