---
name: autodev-specs
description: Dev 阶段行为规格生成。
version: v1.16.09072
---


# /autodev-specs — Proposal + Behavior Specs

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## 阶段定位

`autodev-specs` 是 Dev 阶段的上下文边界，负责把PRD.md输入转成稳定的行为契约。

本阶段只做：

- **为什么做**：沉淀到 `proposal.md`
- **系统应该表现为什么行为**：沉淀到 `specs/**/*.md`

本阶段不考虑：

- **怎么实现 / 怎么拆编码任务**
- **怎么改代码**

## 实现范围

生成 proposal/specs 前读取 `IMPLEMENTATION_SCOPE.json`。`backend_only` 只生成后端可实现、可验证的行为，禁止页面、交互和前端路由 Scenario；`frontend_only` 只生成前端行为，后端 API 只能作为外部依赖；`full_stack` 保持现有行为。范围缺失时按兼容规则视为 `full_stack`。

## 输入与输出

读取输入:
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`；存在 `source-context.json` 时读取其中 `targets` 含 `spec` 的要求及其 `sources/SRC-NNN/` 快照
- 与当前 feature 相关的现有代码、接口、数据模型、测试、配置
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json`（缺失时必须按目标分支 UI 协议生成；不得从 Markdown 关键词临时推断 UI 范围）

输出产物：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/<capability>/spec.md`

同步维护（非阶段产物）：

- 会话工作区 `CONTEXT.md`（领域词汇表）：术语对齐后当场回写，协议见 `${pluginPath}/skills/references/domain-context.md`
- Feature 的 `UI_CONTEXT.json`：只通过 `${pluginPath}/hooks/ui_context_writer.py` 更新并在 specs 完成时锁定

禁止写入：

- 业务代码、测试代码、配置、迁移脚本

## 写入 checkpoint

开始生成规格前推进到 `specs_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_in_progress
```

## Explore 协议

进入探索模式。先把需求、现状、隐性约束和行为边界想清楚，再生成 specs。

> 进入探索前先使用 write_todos 工具建立一份覆盖本轮宏观流程的任务清单，并随阶段推进实时更新状态。

使用 task 工具，指定 Explore-autodev 角色进行探索。子代理直接将下面的内容作为prompt。

---

探索时必须：

- 从PRD.md中提取目标、用户角色、主流程、验收标准、非目标。
- 从 `source-context.json` 提取全部 `SRC-NNN-RNNN`。`snapshot_only` 使用已保存快照，不重新索取；只有 `never_provided` 且影响行为时才列入信息缺口。
- 阅读现有代码，识别已有接口、数据模型、权限。
- 将上游需求改写为外部可观察行为，不要把实现猜测写成需求。
- 识别 capabilities：一组可以独立命名、独立验收的能力边界，例如 `order-export`、`approval-reminder`。
- 与用户对齐了术语或规范代码名时，按 `${pluginPath}/skills/references/domain-context.md` 当场回写会话工作区 `CONTEXT.md`；只收已对齐术语。

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、分页、异步行为，但接口形态还不准确，作为待讨论点返回主代理。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据语义还不准确，作为待讨论点返回主代理。
- 讨论时只提出影响实现路径或验收结果的关键问题，并给出当前建议、备选方案和影响面；不要机械问卷。

探索结束时先生成待确认问题清单。需求或用户材料中的「待补充」「待提供」「后续给出」如影响行为契约，逐项列入；无待确认项时写「无」并继续生成产物。

讨论输出：

```markdown
## 行为/API/数据决策待确认

我不建议现在直接生成 specs，因为以下决策会影响行为契约或实现路径：

