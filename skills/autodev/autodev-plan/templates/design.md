# 技术设计模板

> 由 Plan 阶段生成，写入 `{FEATURE_DIR}/design.md`。

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
- **当前代码现状:** [一句话概述；逐条事实进 Code Evidence]

## 2. Code Evidence / 代码探索证据

> 探索得到的代码事实逐条落盘：EVD ID 稳定、不复用；重入时只复核并更新变化的条目。
> 与 DEC/REQ 冲突的观察不得直接覆盖本表或改写 specs/Decision Log，列为 reconciliation（R-xx Type=读码差异）进裁定门。

| Evidence ID | Path / Symbol | Observed Fact | Verified At |
|-------------|---------------|---------------|-------------|
| EVD-001 | [路径::符号] | [观察到的现状事实] | [commit] |

## 3. Spec Traceability / 规格追踪

| Requirement | Scenarios | Decision | Design Coverage | Evidence |
|-------------|-----------|----------|-----------------|----------|
| REQ-[capability]-001 | SCN-[capability]-001-01, -02 | DEC-001/无 | API-01 / DATA-01 / D-01 | EVD-001 |

## 4. API Decisions / 接口决策

- **x-auto-no-http-api:** [true/false]
- **说明:** [无 HTTP/API 时说明原因；有 API 时说明入口和契约。]

| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |
|----|--------|--------------|---------|----------|--------|-------------------|--------|
| API-01 | [GET/POST/无] | [路径或函数入口] | [请求约束] | [响应约束] | [错误处理] | [权限/租户/审计假设] | 已确认 |

## 5. Data Decisions / 数据决策

- **x-auto-no-sql:** [true/false]
- **说明:** [无数据库变更时说明原因；有数据变更时说明表、字段、索引、迁移。]

| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |
|----|-------------|--------|--------|-----------------|----------|--------|
| DATA-01 | [表/模型] | [新增/修改/无] | [字段和含义] | [索引/迁移] | [回滚方式] | 已确认 |

## 6. Technical Design / 技术设计

### Current State
[基于 Code Evidence 的综合描述；单条事实引用 EVD-xx，不另行复述。]

### Decisions
| ID | Decision | Rationale | Alternatives | Status |
|----|----------|-----------|--------------|--------|
| D-01 | [技术决策] | [原因] | [备选方案] | 已确认 |

### Integration Points
- [模块/文件/入口路径]

## 7. Risks / Open Questions

| ID | Type | Description | Impact | Owner/Next Step |
|----|------|-------------|--------|-----------------|
| R-01 | 风险/待确认/读码差异 | [描述；读码差异写明「spec/DEC 说 X，代码是 Y（EVD-xx）」] | [影响] | [下一步] |

> Type 为「待确认」或「读码差异」的 R-xx 条目属于设计确认环节的逐条裁定范围；用户裁定后回写本表（更新 Type 或在 Owner/Next Step 记录裁定结果）。
> 进入 PLAN 生成前，本文件所有表格不得残留「待确认」「读码差异」单元格；裁定结果（含用户提供的链接/内容）须回写到对应行或章节。
```
