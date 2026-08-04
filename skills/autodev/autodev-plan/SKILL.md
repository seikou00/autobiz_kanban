---
name: autodev-plan
description: Dev 阶段技术设计与执行计划生成。
version: v1.8.0804
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
```

# /autodev-plan - Executable Task Plan

进入 Plan 时读取 `${FEATURE_DIR}/IMPLEMENTATION_SCOPE.json`。`backend_only` 只允许生成 `uiRequired=false` 的 backend task；`frontend_only` 只允许生成 `uiRequired=true` 的 frontend task；`full_stack` 保持现有行为。Plan writer 会在分组预检、Draft 和正式计划校验中重复执行该门禁。

## explore
进入设计探索模式。未提供的上游产物根据缺失清单处理。隐性知识需要理解现有系统代码完成探索，并将隐性知识与用户讨论，再进入 Plan 生成。

> 进入本技能时先使用`write_todos`工具建立覆盖宏观流程的任务清单：`探索澄清（自由进行，不强制子项）` / `生成 design.md` / `生成 plan.json` / `推进 plan_done`，并随阶段推进实时更新状态（待做 / 进行中 / 完成）。用户在结束探索时若选"暂不生成"，后续条目保持待做即可。

**重要：探索模式用于澄清和调研，不用于实现。** 你可以读取已有的设计文档和相关代码，可以搜索代码库、理解现有架构、确认接口/数据模型/验证方式的边界；但不得编写业务代码、修改实现文件、创建迁移脚本，或把未经确认的 API/SQL/鉴权/租户/审计规则写成硬约束。如果用户要求直接实现，提醒用户本阶段只做探索和计划。

**这是一种工作姿态，不是固定流程。** 没有必须照搬的问题清单，也没有强制产物。你的任务是作为技术设计伙伴，把 specs 中的行为契约变成可实现、可验证的设计上下文：明确接口、数据、模块边界、风险、待确认项，以及后续 Plan 可以使用的结论。

---

### 探索姿态
使用task工具进行探索，指定Explore-autodev角色，探索必须要读<AGENTS_INSTRUCTIONS></AGENTS_INSTRUCTIONS>里面提到的文件，再按下面列举的要求，最后需要返回完整详尽的结构化文档结果让主代理参考。
- **好奇而不武断** - 顺着用户表达、proposal 和 specs 自然追问，不预设唯一答案。
- **展开线索而不审问** - 同时呈现几个值得看的方向，让用户选择最相关的，不要把对话压成机械问卷。
- **扎根现实** - 优先读取 proposal、specs、已有代码、现有接口、数据表、测试和约定；不要只做抽象讨论，也不要假装理解——不清楚就查代码或追问。
- **质疑假设** - 包括用户的假设和你自己的推断。
- **适度可视化** - 当结构复杂时，用 ASCII 图、列表或表格澄清模块关系、数据流、状态流、任务边界。
- **允许不确定** - 未确认的业务语义、字段、权限、异常分支要标成待确认，不要替用户补齐。
- **为设计和计划服务** - 探索的目标不是产出漂亮分析，而是为 `design.md` 与 `plan.json` 提供可靠依据；可以自由展开，但结尾要能喂给 Plan。

---

### 你可能会做什么

根据输入和上下文，你可能会：

**探索问题空间**

- 梳理 proposal 的目标、范围、影响面，以及 specs 中的 Reqrement / Scenario
- 找出 specs 中描述模糊、互相冲突、缺少边界的行为
- 将 specs 映射到接口、数据模型、权限、配置、前端交互或验证方式
- 如果发现行为契约本身不准确，停止

**调查代码库**

- 读取项目约定
- 查找相关模块、路由、接口、schema、数据库访问、测试和已有任务模板
- 找到最可能的集成点和受影响文件
- 识别现有命名、错误体、分页、鉴权、租户、审计、日志等风格
- 隐性知识你需要理解现有系统完成探索，并将隐性知识与我讨论
- 任务、需求和设计决策都必须使用稳定 ID：Task `T001`、Requirement `REQ-001`、Scenario `SCN-001`、API `API-001`、Data `DATA-001`、技术决策 `D-001`。`D-NNN` 由本阶段写进 design 技术决策表，并且每个任务的 `decisionIds` 至少引用一个；design 里的每条 `D-NNN` 也都必须被某个任务引到，双向由 `plan_json_contract` 判定。
- 规格决策 `DEC-001` ：由 specs 阶段在 proposal 的 `## Decision Log` 节定义，本阶段只在 design 追踪表的 `Decision` 列引用、不新增，该 Requirement 无此类决策时写「无」。`design_contract` 只判引用能否在该节内解析，不要求每个 Requirement 都有。两者都叫「决策」，区别是 `D` 本阶段产出且引用强制，`DEC` 上游输入且可为空。

**比较选项**

- 在多个方案都合理时，对比影响面、复杂度、风险和验证成本
- 推荐更贴合现有系统的方向，但把假设和待确认项说清楚
- 对 API、数据和任务拆分只形成设计/计划依据，不在探索阶段写业务实现

**可视化**

- 结构复杂时用 ASCII 图 / 列表 / 表格澄清模块关系、状态机、数据流、接口边界、依赖图、覆盖矩阵；模板与样例见 `${pluginPath}/skills/autodev/autodev-plan/templates/EXPLORE.md`。

**揭示风险和未知点**
- 识别需求、技术、数据、权限、兼容性、测试方面的风险
- 标出必须追问用户或必须查证代码的点
- 建议 spike/调研任务，但不得在本阶段实现

---

### autodev-plan 上下文感知

探索开始时，优先确认当前 Feature状态：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

- 读取上游产物原件、用户补充说明、已有 `design.md`、`plan.json`（如果存在）。
- 读取本 Feature 相关的代码/测试/配置，用于理解现有约束。
- 如果已有 Plan 产物，只把它们作为上下文来讨论；除非用户明确要求进入 Plan 写入阶段，不要自动改写。

当探索发现不同类型的信息时，按下面方式准备给 Plan 使用：

| 探索发现                     | 后续沉淀位置                                                 |
| ---------------------------- | ------------------------------------------------------------ |
| 需求目标、范围、非目标变化   | 回到 `proposal.md`，或在 `design.md` 记录影响与风险           |
| 新增或变化的外部可观察行为   | 回到 `specs/**/*.md`，不得只写入 `design.md`                  |
| 新增或变化的 HTTP 行为       | `design.md` 的 API Decisions；无 API 写 `x-auto-no-http-api: true` |
| 数据表/字段/索引/迁移需求    | `design.md` 的 Data Decisions；无数据变更写 `x-auto-no-sql: true` |
| 技术方案、模块边界、集成点   | `design.md` 的 Technical Design                              |
| 实现切分、涉及文件、验证方法 | `plan.json` 的任务 DAG、任务详情和覆盖矩阵；`PLAN.md` 以plan.json为准 |
| 未确认业务语义或技术假设     | `design.md` 与 `plan.json` 的风险与待确认项，并回到用户确认；`PLAN.md` 以plan.json为准 |

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先进入 API Decisions 讨论，不要直接生成 PLAN。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据决策还不准确，先进入 Data Decisions 讨论，不要直接生成 PLAN。
- 讨论时只提出会影响实现路径的关键问题，并给出当前建议、备选方案和影响面；不要把用户带进机械问卷。
- 已确认的决策沉淀为 `design.md` 中的 `已确认`；仍不确定但不影响实现路径的内容可标为 `待确认` 并进入风险；会影响实现路径的 `待确认` 必须先和用户讨论清楚。
- 如果仍有 `待确认` 且会影响接口形态、数据模型、权限/租户/审计、幂等、分页、异步、状态流、迁移或验收结果，不要结束探索进入 Plan 生成。

