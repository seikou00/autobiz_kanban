---
name: autodev-plan
description: Dev 阶段技术设计与执行计划生成。
version: v1.2.1701
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
```

# /autodev-plan - Executable Task Plan

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## explore
进入设计探索模式。未提供的上游产物根据缺失清单处理（如基于用户直供需求建立上下文），不要硬等。隐性知识需要理解现有系统代码完成探索，并将隐性知识与用户讨论，再进入 Plan 生成。

> 进入本技能时先使用`write_todos`工具建立覆盖宏观流程的任务清单：`探索澄清（自由进行，不强制子项）` / `生成 design.md` / `生成 plan.json` / `推进 plan_done`，并随阶段推进实时更新状态（待做 / 进行中 / 完成）。用户在结束探索时若选"暂不生成"，后续条目保持待做即可。

**重要：探索模式用于澄清和调研，不用于实现。** 你可以读取已有的设计文档和相关代码，可以搜索代码库、理解现有架构、确认接口/数据模型/验证方式的边界；但不得编写业务代码、修改实现文件、创建迁移脚本，或把未经确认的 API/SQL/鉴权/租户/审计规则写成硬约束。如果用户要求直接实现，提醒用户本阶段只做探索和计划，需要进入后续 code 阶段才实现。

**这是一种工作姿态，不是固定流程。** 没有必须照搬的问题清单，也没有强制产物。你的任务是作为技术设计伙伴，把 specs 中的行为契约变成可实现、可验证的设计上下文：明确接口、数据、模块边界、风险、待确认项，以及后续 Plan 可以使用的结论。

---

### 探索姿态

- **好奇而不武断** - 顺着用户表达、proposal 和 specs 自然追问，不预设唯一答案。
- **展开线索而不审问** - 同时呈现几个值得看的方向，让用户选择最相关的，不要把对话压成机械问卷。
- **扎根现实** - 优先读取 proposal、specs、已有代码、现有接口、数据表、测试和约定；不要只做抽象讨论。
- **适度可视化** - 当结构复杂时，用 ASCII 图、列表或表格澄清模块关系、数据流、状态流、任务边界。
- **允许不确定** - 未确认的业务语义、字段、权限、异常分支要标成待确认，不要替用户补齐。
- **为设计和计划服务** - 探索的目标不是产出漂亮分析，而是为 `design.md` 与 `PLAN.md` 提供可靠依据。

---

### 你可能会做什么

根据输入和上下文，你可能会：

**探索问题空间**

- 梳理 proposal 的目标、范围、影响面，以及 specs 中的 Requirement / Scenario
- 找出 specs 中描述模糊、互相冲突、缺少边界的行为
- 将 specs 映射到接口、数据模型、权限、配置、前端交互或验证方式
- 如果发现行为契约本身不准确，停止

**调查代码库**

- 读取项目约定
- 查找相关模块、路由、接口、schema、数据库访问、测试和已有任务模板
- 找到最可能的集成点和受影响文件
- 识别现有命名、错误体、分页、鉴权、租户、审计、日志等风格
- 隐性知识你需要理解现有系统完成探索，并将隐性知识与我讨论

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

### 上下文感知

探索开始时，优先确认当前 Feature：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入、恢复模式和来源判断直接取用 `CHECKPOINT`。

- 读取上游产物原件、用户补充说明、已有 `design.md`、`PLAN.md`（如果存在）。
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
| 实现切分、涉及文件、验证方法 | `PLAN.md` 的任务 DAG、任务详情和覆盖矩阵                     |
| 未确认业务语义或技术假设     | `design.md` 与 `PLAN.md` 的风险与待确认项，并回到用户确认    |

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先进入 API Decisions 讨论，不要直接生成 PLAN。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据决策还不准确，先进入 Data Decisions 讨论，不要直接生成 PLAN。
- 讨论时只提出会影响实现路径的关键问题，并给出当前建议、备选方案和影响面；不要把用户带进机械问卷。
- 已确认的决策沉淀为 `design.md` 中的 `已确认`；仍不确定但不影响实现路径的内容可标为 `待确认` 并进入风险；`待确认` 必须先和用户讨论清楚。
- 如果仍有 `待确认` 且会影响接口形态、数据模型、权限/租户/审计、幂等、分页、异步、状态流、迁移或验收结果，不要结束探索进入 Plan 生成，直接和用户确认。

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
- **补充既有计划**："这个决策会影响 design.md 的接口/数据决策和 PLAN.md 的任务拆分，要不要更新？"
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
- PLAN.md: [建议任务边界和验证重点]

```
### 探索约束

