---
name: autodev-utest
description: "Dev 阶段单元测试协调、生成、执行与单测驱动最小修复技能。"
version: v1.2.08311
---

## 插件脚本执行

调用 `${pluginPath}` 下的脚本时，execute/shell 工具请求省略 `cwd` 字段。`${pluginWorkspace}/${projectDir}` 只作为产物路径或脚本的 `--workspace` 参数。仓库根目录与执行目录只使用环境检查器返回值，不作为模型填写的脚本参数。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-utest --feature "${feature}" --plain
```

# /autodev-utest - 单测协调与验证

## 阶段定位

Plan 已固化每个 TASK 的实现范围，Code 只实现生产代码、不创建测试。本阶段使用路由脚本生成的 assignment 内容，生成或补齐单测，运行真实测试命令，归因失败，生成 `UNIT_TEST_REPORT.md` 与 `UNIT_TEST_RESULT.json`。

调用脚本读取当前 Feature 状态：

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
```

每次需要当前状态或 checkpoint 时重新运行该脚本，不得直接读取 `.autobizdevops/state.json`、`.autobizdevops/STATE.md`、`hooks.ndjson` 或 Feature 目录内的 `.plan.lock`。

首次从 `requirements_eval_done` 进入本阶段时写入开始 checkpoint；当前已是 `unit_test_in_progress` 时不重复写入：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint unit_test_in_progress
```

## 输入与产物

测试 assignment 只通过以下脚本生成：

```bash
python "${pluginPath}/hooks/utest_assignment_router.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --json
```

每个 assignment 的 `promptContent` 只包含 Batch plan 的绝对路径，以及 TASK `id`、`implementationPoints`、`nonGoals` 和从 `validationCommands` 提取的 `validationLocations.repo/cwd`。派发时原样使用；不得自行打开 plan 补取、转述或拼接 TASK 字段。Plan 命令的 argv 不作为测试命令。

code 阶段未解决的缺陷会原样留在 plan 里交到本阶段。开工前逐个 Batch 读取并列出：`batchCompile`（`status`、`failureCategory`、`lastFailure`、`repairAttempts` / `maxRepairAttempts`）、`batchValidation.status` 与 `deferredIssues[]`、TASK `blockers[]`、根 `deferredValidationIssues[]`。它们是本阶段必须修复的入场缺陷，与 UT target 并列进入修复队列。

判定现状以本轮重跑该命令的结果为准，不以字段快照为准：`status=passed` 的条目里 `lastFailure` 只是修复过程记录，重跑通过即不再是缺陷；`status` 非 `passed` 或存在未清空的 `blockers` / `deferredIssues` 时，先重跑确认复现，再按失败分类修复。未经重跑不得直接依据字段动生产代码。

其余输入只用于定位与执行，不用于重新推导测试目标：

- 根据系统约束引用，仓库边界与 framework 约束。
- 当前 feature 源码、已有测试、构建清单、锁文件和测试配置。
- `${pluginPath}/skills/autodev/autodev-utest/reference/test-engineer-task-protocol.md`。

输出：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_REPORT.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_RESULT.json`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log`
- 阻断回流时的 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/FIX_REQUEST.json`
- validation Evidence

可修改：

- 测试源码、fixture、mock、测试辅助代码。
- 测试环境配置、依赖 manifest 与对应的单一锁文件。
- 当前 feature 的生产源码，限失败测试锚定的最小范围。

修复超出 assignment 的仓库或 TASK 边界时返回 `source_fix_request`，不跨边界改动。

`UNIT_TEST_RESULT.json`、`evidence/EVIDENCE.jsonl`、`evidence/EVIDENCE.index.json`不得用编辑、`sed`、截断或重写修改它们，不得删除已写入的 target 与 evidence 行。校验不通过时按 `requiredAction` 修正对应 plan、assignment 或测试命令后重跑。

## 执行主体与派发顺序

task 工具可用时，主协调器必须使用 `test-engineer-autodev`：

