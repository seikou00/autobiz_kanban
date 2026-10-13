---
name: autodev-utest
description: "Dev 阶段单元测试协调、生成、执行与单测驱动最小修复技能。"
version: v1.2.08311
---

## 插件脚本执行

调用 `${pluginPath}` 下的脚本时，execute/shell 工具请求省略 `cwd` 字段。`${pluginWorkspace}/${projectDir}` 只作为产物路径或脚本的 `--workspace` 参数。仓库根目录与执行目录只使用环境检查器返回值，不作为模型填写的脚本参数。

## 缺失产物处理

```bash
python -X utf8 "${pluginPath}/hooks/inspect_skill_contract.py" autodev-utest --feature "${feature}" --plain
```

# /autodev-utest - 单测协调与验证

## 阶段定位

Plan 已固化每个 TASK 的实现范围，Code 只实现生产代码、不创建测试。本阶段使用路由脚本生成的 assignment 内容，生成或补齐单测，运行真实测试命令，归因失败，生成 `UNIT_TEST_REPORT.md` 与 `UNIT_TEST_RESULT.json`。

固定 Code Workflow 中，成功或失败时封存测试资产均携带 `--commit-stage utest --commit-summary "<本次新增或修复测试的中文说明>"`。提交格式为 `<看板ID> #comment cmbdevcalw提交 UTest：<说明>`，不附加 TASK，不自动宣称测试通过。生产问题回到 Rework，其提交也不附加 TASK；Review 只读、不创建提交。没有代码变更时复用已有 commitSha。详见 `docs/code-commit-messages.md`。

调用脚本读取当前 Feature 状态：

```bash
python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"
```

每次需要当前状态或 checkpoint 时重新运行该脚本，不得直接读取 `.autobizdevops/state.json`、`.autobizdevops/STATE.md`、`hooks.ndjson` 或 Feature 目录内的 `.plan.lock`。

## 输入与产物

测试 assignment 只通过以下脚本生成：

```bash
python -X utf8 "${pluginPath}/hooks/utest_assignment_router.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --json
```

每个 assignment 的 `promptContent` 只包含 Batch plan 的绝对路径，以及 TASK `id`、`outcome`、`implementationPoints`、`testPoints`、`verificationIntent` 和 `validationLocations.repo/cwd`。派发时原样使用；不得自行打开 plan 补取、转述或拼接 TASK 字段。Plan 命令的 argv 不作为测试命令。

Code 阶段未解决的缺陷会原样留在 plan 里交到本阶段。开工前逐个 Batch 读取并列出 TASK `blockers[]`。测试归属的入场缺陷与 UT target 并列进入 UTest 修复队列；任何生产实现缺陷必须形成 `source_fix_request` 回到同一 Batch 的 implement repair，禁止在 UTest 直接改码。

判定现状以本轮重跑该命令的结果为准，不以字段快照为准：`status=passed` 的条目里 `lastFailure` 只是修复过程记录，重跑通过即不再是缺陷；`status` 非 `passed` 或存在未清空的 `blockers` / `deferredIssues` 时，先重跑确认复现，再按失败分类处理。未经重跑不得直接依据字段动生产代码。

其余输入只用于定位与执行，不用于重新推导测试目标：

- 根据系统约束引用，仓库边界与 framework 约束。
- 当前 feature 源码、已有测试、构建清单、锁文件和测试配置。
- `${pluginPath}/skills/autodev/autodev-utest/reference/test-engineer-task-protocol.md`。

输出：

- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_REPORT.md`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/UNIT_TEST_RESULT.json`
- `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log`
- validation Evidence

测试工程师可修改：

- 测试源码、fixture、mock、测试辅助代码。
- 测试环境配置；当前模块 `pom.xml` 中测试需要的 `test` scope 依赖与必要 Surefire/Failsafe 配置。
- POM 修改由当前 Batch 的 test 阶段授权，不要求重新启动已 implemented 的生产 TASK。写守卫与封存器按 XML 实际差异校验；保留生产依赖、parent/BOM、共享属性和 profiles。生产源码只能回实现修复阶段处理。

修复超出 assignment 的仓库或 TASK 边界时返回 `source_fix_request`，不跨边界改动。

`UNIT_TEST_RESULT.json`、`evidence/EVIDENCE.jsonl`、`evidence/EVIDENCE.index.json` 只由 `run_utest_command.py` 与 `unit_test_result_writer.py` 写入。不得用编辑、`sed`、截断或重写修改它们，不得删除已写入的 target 与 evidence 行。校验不通过时按 `requiredAction` 修正对应 plan、assignment 或测试命令后重跑。

