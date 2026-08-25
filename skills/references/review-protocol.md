# 回检协议

本文件是 dev.specs / dev.plan / dev.code 三个阶段回检段规则。

编辑规则：

- 每个小节由 `<!-- section: <名称> | stages: <取值> -->` 开始，到下一个同类标记或文件末尾为止。
- `stages: *` 表示三个阶段都输出；否则写逗号分隔的节点 ID（`dev.specs` / `dev.plan` / `dev.code`）。
- 小节在输出中的顺序 = 它们在本文件中的顺序。要调整输出顺序就调整本文件顺序。
- 通用文字只允许写在 `stages: *` 的小节里。若发现同一句话被复制进多个阶段小节，说明它应该上移到通用小节。

<!-- section: 前提与角色 | stages: dev.specs -->
## 前提与角色

使用 task 工具，指定 `critic-autodev` 角色，对比上游需求与本阶段产物进行严格审查：spec 是否已完全覆盖需求范围，是否有违反需求的地方。

启动时必须在 task prompt 中写全下面两份清单。任缺一项，回检就只覆盖模型当时想到的部分——遗漏不会以失败的形式出现，只会以「没提」的形式消失。

### 输入材料清单

| 材料 | 用途 | 缺失时 |
|------|------|--------|
| `PRD.md` | 需求来源，逐功能点核对覆盖 | 说明无 PRD，改以用户确认的行为描述为准 |
| `proposal.md` | Capabilities 分组、Decision Log、Open Questions | 缺失即阻断，先回本阶段生成 |
| `specs/**/*.md` | 全部行为契约，逐 REQ/SCN 核对 | 缺失即阻断 |
| `IMPLEMENTATION_SCOPE.json` | 本轮实现范围 | 按兼容规则视为 `full_stack`，在结论中注明 |
| `source-context.json` 与 `sources/SRC-NNN/` | 外部资料要求与快照 | 无外部资料时跳过 |
| `**Existing:**` 断言指向的源码路径 | 核对 New 组是否真的没有存量入口 | 断言为 `none` 时改为自行搜索该能力的入口 |

### 必查项

五项逐项给结论，不合并、不省略。这五项与 `SPECS_REVIEW.md` 的 `## Review Baseline` 一一对应，每项的结论只能取 `通过` / `发现问题` / `不适用`，且必须附证据。

| 必查项 | 查什么 | 证据形态 |
|--------|--------|----------|
| 需求覆盖 | PRD 每个功能点是否都有 REQ/SCN 承接，有无遗漏或与 PRD 矛盾的行为 | 功能点与 REQ/SCN 的对应，或缺口所在 |
| 实现范围符合性 | 逐条 Scenario 是否落在 `IMPLEMENTATION_SCOPE.json` 声明的范围内。`backend_only` 下描述页面布局、点击、展开折叠、下拉、输入框、前端路由跳转的 Scenario 都是越界 | 越界的 `file:SCN-NNN` 与其表述 |
| 操作分类与代码事实 | `New Capabilities` 每项的 `**Existing:** none` 是否属实——实际去代码库搜该能力的入口。找得到就是分类错，应为 Modified | 搜到的 `路径#符号`，或搜索方式与无结果 |
| 上游资料引用 | 每个 `SRC-NNN` 是否被 spec 保留，`targets` 含 `spec` 的要求是否落进 Requirement/Scenario | SRC 编号与落位的 REQ/SCN |
| 待确认项消解 | `Open Questions` 各行是否真由用户裁定，有无自行写成 `已确认`，有无 TBD/待补充/「以实际文档为准」残留 | 行 ID 与其消解依据 |

预检已经机械判定的东西不在这里重复：ID 格式与唯一性、章节齐全、能力双向对应、`**Existing:**` 字段是否存在，都由「产物契约预检」负责。本节查的是那些字段说的**是不是真的**。

<!-- section: 前提与角色 | stages: dev.plan -->
## 前提与角色