```bash
python "${pluginPath}/hooks/utest_assignment_router.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --json
```

1. 使用 router 返回的 assignment 顺序。
2. 先串行执行全部 backend assignments，再串行执行全部 frontend assignments；其他 lane 排在其后。
3. 一次只派发一个 assignment；收到完整返回后再派发下一个，不并发跨 Batch 或跨仓库测试。
4. task description 以 router 返回的 `promptContent` 原文开头，再附加 SCOPE/SYSTEM/UNIT 引用、环境检查器原样返回的仓库路径、允许写入边界、失败分类和固定返回字段；不得附加 plan TASK JSON。

task 工具不可用时，不模拟子任务；由主会话按相同 Batch/lane/workspace 顺序执行下述检查、参考渲染、命令记录、归因和报告流程。

进入工作流程前先输出本轮走哪条分支。

## 约束与环境

`<SCOPE>` 决定 deploy unit 与仓库边界。runner、构建工具和包管理器只来自真实 `pom.xml`、Gradle 文件、`package.json`、锁文件和测试配置。

- 系统约束与工程事实冲突：`contract_gap`，阻断 assignment。
- 系统约束未声明 framework：从工程事实回落并记录 warning。
- 已有 Jest/Vitest：原样复用。
- Next/Nuxt 或其他栈：仅复用既有 runner；没有 runner 时为 `environment` 阻断。

一次检查当前 Feature 的全部 assignment：

```bash
python "${pluginPath}/hooks/inspect_test_environment.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --record-blocked --json
```

检查器调用 workspace binding 解析器，根据当前 plan 与已验证的 Code 产物自动保存仓库绑定，再用 `validationCommands.cwd` 定位构建根目录；`scope.modules` 仅用于精确定位，无法解析时按检查器返回的 `locationWarnings` 回退 `validationLocations`。模型不得填写 repo、仓库地址、framework 或 cwd，不得直接编辑 `.autobizdevops/workspace-bindings.json`。

按检查器 `status` 分支：