- **不要实施** - 不写业务代码、不改实现文件、不创建迁移脚本。
- **不要假装理解** - 不清楚就查代码或追问。
- **不要偷补需求** - 未确认的 API、SQL、字段、权限、异常分支必须标注待确认。
- **不要强迫结构** - 探索可以自由，但结尾要能喂给 Plan。
- **不要自动落盘** - 先提出要写入/更新的内容，等用户确认进入 Plan。
- **要可视化** - 复杂流程用图或表格降低歧义。
- **要扎根代码库** - 尽量用现有模块、约定、测试和风格作为依据。
- **要质疑假设** - 包括用户的假设和你自己的推断。

## explore 结束
**结束决策**：当你判断 explore 已足够支撑 Plan 时，必须询问用户是否结束探索并进入 Plan 生成/更新。

- 提出上面的结束询问时，按共享 `ask-user-question.md` 协议用 `request_user_input` 发起选择，选项为 `进入 Plan 生成/更新 (Recommended)` / `继续探索` / `暂不生成、停在澄清结果`；
- **自由表达即退出结构化**：若用户不点选项、而是直接给出实质回复（补需求、改约束、抛新问题），
  当作普通文本吸收、更新探索结论后继续探索，**不得机械重复同一结构化选择**；下一轮再择机重发该门。
- 未拿到明确答复前，不得写入 `plan_in_progress`，也不得生成 design.md / plan.md。

用户确认后，才进入 `PLAN阶段`。

---

### PLAN阶段
> **specs 驱动设计**：基于 `proposal.md`、`specs/**/*.md` 和 design exploration 结论，先生成 `design.md`，再基于 specs + design 生成 `PLAN.md`。
。

#### 写入checkpoint
```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_in_progress --stage "Plan（来源: Specs）" --allow-create
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

---

#### 生成 design.md

本阶段必须生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/design.md`。`design.md` 是后续编码、测试和验收的稳定技术设计契约，承载 API、数据、架构、迁移和风险决策；行为契约以 `specs/**/*.md` 为准，不在 design.md 中重复维护完整 specs。

按 `${pluginPath}/skills/autodev/autodev-plan/templates/design.md` 的结构输出，并满足：

- **Context / 输入上下文**：引用 proposal 和 specs，说明当前代码现状和约束。
- **Spec Traceability / 规格追踪**：列出本设计覆盖的 capability、Requirement、Scenario。
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
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/design.md` 文件已写入磁盘
- [ ] design.md 包含 Context、Spec Traceability、API Decisions、Data Decisions、Technical Design、Risks / Open Questions
- [ ] API Decisions 明确写出 `x-auto-no-http-api: true/false`
- [ ] Data Decisions 明确写出 `x-auto-no-sql: true/false`
- [ ] 未确认项没有进入硬约束，已标注为待确认

#### design.md 确认规则

**「文件已写入」不等于「已向用户展示」**：用户不会自动去读 design.md。写入 design.md 之后、进入 PLAN 生成之前，必须把其中的 API Decisions 表格、Data Decisions 表格和影响实现路径的关键 Technical Decisions 摘录到对话里，让用户直接看到接口形态、字段变更、索引/迁移和回滚方式。展示格式：

```markdown
## 技术设计确认（design.md）

**API Decisions**（x-auto-no-http-api: true/false）
[摘录 API Decisions 表格；无 API 时一句话说明原因]

**Data Decisions**（x-auto-no-sql: true/false）
[摘录 Data Decisions 表格；无数据变更时一句话说明原因]

**关键技术决策**
[D-xx 中影响实现路径的决策及备选方案]

