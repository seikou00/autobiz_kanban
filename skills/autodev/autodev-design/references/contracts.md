# 按需设计契约

只读取涉及的部分，将真实内容补入 design.md 的 Technical Design；无对应变更不生成章节或示例行。API/DATA/D 的定义仍使用首列为稳定 ID 的表格，既有 ID 不重排、不复用。

## 接口变化

将 `x-auto-no-http-api` 改为 false，记录入口、请求、响应、错误与权限/租户/审计约束。无 HTTP API 时标记为 true；必要的公开函数契约可写进 D-NNN。

| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit |
|---|---|---|---|---|---|---|
| API-001 | POST | /orders/export | [请求] | [响应] | [失败结果] | [实际约束] |

## 数据变化

将 `x-auto-no-sql` 改为 false，记录模型、字段语义、索引/唯一约束、迁移与回滚；有状态变化时说明状态转换和历史兼容。

| ID | Table/Model | Change | Fields | Index/Migration | Rollback |
|---|---|---|---|---|---|
| DATA-001 | [模型] | [变更] | [字段语义] | [约束与迁移] | [回滚] |

## 外部资料与风险

只引用约束本期技术决策的真实 SRC-NNN、可用快照或契约事实，放在受影响的决策中即可；行为与资料映射仍维护在对应 Spec 的 Source References 表。

有实际风险时追加 `## Risks / 风险`，说明影响和应对。信息缺口涉及行为、公开契约或实现路径时按共享提问协议解决，不以状态词或占位文字代替具体结论。
