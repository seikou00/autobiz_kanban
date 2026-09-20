---
name: autodev
description: Autodev Dev 阶段根路由器。以 board_config 编译出的 checkpoint 路由 Specs、Design、Plan、可选详细设计和固定 Code Workflow。
version: v1.2.0
---

## 路由原则

唯一的流程事实源是 `board_core/board_config.json` 与
`resolve_next_skill.py --json`。不得根据旧报告、旧技能描述或历史 checkpoint
推断下一步。

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
python "${pluginPath}/hooks/resolve_next_skill.py" --json
```

根路由只路由 Board 中声明的节点：Specs、Technical Design、Plan、可选 Detailed
Design、Code。`review`、`test` 与 `e2e_test` 是固定 Code Workflow 的 Batch
子阶段，不是 Board 节点，也不允许写入独立 checkpoint。

```text
prd_done → specs → design → plan → [detail design] → code → cicd
                                             └─ Code 内部：implement → review → test → B-E2E → evidence aggregate
```

## Code 阶段

进入 `/autodev-code` 后，全局 checkpoint 只保持 `code_in_progress`，直到固定
Workflow 已完成所有交付 Batch、B-E2E 与证据聚合，再推进到 `code_done`。

- `task_runner start` 仅授权当前实现 TASK 的生产代码写入；已 implemented 的
  TASK 不得为测试重新启动 run。
- UTest 测试资产只能在当前 Batch 的 `test` 子阶段写入。
- B-E2E 仅在全部 delivery Batch 合并后由 V-E2E 的 `e2e_test` 子阶段执行。
- 不得调用 `/autodev-reviewer`、`/autodev-utest`、`/autodev-e2e` 或
  `/autodev-verify` 来创建独立阶段或推进 `review_*`、`unit_test_*`、`e2e_*`、
  `verify_*` checkpoint。

失败时由固定 Workflow 记录 Batch evidence 或生成 `needs_fix`；Plan 契约问题才
回到 `plan_in_progress`。Code 完成后按 `resolve_next_skill.py` 进入 CI/CD。