## 执行主体与派发顺序

task 工具可用时，主协调器必须使用 `test-engineer-autodev`：

```bash
python -X utf8 "${pluginPath}/hooks/utest_assignment_router.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --json
```

1. 使用 router 返回的 assignment 顺序。
2. 先串行执行全部 backend assignments，再串行执行全部 frontend assignments；其他 lane 排在其后。
3. 一次只派发一个 assignment；收到完整返回后再派发下一个，不并发跨 Batch 或跨仓库测试。
4. task description 以 router 返回的 `promptContent` 原文开头，再附加 SCOPE/SYSTEM/UNIT 引用、环境检查器原样返回的仓库与模块路径、允许写入边界、失败分类和固定返回字段；不得附加 plan TASK JSON。
5. 收集 `status`、`assignment`、`constraint_files`、`lane`、`framework`、`runner`、`environment_initialization`、`test_targets`、`command_results`、`evidence_ids`、`failure_classification`、`source_bug_attestation`、`source_fix_request`、`e2e_handoff`、`warnings`。

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
python -X utf8 "${pluginPath}/hooks/inspect_test_environment.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --batch-worktree "${batchWorktree}" --json
```

检查器只从当前 Plan 的 `codeWorkspaces` 映射解析仓库；模型不得填写 repo、仓库地址、framework 或 cwd，也不得从历史 Task Run、当前 cwd 或任何缓存推断仓库。必须传入当前 Batch 的 `--batch-worktree`，让检查和执行都绑定到同一 worktree。`scope.modules` 是可选的范围收窄项：声明时只检查所列模块；未声明时，检查器只在当前 Batch worktree 和 `scope.workspaceRoots` 内发现构建项目，不跨出该范围，也不进入嵌套 Git 仓库、依赖缓存或构建输出目录。若 Batch 当前提交或 UTest 改动能定位工程，检查器只检查改动所属的最深项目根；否则返回候选目标。`environment_target_ambiguous` 时，依据 TASK 与候选路径判断；能确定时用 `--task-id <TASK_ID> --environment-target "<TASK_ID>=<ENVIRONMENT_TARGET_ID>"` 重跑检查，并保留 `--batch-worktree`，不能确定时按环境阻断收口，不得猜选、初始化全部候选或要求把路径补写进 Plan。环境 ready 后，执行器仍按测试文件路径选择所属目标，嵌套项目优先匹配最深项目根。`workspace_binding_missing` 时回到 `/autodev-plan` 修正 `codeWorkspaces`；`contract_gap` 只用于 plan 的 `workspaceRef`、显式 `scope.modules` 与仓库绑定不一致。

`status=init_required` 时读取并应用：

```text
${pluginPath}/skills/autodev/autodev-utest/reference/test-environment-profiles.md
```

环境初始化只修改测试配置、manifest 与对应锁文件。按 profile 完成改动后用 `--kind setup` 执行安装或校验命令，再重新运行 inspector 并输出返回的 `status`；`status` 未变为 `ready` 前不进入测试生成与执行，不得用一条自拟的 `--kind setup` 命令通过环境检查。`conflict` / `unsupported` 不做猜测性初始化；环境目标有歧义时按上面的 runtime selector 重查，不要求把模块路径补写到 Plan。网络或安装授权被拒绝时分类为 `environment` 并阻断。

### 一次准备与依赖诊断

在 Workflow 给出的当前 run/Batch/worktree/lease 下，每个 TASK 先运行 `utest_runtime.py prepare`。通用参数为 `--workspace`、`--feature`、`--run-id`、`--batch-id`、`--batch-worktree`、`--task-id`、`--owner-token`；值只来自当前 Workflow。按返回 preset、模块能力和 `testPoints` 执行，JUnit 与 Mockito 配合使用，保留实际 JUnit 4/5。依赖声明不是解析成功的证明；基础环境 ready 但缺 preset 能力时也可补齐测试依赖一次。

POM 修改可用 `apply-pom`、同一绑定参数和 `--content-file <worktree或系统临时目录中的完整POM内容>`；也可通过完整 write_file/精确 edit_file。二者与 seal 使用同一 XML 差异校验，允许测试所需依赖及必要测试插件，禁止改生产依赖、parent/BOM、共享属性、profiles。不得为获取权限重开生产 TASK。

有实际证据时 `select --preset <ID> --reason <原因>` 允许执行前调整一次；执行后冻结。静态依赖读取 `reference/legacy-static-test-patterns.md`，只复用可验证的 inline/PowerMock/隔离 fixture 模板。无法隔离时返回 `testability_gap`，不得静态推导 source_bug；H2/mapper 测试不替代 Service 授权逻辑。

Runtime 保存 UTEST_ENVIRONMENT.json/UTEST_EXECUTION.json，恢复复用决策与预算，不复用历史 PASS。Plan V2 不增加 preset/依赖/路径/argv，不要求 nonGoals 输入。Maven 只读诊断统一经 `run_utest_command.py --kind setup`，相同模块/配置/argv 缓存成功及失败；仅外部依赖环境实际变化后使用 `--refresh-diagnostics`。classpath 查类用 `lookup-class --classpath-file <已有文件> --class-name <完整包名>`；不递归扫描本地 Maven 仓库。命中、缺失、超时均复用，同一 jar 的源码/反编译结果也复用。

## 测试域路由

Spring Boot 2/3 按目标选择 `fundamentals`、`mvc`、`security`、`websocket`、`persistence`：

```bash
python -X utf8 "${pluginPath}/hooks/render_spring_test_reference.py" --domain <domain>
```

Vue3/React 按目标选择 `fundamentals`、`component`、`logic`、`state`、`integration`：

```bash
python -X utf8 "${pluginPath}/hooks/render_frontend_test_reference.py" --framework <vue|react> --domain <domain>
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

