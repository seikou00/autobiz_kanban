# 任务拆分与计划语义

在输出候选分组表前必须阅读本文件。字段、枚举和模板形状以 `plan_writer.py add-task-contract` 的 JSON 为准；这里只保留脚本无法表达的拆分判断和跨阶段边界。

## 规划前清点

模型写入的是 `autodev.plan-core.v1` / `autodev.plan-detail.v1`，不是下游运行时字段。以下运行时名只用于理解 writer 的投影和校验：`outcome -> title/默认 goal`、`dependsOn -> deps`、`writeSet -> touches/scope.paths/writeTargets`、`refs.requirements|scenarios|api -> specRefs/apiIds`、`validation.seam -> validationBoundary`、`validation.mergeJustification -> mergedScenarioRefs/splitRationale`、`ui -> uiRequired/uiRefs`。模型不得同时输出两套字段或把完整运行时 Plan 回传。

先确认 `proposal.md` 的影响模块、`scope.md`、`UI_CONTEXT.json`、设计锁和所有实际 Git 根；前端或外部仓库的具体位置不明时先向用户确认。建立 `workspace -> Git 根 -> workspaceRoot -> 路径格式` 映射，并在开始分组前建立文件写集表：Core 的 `writeSet` 默认投影为整文件 `touches` / `scope.paths`，必须有唯一写入 owner；仅同一 Controller/Service 中可以稳定区分的方法，才可增加 `symbols:["Class#method"]`，且不同 Task 的 symbols 必须互不重叠。此表用于识别共享 Controller、SQL、路由、协议与全局配置，不能等到 detail 阶段才发现冲突。

候选分组通过 `write-task-groups --body-stdin` 提交：脚本先在内存中完成一次完整预检，只有通过后才原子写入 Core 源。它一次返回结构、粒度、共享写集、Design API、SCN 实体和覆盖问题，不能用覆盖不完整的 2-task 示例单独验证路径格式。路径和字段形状以模板、`add-task-contract` 和完整候选分组的返回值为准；`grouping.detailObligations` 会提前列出 matrix Detail 义务。Draft 创建后，先以一个高风险 task 的 `lint-draft-task-detail` 作为不落盘详情冒烟验证；首次全量提交用 `lint-draft-task-details --full`，通过后再使用同一 payload 的 `set-draft-task-details --full`。

## 拆分候选任务

一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令。先按 vertical slice 拆开，只在共享同一验证闭环时合并；不要按 capability、模块、Controller、DTO、Mapper、SQL、样式或测试文件分任务。

1. 先按 Source Bundle 和 `implementationScope` 确定本轮 specs 分母。`backend_only` / `frontend_only` 不得把已剥离范围重新放进覆盖矩阵，也不要从 PRD 余量或关键词推导额外任务。
2. 在对话中输出 Scenario 覆盖矩阵：`SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam / 风险或依赖`。矩阵中的每个完整路径级 `SCN` 最终都要进入某个 task 的 `refs.scenarios`。
3. 再输出最终候选任务分组表：`候选 Task / refs.requirements 与 refs.scenarios / SCN 数 / refs.api 数 / Page 数 / UIX 数 / implementation 数 / checks / dependsOn / writeSet owner / 拆分结论 / mergeJustification 草稿`。先按“用户动作 + 公开 seam + 自动化验证边界”分组；同一文件本身不是合并理由，但若同一端到端动作必须修改同一入口，优先把改动和验证闭环收敛到一个 owner task；不要一边补 task detail 一边重新拆分。
4. 进入 writer 前一次性编号为连续 `T001`、`T002`、`T003`……；不得生成 `T003a`、`T004b1`、`v2` 或 `v3`。不同 spec 文件里的同号 `SCN-001` 是不同场景，必须写 `specs/<capability>/spec.md#SCN-001`，不能用范围或拼接文本计数。

每个候选 task 都应是可理解、可执行、可验证的业务闭环；可以跨接口、服务、数据、前端和配置。基础能力可以单独成 task，但必须验证下游公开 seam；否则并入最早消费它的业务闭环。

