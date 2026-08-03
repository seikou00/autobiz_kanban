# release-0727 独立特性发版顺序

## 1. 文档目标

本文给出从 `release-0727` 向当前 `dev_workflow_py` 能力演进的最终合入顺序。

目标不是复刻当前分支的提交历史，而是把长期分叉产生的混合提交重新整理为可独立合入、独立启用、独立验证和独立回滚的 Feature PR。

基线信息：

- 发布基线：**`dev_0803`**（= `release-0727` + 11 个 spec/skill 提交）。`release-0727` 的 SHA 为 `a58ccc14586885b5d37de628b20068bbf63a35ab`，即两者的 merge-base；本文原先以 `release-0727` 为基线的所有步骤一律改为从 `dev_0803` 出发。
- 能力来源：`dev_workflow_py`，已提交 HEAD 为 `06f0bfb56d226caa93a9efee6c6b91c60ccedf6d`。
- 共同祖先：`f52ef45ac0860a96657f731fb0c34ebaf0a8499f`。
- 当前分支相对共同祖先有 63 个独有提交（本文初稿写 60，其后新增 `bf444f5` `e21c548` `06f0bfb`）。
- 当前工作区另有尚未提交的“验证延期”实现，本方案将其作为最后一个独立特性纳入。

## 2. 独立特性约束

每个 Feature PR 必须满足以下条件：

1. 只依赖本清单中排在它之前的公共底座或特性，禁止依赖后续特性。
2. 合入后即使暂不启用，也必须保证现有 `release-0727` 流程可运行。
3. 新行为必须通过显式配置、契约字段或输入条件启用，不能依赖“后续 PR 会补齐”。
4. 每个特性拥有独立测试范围、发布标签和回滚提交。
5. 回滚一个特性时，不得删除公共底座，也不得破坏其他已经发布的特性。
6. Schema 变更采用只增不删；特性关闭后允许保留未消费的新字段。
7. 前端和后端能力分别合入。共享内核可以共用，但 Lane 激活、验证策略和优化必须分开。

“独立”表示特性可以被跳过或回滚，不表示它可以脱离公共底座运行。

## 3. 公共底座原则

### F00：兼容与扩展底座

F00 是唯一的强制前置项，不作为业务特性发版。

职责：

- 从 `release-0727` 创建新的集成线。
- 保留基线已有的 Agent 注入、独立 `autodev-frontend`、Session Context 和 Checkpoint 行为。
- 恢复并适配通用 Writer、Plan、Evidence、Task Runner 和 Validation 扩展 API。
- 提供 Lane、Validation Profile、Validation Policy 和可选状态字段的注册能力。
- 所有新策略默认关闭，保持基线行为：无 Batch Plan 自动启用、无验证合并、无验证延期。
- 不在 F00 中启用后端或前端的新执行流程。

建议分支：

```text
integration/release-0727-feature-platform
```

F00 需要重点语义合并的文件：

```text
board_core/board_config.json
hooks/plan_json.py
hooks/plan_writer.py
hooks/evidence_store.py
hooks/evidence_integrity_gate.py
hooks/task_runner.py
skills/autodev/hooks/artifact_check.py
skills/autodev/*/SKILL.md
```

禁止用 `ours` 或 `theirs` 整体覆盖这些文件。

## 4. 最终发版顺序

```text
F00 公共兼容底座
  -> F01 机器产物与 Evidence
  -> F02 Batch Plan 与原子发布
      -> F03 后端执行与验证
          -> F04 多仓库与运行恢复
          -> F08 后端验证优化
      -> F05 前端计划与 UI 契约
          -> F06 前端实现与高保真路由
              -> F07 前端验证与冒烟
                  -> F09 前端验证优化
  -> F10 验证执行可靠性与跨平台
  -> F11 验证延期
```

实际发板按“公共底座 -> 后端 -> 前端 -> 通用验证”执行，避免同一板混合前后端改动。特性编号是稳定标识，不代表必须按编号连续发布；例如 F05 不依赖 F03/F04，整条后端线都可以跳过而不阻断前端线。

| 板次 | 特性 ID | 范围 | 特性 | 前置依赖 | 是否可跳过 | 发布边界 |
| --- | --- | --- | --- | --- | --- | --- |
| 准备批 | F00 | 公共 | 兼容与扩展底座 | 无 | 否 | 内部集成标签，不对外启用新行为 |
| 第 1 板 | F01 | 公共 | 机器产物与 Evidence | F00 | 是 | Writer、Stage Gate、Evidence 独立可用 |
| 第 2 板 | F02 | 公共 | Batch Plan 与原子发布 | F01 | 是 | Plan 分批，不启用任何 Lane 执行 |
| 第 3 板 | F03 | 后端 | 后端执行与验证 | F01、F02 | 是 | 只启用 Backend Lane |
| 第 4 板 | F04 | 后端 | 多仓库与运行恢复 | F02、F03 | 是 | 后端多仓库和恢复能力 |
| 第 5 板 | F08 | 后端 | 后端验证优化 | F03 | 是 | Maven 聚合、修复责任优化 |
| 第 6 板 | F05 | 前端 | 前端计划与 UI 契约 | F01、F02 | 是 | 只生成 Frontend Lane 计划 |
| 第 7 板 | F06 | 前端 | 前端实现与高保真路由 | F05 | 是 | 适配基线独立 `autodev-frontend` |
| 第 8 板 | F07 | 前端 | 前端验证与冒烟 | F01、F02、F05、F06 | 是 | 只启用前端 build/typecheck/lint |
| 第 9 板 | F09 | 前端 | 前端验证优化 | F07 | 是 | 前端重复构建去重 |
| 第 10 板 | F10 | 公共验证 | 验证执行可靠性与跨平台 | F00；至少启用 F03 或 F07 之一才产生运行效果 | 是 | 超时、日志、Windows、子代理协议 |
| 第 11 板 | F11 | 公共验证 | 验证延期 | F01、F02、F10；至少启用 F03 或 F07 之一才产生运行效果 | 是 | 默认关闭，显式策略启用 |

