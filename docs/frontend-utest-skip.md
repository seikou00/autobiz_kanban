# Code 测试阶段移除说明

前端与后端使用同一 Code 流程：prepare → implement → review → 交付 → 证据汇总。
Review checkpoint 不再调用 prepare-test；交付 gate 不接受 `--skip-tests`，
aggregate 使用 `--finish-workflow`，不接受 `--skip-e2e`。

新 Run 不创建 test 或 V-E2E 阶段，不登记 SKIP 证据，最终报告不包含
skippedUtests、e2eStatus 或 e2eSkippedReason。旧 Plan 的测试意图保留为设计上下文，
启动时投影为当前 Code 执行契约。历史证据读取兼容不代表恢复测试执行。

后端 TASK 的生产纯编译和 Review 门槛仍保留；dev 阶段尚未添加独立 E2E。

回归：

```bash
python -m unittest tests.test_code_pipeline_contract tests.test_parallel_delivery
node --test tests/test_workflow_delivery_control.mjs tests/test_workflow_review_recovery.mjs
```
