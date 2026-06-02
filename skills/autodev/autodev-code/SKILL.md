---
name: autodev-code
description: 按照 autodev-specs 与 autodev-plan 产物逐任务执行代码。读取 proposal.md、specs/**/*.md、design.md、PLAN.md 和 AGENTS.md，以 specs 为行为契约、design 为实现决策、PLAN 为执行队列，做最小实现、逐项验证、更新 PLAN.md 任务状态，并在全部任务完成后推进 code_done。支持中断恢复、--feature 多人协作、--auto 自动衔接 /autodev-reviewer。
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
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-code --json
```
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-code — 代码执行

## 阶段定位

`autodev-code` 只负责把 `/autodev-specs` 确认的行为契约和 `/autodev-plan` 确认的技术设计落成代码。

输入契约：
- `{FEATURE_DIR}/proposal.md`：本轮变更目标、能力边界、影响面和非目标。
- `{FEATURE_DIR}/specs/**/*.md`：Requirement / Scenario 行为契约，是实现和验收的最高行为依据。
- `{FEATURE_DIR}/design.md`：API Decisions、Data Decisions、Technical Design、风险与待确认项。
- `{FEATURE_DIR}/PLAN.md`：任务 DAG、任务总览、任务详情、验证方法、覆盖矩阵；代码文件由任务、specs、design.md 和代码库探索共同定位。
- `AGENTS.md`：项目级工程约束；如与本技能冲突，以 AGENTS.md 为准，除非系统级指令另有要求。

输出契约：
- 业务代码/测试/配置的最小必要修改。
- `{FEATURE_DIR}/PLAN.md` 中任务状态和验证证据更新。
- 刷新后的 `CHECKPOINT` 推进到 `code_done`。

不得修改：
- `{FEATURE_DIR}/PRD.md`（如果存在）
- `{FEATURE_DIR}/proposal.md`
- `{FEATURE_DIR}/specs/**/*.md`
- `{FEATURE_DIR}/design.md`
- 其他阶段报告文件

如果发现 `specs/**/*.md` 与 proposal、代码现实或 PLAN 任务冲突，停止编码，报告需要回到 `/autodev-specs` 更新行为契约；如果发现 `design.md` 与 specs、代码现实或 PLAN 任务冲突，停止编码，报告需要回到 `/autodev-plan` 更新设计。不要在 code 阶段偷偷修规格或设计。`PRD.md` 只允许作为排查上游规格缺口的可选参考，不能覆盖 specs。

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

必须读取：
- `{FEATURE_DIR}/proposal.md`
- `{FEATURE_DIR}/specs/**/*.md`
- `{FEATURE_DIR}/design.md`
- `{FEATURE_DIR}/PLAN.md`
- AGENTS.md（如果存在）

如果缺少任一必读文件，停止，不要生成替代文件。

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

从 `proposal.md` 抽取并记住：
- 本轮目标、能力边界、影响面、非目标和 Open Questions。

从 `specs/**/*.md` 抽取并记住：
- 每个 capability 的 Requirement / Scenario。
- ADDED / MODIFIED / REMOVED 行为变化。
- SHALL/MUST 约束、边界条件、失败路径、权限/租户/审计/幂等/异步等外部可观察行为。

从 `design.md` 抽取并记住：
- Spec Traceability：specs 与 API/Data/Technical Design 的对应关系。
- API Decisions：API-*、`x-auto-no-http-api`、接口入口、请求响应、错误处理、权限/租户/审计约束。
- Data Decisions：DATA-*、`x-auto-no-sql`、表/模型、字段、索引、迁移、回滚。
- Technical Design：D-*、模块边界、集成点、方案取舍。
- Risks / Open Questions：所有待确认项。

从 `PLAN.md` 抽取：
- 任务 DAG 和依赖顺序。
- 每个任务的「做什么」「规格依据」「设计依据」「验证方法」「状态」。
- specs 行为覆盖和设计决策覆盖。
- 如果 PLAN 中额外写明模块路径、入口、涉及文件或用户补充技术细节，把它们作为定位线索；不要要求 PLAN 必须列出完整文件清单。

如果某个待做任务引用的 `规格依据` 在 `specs/**/*.md` 中不存在，停止并提示回到 Specs/Plan 修正。

如果某个待做任务引用的 `设计依据` 在 `design.md` 中不存在，停止并提示回到 Plan 修正。

如果某个待做任务依赖 proposal/specs/design.md 中仍为「待确认」且会影响实现结果的事项，停止并提示用户确认或回到对应阶段；不要基于猜测继续。

### 2. 选择下一个任务

扫描 `PLAN.md`：
- 跳过状态为「完成」的任务。
- 优先选择状态为「进行中」的任务恢复。
- 否则选择第一个状态为「待做」且依赖任务均已完成的任务。
- 如果存在「失败」任务，先读取失败原因；只有用户明确要求继续修复，才重新置为进行中并处理。

每次只处理一个任务，完成后再进入下一个任务。

### 3. 执行单个任务

对当前任务：

1. 将任务状态改为「进行中」，并保留原任务内容。
2. 读取任务的「做什么」「规格依据」「设计依据」「验证方法」和覆盖矩阵，先用 `specs/**/*.md` 确认行为，再结合 `design.md` 定位接口、数据模型、模块边界和验证重点。
3. 使用 `rg`、项目结构、AGENTS.md 和既有代码约定定位实际需要修改的文件。若 PLAN 明确列出模块路径或涉及文件，优先读取；若路径不存在，先定位真实路径，仍无法定位则停止并记录阻断，不要凭空创建疑似路径。
4. 对照 `specs/**/*.md` 与 `design.md`：
   - 行为实现必须满足 specs 中对应 Requirement / Scenario；不得把 PLAN 任务说明当作覆盖 specs 的理由。
   - 涉及接口时，只实现 API Decisions 中已确认的入口和行为；若 `x-auto-no-http-api: true`，不得新增 HTTP/API。
   - 涉及数据时，只实现 Data Decisions 中已确认的数据变更；若 `x-auto-no-sql: true`，不得新增数据库表、字段或迁移。
   - 涉及架构/模块边界时，遵守 Technical Design 的集成点和方案取舍。
5. 做最小必要代码修改，不做无关重构。
6. 补充必要注释：
   - 对重要业务逻辑、非显然分支、边界处理、权限/租户/审计/幂等/状态流等关键约束，添加简洁注释说明“为什么这样处理”。
   - 对新增或修改的 PO、DTO、Entity、VO 等对象，按项目既有风格补充类注释和关键字段注释，说明业务含义、取值范围、单位、是否必填、状态枚举或兼容约束。
   - 不要给 getter/setter、简单赋值、自解释代码添加噪音注释；注释必须帮助后续维护者理解需求语义或实现边界。
7. 执行任务的「验证方法」。验证方法缺失或不可执行时，基于 AGENTS.md 和项目脚本选择最小可行验证，并把替代验证记录回任务。
8. 验证通过后，把任务状态改为「完成」，并记录验证命令/结果摘要。
9. 验证失败时：
   - 如果是当前任务代码问题，继续最小修复并重跑验证。
   - 如果是环境、依赖、数据、权限、需求不清或设计冲突，停止；把任务状态改为「失败」，记录失败原因和建议回流阶段。

任务状态行必须保持形如：

```markdown
- **状态:** 待做 | 进行中 | 完成 | 失败
```

这样 hook 才能识别状态。

### 4. 全部任务完成后的验证

当 `PLAN.md` 中没有「待做」或「进行中」任务后：

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
- `PLAN.md` 中当前任务状态、验证证据、失败原因。
- 当前任务对应需求闭环直接相关的业务代码、测试、配置。
- 为完成当前任务必须新增的文件，但必须能追溯到 specs 行为依据、PLAN.md 任务和 design.md 设计依据。

禁止写入：
- `PRD.md`（如果存在）
- `proposal.md`
- `specs/**/*.md`
- `design.md`
- 当前 skill 未在 `board_core/board_config.json` 输出产物中声明的其他 Feature 阶段产物。
- 与当前任务无关的业务文件。

如果为了完成任务必须修改 PLAN.md 未直接提到的业务文件，先确认该文件与 specs 行为依据和 design.md 集成点一致，再把文件和原因记录到当前任务验证证据或失败/完成摘要中；不要悄悄扩大范围。

## 完成条件

- `PLAN.md` 中所有任务状态均为「完成」。
- 若存在「失败」任务，本 skill 不算完成，不得推进 `code_done`，必须说明阻断和建议回流阶段。
- 所有必要验证通过。
- 项目编译通过；code_done execute hook 会额外记录模块编译结果，但不作为 checkpoint 推进阻断。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。** 下一步：`/autodev-reviewer`