使用 task 工具，指定 `critic-autodev` 角色，对比 `specs/**/*.md`、`proposal.md` 与 `design.md`、`PLAN.json` 进行严格审查，从四个维度核查：

1. 技术选择是否合理；
2. 规格是否完全覆盖（Contract Coverage 逐 REQ/SCN 核对）；
3. 测试是否合理和完备；
4. 引用与事实是否相符（Code Evidence 各条与代码实际一致，Spec Traceability 引用的 REQ/SCN/D-xxx 在上游真实存在）。

<!-- section: 前提与角色 | stages: dev.code -->
## 前提与角色

本节发生在所有批次生产代码编译通过之后。此刻 Code runner 的实现与批次编译修复流程均已收口——已完成 TASK 不得重启，编译失败只能在最多 3 次的模型修复流程中处理；批次重入、旧逐 TASK 验证和项目检查入口均不可用。

使用 task 工具，先从 git 中获取本轮改动的代码，对照 `plan.json` 与 `design.md` 同时审查三个方面：

1. 使用 `Explore-autodev` 角色，逐 TASK 对照 `goal` / `specRefs` / `designRefs` 核对 diff：每个任务的改动是否兑现其引用的 REQ/SCN 行为与 API/DATA/D 形态，有无未覆盖的 `acceptanceCriteria`、有无越过 `scope` / `nonGoals` 的改动；
2. 使用 `code-reviewer-autodev` 角色，查看代码是否有不满足设计与需求的地方；
3. 使用 `code-simplifier-autodev` 角色，代码是否有冗余或不合理的地方。

`code-simplifier-autodev` 的默认契约是**直接改文件**（它的输出格式就是 `## Files Simplified` / `## Changes Applied`）。本阶段没有可用的改码通道，因此启动它时必须在 task prompt 中显式要求：只报告建议、给出 `file:line` 与替代写法，不要落笔修改任何文件。

**主 agent 禁止**：不得因回检结论修改任何业务源码、测试或配置；不得改写任何 `action=validation` evidence 或 `validationDisposition`；不得为回检启动新的 task run 或批次编译修复。

子代理若仍然改了文件（它有写权限，约束只在 prompt 层），不要自行还原——本阶段无法重验，静默还原可能覆盖用户自己的改动。改为在结论块中把「子代理已直接修改 <文件清单>，未经任何验证」作为一条 `仅列出` 结论如实记录，交由用户处置。

<!-- section: 严重度词表 | stages: dev.specs,dev.plan -->
## 严重度词表

使用 `critic-autodev` 的原文分节名，不要改写成别的词：

- `Critical Findings` 与 `Major Findings` 下的每一条都必须落入下方分类表的一个分类，不得省略。
- `Minor Findings` 与 `Open Questions (unscored)` 不单独触发改产物。其中涉及取舍的按「需用户裁定」处理，其余归「仅列出」。

<!-- section: 严重度词表 | stages: dev.code -->
## 严重度词表

三个角色的词表不同，本阶段不做归一：

- `code-reviewer-autodev` 输出 CRITICAL / HIGH / MEDIUM / LOW（没有 MAJOR），并把低置信的高危项单列到它自己的 Open Questions；
- `Explore-autodev` 与 `code-simplifier-autodev` 没有严重度轴。

`原文严重度` 一律原样转录角色自己的词，无严重度轴时写 `无`。

<!-- section: 总评行规则 | stages: * -->
## 总评行不作为动作依据

角色的总评行（critic 的 `VERDICT`，code-reviewer 的 `APPROVE` / `REQUEST CHANGES` / `COMMENT`）只是总评，不作为动作依据。即使总评是 `ACCEPT` 或 `APPROVE`，下方逐条处理仍须完整完成。

<!-- section: 逐条复核 | stages: * -->
## 逐条复核

回检结论逐条处理：先用原文复核该条是否成立，再按分类表定动作。受影响产物一次性改完，不逐条往返。

