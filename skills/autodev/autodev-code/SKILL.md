---
name: autodev-code
description: 按工作流契约逐任务执行代码，并在 code 阶段内部处理可选前端 HTML 实现分支。消费契约 Source Bundle 列出的正式流程产物 input，逐个按其 Method Bundle 执行（input 专属指令优先于通用默认）；契约未列出的 id 不作为上游阶段产物读取或索要，但不阻止用户直供 HTML/DOM 素材和内部 route SKILL。做最小实现、逐任务验证，全部完成后推进 code_done。支持中断恢复、--feature 多人协作。
version: v1.2.0703
---

## 前端 Route 强制闸门（必须优先执行）

当本轮任务是前端代码生成、HTML/DOM/设计导出稿转工程代码，或触发「前端 HTML 实现分支」时，`/autodev-code` 不得自行改写成普通前端编码任务。必须先解析内部 route。UI 范围以 `UI_CONTEXT.json` 和 `plan.json.tasks[].uiRequired/uiRefs` 为机器事实源，Markdown 只作迁移兜底。

1. 推进到 `code_in_progress` 后，先解析并记录 route：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --write-evidence --json
```

如用户本轮直供了不在 `{FEATURE_DIR}/frontend-html/` 或流程文档中的 HTML 文件，追加 `--html-file "<HTML_PATH>"`，可重复。

2. 按输出的 `route` 读取 route SKILL 到 EOF：
   - `route=absolute-html`：完整读取 `skills/autodev/autodev-code/deps/frontend-html/with-absolute-html/SKILL.md`
   - `route=standard-html`：完整读取 `skills/autodev/autodev-code/deps/frontend-html/with-standard-html/SKILL.md`
   - `route=spec-driven-ui`：有 UI 任务但没有 HTML/设计稿输入，按 specs/design/plan 实现前端；不读取 HTML parser，不要求 route SKILL。
   - `route=none`：`UI_CONTEXT.json` 标记 `uiRequired=false`，不得写前端业务代码。
   - 如果读取工具返回截断内容，继续续读直到 EOF；未确认 `routeSkillReadComplete=true` 前，不得读取 parser、不得读取 HTML、不得写前端代码。

3. 把 route SKILL 中定义的 `write_todos` 主流程转成可见任务清单，逐项执行并更新状态，不能合并成一句“实现前端页面”。清单创建后立即记录机器证据：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-created --json
```

4. 只有 route SKILL 的清单推进到“转交 parser”步骤时，才能读取 parser：
   - `absolute-html` 只能由 `with-absolute-html/SKILL.md` 转交 `deps/html-parser.md`
   - `standard-html` 只能由 `with-standard-html/SKILL.md` 转交 `deps/standard-html-parser.md`
   - `/autodev-code` 根技能不得直接跳入 parser 文档。

5. route SKILL 的全部主流程清单完成后记录：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-completed --json
```

6. 统一前端回检后，把结果写入 `{FEATURE_DIR}/FRONTEND_ROUTE.json`：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --review-status passed --json
```

允许值：`passed`、`has-suggestions`、`skipped-by-user`、`failed`。`failed` 或未写明且未明确跳过时，`frontend_route_gate` 会阻断 `code_done`。

`{FEATURE_DIR}/FRONTEND_ROUTE.json` 是本闸门的机器证据。HTML 路线下，前端代码生成任务缺少该文件、route SKILL 未读完、route todos 未创建/未完成、parser 未读、回检未通过或未明确跳过时，不得推进 `code_done`。`spec-driven-ui` 不要求 HTML parser；`none` 不允许写前端业务代码。

## 缺失产物处理

```bash
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-code --feature "{FEATURE_ID}" --json
```


# /autodev-code — 代码执行

## 阶段定位

把上游确认的契约落成代码。输入/输出/读取方式以「流程契约」一节取到的契约为唯一事实源。

**核心：** 你的正式流程产物 input 就是契约 Source Bundle 里列出的那几个，逐个**按其 Method Bundle（focus/method）**执行；各 input 的角色、优先级、冲突回流去向都写在它自己的 method 里。契约没列的 id 不作为上游阶段产物读取、等待或索要。用户本轮直接提供的 HTML/DOM/设计导出稿、`{FEATURE_DIR}/frontend-html/` 素材、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 不受这条排除规则限制。**每个 input 的 method 优先于本文通用默认。**

输出：业务代码 / 测试 / 配置的最小必要修改；刷新后的 `CHECKPOINT` 推进到 `code_done`。

补充上下文（存在即读，非契约硬依赖）：`{FEATURE_DIR}/DETAIL_DESIGN.md`、`{CODE_WORKSPACE}/AGENTS.md`（与本技能冲突时以 AGENTS.md 为准，除非系统级指令另有要求）。

