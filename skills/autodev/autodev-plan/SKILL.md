---
name: autodev-plan
description: Dev 阶段技术设计与执行计划生成。
version: v1.2.1701
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
```

读取本技能或任何上游产物时，如果工具返回 `content truncated`、分页提示或只显示部分行，必须继续按 offset/limit 读取直到 EOF；未完整读取前不得声称“已读取完整说明/完整产物”。

# /autodev-plan - Executable Task Plan

## explore
进入设计探索模式，严禁使用task工具。读取清单列出的上游产物原件，按各自 `读取方式` 抽取重点，把行为契约、现状、技术约束和未知点想清楚；清单里标『未生成』的可选上游产物按其 `缺失处理`（降级）处理（如基于用户直供需求建立上下文），不要硬等。隐性知识需要理解现有系统代码完成探索，并将隐性知识与用户讨论，再进入 Plan 生成。

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
- **为设计和计划服务** - 探索的目标不是产出漂亮分析，而是为 `design.md` 与 `plan.json` 提供可靠依据。

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
- 任务、需求和设计决策都必须使用稳定 ID：Task `T001`、Requirement `REQ-001`、Scenario `SCN-001`、API `API-001`、Data `DATA-001`、Decision `D-001`

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

探索开始时，优先确认当前 Feature：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入、恢复模式和来源判断直接取用 `CHECKPOINT`。

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
| 实现切分、涉及文件、验证方法 | `plan.json` 的任务 DAG、任务详情和覆盖矩阵；`PLAN.md` 同步为人类视图 |
| 未确认业务语义或技术假设     | `design.md` 与 `plan.json` 的风险与待确认项，并回到用户确认；`PLAN.md` 同步为人类视图 |

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

- 提出上面的结束询问时，用 `request_user_input`发起选择，选项至少含 `进入 Plan 生成/更新 (Recommended)` / `继续探索` / `暂不生成、停在澄清结果` / `其他`；
- **自由表达即退出结构化**：若用户不点选项、而是直接给出实质回复（补需求、改约束、抛新问题），
  当作普通文本吸收、更新探索结论后继续探索，**不得机械重复同一结构化选择**；下一轮再择机重发该门。
- 未拿到明确答复前，不得写入 `plan_in_progress`，也不得生成 design.md / plan.json。

用户确认后，才进入 `PLAN阶段`。

---

### PLAN阶段
先生成 `design.md`，再基于这些输入与 design 生成 `plan.json`，并同步生成 `PLAN.md` 。

#### 工作目录
若 `CHECKPOINT` 为空、未知，重新通过脚本获取当前checkpoint；后必须刷新 `CHECKPOINT`。

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

---

#### 生成 plan.json + PLAN.md

本阶段必须生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.json` 与 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PLAN.md`。`PLAN.md` 是从 `plan.json` 投影的人类视图；`plan.json` 才是任务 DAG、状态与 evidenceIds 的机器事实源；行为冲突以 `specs/**/*.md` 为准，技术冲突以 `design.md` 为准。

生成或修改 `plan.json` / `PLAN.md` / `SMOKE_TEST_PLAN.json` 必须使用 writer：`${pluginPath}/hooks/plan_writer.py`、`${pluginPath}/hooks/smoke_plan_writer.py`。不得直接整份写入或编辑这些 JSON；`PLAN.md` 必须由 `plan_writer.py render-md` 从 `plan.json` 投影生成。调试只使用 writer 的 `validate` / `show --summary`，不要把整份 JSON 打进上下文。运行 `init` 前必须先确认目标产物是否已存在；writer 默认拒绝覆盖已有非空产物，只有在明确需要重建并理解会丢弃旧内容时才传 `--force`。

生成 `plan.json` 时必须先完整读取 `${pluginPath}/skills/autodev/autodev-plan/templates/plan.json`，再通过 `plan_writer.py init/add-task/set-*` 增量写入，不得先自由生成再依赖 validator 反复修字段。复杂任务优先用 `plan_writer.py add-task --body-stdin` 通过 pipe/stdin 传入单个完整 task JSON object，不要裸运行后等待交互式输入，避免把大量字段塞进一条超长命令，也避免为每个 task 落盘临时 JSON 文件；简单任务才使用逐项 CLI 参数。简单任务只因软阈值需要合并说明时，直接在首次 `add-task` 使用 `--split-rationale "..."`，不要为了补一个 `splitRationale` 改成全量 JSON 重写。长文本、跨 spec 路径较多或 Windows shell 引号/编码不稳时，仍应使用 body 模式并在 JSON 内写 `splitRationale`；body 模式不得混用 `--split-rationale`，CLI 标志不会合并进 body JSON。只有运行环境无法传 stdin 时，才允许用 `--body-file` 作为降级方式，且 task 片段只能写入 feature 目录下的 `.tmp/plan_writer/tasks/`，成功写入 `plan.json` 后必须清理，不得放在 feature 根目录或作为正式产物保留。模板同时包含非 UI task 与 UI task 示例：`UI_CONTEXT.uiRequired=false` 时删除 UI 示例任务，只保留 `uiRequired:false` 的普通任务；`UI_CONTEXT.uiRequired=true` 时按 UI 示例生成至少一个 `uiRequired:true` 的 UI task。`plan.json` 的基础字段结构以模板和 validator 为唯一事实源；UI 条件字段见本文「UI 任务投影规则」并由 validator 校验。本文只说明语义与边界，不重复维护完整 schema。`plan.json` 只能是合法 JSON，不允许 Markdown、注释、尾逗号或解释性文本。

`add-task` 会在写入前执行单任务结构与粒度校验；粒度错误处理见下方「与 writer 的衔接」。

`plan.json` 语义规则：

- Task ID 使用 `T001`、`T002` ...，不跳号、不复用已删除或已完成任务 ID。
- 顶层必须保留 `taskDetailVersion: 1`。每个 task 必须写 `goal`、`scope`、`implementationPoints`、`acceptanceCriteria`、`nonGoals`；`splitRationale` 仅在超过任务粒度阈值时填写，不要为普通任务写空泛合并理由。
- `goal` 写本任务交付的用户可观察结果；`scope.modules/entrypoints/pages/dataObjects` 写执行范围；`implementationPoints` 写 2-6 条可执行要点；`acceptanceCriteria` 写本任务可观察验收口径；`nonGoals` 写本任务明确不做的范围。`uiRequired=true` 或 `apiIds` 非空时，`nonGoals` 至少 1 条；纯后端小任务可写空数组。
- Plan 阶段所有任务初始状态为 `todo`，初始 `evidenceIds` 为空；无阻断时 `blockers` 为空，有影响执行的待确认事项才写 blocker。
- 每个任务必须追溯到真实 specs 与 design：`specRefs` 至少覆盖一个 `REQ-xxx` 和一个 `SCN-xxx`；`designRefs`/`apiIds`/`dataIds`/`decisionIds` 只引用 `design.md` 中真实定义的决策。模板中的 API/Data/Decision ID 都是占位示例，必须替换成真实 ID。任务不涉及接口或数据变更时，不要为了过校验强行编造 `API-*` / `DATA-*`：`plan.json.apiIds` / `dataIds` 写空数组 `[]`，`PLAN.md` 的 `api_id` / `data_id` 写 `无` 或 `-`。如果 `design.md` 中存在 API/Data 决策，则这些决策必须被至少一个真正相关的任务覆盖；只有整轮都不涉及 HTTP/API 或 SQL/持久化时，才在 design.md 写 `x-auto-no-http-api: true` / `x-auto-no-sql: true`。
- `specRefs` / `designRefs` 是 feature 产物目录下的逻辑相对引用，必须写成 `specs/<capability>/spec.md#SCN-001`、`design.md#API-001` 这类形式；不要写业务代码仓库相对路径，也不要把绝对产物路径固化进 `plan.json`。Code 阶段会通过 `${pluginPath}/hooks/code_task_context.py` 按 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}` 解析这些引用。
- `validationCommands` 是强门禁验证命令，必须可直接运行并由命令退出码/断言自行判读；不能确定真实文件时不要凭空填写 `expectedFiles`。

