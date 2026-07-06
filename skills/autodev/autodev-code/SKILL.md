---
name: autodev-code
description: 按工作流契约逐任务执行代码，并在 code 阶段内部处理可选前端 HTML 实现分支。消费契约 Source Bundle 列出的正式流程产物 input，逐个按其 Method Bundle 执行（input 专属指令优先于通用默认）；契约未列出的 id 不作为上游阶段产物读取或索要，但不阻止用户直供 HTML/DOM 素材和内部 route SKILL。做最小实现、逐任务验证，全部完成后推进 code_done。支持中断恢复、--feature 多人协作。
version: v1.2.0703
---

## 前端 Route 强制闸门（必须优先执行）

当本轮任务是前端代码生成、HTML/DOM/设计导出稿转工程代码，或触发「前端 HTML 实现分支」时，`/autodev-code` 不得自行改写成普通前端编码任务。必须先解析内部 route。UI 范围以 `UI_CONTEXT.json` 和 `plan.json.tasks[].uiRequired/uiRefs` 为机器事实源，Markdown 只作迁移兜底。

1. 推进到 `code_in_progress` 后，先解析并记录 route：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --start-route-run --json
```

如用户本轮直供了不在 `{FEATURE_DIR}/frontend-html/` 或流程文档中的 HTML 文件，追加 `--html-file "<HTML_PATH>"`，可重复。

2. 按输出的 `route` 读取 route SKILL 到 EOF：
   - `route=absolute-html`：完整读取 `skills/autodev/autodev-code/references/frontend-html/with-absolute-html/SKILL.md`
   - `route=standard-html`：完整读取 `skills/autodev/autodev-code/references/frontend-html/with-standard-html/SKILL.md`
   - `route=spec-driven-ui`：有 UI 任务但没有可读取的 HTML/设计稿输入，按 specs/design/plan 实现前端；不读取 HTML parser，不要求 route SKILL。如果输出含 `htmlSourceMissing=true` / `htmlRequestMessage`，先按提示引导用户提供 HTML；用户不提供时，不阻断本阶段，继续按无高保真流程实现。
   - `route=none`：`UI_CONTEXT.json` 标记 `uiRequired=false`，不得写前端业务代码。
   - 如果读取工具返回截断内容，继续续读直到 EOF；未确认 `routeSkillReadComplete=true` 前，不得读取 parser、不得读取 HTML、不得写前端代码。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-skill-read-complete --json
```

3. 把 route SKILL 中定义的 `write_todos` 主流程转成可见任务清单，逐项执行并更新状态，不能合并成一句“实现前端页面”。清单创建后立即记录机器证据：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-created --json
```

4. 只有 route SKILL 的清单推进到“转交 parser”步骤时，才能读取 parser：
   - `absolute-html` 只能由 `with-absolute-html/SKILL.md` 转交 `references/html-parser.md`
   - `standard-html` 只能由 `with-standard-html/SKILL.md` 转交 `references/standard-html-parser.md`
   - `/autodev-code` 根技能不得直接跳入 parser 文档。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark parser-read --json
```

5. route SKILL 的全部主流程清单完成后记录：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-completed --json
```

6. 统一前端回检后，把结果写入 `{FEATURE_DIR}/FRONTEND_ROUTE.json`：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --review-status passed --json
```

允许值：`passed`、`has-suggestions`、`skipped-by-user`、`failed`。`failed` 或未写明且未明确跳过时，`frontend_route_gate` 会阻断 `code_done`。

`{FEATURE_DIR}/FRONTEND_ROUTE.json` 是本闸门的机器证据。HTML 路线下，前端代码生成任务缺少该文件、route SKILL 未读完、route todos 未创建/未完成、parser 未读、回检未通过或未明确跳过时，不得推进 `code_done`。`spec-driven-ui` 不要求 route SKILL / HTML parser，但仍必须完成统一前端回检并写入 `reviewStatus`；`none` 不允许写前端业务代码。

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```


# /autodev-code — 代码执行

## 阶段定位

把上游确认的契约落成代码。输入/输出/读取方式以「流程契约」一节取到的契约为唯一事实源。

**核心：** 你的 input 就是执行清单 `## 输入产物` 里列出的那几个，逐个**按其 `读取方式`**执行；各 input 的角色、优先级、冲突回流去向都写在它自己的读取方式里。清单没列的 id 不属于本工作流——不读、不等、不索要，也不要设想"如果有 X"。用户本轮直接提供的 HTML/DOM/设计导出稿、`{FEATURE_DIR}/frontend-html/` 素材、代码工作区上下文、AGENTS.md、内部 route SKILL/references 不受这条排除规则限制。**每个 input 的读取方式优先于本文通用默认。** 若清单含 `plan.json`，任务 DAG、依赖、状态与 evidenceIds 一律以 `plan.json` 为事实源；`PLAN.md` 若存在只作为人类可读视图按需同步维护，不参与机器判断。