### Code Workflow 的 UTest 子阶段

固定 Workflow 已经启动当前 Batch 的 `test` 子阶段后才可执行本技能的测试协议。全局 checkpoint 始终保持 `code_in_progress`；不得调用 `update_checkpoint.py` 创建 `unit_test_in_progress` 或 `unit_test_done`。

### 展开 assignment 的测试计划

每个 TASK 建立一个 UT target，测试重点逐条取该 TASK 的 `testPoints`，并用 `outcome`、`implementationPoints` 和 `verificationIntent` 理解预期行为；`validationLocations` 只确认 repo/cwd。

```markdown
| ID | Task | Test Focus | Priority | Status |
|----|------|------------|----------|--------|
| UT-001 | B001/T003 | testPoints 原文 | P0 | planned |
```

- `testPoints` 是必须覆盖的测试重点；`implementationPoints` 用于定位行为边界，不能替代测试重点。
- 需要真实浏览器、多页面导航或真实网络链路的重点只写入 `e2e_handoff`。
- 每个 TASK 的 target 为 P0；AC 与 spec 覆盖由 runner 从当前 plan 绑定。
- plan 之外的补充测试记 P2，并写明补充理由；不得用它替代任何 plan 目标。
- `promptContent` 缺 Batch plan 绝对路径、`outcome`、`implementationPoints`、`testPoints`、`verificationIntent` 或有效 `validationLocations` 时记 `contract_gap`。

由 Runtime 准备结果确认目标后进入生成与执行；报告表在收口时聚合，不额外生成长篇选型方案。

### 生成测试

每个 UT target 都先落地测试文件，再执行为该测试生成的精确命令；没有落地测试文件的 target 不执行、不记 PASS。

固定 Batch 流程必须在文件落地后运行 `utest_runtime.py preflight`，携带当前 Workflow run/Batch/worktree/lease/TASK 绑定、`--test-file <仓库根相对文件>`、`-- <精确 argv>`，然后执行返回的 `bashCommand`。Runtime 生成的 Bash 直接运行测试、监控子进程、立即捕获 PIPESTATUS/tee 状态并登记 Evidence；Python 不启动行为测试。`--retry-kind test_bug` 与受控生产修复后的 `--retry-kind source_repair` 各最多一次，argv/target 冻结。`awaiting_record` 只补记现有输出，不重跑。

首次真实测试命令预算 600 秒，阶段总预算最多 1800 秒（Workflow 可收窄），命令最多 300 秒；预算包含探索和 setup，恢复/续 lease 不重置。Bash 开始执行前再次检查绑定与期限。零用例或无法证明用例实际执行为 BLOCKED；exit 0 不足以 PASS。到期或阻断仍须按 Workflow 封存有效测试资产、记录 issue 并释放 lease，不读插件源码寻找绕行路径。

每个 UT target：

