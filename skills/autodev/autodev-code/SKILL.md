---
name: autodev-code
description: 进行代码实现。
version: v1.3.0705
---

# /autodev-code — 代码执行

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

## 准入检查

先获取当前 checkpoint：

```bash
python "${pluginPath}/read_state_json.py" --feature "{feature}"
```


开始任何业务代码修改前，根据项目 manifest 生成模块编译清单 `.autobizdevops/modules_compile.json`：

```json
{
  "version": 1,
  "modules": [
    { "module": "root", "path": "/absolute/path/to/code/module", "compile_command": "mvn compile" }
  ]
}
```

识别规则：优先遵守已声明的构建方式；否则按项目 manifest 的单/多模块入口生成；`path` 用模块目录绝对路径，`compile_command` 以该目录为 cwd 执行（命令本身不要再写 `cd`）；无法确定时停止并询问用户，不得开始编码。
## 缺失产物处理
```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```

## 入场校验（有 PLAN.md 时必须先跑）

```bash
python "${pluginPath}/skills/autodev/hooks/plan_execution_check.py" "${feature}"
```

- `PASS` → 继续。
- 其他 `FAIL`（引用缺失 / DAG 非法 / design 决策未被任务覆盖）→ 停止，展示错误项，提示回 `/autodev-plan` 修 PLAN；不得跳过或自行改写引用。`uncovered_design_decision` 表示 design.md 的某个 API/DATA/D 决策没有任何任务的「设计依据」引用它，必须回 plan 补任务或在 Contract Coverage 标注「无需实现:<理由>」。
- `LEGACY_PLAN_DEGRADE`→ 继续，但仍按下方协议装载 PLAN 的任务，不得重拆。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
```

## 执行协议

### 装载任务队列（PLAN.md 存在时）

**队列即 PLAN，禁止重拆。** 把 PLAN.md 任务总览中的全部 `TASK-NNN` 按 DAG 顺序 1:1 镜像到 `write_todos`，不得合并、拆分、重排、增删任务。每次状态变化先回写 PLAN.md，再同步 todo，两边任何时刻保持一致。

- 认为 PLAN 拆分不合理（过粗/过细/依赖错）不是重拆的理由，停下向用户说明，经确认后修订 PLAN.md（修订处注明「code 阶段修订」）再装载。
- 仅当缺失产物处理显示本工作流不含 PLAN.md时，才允许 fallback：自建 2–5 个需求闭环任务队列，每个含 做什么 / 依据（指向对应 input 中的具体条目）/ 验证方法，队列与状态记录在完成摘要。

### 选择下一个任务

按 PLAN.md 状态选择：跳过「完成」；优先恢复「进行中」；否则按 DAG 顺序取第一个依赖任务全部「完成」的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，同一时刻至多一个任务「进行中」。

### 执行单个任务

1. PLAN.md 中该任务状态置「进行中」，todo 同步。
2. **展开任务执行包**：读任务的 做什么 / 各依据字段，并把每个引用展开到源文，禁止只凭任务名和「做什么」开工
   - `REQ-…` / `SCN-…` → specs 中对应 Requirement / Scenario 全文（要实现的行为与验收口径）；
   - `API-…` / `DATA-…` / `D-…` → design.md 对应决策行（**必须遵循**）；
   - `EVD-…` → design.md Code Evidence 对应行（现有代码入口与事实，探索的起点）；
   - `DETAIL_DESIGN.md` 存在时 → 该任务相关章节的文件级修改方案。
   汇成执行包：做什么 / 行为与验收 / 技术决策 / 代码事实 / 预计修改文件 / 验证命令与预期结果。
3. 改代码前做有界探索定位真实文件与既有模式：只读 PLAN.md、design.md、DETAIL_DESIGN.md（如有）、执行包引用的源文与 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；对照执行包核实「预计修改文件」，形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加注释。
6. **实现差异协议**：实现中遇到以下任一情况，停下用 `request_user_input` 单次确认，展示「design/spec 说 X，代码/实现是 Y」，拿到裁定前不得继续：
   - **EVD/design 与代码现实不符**→ 裁定后回写 design.md 对应行（注明「code 阶段修订」）再继续；
   - **必须偏离 API/DATA/D 已定形态**（定的做不了或明显更差）→ 同上，偏离经裁定回写 design.md 后才可按新形态实现；「实现细节自由度」不覆盖已定的接口/数据/技术决策形态；
   - **实现将违反 REQ/SCN 行为契约** → 该任务状态置「失败」、记原因与建议回流阶段（specs/plan），不得实现一个违反行为契约的版本。
7. 执行任务「验证方法」（缺失则基于 系统约束 / 项目脚本选最小可行验证并记回任务）：
   - 通过 → PLAN.md 就地回写：状态「完成」+ **完成记录**（验证命令与关键输出摘要 / 改动文件清单 / 如有 commit 则附 hash）。**先回写 PLAN.md，再进入下一个任务**；无完成记录不得置「完成」。
   - 失败 → 代码问题就继续最小修复重跑；环境/依赖/需求不清/契约冲突则停止，PLAN.md 状态置「失败」、记原因与建议回流阶段。

### 全部任务完成后的验证

如果task工具可用，使用task工具，先从git cache中获取当前改动的代码，对照 PLAN.md 与 design.md 同时审查三个方面：
1. 使用explore-autodev角色，逐 TASK 对照「做什么 / 规格依据 / 设计依据」核对 diff：每个任务的改动是否兑现其引用的 REQ/SCN 行为与 API/DATA/D 形态，有无未覆盖项、有无越界改动；
2. 使用code-reviewer-autodev角色，查看代码是否有不满足设计与需求的地方；
3. 使用code-simplifier-autodev角色，代码是否有冗余或不合理的地方。
如任一子代理返回有问题，则需要修复代码。
如果task不可用则不用执行上面的内容。继续任务。

PLAN.md 队列无「待做」「进行中」后，进行编译验证（优先 系统约束 ）。失败回到相关任务，不推进。通过后先回填领域词汇表锚点：会话工作区 `CONTEXT.md` 中锚点为「规划中」且本轮已落地的词条，回填为实际类/表/枚举与相对路径（协议见 `${pluginPath}/skills/references/domain-context.md`；无该文件或无「规划中」词条则跳过）。再推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/配置；能追溯到任务依据与队列的新增文件；PLAN.md 的任务状态与完成记录就地回写（含经用户确认的 PLAN 修订）；design.md 中经实现差异协议裁定后的对应行修订（注明「code 阶段修订」）；会话工作区 `CONTEXT.md` 的领域词汇表锚点回填。

为完成任务必须改队列未直接提到的业务文件时，先确认与各输入产物确立的依据一致，再把文件与原因记入该任务的完成记录，不要悄悄扩大范围。

## 完成条件

- PLAN.md 所有任务「完成」，且每个「完成」任务的完成记录非「无」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- design.md 每个 API/DATA/D 决策在 PLAN.md 中有对应实现任务或「无需实现」标注。
- 必要验证通过；项目编译通过。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