## 5. 各特性范围

### F01：机器产物与 Evidence

能力范围：

- UI、Plan、Review、UTEST、E2E、Verify 等 JSON Writer。
- 稳定 ID、通用 Writer 校验和 Stage Gate。
- Evidence Store、Audit、Integrity Gate 和防跳过约束。
- Task Runner 的基础 Evidence 写入，不启用批次验证。

主要来源：

```text
85605d1 788c58c ce9d211 1d85ffe
b0a3d1e fdeb59b 4cbd3f4
```

独立启用条件：工作流契约声明对应 JSON 产物时启用；旧文档型产物流程保留兼容路径。

验收：Writer 测试、Artifact Contract 测试、Evidence append-only 和 Code Done Gate 全部通过。

### F02：Batch Plan 与原子发布

能力范围：

- Root Plan 与 `plans/Bxxx/plan.json` Batch 事实源。
- 按拓扑、能力和 Lane 分批。
- Task 输入模板、粒度门禁和完整任务集物化。
- Plan Draft 逐任务补齐后原子发布。
- 本特性只负责计划生成，不执行 Backend 或 Frontend Lane。

主要来源：

```text
15597b3 fe73e23 07ae948 d96d9e6
5e0a4da f610854 b47acca b6ea850
021d7b6 71deae0 00713f3
4644532 5c29806 5fc3cc7 6c32459
```

独立启用条件：Plan 明确选择 Batch Strategy；未选择时继续使用基线计划路径。

验收：单批、跨批依赖、任务上限、原子失败回滚和重复 finalize 幂等测试通过。

### F03：后端执行与验证

能力范围：

- Backend Lane Task Runner。
- 后端 Task Validation 与 Batch Validation。
- Maven/Gradle required 命令、Evidence 绑定和批次完成门禁。
- 验证失败后的整批重验和可恢复 Validation Run。
- 不包含多仓库、Maven 聚合、Windows 和验证延期。

主要来源切片：

```text
db61505 e74c944 2c05bc6 aece01a 013a968
5a8f713 a0e7d88 089fdfe 9785478
```

`a0e7d88` 和 `9785478` 只提取后端部分，禁止带入 UI 分组或 npm 策略。

独立启用条件：存在 Backend Validation Profile；没有 Profile 时 Task Runner 不执行后端验证。

验收：后端成功、编译失败、测试失败、repair、整批重验、进程中断恢复测试通过。

### F04：多仓库与运行恢复

能力范围：

- 多 Repository/Workspace 绑定。
- 跨 Run 文件变更累计。
- 串行批次变更归集和工作区边界校验。
- 原 Run 重试及环境阻断后的恢复。

主要来源：

```text
1cfc2dd 711de72 0a70bc4 ae86668
```

独立启用条件：请求中存在多个 Workspace；单仓库继续走 F03 原路径。

验收：单仓库无回归、双仓库隔离、越界修改拒绝、原 Run 恢复和 Windows/Linux 打包测试通过。

### F05：前端计划与 UI 契约

> 2026-08-03 决定不合入。前端走 `frontend_before_specs` profile 的 `dev.frontend` 节点（PRD + HTML 直接实现），不再引入 `UI_CONTEXT.json` 事实源。`f00`/`f04` 随底座带回的 `hooks/ui_context.py`、`ui_context_writer.py`、`resolve_frontend_html_route.py`、`frontend_route_write_guard.py` 与 `artifact_check.py` 的 `plan_ui_projection` 已一并摘除；`uiRequired` 作为 lane 开关保留。本节及下面的 F06 仅作历史记录。

能力范围：

- Frontend Lane 计划生成。
- `PAGE/UIX/VIS`、UI Context、视觉来源摘要和任务引用。
- UI Task 分组及前后端 Task 拆分。
- 不执行前端实现，不运行 npm 验证。

主要来源切片：

```text
71deae0 a0e7d88 9b0ccec
```

`71deae0`、`a0e7d88` 只提取 Frontend Lane/Task 分组；`9b0ccec` 只提取 UI 数据契约。

独立启用条件：存在 UI Context 且 Plan 选择 Frontend Lane。

验收：纯后端计划不产生前端任务；纯前端和前后端混合计划的 Lane、依赖和 UI 引用正确。

### F06：前端实现与高保真路由

> 2026-08-03 决定不合入，理由同 F05。高保真/标准 HTML 分流由 `/autodev-frontend` 内部的 `route/with-absolute-html`、`route/with-standard-html` 负责，不经 Plan 阶段的 `frontendRoute` 投影。

能力范围：

- 高保真页面归档、内容摘要和任务追踪。
- HTML/视觉来源到前端实现的路由。
- 保留 `release-0727` 的独立 `autodev-frontend` 阶段。
- 不把当前分支的 `autodev-code` 前端路由整体覆盖到基线。

主要来源切片：

```text
9b0ccec
```

独立启用条件：UI Context 存在 required visual source，并明确选择对应前端 Route。

验收：无 UI 需求时不进入前端阶段；标准 HTML、高保真 HTML 和视觉来源缺失降级流程分别通过。

### F07：前端验证与冒烟

能力范围：

- npm/pnpm/yarn build、typecheck、lint 和前端测试命令。
- Frontend Validation Profile 和前端批次收口。
- 移除旧标准冒烟强依赖，保留显式 Smoke Plan。
- 不包含重复命令去重。

主要来源切片：

