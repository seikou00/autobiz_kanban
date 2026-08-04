# Proposal: [来自 PRD 的标题]

来源: PRD.md + explore 阶段结论 + 现有代码/项目约束
状态: 待设计
创建时间: [ISO 日期时间]

## Why

[1-3 句话说明为什么要做，解决什么问题。]

## What Changes

- [用户可见或系统外部可观察的变化]
- [新增/修改/移除的能力]

## Capabilities

### New Capabilities

- `[capability-name]`: [能力说明，对应 specs/<capability-name>/spec.md]

### Modified Capabilities

- `[existing-capability]`: [修改原因与范围；无则写“无”]

### Removed Capabilities

- `[removed-capability]`: [移除原因；无则写“无”]

## Impact

- 影响模块:
- 影响用户/角色:
- 影响接口/API:
- 影响数据/权限/配置:
- 影响测试/验收:

## Out of Scope

- [本轮明确不做的内容]

## Decision Log

塑造本轮行为契约的关键决策及理由，供 plan/design 阶段消费；`design.md` 的规格追踪表按 `DEC-NNN` 引用本节。
记录门槛（三者取一，且是真实决策不是复述需求）：① 结果偏离「直接读代码/需求会得到的显然做法」；② 有真实备选并择一；③ 改变外部可观察行为的边界或口径、读者不知理由会困惑。
显然的、无备选的、需求直接决定的不记。无满足门槛的决策时，本节正文只写「无」。

### DEC-001: [决策标题]

- **决定:** [定了什么行为、边界或口径]
- **为什么:** [理由]
- **否决:** [被否决的备选及原因；无真实备选写「无」]
- **约束:** [关联的 capability 或 REQ-NNN]

## Open Questions

| ID    | Question | Impact | Status |
|-------|----------|--------|--------|
| Q-001 | [待确认问题；无则写“无”] | [影响] | 已确认/待确认 |
