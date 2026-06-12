---
name: autodev-code
description: 按上游产物契约（Source Bundle）逐任务执行代码。读取契约列出的产物原件（标准链为 proposal/specs/design/PLAN）与 AGENTS.md，以 specs 为行为契约、design 为实现决策、PLAN 为执行队列；契约未提供 design/PLAN 时按降级读法从 proposal+specs 推导最小任务。做最小实现、逐项验证、更新任务状态，并在全部任务完成后推进 code_done。支持中断恢复、--feature 多人协作。
version: v1.1.1604
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-code --feature "$FEATURE_ID" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件，不要读取清单之外的阶段产物作为硬依赖。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）；按它决定读哪些部分、如何提取上下文。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止；bundle 未列出的产物不属于本工作流，不要读取、不要等待，也不要要求用户提供。
- **降级语义**：`required: false` 的输入是可选参考，缺失时按其 `extract.degrade` 的退化读法继续执行，不要因缺失而停止。上游节点不在当前工作流时，其产物已从 bundle 中移除，按本文对应的「bundle 不含 X」分支处理。

无 `$FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-code — 代码执行

## 阶段定位

`autodev-code` 只负责把上游确认的行为契约和技术设计落成代码。

输入契约（Source Bundle）：
- 输入产物以「流程契约」一节获取的 Source Bundle 为准，不要依赖本文写死的文件名；标准链下为 `proposal.md`、`specs/**/*.md`、`design.md`、`PLAN.md`。
- 每个输入按其 Method Bundle（`extract`：focus / method / degrade）决定读取重点和提取方式。
- `{FEATURE_DIR}/DETAIL_DESIGN.md`：如果存在，作为文件级实现设计的补充上下文；不得覆盖契约内产物的硬约束。
- `AGENTS.md`：项目级工程约束；如与本技能冲突，以 AGENTS.md 为准，除非系统级指令另有要求。

输出契约：
- 业务代码/测试/配置的最小必要修改。
- `{FEATURE_DIR}/PLAN.md`（在 bundle 中时）任务状态和验证证据更新。
- 刷新后的 `CHECKPOINT` 推进到 `code_done`。

不得修改（对 bundle 中存在的产物生效）：
- `{FEATURE_DIR}/PRD.md`（如果存在）
- `{FEATURE_DIR}/proposal.md`
- `{FEATURE_DIR}/specs/**/*.md`
- `{FEATURE_DIR}/design.md`
- 其他阶段报告文件

如果发现 `specs/**/*.md` 与 proposal、代码现实或 PLAN 任务冲突，停止编码，报告需要回到 `/autodev-specs` 更新行为契约；如果 bundle 含 `design.md` 且发现它与 specs、代码现实或 PLAN 任务冲突，停止编码，报告需要回到 `/autodev-plan` 更新设计。不要在 code 阶段偷偷修规格或设计。`PRD.md` 只允许作为排查上游规格缺口的可选参考，不能覆盖 specs。

## 准入检查

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续准入、恢复和完成判断直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

开始前必须确认当前 Feature 目录存在：

```text
{FEATURE_DIR}/
```

读取输入（消费 Source Bundle）：
- 先按「流程契约」一节取本 Feature 的契约 JSON，按 `sourceBundle` 逐项读取原件，按各自 `extract` 抽取上下文。
- `{FEATURE_DIR}/DETAIL_DESIGN.md`（如果存在）与 AGENTS.md（如果存在）作为补充上下文一并读取。
- 仅当 `required_inputs` 中的产物缺失时停止，不要生成替代文件；`required: false` 的输入缺失时按其 `extract.degrade` 继续，不要停止；bundle 未列出的产物不读不等。

开始任何业务代码修改前，必须根据 AGENTS.md 与项目 manifest 生成模块编译清单：

```text
.autobizdevops/modules_compile.json
```

格式必须为：

```json
{
  "version": 1,
  "modules": [
    {
      "module": "root",
      "path": "/absolute/path/to/code/module",
      "compile_command": "mvn compile"
    }
  ]
}
```

识别规则：
- 优先遵守 AGENTS.md 中声明的多模块构建方式。
- 若 AGENTS.md 未明确，但项目 manifest 明确存在单模块或多模块构建入口，则生成对应模块清单。
- `path` 必须是模块目录的绝对路径；`compile_command` 会在该目录作为 cwd 执行，命令本身不要再写 `cd ... && ...`。
- 无法确定模块路径或编译命令时，停止并询问用户，不得开始编码。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

## 执行协议

### 1. 建立执行上下文

按 Method Bundle 抽取上下文：对 Source Bundle 中的每个输入，按其 `extract.focus` 列出的重点和 `extract.method` 的提取方式读取并记住关键信息；不要照搬固定清单，以契约输出为准。

执行队列的确定：
- bundle 含 `PLAN.md` 时，按 PLAN 的任务 DAG 作为执行队列；如果 PLAN 中额外写明模块路径、入口、涉及文件或用户补充技术细节，把它们作为定位线索，不要要求 PLAN 必须列出完整文件清单。
- bundle 不含 `PLAN.md` 时（其生产者不在当前工作流）：从 proposal+specs 推导最小任务队列（2-5 个需求闭环任务，每个任务含做什么/规格依据/验证方法），逐项实现，并在完成摘要中记录任务清单与验证结果。

一致性检查（对 bundle 中存在的产物生效）：
- 如果某个待做任务引用的 `规格依据` 在 `specs/**/*.md` 中不存在，停止并提示回到 Specs/Plan 修正。
- 如果 bundle 含 `design.md`，且某个待做任务引用的 `设计依据` 在 `design.md` 中不存在，停止并提示回到 Plan 修正。
- 如果某个待做任务依赖上游产物中仍为「待确认」且会影响实现结果的事项，停止并提示用户确认或回到对应阶段；不要基于猜测继续。

### 2. 选择下一个任务

扫描执行队列（`PLAN.md`，或降级模式下的推导任务清单）：
- 跳过状态为「完成」的任务。
- 优先选择状态为「进行中」的任务恢复。
- 否则选择第一个状态为「待做」且依赖任务均已完成的任务。
- 如果存在「失败」任务，先读取失败原因；只有用户明确要求继续修复，才重新置为进行中并处理。

每次只处理一个任务，完成后再进入下一个任务。

### 3. 执行单个任务

对当前任务：

1. 将任务状态改为「进行中」，并保留原任务内容。
2. 读取任务的「做什么」「规格依据」「设计依据」「验证方法」和覆盖矩阵，先用 `specs/**/*.md` 确认行为，再结合 `design.md`（如在 bundle 中；否则按其降级读法用现有代码模式）定位接口、数据模型、模块边界和验证重点。
3. 在修改任何业务代码前，先做 scoped exploration（有界代码探索），定位实际需要修改的文件和既有实现模式：
   - 只读取由 `PLAN.md`、`design.md`、`specs/**/*.md`、AGENTS.md 直接指向的文件，或通过有针对性的 `rg` 搜索定位到的相关文件。
   - 先识别当前项目已有的分层、命名、错误处理、校验、日志和测试风格，再决定如何修改。
   - 先形成简短的修改映射：规格/设计依据、拟修改文件、复用的既有模式、需要执行的验证命令；完成该映射后再进入代码修改。
   - 若 PLAN 明确列出模块路径或涉及文件，优先读取；若路径不存在，先定位真实路径；若真实入口、集成点或既有模式仍无法定位，停止并记录阻断，不要凭空创建疑似路径或引入猜测性抽象。
4. 对照 `specs/**/*.md` 与 `design.md`（如在 bundle 中）：
   - 行为实现必须满足 specs 中对应 Requirement / Scenario；不得把 PLAN 任务说明当作覆盖 specs 的理由。
   - bundle 含 design 时：涉及接口只实现 API Decisions 中已确认的入口和行为，若 `x-auto-no-http-api: true` 不得新增 HTTP/API；涉及数据只实现 Data Decisions 中已确认的数据变更，若 `x-auto-no-sql: true` 不得新增数据库表、字段或迁移；涉及架构/模块边界遵守 Technical Design 的集成点和方案取舍。
   - bundle 不含 design 时：接口/数据/架构决策遵循现有代码模式做最小决策，重大取舍先与用户确认，并把关键决策记入完成摘要。
   - 不得为了通过验证削弱校验、安全检查、日志或错误处理。
5. 做满足当前 specs/design 引用的最小必要代码修改，保持当前需求范围外的既有行为不变；只有在消除真实重复、降低当前实现复杂度，或符合项目既有模式时，才新增抽象。
   - 修改前先观察当前文件的局部格式风格；新增或修改代码必须保持一致。
   - 只做最小必要 patch，不重排、不重缩进、不格式化与当前任务无关的代码。
   - 当前任务完成前快速检查本轮 diff；若发现无关格式变化，先还原再验证。
6. 补充必要注释：
   - 对重要业务逻辑、非显然分支、边界处理、权限/租户/审计/幂等/状态流等关键约束，添加简洁注释说明“为什么这样处理”。
   - 对新增或修改的 PO、DTO、Entity、VO 等对象，按项目既有风格补充类注释和关键字段注释，说明业务含义、取值范围、单位、是否必填、状态枚举或兼容约束。
   - 不要给 getter/setter、简单赋值、自解释代码添加噪音注释；注释必须帮助后续维护者理解需求语义或实现边界。
7. 执行任务的「验证方法」。验证方法缺失或不可执行时，基于 AGENTS.md 和项目脚本选择最小可行验证，并把替代验证记录回任务。
8. 验证通过后，把任务状态改为「完成」，并记录验证命令/结果摘要。
9. 验证失败时：
   - 如果是当前任务代码问题，继续最小修复并重跑验证。
   - 如果是环境、依赖、数据、权限、需求不清或设计冲突，停止；把任务状态改为「失败」，记录失败原因和建议回流阶段。

bundle 含 `PLAN.md` 时，任务状态行必须保持形如：

```markdown
- **状态:** 待做 | 进行中 | 完成 | 失败
```

这样 hook 才能识别状态。降级模式（无 PLAN）下，任务状态维护在完成摘要的任务清单中。

### 4. 全部任务完成后的验证

当执行队列中没有「待做」或「进行中」任务后：

1. 运行项目级验证命令。优先使用 AGENTS.md 或 PLAN.md 指定命令；没有明确命令时按项目类型选择最小验证。
2. Java/Maven 项目至少运行编译命令。
3. 如果验证失败，回到相关任务继续修复；不要推进 `code_done`。

验证通过后推进 checkpoint：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

## 写入边界

允许写入：
- `PLAN.md`（在 bundle 中时）当前任务状态、验证证据、失败原因。
- 当前任务对应需求闭环直接相关的业务代码、测试、配置。
- 为完成当前任务必须新增的文件，但必须能追溯到 specs 行为依据与执行队列任务（bundle 含 design 时还需能追溯到设计依据）。

禁止写入：
- `PRD.md`（如果存在）
- `proposal.md`
- `specs/**/*.md`
- `design.md`
- 当前 skill 未在 `board_core/board_config.json` 输出产物中声明的其他 Feature 阶段产物。
- 与当前任务无关的业务文件。

如果为了完成任务必须修改 PLAN.md 未直接提到的业务文件，先确认该文件与 specs 行为依据和 design.md 集成点一致，再把文件和原因记录到当前任务验证证据或失败/完成摘要中；不要悄悄扩大范围。

## 完成条件

- 执行队列（`PLAN.md`，或降级模式下的推导任务清单）中所有任务状态均为「完成」。
- 若存在「失败」任务，本 skill 不算完成，不得推进 `code_done`，必须说明阻断和建议回流阶段。
- 所有必要验证通过。
- 项目编译通过；code_done execute hook 会额外记录模块编译结果，但不作为 checkpoint 推进阻断。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。** 下一步以当前 Feature 的工作流为准：

```bash
python "$PLUGIN_ROOT/hooks/resolve_next_skill.py" --workspace "$PLUGIN_WORKSPACE/$PROJECT_CODE" --feature "$FEATURE_ID"
```

标准链下一步为 `/autodev-reviewer`。