```text
ac93ecd 9785478
```

独立启用条件：存在 Frontend Validation Profile；未配置时不自动推导 npm 命令。

验收：前端成功、缺少 package script、编译失败、可选命令失败以及无前端项目场景通过。

### F08：后端验证优化

能力范围：

- 收紧代码探索缓存信任边界。
- Validation Cursor 与 Repair Owner 分离。
- 兼容 Maven 定向测试合并为一次物理执行。
- 根据 Surefire/Failsafe 报告拆回各 TASK 的逻辑结果和 Evidence。

主要来源切片：

```text
8d6c91b 8e654bb a46141e
```

`a46141e` 只提取 Maven 聚合，不带入前端 build 去重。

独立启用条件：建议增加 `executionGrouping=compatible_maven`；默认 `off`。

验收：兼容测试合并、不兼容测试隔离、不同 TASK 失败归属和关闭开关后的原执行次数通过。

### F09：前端验证优化

能力范围：

- 对完全相同的前端 build/typecheck/lint 物理命令去重。
- 为每个逻辑 TASK 保留独立 Evidence。
- 不修改 Maven 行为。

主要来源切片：

```text
a46141e
```

独立启用条件：建议增加 `executionGrouping=exact_frontend_compile`；默认 `off`。

验收：相同命令只执行一次、不同 cwd/repo/kind 不合并、关闭开关后恢复原行为。

### F10：验证执行可靠性与跨平台

能力范围：

- 编译失败与真实环境超时分类。
- 实时日志监控和子进程树终止。
- Windows `%COMSPEC%`、`mvn.cmd/npm.cmd` 和文件日志捕获。
- 验证子代理严格交接、后台任务轮询和恢复协议。
- 不改变验证失败后的业务处置策略。

主要来源：

```text
025b06a a203020 90df69d ebc03bc
```

独立启用条件：执行平台自动选择启动方式；子代理协议应保留一个版本的旧协议兼容入口。

验收：Linux/macOS 基础回归、真实 Windows 命令、硬超时、源码/测试编译失败、普通测试输出和子代理恢复通过。

### F11：验证延期

能力范围：

- `deferredValidationIssues`、`validationDisposition` 和 `passed_with_deferred`。
- 环境失败记录 blocked Evidence 后延期并继续。
- 普通验证失败最多 repair 两次，耗尽后延期。
- Task、Batch、Project 三种 Scope 的延期闭环。
- `--adopt-workspace-changes` 采用提前发生且未越界的修复改动。
- UTEST/E2E 消费延期问题；延期不是 PASS，也不能静默丢弃。

来源：当前工作区尚未提交的 12 个文件变更。

本特性内部使用三个技术提交，但只作为一个 Feature 发布：

```text
1. feat(validation-deferral): add additive schema and integrity checks
2. feat(validation-deferral): add runner policy behind failStrategy
3. feat(validation-deferral): hand off deferred issues to utest and e2e
```

独立启用策略：

```json
{
  "taskValidationPolicy": {
    "failStrategy": "fail_fast",
    "maxRepairAttempts": 2
  }
}
```

合入后默认必须保持 `fail_fast`。只有显式配置以下策略才启用延期：

```json
{
  "taskValidationPolicy": {
    "failStrategy": "repair_then_defer",
    "maxRepairAttempts": 2,
    "environmentFailureDisposition": "defer",
    "exhaustedRepairDisposition": "defer"
  }
}
```

验收：

- 未启用时行为与 F10 完全一致，验证失败继续阻断。
- 环境失败产生 blocked Evidence 和 deferred issue，但不伪装为 PASS。
- 两次 repair 后仍失败才延期。
- Task、Batch、Project 延期都能推进到合法终态。
- UTEST/E2E 能读取 issueId、scope、reason、commandId、evidenceIds 和 repairAttempts。
- 单独回滚 F11 后，F01-F10 仍可运行。

## 6. 混合提交处理规则

以下原提交不能整体 cherry-pick，必须按本方案重新形成适配提交：

| 原提交 | 必须拆分到 |
| --- | --- |
| `5e0a4da` | F02 公共 Batch Plan；不得直接启用 Lane |
| `71deae0` | F02 公共分批内核、F05 Frontend Lane |
| `a0e7d88` | F03 通用/后端收口、F05 UI Task 分组 |
| `089fdfe` | F03 后端验证、F04 多 Workspace 上下文 |
| `9b0ccec` | F05 UI 数据契约、F06 高保真路由 |
| `9785478` | F03 后端验证策略、F07 前端验证策略 |
| `a46141e` | F08 Maven 聚合、F09 前端命令去重 |
| `ebc03bc` | F10 Windows/日志/子代理，不带入 F11 延期策略 |
| `06f0bfb` | F03（`task_run_integrity.py`、`task_runner.py` 完整性门禁）、F06（`frontend_route_write_guard.py` 扩展 + `hooks.json` 前端路由注册行）、F08（`code_task_context.py` 探索门禁）、F10（`repository_snapshot.py` unborn HEAD） |

不要保留原提交的混合边界。每个 Feature PR 应重新提交，并在提交说明中记录来源 SHA。

## 7. 分支、版本和回滚

建议分支链：

```text
release-0727
  -> integration/release-0727-feature-platform   # F00
  -> release-next/f01-artifact-evidence
  -> release-next/f02-batch-plan
  -> release-next/f03-backend-execution
  -> release-next/f04-multi-workspace
  -> release-next/f05-frontend-plan
  -> release-next/f06-frontend-route
  -> release-next/f07-frontend-validation
  -> release-next/f08-backend-validation-opt
  -> release-next/f09-frontend-validation-opt
  -> release-next/f10-validation-runtime
  -> release-next/f11-validation-deferral
```

