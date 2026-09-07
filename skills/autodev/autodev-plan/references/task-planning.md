# 任务拆分与计划语义

在输出候选分组表前必须阅读本文件。字段、枚举和模板形状以 `plan_writer.py add-task-contract` 的 JSON 为准；这里只保留脚本无法表达的拆分判断和跨阶段边界。

## 拆分候选任务

一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令。先按 vertical slice 拆开，只在共享同一验证闭环时合并；不要按 capability、模块、Controller、DTO、Mapper、SQL、样式或测试文件分任务。

1. 先按 Source Bundle 和 `implementationScope` 确定本轮 specs 分母。`backend_only` / `frontend_only` 不得把已剥离范围重新放进覆盖矩阵，也不要从 PRD 余量或关键词推导额外任务。
2. 在对话中输出 Scenario 覆盖矩阵：`SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam / 风险或依赖`。矩阵中的每个完整路径级 `SCN` 最终都要进入某个 task 的 `specRefs`。
3. 再输出最终候选任务分组表：`候选 Task / 完整 specRefs 清单 / SCN 数 / API 数 / Page 数 / UIX 数 / implementationPoints 数 / validationCommands / deps / 拆分结论 / splitRationale 草稿`。先按“用户动作 + 公开 seam + 自动化验证边界”分组；不要一边补 task detail 一边重新拆分。
4. 进入 writer 前一次性编号为连续 `T001`、`T002`、`T003`……；不得生成 `T003a`、`T004b1`、`v2` 或 `v3`。不同 spec 文件里的同号 `SCN-001` 是不同场景，必须写 `specs/<capability>/spec.md#SCN-001`，不能用范围或拼接文本计数。

每个候选 task 都应是可理解、可执行、可验证的业务闭环；可以跨接口、服务、数据、前端和配置。基础能力可以单独成 task，但必须验证下游公开 seam；否则并入最早消费它的业务闭环。

## 合并与粒度

普通 group 的 `mergedScenarioRefs` 保持空数组。只有同时满足以下条件才允许标记 `可合并(附 splitRationale)`：同一触发动作、同一公开 seam、同一验证命令或同一组断言、拆开会复制同一验证闭环，而且没有超过硬上限。`splitRationale` 必须点名完整 SCN/API/PAGE/UIX 并解释共享的请求响应、权限或状态矩阵；“同一模块”“实现方便”等理由无效。

- 常规 task：SCN `<=5`、apiIds `<=2`、pageRefs `<=1`、interactionRefs `<=3`、`implementationPoints` 为 2–6 条，且至少一条可独立运行的验证命令。
- 例外 task：至少一个维度超过软阈值，但不得超过 SCN `12`、apiIds `3`、pageRefs `2`、interactionRefs `4`；SCN 例外还必须填写 `mergedScenarioRefs`，并用一条 required 的 `behavior_test`、`integration_test` 或 `e2e_test` 覆盖全部 AC。
- 超过硬上限、存在不同用户动作/seam/验证边界，或结论为 `需拆分` 时都不得进入 Draft。一个候选组只允许一次拆分；预检失败回覆盖矩阵重新分组，不能通过补 detail 探索拆法。

`preflight-task-groups` 通过后，`prepare-task-draft` 会锁定 `groupingDigest`。后续若分组变动，只能 `rebuild-task-draft`，不得手工同步 Draft；预检问题回覆盖矩阵定位遗漏并重新分组。

## 字段与跨阶段边界

- `specRefs` 至少包含真实的 `REQ-*` 和 `SCN-*`。`designRefs`、`apiIds`、`dataIds`、`decisionIds` 只能引用已锁定设计中的真实 ID；不涉及时写空数组 `[]`，不得补造 `API-*` / `DATA-*` / `D-*`。设计需要改变时，回 `/autodev-design`，Plan 不得改 `design.md`。
- `goal` 描述用户可观察结果；`implementationPoints` 说明真实入口、符号或文件锚点和关键失败路径，但不要拆成逐文件微任务或代码块。`nonGoals` 至少有一条具体的相邻范围排除；`validationBoundary` 指向公开 seam 与可运行校验。
- `validationCommands` 是后续 UTest/E2E 的测试意图，不在 Code 阶段运行。命令必须窄、快、可自行判读；缺测试时声明真实待补边界，不伪造 `expectedFiles`、测试目标或 `create_in_code`。`executionMode=external_dependency` 必须说明 `system`、`owner`、`trackingRefs`，且不得配置本地验证。
- 用户补充的任务边界、验证方法和风险写入对应计划；若改变行为契约或设计决策，分别回 Specs 或 Design。Plan 不修改业务代码、测试、迁移或配置。

### UI 任务

先读取并校验 `UI_CONTEXT.json`。`uiRequired` 是 group 顶层 bool；`uiRefs` 只包含 `pageRefs`、`interactionRefs`、`visualSourceRefs`、`frontendRoute`。所有 PAGE/UIX/VIS 和 route 必须逐项投影自 UI_CONTEXT：

- UI task 写 `uiRequired:true`，`scope.pages` 与 `uiRefs.pageRefs` 一致；非 UI task 显式写 `uiRequired:false` 且 `uiRefs` 为空。
- 不得按出现顺序新编 PAGE/UIX，不得为了预检虚构 UI task，也不能把 `visualSourceRefs` 或 `frontendRoute=spec-driven-ui` 当默认值。
- 缺少可引用的 UI capability 时回 Specs。仅后端菜单、权限或菜单数据变更仍是非 UI task；HTML 转换由 Code 的 frontend route 负责。

### 工作区与 Batch

每个 group 只绑定一个真实 Git 根 `workspaceRef`；跨仓库行为拆成 task 并用 `deps` 表达。`scope.workspaceRoots` 由 writer 根据 `prepare-task-draft --code-workspace` 派生，`scope.paths` 只写相对该 workspace 的提示性路径，不是实现文件白名单；具名仓库路径使用 `repoId:relative/path`。`validationCommands[].cwd` 保持 Git 根相对路径，且 command 的 `repo` 与 task 的 `workspaceRef` 相同。

writer 由 `uiRequired` 推导 lane（backend / frontend）和 Batch。任务按 DAG 拓扑序排列，backend 不依赖 frontend，依赖不得前向或跨批成环。`touches` 是共享写集 owner 声明：同一 workspace 中共享 SQL、路由、协议或全局配置由一个前置 owner task 完成，消费者只通过 `deps` 依赖。未知写集保守串行；`proto`、`global`、`integration` Batch 单独收口。

`compileProfiles` 为每个实际 lane 配置一条 required `kind=compile` 命令；`qualityGateProfiles` 只放静态检查。可选 `projectValidationCommands` 只在所有 Batch 合并后的 B-E2E 工作树执行，不能替代 task 的 AC 覆盖。
