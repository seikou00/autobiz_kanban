# 技术设计模板

> 由 Plan 阶段生成，写入 `{FEATURE_DIR}/design.md`。本文件承载接口、数据、技术设计和风险决策；行为契约以 `{FEATURE_DIR}/specs/**/*.md` 为准。

## 稳定 ID 规范

- API 决策统一使用 `API-001`、`API-002` ...
- Data 决策统一使用 `DATA-001`、`DATA-002` ...
- 技术决策统一使用 `D-001`、`D-002` ...
- `Spec Traceability`、`API Decisions`、`Data Decisions`、`Technical Design` 中的引用必须能回到 `specs/<capability>/spec.md#REQ-001` / `#SCN-001`。
- 若 `x-auto-no-http-api: true` 或 `x-auto-no-sql: true`，不要为对应类型伪造 `API-*` / `DATA-*` 占位 ID。
- 新建决策继续递增，不允许重用已删除或已废弃的 ID。

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
| specs/[capability]/spec.md | Requirement [REQ-001] / Scenario [SCN-001] | API-001 / DATA-001 / D-001 |

## 3. API Decisions / 接口决策

- **x-auto-no-http-api:** [true/false]
- **说明:** [无 HTTP/API 时说明原因；有 API 时说明入口和契约。]
- **填写规则:** false 时至少填写一行 `API-*`；true 时不要创建 `API-*` 行，只写无决策项说明。

| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |
|----|--------|--------------|---------|----------|--------|-------------------|--------|
| API-001 | [GET/POST/无] | [路径或函数入口] | [请求约束] | [响应约束] | [错误处理] | [权限/租户/审计假设] | 已确认/待确认 |
| 无决策项 | 无 | 无 | 无 | 无 | 无 | 无 | 仅当 x-auto-no-http-api=true 时保留 |

## 4. Data Decisions / 数据决策

- **x-auto-no-sql:** [true/false]
- **说明:** [无数据库变更时说明原因；有数据变更时说明表、字段、索引、迁移。]
- **填写规则:** false 时至少填写一行 `DATA-*`；true 时不要创建 `DATA-*` 行，只写无决策项说明。

| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |
|----|-------------|--------|--------|-----------------|----------|--------|
| DATA-001 | [表/模型] | [新增/修改/无] | [字段和含义] | [索引/迁移] | [回滚方式] | 已确认/待确认 |
| 无决策项 | 无 | 无 | 无 | 无 | 无 | 仅当 x-auto-no-sql=true 时保留 |

## 5. Technical Design / 技术设计

### Current State
[现有代码、模块、流程、约束。引用实际路径。]

### Decisions
| ID | Decision | Rationale | Alternatives | Status |
|----|----------|-----------|--------------|--------|
| D-001 | [技术决策] | [原因] | [备选方案] | 已确认/待确认 |

### Integration Points
- [模块/文件/入口路径]

## 6. Risks / Open Questions

| ID | Type | Description | Impact | Owner/Next Step |
|----|------|-------------|--------|-----------------|
| R-01 | 风险/待确认 | [描述] | [影响] | [下一步] |
```
