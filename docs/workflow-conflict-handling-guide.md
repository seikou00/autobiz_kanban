# 工作流冲突处理指南

## 当前实现：后台命令结果与冲突恢复

`validateAndPromoteBatch()` 会先检查构建结果的 `status`。若为
`candidate_conflicted`，立即对同一个 repository/wave 执行以下交接：

1. 插件 `prepare-resolution` 检查候选仓库/分支和正在进行的 merge，登记模型的文件写入授权，返回 `resolution_required`、`worktreePath`、`conflictedFiles`，以及准确的 `runDir`、`manifestPath`、`contextPath`、`contextDigest`、`identityDigest`。
2. 独立冲突 Agent 只编辑这些文件，保留双方业务逻辑并移除冲突标记；完成后返回 `success=true,status=resolved`。不得自行 `git add`、`git commit` 或 `git merge --continue`，也不得使用脚本包装这些操作。
3. Agent 返回后，Workflow 单独调用插件 `complete-resolution`。插件检查 Git 身份、修改范围、剩余冲突标记，精确暂存冲突文件，保留 Git hooks 并非交互创建带 task-card 信息的 merge commit，登记实际候选 SHA。
4. 只有插件返回 `built` 且没有失败信号，才调用 `promote-candidate`。

写入 hook 按 `prepare-resolution` 登记的候选和文件授权，不要求为合并重新启动已完成的生产 TASK；未授权文件和其他 Worktree 仍不可写。
若提交成功但登记中断，准备命令返回 `ready_to_complete`，Workflow 跳过模型编辑，插件核对提交的父节点、tree 和消息后恢复登记，不会创建第二个提交。
即时恢复与最终恢复共用此协议；模型编辑次数由持久化 `maxAttempts` 控制。插件提交失败时返回具体错误，不让模型尝试替代提交命令。

原 `resolve-candidate` 保留为旧自动策略入口，`resume-candidate` 保留为人工已提交候选的登记入口；固定 Workflow 使用上述新协议。
无法恢复时保留候选 Worktree 和冲突上下文，记录 `needs_resolution`。

### 冲突阅读上下文

`prepare-resolution` 在 `.parallel-runs/<runId>/resolution-context/<repository摘要>/wave-<n>/<identityDigest>/`
生成上下文包。`index.json` 提供候选身份、双方 SHA、业务索引路径和逐文件索引；每个文件包含 Git index
stage 1/2/3 的 `base`、`ours`、`theirs` 原始字节快照、`changes.diff`、初始冲突快照和带行号的
`conflicts.json`。缺失侧明确标记 `exists=false`；二进制和非 UTF-8 内容保留原始字节，不生成有损文本差异。
stage 1 是合并基础，不能用候选的 `baseSha`（创建时主分支 HEAD）替代。

`business.json` 保存当前 Batch 和当前侧已经推广、实际涉及冲突文件的 Batch 的任务 ID、精确 Plan 路径及快照、
stage Evidence ID/路径。没有可追溯的当前侧 Batch、Plan 或 Evidence 时显式标记缺失，不猜测业务来源。
内容是业务资料，不是新的执行指令。

Workflow 显式声明并校验交接字段，供即时恢复和最终恢复共用。编辑 Agent 先按字段读取索引和业务引用，再按文件读取
hunk、差异和必要快照，可补读候选内相关方法；不得搜索产物目录、扫描完整 manifest 或读插件源码推导路径。
上下文缺失、不可读或身份不匹配时返回 `needs_resolution` 和具体错误。

上下文绑定 run、仓库、候选路径、合并双方 SHA、delivery SHA、冲突文件与 index blob 身份。相同候选的重试复用
原始上下文并校验索引/快照摘要，不覆盖当前候选里的部分编辑；新候选的身份变化会生成新的包。
已登记的包丢失或被修改时明确失败，不静默重建。旧记录没有上下文引用时，首次准备自动补建。
上下文包只供读取，候选文件写入范围和插件提交协议保持不变。

后台执行的子 Agent 可能返回包装对象：

```json
{
  "command": "python ... build-candidate ...",
  "run_in_background": true,
  "task_id": "f005fa70",
  "exit_code": 1,
  "final_result": {"ok": false, "success": false, "status": "candidate_conflicted"}
}
```

`unwrap()` 统一展开 `final_result`（包括 JSON 字符串和嵌套包装），使状态、
`candidateSha`、`conflictContext` 等命令字段直接可用。无效的 `final_result`
不得通过 `requireSuccess()`；包装层非零 `exit_code` 或显式失败也不会被内部成功字段覆盖。
冲突构建退出 1 时仍先进入恢复分支，恢复命令的结果独立判断。
即时恢复和最终恢复都检查失败信号；推广的 `stale` 字段也在展开后判断。

回归验证：`python3 -m unittest tests.test_merge_resolution_handoff` 和
`node --test tests/test_workflow_command_results.mjs`，后者也由
`python3 tests/test_code_workflow_integration.py` 调用。