| ID | 类型 | 决策点 | 当前建议 | 备选方案 | 影响 | 需要确认 |
|----|------|--------|----------|----------|------|----------|
| SPEC-001 | Behavior | [行为边界] | [建议] | [备选] | [影响验收] | [问题] |
| API-001 | API | [接口入口/请求响应/错误码] | [建议] | [备选] | [影响任务/验收] | [问题] |
| DATA-001 | Data | [表/字段/状态/约束] | [建议] | [备选] | [影响任务/验收] | [问题] |
```
---


### 待确认问题裁定门

- 仅裁定讨论表中的待确认条目；没有条目时直接生成产物。
- 所有条目拿到用户裁定之前，禁止生成 proposal 与 specs。展示不等于裁定：清单列出来但没有逐条提问，等于没裁定。
- 逐条提问；`id` 与讨论表条目 ID 对应（如 `SPEC-001` → `spec_001`）。不设置 `autoResolutionMs`，必须等待明确答复；发起后停止执行，不得在同一轮继续生成产物。
- 回写：拿到裁定后立即回写讨论表对应行，用户给出的链接/字段/方案必须先写进对应行才算「已确认」。
- 消解自查：生成产物前确认讨论表无「待确认」单元格，回写内容无 TBD/待补充/待提供/占位，无对缺失材料的引用（「根据实际文档」「以实际接口为准」「编码阶段补充」等）；任一命中回到逐条裁定。
- 全部条目裁定后直接生成 proposal 与 specs，不再确认 capability 切分或规格范围。

## 生成 proposal.md

按 `${pluginPath}/skills/autodev/autodev-specs/templates/proposal.md` 输出。

生成前一次性建立规格清单，列出每个 capability 的名称、分类与 `REQ IDs / SCN IDs`。同一份清单用于生成 proposal 与全部 specs，不逐文件临时起名。编号顺序取当前 feature 内未占用的三位数字即可。

capability 的变更分类写进 `## Capabilities` 节：

- `ADDED`：当前系统没有对应的外部可观察能力、入口、流程或业务结果；本轮新增一个可独立验收的行为边界。复用已有组件、接口或表，不影响 `ADDED` 判定。
- `MODIFIED`：已有能力仍然存在，但本轮改变或扩展其外部可观察行为，包括条件、输出、校验、权限、错误码、状态流、异步时机、数据口径、UI 状态或交互分支。给已有流程增加筛选项、字段、按钮、状态、限制条件或兼容逻辑，默认是 `MODIFIED`。
- `REMOVED`：已有能力、入口、分支或业务结果在本轮后不再支持、不可访问或不再生效；必须说明移除原因、迁移/兼容方式，以及旧入口被触发时的期望行为。
- 同一用户目标同时包含新增独立能力和修改既有能力时，拆成不同 capability 或同一 spec 内不同 Requirement。
- 分类前用 `git ls-files` / `git grep` 逐项搜索既有 specs 与代码入口：已有相同外部可观察能力归 `Modified`，没有归 `New`；无法判断时回到用户确认。proposal 只写分类结果。
- 本轮某个分组无 capability 时该组写 `无`。

必须包含：

- **Why**：为什么要做。
- **What Changes**：用户可见或系统外部可观察变化。
- **Capabilities**：按 New / Modified / Removed 分组列出本轮能力，名称使用 kebab-case。
- **Impact**：影响模块、接口、数据、权限、配置、测试或运维。
- **Out of Scope**：本轮明确不做的内容。
- **Decision Log**：本阶段定下的关键取舍，每条一个 `### DEC-NNN`，写决定/为什么/否决/约束。`design.md` 的规格追踪表按 `DEC-NNN` 引用本节，是 specs 阶段决策传到 plan 的唯一通道。记录门槛三者取一，且必须是真实决策不是复述需求：① 结果偏离「直接读代码/需求会得到的显然做法」；② 有真实备选并择一；③ 改变外部可观察行为的边界或口径。显然的、无备选的、需求直接决定的不记；无满足门槛的决策时本节正文只写「无」。
- **Open Questions**：讨论表中的每条待确认项落一行，`Status=已确认`，裁定结论体现在对应的 Requirement/Scenario 上；本轮无待确认项时本节正文只写「无」。

## 生成 specs/**/*.md

按 `${pluginPath}/skills/autodev/autodev-specs/templates/spec.md` 输出。

规则：

