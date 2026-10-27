# 前端 UTest 临时跳过

当前固定 Code Workflow 在 Review 通过后调用
`parallel_batch_stage.py review-checkpoint`，由其中的 `prepare-test`
按 manifest 的真实 `executionLane` 决定是否执行 UTest。

- `frontend`：记录 `stageStates.test.status=skipped` 和
  `frontend_utest_temporarily_disabled`，保存 `outcome=SKIP` 的阶段审计记录。
  不启动测试 Agent，不初始化测试环境、不生成测试源码、不执行测试或编译。
  复用 Review 后已有交付 SHA，继续 gate 和候选合并。
- `backend` 及其他非前端 lane：返回 `test_required`，继续原有 UTest 流程。

前端不能通过 `start --stage test` 重新开启已禁用的测试阶段。
跳过记录出现在 scheduler 的 `manifest.skippedUtests`、最终 evidence aggregate
和 Workflow 的 `skippedUtests` 字段中，不得当成测试通过。跳过不删除历史测试
文件、测试意图或此前的失败记录。Review 和最终 B-E2E 继续执行。

回归：

```bash
python3 -m unittest tests.test_parallel_staged_pipeline
node --test tests/test_workflow_commit_messages.mjs tests/test_workflow_command_results.mjs
```