## 前端 HTML 实现分支

HTML 转前端已经并入 `/autodev-code`。它不是独立 workflow 节点，也不再产生 `frontend_in_progress` / `frontend_done` checkpoint；完成后仍按本技能统一收尾推进到 `code_done`。

触发条件（任一满足即进入本分支）：

- `PLAN.md` / specs / 用户本轮任务明确要求根据 HTML、DOM 片段、设计导出 HTML 实现前端页面。
- 用户本轮直接粘贴或提供了可读取的 HTML/DOM 片段、设计导出稿或静态页面素材。

执行优先级：

1. 行为契约以 `specs/**/*.md` 为最高依据。
2. 技术边界以 `design.md` 与 `PLAN.md` 为实现依据。
3. HTML/DOM/设计导出稿只提供页面结构、视觉布局、组件槽位与交互线索，不得覆盖 specs/design/PLAN。
4. 如果任务明确要求 HTML 转换但没有可读取 HTML/DOM/静态素材，停止并要求补充；如果任务可由 specs/design/PLAN 直接实现且没有可读 HTML 素材，则跳过本分支。

内部分流：

1. 先建立本分支任务队列。若当前运行模式支持 `write_todos`，必须先把本分支主线写成可见清单，再读取 route SKILL/deps 或改代码；未完成这一步，不得进入后续分流。清单至少覆盖：`判断 HTML 路线并读取对应 SKILL` / `完成 HTML 解析与页面结构还原` / `映射真实工程组件、样式与交互` / `执行分支验证并回到 /autodev-code 主流程`；可按实际任务细化，但不得缺项、不得只放在脑内。若当前 code 主队列已存在更细任务，可继续沿用，但必须确保上述 4 类动作在同一份清单里都有对应条目。若不支持 `write_todos`，仍需在完成摘要维护同顺序队列与状态。状态规则沿用下文「执行协议」：每次只允许一个“进行中”，完成或失败后立即同步。
2. 判断 HTML 路线并读取对应 SKILL：
   - 绝对定位 / 高保真 / Figma 或低代码导出的 HTML：读取 `deps/frontend-html/with-absolute-html/SKILL.md`，再按其 `deps/html-parser.md` 执行。
   - 标准 DOM / 语义结构清晰 / 普通静态 HTML / HTML 转 React：读取 `deps/frontend-html/with-standard-html/SKILL.md`，再按其 `deps/standard-html-parser.md` 执行。
3. 沿选中路线完成 HTML 解析、页面结构还原，以及真实工程组件、样式与交互映射。
4. 执行本分支验证，确认已回到 `/autodev-code` 主流程后，再按本文件的「执行协议」与「完成条件」收尾；在显式完成“回到 `/autodev-code` 主流程并按 code 节点收尾”前，不得把本分支视为完成。

该分支完成后回到本文件的「执行协议」与「完成条件」：更新真实业务代码、执行必要验证、按 code 节点规则推进。

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

**任务队列：** 必须读取契约提供的 `plan.json`，直接按 `tasks[]` 中的 `deps/status/specRefs/designRefs/apiIds/dataIds/decisionIds/validationCommands/evidenceIds` 建立队列，不得重新拆分任务；更新任务时必须修改 `plan.json`，`PLAN.md` 若存在只作为人类视图同步。若当前 workflow 未提供 `plan.json`，停止执行并回到 Plan 阶段补齐结构化计划，不得在 Code 阶段临时拆分或维护 Markdown 任务清单。
- 若当前运行模式支持 `write_todos`，把 `plan.json.tasks[]` 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败，并与 `plan.json` 保持同步；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度，不替代 checkpoint 脚本与产物校验。
（依"方法优先"：若某 input 的读取方式给了更具体读写要求，按其指示执行。）

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
   - 任务需要写 / 改测试时，遵循 `${pluginPath}/skills/references/test-quality.md`：站在 seam 上验证、期望值来自独立事实源（勿同义反复）、mock 只在系统边界。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（缺失则基于 AGENTS.md / 项目脚本选最小可行验证并记回任务）。通过 → 状态「完成」+ 记录验证；失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。

> 一致性：任务的依据在对应 input 里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

### 4. 全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（优先 AGENTS.md / 契约指定命令；Java/Maven 至少编译）。失败回到相关任务，不推进。通过后：

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
- 必要验证通过；项目编译通过（code_done execute hook 另记模块编译结果，非阻断）。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。** 下一步以 `resolve_next_skill.py` 为准（不假设固定下一技能）：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py"
```
