---
name: autodev-utest
description: "Dev 阶段单元测试协调、生成、执行与单测驱动最小修复技能。"
version: v1.2.08121
---

## 插件脚本执行

调用 `${pluginPath}` 下的脚本时，execute/shell 工具请求省略 `cwd` 字段。`${pluginWorkspace}/${projectDir}` 只作为产物路径或脚本的 `--workspace` 参数；`run_utest_command.py --cwd` 只表示 `<BUSINESS_REPO>` 内的相对执行目录。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-utest --feature "${feature}" --plain
```

# /autodev-utest - 单测协调与验证

## 阶段定位

Plan 已把每个 TASK 的测试边界和测试内容固化进 plan.json，Code 只实现生产代码、不创建测试。本阶段按 plan.json 展开 UT target，生成或补齐单测，运行真实命令，归因失败，生成 `UNIT_TEST_REPORT.md` 与 `UNIT_TEST_RESULT.json`；不从 `proposal.md`、`specs/**/*.md`、`design.md` 重新推导测试目标。

调用脚本读取当前 Feature 状态：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

每次需要当前状态或 checkpoint 时重新运行该脚本，不得直接读取 `.autobizdevops/state.json`、`.autobizdevops/STATE.md`、`hooks.ndjson` 或 Feature 目录内的 `.plan.lock`。

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mode auto` | 是 | 测试工程师修复测试自身问题；生产缺陷由主协调器按门槛修复 |
| `--mode test` | 否 | 只修改测试与测试环境 |
| `--mode code` | 否 | 主协调器只在已有失败测试锚定下修当前 feature 生产代码 |
| `--no-fix` | 否 | 只生成、运行、记录 |
| `--max-fix N` | `3` | 单个生产根因最多修复尝试次数 |

## 输入与产物

```text
FEATURE_DIR = ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}
```

测试内容与边界的事实源只有 plan：

- 根 `plan.json`：`batches[]`、`taskValidationPolicy`、`deferredValidationIssues[]`。
- `plans/Bxxx/plan.json`：`executionLane`，以及每个 TASK 的 `id`、`workspaceRef`、`validationBoundary`、`nonGoals`、`acceptanceCriteria[]`、`validationCommands[]`（`id`、`kind`、`argv`、`cwd`、`repo`、`required`、`covers`）、`validationTestPlan[]`（`commandId`、`assetType`、`executionStage`、`covers`、`testIntent.behavior`、`testIntent.acceptanceCriteria`）。

其余输入只用于定位与执行，不用于重新推导测试目标：

- `<AGENTS_INSTRUCTIONS>` 中的 `<SCOPE>`、`<SYSTEM>`、`<UNIT>` 引用及其真实文档，决定仓库边界与 framework 约束。
- TASK `specRefs` / `designRefs` 指向的锚点：只有 `testIntent.behavior` 与 `acceptanceCriteria` 不足以确定断言时才定点读取该锚点，不通读 specs/design。
- 当前 feature 源码、已有测试、构建清单、锁文件和测试配置。
- `${pluginPath}/skills/autodev/autodev-utest/reference/test-engineer-task-protocol.md`。

输出：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_REPORT.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_RESULT.json`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log`
- validation Evidence

测试工程师只允许修改：

- 测试源码、fixture、mock、测试辅助代码。
- 测试环境配置、依赖 manifest 与对应的单一锁文件。

测试工程师不得修改生产源码。生产缺陷返回 `source_fix_request`。

## 执行主体与派发顺序

task 工具可用时，主协调器必须使用 `test-engineer-autodev`：

```bash
python "${pluginPath}/hooks/utest_assignment_router.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --json
```