讨论输出建议：

```markdown
## 接口与数据决策待确认

我不建议现在直接生成 PLAN，因为以下决策会影响实现路径：

| ID | 类型 | 决策点 | 当前建议 | 备选方案 | 影响 | 需要确认 |
|----|------|--------|----------|----------|------|----------|
| API-001 | API | [接口入口/请求响应/错误码] | [建议] | [备选] | [影响任务/验收] | [问题] |
| DATA-001 | Data | [表/字段/状态/约束] | [建议] | [备选] | [影响任务/验收] | [问题] |
```

约束：探索阶段可以提出“建议写入哪里”，但不要自动捕捉或落盘，除非用户明确确认进入 Plan 生成/更新。行为契约变更必须回到 `/autodev-specs`，不要在 Plan 阶段偷偷改写 specs。


### 你不必做的事情

- 照本宣科
- 每次都问同样的问题
- 产出特定产物
- 得出结论
- 死守主题（有价值的支线可以展开）
- 刻意简短（这是思考时间）

---

### 处理不同的切入点

不同切入点（模糊想法 / 具体问题 / 中途卡住 / 比较选项）的完整对话样例见 `${pluginPath}/skills/autodev/autodev-plan/templates/EXPLORE.md`。核心姿态：先展开可选边界，再落到 specs 与代码现状，把关键分歧标成待确认项喂给 Plan。

---

### 结束探索

没有固定结局。探索可能会：

- **进入 Plan 生成**："这些信息已经足够生成 Plan，要我继续吗？"
- **补充既有计划**："这个决策会影响 design.md 的接口/数据决策和 plan.json 的任务拆分，要不要更新？"
- **停在澄清结果**：用户已经得到判断，暂不生成文件。
- **稍后继续**："我们可以之后从这些待确认项继续。"

当判断探索已经足够进入 Plan 时，必须先给出简短探索结论，并询问用户是否结束探索、进入 Plan 生成/更新：

```
## 探索结论

**需求目标：** [当前理解]

**影响范围：** [模块/API/数据/权限/前端/配置]

**已确认：** [用户、proposal 或 specs 已明确的信息]

**待确认：** [必须追问或在 Plan 中标注的事项]

**生成依据：**
- specs/**/*.md: [行为契约和验收场景]
- design.md: [API 决策/数据决策/技术设计需要覆盖什么]
- plan.json: [只说明进入 Plan 后将按覆盖矩阵与候选任务分组表拆分；未输出完整矩阵和最终分组表前，不得预估“3-4 个任务”这类固定任务数]

```
## explore 结束
**结束决策**：当你判断 explore 已足够支撑 Plan 时，必须询问用户是否结束探索并进入 Plan 生成/更新。

- 提出上面的结束询问时，用 `request_user_input`发起选择，选项至少含 `进入 Plan 生成/更新 (Recommended)` / `继续探索` / `暂不生成、停在澄清结果` / `其他`；
- **自由表达即退出结构化**：若用户不点选项、而是直接给出实质回复（补需求、改约束、抛新问题），
  当作普通文本吸收、更新探索结论后继续探索，**不得机械重复同一结构化选择**；下一轮再择机重发该门。
- 未拿到明确答复前，不得写入 `plan_in_progress`，也不得生成 design.md / plan.json。

用户确认后，才进入 `PLAN阶段`。

---

### PLAN阶段
先生成 `design.md`，再基于这些输入与 design 生成 `plan.json`，并同步生成 `PLAN.md` 。

#### 更新状态
```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_in_progress --stage "Plan（来源: Specs）"
```

---

#### 生成 design.md

本阶段必须生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/design.md`。`design.md` 是稳定技术设计契约，承载 API、数据、架构、迁移和风险决策；行为契约以 `specs/**/*.md` 为准，不在 design.md 中重复维护完整 specs。

按 `${pluginPath}/skills/autodev/autodev-plan/templates/design.md` 的结构输出，并满足：

- **Context / 输入上下文**：引用 proposal 和 specs，说明当前代码现状和约束。
- **Spec Traceability / 规格追踪**：列出本设计覆盖的 capability、Requirement、Scenario。`Decision` 列填 proposal `## Decision Log` 里对应的 `DEC-NNN`——那是 specs 阶段为该 Requirement 定下的取舍及其否决项，实现遇阻要偏离时先看这里有没有权衡过；该 Requirement 无此类决策时写「无」。技术决策是 `Design Coverage` 列的 `D-NNN`，不要混进 `Decision` 列。`design_contract` 判定引用的 `DEC-NNN` 在 proposal 中真实存在。
- **API Decisions / 接口决策**：
  - 不再生成独立接口契约文件。
  - 如本轮不涉及 HTTP/API，必须写 `x-auto-no-http-api: true` 并说明原因。
  - 如涉及 HTTP/API，用结构化表格记录 Method、Path/Entry、Request、Response、Errors、Auth/Tenant/Audit、Status。
  - 不得把未确认的鉴权、租户、审计字段写成硬约束；必须标为待确认。
- **Data Decisions / 数据决策**：
  - 不再生成独立 SQL 设计文件。
  - 如本轮不涉及数据库或持久化，必须写 `x-auto-no-sql: true` 并说明原因。
  - 如涉及数据变更，记录表/模型、字段、索引、迁移、回滚和状态。
  - specs 明确涉及数据但字段/类型/索引缺失时，必须回到用户追问或标为待确认；不得凭空发明字段。
- **Technical Design / 技术设计**：记录现状、决策、备选方案、集成点和涉及路径。
- **Risks / Open Questions**：所有未确认业务语义、技术假设、兼容风险必须落在这里。

完成条件：
- [ ] design.md 包含 Context、Spec Traceability、API Decisions、Data Decisions、Technical Design、Risks / Open Questions
- [ ] API Decisions 明确写出 `x-auto-no-http-api: true/false`
- [ ] Data Decisions 明确写出 `x-auto-no-sql: true/false`
- [ ] 未确认项没有进入硬约束，已标注为待确认

---

#### design.md 确认规则

**「文件已写入」不等于「已向用户展示」**：用户不会自动去读 design.md。写入 design.md 之后、进入 PLAN 生成之前，必须把其中的 API Decisions 表格、Data Decisions 表格和影响实现路径的关键 Technical Decisions 摘录到对话里，让用户直接看到接口形态、字段变更、索引/迁移和回滚方式。展示格式：