输出：业务代码 / 测试 / 配置的最小必要修改；刷新后的 `CHECKPOINT` 推进到 `code_done`。

补充上下文（存在即读，非契约硬依赖）：`{FEATURE_DIR}/DETAIL_DESIGN.md`、`{CODE_WORKSPACE}/AGENTS.md`（与本技能冲突时以 AGENTS.md 为准，除非系统级指令另有要求）。

## 前端 HTML 实现分支

HTML 转前端已经并入 `/autodev-code`。它不是独立 workflow 节点，也不再产生 `frontend_in_progress` / `frontend_done` checkpoint；完成后仍按本技能统一收尾推进到 `code_done`。本分支只处理 HTML/DOM/设计导出稿到真实工程代码的实现方式，正式流程输入、行为边界与回流规则仍以本 Feature 的 Source Bundle + Method Bundle 为准。
本分支不要求 Source Bundle 中存在 `frontend_html`；用户本轮直接提供的 HTML/DOM 素材、`{FEATURE_DIR}/frontend-html/` 素材和内部 route SKILL/references 由本文的前端 Route 强制闸门管理。

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
6. 如果前面阶段声明了高保真/HTML 但 resolver 输出 `htmlSourceMissing=true`，先向用户说明缺失路径并请求提供 HTML；若用户本轮不提供，则按 `spec-driven-ui` 的无高保真流程继续，不等待、不阻断，也不得假装读取 HTML。

内部分流：

HTML 分流规则：

| 输入形态 | 路线 |
| --- | --- |
| 标准 DOM、语义结构清晰、`form` / `table` / `button` / `label` / flex / grid / class 规则明显 | `references/frontend-html/with-standard-html/SKILL.md` |
| 普通静态 HTML、复制 DOM、小型静态站点、HTML 转 React，且页面主体不是绝对定位碎片结构 | `references/frontend-html/with-standard-html/SKILL.md` |
| 高保真 HTML、Figma/MasterGo/低代码导出稿、坐标稿、碎片 div、页面主体或关键分区由 `position:absolute` / `left/top` / 固定像素尺寸主导 | `references/frontend-html/with-absolute-html/SKILL.md` |
| 高保真但绝对定位仅局部、稀疏、装饰性存在，整体仍以标准 DOM / flex / grid 为主 | `references/frontend-html/with-standard-html/SKILL.md` |
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
3. 标准 HTML 路线进入 `with-standard-html/SKILL.md` 后，必须按其 `write_todos` 完成路线判定、页面模块、转换、Ant Design 审计四类清单，并带 `routeType`、`absoluteSignalsCleared`、`moduleTodosReady`、`conversionTodosReady`、`uiLibraryTarget`、`antdMode`、`auditRequired` 交接状态转给 `references/standard-html-parser.md`。
4. 绝对定位高保真路线进入 `with-absolute-html/SKILL.md` 后，必须按其 `write_todos` 完成页面模块清单、独立脚本清单、上下文读取与 `references/html-parser.md` 转交；脚本清单至少覆盖参数确认、执行脚本、检查 `.frontend/html-analysis/<task-stem>.*` 产物、失败降级。脚本异常不阻塞主流程，降级后以原始 HTML 为唯一视觉源继续。
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
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## 执行协议

### 建立执行上下文与任务队列

对每个 input 按其 `读取方式` 抽取并记住关键信息。

- 使用`write_todos`，把 `plan.json`（如有） 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度。

###  选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

###  执行单个任务

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
6. 执行任务「验证方法」（存在 `plan.json` 时优先 `plan.json.tasks[].validationCommands`；缺失或契约未列出 `plan.json` 时，基于 specs、AGENTS.md 和项目脚本选最小可行验证）。每次验证完成后用 `hooks/evidence_store.py append` 向 `evidence/EVIDENCE.jsonl` 末尾追加一条 evidence，记录 taskId（无 plan 时使用本轮轻量任务 ID）、specRefs、designRefs（无 design 契约时可为空）、changedFiles、validation.command/exitCode/result；`ev_XXXX` 按全流顺序自动递增，不按阶段重排，不得插入旧记录前、重编号、截断、重写、删除 `EVIDENCE.index.json` 后重建或手动修改 `EVIDENCE.index.json`。若 append 或 checkpoint 报 `evidence_stream_rewritten_or_truncated` / `missing_evidence_index_for_nonempty_stream`，必须恢复被改写前的 `EVIDENCE.jsonl` / `EVIDENCE.index.json`，无法恢复时停止并向用户报告。通过 → 状态「完成」；存在 `plan.json` 时还要将新增 evidenceId 写回 `plan.json.tasks[].evidenceIds`，`PLAN.md` 若存在再同步人类视图。失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。
7. 若 Source Bundle 含 `SMOKE_TEST_PLAN.json`，按其中 `tests[]` 生成或补齐旁路冒烟测试源码/脚本。每条 smoke 必须按计划中的 `seam` 站在公开边界上验证，不测私有方法、不查内部实现细节；按 `verticalSlice` 一次只实现一个最小闭环，不把多个场景合成一条大烟测；按 `mockPolicy` 只 mock 系统边界，不 mock 自有模块或内部协作者。冒烟测试必须是 opt-in：Java/Spring 可用 `*SmokeIT` + `-Psmoke`，前端可用 `tests/smoke/` + 单独 smoke script，CLI/API 可用 `scripts/smoke/`；这些源码/脚本只用于本地验证，可以放在业务项目测试目录，但不得进入业务项目 Git 托管。生成后必须确保 `sourcePath` 被目标项目 Git 忽略，优先把精确路径或窄范围 AutoDev smoke 模式写入 `.git/info/exclude`，不要把 smoke 源码 `git add`，也不得让默认 `validationCommands` 无意中跑到慢/脆的冒烟。全部强 validation 通过后，运行：

