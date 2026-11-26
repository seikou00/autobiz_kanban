---
name: autodev
description: Autodev Dev 阶段根路由器。按 Board checkpoint 路由 Specs、Design、Plan、可选详细设计和 Code。
version: v1.2.0
---

## 路由原则

执行本阶段以及下游子技能中的所有 Python 命令时，统一使用 `python -X utf8 <script.py>`（等效于 `PYTHONUTF8=1`）。

唯一的流程事实源是 `board_core/board_config.json` 与
`resolve_next_skill.py --json`。不得根据旧报告、旧技能描述或历史 checkpoint
推断下一步。

```bash
python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/resolve_next_skill.py" --json
```

根路由只路由 Board 中声明的节点。每个 Code Batch 在自己的对话里完成实现与 Review。

```text
prd_done → specs → design → plan → [detail design] → code → cicd
```

## Code 阶段

`/autodev-code` 通过 `task_runner.py next-code-batch` 领取当前批次，使用 Plan 绑定的仓库和分支。在本对话实现、编译并 Review，发现问题时修复后复验，再登记交付。UTest 和 E2E 在各自阶段执行。

中间批次交付后，按 Runner 的提示新开对话继续。最后一批通过门禁后才更新 `code_done`，再由 Board 路由到 CI/CD。