```markdown
## 技术设计确认（design.md）

**API Decisions**（x-auto-no-http-api: true/false）
[摘录 API Decisions 表格；无 API 时一句话说明原因]

**Data Decisions**（x-auto-no-sql: true/false）
[摘录 Data Decisions 表格；无数据变更时一句话说明原因]

**关键技术决策**
[D-xxx 中影响实现路径的决策及备选方案]

**待确认项**
[所有待确认条目：API-xxx / DATA-xxx / D-xxx 中 Status 为「待确认」的行，以及 R-xxx 中 Type 为「待确认」或「读码差异」的行；逐条说明影响，读码差异条目按「spec/D-xxx 说 X，代码是 Y（EVD-xxx）」呈现。]
```

展示后按以下两步确认，顺序不可颠倒：

**待确认项逐条裁定**

- 范围：API-xxx / DATA-xxx / D-xxx 中 Status 为「待确认」的行，以及 R-xxx 中 Type 为「待确认」或「读码差异」的行；没有待裁定条目时跳过本步，直接进入第二步。读码差异条目的选项闭集同样适用：裁定结果只能是「spec 基线过时，按代码现实修订（结果回写 spec/design 对应处）」「plan 读码有误，修正 Evidence」「行为契约需要变更，回 /autodev-specs」三者之一的具体化，不存在「按代码先做」的默认出口。
- 消解定义：裁定即消解。一个条目被消解 = design.md 对应行 Status/Type 回写「已确认」，**且**裁定产生的具体内容（采纳的方案、用户提供的链接/字段）已写进对应行或章节。每个预设选项选中后必须能立即达成消解或明确暂停推进；两者之外的选项非法。
- 协议：按共享 `ask-user-question.md` 协议用 `request_user_input` 逐条提问，每轮最多 3 项（对应协议中「逐项裁定」条款）；`id` 与条目 ID 对应（如 `pending_r_001`）。这是阶段门的组成部分，不设置 `autoResolutionMs`，必须等待明确答复。
- 选项闭集：每条给 2–3 个互斥选项，语义只能从以下四类中取——①「按当前设计确认 (Recommended)」：采纳设计中已写出的方案；②「采纳备选：<方案>」：选项自身携带具体替代方案；③「需要调整」：用户将给出修改意见，吸收后更新章节、重新展示、该条重新裁定；④「暂停，拿到材料后继续」：仅信息缺口型条目可用，保留在 plan 阶段、不推进。
- 信息缺口型条目（缺接口文档 url、字段定义、外部约定等）：`question` 中直接写「若现在能提供，请在『其他』中粘贴链接或具体内容」；预设选项只从「调整设计移除该依赖」「暂停，拿到材料后继续」中取。缺失材料只有三个出口：当场提供、移除依赖、暂停；不存在「先假设 / 先按默认方案 / 先占位」后推进的出口——该出口已从选项闭集移除，不得以任何措辞重新引入。用户在「其他」提供内容 → 内容写进 design.md → 回写「已确认」。共享协议第 3 节的「后续补充并继续」模板在这个阶段禁止搬进裁定门。
- 回写：拿到裁定后立即回写 design.md 对应行——回写「已确认」的前提是信息实体落地：用户答复中给出的链接/字段/方案必须先写进对应行或章节。**声称拥有 ≠ 提供**：用户仅声称「我有 / 稍后给」而未提供实体时，该条**未消解**：追问一次索取内容，仍未提供则按「调整设计移除该依赖 / 暂停」重发裁定。不得有延后选择，后续阶段不会检查待确认；裁定改变设计内容时更新对应章节并重新展示变更部分。
- 消解自查：全部裁定回写后、发起第二步之前，自查 design.md 各表单元格无「待确认」「读码差异」、回写内容无 TBD/待补充/待提供/占位 等词、无对缺失材料的引用（「根据实际文档」「以实际接口为准」「编码阶段补充」等）；任一命中回到第一步。plan_done 的 postcheck 会机械校验残留单元格，绕过自查也无法推进。
- 顺序硬约束：所有待确认条目都拿到用户裁定之前，禁止发起第二步的整体确认门。

**整体确认门**

- **发起阶段门**：按共享 `ask-user-question.md` 协议用 `request_user_input` 发起选择，选项为 `确认设计，进入 PLAN 生成 (Recommended)` / `需要调整设计` / `暂停，稍后继续`；这是阶段门，不设置 `autoResolutionMs`，必须等待明确答复。
- **自由表达即退出结构化**：用户不点选项、直接给出修改意见时，当作普通文本吸收，更新 design.md 对应章节并重新展示变更部分，再择机重发确认门。

反模式（禁止）：

- 只问「以上技术设计是否满足需求？」「是否可以继续？」这类未展示具体内容的笼统问题。
- 以「内容已经写在 design.md 里」为由省略对话内展示。
- 未拿到明确确认就开始生成 PLAN.json。
- 把待确认项在展示块中逐条列出后，未逐条以 `request_user_input` 提问就直接发起整体确认门；展示不等于裁定。
- 自行判断某待确认项「编码阶段参考接口文档即可」「不影响主路径」而跳过提问；「延后处理」不能出现。
- 选项 label/description 含「待确认」「先占位」「后续补充」「稍后提供」「编码阶段再」「编码阶段根据实际文档补充」「实现时参考文档」「字段以实际接口为准」等延后语义——凡选中后条目仍处于待确认状态的选项都是非法选项。延后判定按语义不按字面。
- 「已确认，我有 url/文档」这类仅声称拥有信息、不当场收集内容的选项，需要继续发起一次追问。

---

#### 生成 plan.json + PLAN.md

本阶段必须一次性生成完整的 plan.json + PLAN.md，并同时生成全部 `plans/Bxxx/plan.json`。不得只生成第一批并等待 Code 跑完后再规划下一批。`plan.json` 只保存 feature 状态、任务集封口状态、批次索引、批次状态、lane 级批次验证配置和可选的跨批次项目验证，不得包含 `tasks`；每个 `plans/Bxxx/plan.json` 保存该批任务契约、task 状态和投影后的批次验证状态。`PLAN.md` 是从 `plan.json` 投影，并包含全部批次计划中的任务摘要；行为冲突以 `specs/**/*.md` 为准，技术冲突以 `design.md` 为准。

生成或修改 `plan.json` / `PLAN.md` 必须使用 `${pluginPath}/hooks/plan_writer.py`。不得直接整份写入或编辑这些 JSON；`PLAN.md` 必须由 `plan_writer.py render-md` 从 `plan.json` 生成。调试只使用 writer 的 `validate` / `show --summary`，不要把整份 JSON 打进上下文。运行 `init` 前必须先确认目标产物是否已存在；writer 默认拒绝覆盖已有非空产物，只有在明确需要重建并理解会丢弃旧内容时才传 `--force`。

