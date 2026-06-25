---
name: autodev-code
description: 按工作流契约逐任务执行代码。消费契约 Source Bundle 列出的 input，逐个按其 Method Bundle 执行（input 专属指令优先于通用默认）；契约未列出的 id 不属于本工作流，不读不等。做最小实现、逐任务验证，全部完成后推进 code_done。支持中断恢复、--feature 多人协作。
version: v1.1.1604
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）。
- **方法优先**：每个 input 的 `extract.method` 是它在场时的专属指令，优先于技能正文的通用默认。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止。
- **不列即不存在**：bundle 未列出的 id 不属于本 workflow 的正式流程产物 input，不要把它当作上游阶段产物读取、等待或索要。
- **适用边界**：上一条只约束正式流程产物 input；不限制用户本轮直接提供的材料、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 或技能正文明确要求读取的辅助素材。
- **降级语义**：`required: false` 的输入缺失时按其 `extract.degrade` 继续，不要因缺失而停止。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-code — 代码执行

## 阶段定位

把上游确认的契约落成代码。输入/输出/读取方式以「流程契约」一节取到的契约为唯一事实源。

**核心：** 你的 input 就是契约 Source Bundle 里列出的那几个，逐个**按其 Method Bundle（focus/method）**执行；各 input 的角色、优先级、冲突回流去向都写在它自己的 method 里。契约没列的 id 不属于本工作流——不读、不等、不索要，也不要设想"如果有 X"。**每个 input 的 method 优先于本文通用默认。** 若 Source Bundle 含 `plan.json`，任务 DAG、依赖、状态与 evidenceIds 一律以 `plan.json` 为事实源，`PLAN.md` 只作为人类可读视图同步维护。

输出：业务代码 / 测试 / 配置的最小必要修改；刷新后的 `CHECKPOINT` 推进到 `code_done`。

补充上下文（存在即读，非契约硬依赖）：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/DETAIL_DESIGN.md`、`AGENTS.md`（与本技能冲突时以 AGENTS.md 为准，除非系统级指令另有要求）。

## 准入检查

确定 `{slug}` 后，先读快照并捕获 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

`CHECKPOINT` 为空、未知→停止并请用户选择。确认 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` 存在。

读取输入：按「流程契约」取契约 JSON，按 `sourceBundle` 逐项读原件、按各自 `extract` 抽取上下文。仅当 `required_inputs` 缺失时停止，不要生成替代文件。

开始任何业务代码修改前，根据 AGENTS.md 与项目 manifest 生成模块编译清单 `.autobizdevops/modules_compile.json`：

```json
{
  "version": 1,
  "modules": [
    { "module": "root", "path": "/absolute/path/to/code/module", "compile_command": "mvn compile" }
  ]
}
```

识别规则：优先遵守 AGENTS.md 声明的构建方式；否则按项目 manifest 的单/多模块入口生成；`path` 用模块目录绝对路径，`compile_command` 以该目录为 cwd 执行（命令本身不要再写 `cd`）；无法确定时停止并询问用户，不得开始编码。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 执行协议

### 1. 建立执行上下文与任务队列

对每个 input 按其 `extract.focus` / `method` 抽取并记住关键信息。

**任务队列：** 若契约提供 `plan.json`，直接读取 `tasks[]`，按其中的 `deps/status/specRefs/designRefs/apiIds/dataIds/decisionIds/validationCommands/evidenceIds` 建立队列，不得重新拆分任务；更新任务时同步修改 `plan.json` 与 `PLAN.md` 视图。只有当前 workflow 未提供 `plan.json` 时，才把本轮变更拆成 2–5 个需求闭环任务并在完成摘要中维护。
- 若当前运行模式支持 `write_todos`，把 `plan.json.tasks[]` 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败，并与 `plan.json` 保持同步；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度，不替代 checkpoint 脚本与产物校验。
（依"方法优先"：若某 input 的 method 给了更具体读写要求，按其指示执行。）

### 2. 选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

### 3. 执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。先依各 input 的 method 确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（method 优先于此默认）。
3. 改代码前做有界探索定位真实文件与既有模式：只读契约 input、AGENTS.md 指向的或 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 行为满足各 input method 确立的行为契约条目（method 已标明何者为最高依据）。
   - 遵守各 input method 施加的约束。
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（优先 `plan.json.tasks[].validationCommands`；缺失则基于 AGENTS.md / 项目脚本选最小可行验证并记回任务）。每次验证完成后用 `hooks/evidence_store.py append` 追加一条 evidence，记录 taskId、specRefs、designRefs、changedFiles、validation.command/exitCode/result；不要截断或重写 `evidence/EVIDENCE.jsonl`。通过 → 状态「完成」+ 将新增 evidenceId 写回 `plan.json.tasks[].evidenceIds` 与 `PLAN.md`；失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。

> 一致性：任务的依据在对应 input 里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

### 4. 全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（优先 AGENTS.md / 契约指定命令；Java/Maven 至少编译），并追加项目级 validation evidence。失败回到相关任务，不推进。通过后：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件；各 input method 指示你更新的产物。

禁止：**Source Bundle 中的任何 input**（凡在 bundle 中即只读）；本节点未在 `board_core/board_config.json` outputs 中声明的其他阶段产物；与当前任务无关的业务文件。

为完成任务必须改队列未直接提到的业务文件时，先确认与各 input method 确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- `plan.json` 中所有任务为完成态，每个任务至少有一条通过的 evidence；`evidence/EVIDENCE.jsonl` 与 `evidence/EVIDENCE.index.json` 完整性校验通过，不存在截断/重写。
- 必要验证通过；项目编译通过（code_done execute hook 会在推进前再次校验 plan/evidence 闭环与模块编译）。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。** 下一步以 `resolve_next_skill.py` 为准（不假设固定下一技能）：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py"
```
