---
name: autodev-specs
description: Dev 阶段行为规格生成。
version: v1.15.0903
---

## 缺失产物处理
```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-specs --feature "${feature}" --plain
```


# /autodev-specs — Proposal + Behavior Specs

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## 阶段定位

`autodev-specs` 是 Dev 阶段的上下文边界，负责把上游需求输入转成稳定的行为契约。

本阶段只回答：

- **为什么做**：沉淀到 `proposal.md`
- **系统应该表现为什么行为**：沉淀到 `specs/**/*.md`

本阶段不回答：

- **怎么实现 / 怎么拆编码任务**：交给后续设计与计划阶段
- **怎么改代码**：交给后续编码阶段

## 实现范围

生成 proposal/specs 前读取 `IMPLEMENTATION_SCOPE.json`。`backend_only` 只生成后端可实现、可验证的行为，禁止页面、交互和前端路由 Scenario；`frontend_only` 只生成前端行为，后端 API 只能作为外部依赖；`full_stack` 保持现有行为。范围缺失时按兼容规则视为 `full_stack`。

## 输入与输出

读取输入:
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md`；存在 `source-context.json` 时读取其中 `targets` 含 `spec` 的要求及其 `sources/SRC-NNN/` 快照
- 与当前 feature 相关的现有代码、接口、数据模型、测试、配置

输出产物：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/<capability>/spec.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/SPECS_REVIEW.md`（回检结论，模板 `${pluginPath}/skills/autodev/autodev-specs/templates/specs-review.md`）

同步维护（非阶段产物）：

- 会话工作区 `CONTEXT.md`（领域词汇表）：术语对齐后当场回写，协议见 `${pluginPath}/skills/references/domain-context.md`

禁止写入：

- 业务代码、测试代码、配置、迁移脚本
- 后续阶段报告

## 写入 checkpoint

开始生成规格前推进到 `specs_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_in_progress
```

## Explore 协议

进入探索模式。先把需求、现状、隐性约束和行为边界想清楚，再生成 specs。

> 进入探索前先使用 write_todos 工具建立一份覆盖本轮宏观流程的任务清单，并随阶段推进实时更新状态。

使用 task 工具，指定 Explore-autodev 角色进行探索。子代理按下面的要求返回结构化内容供主代理参考。

探索时必须：

- 从上游需求输入提取目标、用户角色、主流程、验收标准、非目标。
- 从 `source-context.json` 提取全部 `SRC-NNN-RNNN`。`snapshot_only` 使用已保存快照，不重新索取；只有 `never_provided` 且影响行为时才列入信息缺口。
- 阅读现有代码，识别已有接口、数据模型、权限、租户、审计、错误体、分页、状态流、配置和测试风格。
- **只探索源码，不碰编译/生成产物**：`target/`、`build/`、`out/`、`bin/`、`*.class`、`*.jar/war/ear`、`__pycache__/`、`*.pyc`，以及一切 `.gitignore` 命中的路径都不是事实源。扫描优先 `git ls-files <pattern>` 找文件、`git grep <regex>` 搜内容，不要用裸 `find`/`grep` 做全库扫描。例外：某生成物本身就是问题对象时可读，但须标注「生成物」并回溯到其生成器/源码。
- 将上游需求改写为外部可观察行为，不要把实现猜测写成需求。
- 识别 capabilities：一组可以独立命名、独立验收的能力边界，例如 `order-export`、`approval-reminder`。
- 与用户对齐了术语或规范代码名时，按 `${pluginPath}/skills/references/domain-context.md` 当场回写会话工作区 `CONTEXT.md`；只收已对齐术语。

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先讨论。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据语义还不准确，先讨论。
- 讨论时只提出影响实现路径或验收结果的关键问题，并给出当前建议、备选方案和影响面；不要机械问卷。

探索结束时先生成待确认问题清单。需求、PRD 或用户材料中的「待补充」「待提供」「后续给出」如影响行为契约，逐项列入；无待确认项时写「无」并继续生成产物。

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

### 待确认问题裁定门

