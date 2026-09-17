# Plan 范围澄清

Plan v2 不另设 explore 阶段，也不新增探索产物。正常流程只消费已确认的 Specs、Design、UI_CONTEXT 和实现范围。

- 仓库归属、复用能力或真实依赖不清楚时，定向读取相关仓库信息即可，不预测文件清单。
- 任务过大时按业务切片拆分，例子见 `../references/task-planning.md`。
- 行为或公开技术契约尚未确认时回到 Specs/Design 收口，不能以 Plan 的风险或占位任务代替确认。
- 既定业务契约内的拆分、顺序和内部实现取舍可以自行处理，不要求用户确认常规任务组织。

Plan 只提交 `task-groups.json` 模板所示的 Plan v2；`PLAN.md` 由 writer 生成，不能另写 Design 或执行计划来补充隐藏契约。