虽然实际合入按顺序执行，每个 Feature PR 都必须保持单独的 merge commit 或 squash commit，不得把多个 Feature PR 再 squash 成一个总提交。

每次发布：

1. 从上一稳定标签创建 Feature 分支。
2. 只移植该 Feature 的代码和测试。
3. 验证关闭开关时与上一版本行为一致。
4. 执行特性测试和全量测试。
5. 打独立标签并发布。
6. 下一特性从该标签继续。

回滚使用 Feature 提交的 revert，不回退公共底座，不用 `reset` 覆盖后续历史。

## 8. 通用发布门禁

每个 Feature 必须执行：

```bash
python -m unittest discover -s tests
```

并满足：

- 无未解释的测试删除或跳过。
- Feature 开关关闭时通过兼容回归。
- Feature 开关开启时通过正向、失败和恢复测试。
- `board_config.json`、Skill 文档、Writer、Runner 和测试表达同一契约。
- 发布产物版本号、Git 标签和 Feature 清单一致。
- 没有依赖尚未合入的函数、字段、模板、Skill 路由或测试夹具。

如果某个 PR 在测试中需要临时复制后续分支代码才能通过，该 PR 不具备独立发布条件，必须退回重新切分。

## 9. 执行期修订（F00 已落地 / F01 进行中）

以下内容来自实际执行，纠正本文初稿的判断。

### 9.0 分支关系不是平行演进，而是「回退 vs 回退前」

本文初稿把 `dev_0803` 与 `dev_workflow_py` 当作从共同祖先平行演进的两条线。实际不是。

merge-base `f52ef45` 之后的**第一个提交** `aafc857`「仅实现agents.md注入」
（单父提交，直接挂在 `f52ef45` 上）删除了 19551 行、25 个 `.py` 文件，
把机器契约层整体剥离：

```text
hooks/plan_json.py              hooks/evidence_store.py
hooks/evidence_integrity_gate.py hooks/ui_context.py
hooks/frontend_route_write_guard.py hooks/resolve_frontend_html_route.py
hooks/run_advisory_smoke.py
tests/test_plan_json_and_evidence.py  tests/test_board_config_invariants.py
tests/test_state_json_source.py        tests/test_artifact_check_id_contracts.py
tests/test_frontend_route_gate.py      tests/test_batched_plan.py 等 16 个测试
```

`aafc857` 在 `release-0727` 上，因此也在 `dev_0803` 上。真实拓扑：

```text
f52ef45  机器契约层存在
├── aafc857  剥离机器契约  -> release-0727 -> dev_0803  (+42 提交，文档驱动)
└── dev_workflow_py                                     (+63 提交，在机器契约上继续建设)
```

且 `dev_0803` 不只是留下空缺，还新建了 `skills/autodev/hooks/plan_execution_check.py`
（324 行）与 `plan_initial_tasks` / `plan_execution_contract`，把 plan 校验
重建成文档驱动形态。

**该剥离已确认为有意的架构选择**（用户裁定）。但目标同样明确：
**全流程通过 JSON 流转，`dev.code` 等阶段读取 JSON 产物**。
因此「适配」不是保留文档驱动为默认，而是把机器契约路径接回来并作为默认，
dev_0803 在 `aafc857` 之后新建的文档驱动实现（`plan_execution_check.py` 等）
在 JSON 路径挂载后让位。

两个推论：

1. §9.4 描述的 board_config 验证器互斥确实是两套都有效的设计之争，
   裁定结果是 **JSON 路径胜出并挂载**。处置见 §9.9。
2. 本文所说的「移植 dev_workflow_py 的能力」，对其中 7 个 hook 模块和 16 个测试文件
   而言，准确表述是**恢复 `aafc857` 删除的文件**。§9.6 把 28 个模块全部称为
   「纯新增」是错的，正确划分见 §9.7。

### 9.1 冲突面比初稿设想的窄

`dev_0803` 的 11 个提交与 `dev_workflow_py` 只在 4 个文件上双向改动：

```text
board_core/board_config.json
board_core/artifacts.py
skills/autodev/hooks/artifact_check.py
hooks/update_checkpoint.py        # 初稿漏列
```

`hooks/` 下 28 个模块（`plan_json` `plan_writer` `evidence_store` `evidence_integrity_gate`
`task_runner` `stage_gate` `validation_policy` `validation_groups` `task_run_integrity` 等）
在 `dev_0803` 完全不存在，是纯新增，无需语义合并。

### 9.2 判断"是否需要语义合并"的正确标准

只看"两侧是否都改过同一文件"会漏项。`hooks/update_checkpoint.py` 上
`dev_workflow_py` 一行未改，但 `dev_0803` **删除**了 `validate_plan_json_for_checkpoint`
并新增了 `needs_fix_from_checkpoint` + FIX_REQUEST 门禁。直接取任一侧都会丢功能。

正确标准是：**任一侧是否删除了对侧仍在依赖的符号。**
F02 之前应按此标准重新审计一遍，不要复用初稿的文件清单。

### 9.3 `artifact_check.py` 的合并基座必须是 `dev_0803`

初稿未指明基座方向。实测两侧同名验证器实现差异很大，`dev_0803` 一侧显著更严：

| 验证器 | dev_0803 | dev_workflow_py |
| --- | --- | --- |
| `validate_specs_contract` | 142 行 | 21 行 |
| `validate_proposal_contract` | 58 行 | 20 行 |
| `validate_plan_finished_tasks` | 43 行 | 21 行 |

以 `dev_workflow_py` 为基座会把这些验证器降级，导致 `test_artifact_contracts` 40 项失败。

