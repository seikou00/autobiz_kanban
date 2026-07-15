# Dual-axis Reviewer Prompts

仅在 reviewer coordinator 能启动只读子代理时使用。两个子 reviewer 共享同一份 coordinator 已验证的 scope、commit list、changed-file list 和来源路径，但互相看不到对方结论；它们不得写文件。

## Standards reviewer

传入：

- 每个仓库的 id、path、base SHA、head SHA、scope confidence。
- coordinator 已验证的 diff/commit 命令与 changed files。
- 适用的 standards-source 文件路径。
- `references/standards-baseline.md` 的完整规则。

指令：

> 只执行 Standards 轴。逐个 changed hunk 检查文档化规范违规和有具体维护成本的 smell。每条 finding 返回 id、severity、kind、judgement_call、repo/path/line、standard source、evidence、impact、suggested action。文档化规范可以是 hard violation；smell 永远是 judgement call，不能单独成为 blocker。仓库规范覆盖通用 baseline。跳过已被可信工具证据覆盖的规则、纯偏好和未修改历史代码。不要评价需求是否实现，不要写文件。

## Spec reviewer

传入：

- 每个仓库的 id、path、base SHA、head SHA、scope confidence。
- coordinator 已验证的 diff/commit 命令与 changed files。
- completion proposal、proposal、specs、design、PLAN 和可选 PRD 路径。

指令：

> 只执行 Spec 轴。逐条检查 Requirement / Scenario：缺失或部分实现、实现行为错误、scope creep、跨仓库/API/数据契约不一致，以及验证证据不足。每条 finding 返回 id、severity、kind、repo/path/line、spec source、evidence、impact、required action。引用具体 Requirement / Scenario 或设计决策。不要评价纯代码风格或 smell，不要写文件。

## Coordinator aggregation

- 保留两个轴的原始 finding，不跨轴合并、删除或重新排序严重性。
- 只允许做格式归一化、同一轴内完全重复项去重和证据路径补全。
- 按 `references/schemas.md` 的机械矩阵生成 axis status 与总 Verdict。
- 只有 coordinator 写 `REQUIREMENTS_EVAL.md`。