分类轴的共有取值（含义与阶段无关，具体动作见下方分类表）：

- **需用户裁定**：结论要求在多个方案间取舍，或与已确认的决策冲突。
- **回流上游**：上游契约本身缺失或矛盾，不在本阶段补写。
- **仅列出**：成立但不足以触发产物或下游动作（Minor、低置信、风格类）。
- **结论不成立**：复核后与产物或代码实际不符。

<!-- section: 分类表 | stages: dev.specs -->
## 分类表

| 分类 | 判定 | 动作 |
|------|------|------|
| 产物可修 | 行为写漏、写错、操作分类错、索引与 spec 不对应 | 只改被指出的条目，保持 WHAT 层 |
| 需用户裁定 | 结论要求在多个行为方案间取舍 | 补入讨论表，按「待确认问题裁定门」逐条裁定，结果落 `Open Questions` |
| 回流上游 | 上游需求本身缺失、矛盾，或超出本轮范围 | 不扩写 specs；落 `Out of Scope` 或回到用户确认 |
| 仅列出 | 成立但不足以改产物 | 不改产物，在结论块中列出 |
| 结论不成立 | 复核后与 PRD、产物实际不符 | 不改产物，在结论块中引原文说明 |

- 稳定 ID 不重排、不复用；`Status=已确认` 的 `Open Questions` 行不因回检改写。
- 不得靠删 Requirement/Scenario 或缩小 `Capabilities` 消除覆盖类结论。

<!-- section: 分类表 | stages: dev.plan -->
## 分类表

| 分类 | 判定 | 动作                                                |
|------|------|---------------------------------------------------|
| 产物可修 | 技术方案、接口/数据形态、任务拆分、覆盖缺口、验证方法不足 | 技术结论改 design.md，执行结论改 PLAN.json，两边受影响处同步          |
| 引用与事实不符 | Code Evidence 与代码不一致，或引用的 REQ/SCN/D-xxx 不存在 | 更新 EVD-xxx 与引用；与 spec/D-xxx 冲突记 R-xxx（Type=读码差异）走裁定门 |
| 需用户裁定 | 有真实备选且改变实现路径，或与 design.md 中 `Status=已确认` 的 API/DATA/D 决策冲突 | 记 R-xxx（Type=待确认），按「design.md 确认规则」第一步逐条裁定         |
| 回流上游 | 行为契约本身缺失或矛盾 | 停止并建议回 `/autodev-specs`，不在本阶段补写行为契约               |
| 仅列出 | 成立但不足以改产物 | 不改产物，在结论块中列出                                      |
| 结论不成立 | 复核后与产物、代码实际不符 | 不改产物，在结论块中引 file:line 或产物原文说明                     |

- 只改被指出的条目；TASK/EVD/R/API/DATA/D 稳定 ID 不重排、不复用，已裁定行不因回检改写。
- 不得靠删任务、缩小 Contract Coverage 或加「无需实现」豁免消除覆盖类结论。

<!-- section: 分类表 | stages: dev.code -->
## 分类表

本阶段不改代码，所有分类的动作都只是记录与交接。

| 分类 | 判定 | 动作 |
|------|------|------|
| 交接下游 | 实现与引用的行为或已定形态不符、缺失、越界改动，或冗余需整理 | 记入结论块，`处置` 写具体交接阶段 `dev.review` / `dev.utest` / `dev.e2e` |
| 需用户裁定 | 结论要求的做法与已定 REQ/SCN 或 API/DATA/D 冲突 | 按「实现差异协议」发起确认，不得先动代码 |
| 回流上游 | 行为契约本身缺失或矛盾 | 记入结论块并建议回 `/autodev-specs` 或 `/autodev-plan` |
| 仅列出 | 成立但不足以交接（风格类、低置信） | 记入结论块，不产生下游动作 |
| 结论不成立 | 复核后与 diff、产物实际不符 | 不交接，在 `处置` 中引 file:line 说明依据 |