- 仅裁定讨论表中的待确认条目；没有条目时直接生成产物。
- 所有条目拿到用户裁定之前，禁止生成 proposal 与 specs。展示不等于裁定：清单列出来但没有逐条提问，等于没裁定。
- 按 `ask-user-question.md` 协议用 `request_user_input` 逐条提问，每轮最多 3 项；`id` 与讨论表条目 ID 对应（如 `SPEC-001` → `spec_001`）。不设置 `autoResolutionMs`，必须等待明确答复；发起后停止执行，不得在同一轮继续生成产物。
- 选项闭集：每条给 2–3 个互斥选项，语义只能取——①「按当前建议确认 (Recommended)」；②「采纳备选：<方案>」；③「需要调整」（用户给出修改意见，吸收后更新讨论表并重新裁定）；④「暂停，拿到材料后继续」（仅信息缺口型条目可用）。
- 信息缺口型条目（缺接口文档 url、字段定义、外部约定等）：`question` 中写「若现在能提供，请在『其他』中粘贴链接或具体内容」；预设选项只从「调整方案移除该依赖」「暂停，拿到材料后继续」中取，不得使用「已准备好，稍后提供」或「后续补充并继续」。缺失材料只有三个出口：当场提供、移除依赖、暂停；不存在「先假设 / 先按默认方案 / 先占位」后推进的出口，不得以任何措辞重新引入。共享协议第 3 节的「后续补充并继续」模板在裁定阶段禁止使用。
- **凡选中后条目仍处于待确认状态的选项都是非法选项**：「先占位」「后续补充」「稍后提供」「编码阶段再补」「以实际接口为准」等延后语义，按语义不按字面判定。
- **禁止自行确认**：`已确认` 只能是用户裁定的结果。「这是外部接口细节」「不影响行为契约」「specs 阶段只关心 WHAT」都不是跳过裁定的理由。
- **声称拥有 ≠ 提供**：用户仅声称「我有 / 稍后给」而未给出实体时该条未消解，追问一次索取内容，仍未提供则按「移除依赖 / 暂停」重发裁定。
- **自由表达即退出结构化**：用户不点选项、而是直接给出实质回复时，当作该条的裁定内容吸收并更新，不得机械重复弹同一个结构化选择。
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
- **列入即生成**：`Capabilities` 中每一项（正文「无」除外）都必须有对应的 `specs/<capability>/spec.md`，反过来每个 `specs/*/spec.md` 也必须能在 `Capabilities` 中找到出处。若认为某 capability 不值得单独成 spec，回到 proposal 将其移除或并入其他 capability。
- specs 定义 **WHAT**，不得写实现步骤、类名、SQL 细节或任务拆分。
- `source-context.json` 中 `targets` 含 `spec` 的 `SRC-NNN` 必须进入 spec 的 `Source References / 外部资料引用` 表并映射 REQ/SCN；`background` 等无 spec 要求的来源无需列入，保留时映射写 `-` 并填写 Usage。只能引用 PRD 已定义的 ID；PRD 无来源项时该节正文写「无」。
- `targets` 含 `spec` 的每个 `SRC-NNN-RNNN` 必须写入对应 Requirement 或 Scenario；一条要求同时约束设计时仍保留同一 ID。
- 外部接口资料至少核对 method/path、鉴权、请求/响应、错误和超时中与本期有关的内容；资料与用户已确认行为矛盾时回流澄清，不得自行选择一个版本。
- Requirement 使用 `### Requirement REQ-NNN: <标题>`，Scenario 使用四级标题 `#### Scenario SCN-NNN: <标题>` 并写在所属 Requirement 标题之下；ID 外的方括号可有可无。
- `NNN` 是三位数字；ID 在同一 feature 内全局唯一，跨 spec 文件也不得重号。改标题不改 ID，删除后 ID 不复用。新增时取一个未使用的编号即可，允许跳号，不要求与文档顺序一致——不得为了顺序重排已有 ID。
- 每个 Requirement 至少一个 Scenario；REMOVED Requirement 也必须用 Scenario 描述旧入口被触发时的期望响应。
- 使用 SHALL/MUST 表达可验证行为。
- 每个 Requirement 只能放入一个操作段：`ADDED Requirements`、`MODIFIED Requirements` 或 `REMOVED Requirements`。只是已有行为增加条件、字段、状态或分支的，放入 `MODIFIED Requirements`。
- `MODIFIED Requirements` 必须写修改后的完整行为，并覆盖旧行为受影响的触发条件和新期望；不要只写「新增字段」「调整逻辑」这类差异片段。
- `REMOVED Requirements` 必须写 `**Reason:** <移除原因>` 与 `**Migration:** <迁移方式>` 两行，并用 Scenario 描述旧入口被触发时系统应该如何响应。
- 操作段要与 proposal 的分组对上：`New Capabilities` 的 spec 在 `ADDED Requirements` 下写 Requirement，`MODIFIED`/`REMOVED` 段下不得有 Requirement；`Modified`/`Removed` 的 spec 必须在同名操作段下写 Requirement，另加 `ADDED` 是允许的。
- 模板槽位必须全部替换成实际内容：`[能力名]`、`[触发条件]`、`REQ-NNN`、`SCN-NNN` 以及 `TBD`／`待补充`／`待提供`／`待定` 都不得留在产物里；Markdown 链接不算槽位。
- 某个操作段无内容时保留段标题，段下不写 Requirement。
- 对未确认且影响行为的内容，必须回到用户确认；不要把猜测写进 specs。

