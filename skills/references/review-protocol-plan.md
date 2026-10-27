# Plan 语义回检

首份有效计划使用 `critic-autodev-plan-zh` 做一次独立回检。提供 Feature 绝对路径、根 plan.json、writer 的 `reviewChanges` 和命中任务的上游行为/设计；按 root `batches[].path` 读取实际任务，PLAN.md 尚未投影。

只判断：任务是否是完整且连贯的交付、仓库职责是否正确、依赖是否来自真实交付需要。引用存在性、场景覆盖、D 的唯一归属和 DAG 环由 writer/门禁检查，不重复逐项审计。设计合理性和代码事实由 Design 承担，兼容精简及旧设计，不要求 EVD 表，不读业务代码。

问题只写具体 Task、证据、影响与建议修改的输入字段；无问题直接说明，不生成严重度分节、分类台账或空报告。

修复后由主会话核对成立的问题：`reviewChanges.reviewScope=affected` 时只复核 `affectedTaskIds` 及直接协作关系，`full` 时覆盖本期任务；`none` 只有存在先前通过结论时才可复用，否则仍须首次回检。内部拆分和排序自行修正，改变已确认行为或公开契约才按共享提问协议处理。反复出现相同未解决问题时报告阻断。

修改 Plan 输入后重新 prepare，writer 仍做全局机器校验。回检通过才 publish，定稿后变更走 Plan rollback；不在 Plan 中重开技术设计讨论或手改 writer 产物。