1. 读取同仓库最邻近的 2 至 3 个测试，匹配 runner、命名和 setup/teardown。
2. 断言覆盖 `testPoints`，并与 `outcome`、`implementationPoints` 和 `verificationIntent` 一致。
3. 在公开 seam 上写一个行为目标；按 `${pluginPath}/skills/references/test-quality.md` 选择 mock 边界。
4. 后实现测试使用 `post_implementation=true`、`tdd_rebuild=false`；首次即通过记 `characterization_pass`，不得删除现有生产实现制造 red。
5. 测试自身错误由测试工程师修复并重跑同一精确目标。

### 执行与证据

环境 setup 命令（依赖安装或受支持 profile 的环境校验）仍由 runner 执行：

```bash
python -X utf8 "${pluginPath}/hooks/run_utest_command.py" --kind setup --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --task-id "<TASK_ID>" --batch-worktree "${batchWorktree}" -- <argv...>
```

一个 TASK 有多个环境目标时，setup 阶段按检查器返回的 `environmentTargetId` 分别增加 `--environment-target-id "<ENVIRONMENT_TARGET_ID>"`；test 阶段由测试文件路径选择其所属目标，嵌套项目使用最深匹配目录。

行为测试由 Bash 直接执行，Python runner 仅在执行后登记真实结果与 Evidence。先在 Bash 中捕获输出并保留测试进程自己的退出码：

```bash
RAW_OUTPUT="$(mktemp)"
set -o pipefail
<TEST_ARGV...> 2>&1 | tee "$RAW_OUTPUT"
test_exit=${PIPESTATUS[0]}
python -X utf8 "${pluginPath}/hooks/run_utest_command.py" --kind test --record-only --exit-code "$test_exit" --output-file "$RAW_OUTPUT" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --task-id "<TASK_ID>" --batch-worktree "${batchWorktree}" --test-file "<RELATIVE_TEST_FILE>" -- <同一组 TEST_ARGV...>
record_exit=$?
if [ "$record_exit" -ne 0 ]; then
  exit "$record_exit"
fi
rm -f "$RAW_OUTPUT"
exit 0
```

`<TEST_ARGV...>` 由真实 manifest、测试配置与新建测试文件确定；`--task-id` 取 `promptContent`。Bash 是测试命令的唯一执行者，因此编译器和测试失败会即时显示；不要再把测试 argv 交给 Python runner 执行。之后的 `--record-only` 必须传入完全相同的 argv，它会校验当前 Plan/TASK/测试文件绑定、生成稳定 digest 与 commandId/targetId、把捕获输出追加到 `test-output.log`，并创建 Evidence、更新 `UNIT_TEST_RESULT.json`。记录成功后删除临时输出文件；如果记录失败则保留该文件，修复记录问题时不得重新执行测试。

### 失败分类与修复

| 类型 | 处置 |
|------|------|
| `test_bug` | 测试工程师只修测试、fixture、mock、辅助代码或测试环境配置，重跑原命令 |
| `source_bug` | 用失败测试锚点生成 `source_fix_request`；不得在 UTest 中改生产代码。固定 Batch Workflow 会回到同一 Worktree 的 implement repair，重新编译、Review、UTest |
| `contract_gap` | 阻断并回流约束/计划 |
| `environment` | 记录环境与复现命令；只应用受支持 initProfile |
| `flaky` | 记录多次重跑结果与根因 |
| `unknown` | 停止，不猜修 |

任何生产修复或 `source_fix_request` 前，先校验失败锚点：

```bash
python -X utf8 "${pluginPath}/hooks/validate_utest_source_bug.py" --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --task-id "<TASK_ID>" --command-id "<GENERATED_COMMAND_ID>" --target-id "<UT_ID>" --task-digest "<RUNNER_RETURNED_TASK_DIGEST>" --evidence-id "<EVIDENCE_ID>"
```

静态观察、未执行测试或 exit 0 不得分类为 `source_bug`。

1. 确认失败测试稳定复现，并通过 source-bug validator 校验当前 plan 覆盖。
2. 排除测试、命令与环境问题。
3. 不改生产代码；返回最小 `source_fix_request`，包含 attestation、失败命令、根因和受影响 implementation point。

主协调器收到 `source_fix_request` 后，在同一 Batch 原生 Worktree 的 implement repair 中做最小生产修复，重新编译和封存；随后必须重新执行生产代码 Review 和原 Batch/lane/workspace UTest，复用 UT target 并追加新 Evidence。

