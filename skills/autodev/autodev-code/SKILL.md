---
name: autodev-code
description: Dev 阶段逐任务代码执行。
version: v1.1.1604
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（执行清单）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，取当前 Feature 的执行清单：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```

- **逐条执行**：`## 输入产物` 下每个 input 只有一行确定指令，按序执行即可，不需要自己判断产物是否存在或该走哪个分支。
- **已生成**：按其 `读取方式` 读原件并纳入上下文；`读取方式` 是该 input 在场时的专属指令，优先于技能正文的通用默认。
- **未生成**：按其 `缺失处理` 执行——必需 input 停止并回流上游补齐；可选 input 按其降级动作继续，不因缺失而停止。
- **不列即不存在**：清单未列出的 id 不属于本 workflow 的正式流程产物 input，不要把它当作上游阶段产物读取、等待或索要。
- **适用边界**：上一条只约束正式流程产物 input；不限制用户本轮直接提供的材料、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 或技能正文明确要求读取的辅助素材。
- **输出与校验**：`## 输出产物` 是本节点应产出的产物；`## Validators`/`## Guards` 是推进 checkpoint 的校验项。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线清单（此时按 `读取方式` 预览，不含产物状态）。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


# /autodev-code — 代码执行

## 阶段定位

把上游确认的契约落成代码。输入/输出/读取方式以「流程契约」一节取到的契约为唯一事实源。

**核心：** 你的 input 就是执行清单 `## 输入产物` 里列出的那几个，逐个**按其 `读取方式`**执行；各 input 的角色、优先级、冲突回流去向都写在它自己的读取方式里。清单没列的 id 不属于本工作流——不读、不等、不索要，也不要设想"如果有 X"。**每个 input 的读取方式优先于本文通用默认。** 若清单含 `plan.json`，任务 DAG、依赖、状态与 evidenceIds 一律以 `plan.json` 为事实源；`PLAN.md` 若存在只作为人类可读视图按需同步维护，不参与机器判断。

输出：业务代码 / 测试 / 配置的最小必要修改；刷新后的 `CHECKPOINT` 推进到 `code_done`。

补充上下文（存在即读，非契约硬依赖）：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/DETAIL_DESIGN.md`、`AGENTS.md`（与本技能冲突时以 AGENTS.md 为准，除非系统级指令另有要求）。

## 准入检查

确定 `{slug}` 后，先读快照并捕获 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

`CHECKPOINT` 为空、未知→停止并请用户选择。确认 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/` 存在。

读取输入：按「流程契约」取执行清单，`## 输入产物` 逐项读原件、按各自 `读取方式` 抽取上下文。仅当标『未生成』的必需 input 存在时才停止，不要生成替代文件。

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

对每个 input 按其 `读取方式` 抽取并记住关键信息。

- 使用`write_todos`，把 `plan.json`（如有） 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度。

### 2. 选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

### 3. 执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。先依各 input 的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。
3. 改代码前做有界探索定位真实文件与既有模式：只读契约 input、AGENTS.md 指向的或 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 行为满足各 input 读取方式确立的行为契约条目（读取方式已标明何者为最高依据）。
   - 遵守各 input 读取方式施加的约束。
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（存在 `plan.json` 时优先 `plan.json.tasks[].validationCommands`；缺失或契约未列出 `plan.json` 时，基于 specs、AGENTS.md 和项目脚本选最小可行验证）。每次验证完成后用 `hooks/evidence_store.py append` 向 `evidence/EVIDENCE.jsonl` 末尾追加一条 evidence，记录 taskId（无 plan 时使用本轮轻量任务 ID）、specRefs、designRefs（无 design 契约时可为空）、changedFiles、validation.command/exitCode/result；`ev_XXXX` 按全流顺序自动递增，不按阶段重排，不得插入旧记录前、重编号、截断、重写、删除 `EVIDENCE.index.json` 后重建或手动修改 `EVIDENCE.index.json`。若 append 或 checkpoint 报 `evidence_stream_rewritten_or_truncated` / `missing_evidence_index_for_nonempty_stream`，必须恢复被改写前的 `EVIDENCE.jsonl` / `EVIDENCE.index.json`，无法恢复时停止并向用户报告。通过 → 状态「完成」；存在 `plan.json` 时还要将新增 evidenceId 写回 `plan.json.tasks[].evidenceIds`，`PLAN.md` 若存在再同步人类视图。失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。

> 一致性：任务的依据在对应 input 里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

### 4. 全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（优先 AGENTS.md / 契约指定命令；Java/Maven 至少编译），并追加项目级 validation evidence。失败回到相关任务，不推进。通过后：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件；各 input 读取方式指示你更新的产物。

禁止：**执行清单列出的任何 input**（凡在清单中即只读）；本节点未在 `board_core/board_config.json` outputs 中声明的其他阶段产物；与当前任务无关的业务文件。

为完成任务必须改队列未直接提到的业务文件时，先确认与各 input 读取方式确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 若 Source Bundle 含 `plan.json`：`plan.json` 中所有任务为完成态，每个任务至少有一条通过的 evidence；若 Source Bundle 未列出 `plan.json`：本轮轻量任务队列全部完成，并在 evidence 中记录对应 specs/proposal 依据。
- `evidence/EVIDENCE.jsonl` 与 `evidence/EVIDENCE.index.json` 完整性校验通过，不存在截断、重写、重排、重编号或 index 缺失绕过。
- 必要验证通过；项目编译通过（code_done execute hook 会在推进前再次校验 plan/evidence 闭环与模块编译）。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。** 下一步以 `resolve_next_skill.py` 为准（不假设固定下一技能）：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py"
```