1. 按根 `plan.json.batches[]` 原顺序建立 `(executionLane, workspaceRef)` assignment，并保留各 lane 内的 Batch 顺序。
2. 先串行执行全部 backend assignments，再串行执行全部 frontend assignments；其他 lane 排在其后。
3. 一次只派发一个 assignment；收到完整返回后再派发下一个，不并发跨 Batch 或跨仓库测试。
4. prompt 包含 SCOPE/SYSTEM/UNIT 引用、Batch/TASK ID、lane、workspace、解析后的仓库路径、允许写入边界、`post_implementation=true`、`tdd_rebuild=false`、失败分类和固定返回字段，以及本 assignment 每个 TASK 的 `validationBoundary`、`acceptanceCriteria`、`validationCommands` 和 `validationTestPlan` 原文；不得只给 TASK 标题让子代理自行推导测试内容。
5. 收集 `status`、`assignment`、`constraint_files`、`lane`、`framework`、`runner`、`environment_initialization`、`test_targets`、`command_results`、`evidence_ids`、`failure_classification`、`source_fix_request`、`e2e_handoff`、`warnings`。

task 工具不可用时，不模拟子任务；由主会话按相同 Batch/lane/workspace 顺序执行下述检查、参考渲染、命令记录、归因和报告流程。

## 约束与环境

`<SCOPE>` 决定 deploy unit 与仓库边界。runner、构建工具和包管理器只来自真实 `pom.xml`、Gradle 文件、`package.json`、锁文件和测试配置。

- 系统约束与工程事实冲突：`contract_gap`，阻断 assignment。
- 系统约束未声明 framework：从工程事实回落并记录 warning。
- 已有 Jest/Vitest：原样复用。
- Next/Nuxt 或其他栈：仅复用既有 runner；没有 runner 时为 `environment` 阻断。

对每个 assignment 运行只读检查：

```bash
python "${pluginPath}/hooks/inspect_test_environment.py" --framework <spring|vue|react> --workspace "<BUSINESS_REPO>" --json
```

`status=init_required` 时读取并应用：

```text
${pluginPath}/skills/autodev/autodev-utest/reference/test-environment-profiles.md
```

环境初始化只修改测试配置、manifest 与对应锁文件。初始化命令使用 `--kind setup`，随后重新运行 inspector。`conflict` / `unsupported` 不做猜测性初始化；网络或安装授权被拒绝时分类为 `environment` 并阻断。

## 测试域路由

Spring Boot 2/3 按目标选择 `fundamentals`、`mvc`、`security`、`websocket`、`persistence`：

```bash
python "${pluginPath}/hooks/render_spring_test_reference.py" --domain <domain>
```

Vue3/React 按目标选择 `fundamentals`、`component`、`logic`、`state`、`integration`：

```bash
python "${pluginPath}/hooks/render_frontend_test_reference.py" --framework <vue|react> --domain <domain>
```

路由：

| 被测目标 | 测试域 |
|---|---|
| 纯函数、工具函数 | `unit` / 前端 `fundamentals` |
| hook、composable | `logic` |
| store | `state` |
| 组件 | `component` |
| router、page、API adapter | `integration` |

真实浏览器、多页面导航或真实网络链路只写入 `e2e_handoff`，不生成 Playwright/Cypress。

## 工作流程