正确做法：**`dev_0803`（1039 行）为基座，移植 `dev_workflow_py` 独有的 91 个块**
（20 个常量 + 71 个函数），VALIDATORS 分派表取并集、同名键以 `dev_0803` 为准，
并向 `from common import` 补 `plan_task_blocks` `task_count` `task_statuses`。
合并后 3434 行、29 个验证器，`test_artifact_contracts` 78/78 通过。
（本节记合并当时的实测。该文件与 `plan_execution_check.py` 后续随 D 式 回退整体删除，
`validate_specs_contract` / `validate_proposal_contract` 也回退为 W 式短形态，见 §9.11 与 §9.13。）

推论：F02–F08 无法通过"整份文件取某一侧"来切分，每个特性只能往这份合并文件里
**追加函数与分派表条目**。

### 9.4 board_config.json 的两套验证器无法共存（F01 未决）

`dev.plan` / `dev.code` 上两套验证器互斥，不是合并冲突而是设计冲突：

- `dev_0803`：`plan_initial_tasks` `plan_execution_contract` — 要求 PLAN.md 的
  「任务总览 / 任务详情 / Contract Coverage」结构。
- `dev_workflow_py`：`plan_json_contract` `plan_json_initial_tasks` `plan_ref_resolution`
  — 要求 plan.json 为唯一事实源，且明确不得建立旁路任务队列。

同时注册会让两侧的测试夹具互相判负：JSON-only 夹具触发
`invalid_plan_structure` / `missing_plan_contract_coverage`；切到 JSON 侧则
`dev_0803` 的 `PlanExecutionCheckTest` 失去被测验证器。

三个候选方案：

1. 用 workflow profile / `workflow_decisions` 让两条路径按 profile 并存，
   `dev_0803` 文档流与新 JSON 流各自保留一套 profile。可行性待确认。
2. 承认 F01 是破坏性变更，同时移植 `dev_0803` 的文档型测试到 JSON 夹具。
3. **把 board_config 切换推迟到 F02**（F02 本身必须改 `dev.plan`），
   F01 只交付 hooks + `artifact_check.py` + Writer/Evidence 测试，
   `test_json_writers` 中 3 项 stage_gate 测试作为已知失败留到 F02 转绿。

**结论（已裁定）**：以上三个方案都建立在「可以覆盖 dev_0803 的验证器」这个
前提上，而该前提是错的。正确做法见 §9.9。

### 9.5 测试文件的归属修订

以下测试文件不能在 F01 引入：

| 测试文件 | 归属 | 原因 |
| --- | --- | --- |
| `test_board_config_invariants.py` | F02/F03/F05/F08 | 断言 `dev_workflow_py` 的 board_config 与 SKILL.md 协议 |
| `test_artifact_check_id_contracts.py` | F05 | 主体是 UI Context / plan UI projection |
| `test_state_json_source.py` | F02 | 导入 `dev_0803` 已删除的 `validate_plan_json_for_checkpoint`，需改写到 FIX_REQUEST 门禁 |
| `test_json_writers.py` 中 `..._creates_first_batch` | F02 | 依赖 F02 才提供的 `templates/task-input.json` |

### 9.6 F00 实际交付

分支 `integration/dev_0803-feature-platform`，提交 `17a06d2`：

- 28 个 hook 模块纯新增。
- `board_core/artifacts.py` 语义合并：`GLOB_ARTIFACT_CONTRACTS` 注册表
  （来自 `dev_workflow_py`）+ `resolve_exact_relative_path` 大小写精确校验
  （来自 `dev_0803`）叠加。
- `board_config.json`、`artifact_check.py`、`autodev-code/SKILL.md` 保持 `dev_0803` 原样。
- 验收：255 tests，1 项既有失败（`test_request_user_input_protocol`，
  在 `dev_0803` 上同样失败，与本次合并无关）。

### 9.7 F00 的 28 个模块划分（订正 §9.6）

`17a06d2` 的提交说明把 28 个模块全部称为「纯新增」，这对其中 7 个不准确。
按是否在 merge-base `f52ef45` 上存在划分：

**恢复 `aafc857` 删除的文件（7 个）**

```text
hooks/plan_json.py                  hooks/evidence_store.py
hooks/evidence_integrity_gate.py    hooks/ui_context.py
hooks/frontend_route_write_guard.py hooks/resolve_frontend_html_route.py
hooks/run_advisory_smoke.py
```

**`dev_workflow_py` 真正新增（21 个）**

```text
code_exploration  code_exploration_writer  code_task_context
e2e_result_writer  evidence_audit  evidence_kernel  json_writer_common
plan_granularity  plan_writer  repository_snapshot  result_writer_common
review_findings_writer  smoke_plan_writer  stage_gate  task_run_integrity
task_runner  ui_context_writer  unit_test_result_writer
validation_groups  validation_policy  verify_decision_writer
```

对 F00 的实际交付内容没有影响（两类都需要进入底座），但影响 §9.3 的表述：
`artifact_check.py` 上 `dev_0803` 一侧并非「更严」，而是 `aafc857` 之后
重写的文档驱动版本；`dev_workflow_py` 一侧是机器契约版本。以 `dev_0803`
为基座仍然正确——它包含 `aafc857` 之后 42 个提交的成果，不能丢——但
`VALIDATORS` 同名键以 `dev_0803` 为准这一条，在 board_config 切到 JSON
路径后需要按 stage 重新判断，不能一概而论。

### 9.8 排查方法教训

本次执行中有三次「命令静默失败、把空输出当成结论」：

1. `head -n -1` 在 macOS 上不支持负数行数，导致验证器比对脚本全程失败，
   空输出被读成「实现相同」，据此选错了 `artifact_check.py` 的合并基座。
