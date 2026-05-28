# 技术设计模板

> 由 Plan 阶段生成，写入 `{工作目录}/design.md`。本文件承载接口、数据、技术设计和风险决策；行为契约以 `{工作目录}/specs/**/*.md` 为准。

---

```markdown
# 技术设计: [来自 proposal/specs 的标题]

来源: proposal.md + specs/**/*.md + design exploration 结论 + 现有代码/项目约束
状态: 待执行
创建时间: [ISO 日期时间]

## 1. Context / 输入上下文

- **Feature:** {slug}
- **Proposal:** proposal.md
- **Specs:** specs/[capability]/spec.md
- **当前代码现状:** [现有模块、接口、数据模型、约束，引用实际路径]

## 2. Spec Traceability / 规格追踪

| Spec | Requirement / Scenario | Design Coverage |
|------|------------------------|-----------------|
| specs/[capability]/spec.md | Requirement: [name] / Scenario: [name] | API-01 / DATA-01 / D-01 |

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