生成计划时必须完整读取 `${pluginPath}/skills/autodev/autodev-plan/templates/task-groups.json` 和 `${pluginPath}/skills/autodev/autodev-plan/templates/task-detail-input.json`。先定位本期实际涉及的全部代码仓库，对每个 `--code-workspace` 执行 `git rev-parse --show-toplevel`，以 Git 根目录名作为稳定 `workspaceRef`；前后端或同一 lane 涉及多个仓库时必须全部登记，不得因当前 cwd 位于某一仓库就遗漏其他仓库。再把最终候选分组表写入 `${FEATURE_DIR}/.tmp/plan_writer/task-groups.json`；分组表是 `id/title/deps/uiRequired/workspaceRef/specRefs/mergedScenarioRefs/apiIds/uiRefs/splitRationale/validationBoundary` 的唯一事实源。每个 group 必须且只能绑定一个实际实现仓库；一个行为需要修改多个仓库时必须拆成多个 TASK 并用 deps 表达顺序，禁止单 TASK 跨仓库。每个 `validationBoundary` 必须是具体、非空的公开 seam 与可执行校验边界，不得保留模板占位文本。禁止创建 `.tmp/plan_writer/tasks/Txxx.json` 或任何独立完整 task 副本。writer 会从分组表直接创建 `${FEATURE_DIR}/.tmp/plan_writer/draft/plan.json` 与 Draft `plans/Bxxx/plan.json`，调用方只补 task detail；正式根 `plan.json` 和 `plans/Bxxx/plan.json` 在 finalize 前不存在。

候选分组必须先做可验证性判断：backend group 若只产出 Entity/PO/DO/DTO/Mapper、配置或脚手架等结构，且唯一校验是 `compile/build` 或文件存在检查，则不得独立成 TASK；在不跨 workspace/lane 且不突破粒度上限时，合并到最早消费它的下游行为 group，并重排 ID/deps。只有能在不依赖后续 TASK 的情况下，通过真实的 behavior/integration/static 契约测试验证的数据迁移、ORM、序列化或 Schema 契约，才可保留为独立 backend TASK。frontend group 可按 frontend validation profile 使用 compile/build/typecheck 验证页面工程能成功编译，但不得把该命令伪装成 behavior test。此判断必须在 `preflight-task-groups` 和创建 Draft 前完成，不得在 task detail 阶段用空 `validationCommands`、伪 `static_check` 或占位命令兜底。

每次 Plan 会话准备 Draft 前只执行一次以下只读命令，并以其 JSON 输出获取分组/详情模板路径、group-owned 字段、合法 validation kind、AC 覆盖规则和 Draft 工作流；后续复用该 contract，不重复查 `--help`，不得读取 writer 源码来发现参数或枚举值：

```bash
python "${pluginPath}/hooks/plan_writer.py" add-task-contract
```

writer 自动分组，调用方不指定 batch。`executionLane` 由 writer 根据 `uiRequired` 自动推导：`false=backend`、`true=frontend`，调用方不得自行维护该字段。`task-groups.json` 必须按 DAG 拓扑序排列全部 backend group，再排列全部 frontend group；frontend group 可以依赖更早的 backend group，backend group 不得依赖 frontend group。writer 以第一个 `specRefs` 中 `#` 前的文件路径作为主 capability；只有与紧邻前一批的主 capability 和 execution lane 都相同且该批少于 5 个任务时才合批，否则创建下一 `Bxxx`。因此即使最后一个 backend batch 未满，首个 frontend task 也必须新建 batch。不得伪造 batch ID，也不得通过调整 `specRefs` 顺序伪造分组结果。

最终候选分组表完成后，先运行只读分组预检。`task-groups.json.uiRequiredExample` / `add-task-contract.taskGroupUiRequiredExample` 是 `uiRequired:true` 的完整分组示例，`task-groups.json.matrixExceptionExample` / `add-task-contract.taskGroupMatrixExceptionExample` 是 6-12 个 SCN 共享同一验证闭环时的分组例外示例；两者都只用于指导，不是 `groups[]` 的实际成员。该命令只校验拆分所需的完整路径级 `specRefs`、SCN/API/Page/UIX/VIS/route、DAG/lane 顺序、`mergedScenarioRefs`、`splitRationale`、`validationBoundary` 和完整 Scenario 覆盖，不要求 goal、scope、AC、decisionIds 或完整 validation command：

```bash
python "${pluginPath}/hooks/plan_writer.py" preflight-task-groups --feature "${feature}" --group-file "${FEATURE_DIR}/.tmp/plan_writer/task-groups.json"
```

分组预检失败时只能修改候选分组，不得准备 Draft。`oversized_plan_task_must_split` 必须先拆分；不得先补 AC、VAL、decisionIds、scope 或 implementationPoints。禁止看到 6-12 个 SCN 就为所有 group 自动补 `mergedScenarioRefs` / `splitRationale`，也禁止按连续 SCN 编号机械切块；必须先在候选分组表证明共享验证闭环，确认这些 SCN 共享同一用户动作、公开 seam 和自动化验证边界，否则按业务闭环继续拆分。

分组预检成功后立即创建并锁定 Draft Batch；`prepare-task-draft` 会保存 `groupingDigest`，投影全部 group-owned 字段和自动 Batch，不需要也不接受 task 目录：

```bash
python "${pluginPath}/hooks/plan_writer.py" prepare-task-draft --feature "${feature}" --group-file "${FEATURE_DIR}/.tmp/plan_writer/task-groups.json" --code-workspace "<BACKEND_MODULE>" --code-workspace "<FRONTEND_MODULE>"
```

每个候选分组应显式选择 `executionMode=code|verified_existing|external_dependency`，缺省仅兼容为 `code`。`verified_existing` 表示本 Feature 内已有实现，只允许复用现存可执行验证目标；`external_dependency` 表示行为与验证均由 Feature 外的系统或仓库负责，必须同时写 `externalDependency.system/owner/trackingRefs`，不得配置本地验证命令或待创建测试。外部依赖不是本地 no-code 实现，也不得借创建占位测试把它伪装成已验证。

按 Task ID 逐个把 `task-detail-input.json` 结构通过 stdin 交给 writer。详情不得包含 group-owned 字段，`acceptanceCriteria[].id`、`validationCommands[].id`、`scope.pages` 和 `scope.workspaceRoots` 也不得由调用方提供；writer 自动编号、从 `uiRefs.pageRefs` 投影 pages、根据 group `workspaceRef` 只投影该 TASK 对应的 workspace root，并在命令未显式提供时自动补正确的 `repo` 与 `cwd`。禁止为了通过校验把缺失的前端仓库替换成后端 workspace 或 Git 根 `.`。每个 detail 的 `nonGoals` 必须至少包含一条具体、非空的相邻行为或范围排除说明，不得写空数组、`无` 或保留模板占位文本。每次详情在写入 Draft Batch 前完成结构、AC 场景归属、2-6 条 implementation points、nonGoals、cwd/manifest 和 required AC 覆盖校验，失败时当前 Draft task 保持原样：

```bash
python "${pluginPath}/hooks/plan_writer.py" set-draft-task-detail --feature "${feature}" --task-id T001 --body-stdin
```

不得直接编辑 Draft 根或 Batch JSON。需要查看进度时只运行 `show-task-draft`；它只返回 ready/pending Task ID 和 Batch 摘要。若分组表在 Draft 创建后改变，所有 Draft 命令返回 `task_group_changed_after_draft_created`；只能运行 `rebuild-task-draft --group-file <file>`，writer 仅保留 group projection 与该 TASK workspace contract 都未变化的 ready task 详情，只重置受影响 task，禁止逐字段同步旧 task。若旧 Draft 缺少 code workspace，修改单个 task detail 无法修复，必须运行 `rebuild-task-draft --group-file <file> --code-workspace <path>`；重复参数可登记多个仓库。

