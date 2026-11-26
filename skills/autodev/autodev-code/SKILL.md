---
name: autodev-code
description: 在当前仓库的普通对话中执行一个 Code Batch，完成编译与同会话 Review。
version: v3.1
allowed-tools: execute read_file grep glob write_file edit_file write_todos
---

# /autodev-code

每条对话只执行一个 Batch。进入对话先调用现有 TASK Runner 查看当前该做哪批。

```bash
python -X utf8 "<pluginPath>/hooks/task_runner.py" next-code-batch
```

- `execute_batch`：在调用任何 TASK 工具之前，先向用户显示返回的 `notice`，报出当前 Batch 的编号、名称，以及本批每个 TASK 的编号和名称。再按返回的执行范围在当前分支执行 [TASK 实现协议](references/task-execution.md)。
- `code_dialogue_already_assigned`：回到结果里的原对话继续当前 Batch。
- `code_dialogue_requires_code_rollback_for_old_execution`：旧 Code 执行态需要先用阶段回退保留源码、重置 Code，再新开对话。
- `batch_done`：在本回合的最后回复中原样显示返回的 `notice`，提醒用户新开 Code 对话继续下一批，并附上下一批的编号和名称。若下一批已分配，提示用户回到该批对话。随后结束本回合。
- `code_done`：全部 Batch 已完成并通过完成门禁。结束本回合。

只修改本批涉及的生产代码，保留已有未提交改动。禁止切换分支、建 worktree、暂存、提交、合并或触发 Workflow。失败时报告具体编译、验证或权限问题，留在原对话处理。其他 Batch 保持等待。

当前批次所有 TASK 实现通过后，在本对话 Review。发现问题直接启动对应 TASK 返修，用原分支编译或回检，再复核实际变更。全部问题解决后登记真实结论。

```bash
python -X utf8 "<pluginPath>/hooks/task_runner.py" finish-code-batch --batch-id "<batchId>" --review-summary "<实际复核结论>"
```

登记成功后报告本批结果，并显示返回的 `notice`。最后一批由 Runner 通过门禁后更新 `code_done`，直接告知用户 Code 已完成。

进入 `code_done` 后，用户询问下一阶段时参考 `${pluginPath}/skills/references/ui-continuation-guide.md`。
