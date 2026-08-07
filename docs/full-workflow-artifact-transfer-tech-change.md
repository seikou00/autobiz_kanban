# 全流程文件传递与流转技改

## 特性概述

本次技改统一管理一个 Feature 从需求澄清到归档的文件产物、状态和流转关系。每个 Feature 使用独立目录承载过程文件：

```text
<pluginWorkspace>/<projectDir>/.autobizdevops/features/<feature>/
```

流程以 `state.json` 中的 checkpoint 作为唯一状态事实源，`STATE.md` 仅为自动生成的展示视图。阶段只能在上游必需产物通过校验后推进，所有文件均以 Feature 目录内的相对路径引用，避免依赖会话上下文或人工记忆。

完整主线为：

```text
需求澄清 -> PRD -> Specs -> 设计与计划 -> Code -> 评审 -> 单测 -> E2E -> 验收 -> CI/CD -> 归档
```

其中，前端实现和详细设计为按需启用节点；Code 阶段按 Plan 自动拆分为多个 Batch，并通过跨对话交接逐批执行。

## 业务价值

- 将需求、设计、计划、代码证据和验收结论串成可追溯链路，任何阶段均可定位其输入来源和输出结果。
- 用结构化产物替代口头交接：`plan.json`、批次计划、证据流和验收决策可被脚本校验和机器消费。
- Code 按 Batch 分批收口，限制单次执行范围；每批完成后自动交接下一批，减少跨批次上下文混杂和未验证代码累积。
- 验证结果通过 Evidence 与评审、单测、E2E、验收文件关联，失败和延期均保留事实记录，避免将未通过项误标为完成。
- Feature 归档时整体保留过程产物，支持后续问题追溯、审计、复盘和同类需求复用。

## 功能性描述

### 1. 状态与目录管理

工作区级 `.autobizdevops/state.json` 保存 Feature 的 checkpoint、流程模板、可选节点决策和归档迭代号。阶段开始或恢复时读取状态，阶段完成后通过统一 checkpoint 更新脚本推进状态；不允许手工修改 `state.json`、`STATE.md` 或计划运行时字段。

所有正式过程产物写入 Feature 目录。业务代码、测试和配置写入 Plan 中声明的代码工作区；Feature 目录只保存计划、证据、报告和运行快照，不复制业务工程代码。

### 2. 全流程文件传递

| 阶段 | 上游输入 | 本阶段输出 | 流转规则 |
| --- | --- | --- | --- |
| 需求澄清 | 原始需求材料、领域知识 | `prd_original/**`、`PRD_DISCUSS.md`、`IMPLEMENTATION_SCOPE.json`、条件产物 `SCOPE_SPLIT.md` | 固化讨论结论和本期实现范围，完成后进入 PRD。 |
| PRD | `PRD_DISCUSS.md`、实现范围契约 | `PRD.md` | 消解待确认项后形成正式需求，作为 Specs 的唯一需求输入。 |
| 前端实现（可选） | `PRD.md`、HTML/设计材料 | 前端代码工作区中的工程文件 | 仅在选择前端预处理路径时执行，无独立 Feature 文档。 |
| 行为规格 | `PRD.md`、实现范围契约 | `proposal.md`、`specs/**/*.md` | 输出能力边界、需求和场景，是设计、计划和后续验证的行为依据。 |
| 技术设计与计划 | `proposal.md`、`specs/**/*.md` | `design.md`、`plan.json`、`PLAN.md`、`plans/**/plan.json` | `plan.json` 保存 Feature 级 Batch 索引；每个 `plans/**/plan.json` 保存该批 TASK、验证和运行状态。 |
| 详细设计（可选） | `proposal.md`、`specs/**/*.md`、`design.md`、`plan.json` | `DETAIL_DESIGN.md` | 由用户在 Plan 后选择启用，作为 Code 阶段的补充实现依据。 |
| Code | 当前 Batch 计划、规格、设计、代码工作区 | 业务代码与测试、`evidence/**`、`.task-runs/**`、`cache/code-exploration/**/*.json` | 每次只执行 `activeBatchId` 中的 TASK；实现证据、验证日志和运行快照由 Hook 写入 Feature 目录。 |
| 需求实现评审 | 规格、设计、计划、证据流 | `REVIEW_FINDINGS.json`、可选 `REQUIREMENTS_EVAL.md` | 对照需求与实际改动产出结构化评审结论。 |
| 单元测试 | 评审结论、计划、证据流 | `UNIT_TEST_RESULT.json`、`test-output.log`、可选 `UNIT_TEST_REPORT.md` | 记录单测结果并向证据流追加执行事实。 |
| E2E 测试 | 单测结果、评审结论、规格与计划 | `E2E_TEST_CASES.yaml`、`E2E_RESULT.json`、`e2e-run.log`、可选 `E2E_REPORT.md`、`FIX_REQUEST.json` | 覆盖端到端场景；发现需回流问题时生成修复请求。 |
| 验收汇总 | 评审、单测、E2E 结果及证据 | `VERIFY_DECISION.json`、可选 `VERIFY_REPORT.md`、`FIX_REQUEST.json`、`FEATURE_API_DETAIL.md` | 以场景和证据为单位汇总结论，决定进入 CI/CD 或回流修复。 |
| CI/CD | 验收决策 | `CICD_CHECKLIST.md`、可选 `PR_BODY.md` | 记录流水线、发布和 PR 信息，完成后允许归档。 |
| 归档 | 完整 Feature 过程目录 | `.autobizdevops/archive/<feature>-iter<N>/**` | 将整个 Feature 目录移动至带迭代号的归档目录，保留全部过程产物。 |

除阶段文件外，系统同步会在 Feature 目录维护 `ARTIFACT_CATALOG.json`、`sync-status.json` 和 `hooks.ndjson`，分别用于产物目录、同步状态和 Hook 审计；这些为系统运行文件，不作为业务交付物。

### 3. Code 分批流转

Plan 阶段根据规格、执行通道和代码工作区生成 Batch。一个 Batch 只包含同一执行通道、同一代码工作区的 TASK；后端 Batch 在前端 Batch 前执行，且每批最多五个 TASK。

1. Code 会话入口读取 `plan.json` 的 `activeBatchId`，只加载当前 `plans/**/plan.json`，不得手工指定或切换 Batch。
2. 当前 Batch 内的 TASK 按依赖顺序逐个启动、实现和收口。每个 TASK 先写实现 Evidence，状态由 `in_progress` 变为 `implemented`。
3. 当前 Batch 的所有 TASK 实现完成后，验证子流程统一执行任务级验证和 Batch 级编译、构建、类型检查或 Lint，并将结果写入 `evidence/EVIDENCE.jsonl`、`evidence/EVIDENCE.index.json` 和 `evidence/**` 日志。
4. 验证通过或按延期策略记录后，Batch 才能完成。非末批会生成 `BATCH_HANDOFF.json` 并要求结束当前 Claw 对话。
5. 在新 Claw 对话中再次进入 `/autodev-code`，系统消费 `BATCH_HANDOFF.json`，自动激活下一批；禁止手工调用 `activate-batch` 或编辑批次计划。
6. 全部 Batch 完成后，如 Plan 配置了跨批次项目验证，则执行项目级验证；随后通过 Code 门禁推进到后续评审、测试和验收阶段。

该机制确保文件、状态和证据始终围绕同一个 Feature 与 Batch 流转，任何中断均可从状态、批次计划和运行快照恢复，而不会丢失已完成的过程记录。
