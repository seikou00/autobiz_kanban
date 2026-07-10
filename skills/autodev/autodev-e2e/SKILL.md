---
name: autodev-e2e
description: 对单个 feature 执行端到端测试。作为 Autodev 根流程中的正式阶段，承接 autodev-utest 产物，输出 E2E_REPORT.md / e2e-run.log，并按 checkpoint 做 e2e_done / needs_fix 分支决策。默认由当前会话内联执行；可使用后台进程启动服务或运行长时间测试命令。
version: v1.1.2609
---

# autodev-e2e — E2E 阶段技能


## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-e2e --feature "${feature}" --plain
```


## State 快照读取

第一步调用脚本读取当前 Feature 快照：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入、恢复和分支决策直接取用 `CHECKPOINT`。

## 输入与行为依据

行为契约（specs 的 Requirement / Scenario）是 E2E pass/fail 的主要行为依据。

禁止写入：

- 不要修改上游产物。
- 不要为通过 E2E 而弱化断言、删除用例、伪造报告。

每轮 E2E 必须优先以 specs 中属于用户主链路的 Requirement / Scenario 生成结构化测试用例；相关 API Decision 或 Data Decision 只作为执行和断言上下文。涉及页面、按钮、点击、弹窗、跳转、表单、前端组件、路由、用户可见流程的 P0/P1 用例必须标记 `ui_required: true`。

## Checkpoint 写入

开始 E2E 前推进到 `e2e_in_progress`，写入后立即刷新 `CHECKPOINT`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

E2E 通过后推进到 `e2e_done`；若存在明确失败并需要回流，推进到 `needs_fix`。每次写入后都必须刷新 `CHECKPOINT`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。