用户补充信息沉淀规则：
- 如果用户在对话中谈论了计划实现方式、模块拆分、技术方案、接口设计思路、数据库设计思路、验证方式、风险点，或额外提供了任何技术细节，必须先同步沉淀到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/design.md` 对应章节，再把执行相关部分同步到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.json`；同步更新`PLAN.md`。
- 必须在 `plan.json` 对应任务或风险字段中记录用户补充说明 / 技术细节； `PLAN.md`同步新增或更新「用户补充说明 / 技术细节」章节。
- `PLAN.md` 必须从 `plan.json` 投影，任务 id / deps / status / specRefs / designRefs / validationCommands / evidenceIds 不能漂移；任务的「做什么」「涉及范围」「执行要点」「验收标准」「不做什么」只能来自 `goal` / `scope` / `implementationPoints` / `acceptanceCriteria` / `nonGoals`，不得在 `PLAN.md` 独写机器事实源没有的内容。
- 用户明确确认的内容，标记为「已确认」。
- 用户表达为建议、可能、待定、需要评估的内容，标记为「待确认」。
- 如果用户补充内容影响任务拆分、验证方法或风险，应同步更新对应任务。
- 如果用户补充内容与 specs、design.md 或既有系统约束冲突，必须在 design.md 与 plan.json 的风险/阻断字段中记录，并回到用户确认，不得擅自覆盖 specs； `PLAN.md`同步更新。
- 用户补充的实现细节只能作为计划依据，不得在 Plan 阶段创建或修改业务代码文件。

