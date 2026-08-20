---
name: autobizdevops-rollback
description: 安全回退 Feature 到指定 Biz、Dev 或 Ops 阶段，清理后续产物并重置 Code 执行态；在需要主动回退、清理运行时数据或恢复 Code Session 源码时使用。
---

# /autobizdevops-rollback - Feature 阶段回退

独立执行 Feature 的阶段回退、产物清理、Code 执行态重置和可选源码恢复。用户明确要求回退到某个 Biz/Dev/Ops 阶段、清理后续产物、或重置 Code 实现时使用；不要把它作为正常 workflow 路由的替代入口。

## 执行协议

始终先读取当前状态，不得从 `STATE.md`、hooks 日志或产物推断 checkpoint：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

用户未提供 Feature 时先读取全量状态并要求明确选择。确认目标阶段后，必须先运行 dry-run：

```bash
python "${pluginPath}/hooks/rollback_stage.py" \
  --feature "${feature}" \
  --to-stage "<node-id-or-stage-alias>" \
  --code-source keep \
  --dry-run --json
```

首次 dry-run 不传 `--state-mode`。脚本会返回 `stateOptions`、`plannedArtifacts`，并标记 `confirmationRequired=true`；此时不会选定新 checkpoint。必须把可用状态选项和清理清单展示给用户，并明确询问：

- `target_in_progress`：回退到用户指定目标阶段的 `in_progress` 状态。
- `previous_done`：回退到目标阶段前一个有效阶段的 `done` 状态。

用户必须明确选择其中一个可用模式。首个有效阶段没有 `previous_done`，只能选择 `target_in_progress`；没有 `*_in_progress` checkpoint 的归档类节点只会显示 `previous_done`。无论选择哪个状态模式，清理范围始终相同：目标阶段及其后续阶段的产物都会清理。

收到用户选择后，使用相同模式重跑 dry-run，展示已选的当前/目标 checkpoint、清理产物、Code task 重置清单、源码影响和阻断错误。未获得对该状态选择和回退执行的明确确认前不得调用 `--apply`：

```bash
python "${pluginPath}/hooks/rollback_stage.py" \
  --feature "${feature}" \
  --to-stage "<node-id-or-stage-alias>" \
  --state-mode target_in_progress \
  --code-source keep \
  --dry-run --json
```

确认后以相同的 `--state-mode` 执行：

```bash
python "${pluginPath}/hooks/rollback_stage.py" \
  --feature "${feature}" \
  --to-stage "<node-id-or-stage-alias>" \
  --state-mode target_in_progress \
  --code-source keep \
  --apply --json
```

执行结束后重新运行 `read_state_json.py` 和 `resolve_next_skill.py --json`，只报告脚本返回的状态和路由结果。

## History 清理

回退 history 默认不会自动删除。需要维护磁盘空间时，先预览每个 Feature 超出最近 10 次的记录：

```bash
python "${pluginPath}/hooks/rollback_stage.py" \
  --prune-history \
  --keep-history 10 \
  --dry-run --json
```

确认后显式执行 `--apply`。也可以传 `--feature "${feature}"` 只清理一个 Feature；未知或损坏的 history manifest 永远不会被自动删除。

## Code 阶段规则

Code 回退不支持批次级目标。只要清理范围包含 `dev.code`，就一次性回退整个 Code Session 到 Code 开始前：

- 清理 Code 及其后续阶段产物、evidence index、handoff 和 `.task-runs`；运行时数据会归档到 rollback history。
- 重置全部 Code task、implementation/completion evidence 引用、批次编译状态和 active batch；保留任务契约、依赖和验收标准。
- 默认 `--code-source keep`，不修改业务 Git 仓库，只报告源码变化。
- 只有用户明确确认且存在基线时才使用 `--code-source restore`。源码恢复前会校验当前 hash 是否仍等于该 Feature 的最终 Code 快照；不一致时阻断，不覆盖外部修改。

Code 开始前必须用同一个独立脚本捕获一次整个 Session 基线：

```bash
python "${pluginPath}/hooks/rollback_stage.py" \
  --capture-code-session \
  --feature "${feature}" \
  --code-workspace "<business-repository>" \
  --json
```

没有基线时，`--code-source restore` 必须失败并提示改用 `keep` 或先捕获基线。不要使用 `git stash`、`git reset --hard` 或静默创建提交/tag。

## 安全边界

- `--dry-run` 和 `--apply` 必须二选一；脚本默认不写入。`--apply` 必须显式传入 `--state-mode`，不能沿用隐式默认状态。
- 目标阶段必须已到达，跳过节点不能作为目标；首个有效阶段可以回退到自身的 `in_progress`，但不能回退到不存在的前置 `done`。
- 同一 Feature 的 Code Session 基线捕获和回退由 Feature 锁串行化，不允许并发写入 `active.json`。
- 状态只能由 `rollback_stage.py` 写入；不得手工编辑 `state.json`、`STATE.md` 或 `plan.json`。
- 回退事务、失败恢复和归档记录位于 `.autobizdevops/rollback/`；不要删除 history 以外的业务仓库文件。