2. zsh 会在未加引号的 `$A:hooks/...` 中展开 `:h`（dirname 修饰符），
   `git cat-file -e` 全部报 `Not a valid object name .ooks/...` 并落入 else 分支，
   得出「0 个恢复、28 个全新增」的错误结论。
3. 同一原因导致 `git log --diff-filter=D` 查不到删除提交，一度以为
   「文件不在树里但没有删除提交」。

4. zsh 在 ref 与路径拼接处报 `bad substitution`，输出的 0 被当成「模板里没有该段」。
5. `git show > 文件` 产出 0 字节空文件，后续 grep/sed 全部返回空，
   空输出被读成「未命中」，据此得出「dev_0803 的 SKILL.md 残留悬空引用」
   的错误结论，并一度用它来判断 `aafc857` 的性质。

结论：涉及 git ref 拼接一律加引号（`"$A:$f"`）；导出到文件后先用
`wc -c` 确认非空再比对；比对脚本必须先用已知答案的样本自检，再用于判断。

## 10. 加法式适配（§9.9）

### 9.9 board_config 挂载 JSON 路径（已裁定）

目标是全流程 JSON 流转，因此 `dev.plan` / `dev.code` 挂载机器契约验证器：

| stage | validators | artifacts |
| --- | --- | --- |
| `dev.plan` | `design_contract`、`plan_json_contract`、`plan_json_initial_tasks`、`plan_ref_resolution`、`plan_task_granularity`、`plan_scenario_coverage` | outputs 增加 `plan_json` |
| `dev.code` | `plan_json_contract`、`plan_task_detail_schema`、`plan_ref_resolution`、`plan_finished_tasks`、`evidence_detail_quality`、`code_done_gate`、`evidence_integrity` | inputs 的 `plan`(PLAN.md) 换成 `plan_json`；outputs 增加 `evidence_stream` |

`PLAN.md` 保留为人类视图（`dev.plan` outputs 仍含 `plan`），
但不再是 `dev.code` 的机器事实源。

dev_0803 的文档型验证器 `plan_initial_tasks` / `plan_execution_contract`
随之不再挂载。本节初版裁定其实现「保留在树里不删除，以便需要时回退」，
该裁定后续被推翻：spec ID 约定回退到括号式（`REQ-001` / `SCN-001`）后，
`plan_execution_check.py` 依赖的 `REQ_HEADING` / `SCN_HEADING` / `section_text`
（capability 前缀式正则与 `Capability Index` 解析）随 D 式一并删除，
该文件失去可编译的依赖基础。因此 `plan_execution_contract` 的实现
与 `skills/autodev/hooks/plan_execution_check.py` 已从树中删除，
回退路径改为经 git 历史取回。

### 9.10 两条路径无法用现有 profile 机制并存

已确认 `workflow.profiles` 只支持通过 `insertBefore` 做**节点插入**
（见 `frontend_before_specs`），没有节点字段的覆盖机制，
`workflow_compiler.py` 中也没有 profile 级 `validators` 处理。

因此「按 profile 同时保留文档流与 JSON 流」在当前代码里无法表达。
这也意味着 §2 独立特性约束第 3 条（新行为通过显式配置启用）在
`dev.plan` / `dev.code` 的验证器层面做不到 —— 挂载 JSON 路径就是切换，
不是开关。

若日后需要两条路径共存，需新增 profile 级 `validators` 覆盖能力，
属于新设计，不在 F01–F11 移植范围内。

### 9.11 受此影响的测试处置

两套验证器互斥是**对称**的：挂哪一套，另一套经 `board_config` 分派的
测试就失效。实测数字：

| board_config 挂载 | 失效测试 |
| --- | --- |
| JSON 路径 | `PlanExecutionCheckTest` 2 项 |
| 文档路径（基线，当前选择） | `test_json_writers` 的 stage_gate 2 项 |

按裁定挂载 JSON 路径，初版处置为：

- `PlanExecutionCheckTest` 的 `test_plan_postcheck_passes_valid_execution_contract`
  与 `test_plan_postcheck_blocks_dependency_cycle` 标记 skip，
  原因记明 `plan_execution_contract` 不再挂载、依赖环检测由
  `plan_json_contract` 的 `plan_dependency_cycle` 承担。
  选择 skip 而非删除，保留 dev_0803 的实现与历史。
- `test_json_writers` 全部有效，无 skip。
- 两类测试中直调 `plan_check_main` / `detect_cycle` /
  `plan_writer --structure` 的用例都不经 `board_config` 分派，
  两种挂载下均有效，未受影响。

上述 skip 处置已随 §9.9 的裁定推翻而失效。`plan_execution_check.py` 删除后，
`PlanExecutionCheckTest`（22 项）连同 `tests/test_artifact_contracts.py`
整体删除 —— 该文件由 `d7350ab` 随 D 式引入，其余 6 个测试类是此后累积的，
一并随文件移除。依赖环检测仍由 `plan_json_contract` 的
`plan_dependency_cycle` 承担，`test_json_writers` 不受影响。

由此丢失覆盖的四项，无其他测试文件承接，属已知缺口：
`validate_design_contract`、`validate_plan_initial_tasks`、
`validate_plan_finished_tasks`、`find_template_guidance_residue`。

### 9.12 SKILL.md 合并方向

`autodev-plan/SKILL.md` 与 `autodev-code/SKILL.md` 需要与 §9.9 的 board_config
挂载保持一致，即以 **`dev_workflow_py` 的 JSON 流程为主干**：
`plan.json` / `plans/Bxxx/plan.json` 为唯一事实源、批次会话入口
`code-session`、批次探索闸门等段落都要写入。

同时必须保留 dev_0803 在 `aafc857` 之后新增、且与 JSON 流程不冲突的内容。
实测 `autodev-code/SKILL.md` 上 dev_0803 独有的内容有 7 类：

