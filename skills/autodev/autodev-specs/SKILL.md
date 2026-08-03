---
name: autodev-specs
description: Dev 阶段行为规格生成。
version: v1.8.0803
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

## 输入与输出

读取输入:
- 与当前 feature 相关的现有代码、接口、数据模型、测试、配置

输出产物：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/proposal.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/specs/<capability>/spec.md`

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

> 进入探索前先使用write_todos工具建立一份覆盖宏观流程的任务清单：`探索并生成待确认问题清单` / `逐条裁定待确认问题` / `统一生成 proposal 与 specs` / `集中校验并推进 specs_done`，并随阶段推进实时更新状态（待做 / 进行中 / 完成）。
使用task工具，指定Explore-autodev角色进行探索，task prompt 第一行必须传入系统约束中可参考的文件清单。
子代理先读该清单、再顺其索引读相关约束文档，然后按下面的要求返回结构化内容供主代理参考。
探索时必须：

- 从上游需求输入提取目标、用户角色、主流程、验收标准、非目标。
- 阅读现有代码，识别已有接口、数据模型、权限、租户、审计、错误体、分页、状态流、配置和测试风格。
- **只探索源码，不碰编译/生成产物**：`target/`、`build/`、`out/`、`bin/`、`*.class`、`*.jar/war/ear`、`__pycache__/`、`*.pyc`，以及一切 `.gitignore` 命中的路径，都不是事实源，不得据其识别接口/数据模型/约定——它们由源码再生成。扫描优先 `git ls-files <pattern>` 找文件、`git grep <regex>` 搜内容：只走已跟踪源码，自动排除上述产物；不要用裸 `find`/`grep` 做全库扫描。例外：某生成物本身就是问题对象时可读，但须标注「生成物」并回溯到其生成器/源码。
- 将上游需求改写为外部可观察行为，不要把实现猜测写成需求。
- 识别 capabilities：一组可以独立命名、独立验收的能力边界，例如 `order-export`、`approval-reminder`。
- 与用户对齐了术语或规范代码名时，按 `${pluginPath}/skills/references/domain-context.md` 当场回写会话工作区 `CONTEXT.md`（领域词汇表）；只收已对齐术语。
- 如果 API 或数据边界会影响行为契约，必须先与用户讨论。不要带着关键待确认项生成 specs。

接口/数据决策讨论触发：

- 如果新增或修改 HTTP/API、函数入口、请求响应、错误码、权限、租户、审计、幂等、分页、异步行为，但接口形态还不准确，先讨论。
- 如果涉及表、字段、状态、枚举、索引、唯一约束、迁移、回滚、数据保留、历史兼容，但数据语义还不准确，先讨论。
- 讨论时只提出影响实现路径或验收结果的关键问题，并给出当前建议、备选方案和影响面；不要机械问卷。
- 仍有 `待确认` 且会影响接口形态、数据模型、权限/租户/审计、幂等、分页、异步、状态流、迁移或验收结果时，不要结束探索。

探索结束时先生成待确认问题清单。需求、PRD 或用户材料中的「待补充」「待提供」「后续给出」如影响行为契约，逐项列入；无待确认项时写「无」并继续生成产物。

讨论输出：

```markdown
## 行为/API/数据决策待确认

我不建议现在直接生成 specs，因为以下决策会影响行为契约或实现路径：