## 合并与粒度

普通 Core task 不写 `validation.mergeJustification`。只有同时满足以下条件才允许标记 `可合并(附 mergeJustification)`：同一触发动作、同一公开 seam、同一验证命令或同一组断言、拆开会复制同一验证闭环，而且没有超过硬上限。理由必须直接列出完整引用并解释共享的请求响应、权限或状态矩阵；“同一模块”“实现方便”等理由无效。writer 会从 `refs.scenarios` 派生运行时 `mergedScenarioRefs` / `splitRationale`。**API=3 仍在硬上限 3 内，但已超过软上限 2，因此必须填写该理由**；SCN 例外至少列出 3 个完整场景引用，例如：`specs/mobile-summary/spec.md#SCN-001, specs/mobile-summary/spec.md#SCN-002, specs/mobile-summary/spec.md#SCN-003 共享同一次提交动作、同一个响应断言和同一验证闭环，不能独立验证。`；若 API、PAGE 或 UIX 超阈值，也必须分别点名足够数量的真实 ID。

- 常规 task：SCN `<=5`、`refs.api` `<=2`、`ui.pages` `<=1`、`ui.interactions` `<=3`；Detail 的 `implementation` 为 2–6 条，且至少一条可独立运行的验证命令。
- 例外 task：至少一个维度超过软阈值，但不得超过 SCN `12`、`refs.api` `3`、`ui.pages` `2`、`ui.interactions` `4`；在 Core 中填写 `validation.mergeJustification`，不要写 `mergedScenarioRefs` 或 `splitRationale`；writer 会从 `refs.scenarios` 派生它们。SCN 例外还需在 Detail 用**恰好一条** required 的 `behavior_test`、`integration_test` 或 `e2e_test` 覆盖全部 AC（UI task 也可使用匹配的 frontend compile 类型）。省略 `checks[].covers` 让 writer 自动覆盖全部 AC；手写时必须等于全部 AC，不能只写 `[1]`。
- 超过硬上限、存在不同用户动作/seam/验证边界，或结论为 `需拆分` 时都不得进入 Draft。一个候选组只允许一次拆分；预检失败回覆盖矩阵重新分组，不能通过补 detail 探索拆法。

## 字段与跨阶段边界

- Core 的 `refs.requirements` 与 `refs.scenarios` 至少分别包含真实的 `REQ-*` 和 `SCN-*`。Detail 的 `refs.design`、`refs.data`、`refs.decisions` 只能引用已锁定设计中的真实 ID；不涉及时写空数组 `[]`，不得补造 `API-*` / `DATA-*` / `D-*`。设计需要改变时，回 `/autodev-design`，Plan 不得改 `design.md`。
- Core 的 `outcome` 描述用户可观察结果；Detail 的 `implementation` 说明真实入口、符号或文件锚点和关键失败路径，但不要拆成逐文件微任务或代码块。`nonGoals` 至少有一条具体的相邻范围排除；Core 的 `validation.seam` 指向公开 seam 与可运行校验。
- Detail 的 `checks` 是后续 UTest/E2E 的测试意图，不在 Code 阶段运行。命令必须窄、快、可自行判读；缺测试时声明真实待补边界，不伪造测试目标或 `create_in_code`。`mode=external_dependency` 必须说明 `external.system`、`external.owner`、`external.trackingRefs`，且不得配置本地验证。
- `mode=verified_existing` 只用于确认已存在的行为，不得计划业务代码修改。它不要求 `writeSet` 非空，通常应保持为空；用真实公开 seam、验证命令、场景引用和实现锚点说明验证范围。不要把只读验证对象放进 `writeSet`，以免错误占用写入归属。
- 用户补充的任务边界、验证方法和风险写入对应计划；若改变行为契约或设计决策，分别回 Specs 或 Design。Plan 不修改业务代码、测试、迁移或配置。

### UI 任务

