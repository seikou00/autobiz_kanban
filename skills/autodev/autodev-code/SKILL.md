---
name: autodev-code
description: 按工作流契约逐任务执行代码，并在 code 阶段内部处理可选前端 HTML 实现分支。消费契约 Source Bundle 列出的正式流程产物 input，逐个按其 Method Bundle 执行（input 专属指令优先于通用默认）；契约未列出的 id 不作为上游阶段产物读取或索要，但不阻止用户直供 HTML/DOM 素材和内部 route SKILL。做最小实现、逐任务验证，全部完成后推进 code_done。支持中断恢复、--feature 多人协作。
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

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `{PLUGIN_ROOT}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-code --feature "{FEATURE_ID}" --json
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

**核心：** 你的正式流程产物 input 就是契约 Source Bundle 里列出的那几个，逐个**按其 Method Bundle（focus/method）**执行；各 input 的角色、优先级、冲突回流去向都写在它自己的 method 里。契约没列的 id 不作为上游阶段产物读取、等待或索要。用户本轮直接提供的 HTML/DOM/设计导出稿、`{FEATURE_DIR}/frontend-html/` 素材、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 不受这条排除规则限制。**每个 input 的 method 优先于本文通用默认。**

输出：业务代码 / 测试 / 配置的最小必要修改；刷新后的 `CHECKPOINT` 推进到 `code_done`。

补充上下文（存在即读，非契约硬依赖）：`{FEATURE_DIR}/DETAIL_DESIGN.md`、`{CODE_WORKSPACE}/AGENTS.md`（与本技能冲突时以 AGENTS.md 为准，除非系统级指令另有要求）。

## 前端 HTML 实现分支

HTML 转前端已经并入 `/autodev-code`。它不是独立 workflow 节点，也不再产生 `frontend_in_progress` / `frontend_done` checkpoint；完成后仍按本技能统一收尾推进到 `code_done`。本分支只处理 HTML/DOM/设计导出稿到真实工程代码的实现方式，正式流程输入、行为边界与回流规则仍以本 Feature 的 Source Bundle + Method Bundle 为准。
本分支不要求 Source Bundle 中存在 `frontend_html`；用户本轮直接提供的 HTML/DOM 素材、`{FEATURE_DIR}/frontend-html/` 素材和内部 route SKILL/deps 由本文的前端 Route 强制闸门管理。

触发条件（任一满足即进入本分支）：

- `UI_CONTEXT.json` 中 `uiRequired=true`，或当前 plan task 中 `uiRequired=true`。
- `PLAN.md` / specs / 用户本轮任务明确要求根据 HTML、DOM 片段、设计导出 HTML 实现前端页面。
- 用户本轮直接粘贴或提供了可读取的 HTML/DOM 片段、设计导出稿或静态页面素材。

总优先级：

1. UI 范围以 `UI_CONTEXT.json` 为最高机器事实源。
2. 行为契约以 `specs/**/*.md` 为最高依据。
3. 技术边界以 `design.md` 与 `plan.json` 为实现依据。
4. HTML/DOM/设计导出稿只提供页面结构、视觉布局、组件槽位、文案内容和交互线索，不得覆盖 UI_CONTEXT/specs/design/plan.json。
5. PRD / specs / plan.json 与 HTML 同时存在时：业务字段、文案、交互和任务边界以流程契约为准；布局、结构、间距、视觉层级以 HTML 为准。
6. 如果 route=`missing-html`，停止并要求补充 HTML 或回到上游修改 UI_CONTEXT/plan；如果 route=`spec-driven-ui`，按 specs/design/plan 直接实现，不得假装读取 HTML。

内部分流：

HTML 分流规则：

| 输入形态 | 路线 |
| --- | --- |
| 标准 DOM、语义结构清晰、`form` / `table` / `button` / `label` / flex / grid / class 规则明显 | `deps/frontend-html/with-standard-html/SKILL.md` |
| 普通静态 HTML、复制 DOM、小型静态站点、HTML 转 React，且页面主体不是绝对定位碎片结构 | `deps/frontend-html/with-standard-html/SKILL.md` |
| 高保真 HTML、Figma/MasterGo/低代码导出稿、坐标稿、碎片 div、页面主体或关键分区由 `position:absolute` / `left/top` / 固定像素尺寸主导 | `deps/frontend-html/with-absolute-html/SKILL.md` |
| 高保真但绝对定位仅局部、稀疏、装饰性存在，整体仍以标准 DOM / flex / grid 为主 | `deps/frontend-html/with-standard-html/SKILL.md` |
| 有 UI 任务但没有 HTML/设计稿输入 | `spec-driven-ui`，按 specs/design/plan 直接实现 |

高保真 / 绝对定位强信号（命中且主导页面主体、关键分区或多个视觉块时，必须走 absolute 路线）：

- 用户明确标注“高保真 HTML”“设计导出 HTML”“Figma/MasterGo 导出”“绝对定位”“纯坐标还原稿”。
- 大量 `position:absolute`、`left/top`、固定像素宽高、`clip-path`、`data:image/svg+xml`、渐变、阴影。
- 页面主体由碎片 `div`、梯形块、迷你趋势图、像素级卡片矩阵、复杂壳层布局组成。

组件、图标与图表来源：

1. 先读 `{CODE_WORKSPACE}/AGENTS.md` 与项目说明，再扫真实源码；项目约束优先于本分支默认规则。
2. 组件来源优先级：AGENTS.md 指定公共组件库 -> `architecture/components` -> 项目本地组件 -> 已安装且真实使用的组件库 -> 用户提供兜底组件库 -> 相似页面 -> fidelity-only。
3. 图标来源优先级：项目图标规则 -> 本地 icon/svg/iconfont -> 已安装且真实使用的图标库 -> React + AntD `@ant-design/icons` 兜底；纯图标按钮补 `Tooltip` 与 `aria-label`。
4. 图表必须使用真实图表组件或图库实现；优先项目图表规则 / 本地图表组件 / 已安装且真实使用的图库，缺证据时按任务约束确认或默认 ECharts 兜底，不得用静态 SVG / CSS 图形假冒真实图表，除非用户明确只要静态占位。
5. 缺少需要新增的组件库、图标库或图表库时，按项目包管理器和用户确认流程处理；安装完成前不得把依赖相关能力标记为最终完成。

实现与收尾要求：

1. 先执行本文开头的「前端 Route 强制闸门」：运行 `resolve_frontend_html_route.py`，完整读取对应 route SKILL.md 到 EOF，再把该 route SKILL 中定义的 `write_todos` 主流程转成可见清单；未完成这一步，不得读取 parser、读取 HTML 或改前端代码。
2. 判断 HTML 路线并读取对应 SKILL：按上方内部分流规则选择 `with-standard-html/SKILL.md` 或 `with-absolute-html/SKILL.md`；最终 route 以 `{FEATURE_DIR}/FRONTEND_ROUTE.json` 为机器事实。
3. 标准 HTML 路线进入 `with-standard-html/SKILL.md` 后，必须按其 `write_todos` 完成路线判定、页面模块、转换、Ant Design 审计四类清单，并带 `routeType`、`absoluteSignalsCleared`、`moduleTodosReady`、`conversionTodosReady`、`uiLibraryTarget`、`antdMode`、`auditRequired` 交接状态转给 `deps/standard-html-parser.md`。
4. 绝对定位高保真路线进入 `with-absolute-html/SKILL.md` 后，必须按其 `write_todos` 完成页面模块清单、独立脚本清单、上下文读取与 `deps/html-parser.md` 转交；脚本清单至少覆盖参数确认、执行脚本、检查 `.frontend/html-analysis/<task-stem>.*` 产物、失败降级。脚本异常不阻塞主流程，降级后以原始 HTML 为唯一视觉源继续。
5. 主线结束前必须做样式细节收尾，补齐 padding、边框、圆角、阴影、字色、字号、字重、行高、内外边距、对齐、状态色、文本内容、hover / active / selected 等用户一眼能看出的差异。
6. 主线里完成页面拆分，以及函数、常量、类型、helper / hook、图表配置与同页公共内容抽取；不要把明显的可维护性工作留给后续 `/autodev-reviewer`。
7. 执行本分支验证，确认已回到 `/autodev-code` 主流程后，再按本文件的「执行协议」与「完成条件」收尾；在显式完成“回到 `/autodev-code` 主流程并按 code 节点收尾”前，不得把本分支视为完成。

分支返回契约：两个 HTML 路线完成后都必须返回 `/autodev-code` 主流程，由 code 根技能继续项目级验证、统一前端回检、`.autobizdevops/modules_compile.json` 编译清单校验和 `code_done` checkpoint 推进。HTML 分支内部不得发起独立回检选择，不得调用或引用已移除的 `autodev-frontend`、`frontend_done` 或内部回检路线。返回主流程时必须带回回检输入：生成/修改的目标源码路径、原始 HTML 路径、可用的 `.frontend/html-analysis/*.json` 路径（没有则写 none）、`PLAN.md` 路径（没有则写 none）、`uiLibraryTarget`、`antdMode`、`auditRequired`。

## 准入检查

确定 `{slug}` 后，先读快照并捕获 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

`CHECKPOINT` 为空、未知或无法唯一确定当前 Feature → 停止并请用户选择。确认 `{FEATURE_DIR}/` 存在。

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
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## 执行协议

### 1. 建立执行上下文与任务队列

对每个 input 按其 `extract.focus` / `method` 抽取并记住关键信息。

**任务队列：** 若 Source Bundle 含 `plan.json`，必须读取它，并直接按 `tasks[]` 中的 `deps/status/specRefs/designRefs/apiIds/dataIds/decisionIds/validationCommands/evidenceIds` 建立队列，不得重新拆分任务；更新任务时必须修改 `plan.json`，`PLAN.md` 若存在只作为人类视图同步。若当前 workflow 的 Source Bundle 未列出 `plan.json`（如 lean/custom 链已从契约中移除），不得回到 Plan 阶段索要它；基于 `proposal.md` 与 `specs/**/*.md` 建立本轮轻量任务队列，并在完成摘要和 evidence 中记录任务依据。
- 若当前运行模式支持 `write_todos`，把任务队列映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败；存在 `plan.json` 时与 `plan.json` 保持同步，每次只置一个任务为"进行中"。`write_todos` 只反映任务进度，不替代 checkpoint 脚本与产物校验。
（依"方法优先"：若某 input 的 method 给了更具体读写要求，按其指示执行。）

### 2. 选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

### 3. 执行单个任务

1. 任务状态置「进行中」，保留原内容（如启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。先依各 input 的 method 确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（method 优先于此默认）。
3. 改代码前做有界探索定位真实文件与既有模式：只读契约 input、AGENTS.md 指向的或 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 行为满足各 input method 确立的行为契约条目（method 已标明何者为最高依据）。
   - 遵守各 input method 施加的约束。
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（存在 `plan.json` 时优先 `plan.json.tasks[].validationCommands`；缺失或契约未列出 `plan.json` 时，基于 specs、AGENTS.md 和项目脚本选最小可行验证）。每次验证完成后用 `hooks/evidence_store.py append` 追加一条 evidence，记录 taskId（无 plan 时使用本轮轻量任务 ID）、specRefs、designRefs（无 design 契约时可为空）、changedFiles、validation.command/exitCode/result；不要截断或重写 `evidence/EVIDENCE.jsonl`。通过 → 状态「完成」；存在 `plan.json` 时还要将新增 evidenceId 写回 `plan.json.tasks[].evidenceIds`，`PLAN.md` 若存在再同步人类视图。失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。

> 一致性：任务的依据在对应 input 里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

### 4. 全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（优先 AGENTS.md / 契约指定命令；Java/Maven 至少编译）。失败回到相关任务，不推进。

如本轮触发 HTML 分支，或变更了前端源码（`.tsx` / `.jsx` / `.ts` / `.js` / `.vue` 及相关样式文件），项目级验证通过后必须运行统一前端回检；只有用户明确要求“跳过回检 / 先不回检 / 不要跑回检 / 先不验证”时才跳过，并在最终摘要写 `reviewStatus=skipped-by-user`。默认命令：

```bash
python "{PLUGIN_ROOT}/skills/autodev/autodev-code/deps/frontend-html/scripts/review_runner.py" --target "<file-or-dir>" --antd-audit auto --format markdown
```

- `--target` 指向本轮生成/修改的前端页面、组件文件或包含它们的目录；`--source-html`、`--analysis`、`--plan` 只在对应文件真实存在时追加，标准 HTML 路线没有 analysis JSON 时不要传 `--analysis`。
- 退出码 `0`：回检通过，记 `reviewStatus=passed`。
- 退出码 `1`：读取 findings；`must-fix` 是阻塞项，按最小修复同轮重跑，默认最多 2 轮；仅剩 `suggestion` 时允许推进，但必须记录 `reviewStatus=has-suggestions` 和建议项。
- 退出码 `2`：回检执行异常，记 `reviewStatus=failed`，不得声称完整验证通过，不得推进 `code_done`，除非用户明确选择跳过回检。
- 本回检只属于 code 阶段前端生成质量自检，不替代后续 `/autodev-reviewer` 的独立需求实现评审。

项目级验证与必要的统一前端回检均收敛后：

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件；各 input method 指示你更新的产物。

禁止：**Source Bundle 中的任何 input**（凡在 bundle 中即只读）；本节点未在 `board_core/board_config.json` outputs 中声明的其他阶段产物；与当前任务无关的业务文件。

为完成任务必须改队列未直接提到的业务文件时，先确认与各 input method 确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 若 Source Bundle 含 `plan.json`：`plan.json` 中所有任务为完成态，每个任务至少有一条通过的 evidence；若 Source Bundle 未列出 `plan.json`：本轮轻量任务队列全部完成，并在 evidence 中记录对应 specs/proposal 依据。
- `evidence/EVIDENCE.jsonl` 与 `evidence/EVIDENCE.index.json` 完整性校验通过，不存在截断/重写。
- 必要验证通过；项目编译通过（code_done execute hook 会在推进前再次校验 plan/evidence 闭环与模块编译）。
- HTML 分支或前端源码变更已完成统一前端回检，或用户明确跳过；仍有 `must-fix` / 执行异常时不得推进 `code_done`。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。** 下一步以 `resolve_next_skill.py` 为准（不假设固定下一技能）：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_next_skill.py" --workspace "{PLUGIN_WORKSPACE}/{PROJECT_CODE}" --feature "{FEATURE_ID}"
```