| ID | 类型 | 决策点 | 当前建议 | 备选方案 | 影响 | 需要确认 |
|----|------|--------|----------|----------|------|----------|
| SPEC-01 | Behavior | [行为边界] | [建议] | [备选] | [影响验收] | [问题] |
| API-01 | API | [接口入口/请求响应/错误码] | [建议] | [备选] | [影响任务/验收] | [问题] |
| DATA-01 | Data | [表/字段/状态/约束] | [建议] | [备选] | [影响任务/验收] | [问题] |
```

### 待确认问题裁定门

- 仅裁定讨论表中的待确认条目；没有条目时直接生成产物。
- 消解定义：裁定即消解，但**裁定必须落盘才算数**。生成 proposal 时每条落为 `Open Questions` 一行，`Status=已确认`，裁定结论体现在对应的 Requirement/Scenario 上。
- 协议：按共享 `ask-user-question.md` 协议用 `request_user_input` 逐条提问，每轮最多 3 项（对应协议中「逐项裁定」条款）；`id` 与讨论表条目 ID 对应（如 `SPEC-01` → `spec_01`）。这是阶段门的组成部分，不设置 `autoResolutionMs`，必须等待明确答复。
- 选项闭集：每条给 2–3 个互斥选项，语义只能从以下四类中取——①「按当前建议确认 (Recommended)」：采纳讨论表中的当前建议；②「采纳备选：<方案>」：选项自身携带具体方案；③「需要调整」：用户将给出修改意见，吸收后更新讨论表、重新展示、该条重新裁定；④「暂停，拿到材料后继续」：仅信息缺口型条目可用，保留在 specs 阶段、不推进。
- 信息缺口不得使用「已准备好，稍后提供」或「后续补充并继续」；现在提供材料时由用户在「其他」中填写。
- **禁止自行确认**：`已确认` 只能是用户裁定的结果。不得以「这是外部接口细节」「不影响行为契约的定义」「specs 阶段只关心 WHAT」等任何理由，自己把 Status 写成 `已确认`。判定某条不影响行为契约不是跳过裁定的理由，必须由用户裁定。
- **自由表达即退出结构化**：用户不点选项、而是直接给出实质回复（补一条决策、改一个字段、提新问题），当作该条的裁定内容吸收并更新，**不得机械重复弹同一个结构化选择**；下一轮合适时机再重新发起该决策。
- 每次发起问题后停止执行，等待用户回复；不得在同一轮继续生成产物。
- 全部条目裁定后直接生成 proposal 与 specs，不再确认 capability 切分或规格范围。
- `request_user_input` 不可用时按共享协议文本降级，并结束当前回复等待用户。

## 生成 proposal.md

讨论表有待确认条目时，全部裁定后进入本节；没有条目时直接进入本节。

按 `${pluginPath}/skills/autodev/autodev-specs/templates/proposal.md` 输出。

生成前一次性建立规格清单，列出每个 capability 的名称、分类与 `REQ IDs / SCN IDs`。同一份清单用于生成 proposal 与全部 specs，不逐文件临时起名。

capability 的变更分类写进 `## Capabilities` 节。探索中形成的判定依据与既有行为来源在对话中说明，本节只留结论。

分类规则：

- `ADDED`：当前系统没有对应的外部可观察能力、入口、流程或业务结果；本轮新增一个可独立验收的行为边界。复用已有组件、接口或表，不影响 `ADDED` 判定。
- `MODIFIED`：已有能力仍然存在，但本轮改变或扩展其外部可观察行为，包括条件、输出、校验、权限、错误码、状态流、异步时机、数据口径、UI 状态或交互分支。给已有流程增加筛选项、字段、按钮、状态、限制条件或兼容逻辑，默认是 `MODIFIED`。
- `REMOVED`：已有能力、入口、分支或业务结果在本轮后不再支持、不可访问或不再生效；必须说明移除原因、迁移/兼容方式，以及旧入口被触发时的期望行为。
- 同一用户目标同时包含新增独立能力和修改既有能力时，拆成不同 capability 或同一 spec 内不同 Requirement，不得用一个分类吞掉全部变化。
- 无法判断是否已有行为时，先搜索既有 specs、代码入口、接口、菜单、配置和测试；仍不确定则回到用户确认，不要猜测分类。
- 本轮某个分组无 capability 时该组写 `无`；不得保留占位行。

必须包含：

- **Why**：为什么要做。
- **What Changes**：用户可见或系统外部可观察变化。
- **Capabilities**：按 New / Modified / Removed 分组列出本轮能力，名称使用 kebab-case。
- **Impact**：影响模块、接口、数据、权限、配置、测试或运维。
- **Out of Scope**：本轮明确不做的内容。
- **Open Questions**：discussion 表中的每条待确认项落一行，按上面「待确认问题裁定门」的消解定义填 `Status`；本轮无待确认项时本节正文只写「无」。