- `ready`：继续测试计划。
- `init_required`：应用对应 profile；完成环境变更后重跑一次检查器。
- `workspace_binding_ambiguous`：向用户展示 `candidates`；收到用户选择后只传回候选 ID，再重跑一次检查器。
- `contract_gap`、`workspace_binding_missing`、`workspace_binding_invalid`、`conflict`、`unsupported`、`environment_inspection_failed`：确认返回包含 `blockedArtifacts` 且不含 `blockedArtifactError`，推进 `needs_fix` 后立即停止：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint needs_fix
```

阻断产物写入失败时原样报告 `blockedArtifactError` 并停止，不更新 checkpoint。没有环境变更或用户选择时不重跑检查器。`contract_gap` 只用于 plan/TASK 契约损坏及 `workspaceRef`、`validationCommands.repo/cwd` 的非法或不一致；`scope.modules` 无法解析不是 `contract_gap`。

`workspace_binding_ambiguous` 时，用户选定后运行：

```bash
python "${pluginPath}/hooks/utest_workspace_binding.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --workspace-ref "<RETURNED_WORKSPACE_REF>" --select-candidate "<USER_SELECTED_CANDIDATE_ID>" --json
```

不得由模型选择 candidate，也不得把候选路径改写成参数。已有 persisted binding 或当前没有 pending ambiguity 时不得传 `--select-candidate`。

`status=init_required` 时读取并应用：

```text
${pluginPath}/skills/autodev/autodev-utest/reference/test-environment-profiles.md
```

环境初始化只修改测试配置、manifest 与对应锁文件。按 profile 完成改动后用 `--kind setup` 执行安装或校验命令，再重新运行 inspector 并输出返回的 `status`；`status` 未变为 `ready` 前不进入测试生成与执行，不得用一条自拟的 `--kind setup` 命令通过环境检查。`conflict` / `unsupported` 不做猜测性初始化；网络或安装授权被拒绝时分类为 `environment` 并阻断。

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

### 展开 assignment 的测试计划

每个 TASK 建立一个 UT target，`validationLocations` 只确认 repo/cwd。

```markdown
| ID | Task | Test Focus | Priority | Status |
|----|------|------------|----------|--------|
| UT-001 | B001/T003 | implementationPoints 原文 | P0 | planned |
```

- `implementationPoints` 是必须覆盖的测试重点，`nonGoals` 不生成测试。
- 需要真实浏览器、多页面导航或真实网络链路的重点只写入 `e2e_handoff`。
- 每个 TASK 的 target 为 P0；AC 与 spec 覆盖由 runner 从当前 plan 绑定。
- `deferredValidationIssues[]`：`scope=task` 且能映射到单元边界的并入对应 TASK 的 UT target；batch/project 项进入扩大验证。
- plan 之外的补充测试记 P2，并写明补充理由；不得用它替代任何 plan 目标。
- `promptContent` 缺 Batch plan 绝对路径、`implementationPoints`、`nonGoals` 或有效 `validationLocations` 时记 `contract_gap`。

完整表输出后才进入生成测试与执行。

### 生成测试

每个 UT target 都先落地测试文件，再执行为该测试生成的精确命令；没有落地测试文件的 target 不执行、不记 PASS。

每个 UT target：

1. 读取同仓库最邻近的 2 至 3 个测试，匹配 runner、命名和 setup/teardown。
2. 断言覆盖 `implementationPoints`，不覆盖 `nonGoals`。
3. 在公开 seam 上写一个行为目标；按 `${pluginPath}/skills/references/test-quality.md` 选择 mock 边界。
4. 后实现测试使用 `post_implementation=true`、`tdd_rebuild=false`；首次即通过记 `characterization_pass`，不得删除现有生产实现制造 red。
5. 测试自身错误由测试工程师修复并重跑同一精确目标。

### 执行与证据

setup 命令：

```bash
python "${pluginPath}/hooks/run_utest_command.py" --kind setup --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --task-id "<TASK_ID>" -- <argv...>
```

test 命令提交 assignment 绑定、生成的测试文件与真实测试 argv：

```bash
python "${pluginPath}/hooks/run_utest_command.py" --kind test --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --task-id "<TASK_ID>" --test-file "<RELATIVE_TEST_FILE>" -- <TEST_ARGV...>
```

`--task-id` 取 `promptContent`。`<TEST_ARGV...>` 根据真实 manifest、测试配置与新建测试文件生成，必须实际执行测试。runner 根据当前 plan、绑定、TASK 与测试文件自动选择仓库和构建目录，生成稳定 digest、commandId/targetId，并校验位置、specRefs 与全部 AC。完整输出追加到 `test-output.log`；重跑保留历史 evidence IDs。

### 失败分类与修复

| 类型 | 处置 |
|------|------|
| `test_bug` | 测试工程师只修测试、fixture、mock、辅助代码或测试环境配置，重跑原命令 |
| `source_bug` | 在失败测试锚定下做最小生产修复并重跑原命令；超出 assignment 边界时返回 `source_fix_request` |
| `contract_gap` | 使用检查器落 BLOCKED 结果与 `FIX_REQUEST.json`，推进 `needs_fix` 后停止 |
| `environment` | 只应用受支持 initProfile；无法继续时落 BLOCKED 结果并推进 `needs_fix` |
| `flaky` | 记录多次重跑结果与根因 |
| `unknown` | 停止，不猜修 |

任何生产修复或 `source_fix_request` 前，先校验失败锚点：

```bash
python "${pluginPath}/hooks/validate_utest_source_bug.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --task-id "<TASK_ID>" --command-id "<GENERATED_COMMAND_ID>" --target-id "<UT_ID>" --task-digest "<RUNNER_RETURNED_TASK_DIGEST>" --evidence-id "<EVIDENCE_ID>"
```

静态观察、未执行测试或 exit 0 不得分类为 `source_bug`。

1. 确认失败测试稳定复现，并通过 source-bug validator 校验当前 plan 覆盖。
2. 排除测试、命令与环境问题。
3. 只改让该失败测试通过所必需的最小范围。

主协调器收到 `source_fix_request` 后按同样三步处理，再重新派发原 Batch/lane/workspace assignment，复用 UT target 并追加新 Evidence。

同一个生产根因最多修复 3 次；仍失败时保留 `unit_test_in_progress` 并记录阻断。

### 扩大验证

所有 P0/P1 精确目标通过后，按 assignment 顺序重跑修改过的测试文件、受影响模块轻量测试，以及项目约定的编译/测试编译命令。

扩大验证命令用 `--kind setup` 执行，不带 `--target-id`、`--task-id`、`--spec-ref`，不产生 UT target；命令、退出码与结论写入 `UNIT_TEST_REPORT.md` 的 `Execution Summary`，完整输出在 `test-output.log`。扩大验证失败必须归因，不得用精确测试通过覆盖失败。

### 生成结果与报告

主协调器聚合所有 assignment 返回，生成 `UNIT_TEST_REPORT.md`：

```markdown
# Unit Test Report