```bash
python "${pluginPath}/hooks/run_advisory_smoke.py" --feature "${feature}"
```

`run_advisory_smoke.py` 会写入 `SMOKE_RESULT.json` 并向 `EVIDENCE.jsonl` 追加 `action=smoke` evidence。冒烟 PASS/FAIL/BLOCKED/SKIPPED 都只作为旁路风险信号：不得把 smoke evidence 写入 `plan.json.tasks[].evidenceIds`，不得把冒烟失败改成任务失败，不得因为 `SMOKE_RESULT.json.verdict` 非 PASS 而阻断 `code_done`。但如果 `SMOKE_TEST_PLAN.json.tests[]` 非空，必须产出覆盖每个 `SMK-xxx` 的 `SMOKE_RESULT.json`。

若 `run_advisory_smoke.py` 在执行前置检查阶段返回非 0（例如 `sourcePath` 对应测试源码不存在、测试条目非法、命令缺失、sourcePath 已被 Git 跟踪或未被 Git ignore 命中），这表示 Code 阶段尚未按 `SMOKE_TEST_PLAN.json` 补齐本地冒烟测试资产；必须先补齐测试源码/修正计划/更新 `.git/info/exclude` 后重跑。只有冒烟命令已经实际执行后的 PASS/FAIL/BLOCKED/SKIPPED 结果才属于不阻断流转的旁路风险信号。

策略边界：`plan.json.tasks[].validationCommands`、`action=validation` evidence、`code_done_gate` 与模块编译检查仍是强门禁；`SMOKE_TEST_PLAN.json` / `SMOKE_RESULT.json` 只表达旁路冒烟风险。不要把启动/主链路 smoke 命令同时放进强门禁和 advisory smoke；除非用户明确要求恢复阻断式 startup gate，否则不得让 `SMOKE_RESULT.json.verdict` 影响 `code_done` 流转。

> 一致性：任务的依据在对应 input 里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

###  全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（优先 AGENTS.md / 契约指定命令；Java/Maven 至少编译）。失败回到相关任务，不推进。

如本轮触发 HTML 分支，或变更了前端源码（`.tsx` / `.jsx` / `.ts` / `.js` / `.vue` 及相关样式文件），项目级验证通过后必须运行统一前端回检；只有用户明确要求“跳过回检 / 先不回检 / 不要跑回检 / 先不验证”时才跳过，并在最终摘要写 `reviewStatus=skipped-by-user`。默认命令：

```bash
python "{PLUGIN_ROOT}/skills/autodev/autodev-code/references/frontend-html/scripts/review_runner.py" --target "<file-or-dir>" --antd-audit auto --format markdown
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

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件；各 input 读取方式指示你更新的产物。

禁止：**执行清单列出的任何 input**（凡在清单中即只读）；本节点未在 `board_core/board_config.json` outputs 中声明的其他阶段产物；与当前任务无关的业务文件。

为完成任务必须改队列未直接提到的业务文件时，先确认与各 input 读取方式确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 若 Source Bundle 含 `plan.json`：`plan.json` 中所有任务为完成态，每个任务至少有一条通过的 evidence；若 Source Bundle 未列出 `plan.json`：本轮轻量任务队列全部完成，并在 evidence 中记录对应 specs/proposal 依据。
- `evidence/EVIDENCE.jsonl` 与 `evidence/EVIDENCE.index.json` 完整性校验通过，不存在截断、重写、重排、重编号或 index 缺失绕过。
- 若 Source Bundle 含 `SMOKE_TEST_PLAN.json`：已按计划生成/补齐冒烟测试源码并确认其被目标项目 Git 忽略，已运行 `run_advisory_smoke.py`；`SMOKE_RESULT.json` 已写入。`SMOKE_RESULT.json.verdict` 为 `FAIL` / `BLOCKED` / `SKIPPED` 时，记录为风险但不阻断本阶段流转。
- 必要验证通过；项目编译通过（code_done execute hook 会在推进前再次校验 plan/evidence 闭环与模块编译）。
- HTML 分支或前端源码变更已完成统一前端回检，或用户明确跳过；仍有 `must-fix` / 执行异常时不得推进 `code_done`。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**