UI 任务投影规则：
- 必须读取 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UI_CONTEXT.json`；UI 范围只从该 JSON 投影，不从 PRD/specs/PLAN Markdown 关键词推导。
- UI 任务需要按模板中的 UI task 示例投影 UI 条件字段；`uiRequired` 是 task 顶层字段，不在 `uiRefs` 内部。`uiRefs` 只包含 `pageRefs`、`interactionRefs`、`visualSourceRefs`、`frontendRoute`。模板里的 `PAGE-001` / `UIX-001` 只是占位示例，必须替换为 `UI_CONTEXT.json` 中真实存在的 ID，禁止原样复制占位 ID。缺失或与 `UI_CONTEXT.json` 不一致会被拒绝。
- `UI_CONTEXT.uiRequired=true` 但缺少带 `REQ/SCN specRefs` 的 UI capability 时，不生成 UI 任务，回到 `/autodev-specs` 补齐 UI 场景分母。
- `UI_CONTEXT.uiRequired=true` 时，只为 UI capability 生成 `uiRequired=true` 的任务，并补齐 `uiRefs.pageRefs`、`uiRefs.interactionRefs`、`uiRefs.visualSourceRefs` 和 `uiRefs.frontendRoute`。
- UI feature 下，`uiRequired` 不是 `true` 的任务必须显式写 `uiRequired:false`，且不得带非空 `uiRefs`；纯后端支撑任务只保留业务/设计/验证依据。
- UI task 的 `scope.pages` 必须与 `uiRefs.pageRefs` 集合一致；非 UI task 的 `scope.pages` 必须为空数组。
- `UI_CONTEXT.uiRequired=false` 时，不生成 UI task；纯后端任务不得夹带前端实现。
- `uiRefs.frontendRoute` 取值为 `none`、`spec-driven-ui`、`absolute-html`、`standard-html` 或 `missing-html`。有 UI 但无 HTML/设计稿时使用 `spec-driven-ui`，不要伪造 HTML 输入。
- `uiRefs.frontendRoute` 必须从 `UI_CONTEXT.visualSources` 投影，不得只凭任务标题、Markdown 描述、PRD/specs 关键词或“普通前端页面”猜成 `standard-html`。
- 若任务引用的 `visualSources[].route` 为 `absolute-html`、`standard-html`、`missing-html` 或 `spec-driven-ui`，必须原样写入 `uiRefs.frontendRoute`；其中 `absolute-html` 不得降级为 `standard-html`。
- 若任务引用的 visual source 未显式写 `route`，但 `type=high_fidelity_html` 且存在 HTML 输入，必须写 `absolute-html`；若 `type=standard_html` 且存在 HTML 输入，写 `standard-html`。
- 仅当 `UI_CONTEXT.uiRequired=true` 且没有可用 HTML/设计稿 visual source 时，才使用 `spec-driven-ui`；若 visual source 标记需要 HTML 但文件不可读，写 `missing-html`。

### Plan Task 拆分算法（生成 plan.json 前必走）

核心：一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令。默认先按 vertical slice 拆开，再按严格条件合并；不要先按 capability、模块或文件层合成巨型任务。

1. 确认本轮实现范围
   - 先按 Source Bundle 与当前 `implementationScope`（如存在）确认本轮 specs 分母；`backend_only` 时不要把已剥离的 UI 场景、页面或交互放进 task 覆盖矩阵，`frontend_only` 时不要把已剥离的后端 API/数据实现放进当前 task。
   - 只从当前实现范围内的 `specs/**/*.md`、`design.md`、`UI_CONTEXT.json` 提取任务依据；不要从被剥离范围、PRD 余量或 Markdown 关键词反推额外任务。

2. 建立 Scenario 覆盖矩阵
   - 写 task 前，必须在对话中输出覆盖矩阵，不得只在脑内跳过。矩阵列：`SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam / 风险或依赖`。
   - 没有进入矩阵的 Scenario 不允许直接生成 task；矩阵中的每个 `SCN-xxx` 最终必须映射到某个 task 的 `specRefs`。

3. 按验证闭环生成候选任务分组表
   - 默认按 specs 中的 Requirement / Scenario、用户主流程或验收闭环拆成“需求任务”，不要按 Controller、DTO、Mapper、SQL、样式文件、测试文件等代码层步骤拆任务；禁止按文件/分层机械拆，但必须按用户可观察的 vertical slice 拆。
   - 不同用户动作、不同公开入口/API/页面/job/CLI、不同可观察结果、不同页面、不同数据模型/状态流/迁移风险、不同验证命令，默认拆成不同 task。
   - 一个任务应交付一个可理解、可执行、可验证的业务闭环；它可以同时涉及接口、服务、数据、前端、测试和配置。
   - 基础能力可以单独成 task，但必须服务于后续业务 vertical slice，并且 `validationCommands` 必须验证下游公开 seam。若只能验证工具类、DTO、Mapper 或内部函数，则并入第一个消费它的业务 task。
   - 调用 `add-task` 前，必须先输出最终候选任务分组表，不得边想边 `add-task`。草稿阶段可用标题或 `C001` 标识候选项；进入 writer 前的最终表必须把 taskId 一次性重排为连续 `T001`、`T002`、`T003`...，禁止 `T003a`、`T004b1` 这类临时编号。
   - 最终分组表列：`候选 Task / 完整 specRefs 清单 / SCN 数 / API 数 / Page 数 / UIX 数 / implementationPoints 数 / validationCommands / deps / 拆分结论 / splitRationale 草稿`。
   - `SCN 数` 必须从完整路径级 `specRefs` 展开后计数；不同 spec 文件里的同号 `SCN-001` 必须按不同场景分别计数。最终表不得用 `SCN-007~SCN-016`、`SCN-001SCN-003(menu)` 这类范围或拼接文本作为计数依据。
   - `拆分结论` 只能写 `通过`、`需拆分`、`可合并(附 splitRationale)`。`需拆分` 行不允许进入 `add-task`；`可合并(附 splitRationale)` 行必须在分组表中写出完整 `splitRationale` 草稿，后续首次 `add-task` 时原样带入 task JSON，不得临场改写。

4. 只有共享同一验证闭环时才允许合并
   - 多个 SCN/API/PAGE/UIX 合并到一个 task，必须同时满足：同一触发动作、同一公开 seam、同一验证命令或同一组响应/页面断言、拆开会复制同一验证闭环、没有超过硬上限。
   - 任务超过软阈值时默认必须继续拆分；`splitRationale` 只允许用于已经按公开入口、用户动作、可观察结果和验证命令拆到最小闭环后，仍因同一请求、同一权限/状态矩阵或同一响应断言无法独立验证的少数例外。
   - 上述例外同样适用于 API/PAGE/UIX 超软阈值但未超硬上限的情况。单个 task 覆盖 SCN 数 `>5`、`apiIds` 数 `>2`、`uiRefs.pageRefs` 数 `>1`、`uiRefs.interactionRefs` 数 `>3` 时，必须在 `splitRationale` 点名相关 SCN/API/PAGE/UIX ID，并说明为什么这些场景/API/页面/交互无法独立验证、只能共享同一验证闭环。
   - 标记 `可合并(附 splitRationale)` 前必须逐项确认：不同触发动作已拆开；不同公开 seam 已拆开；不同可观察结果已拆开；不同 validation command 已拆开。任一项未满足时不得标记可合并。
   - 合格示例：`SCN-001、SCN-004、SCN-007 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。`
   - 跨 spec 同号场景必须点名完整路径，合格示例：`specs/menu/spec.md#SCN-001、specs/my-approval/spec.md#SCN-001、specs/apply-report/spec.md#SCN-001 均由同一次提交动作触发、同一个响应断言验证，拆开会复制同一验证闭环。`
   - 状态/操作矩阵例外示例：`SCN-006、SCN-007、SCN-008、SCN-009、SCN-010、SCN-011、SCN-012 均由同一个操作权限计算入口返回操作集合，并由同一组状态-操作矩阵断言验证；拆开会复制同一验证闭环。`
   - 不合格示例：`这些都是同一个操作权限判断逻辑。`
   - 不得用“同一模块”“同一 capability”“同一页面”“同一列表”“不同组成部分”“实现方便”“一起实现”“顺手一起”等空泛理由。
   - 硬上限不可豁免：SCN 数 `>8`、apiIds 数 `>3`、uiRefs.pageRefs 数 `>2`、uiRefs.interactionRefs 数 `>4` 时必须继续拆分，不能用 `splitRationale` 放行。

