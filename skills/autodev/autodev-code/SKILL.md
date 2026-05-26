---
name: autodev-code
description: 按照 autodev-plan 生成的 design.md 与 PLAN.md 逐任务执行代码。读取 PRD、design.md、PLAN.md 和 AGENTS.md，按任务 DAG 做最小实现、逐项验证、更新 PLAN.md 任务状态，并在全部任务完成后推进 code_done。支持中断恢复、--feature 多人协作、--auto 自动衔接 /autodev-reviewer。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```

<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->
## 流程契约（由 board_config.json 生成）

本区块由 `board_core/board_config.json` 静态编译生成，请勿手工修改；修改流程契约后运行 `python "{PLUGIN_DIR}/hooks/compile_skill_contracts.py" --write` 重新生成。

- **唯一事实来源:** `{PLUGIN_DIR}/board_core/board_config.json` 中 `skill: "autodev-code"` 的节点。
- **节点:** `dev.code`
- **阶段:** 代码实现
- **分组:** Dev
- **Checkpoints:** `code_in_progress`, `code_done`

### 输入产物
- `PRD.md`：PRD文档（必需）
- `design.md`：设计契约（必需）
- `PLAN.md`：执行计划（必需）

### 输出产物
- `PLAN.md`：执行计划（更新）（必需）

### Validators
- `plan_finished_tasks`
<!-- AUTOBIZDEVOPS_CONTRACT:END -->

# /autodev-code — 代码执行

## 阶段定位

`autodev-code` 只负责把 `/autodev-plan` 已确认的设计契约落成代码。

输入契约：
- `{工作目录}/PRD.md`：需求目标、边界、验收标准。
- `{工作目录}/design.md`：需求契约、行为规格、API Decisions、Data Decisions、Technical Design、风险与待确认项。
- `{工作目录}/PLAN.md`：任务 DAG、任务详情、涉及文件、验证方法、覆盖矩阵。
- `AGENTS.md`：项目级工程约束；如与本技能冲突，以 AGENTS.md 为准，除非系统级指令另有要求。

输出契约：
- 业务代码/测试/配置的最小必要修改。
- `{工作目录}/PLAN.md` 中任务状态和验证证据更新。
- `.autobizdevops/state.json` 中当前 feature checkpoint 推进到 `code_done`。

不得修改：
- `{工作目录}/PRD.md`
- `{工作目录}/design.md`
- 其他阶段报告文件

如果发现 `design.md` 与 PRD、代码现实或 PLAN 任务冲突，停止编码，报告需要回到 `/autodev-plan` 更新设计契约；不要在 code 阶段偷偷修设计。

## 准入检查

若 checkpoint 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

开始前必须确认当前 Feature 目录存在：

```text
.autobizdevops/features/{slug}/
```

必须读取：
- `.autobizdevops/features/{slug}/PRD.md`
- `.autobizdevops/features/{slug}/design.md`
- `.autobizdevops/features/{slug}/PLAN.md`
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
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint code_in_progress
```

## 执行协议

### 1. 建立执行上下文

从 `design.md` 抽取并记住：
- Behavior Specs：Requirement / Scenario。
- API Decisions：API-*、`x-auto-no-http-api`、接口入口、请求响应、错误处理、权限/租户/审计约束。
- Data Decisions：DATA-*、`x-auto-no-sql`、表/模型、字段、索引、迁移、回滚。
- Technical Design：D-*、模块边界、集成点、方案取舍。
- Risks / Open Questions：所有待确认项。

从 `PLAN.md` 抽取：
- 任务 DAG 和依赖顺序。
- 每个任务的「做什么」「设计依据」「涉及文件」「验证方法」「状态」。
- PRD 验收标准覆盖和设计决策覆盖。

如果某个待做任务引用的 `设计依据` 在 `design.md` 中不存在，停止并提示回到 Plan 修正。

如果某个待做任务依赖 design.md 中仍为「待确认」且会影响实现结果的事项，停止并提示用户确认或回到 Plan；不要基于猜测继续。

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
2. 读取任务列出的所有「涉及文件」。
3. 如果文件不存在，先用 `rg` / 项目结构定位真实路径；仍无法定位则停止并记录阻断，不要凭空创建疑似路径。
4. 对照 `design.md`：
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
9. 验证失败时

任务状态行必须保持形如：

```markdown
- **状态:** 待做 | 进行中 | 完成 | 失败
```

这样 hook 才能识别状态。

### 4. 全部任务完成后的验证

当 `PLAN.md` 中没有「待做」或「进行中」任务后：

1. 运行项目级验证命令。优先使用 AGENTS.md 或 PLAN.md 指定命令；没有明确命令时按项目类型选择最小验证。
2. Java/Maven 项目至少运行编译命令；`code_done` checkpoint 的前置 hook 会读取 `.autobizdevops/modules_compile.json` 并逐模块强制编译。
3. 如果验证失败，回到相关任务继续修复；不要推进 `code_done`。

验证通过后推进 checkpoint：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint code_done
```

## 写入边界

允许写入：
- `PLAN.md` 中当前任务状态、验证证据、失败原因。
- 当前任务「涉及文件」列出的业务代码、测试、配置。
- 为完成当前任务必须新增的文件，但必须能追溯到 PLAN.md 和 design.md 的设计依据。

禁止写入：
- `PRD.md`
- `design.md`
- `REQUIREMENTS_EVAL.md`
- `UNIT_TEST_REPORT.md`
- `E2E_REPORT.md`
- `VERIFY_REPORT.md`
- 与当前任务无关的业务文件。

如果为了完成任务必须修改 PLAN.md 未列出的业务文件，先确认该文件与 design.md 的集成点一致，再把文件追加到当前任务「涉及文件」或验证记录中；不要悄悄扩大范围。

## 完成条件

- `PLAN.md` 中所有任务状态均为「完成」。
- 若存在「失败」任务，本 skill 不算完成，不得推进 `code_done`，必须说明阻断和建议回流阶段。
- 所有必要验证通过。
- 项目编译通过或外部 checkpoint 编译校验通过。
- `.autobizdevops/state.json` 中当前 feature checkpoint 为 `code_done`。

**Skill 完成。** 下一步：`/autodev-reviewer --feature {slug}`
