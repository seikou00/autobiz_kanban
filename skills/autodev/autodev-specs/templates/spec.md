# [Capability Name] Specification

来源: proposal.md + PRD.md + explore 阶段结论

## 稳定 ID 规范

- Requirement ID 统一使用 `REQ-001`、`REQ-002` ...，并写在 Requirement 标题中。
- Scenario ID 统一使用 `SCN-001`、`SCN-002` ...，并写在 Scenario 标题中。
- 其他阶段引用规格时，使用 `specs/<capability>/spec.md#REQ-001` 或 `specs/<capability>/spec.md#SCN-001`。
- 一个 Requirement 可以有多个 Scenario，但每个 Scenario 只属于一个 Requirement。
- 下面三个操作段的标题一律保留，保持文件形状统一。某段不适用时**删掉该段下的
  Requirement 示例**（段内留空或只写「无」都可以）。不要把「无」写进 Requirement
  正文而保留标题——标题一旦留下就会被索引成一条真需求，流进下游覆盖检查。
- proposal 把该能力列在 New / Modified / Removed 哪一组，就在对应段写 Requirement。
  New 的能力没有存量需求可改可删，MODIFIED / REMOVED 段下不得有 Requirement。

## ADDED Requirements

### Requirement [REQ-001]: [能力名]

The system SHALL [外部可观察行为]。

#### Scenario [SCN-001]: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## MODIFIED Requirements

### Requirement [REQ-002]: [已有能力名]

The system SHALL [修改后的完整新行为]。改已有行为时写完整的新行为，不要只写差异。

#### Scenario [SCN-002]: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## REMOVED Requirements

### Requirement [REQ-003]: [移除能力名]

**Reason:** [移除原因]
**Migration:** [迁移方式]