5. 写入前两档计数预检
   - `拆分结论=通过` 的候选 task 必须满足：SCN `<=5`、apiIds `<=2`、pageRefs `<=1`、interactionRefs `<=3`、`implementationPoints` 为 2-6 条、至少 1 条可独立运行的 `validationCommands`。
   - `拆分结论=可合并(附 splitRationale)` 的候选 task 必须满足：未超过硬上限（SCN `<=8`、apiIds `<=3`、pageRefs `<=2`、interactionRefs `<=4`）；至少一个维度超过软阈值；分组表已有完整 `splitRationale` 草稿；首次 `add-task` 的 task JSON 原样带上 `splitRationale`。
   - 最终候选任务分组表不得包含 `拆分结论=需拆分` 的行；`拆分结论=需拆分`、超过任何硬上限、缺少 `splitRationale` 草稿或未完成最小闭环确认的候选 task，不得调用 `add-task`。
   - 分组表预检通过仅表示粒度计数合规；`add-task` 仍可能因结构校验失败（占位 ID、缺字段、UI_CONTEXT 不一致等）被拒绝。结构失败时修字段，不要靠加减 SCN 碰运气。

6. 写入前预检每个 task 内容
   - `specRefs` 至少包含一个真实 `REQ-xxx` 和一个真实 `SCN-xxx`；不同 spec 文件里的 `SCN-001` 是不同场景，必须写完整 `specs/<capability>/spec.md#SCN-001` 路径，不能只写 `#SCN-001` 造成路径级覆盖缺失。
   - 任务名用业务结果命名，例如“实现订单导出主链路”“支持审批超时提醒”“补齐用户配置保存与回显”，避免“修改某文件”“新增某类”。
   - 不要生成“新增 DTO”“修改 Controller”“补 Mapper”“写单测”这类单纯代码操作任务；不要生成只有“实现某能力”“补充验证”“更新相关代码”这类泛泛描述的任务。
   - 每个任务必须包含「涉及范围」「执行要点」「验证命令」「预期结果」：
     - 「涉及范围」写模块、入口、服务、模型、配置、测试等方向；能确定真实路径时写路径，不能确定时写现有代码中要定位的范围，不要凭空发明文件。
     - 「执行要点」写入 `implementationPoints`，必须 2-6 条，每条是一个可执行动作或关键约束，覆盖实现切入点、关键改动、复用现有能力、边界/失败路径和测试补充。
     - 「验证命令」必须是执行者（大模型）能直接在命令行运行、并自行判读结果的命令：自动化测试（如 `mvn test -Dtest=XxxTest`）、构建、lint，或接口级的 `curl`/HTTP 脚本断言。每个 task 的 `validationCommands` 必须窄、快、可单独运行，禁止每个 task 都绑定全量 `mvn test` / `npm test`。**禁止任何需要人参与的验证**——不写"手工""人工验证""用 Postman 调一下""在浏览器里点一下"这类步骤。HTTP 接口用 `curl ... | 断言`（或等价脚本/集成测试）覆盖，而不是描述人去点 Postman。若当前确实缺少可自动执行的验证手段，则在本任务里补一个最小可运行的测试/脚本，并把该测试/脚本的运行命令写进「验证命令」。
     - 「预期结果」写可观察结果，不要只写“通过”。
   - 执行要点要写到可直接开工的可执行程度：钉住真实文件/符号/入口、真实命令与预期结果；但不要拆成 2-5 分钟步骤、完整代码块、逐文件微任务或频繁 commit，PLAN 仍保持需求闭环任务粒度。
   - 测试通常作为每个需求任务的验证方法沉淀；只有跨多个需求的验收闭环、E2E 主链路或质量门禁需要单独编排时，才生成独立验证任务。