全部 task ready 后运行一次 Draft 全局预检，再原子发布正式 Bundle：

```bash
python "${pluginPath}/hooks/plan_writer.py" preflight-task-draft --feature "${feature}"
python "${pluginPath}/hooks/plan_writer.py" finalize-task-draft --feature "${feature}"
```

全局预检只负责跨 task Scenario 覆盖、DAG、backend/frontend 顺序、Batch 投影和设计覆盖；单 task 内容错误必须已在 `set-draft-task-detail` 当场阻断。finalize 会重跑同一校验并通过事务一次写入正式根计划、全部 Batch 和 `PLAN.md`；失败时不写任何正式产物。正式计划已存在时默认拒绝覆盖。禁止使用 `python -c` 构造 Python dict 或 JSON，也不得混用 Python 的 `True/False/None` 与 JSON 的 `true/false/null`。

除 `executionMode=external_dependency` 外，每个 task detail 必须包含非空 `validationCommands`。合法 kind、命令禁令与待创建测试的取值以 `add-task-contract` 输出为唯一事实源：`validationKindsByLane` 给出 backend/frontend 各自允许的 kind，`validationCommandPolicy` 给出禁用可执行文件、内联 shell、placeholder 与 Maven 选择器要求，`validationTestPlanPolicy.byExecutionMode` 给出各 executionMode 可用的 `reuse_existing` / `create_in_code`。要点：backend TASK 禁止配置 compile/build/typecheck/lint，lint 只能作为 Batch 补充门禁；frontend TASK 的编译型 kind 必须与真实命令动作一致；Maven `test` 必须带具体类选择器 `-Dtest=XxxTest` 或 `-Dit.test=XxxIT`。`verified_existing` 缺少可复用目标时返回 Plan 重做契约。不得通过 validator 失败来探索 schema。

所有 JSON 必须合法，不允许 Markdown、注释、尾逗号或解释性文本。任务依赖只能指向本批更早任务或更早批次任务，禁止前向依赖、backend 依赖 frontend 和跨批环。不得直接整份写入 root/batch 正式 JSON，也不得生成 `plan_v1.json`、`plan_v2.json` 等平行版本；发现根 `plan.json` 含 `tasks`、缺少 `taskSetStatus` / `executionLane` / `batchValidationProfiles`、任一批次缺少 `batchValidation`，或使用旧 batch strategy 时不迁移、不兼容。validator 会返回 `batch_validation_contract_requires_rebuild`，必须清理并重跑完整 Plan。

`templates/task-detail-input.json` 是唯一 task detail 示例，不包含 ID、标题、依赖、specRefs、apiIds、uiRefs 或 splitRationale。`status`、Evidence 字段和 completionPolicy 也由 writer 设置。`uiRequired` 与全部 UI refs 只写在分组表并由 writer 投影；task detail 的 scope 不写 pages 或 workspaceRoots。批次和项目级验证命令使用结构化 argv/cwd/kind/required；项目级验证只用于确有必要的跨 backend/frontend 或跨批次检查。不得先自由生成再依赖 validator 反复修字段。

任务需要 `splitRationale` 时必须在候选分组表首次定稿时写入；Draft task 由 writer 原样投影，不允许 detail 再维护。

旧 `preflight-task-set --task-dir` / `materialize-task-set --task-dir` 只保留兼容；已有未完成的旧 task 目录可一次性运行 `import-task-directory --group-file <file> --task-dir <directory> --code-workspace <path>` 导入 Draft，新 Plan 不得使用旧流程。

`plan.json` 语义规则：