先读取并校验 `UI_CONTEXT.json`。Core 的 `ui` 存在即为 UI Task，包含 `pages`、`interactions`、`visualSources`、`route`；writer 投影成运行时 `uiRequired` 和 `uiRefs`。所有 PAGE/UIX/VIS 和 route 必须逐项投影自 UI_CONTEXT：

- UI task 写 `ui:{pages,interactions,visualSources,route}`；`scope.pages` 与运行时 `uiRefs.pageRefs` 由 writer 保持一致；非 UI task 不写 `ui`。
- 不得按出现顺序新编 PAGE/UIX，不得为了预检虚构 UI task，也不能把 `visualSourceRefs` 或 `frontendRoute=spec-driven-ui` 当默认值。
- 缺少可引用的 UI capability 时回 Specs。仅后端菜单、权限或菜单数据变更仍是非 UI task；HTML 转换由 Code 的 frontend route 负责。

### 工作区与 Batch

每个 Core task 只绑定一个真实 Git 根 `workspace`；跨仓库行为拆成 task 并用 `dependsOn` 表达。`scope.workspaceRoots` 由 writer 根据 `prepare-task-draft --code-workspace` 派生，`scope.paths` 只写相对该 workspace 的提示性路径，不是实现文件白名单；具名仓库路径使用 `repoId:relative/path`，默认 workspace root 不带前缀。路径不得是绝对路径，也不得重复已经由 `workspaceRoots` 表示的仓库根。Core 的 `writeSet` 会投影为该任务的 `scope.paths`，因此须使用同一已确认的路径格式。`checks[].cwd` 省略时由 writer 派生；最终 command 的 `repo` 与 task 的 `workspaceRef` 相同。

writer 由 `ui` 推导 lane（backend / frontend）和 Batch。任务按 DAG 拓扑序排列，backend 不依赖 frontend，依赖不得前向或跨批成环。`writeSet` 是共享写集 owner 声明：跨 Batch 的同一写入路径默认只能由一个 Batch owner 持有。对同一 Controller/Service 的独立新增方法，可以让各 Task 以同一 `path` 加互不重叠的稳定 `symbols`（如 `ActivityController#submit`）声明成员级归属；任一 Task 未声明 symbols，或 symbols 重叠，仍按整文件冲突处理。共享 SQL、路由、协议或全局配置由一个前置 owner task 完成，消费者只通过 `dependsOn` 依赖，并从 `writeSet` 和实现锚点移除该路径；同一 Batch 内的任务不因同文件自动违规，但仍应优先按可观察业务闭环收敛。未知写集保守串行；`proto`、`global`、`integration` Batch 单独收口。

`preflight-task-groups` 通过后，`prepare-task-draft` 会锁定 `groupingDigest`。后续若分组变动，只能通过受限 Core patch 或 `rebuild-task-draft`，不得手工同步 Draft；读取结果中的 `preservedTaskIds` 和 `resetTaskIds`，只为被 reset 的任务重填 detail，不要预设 rebuild 的影响范围。已 finalized 且尚未执行时，如果 Design snapshot 和 groupingDigest 同时漂移，`diagnose-plan-repair` 会返回 `full_rebuild_required`；使用 `rebuild-finalized-draft --group-file <file> --design-revision-confirmed --reason <reason>` 原子重投影，不删除 Draft。
预检发现 API 软上限、覆盖、粒度或共享写集问题时，先保留现有 `task-groups.json`，执行 `create-repair-work --group-file <file>`，再按返回的 `allowedOps` 和 `baseGroupingDigest` 用 `apply-draft-patch` 做定点 `replace`。不得删除文件、不得输出新的完整 Core、不得为绕过共享 Controller 冲突擅自新增平行 Controller；架构确需变化时回到 Design。共享写集的消费者从 `writeSet` 移除文件并用 `dependsOn` 消费 owner 产物；随后才填充或重投影受影响 Task 的 detail。

批次编译不属于 Plan 合同；`qualityGateProfiles` 只放静态检查。可选 `projectValidationCommands` 只在所有 Batch 合并后的 B-E2E 工作树执行，不能替代 task 的 AC 覆盖。
