---
name: autodev-verify
description: 已退役的独立 Verify 阶段说明。当前 Code Batch 在同一对话内完成验证和 Review。
version: v2.0.0
---

# 独立 Verify 已退役

当前 Board 没有 `dev.verify` 节点，也没有 `verify_in_progress` 或 `verify_done`
checkpoint。不要调用本技能推进状态、生成独立验收流程或执行新的测试命令。

当前验证结果记录在对应 TASK 的 Evidence 中，Review 结论保存在对应 Batch 的交付记录中。全部批次通过 Code 门禁后进入 `code_done`。