7. 生成 DAG 与覆盖检查
   - 依赖只表达真实执行顺序：基础能力 task 可以被业务闭环 task 依赖，页面 task 可以依赖 API/data task；不要为了排列顺序把所有 task 串成链，能并行的 task 保持无依赖。
   - 任务数不是首要目标：8-15 个清晰 vertical slice 优于 5 个巨型 capability task。超过 15 个任务时才检查是否把代码步骤误拆成任务；禁止为了压低任务数合并独立场景。
   - specs 中每个 `SCN-xxx` 必须至少被一个 task 的 `specRefs` 覆盖；design.md 中的每个 API Decision、Data Decision 和关键 Technical Decision 都必须被实现任务和验证方法覆盖，或明确说明无需实现。
   - 任务拆分变化后必须重新检查 `SMOKE_TEST_PLAN.json`：冒烟案例的 `taskId` 不得继续绑定旧的巨型任务；优先一 task 一 SMK 或一关键 SCN 一 SMK。

与 writer 的衔接：

- 调用 `add-task` 前必须完成候选任务分组表计数预检；不得通过 writer 失败来探索如何拆分。每确定一个预检通过的 task，才调用 `plan_writer.py add-task`，不要批量写完后再等 `stage_gate` 兜底。
- 禁止用脚本一次性循环写入多个 task 后再看失败列表；脚本只允许用于构造单个 task JSON 或解决 stdin/编码问题。每个 task 必须单独完成最终表预检、单独 `add-task`、单独处理返回结果。
- 必须按 DAG 拓扑序写入：当前 task 的 `deps` 全部成功落盘后才允许写入当前 task。若失败原因是依赖未落盘，修写入顺序，不要改粒度或补 `splitRationale`。
- 第一个 task 成功落盘后，禁止因为后续 task 失败重新 `init --force` 或全量重建 `plan.json`；只能修正未写入的候选 task。`init --force` 只允许在尚未成功写入任何 task，且明确需要重建空计划时使用。
- 如果 `add-task` 返回 `oversized_plan_task_must_split`，回分组表把该候选标为 `需拆分` 并重新切分；如果返回 `missing_plan_task_split_rationale`，回分组表核对计数、拆分结论和是否遗漏 `splitRationale` 草稿；如果返回 `invalid_plan_task_split_rationale`，回分组表修正草稿，不要反复重试同一个 task JSON。
- `add-task` 粒度失败时该 task 未写入 `plan.json`，可以复用同一 taskId 重新 `add-task`；不得尝试 `set-split-rationale` 修复失败 task。只有已成功写入的 task 后续确需补充合并说明时，才使用 `set-split-rationale`。
- 若最终分组表已判定为 `可合并(附 splitRationale)`，首次写入就必须携带合并说明：逐项 CLI 使用 `add-task --split-rationale`，body 模式在 task JSON 中写 `splitRationale`；不要先提交缺少 rationale 的 task 再等待 writer 打回。
- 若已有 task 成功写入后后续 `add-task` 失败，只重新分组未写入的候选 task，不得修改已落盘 task 的 ID 与 `specRefs`。

