---
name: autodev-utest
description: "Dev 阶段单元测试生成与单测驱动最小修复技能。读取 proposal.md、specs/**/*.md、design.md、PLAN.md 与 REQUIREMENTS_EVAL，按 specs 行为契约生成单测、执行验证、归因失败，并在当前 feature 范围内做最小测试代码或业务代码修复。默认由当前会话内联执行。"
version: v1.1.0_v0602
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约

当前 skill 的 checkpoint、输入/输出产物和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 为唯一事实来源。
运行前如需查看当前契约，执行：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-utest --json
```
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-utest - 单测生成与最小修复

## 阶段定位

本技能位于 `autodev-reviewer` 之后、`autodev-e2e` 之前。它的职责不是只补测试，也不是自由修代码，而是用单元测试把当前 feature 的实现拉到可验证状态。

核心目标：

- 从 `proposal.md`、`specs/**/*.md`、`design.md`、`PLAN.md`、`REQUIREMENTS_EVAL.md` 提取需要单测覆盖的行为，其中 specs 是主要行为契约。
- 为当前 feature 生成或补齐单元测试。
- 逐个运行测试，保留原始测试日志。
- 对失败进行根因归类。
- 在边界内做最小修复：测试代码问题修测试，当前 feature 的业务实现问题可修生产代码。
- 生成 `UNIT_TEST_REPORT.md`，为 E2E 与 Verify 阶段提供证据。

## 执行主体

本 skill 默认且只能由当前会话内联执行：

- 当前会话直接读取源码、生成测试、执行验证、分析失败、做最小修复、更新状态文件。
- 不得把测试生成、失败归因、代码修复或报告编写委派给下级 agent 或子 agent。
- 后台进程只允许用于运行构建或测试命令，不承担 agent 工作。

状态文件统一使用 `.autobizdevops/state.json`；`.autobizdevops/STATE.md` 仅为自动生成视图。每次推进 checkpoint 时，只允许更新当前 `{slug}` 对应的 Feature 记录，不得改写其他 slug 的状态。

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续准入、恢复和完成判断直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 参数

扫描 `$ARGUMENTS`：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--feature {slug}` | 自动推断 | 指定当前 Feature |
| `--mode auto` | 是 | 先归因，再决定修测试还是修业务代码 |
| `--mode test` | 否 | 只允许修改测试代码、fixture、mock、测试辅助文件 |
| `--mode code` | 否 | 只允许在已有失败测试锚定下修当前 feature 的业务代码 |
| `--no-fix` | 否 | 只生成、运行、记录，不做任何修复 |
| `--max-fix N` | `3` | 单个根因最多修复尝试次数 |

没有显式传入 mode 时使用 `auto`。

## 输入与产物

FEATURE_DIR：

```text
FEATURE_DIR = {PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}
```

必读输入：

- `{FEATURE_DIR}/proposal.md`
- `{FEATURE_DIR}/specs/**/*.md`
- `{FEATURE_DIR}/design.md`
- `{FEATURE_DIR}/PLAN.md`
- `{FEATURE_DIR}/REQUIREMENTS_EVAL.md`
- `AGENTS.md` 与项目内相关约束
- 与当前 feature 相关的源码、已有测试、构建配置

输出产物：

- `{FEATURE_DIR}/UNIT_TEST_REPORT.md`
- `{FEATURE_DIR}/test-output.log`
- `.autobizdevops/state.json` 与自动生成视图 `.autobizdevops/STATE.md`

禁止修改：

- `{FEATURE_DIR}/PRD.md`（如果存在）
- `{FEATURE_DIR}/proposal.md`
- `{FEATURE_DIR}/specs/**/*.md`
- `{FEATURE_DIR}/design.md`
- `{FEATURE_DIR}/PLAN.md`
- `{FEATURE_DIR}/REQUIREMENTS_EVAL.md`
- E2E / Verify 阶段产物
- 与当前 feature 无关的业务代码
- 生产代码中的测试专用入口、测试专用分支、伪造实现

允许修改：

- 当前 feature 直接相关的测试文件、fixture、mock、测试工具类。
- 当前 feature 直接相关的业务代码，但必须满足“最小业务修复门槛”。

## 最小业务修复门槛

只有同时满足以下条件，才允许修改业务代码：