### 写入开始 checkpoint

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint unit_test_in_progress
```

### 展开 plan 的测试计划

UT target 由 plan 展开，不做二次推导：assignment 内每个 TASK 的每条 `validationTestPlan[]` 展开成一个 UT target，`commandId` 指向该 TASK `validationCommands[]` 中要执行的命令。

```markdown
| ID | Task | Command | Behavior | Boundary | Covers | Priority | Status |
|----|------|---------|----------|----------|--------|----------|--------|
| UT-001 | B001/T003 | VAL-T003-001 | testIntent.behavior 原文 | validationBoundary | AC-001,AC-002 | P0 | planned |
```

- `Behavior` 取 `testIntent.behavior`，`Boundary` 取 TASK `validationBoundary`，`Covers` 取该条 `covers` 指向的 `acceptanceCriteria`。
- `assetType=unit_test`、`integration_test`：本阶段生成并执行。`kind` 为 `build`/`compile`/`typecheck` 的前端命令是工程门禁，按原 argv 执行并记 Evidence，不写测试文件。
- `assetType=e2e_test`，以及需要真实浏览器、多页面导航或真实网络链路的目标：只写入 `e2e_handoff`，本阶段不生成、不执行。
- `required=true` 记 P0，`required=false` 记 P1。plan 契约保证 required 命令覆盖全部 `acceptanceCriteria`，因此 P0 全通过即 AC 全覆盖。
- `deferredValidationIssues[]`：`scope=task` 且能映射到单元边界的并入对应 TASK 的 UT target；batch/project 项进入扩大验证。
- plan 之外的补充测试记 P2，并写明补充理由；不得用它替代任何 plan 目标。
- TASK 缺 `validationCommands`、`validationTestPlan` 为空或 `commandId` 悬空：`contract_gap` 阻断并回流 `/autodev-plan`，不得自拟测试目标顶替。

### 生成测试

每个 UT target：

1. 读取同仓库最邻近的 2 至 3 个测试，匹配 runner、命名和 setup/teardown。
2. 断言只覆盖 `testIntent.behavior` 与 `covers` 指向的 `acceptanceCriteria`；不覆盖 `nonGoals`，不为凑数扩展计划外行为。行为细节不足时才按该 TASK `specRefs` / `designRefs` 定点读取锚点。
3. 在公开 seam 上写一个行为目标；按 `${pluginPath}/skills/references/test-quality.md` 选择 mock 边界。
4. 后实现测试使用 `post_implementation=true`、`tdd_rebuild=false`；首次即通过记 `characterization_pass`，不得删除现有生产实现制造 red。
5. 测试自身错误由测试工程师修复并重跑同一精确目标。

### 执行与证据

setup 命令：

```bash
python "${pluginPath}/hooks/run_utest_command.py" --kind setup --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --code-workspace "<BUSINESS_REPO>" --cwd "<RELATIVE_CWD>" -- <argv...>
```

test 命令；首次执行可省略 `--target-id` 自动分配，重跑必须复用返回的 ID：

```bash
python "${pluginPath}/hooks/run_utest_command.py" --kind test --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --code-workspace "<BUSINESS_REPO>" --cwd "<RELATIVE_CWD>" --target-id "<UT_ID>" --task-id "<TASK_ID>" --spec-ref "<SPEC_REF>" -- <argv...>
```

`argv`、`--cwd` 与仓库取该 UT target 对应 `validationCommands` 条目的 `argv`、`cwd`、`repo`，`--task-id` 取 TASK `id`，`--spec-ref` 取 TASK `specRefs`；plan 命令跑不通时按失败分类处理，不得改写成自拟命令。执行器必须接收 argv，不接收 shell 命令字符串；cwd 不得越出分配仓库。完整输出追加到 `test-output.log`。test 执行追加 `autodev-utest` validation Evidence，并创建或更新同一 UT target；重跑保留历史 evidence IDs。

### 失败分类与修复

| 类型 | 处置 |
|------|------|
| `test_bug` | 测试工程师只修测试、fixture、mock、辅助代码或测试环境配置，重跑原命令 |
| `source_bug` | 返回 `source_fix_request`，测试工程师不改生产源码 |
| `contract_gap` | 阻断并回流约束/计划 |
| `environment` | 记录环境与复现命令；只应用受支持 initProfile |
| `flaky` | 记录多次重跑结果与根因 |
| `unknown` | 停止，不猜修 |

主协调器收到 `source_fix_request` 后：

1. 确认失败测试稳定复现并映射该 TASK 的 `acceptanceCriteria` 与 `validationBoundary`。
2. 排除测试、命令与环境问题。
3. 在 `--mode auto|code` 且未超过 `--max-fix` 时做最小生产修复；`--mode test` 或 `--no-fix` 不修生产代码。
4. 重新派发原 Batch/lane/workspace assignment，复用 UT target 并追加新 Evidence。

达到 `--max-fix` 仍失败，保留 `unit_test_in_progress` 并记录阻断。

### 扩大验证

所有 P0/P1 精确目标通过后，按 assignment 顺序重跑修改过的测试文件、受影响模块轻量测试，以及项目约定的编译/测试编译命令。扩大验证失败必须归因，不得用精确测试通过覆盖失败。

### 生成结果与报告

主协调器聚合所有 assignment 返回，生成 `UNIT_TEST_REPORT.md`：

```markdown
# Unit Test Report