## 生成 specs/**/*.md

按 `${pluginPath}/skills/autodev/autodev-specs/templates/spec.md` 输出。

规则：

- 按规格清单统一生成全部 spec，再进入校验；不得生成一个、校验一个、修复一个。
- **列入即生成**：`Capabilities` 中每一项（正文"无"除外）都必须有对应的 `specs/<capability>/spec.md`，反过来每个 `specs/*/spec.md` 也必须能在 `Capabilities` 中找到出处。若认为某 capability 不值得单独成 spec，唯一合法做法是回到 proposal 将其移除或并入其他 capability；禁止单方面少生成。集中校验的 `capability_spec_correspondence` 双向判定这条，无需在回复中自行输出对照表。
- specs 定义 **WHAT**，不得写实现步骤、类名、SQL 细节或任务拆分。
- Requirement 使用 `### Requirement [REQ-NNN]: <标题>`（NNN 三位递增；改标题不改 ID；删除后 ID 不复用；ID 在同一 feature 内全局唯一）。
- Scenario 使用四级标题 `#### Scenario [SCN-NNN]: <标题>`，归属本文件中已存在的 REQ。
- 每个 Requirement 至少一个 Scenario；REMOVED Requirement 也必须用 Scenario 描述旧入口被触发时的期望响应。
- 使用 SHALL/MUST 表达可验证行为。
- 每个 Requirement 只能放入一个操作段：`ADDED Requirements`、`MODIFIED Requirements` 或 `REMOVED Requirements`。
- `ADDED Requirements` 只写新增行为；如果只是已有行为增加条件、字段、状态或分支，放入 `MODIFIED Requirements`。
- `MODIFIED Requirements` 必须写修改后的完整行为，并在 Requirement 正文或 Scenario 中覆盖旧行为受影响的触发条件和新期望；不要只写“新增字段”“调整逻辑”这类差异片段。
- `REMOVED Requirements` 必须写旧能力的移除原因、迁移/兼容方式，并用 Scenario 描述旧入口、旧条件或旧分支被触发时系统应该如何响应。
- 某个操作段无内容时保留段标题，段下不写 Requirement（留空或写“无”均可）；不要把“无”写进 Requirement 正文而保留标题，也不要保留模板占位 Requirement。
- 操作段要与 proposal 的分组对上：`New Capabilities` 的 spec 在 `ADDED Requirements` 下写 Requirement，且 `MODIFIED`/`REMOVED` 段下不得有 Requirement（全新能力没有存量可改可删）；`Modified`/`Removed` 的 spec 必须在同名操作段下写 Requirement，另加 `ADDED` 是允许的。`capability_spec_correspondence` 判定这条。
- 对未确认且影响行为的内容，必须回到用户确认；不要把猜测写进 specs。

## 集中校验

proposal 与全部 specs 生成完成后执行一次完整校验：

```bash
python "${pluginPath}/skills/autodev/hooks/artifact_check.py" postcheck autodev-specs "${feature}" --repo-root "${pluginPath}" --workspace-root "${pluginWorkspace}/${projectDir}"
```

- 等命令完整结束后再处理结果。
- 汇总本次输出中的全部失败项，按文件归组，一次性修改所有受影响产物。
- 修改完成后重新执行同一完整校验；通过前不得推进 checkpoint。
- 不以 `update_checkpoint.py` 代替产物预检，不按单条错误在校验与编辑之间往返。

## 完成条件

- 「输入与输出」列出的两个产物都已生成，`specs/` 下至少存在一个 `spec.md`。
- 集中校验通过。能力双向对应、REQ/SCN ID 格式与唯一性、每个 Requirement 至少一个 Scenario、proposal 必备章节都由它判定，失败无法写入 specs_done。
- specs 只描述行为契约，不包含实现任务。
- `Open Questions` 每行都经逐条裁定门消解（`Status=已确认`），或本节正文只写「无」。

## 回检与修复

本节完整协议由脚本按阶段渲染,必须先运行下面命令，并完整遵循其输出；不得凭记忆执行本节，也不得跳过该命令。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.specs
```

集中校验与回检修复均通过后推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
```

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