- Task ID 使用 `T001`、`T002` ...，不跳号、不复用已删除或已完成任务 ID，且在全部批次内全局唯一。
- `taskSetDigest` 保护 writer 生成的根索引和 task 契约；直接编辑正式 JSON 会被后续读取拒绝。`finalize-task-draft` 只写完整覆盖且 `finalized` 的任务集。
- 字段清单、必填项与取值枚举以 `add-task-contract` 为准：`fields` / `conditionalFields` 给字段契约，`workspaceContract` 给 workspace 与 scope 派生规则，`batchAssignment` 给分批与 lane 推导，`taskSetFinalization` 给发布顺序。下面几条是它无法表达的语义判断和易错点。
- 只使用当前结构，不写 `version` / `taskDetailVersion` 字段。发现带版本字段或根含 tasks 的 plan 时，不迁移、不兼容，清理后重新执行 Plan。
- `goal` 写用户可观察结果，不是实现动作；`scope.modules/entrypoints/dataObjects` 写执行范围，`scope.pages` 由 writer 从分组 UI refs 投影。`validationBoundary` 必须描述公开 seam 与可执行校验边界，`nonGoals` 至少一条具体、非空的相邻行为排除，二者都不接受模板占位。
- `scope.workspaceRoots` 由 writer 根据 `prepare-task-draft --code-workspace` 派生，再按 `workspaceRef` 选择唯一仓库；`scope.paths` 只写相对该 workspace 的提示性路径，**不是实现文件白名单**——runner 会从 start 快照自动统计该 workspace 内全部有效 Git 变更，DTO、domain、test、resources、迁移或配置遗漏在 `scope.paths` 中不会导致 TASK abort，跨 workspace 的变更仍然拒绝。具名 repo 使用 `repoId:relative/path`，禁止再次包含 workspace 前缀。
- `validationCommands[].cwd` 保持 Git 根相对路径，必须等于或位于该 TASK 的 workspace root 下；省略时 writer 自动补 repo 与 workspace root。多仓库计划中每个 TASK validation command 的 `repo` 必须等于该 TASK 唯一 `workspaceRef`；project command 按实际执行仓库填写。所有 evidence 文件仍属于 feature 产物目录。
- 顶层 `batchValidationProfiles` 按 lane 选择 `mode=task_covered|commands`。backend lane 固定 `commands`：TASK 跑定向行为测试，Batch 再跑 required compile/build 收口；Maven 工程的典型组合是 TASK `mvn test -Dtest=...`、Batch `mvn compile`。frontend 仍有 TASK 未覆盖的工程风险时也用 `commands`，至少配置一条有增量价值的 required 命令。
- 顶层 `projectValidationCommands` 只承载可选的跨 lane、跨批次或全项目集成检查，按 `argv + cwd + repo` 归一化后不得与任何 batch profile 命令重复，也不能替代 TASK 的 AC 覆盖。没有这种检查时保持空数组，Code 可直接进入完成门禁。
- Plan 阶段所有任务初始状态为 `todo`，evidence 相关字段为空或 null，这些运行字段只由 task runner 更新。Plan 初始激活 `B001`；非末批完成后根状态会成为 `awaiting_next_conversation`，Code 必须停止当前对话，新对话通过 `task_runner.py code-session` 检查并自动激活下一批。
- 每个任务必须追溯到真实 specs 与 design：`specRefs` 至少覆盖一个 `REQ-xxx` 和一个 `SCN-xxx`；`designRefs`/`apiIds`/`dataIds`/`decisionIds` 只引用 `design.md` 中真实定义的决策。模板中的 API/Data/Decision ID 都是占位示例，必须替换成真实 ID。任务不涉及接口或数据变更时，不要为了过校验强行编造 `API-*` / `DATA-*`：`plan.json.apiIds` / `dataIds` 写空数组 `[]`，`PLAN.md` 的 `api_id` / `data_id` 写 `无` 或 `-`。如果 `design.md` 中存在 API/Data 决策，则这些决策必须被至少一个真正相关的任务覆盖；只有整轮都不涉及 HTTP/API 或 SQL/持久化时，才在 design.md 写 `x-auto-no-http-api: true` / `x-auto-no-sql: true`。
- `specRefs` / `designRefs` 是 feature 产物目录下的逻辑相对引用，必须写成 `specs/<capability>/spec.md#SCN-001`、`design.md#API-001` 这类形式；不要写业务代码仓库相对路径，也不要把绝对产物路径固化进 `plan.json`。Code 阶段会通过 `${pluginPath}/hooks/code_task_context.py` 按 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}` 解析这些引用。
- `validationCommands` 是 task 级强门禁，必须窄、快、可直接执行并由退出码/断言判读；不能确定真实文件时不要凭空填写 `expectedFiles`。`executionMode=external_dependency` 不运行本地 TASK 验证，由 runner 记录 blocked/deferred Evidence。

用户补充信息沉淀规则：
- 如果用户在对话中谈论了计划实现方式、模块拆分、技术方案、接口设计思路、数据库设计思路、验证方式、风险点，或额外提供了任何技术细节，必须先同步沉淀到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/design.md` 对应章节，再把执行相关部分同步到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.json`；同步更新`PLAN.md`。
- 必须在 `plan.json` 对应任务或风险字段中记录用户补充说明 / 技术细节； `PLAN.md`同步新增或更新「用户补充说明 / 技术细节」章节。
- `PLAN.md` 必须从 `plan.json` 投影，任务 id / deps / status / workspaceRef / specRefs / designRefs / validationCommands / evidenceIds 不能漂移；任务的「做什么」「代码工作区」「涉及范围」「执行要点」「验收标准」「不做什么」只能来自 `goal` / `workspaceRef` / `scope` / `implementationPoints` / `acceptanceCriteria` / `nonGoals`，不得在 `PLAN.md` 独写机器事实源没有的内容。
- 用户明确确认的内容，标记为「已确认」。
- 用户表达为建议、可能、待定、需要评估的内容，标记为「待确认」。
- 如果用户补充内容影响任务拆分、验证方法或风险，应同步更新对应任务。
- 如果用户补充内容与 specs、design.md 或既有系统约束冲突，必须在 design.md 与 plan.json 的风险/阻断字段中记录，并回到用户确认，不得擅自覆盖 specs； `PLAN.md`同步更新。
- 用户补充的实现细节只能作为计划依据，不得在 Plan 阶段创建或修改业务代码文件。

UI 任务规则：
- `uiRequired` 是 task 顶层 bool 字段，不在 `uiRefs` 内部，每个 task 必须显式写。`uiRefs` 只包含 `pageRefs`、`interactionRefs`、`visualSourceRefs`、`frontendRoute`。
- `uiRequired=true` 时，按本 Feature 内页面与交互出现顺序自行分配 `PAGE-001`、`UIX-001` 等 ID，同一页面在多个 task 中复用同一 ID；`visualSourceRefs` 写空数组，`frontendRoute` 写 `spec-driven-ui`。
- `uiRequired` 不是 `true` 的任务必须显式写 `uiRequired:false`，且不得带非空 `uiRefs`；纯后端支撑任务只保留业务/设计/验证依据。
- 仅配置后端菜单、权限或菜单数据且不修改前端页面/路由实现的任务保持 `uiRequired:false`，不得为通过分组预检虚构 PAGE/UIX。真正修改前端菜单路由或页面入口时才标记 `uiRequired:true`。
- UI task 的 `scope.pages` 必须与 `uiRefs.pageRefs` 集合一致；非 UI task 的 `scope.pages` 必须为空数组。
- `uiRefs.frontendRoute` 取值为 `none`、`spec-driven-ui`、`absolute-html`、`standard-html` 或 `missing-html`。HTML 设计稿转前端由 `frontend_before_specs` profile 的 `/autodev-frontend` 节点负责，不在 Plan 阶段分流。

### Plan Task 拆分算法（生成 plan.json 前必走）

核心：一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令。默认先按 vertical slice 拆开，再按严格条件合并；不要先按 capability、模块或文件层合成巨型任务。

1. 确认本轮实现范围
   - 先按 Source Bundle 与当前 `implementationScope`（如存在）确认本轮 specs 分母；`backend_only` 时不要把已剥离的 UI 场景、页面或交互放进 task 覆盖矩阵，`frontend_only` 时不要把已剥离的后端 API/数据实现放进当前 task。
   - 只从当前实现范围内的 `specs/**/*.md` 与 `design.md` 提取任务依据；不要从被剥离范围、PRD 余量或 Markdown 关键词反推额外任务。

2. 建立 Scenario 覆盖矩阵
   - 写 task 前，必须在对话中输出覆盖矩阵，不得只在脑内跳过。矩阵列：`SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam / 风险或依赖`。
   - 没有进入矩阵的 Scenario 不允许直接生成 task；矩阵中的每个 `SCN-xxx` 最终必须映射到某个 task 的 `specRefs`。

3. 按验证闭环生成候选任务分组表
   - 默认按 specs 中的 Requirement / Scenario、用户主流程或验收闭环拆成“需求任务”，不要按 Controller、DTO、Mapper、SQL、样式文件、测试文件等代码层步骤拆任务；禁止按文件/分层机械拆，但必须按用户可观察的 vertical slice 拆。
   - 不同用户动作、不同公开入口/API/页面/job/CLI、不同可观察结果、不同页面、不同数据模型/状态流/迁移风险、不同验证命令，默认拆成不同 task。
   - 一个任务应交付一个可理解、可执行、可验证的业务闭环；它可以同时涉及接口、服务、数据、前端、测试和配置。
   - 基础能力可以单独成 task，但必须服务于后续业务 vertical slice，并且 `validationCommands` 必须验证下游公开 seam。若只能验证工具类、DTO、Mapper 或内部函数，则并入第一个消费它的业务 task。
   - 准备 Draft Batch 前，必须先输出最终候选任务分组表，不得边补 task detail 边重新拆分。草稿阶段可用标题或 `C001` 标识候选项；进入 writer 前的最终表必须把 taskId 一次性重排为连续 `T001`、`T002`、`T003`...，禁止 `T003a`、`T004b1` 这类临时编号。
   - 最终分组表列：`候选 Task / 完整 specRefs 清单 / SCN 数 / API 数 / Page 数 / UIX 数 / implementationPoints 数 / validationCommands / deps / 拆分结论 / splitRationale 草稿`。
   - 先按 `用户动作 + 公开 seam + 自动化验证边界` 分组，再为每组分配候选 task；不得先按 capability、同一页面或同一模块合并。
   - `SCN 数` 必须从完整路径级 `specRefs` 展开后计数；不同 spec 文件里的同号 `SCN-001` 必须按不同场景分别计数。最终表不得用 `SCN-007~SCN-016`、`SCN-001SCN-003(menu)` 这类范围或拼接文本作为计数依据；每个 SCN 必须单独写为 `specs/...#SCN-xxx`。
   - `拆分结论` 只能写 `通过`、`需拆分`、`可合并(附 splitRationale)`。`需拆分` 行不允许生成 task 输入文件；`可合并(附 splitRationale)` 行必须在分组表中写出完整 `splitRationale` 草稿，生成 task JSON 时原样带入，不得临场改写。