1. 已有或新生成的单元测试能稳定复现失败。
2. 已确认失败不是测试代码、mock、fixture、命令或环境问题。
3. 失败行为能映射到 `specs/**/*.md`、`design.md` 或 `REQUIREMENTS_EVAL.md` 中的当前 feature 契约。
4. 已定位到当前 feature 直接相关的最小代码区域。
5. 修复不需要改变需求、接口契约、数据模型或跨模块设计。
6. 修复后必须重跑精确失败测试，并重跑受影响测试类或模块。

若任一条件不满足，禁止修改业务代码。记录为 `contract_gap`、`environment`、`flaky` 或 `unknown`，并停止推进 `unit_test_done`。

## 失败归因

每个失败必须归入以下之一：

| 类型 | 含义 | 允许动作 |
|------|------|----------|
| `test_bug` | 测试代码、断言、mock、fixture、测试数据或命令错误 | 修测试代码 |
| `source_bug` | 当前 feature 的业务实现不满足已确认契约 | 最小修业务代码 |
| `contract_gap` | proposal/specs/design/实现/测试之间存在冲突或缺口 | 停止并记录回流建议 |
| `environment` | 依赖、数据库、网络、权限、命令、构建环境问题 | 记录阻断和复现命令 |
| `flaky` | 非确定性失败，重跑结果不一致 | 标记 flaky，记录重跑证据 |
| `unknown` | 无法可靠归因 | 停止，不继续猜修 |

禁止为了通过测试而弱化断言、删除核心用例、改写需求产物、跳过失败测试或伪造日志。

## 工作流程

### Step 1: 前置检查

1. 确定 `{slug}` 与工作目录。
2. 检查当前 checkpoint 应为 `requirements_eval_done` 或 `unit_test_in_progress`。
3. 确认必读输入存在。
4. 读取 `AGENTS.md` 与项目测试约定。
5. 识别构建工具：Maven、Gradle、npm、pnpm、yarn、pytest、go test 等。不要假设一定是 Java/Maven。

缺少任一必读输入时，保持 checkpoint 不变，向用户列出缺失文件后结束。

### Step 2: 写入开始 checkpoint

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint unit_test_in_progress
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

### Step 3: 建立单测计划

从输入产物中提取：

- `proposal.md` 中的能力边界、影响面和非目标。
- `specs/**/*.md` 中的 Requirement / Scenario、ADDED / MODIFIED / REMOVED 行为变化。
- `design.md` 中的 API Decisions、Data Decisions、Technical Design。
- `REQUIREMENTS_EVAL.md` 中的风险、遗漏、建议重点覆盖项。
- `PLAN.md` 中已完成任务和受影响模块。

生成测试矩阵，写入 `UNIT_TEST_REPORT.md` 的 `## Test Plan`：

```markdown
| ID | Source | Behavior | Test Target | Priority | Status |
|----|--------|----------|-------------|----------|--------|
| UT-001 | specs/foo/spec.md / Scenario | ... | FooServiceTest#should... | P0 | planned |
```

优先级：

- P0：核心 Requirement / Scenario、关键业务规则、已在评审中标为高风险的路径。
- P1：边界值、异常分支、权限/状态/幂等/数据一致性。
- P2：兼容性、非核心边界、可维护性补充。

非 public 方法默认不直接测试，应通过 public 行为间接覆盖。只有工具类、纯函数、复杂算法或已有项目约定允许时，才直接测试非 public 行为，并在报告中说明原因。

### Step 4: 生成或补齐单测

每次只处理一个测试目标：

1. 读取最邻近的 2 到 3 个已有测试文件，匹配项目风格。
2. 选择最小测试入口，优先测试真实行为。
3. Mock 只在不可避免时使用，不得只测试 mock 行为。
4. 写入一个测试方法或一个最小测试文件。
5. 在 `UNIT_TEST_REPORT.md` 立刻追加该测试目标的状态。

若当前行为是新需求或缺陷修复，优先走 red-green：

1. 写测试。
2. 运行精确测试。
3. 确认测试因预期原因失败。
4. 再进入最小修复。

若当前行为已经由实现支持，测试可能首次运行即通过。此时必须在报告中标记为 `characterization_pass`，不能伪造 red 阶段。

### Step 5: 执行精确测试

必须优先运行精确到测试方法或最小测试文件的命令，例如：