写入 `plan.json` 后，必须立即运行本产物结构校验：

```bash
python "${pluginPath}/hooks/plan_writer.py" validate --feature "${feature}" --structure
```

结构校验通过后，再生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/SMOKE_TEST_PLAN.json`。必须先完整读取 `${pluginPath}/skills/autodev/autodev-plan/templates/smoke_test_plan.json`，再通过 `smoke_plan_writer.py init/add-test/set-*` 增量写入，不得先自由生成再依赖 validator 反复修字段。

`SMOKE_TEST_PLAN.json` 是旁路冒烟测试计划，借鉴 superpowers writing-plans 与 TDD 的克制粒度：每个案例只描述一个站在公开 seam 上的 vertical slice，必须写清精确测试源码路径、精确运行命令、预期可观察信号和场景依据。Plan 阶段只写计划，不创建或修改业务测试源码，不批量设计覆盖想象行为的测试矩阵。

`SMOKE_TEST_PLAN.json` 规则：
- 字段结构以 `${pluginPath}/skills/autodev/autodev-plan/templates/smoke_test_plan.json` 和 artifact validator 为准；本文只说明生成语义，不重复维护完整字段清单。
- `flowBlocking` 必须为 `false`；`tests[]` 可以为空，确实没有旁路冒烟价值时写清 `skipReason`，不要为了填表编造案例。
- 每条 smoke 按 `SMK-001` 起编号，引用一个真实 `plan.json.tasks[].id` 和一个 specs 中真实 `SCN-xxx`，不要把多个场景塞进一条冒烟。
- `seam` 描述测试站立的公开边界：`type` 使用 `startup/api/http/ui/cli/job/migration/health/custom` 之一，`entrypoint` 写调用方真实使用的入口，`observable` 写通过公开响应、页面、CLI 输出或健康信号观察到的结果；不得把私有方法、内部类、内部表查询当作 seam。
- `verticalSlice` 只写一个最小闭环：`trigger` 是单一用户动作或系统触发，`expectedOutcome` 是来自 specs/design 的独立可观察结果；不要先规划一批横向用例再等 Code 阶段一起实现。
- `mockPolicy.externalOnly` 必须为 `true`；`allowedMocks` 只能列外部 API、支付、邮件、时间、随机、文件系统或受控测试数据库等系统边界 mock。不得计划 mock 自有模块、内部服务或私有协作者。
- 三类预期不要同义反复：`seam.observable` 写站在公开边界能看见什么，`verticalSlice.expectedOutcome` 写来自 specs/design 的行为结论，`expectedSignals[]` 写测试命令或断言可检查的机器信号（如 HTTP 状态、关键响应字段、页面路由可达、CLI 输出片段；机器校验以断言和退出码为准，不解析自然语言）。
- `sourcePath` 只写计划中的目标测试源码或脚本路径，必须落在业务项目测试/冒烟目录，例如 `src/test/`、`tests/smoke/`、`scripts/smoke/`、`e2e/smoke/`；Plan 阶段不要求文件已存在。这些是 AutoDev 本地验证资产，不是业务项目长期测试资产，Code 阶段必须确保对应路径被目标项目 Git 忽略。
- `command` 必须是只运行对应冒烟案例的 opt-in 命令，例如 `mvn -q -Psmoke -Dtest=OrderSmokeIT verify`、`npm run smoke -- order.spec.ts`；不得写需要人工参与的步骤。
- 冒烟案例可覆盖启动/context、主链路 API、关键 UI route、CLI 主命令、migration/profile 加载、外部依赖 stub 等高风险信号，但必须按场景拆成多条 SMK，每条只覆盖一个 vertical slice；不要用它替代单测/E2E。
- 不得把冒烟命令复制进 `plan.json.tasks[].validationCommands`。`validationCommands` 是强门禁，必须快、稳、可重复；`SMOKE_TEST_PLAN.json` 是旁路风险信号，失败不阻断 `code_done`。

写入 `SMOKE_TEST_PLAN.json` 后，运行 `python "${pluginPath}/hooks/plan_writer.py" render-md --feature "${feature}"` 投影输出 `PLAN.md`，冒烟计划可另行投影为人类摘要但不作为机器事实源。推进 checkpoint 前必须运行 `python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"`；`plan_writer.py validate --gate` 只是不完整的本产物快检，不能替代阶段门禁。