**待确认项**
[所有 Status 为「待确认」的条目，逐条说明影响]
```

展示后的确认规则：

- **待确认项逐条裁定**：Status 为「待确认」的 API/Data 决策必须作为明确问题提给用户裁定，不得埋在表格里随整体确认默认通过。
- **发起阶段门**：按共享 `ask-user-question.md` 协议用 `request_user_input` 发起选择，选项为 `确认设计，进入 PLAN 生成 (Recommended)` / `需要调整设计` / `暂停，稍后继续`；这是阶段门，不设置 `autoResolutionMs`，必须等待明确答复。
- **自由表达即退出结构化**：用户不点选项、直接给出修改意见时，当作普通文本吸收，更新 design.md 对应章节并重新展示变更部分，再择机重发确认门。

反模式（禁止）：

- 只问「以上技术设计是否满足需求？」「是否可以继续？」这类未展示具体内容的笼统问题。
- 以「内容已经写在 design.md 里」为由省略对话内展示。
- 未拿到明确确认就开始生成 PLAN.md。

---

#### 生成 PLAN

本阶段必须生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.md`。PLAN 只承载执行任务，不再重复写需求契约、行为规格或完整技术设计；行为冲突以 `specs/**/*.md` 为准，技术冲突以 `design.md` 为准。

用户补充信息沉淀规则：
- 如果用户在对话中谈论了计划实现方式、模块拆分、技术方案、接口设计思路、数据库设计思路、验证方式、风险点，或额外提供了任何技术细节，必须先同步沉淀到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/design.md` 对应章节，再把执行相关部分同步到 `PLAN.md`。
- 如果用户补充内容改变了外部可观察行为、验收标准或能力边界，停止并建议回到 `/autodev-specs` 更新 `proposal.md` / `specs/**/*.md`。
- 必须在 PLAN.md 中新增或更新「用户补充说明 / 技术细节」章节。
- 用户明确确认的内容，标记为「已确认」。
- 用户表达为建议、可能、待定、需要评估的内容，标记为「待确认」。
- 如果用户补充内容影响任务拆分、验证方法或风险，应同步更新对应任务。
- 如果用户补充内容与 specs、design.md 或既有系统约束冲突，必须在 design.md 与 PLAN.md 的「风险与待确认项」中记录，并回到用户确认，不得擅自覆盖 specs。
- 用户补充的实现细节只能作为计划依据，不得在 Plan 阶段创建或修改业务代码文件。

任务拆分粒度：

- 默认按 specs 中的 Requirement / Scenario、用户主流程或验收闭环拆成“需求任务”，不要按 Controller、DTO、Mapper、SQL、样式文件、测试文件等代码层步骤拆任务。
- 一个任务应交付一个可理解、可执行、可验证的业务闭环；它可以同时涉及接口、服务、数据、前端、测试和配置。
- 只有在满足以下条件之一时才继续拆分：可独立验证；风险或决策明显不同；存在明确依赖顺序；可被多个需求复用的基础能力；任务过大导致执行者无法在一次编码闭环中完成。
- 小需求通常 2-5 个任务，中等需求通常 4-8 个任务；如果超过 10 个任务，必须检查是否把代码步骤误拆成了任务，并优先合并。
- 不要生成“新增 DTO”“修改 Controller”“补 Mapper”“写单测”这类单纯代码操作任务。
- 测试通常作为每个需求任务的验证方法沉淀；只有跨多个需求的验收闭环、E2E 主链路或质量门禁需要单独编排时，才生成独立验证任务。
- 任务名用业务结果命名，例如“实现订单导出主链路”“支持审批超时提醒”“补齐用户配置保存与回显”，避免“修改某文件”“新增某类”。

每个任务都要能追溯到 specs 中的 Requirement / Scenario；design.md 中的每个 API Decision、Data Decision 和关键 Technical Decision 都必须被实现任务和验证方法覆盖，或明确说明无需实现。

按 `{PLUGIN_ROOT}/skills/autodev/autodev-plan/templates/plan.md` 的结构输出。

完成条件：
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PLAN.md` 文件已写入磁盘
- [ ] PLAN.md 包含「任务 DAG」「任务总览」「任务详情」「Specs 行为覆盖」「规格与设计决策覆盖」
- [ ] 每个任务都包含「做什么」「规格依据」「设计依据」「验证方法」「状态: 待做」
- [ ] 任务按需求闭环拆分，不按代码层或文件层机械拆分；过细任务已合并到对应需求任务
- [ ] specs 中每个 Requirement / Scenario 至少被一个任务覆盖
- [ ] design.md 中每个接口/数据/技术决策至少被一个实现任务和一个验证方法覆盖，或明确标注无需实现
- [ ] 在 Plan 阶段额外提供了实现细节或技术约束，design.md 与 PLAN.md 已同步记录，并更新相关任务或风险项。

---

## 整体完成条件
- `design.md`、`PLAN.md` 已完成
```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