```bash
mvn test -Dtest=FooServiceTest#shouldRejectEmptyName
./gradlew test --tests "com.example.FooServiceTest.shouldRejectEmptyName"
npm test -- FooService.test.ts -t "rejects empty name"
pytest tests/test_foo.py::test_rejects_empty_name
```

所有命令输出必须追加到：

```text
{FEATURE_DIR}/test-output.log
```

日志中至少保留：

- 时间。
- 工作目录。
- 命令。
- 退出码。
- 输出摘要或完整输出。

### Step 6: 失败归因与最小修复

测试失败时，按顺序处理：

1. 完整读取错误、堆栈、失败断言。
2. 确认是否能稳定复现。
3. 查最近改动、相似实现、相似测试。
4. 形成单一根因假设，写入报告。
5. 根据归因选择动作：
   - `test_bug`：只改测试、fixture、mock 或测试辅助代码。
   - `source_bug`：只做当前 feature 范围内的最小业务修复。
   - 其他类型：停止或记录阻断，不做猜测性修复。
6. 每次只做一个修复尝试。
7. 修复后立即重跑同一个精确测试。

单个根因修复尝试达到 `--max-fix` 仍失败时，停止修复，写入 `needs_fix` 证据，不得继续堆叠补丁。

### Step 7: 扩大验证范围

当所有 P0/P1 单测目标通过后，执行扩大验证：

1. 重跑本轮新增或修改的测试类。
2. 重跑受影响模块的轻量测试命令。
3. 若项目约定要求，运行编译或测试编译命令。

扩大验证失败时，必须回到 Step 6 归因。不得只因精确测试通过就推进完成。

### Step 8: 生成最终报告

`UNIT_TEST_REPORT.md` 必须包含以下章节：

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

`Coverage Matrix` 至少要映射 specs Requirement / Scenario 或 design 契约到测试方法：

```markdown
| Source | Requirement | Test | Result | Evidence |
|--------|-------------|------|--------|----------|
```

`Fix Attempts` 必须列出每一次修复：

```markdown
| ID | Classification | Files Changed | Hypothesis | Command | Result |
|----|----------------|---------------|------------|---------|--------|
```

`Handoff` 必须明确：

- E2E 阶段应重点覆盖的链路。
- 仍需人工确认的项。
- 若失败，建议回流阶段：`autodev-code`、`autobiz` 或 `environment`。

### Step 9: 分支决策

可以推进 `unit_test_done` 的条件：

- P0 单测目标全部 PASS。
- P1 单测目标 PASS，或有明确可接受原因并标记 `PASS_WITH_WARNINGS`。
- `UNIT_TEST_REPORT.md` 与 `test-output.log` 均已写入。
- 所有业务代码修复都有对应失败测试锚点和重跑通过证据。
- 扩大验证命令已运行，并在报告中记录结果。
- 报告 verdict 为 `PASS` 或 `PASS_WITH_WARNINGS`。

推进命令：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint unit_test_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

若存在 `FAIL`、`BLOCKED`、未归因失败、合同缺口或超过最大修复次数，保持 `unit_test_in_progress`，向用户报告阻断。只有根路由或后续验收阶段需要统一回流时，才使用 `needs_fix`。

## 质量规则

1. 测试必须验证业务行为，不以覆盖率数字替代断言质量。
2. 不得只为提高覆盖率而生成无意义测试。
3. 不得删除、跳过、弱化已有失败测试。
4. 不得把 mock 调用次数当成唯一业务断言，除非该调用本身就是契约。
5. 不得为测试向生产代码添加测试专用方法、测试专用开关或伪实现。
6. 每个“已修复”结论都必须有新鲜测试命令和退出码证据。
7. 不能确定根因时，停止并记录，不猜修。
8. 本 skill 的规则不得覆盖 AGENTS.md；如冲突，以 AGENTS.md 中项目约束为准，除非系统级指令另有要求。

## 输出清单

- [ ] 已读取必需输入和项目约束。
- [ ] 已写入 `unit_test_in_progress`。
- [ ] 已建立 Test Plan。
- [ ] 已生成或补齐单测。
- [ ] 已运行精确测试并记录到 `test-output.log`。
- [ ] 失败均已归因。
- [ ] 允许范围内的最小修复均已验证。
- [ ] 已执行扩大验证。
- [ ] `UNIT_TEST_REPORT.md` 包含必需章节和 verdict。
- [ ] 成功时已推进 `unit_test_done`。