```text
缺失产物处理段（inspect_skill_contract.py）
实现差异协议（EVD/design 与代码现实不符时的裁定流程）
CONTEXT.md 领域词汇表锚点回填
domain-context.md 引用
ui-continuation-guide.md 续办意图引导
task 工具三角色审查（explore-autodev / code-reviewer-autodev /
  code-simplifier-autodev）
modules_compile.json 编译清单
```

其中 `modules_compile.json` 与 JSON 流程的「准入不执行编译、批次质量模式
以 `batchValidation` 为唯一事实源」直接冲突，应舍弃，由 F03 的批次验证取代。
其余 6 类与路径方向无关，逐段嵌入 JSON 主干。

`autodev-plan/SKILL.md` 简单得多：两侧 9 段中只有一段名称不同
（dev_0803「生成 PLAN.md」对应 wpy「生成 plan.json 与批次」），
前 149 行完全相同，取 wpy 侧后补回 dev_0803 的独有段落即可。

### §9.13 两处遗留的取证与处置（已解决）

§9.13 初版把这两项都记为「不单方面裁定的遗留」。补做取证后两项定性都变了，
均已在本分支处置完毕。初版的结论与成本估算作废，以本节为准。

#### 一、`test_request_user_input_protocol` 失败是同一提交内的笔误（已修测试）

初版记为「测试与 AGENTS.md 谁为准的项目策略选择」。取证否定了这个定性：
两者出自**同一个提交** `c846b7b`（fix： 修复plan spec技能改动，2026-07-29）。
该提交同时做了三件事：

```text
1. 从 autodev-specs/SKILL.md 删掉「整体确认门」两步裁定结构
2. 在 AGENTS.md 把「capability 切分 ... 不把切分、命名或规格范围交给用户确认」
   补成「不要把需求或改动目的写入技能」这条反模式的示例
3. 新增 test_specs_only_adjudicates_open_questions，8 条断言
```

8 条断言里 7 条与第 1 项改动严格对应（3 条 forbidden 断言正是在断言「整体确认门
已被删除」）。只有 `不把切分、命名或规格范围交给用户确认` 这一条，其句子
**在 SKILL.md 全部历史中从未出现过**（`git log -S` 该句 → 只命中 AGENTS.md
与测试文件两处）。即作者在同一提交里判定该句属反模式、不写入技能，却把它留在了
断言清单里。

不是策略冲突，是一次提交内的笔误。该断言的语义已由同清单下一条所在行承载：

```text
- 全部条目裁定后直接生成 proposal 与 specs，不再确认 capability 切分或规格范围。
```

处置：删该条断言并注明原因，其余 7 条不动。删后 5 个用例全通过，3 条 forbidden
断言仍在守「整体确认门已删除」这个意图。

#### 二、两套 spec ID 约定并存会静默关闭 scenario 覆盖门（已修实现）

初版只说「真实特性会报 unknown_verify_scenario_ref」。实测发现真正的后果比报错
严重得多，且影响面比初版估的更靠前。

**影响面**：从 board_config 已装配的 18 个校验器做调用图闭包，8 个依赖旧式索引，
最早的断点在 dev.plan 而非 verify：

| 阶段 | 依赖旧式索引的已装配校验器 |
| --- | --- |
| dev.plan | `plan_json_contract`、`plan_scenario_coverage` |
| dev.code | `plan_json_contract` |
| dev.review | `review_findings_json` |
| dev.utest | `unit_test_result_json` |
| dev.e2e | `e2e_result_json`、`fix_request_json` |
| dev.verify | `verify_decision_json`、`fix_request_json` |

而 dev.specs 的 `specs_contract` / `proposal_contract` 用新式。技能与模板
（`autodev-specs/SKILL.md`、`templates/spec.md`）教的也是新式。

**真正的后果是静默放行，不是报错**。`missing_scenario_coverage_rows` 用
`defined_scenarios - seen_scenarios` 判覆盖完整性；索引取不到 ID 时
`defined_scenarios` 为空集，该差集恒空，覆盖门被真空满足。实测：

```text
spec 声明 3 个 Scenario，VERIFY_DECISION 全部数组留空、verdict=pass
  修复前：postcheck 放行 -> verify_done          （覆盖门等于不存在）
  修复后：3 条失败拦下（missing_scenario_coverage_rows
          / missing_verify_scenario_decision / ...）
对照组（旧式 spec，约定本就对齐）：修复前即被同样 3 条拦下
```

即错配不是让流程报错卡住，而是把 verify 的 scenario 覆盖门整个变成空操作。

**初版处置（已被推翻）**：曾取方向 A——让索引同时识别两种标题写法，技能与模板
不动，6 个正则常量并集化 + 1 个抽取 helper `_spec_def_ids()`，涉及
`artifact_check.py`、`hooks/plan_writer.py`、`hooks/plan_granularity.py`
三份重复副本（三份副本是这个缺陷能长期潜伏的原因之一：改一处不会让另两处跟上）。
方向 A 零 fixture 改动、向后完全兼容，但把「两套约定并存」固化成了长期状态。

**最终处置：取方向 B——统一到括号式（`REQ-001` / `SCN-001`），移除 D 式支持。**
并集正则回退为括号式单一形态，三份副本同步；技能与模板改回教括号式，
使「技能教的」与「校验器索引的」收敛到同一种写法。

| 文件 | 改动 |
| --- | --- |
| `skills/autodev/hooks/artifact_check.py` | 4 个正则回退为括号式；删除 D 式孤儿实现 28 处 / 245 行（详见下表） |
| `hooks/plan_writer.py` | `SPEC_SCENARIO_DEF_RE` 副本回退，删 `_spec_def_ids()` |
| `hooks/plan_granularity.py` | `SCN_ID` 副本回退 |
| `autodev-specs/SKILL.md` + `templates/{proposal,spec}.md` | 回退到 `77eb793` 的 W 式基线；版本号 v1.3.1706 → v1.4.1707 |
| `autodev-plan/templates/{design,plan}.md` | 示例行的 D 式 ID 改为括号式 |