- **Feature:** {slug}
- **Mode:** auto / test / code / no-fix
- **Generated At:** YYYY-MM-DD HH:MM:SS
- **Verdict:** PASS / PASS_WITH_WARNINGS / FAIL / BLOCKED
- **Test Log:** test-output.log

## Test Plan
## Execution Summary
## Coverage Matrix
## Failure Analysis
## Fix Attempts
## Commands
## Handoff
```

`Coverage Matrix` 按 UT target 映射 `covers` 的 `acceptanceCriteria` 与 Evidence，scenario 级覆盖由 writer 从 Evidence 派生，不手工填；`Fix Attempts` 记录分类、修改文件、假设、命令和结果；`Handoff` 汇总 `e2e_handoff`、人工确认项和阻断。

由 writer 派生 coverage、设置 verdict 并校验稳定 JSON 契约：

```bash
python "${pluginPath}/hooks/unit_test_result_writer.py" derive-scenario-coverage --feature "${feature}"
python "${pluginPath}/hooks/unit_test_result_writer.py" set-verdict --feature "${feature}" <PASS|PASS_WITH_WARNINGS|FAIL|BLOCKED>
python "${pluginPath}/hooks/unit_test_result_writer.py" validate --feature "${feature}" --structure --gate
```

## 分支决策

推进 `unit_test_done`：

- 全部 required 命令对应的 P0 目标 PASS。
- P1 PASS，或有明确原因并标记 `PASS_WITH_WARNINGS`。
- 报告、日志、结果 JSON、Evidence 完整且 writer 校验通过。
- 源码修复均有失败测试锚点和重跑通过 Evidence。
- 扩大验证已执行并记录。

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint unit_test_done
```

存在 FAIL、BLOCKED、未归因失败、`contract_gap` 或超过修复次数时保持 `unit_test_in_progress`。

## 质量规则

1. 测试验证业务行为并站在公开 seam 上；实现耦合、同义反复与水平切片判定见 `${pluginPath}/skills/references/test-quality.md`。
2. 不以覆盖率数字替代断言质量，不生成无意义测试。
3. 不删除、跳过或弱化已有失败测试。
4. mock 调用次数不得作为唯一业务断言，除非调用本身就是契约。
5. 不向生产代码添加测试专用入口、开关或伪实现。
6. 每个修复结论必须有新鲜命令与退出码 Evidence。
7. 根因不确定时停止并记录。

## 输出清单

- [ ] 已读取 SCOPE/SYSTEM/UNIT、根/Batch 计划和工程事实。
- [ ] 已写入 `unit_test_in_progress`。
- [ ] 已按 `validationTestPlan` 展开 UT target，覆盖全部 required 命令，`e2e_test` 已转 `e2e_handoff`。
- [ ] 已按 Batch、lane、workspace 串行完成 assignment。
- [ ] 已检查/初始化测试环境并渲染专项参考。
- [ ] 已生成测试并通过 runner 记录命令、日志、Evidence 与 UT target。
- [ ] 失败已分类，测试自修与 `source_fix_request` 已闭环。
- [ ] 已执行扩大验证并生成报告。
- [ ] 已派生 coverage、设置 verdict、校验 `UNIT_TEST_RESULT.json`。
- [ ] 成功时已推进 `unit_test_done`。

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