4. 只有共享同一验证闭环时才允许合并
   - 多个 SCN/API/PAGE/UIX 合并到一个 task，必须同时满足：同一触发动作、同一公开 seam、同一验证命令或同一组响应/页面断言（frontend 可共享同一编译门禁）、拆开会复制同一验证闭环、没有超过硬上限。
   - 任务超过软阈值时默认必须继续拆分；`splitRationale` 只允许用于已经按公开入口、用户动作、可观察结果和验证命令拆到最小闭环后，仍因同一请求、同一权限/状态矩阵或同一响应断言无法独立验证的少数例外。
   - 普通 group 的 `mergedScenarioRefs` 保持空数组。SCN 超软阈值时使用 `add-task-contract.taskGroupMatrixExceptionExample` 在候选 group 填写 `specRefs`、`mergedScenarioRefs` 与 `splitRationale`；writer 将三者原样投影到 Draft task。对应 detail 必须恰有一个 required 的 `behavior_test`、`integration_test` 或 `e2e_test` 覆盖全部 AC；`splitRationale` 至少点名 3 个相关 SCN，并说明共享请求/响应、权限或状态矩阵与同一验证闭环。
   - API/PAGE/UIX 超软阈值但未超硬上限时仍可用 `splitRationale`，必须点名相关 API/PAGE/UIX ID，并说明为什么无法独立验证。
   - 标记 `可合并(附 splitRationale)` 前必须逐项确认：不同触发动作已拆开；不同公开 seam 已拆开；不同可观察结果已拆开；不同 validation command 已拆开。任一项未满足时不得标记可合并。
   - 合格示例：`SCN-001、SCN-004、SCN-007 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。`
   - 跨 spec 同号场景必须点名完整路径，合格示例：`specs/menu/spec.md#SCN-001、specs/my-approval/spec.md#SCN-001、specs/apply-report/spec.md#SCN-001 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。`
   - 状态/操作矩阵例外示例：`SCN-006、SCN-007、SCN-008、SCN-009、SCN-010、SCN-011、SCN-012 均由同一个操作权限计算入口返回操作集合，并由同一组状态-操作矩阵断言验证；拆开会复制同一验证闭环。`
   - 不合格示例：`这些都是同一个操作权限判断逻辑。`
   - 不得用“同一模块”“同一 capability”“同一页面”“同一列表”“不同组成部分”“实现方便”“一起实现”“顺手一起”等空泛理由。
   - 硬上限不可豁免：任一维度超过下节两档计数预检列出的硬上限时必须继续拆分，不能用 `splitRationale` 放行。

5. 写入前两档计数预检

   两档阈值的事实源是 `add-task-contract.matrixException`（`normalScenarioMaximum` / `scenarioMaximum`）与 `preflight-task-groups` 的判定，下面的数字与它们同源，改动以脚本常量为准：

   - `拆分结论=通过` 的候选 task 必须满足：SCN `<=5`、apiIds `<=2`、pageRefs `<=1`、interactionRefs `<=3`、`implementationPoints` 为 2-6 条、至少 1 条可独立运行的 `validationCommands`。
   - `拆分结论=可合并(附 splitRationale)` 的候选 task 必须满足：未超过硬上限——SCN 数 `>12`、apiIds `>3`、pageRefs `>2`、interactionRefs `>4` 即越界；至少一个维度超过软阈值；分组表已有完整 `splitRationale`；SCN 超软阈值时还必须有完整 `mergedScenarioRefs`。
   - 最终候选任务分组表不得包含 `拆分结论=需拆分` 的行；超过硬上限、缺少 rationale 或未完成最小闭环确认的候选 task，不得进入 Draft。
   - `task-groups.json` 的分组预检通过后由 `prepare-task-draft` 锁定 digest；内容结构错误由 `set-draft-task-detail` 当场拒绝。若确需改变分组，运行 `rebuild-task-draft`，不得手工同步 Draft Batch。
   - 一个候选组只允许一次拆分：若拆分后仍是同一公开 seam 和同一自动化验证边界，且 SCN `<=12`，使用矩阵例外；若超过 `12` 或存在多个独立用户动作、seam 或验证边界，停止并报告规格/规划冲突。不得输出 `v2`、`v3` 等重复分组表，也不得生成 `T012a`、`T012b1` 等临时 taskId。

6. 写入前预检每个 task 内容
   - `specRefs` 至少包含一个真实 `REQ-xxx` 和一个真实 `SCN-xxx`；不同 spec 文件里的 `SCN-001` 是不同场景，必须写完整 `specs/<capability>/spec.md#SCN-001` 路径，不能只写 `#SCN-001` 造成路径级覆盖缺失。
   - 任务名用业务结果命名，例如“实现订单导出主链路”“支持审批超时提醒”“补齐用户配置保存与回显”，避免“修改某文件”“新增某类”。
   - 不要生成“新增 DTO”“修改 Controller”“补 Mapper”“写单测”这类单纯代码操作任务；不要生成只有“实现某能力”“补充验证”“更新相关代码”这类泛泛描述的任务。
   - 每个任务必须包含「涉及范围」「执行要点」「验证命令」「预期结果」：
     - 「涉及范围」写模块、入口、服务、模型、配置、测试等方向；能确定真实路径时写路径，不能确定时写现有代码中要定位的范围，不要凭空发明文件。
     - 「执行要点」写入 `implementationPoints`，每条是一个可执行动作或关键约束，覆盖实现切入点、关键改动、复用现有能力、边界/失败路径和测试补充；条数上限见上一节的两档计数预检，超限时合并同一实现动作或拆 Task，不得机械删除覆盖点。
   - 「验证命令」必须是执行者（大模型）能直接在命令行运行、并自行判读结果的命令，窄、快、可单独运行。backend 用精确自动化测试或接口级 `curl`/HTTP 断言脚本，frontend 用真实的 compile/build/typecheck 作为最低门禁；具体 kind 与命令禁令见「生成 plan.json」中指向的 `add-task-contract` 契约。若 backend 当前确实缺少自动验证手段，则在本任务里声明具体测试类并由 Code 补最小测试/脚本；若 frontend 采用 package script，脚本必须真实存在且不能是 no-op。
     - 「预期结果」写可观察结果，不要只写“通过”。
   - 执行要点要写到可直接开工的可执行程度：钉住真实文件/符号/入口、真实命令与预期结果；但不要拆成 2-5 分钟步骤、完整代码块、逐文件微任务或频繁 commit，PLAN 仍保持需求闭环任务粒度。
   - 测试通常作为每个需求任务的验证方法沉淀；只有跨多个需求的验收闭环、E2E 主链路或质量门禁需要单独编排时，才生成独立验证任务。