`artifact_check.py` 的删除分三层，按不动点分析确定（种子 8 个 + 级联 15 个
+ 已零引用 4 个）：

| 层 | 符号 |
| --- | --- |
| 种子（直接孤儿） | `spec_declared_ids`、`validate_open_questions_rows`、`parse_open_questions`、`parse_capability_index`、`index_placeholder_rows`、`parse_operations_cell`、`malformed_contract_headings`、`validate_plan_execution_contract` |
| 级联（删种子后才成孤儿） | `CAP_ID_HEADER`、`REQ_HEADING`、`VALID_OPERATIONS`、`PENDING_MARKERS`、`RESOLVED_STATUS`、`DEC_ID`、`DEC_HEADING`、`DECISION_FIELD`、`CONSTRAINT_ID`、`PLACEHOLDER_TEXT`、`NORMALIZE_STRIP`、`section_text`、`decision_log_entries`、`is_filled`、`restates_question` |
| 已零引用 | `SCN_HEADING`、`OPERATION_HEADING`、`REQ_CANDIDATE_HEADING`、`SCN_CANDIDATE_HEADING` |

随 D 式 一并移除的还有 `Capability Index` 双射校验、`Decision Log` 证据链
（`3a870bc` 引入）与 `plan_execution_contract` 注册项。`Decision Log` 的
删除是级联后果而非独立裁定：其校验入口 `validate_open_questions_rows`
是 D 式 `约束` ID 的唯一消费者。

**例外：`validate_no_template_guidance` 的两处调用予以保留。** 它来自
`cc73557`（晚于 W 式基线 `77eb793`），防的是模板指导语泄漏进产物，
与 ID 约定无关，对 W 式产物同样成立。逐字回退会连带去掉 proposal / specs
两处保护，因此在 `validate_proposal_contract` 与 `validate_specs_contract`
中显式加回。五个模板经 `find_template_guidance_residue` 复扫均 clean。

**`tests/test_spec_id_convention.py` 相应改写**：由「两种写法都要能索引」
改为钉括号式单一写法，并新增 `test_spec_template_matches_indexer_patterns`
—— 直接断言 `templates/spec.md` 的标题能被 `SPEC_REQUIREMENT_DEF_RE` /
`SPEC_SCENARIO_DEF_RE` 命中。这条是方向 B 的关键守卫：错配的根因正是
「模板教的」与「索引器认的」不是同一种写法，该断言让此类漂移在
模板侧被立刻发现，而不是等到 verify 阶段覆盖门变成空操作。
零覆盖必拦的用例保留。

**方向 B 的取舍**：D 式（`REQ-<capability>-NNN`）有 capability 命名空间和
REQ 归属，语义容量严格更强，这部分表达力是本次回退主动放弃的。换来的是
单一约定、无并存技术债，以及不再需要维护三份并集正则副本。

### §9.14 测试归属表的补充：11 个原表未列的 `dev_workflow_py` 独有测试文件

§ 前面的测试归属表只列了 4 项，实测 `dev_workflow_py` 相对基线共有 15 个独有
测试文件，其中 11 个原表未列。逐个在合入后的 HEAD 上实跑分类：

已随后端线合入（在 HEAD 上直接通过，为已合代码的额外覆盖）：

| 文件 | 用例数 |
| --- | --- |
| `test_advisory_smoke.py` | 5 |
| `test_plan_granularity.py` | 9 |
| `test_inspect_skill_contract_plain.py` | 14 |
| `test_biz_validate_prd.py` | 14 |
| `test_biz_validate_contract_aware.py` | 5 |

`test_biz_validate_contract_aware.py` 依赖 `test_biz_validate_prd.py` 做模块导入，
两者必须同批合入。

延后（依赖未合入的特性，失败原因已定位）：

| 文件 | 失败 | 阻塞原因 / 归属 |
| --- | --- | --- |
| `test_workflow_skip.py` | ImportError | 需要 wpy 版 `update_checkpoint.validate_fix_request_for_needs_fix`；HEAD 保留 dev_0803 的 FIX_REQUEST 门禁实现 |
| `test_skip_node_cli.py` | ImportError | 导入 `tests.test_workflow_skip`，随上一项 |
| `test_state_json_source.py` | ImportError | 需要 dev_0803 已删除的 `validate_plan_json_for_checkpoint`（原表已记，F02） |
| `test_dynamic_workflow.py` | 8 failures | 动态工作流轨道，不属 F00-F11 任一特性 |
| `test_workflow_subset.py` | 6 failures + 1 error | 同上 |
| `test_workflow_next_actions.py` | 25 failures | 同上 |
| `test_skill_artifact_drift.py` | 1 failure | SKILL/产物漂移断言，依赖前端阶段装配 |
| `test_artifact_check_id_contracts.py` | 7 failures | 原表已记，F05（UI Context / plan UI projection） |
| `tests/test_frontend_route_gate.py` | 未引入 | F06 |
| `tests/test_frontend_review_runner.py` | 未引入 | F07 |

`test_workflow_skip.py` / `test_skip_node_cli.py` / `test_dynamic_workflow.py` /
`test_workflow_subset.py` / `test_workflow_next_actions.py` 构成一条原分析完全
遗漏的「动态工作流 / 阶段跳过」轨道（合计约 2200 行测试）。它既不在后端线也不在
前端线，需要单独定特性编号与合入顺序。
