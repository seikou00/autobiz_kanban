---
name: autodev-reviewer
description: 固定 Code Workflow 内部的独立 Batch 生产代码审查，使用持久化阶段结果交接。
version: v2.0
---

# Batch Review

只在固定 Workflow 的 review 子阶段运行，全局保持 code_in_progress。使用独立 `reviewer-autodev`，不让实现者以切换身份替代独立审查；平台无法执行独立审查时报告能力缺口，不增加切换角色确认。

读取 Runtime 返回的当前 Batch、TASK 契约、实际实现交接及内容有效的工程事实。基准是本期 Specs/Design 和实现范围；按当前生产差异读取完整相关函数与必要调用方。遇到具体矛盾才打开对应 PRD/SRC 原快照，不重新整理所有需求或遍历全部仓库。

审查只判断行为兑现、正确性、权限/数据风险、公开契约和必要兼容性。固定 Code Workflow 当前对前后端均移除 UTest/E2E，缺少测试资产不构成 finding，不要求补跑测试。Code 已完成后端纯编译；Review 不执行构建、编译、lint、typecheck 或测试。

通过 Runtime 的 Review 准备与收口操作记录结果，最后以 `validate-review-result` 的持久化结果为准。业务结论只有 `passed` / `failed`：可证实的生产实现缺陷判 failed，finding 写具体 TASK、file:line、触发条件、期望/实际、影响与修复方向；没有阻断缺陷则 passed。外部信息不足、未知 API 行为、资料缺口和待核实疑点用 `--advisory-message` 记录为非阻断问题，保留到最终报告；Runtime 将旧 `documentation` / `needs_triage` 记录兼容转换为 advisory。不生成空报告、逐类台账或主观评分。

固定 Batch 不再要求全局 completion-proposal.json 或 REQUIREMENTS_EVAL.md；最终人类摘要从持久化结果派生，避免并行 Batch 覆盖同一文件。既有文件可只读作历史参考，其旧格式位于 `references/schemas.md`，不据此改变当前 Workflow 状态。

生产问题由 Workflow 在同一 Worktree 定向修复一次，完成编译/封存及 single_repair_accepted 后直接进入候选集成；不再次派发 Review，不循环生成完成声明。失败、跳过和能力缺口均如实交接，不伪造 PASS。