7. 生成 DAG 与覆盖检查
   - Batch 按 `B001 -> B002` 的计划顺序串行执行；同一 Batch 的 TASK 由单一队列逐个收口和校验。依赖仍只表达真实业务前置关系，不要为了 Batch 串行额外伪造跨 Batch 依赖；Batch 内没有真实依赖的 TASK 也不启动并行 run。
   - 任务数不是首要目标：8-15 个清晰 vertical slice 优于 5 个巨型 capability task。超过 15 个任务时才检查是否把代码步骤误拆成任务；禁止为了压低任务数合并独立场景。
   - specs 中每个 `SCN-xxx` 必须至少被一个 task 的 `specRefs` 覆盖；design.md 中的每个 API Decision、Data Decision 和关键 Technical Decision 都必须被实现任务和验证方法覆盖，或明确说明无需实现。

与 writer 的衔接：

- 最终候选任务分组表必须覆盖全部 Scenario，并按 `backend`、`frontend` 两个区段排序。writer 一次创建全部 Draft Batch；不得把剩余 task 延迟到 Code 阶段。Batch 只能包含同一 lane 且同一 `workspaceRef` 的 TASK：前后端绝不共用 Batch，同为 backend/frontend 但仓库不同也必须拆成不同 Batch。
- 必须按 DAG 拓扑序编号：当前 task 的 `deps` 只能指向更早的 task。若分组预检报告依赖错误，只修候选表，不补 task detail。
- `preflight-task-groups` 成功后只运行一次 `prepare-task-draft`，并且必须带真实的 `--code-workspace`。缺少 workspace 时必须先确定业务代码目录，不得创建无 workspace 的 Draft；不得创建独立 `Txxx.json`，不得在每写 5 个 task 后提前 finalize。
- 不得通过完整 task 的内容校验失败来探索如何拆分；拆分必须在覆盖矩阵、候选任务分组表和 `preflight-task-groups` 阶段完成。
- 如果预检返回 `oversized_plan_task_must_split`，回分组表把该候选标为 `需拆分` 并重新切分；如果返回 `missing_plan_task_split_rationale` 或 `invalid_plan_task_split_rationale`，回分组表核对完整路径 SCN、验证闭环和 rationale，不反复试错正式产物。
- 如果预检返回 `missing_plan_scenario_coverage`，必须回 Scenario 覆盖矩阵定位遗漏并重新分组。不得把缺失 Scenario 添加到标题相近、已有 API 或同一页面的 task，除非重新证明它们共享同一公开 seam 和验证闭环。
- 每个 `set-draft-task-detail` 成功后该 task 才进入 ready；失败不落盘。`show-task-draft` 只看摘要，不读取或编辑 Draft JSON。
- 分组 digest 变化时运行 `rebuild-task-draft`；writer 保留分组投影未变化的 ready task，重置其余 task。不得修改 group 后继续向旧 Draft 写详情。
- 全部 task ready 后运行一次 `preflight-task-draft` 和一次 `finalize-task-draft`；未完整通过时正式根计划和批次均不存在。
- 对 finalized 计划不原地解封、不直接编辑 JSON；需要重做时清理正式产物并显式重建 Draft。
- `validate --structure` 会复核已生成 bundle 的结构、完整性摘要和 Task 粒度，但不替代完整 Scenario 覆盖预检或 `dev.plan` 阶段门禁。

finalize 成功后，必须为每个实际使用的 lane 选择一种批次收口模式。`task_covered` 只用于 frontend：当 frontend 的每个 TASK 都有 required 且动作匹配的 compile/build/typecheck 命令，并且没有额外未覆盖的工程风险时使用：

```bash
python "${pluginPath}/hooks/plan_writer.py" set-batch-validation-mode --feature "${feature}" --lane frontend --mode task_covered
```

否则只添加能够覆盖 TASK 盲区的 required 批次命令；添加命令会把该 lane 切换为 `commands`：

```bash
python "${pluginPath}/hooks/plan_writer.py" add-batch-validation-command --feature "${feature}" --lane backend --command "<BACKEND_COMPILE_OR_BUILD>" --kind compile --code-workspace "<BACKEND_MODULE>"
python "${pluginPath}/hooks/plan_writer.py" add-batch-validation-command --feature "${feature}" --lane frontend --command "<FRONTEND_BUILD_OR_TYPECHECK>" --kind build --code-workspace "<FRONTEND_MODULE>"
```

backend 请求 `task_covered` 会直接失败，且不能用 TASK 的 `mvn test` 省略 Batch 编译。同一 lane 只使用一个 workspace 时 writer 可自动选择；使用多个仓库时必须为每个 workspace 分别添加 required 命令并传 `--repo <workspaceRef>`，writer 只把该命令投影到相同 repo 的 Batch。未显式传 `--cwd` 时 writer 使用该 TASK/Batch 声明的唯一 workspace 根；显式 `--cwd` 仍是 Git 根相对路径且必须位于 workspace 内。确有跨 lane/跨批次集成检查时，再用 `add-project-validation-command --kind integration_test|e2e_test|static_check` 添加可选最终检查。

Plan 阶段不再生成独立 smoke 计划。每个 Batch 的测试闭环必须直接落在 TASK `validationCommands`、`task_covered` 或 `batchValidation.commands` 中并由 runner 实际执行；只有测试文件但没有可执行命令和 AC 覆盖关系，不视为有效验证。

完成任务、Batch 和可选项目验证配置后，运行 `python "${pluginPath}/hooks/plan_writer.py" render-md --feature "${feature}"` 投影输出 `PLAN.md`。阶段门禁见文末「整体完成条件」；`plan_writer.py validate --gate` 只是不完整的本产物快检，不能替代它。

完成条件：
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.json` 文件已写入磁盘
- [ ] `plans/B001/plan.json` 起的批次计划已写入磁盘，每批最多 5 个任务，根 plan 不含 tasks
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PLAN.md` 文件已写入磁盘，且从 `plan.json` 投影生成
- [ ] 根 `plan.json` 与各批次计划共同作为任务 DAG 机器事实源，状态投影一致
- [ ] 每个任务已通过 `set-draft-task-detail`，详情符合 `templates/task-detail-input.json`，并能清楚读出业务目标、规格/设计依据、涉及范围、执行要点、强验证命令和预期结果
- [ ] 任务按用户可观察 vertical slice 拆分，不按代码层或文件层机械拆分；超过 15 个 task 时已检查是否误拆到代码步骤，没有为了压低任务数合并独立场景
- [ ] 任务没有停留在泛泛描述；每个任务的执行要点至少有一条钉住真实锚点（文件#符号 / 真实入口 / design.md#API/DATA/D-xxx）
- [ ] 每个任务的「验证命令」都能直接运行并自行判读，没有任何需要人参与的步骤
- [ ] specs 中每个 Requirement / Scenario 至少被一个任务覆盖
- [ ] design.md 中每个接口/数据/技术决策至少被一个实现任务和一个验证方法覆盖，或明确标注无需实现
- [ ] 在 Plan 阶段额外提供了实现细节或技术约束，design.md 与 plan.json 已同步记录，并更新相关任务或风险项。

#### 回检与修复

本节完整协议由脚本按阶段渲染,必须先运行下面命令，并完整遵循其输出；不得凭记忆执行本节，也不得跳过该命令。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.plan
```

---

## 完成

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
