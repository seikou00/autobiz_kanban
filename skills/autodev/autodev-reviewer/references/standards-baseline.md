# Standards Review Baseline

Standards 轴只评审当前 Feature 审查范围内的 changed hunks 及理解这些 hunk 所需的最小上下文。

## 规范来源优先级

1. 对目标文件路径生效的 `AGENTS.md` 或等价目录级指令。
2. 仓库中的 `CODING_STANDARDS.md`、`CONTRIBUTING.md`、开发者文档和语言/框架约定。
3. 仓库级架构文档中适用于该路径的编码与模块约定。
4. 当前仓库邻近代码中稳定、重复出现且与书面规范不冲突的模式。
5. 下方 smell baseline。

仓库明确规范覆盖通用 baseline。只有可信的 lint/format/typecheck 证据确实覆盖当前审查快照时，才跳过由工具完全执行的规则；不能因为仓库存在配置文件就假设工具已经运行。

## Finding 规则

- 文档化规范违规必须引用规范文件、规则和代码位置。
- Smell 只能写成 `judgement_call`，必须说明具体维护成本，不能单独形成 blocker。
- 不报告纯偏好、无影响的格式问题、未修改历史代码或无证据的架构猜测。
- Feature 自身 `design.md` 的 API、数据和模块决策属于 Spec 轴，不得作为 Standards finding 重复报告。
- 规范与需求冲突时不要自行取舍；分别保留在 Standards 与 Spec 轴中。

## Smell baseline

- **Mysterious Name**：名称不能表达职责或数据含义。建议重命名；无法诚实命名时检查设计边界。
- **Duplicated Code**：同一逻辑形状在本次变更多处出现。建议提取共享实现。
- **Feature Envy**：方法主要操作其他对象的数据。建议把行为移动到拥有数据的模块。
- **Data Clumps**：相同字段或参数反复成组传递。建议形成明确类型。
- **Primitive Obsession**：primitive/string 代替有约束的领域概念。建议使用小型领域类型。
- **Repeated Switches**：同一维度的 switch/if cascade 在多处重复。建议集中映射或使用合适的多态。
- **Shotgun Surgery**：一个逻辑变化要求分散修改许多位置。建议聚合共同变化的职责。
- **Divergent Change**：同一模块因多个无关原因被修改。建议拆分变化原因。
- **Speculative Generality**：为当前规格不存在的未来需求增加抽象、参数或 hook。建议删除或内联。
- **Message Chains**：调用方依赖过长的对象导航链。建议在边界对象后隐藏导航。
- **Middle Man**：新增层主要做无价值转发。建议直接调用真实职责方。
- **Refused Bequest**：继承方忽略或推翻大部分父契约。建议改用组合。