- **Feature:** {slug}
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

报告落盘后，由 writer 从当前 TASK 契约与 Evidence 派生 coverage、target result 和 verdict，并校验稳定 JSON 契约：

```bash
python "${pluginPath}/hooks/unit_test_result_writer.py" derive-scenario-coverage --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
python "${pluginPath}/hooks/unit_test_result_writer.py" validate --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --structure --gate
```

## 分支决策

推进 `unit_test_done`：

- 全部 P0 UT target PASS。
- 入场缺陷已全部清零，每项都有重跑通过的 Evidence。
- 报告、日志、结果 JSON、Evidence 完整且 writer 校验通过。
- 源码修复均有失败测试锚点和重跑通过 Evidence。
- 扩大验证已执行并记录。

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint unit_test_done
```

`test_bug`、`source_bug`、`flaky`、未归因失败或超过修复次数时保持 `unit_test_in_progress`。检查器已落阻断产物的 `contract_gap` / `environment` 推进 `needs_fix`；按 `FIX_REQUEST.json` 执行回流，不直接调用 `/autodev-plan` 修改 finalized Plan。

## 质量规则

1. 测试验证业务行为并站在公开 seam 上；实现耦合、同义反复与水平切片判定见 `${pluginPath}/skills/references/test-quality.md`。
2. 不以覆盖率数字替代断言质量，不生成无意义测试。
3. 不删除、跳过或弱化已有失败测试。
4. mock 调用次数不得作为唯一业务断言，除非调用本身就是契约。
5. 不向生产代码添加测试专用入口、开关或伪实现。
6. 每个修复结论必须有新鲜命令与退出码 Evidence。
7. 根因不确定时停止并记录。

## 输出清单

- [ ] 已读取 SCOPE/SYSTEM/UNIT、router 最小 `promptContent` 和工程事实。
- [ ] 已写入 `unit_test_in_progress`。
- [ ] 已声明执行主体分支。
- [ ] 已列出 code 阶段遗留的入场缺陷，并全部重跑清零。
- [ ] 已输出完整 UT target 表，覆盖全部 TASK 测试重点；浏览器目标已转 `e2e_handoff`。
- [ ] 已按 Batch、lane、workspace 串行完成 assignment。
- [ ] 已检查测试环境；`ready` 才进入测试，阻断时已落 BLOCKED 产物并推进 `needs_fix`。
- [ ] 每个 UT target 均已落地测试文件，并通过 runner 记录命令、日志、Evidence 与 target。
- [ ] 失败已分类，修复与 `source_fix_request` 已闭环。
- [ ] 已执行扩大验证并生成 `UNIT_TEST_REPORT.md`。
- [ ] 已派生 coverage、设置 verdict、校验 `UNIT_TEST_RESULT.json`。
- [ ] 成功时已推进 `unit_test_done`。

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