## 门禁修复派发

structure 与 final 两道门禁走同一条修复通道，主流程不自己跑「校验—修复—重跑」循环。先检查当前平台是否提供 task 工具，然后二选一执行：

- **`independent_task`**：task 工具可用时，使用 task 工具指定 `specs-gate-fixer-autodev` 角色，task prompt 写明 `phase`（`structure` 或 `final`）与当前 feature。
- **`inline_main_agent`**：task 工具被平台禁用或不可用时，主 agent 读取 `${pluginPath}/agents/specs-gate-fixer.md`，按其修复循环内联执行。

拿到 `PASS` 即继续后续步骤。`BLOCKED` 时按「需主代理处理」表逐项收口，收口后重新执行：

- `ask_user`：按 `${pluginPath}/skills/references/ask-user-question.md` 逐条裁定，禁止自行填值。
- `return_specs` / `return_plan`：停止本阶段并回流。
- 需要改动行为契约（新增或改写 Requirement/Scenario、调整 capability 分类、变更范围）的失败项：由主流程改完再执行。

## 回检与修复

回检前必须先通过结构预检：

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.specs --phase structure --feature "${feature}"
```

按「门禁修复派发」执行，PASS 前不启动回检。

本节完整协议由脚本按阶段渲染，必须先运行下面命令，并完整遵循其输出；不得凭记忆执行本节。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.specs
```

回检结论必须写进 `SPECS_REVIEW.md`——只输出在回复里不算数。写之前先把「需用户裁定」条目逐条裁定完，本文件一次写成。

回检提出的问题由主模型复核、修复并收口。修改本身改变了行为契约（新增或改写 Requirement/Scenario、调整 capability 分类、变更范围）时才重跑一轮回检；其余修改直接进入产物契约预检。

## 产物契约预检（机器校验）

proposal、全部 specs、`SPECS_REVIEW.md` 生成且回检修改完成后执行：

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.specs --phase final --feature "${feature}"
```

按「门禁修复派发」执行，PASS 前不得推进 checkpoint。

不以 `update_checkpoint.py` 代替产物契约预检。

## 完成条件

- 「输入与输出」列出的三个产物都已生成，`specs/` 下至少存在一个 `spec.md`。
- 产物契约预检通过。
- specs 只描述行为契约，不包含实现任务。
- `Open Questions` 每行都经逐条裁定门消解（`Status=已确认`），或本节正文只写「无」。
- `SPECS_REVIEW.md` 的 `## Unresolved` 段为「无」。

产物契约预检与回检修复均通过后推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