完成条件：
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/plan.json` 文件已写入磁盘
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PLAN.md` 文件已写入磁盘，且从 `plan.json` 投影生成
- [ ] `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/SMOKE_TEST_PLAN.json` 文件已写入磁盘，`flowBlocking=false`
- [ ] `plan.json` 可作为任务 DAG 的机器事实源被后续阶段优先读取
- [ ] 每个任务符合 `templates/plan.json` 与 validator，并能清楚读出业务目标、规格/设计依据、涉及范围、执行要点、强验证命令和预期结果；Plan 初始状态为 `todo`，初始 evidence 为空
- [ ] 冒烟案例符合 `templates/smoke_test_plan.json` 与 validator；每条 smoke 绑定真实 `taskId` 与单个 `SCN-xxx`，包含公开 seam、单个 vertical slice 与 `mockPolicy.externalOnly=true`；没有冒烟案例时写明 `skipReason`
- [ ] 任务按用户可观察 vertical slice 拆分，不按代码层或文件层机械拆分；超过 15 个 task 时已检查是否误拆到代码步骤，没有为了压低任务数合并独立场景
- [ ] 任务没有停留在泛泛描述；每个任务的执行要点至少有一条钉住真实锚点（文件#符号 / 真实入口 / design.md#API/DATA/D-xxx），验证命令带具体目标而非裸 mvn test/npm test；但没有写成逐行代码、逐文件微任务或 commit 步骤
- [ ] 每个任务的「验证命令」都是大模型能直接运行并自行判读的命令（测试/构建/lint/curl/脚本），没有任何"手工""人工验证""Postman""浏览器点击"等需要人参与的步骤；HTTP 接口用 curl 或集成测试覆盖
- [ ] specs 中每个 Requirement / Scenario 至少被一个任务覆盖
- [ ] design.md 中每个接口/数据/技术决策至少被一个实现任务和一个验证方法覆盖，或明确标注无需实现
- [ ] 在 Plan 阶段额外提供了实现细节或技术约束，design.md 与 plan.json 已同步记录，并更新相关任务或风险项。

---

## 整体完成条件
- `design.md`、`plan.json`、`PLAN.md`、`SMOKE_TEST_PLAN.json` 已完成
```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。**
