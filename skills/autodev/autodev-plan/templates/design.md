# 设计契约模板

> 由 Plan 阶段生成，写入 `{工作目录}/design.md`。本文件承载需求契约、行为规格和技术设计，是后续 `PLAN.md`、编码、测试和验收的依据。

---

```markdown
# 设计契约: [来自 PRD 的标题]

来源: PRD.md + explore 阶段结论 + 现有代码/项目约束
状态: 待执行
创建时间: [ISO 日期时间]

## 1. Proposal / 需求契约

### Why
[1-3 句话说明为什么要做，解决什么问题。]

### What Changes
- [用户可见或系统外部可观察的变化]
- [新增/修改/移除的能力]

### Scope
- 本轮包含:
- 本轮不包含:

### Impact
- 影响模块:
- 影响用户/角色:
- 影响配置/权限/数据:

## 2. Behavior Specs / 行为规格

### ADDED Requirements

#### Requirement: [能力名]
[系统 SHALL/MUST 满足的外部可观察行为。]

##### Scenario: [场景名]
- **WHEN** [触发条件]
- **THEN** [期望结果]

### MODIFIED Requirements

#### Requirement: [已有能力名]
[如果修改已有行为，写完整的新行为。无则写“无”。]

### REMOVED Requirements

#### Requirement: [移除能力名]
**Reason:** [移除原因。无则写“无”。]
**Migration:** [迁移方式。无则写“无”。]

## 3. API Decisions / 接口决策

- **x-auto-no-http-api:** [true/false]
- **说明:** [无 HTTP/API 时说明原因；有 API 时说明入口和契约。]

| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |
|----|--------|--------------|---------|----------|--------|-------------------|--------|
| API-01 | [GET/POST/无] | [路径或函数入口] | [请求约束] | [响应约束] | [错误处理] | [权限/租户/审计假设] | 已确认/待确认 |

## 4. Data Decisions / 数据决策

- **x-auto-no-sql:** [true/false]
- **说明:** [无数据库变更时说明原因；有数据变更时说明表、字段、索引、迁移。]

| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |
|----|-------------|--------|--------|-----------------|----------|--------|
| DATA-01 | [表/模型] | [新增/修改/无] | [字段和含义] | [索引/迁移] | [回滚方式] | 已确认/待确认 |

## 5. Technical Design / 技术设计

### Current State
[现有代码、模块、流程、约束。引用实际路径。]

### Decisions
| ID | Decision | Rationale | Alternatives | Status |
|----|----------|-----------|--------------|--------|
| D-01 | [技术决策] | [原因] | [备选方案] | 已确认/待确认 |

### Integration Points
- [模块/文件/入口路径]

## 6. Risks / Open Questions

| ID | Type | Description | Impact | Owner/Next Step |
|----|------|-------------|--------|-----------------|
| R-01 | 风险/待确认 | [描述] | [影响] | [下一步] |
```