同一个生产根因最多受控修复一次；仍失败时保留该 Batch 的测试证据与阻断记录，不改变全局 Code checkpoint。

### 扩大验证

所有 P0/P1 精确目标通过后，仅在新增改动、失败或未验证的影响范围确有需要时安排模块回归；不对已通过且未变化的目标重复执行。扩大验证必须保持 TASK/test-file/Evidence 映射和剩余预算，由协调器明确交付，不由单个 target 自行扩大范围。

项目约定的生产编译或测试编译命令也使用 Bash 直接执行。不要把 compile-only 命令传给 `run_utest_command.py --kind setup`：这样能在执行现场直接看到编译器输出和退出码。通过 Bash 的 `tee` 把完整 stdout/stderr 同时显示并追加到 Feature 的 `test-output.log`，保留 pipeline 前一个进程的退出码：

```bash
set -o pipefail
UTEST_LOG="${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/test-output.log"
printf '\n=== DIRECT BASH COMPILE ===\ncwd: %s\ncommand: %s\n' "$PWD" '<真实 compile-only 命令>' | tee -a "$UTEST_LOG"
<真实 compile-only 命令> 2>&1 | tee -a "$UTEST_LOG"
compile_status=("${PIPESTATUS[@]}")
compile_exit=${compile_status[0]}
compile_log_exit=${compile_status[1]}
printf 'compile_exit_code: %s\n=== END DIRECT BASH COMPILE ===\n' "$compile_exit" | tee -a "$UTEST_LOG"
exit "$((compile_exit != 0 ? compile_exit : compile_log_exit))"
```

从真实项目构建配置选择 compile-only 或 test-compile 目标；不要运行 package/build/check 等会混入打包或行为测试的复合目标。管道后必须立即保存整个 `PIPESTATUS`，同时检查编译退出码和 tee 日志写入退出码；日志写入失败不能报告完整验证成功。将精确命令、cwd、两个退出码和结论写入 `UNIT_TEST_REPORT.md` 的 `Execution Summary`。编译失败必须读取这次 Bash 输出并归因，不得用精确测试通过覆盖失败；生产源码问题按 `source_fix_request` 流程返回实现阶段修复。

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
python -X utf8 "${pluginPath}/hooks/unit_test_result_writer.py" derive-scenario-coverage --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/unit_test_result_writer.py" validate --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --structure --gate
```

## 分支决策

固定 Workflow 收口当前 Batch 的 `test` 子阶段，前提是：

- 全部 P0 UT target PASS。
- 入场缺陷已全部清零，每项都有重跑通过的 Evidence。
- 报告、日志、结果 JSON、Evidence 完整且 writer 校验通过。
- 源码修复均有失败测试锚点，并在实现阶段回修后重新经过 Review 与 UTest 的通过 Evidence。
- 扩大验证已执行并记录。

存在 FAIL、BLOCKED、未归因失败、`contract_gap` 或超过修复次数时保留该 Batch 的测试证据；全局 checkpoint 不变。

测试归属的 `test_bug`、fixture/mock 与测试环境配置必须在本阶段修完并重跑。`source_bug` 是例外：只能携带 attestation 回到同一 Batch 的 implement repair，之后重新进入 Review 与 UTest；不得以 deferred finding 进入合并。`environment` 记录环境与复现命令后阻断，`contract_gap` 回流 `/autodev-plan`。

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
- [ ] 固定 Workflow 已启动当前 Batch 的 `test` 子阶段；全局 checkpoint 仍为 `code_in_progress`。
- [ ] 已声明执行主体分支。
- [ ] 已列出 code 阶段遗留的入场缺陷，并全部重跑清零。
- [ ] 已输出完整 UT target 表，覆盖全部 TASK 测试重点；浏览器目标已转 `e2e_handoff`。
- [ ] 已按 Batch、lane、workspace 串行完成 assignment。
- [ ] 已检查测试环境，inspector 返回 `ready`。
- [ ] 每个 UT target 均已落地测试文件，并通过 runner 记录命令、日志、Evidence 与 target。
- [ ] 失败已分类，修复与 `source_fix_request` 已闭环。
- [ ] 已执行扩大验证并生成 `UNIT_TEST_REPORT.md`。
- [ ] 已派生 coverage、设置 verdict、校验 `UNIT_TEST_RESULT.json`。
- [ ] 成功时已由固定 Workflow 收口当前 Batch 的 `test` 子阶段。

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
