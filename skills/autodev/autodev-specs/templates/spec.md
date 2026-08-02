# [Capability Name] Specification

来源: proposal.md + PRD.md + explore 阶段结论

## 稳定 ID 规范

- Requirement ID 统一使用 `REQ-001`、`REQ-002` ...，并写在 Requirement 标题中。
- Scenario ID 统一使用 `SCN-001`、`SCN-002` ...，并写在 Scenario 标题中。
- 其他阶段引用规格时，使用 `specs/<capability>/spec.md#REQ-001` 或 `specs/<capability>/spec.md#SCN-001`。
- 一个 Requirement 可以有多个 Scenario，但每个 Scenario 只属于一个 Requirement。

## ADDED Requirements

### Requirement [REQ-001]: [能力名]

The system SHALL [外部可观察行为]。

#### Scenario [SCN-001]: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## MODIFIED Requirements

### Requirement [REQ-002]: [已有能力名]

[如果修改已有行为，写完整的新行为。无则写“无”。]

#### Scenario [SCN-002]: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## REMOVED Requirements

### Requirement [REQ-003]: [移除能力名]

**Reason:** [移除原因。无则写“无”。]
**Migration:** [迁移方式。无则写“无”。]
