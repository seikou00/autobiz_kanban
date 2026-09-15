# Task Detail 字段速查

单任务探索使用 `lint-draft-task-detail --task-id <id> --body-stdin`；它不写入 Draft，
会一次返回结构、引用、workspace 和 Maven 问题。首次全量提交必须使用
`lint-draft-task-details --full --body-stdin`，通过后将**同一 payload**交给
`set-draft-task-details --full --body-stdin`。`--full` 要求列出 Draft 的所有 Task，并运行与
finalize 相同的跨 Task 聚合校验；不通过时不会写入任何 Detail。其顶层为：

```json
{
  "details": [
    {"taskId": "T001", "body": {"schemaVersion": "autodev.plan-detail.v1"}}
  ]
}
```

## 固定限制

- `implementation`：2–6 条；超过 6 条先按可观察行为或验证边界拆分，不要把文件级步骤逐条罗列。
- `acceptance`：至少一项；每项的 `scenarioRefs` 必须是对应 Core `refs.scenarios` 的子集。
- `nonGoals`：至少一项非空，明确排除相邻行为。
- `checks`：本地实现任务至少一条 required command，并覆盖全部 acceptance 项；`external_dependency` 不写本地检查。普通 Detail 可省略 `covers`，writer 会自动填充当前任务的所有 AC。
- matrix Detail（Core 的 `grouping.detailObligations` 或 `show-draft-task-work.detailObligation` 非空）：只允许**恰好一条** required 的允许类型命令；省略 `covers`，让 writer 覆盖全部生成的 AC。手写 `covers` 时必须等于全部 AC，不能只写 `[1]`。
- `refs.design`、`refs.data`、`refs.decisions` 只能引用已确认 Design lock 中的真实 ID；不涉及就写 `[]`。

## Maven 命令

- 叶子模块测试：`cwd` 设为该模块，**不要**使用 `-pl`。
- Reactor 聚合根测试：`cwd` 设为含 `<modules>` 的聚合根，然后才可用 `-pl <module>`。
- 非根 `cwd` 不得用 `-pl` 重复选择同一路径；它会触发 `maven_project_selector_duplicates_cwd`。

推荐直接使用 `--body-stdin`。非 `--full` 批量写入仅用于 rebuild 返回的 `resetTaskIds`；如果必须落临时文件，使用 feature/task/随机值组成的唯一文件名，绝不复用旧的 `/tmp/T001-detail.json`。
