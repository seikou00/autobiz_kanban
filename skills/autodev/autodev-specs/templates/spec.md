# [Capability Name] Specification

来源: proposal.md + PRD.md + explore 阶段结论

## ADDED Requirements

[无新增行为时写“无”。只有当前系统不存在对应外部可观察能力、入口、流程或业务结果时，才在本段新增 Requirement。删除本说明和未使用的占位项。]

### Requirement [REQ-001]: [能力名]

The system SHALL [外部可观察行为]。

#### Scenario: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## MODIFIED Requirements

[无既有行为修改时写“无”。已有能力仍存在，但条件、输出、校验、权限、错误码、状态流、数据口径、UI 状态或交互分支发生变化时，写入本段。必须写修改后的完整行为，不只写差异。删除本说明和未使用的占位项。]

### Requirement [REQ-002]: [已有能力名]

The system SHALL [修改后的完整外部可观察行为]。

#### Scenario: [场景名]

- **WHEN** [触发条件]
- **THEN** [期望结果]

## REMOVED Requirements

[无移除行为时写“无”。已有能力、入口、分支或业务结果本轮后不再支持、不可访问或不再生效时，写入本段。必须说明移除原因、迁移/兼容方式，并给出旧入口触发时的期望行为。删除本说明和未使用的占位项。]

### Requirement [REQ-003]: [移除能力名]

**Reason:** [移除原因。]
**Migration:** [迁移/兼容方式；无则写“无”。]

#### Scenario [SCN-003]: [旧行为不可用]

- **WHEN** [旧入口、旧条件或旧分支被触发]
- **THEN** [系统期望响应，例如拒绝、隐藏、提示、转向新流程或保持历史数据只读]