- 按规格清单统一生成全部 spec，再进入校验；不得生成一个、校验一个、修复一个。
- UI 范围只以 `UI_CONTEXT.json` 为准，且只通过 `${pluginPath}/hooks/ui_context_writer.py` 更新（调试只用 `validate` / `show --summary`），字段规则见 `skills/autobiz/references/ui-context.md`。本阶段负责：`uiRequired=true` 时至少有一个 UI capability，并在 `capabilities[]` 回链对应 `REQ-xxx` 与 `SCN-xxx`；`uiRequired=false` 时不生成 UI capability 并写明 `notApplicableReason`；完成时固化 `decisionStatus=locked`、`lockedAtCheckpoint=specs_done`。
- specs 定义 **WHAT**：用 SHALL/MUST 写外部可观察行为，不写实现步骤、类名、SQL 细节或任务拆分。
- `Capabilities` 与 `specs/<capability>/spec.md` 双向一一对应；不值得单独成 spec 的 capability 回到 proposal 移除或并入其他 capability。
- 操作段与 proposal 分组对齐：`New` 的 spec 在 `ADDED Requirements` 下写 Requirement，`MODIFIED`/`REMOVED` 段下不得有 Requirement；`Modified`/`Removed` 的 spec 必须在同名操作段下写 Requirement，另加 `ADDED` 允许。每个 Requirement 只进一个操作段；无内容的操作段保留标题，段下不写 Requirement。
- Requirement 用 `### Requirement REQ-NNN: <标题>`，Scenario 用四级标题 `#### Scenario SCN-NNN: <标题>` 写在所属 Requirement 之下（ID 外的方括号可有可无），每个 Requirement 至少一个 Scenario。
- `NNN` 三位数字，同一 feature 内唯一，跨 spec 文件不得重号；改标题不改 ID，删除后不复用，允许跳号，不得为顺序重排已有 ID。
- `MODIFIED Requirements` 写修改后的完整行为，覆盖旧行为受影响的触发条件和新期望，不写「新增字段」这类差异片段。
- `REMOVED Requirements` 写 `**Reason:**` 与 `**Migration:**` 两行，并用 Scenario 描述旧入口被触发时的期望响应。
- `source-context.json` 中 `targets` 含 `spec` 的 `SRC-NNN` 进入 `Source References / 外部资料引用` 表并映射 REQ/SCN（同一来源或一组模板约束可映射同一个 REQ/SCN）；来源追溯只放这张表，Requirement/Scenario 正文不堆 `SRC-NNN-RNNN`。无 spec 要求的来源无需列入；PRD 无来源项时该节正文写「无」。
- 外部接口资料至少核对 method/path、鉴权、请求/响应、错误和超时中与本期有关的内容；资料与用户已确认行为矛盾时回流澄清，不得自行选择一个版本。
- 模板槽位与 `TBD`／`待补充`／`待提供`／`待定` 不得留在产物里（Markdown 链接不算槽位）。

## 门禁修复派发

structure 与 final 两道门禁走同一条修复通道，主流程不自己跑「校验—修复—重跑」循环。先检查当前平台是否提供 task 工具，然后二选一执行：

- **`independent_task`**：task 工具可用时，使用 task 工具指定 `specs-gate-fixer-autodev` 角色，task prompt 写明 `phase`（`structure` 或 `final`）与当前 feature。
- **`inline_main_agent`**：task 工具被平台禁用或不可用时，主 agent 读取 `${pluginPath}/agents/specs-gate-fixer.md`，按其修复循环内联执行。

拿到 `PASS` 即继续后续步骤。`BLOCKED` 时按「需主代理处理」表逐项收口，收口后重新执行：

- `ask_user`：按 `${pluginPath}/skills/references/ask-user-question.md` 逐条裁定，禁止自行填值。
- `return_specs` / `return_plan`：停止本阶段并回流。
- 需要改动行为契约（新增或改写 Requirement/Scenario、调整 capability 分类、变更范围）的失败项：由主流程改完再执行。


## 完成条件

- 「输入与输出」列出的产物都已生成，`specs/` 下至少存在一个 `spec.md`。
- `UI_CONTEXT.json` 已生成或更新，格式符合 `skills/autobiz/references/ui-context.md`；`decisionStatus=locked`，UI capability 的 `specRefs` 可解析。
- 能力双向对应、REQ/SCN ID 格式与唯一性、每个 Requirement 至少一个 Scenario、proposal 必备章节由 structure / final 门禁判定，失败无法写入 specs_done。
- `Open Questions` 每行都经逐条裁定门消解（`Status=已确认`），或本节正文只写「无」。

产物契约预检与回检修复均通过后推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
