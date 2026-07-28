# [Capability Name] Specification

Capability-ID: CAP-[capability-name]
来源: proposal.md + PRD.md + explore 阶段结论

> 稳定 ID 规则：
> - Requirement 标题：`### REQ-<capability>-NNN: <标题>`，NNN 三位递增；`<capability>` 与本文件目录名一致。
> - Scenario 标题：`#### SCN-<capability>-NNN-NN: <标题>`，前缀必须对应本文件中已存在的 REQ。
> - 改标题不改 ID；Requirement 删除后其 ID 不复用；ID 在同一 feature 内全局唯一。

## ADDED Requirements

### REQ-[capability]-001: [能力名]

The system SHALL [外部可观察行为]。

#### SCN-[capability]-001-01: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## MODIFIED Requirements

### REQ-[capability]-002: [已有能力名]

[如果修改已有行为，写完整的新行为。无则写“无”。]

#### SCN-[capability]-002-01: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## REMOVED Requirements

### REQ-[capability]-003: [移除能力名]

**Reason:** [移除原因。无则写“无”。]
**Migration:** [迁移方式。无则写“无”。]

#### SCN-[capability]-003-01: [旧入口被触发]

- **WHEN** [旧入口、旧条件或旧分支被触发]
- **THEN** [系统的期望响应：拒绝 / 迁移提示 / 兼容行为]