<!-- section: 产出义务 | stages: * -->
## 产出义务

分类处置完成后，必须在回复中输出下面形状的块，每条结论一行：

```
【回检结论】
- 来源: <角色名> | 原文严重度: <角色原样输出的词，无严重度轴时写 无> | 结论: <一句话> | 证据: <file:line 或产物原文> | 分类: <本阶段允许取值，见下> | 处置: <落到哪个产物的哪一条、交接到哪个阶段，或不改的依据>
```

- 每条结论都必须落到一个 `分类`，不允许留空或自造取值。
- 无结论时写：`【回检结论】本轮回检无结论`。

<!-- section: 结论落盘 | stages: dev.specs -->
## 结论落盘

回复中的结论块只是给用户看的即时视图，不是产物。同一份内容必须同时写进 `SPECS_REVIEW.md`，否则本阶段的回检等于没发生过——`specs_review_verdict` 判定它，缺失或不合格时 `specs_done` 写不进去。

按 `autodev-specs` 技能 `templates/specs-review.md` 的形状输出，四节都必填：

- `## Verdict`：`PASS` / `PASS_WITH_WARNINGS` / `FAIL` / `DEGRADED` 取一。只有 `PASS` 与 `PASS_WITH_WARNINGS` 能推进。
- `## Review Baseline`：上面「必查项」五项各一行，结论取 `通过` / `发现问题` / `不适用`，证据列必填。某项标了 `发现问题`，`## Findings` 就必须有对应条目。
- `## Findings`：每条结论一行，`Critical` / `Major` 必须有分类与处置。本轮无结论时正文写「无」。
- `## Unresolved`：仍待用户裁定、尚未拿到答复的条目；有条目就不能推进 `specs_done`。裁定完成后把该条移出本节，并在 Findings 的处置列写下裁定结果——不得自行裁定后直接清空。

<!-- section: 分类取值 | stages: dev.specs -->
本阶段 `分类` 允许取值：`产物可修` | `需用户裁定` | `回流上游` | `仅列出` | `结论不成立`。

<!-- section: 分类取值 | stages: dev.plan -->
本阶段 `分类` 允许取值：`产物可修` | `引用与事实不符` | `需用户裁定` | `回流上游` | `仅列出` | `结论不成立`。

<!-- section: 分类取值 | stages: dev.code -->
本阶段 `分类` 允许取值：`交接下游` | `需用户裁定` | `回流上游` | `仅列出` | `结论不成立`。

<!-- section: 与机器预检的分工 | stages: dev.specs,dev.plan -->
## 与机器预检的分工

「产物契约预检（机器校验）」只判定机械事实（必备产物与章节、格式结构、稳定 ID、引用解析、可机械判定的覆盖关系），本协议只判定它判不了的：需求语义、方案合理性、测试策略、代码事实。同一件事不要两边都报。

预检失败项的 `route` 与本协议 `分类` 一一对应：

| 预检 `route` | 本协议 `分类` |
|---|---|
| `fix_current` | 产物可修 |
| `return_specs` / `return_plan` | 回流上游 |
| `ask_user` | 需用户裁定 |

`仅列出`、`结论不成立`、`引用与事实不符` 只出自本协议，机器预检不产出。

<!-- section: 收口 | stages: dev.specs -->
## 收口

改完先写 `SPECS_REVIEW.md`，再重跑「产物契约预检（机器校验）」——预检此时才会连同回检结论一起判定。仍有未裁定的「需用户裁定」条目时不推进 `specs_done`。

<!-- section: 收口 | stages: dev.plan -->
## 收口

改完重跑「产物契约预检（机器校验）」并逐项复核「完成条件」；仍有未裁定的「需用户裁定」或存在「回流上游」条目时不推进 `plan_done`。

<!-- section: 收口 | stages: dev.code -->
## 收口

本阶段不因回检产生产物修改，无需重跑验证。结论块输出后继续 Code 完成门禁。
