# 任务修复指南

用于修复当前状态为 `implemented` 或 `done` 的 TASK，并通过 `priorEvidenceId` 保留实现证据链。由固定 Code Workflow 准备当前 Batch 的受控修复，再恢复同一 run；任务命令不能代替 Batch 阶段准备或租约领取。

## 修复步骤

1. 从 Runtime 当前任务摘要取得最新 `latestImplementationEvidenceId`，确认问题和修复范围。
2. 使用 Workflow 提供的 artifact workspace、Feature、TASK、Batch Worktree、parallel run 和当前 lease token 启动修复。多仓库任务原样传入 Runtime 提供的 `--workspace-ref`，不能自行推导仓库绑定。

```bash
python -X utf8 "<pluginPath>/hooks/task_runner.py" start-task-repair \
  --workspace "<artifact-workspace>" --feature "<feature>" --task-id "<TASK>" \
  --code-workspace "<batch-worktree>" --parallel-run-id "<parallel-run-id>" \
  --lease-token "<current-lease-token>" --prior-evidence-id "<latestImplementationEvidenceId>"
```

3. 保存返回的真实 TASK `runId`。只有启动成功后才能修改授权范围内的生产代码；恢复已有活动 TASK 时使用 Runtime 的 `resume` 指引。
4. 后端修改仍按[编译登记协议](./code-compile-guard.md)完成当前 TASK/run 的实际生产编译，保存原始日志和退出码。不得以历史 PASS 代替当前编译证据。
5. 用相同绑定和本次 TASK `runId` 完成实现登记。

```bash
python -X utf8 "<pluginPath>/hooks/task_runner.py" finish-implementation \
  --workspace "<artifact-workspace>" --feature "<feature>" --task-id "<TASK>" \
  --code-workspace "<batch-worktree>" --parallel-run-id "<parallel-run-id>" \
  --lease-token "<current-lease-token>" --run-id "<TASK-runId>" --repair-mode
```

6. 恢复固定 Workflow，由它基于本次实现证据继续当前 Batch 的评审、测试和交付门禁。单次修复等特殊恢复分支以 Runtime 返回的阶段协议为准，不手改 Plan、manifest 或历史 Evidence 推进状态。

## 失败与恢复

- `task_repair_requires_implemented_or_done_status:<TASK>`：检查实际状态，不能用修复入口启动尚未实现的任务。
- `task_repair_prior_evidence_mismatch:<TASK>`：重新取得最新实现证据 ID，避免修复旧版本。
- 租约、阶段或 Worktree 绑定失败：回到固定 Workflow 恢复当前授权，不更换参数绕过检查。
- 编译或测试失败：保留真实输出，按当前修复预算处理；预算耗尽则保存延期问题和已有资产。

执行边界见 [Code 技能](../skills/autodev/autodev-code/SKILL.md)，上下文与恢复协议见[工程事实与阶段交接](./engineering-context.md)。
