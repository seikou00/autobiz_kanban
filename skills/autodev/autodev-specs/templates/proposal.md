# Proposal: [来自 PRD 的标题]

来源: PRD.md + explore 阶段结论 + 现有代码/项目约束
状态: 待设计
创建时间: [ISO 日期时间]

## Why

[1-3 句话说明为什么要做，解决什么问题。]

## What Changes

- [用户可见或系统外部可观察的变化]
- [新增/修改/移除的能力]

## Capability Index

> 本表是 capability 的唯一权威索引，`specs/**/*.md` 与本表一一对应。
> Capability ID 用 `CAP-<kebab-case-name>`；Operations 填该 spec 中实际出现的操作段集合
> （ADDED / MODIFIED / REMOVED，多值逗号分隔）；本轮无 capability 时本表正文只写「无」。

| Capability ID | Capability | Operations | Spec Path | Status |
|---------------|------------|------------|-----------|--------|
| CAP-[name] | [kebab-case-name] | ADDED | specs/[name]/spec.md | confirmed |

## Impact

- 影响模块:
- 影响用户/角色:
- 影响接口/API:
- 影响数据/权限/配置:
- 影响测试/验收:

## Out of Scope

- [本轮明确不做的内容]

## Decision Log

> 塑造本轮行为契约的关键决策及理由，供 plan/design 阶段消费；已裁定的决策进本节，仍待确认的进下方 Open Questions。
> 记录门槛（三者取一，且是真实决策不是复述需求）：① 结果偏离“直接读代码/需求会得到的显然做法”；
> ② 有真实备选并择一；③ 改变外部可观察行为的边界或口径、读者不知理由会困惑。
> 显然的、无备选的、需求直接决定的不记。无满足门槛的决策时，本节写“无”。

### DEC-001: [决策标题]

- **决定:** [定了什么行为、边界或口径]
- **为什么:** [理由]
- **否决:** [被否决的备选及原因；无真实备选写“无”]
- **约束:** [关联的稳定 ID：`REQ-<capability>-NNN`；尚未落到具体 Requirement 时写 `CAP-<name>`]

## Open Questions

> specs_done 前本表必须消解：所有行 Status 为「已确认」，或本表正文写「无」；
> 残留「待确认」不得推进 specs_done。

| ID | Question | Impact | Status |
|----|----------|--------|--------|
| Q-01 | [待确认问题；无则写“无”] | [影响] | 已确认 |
