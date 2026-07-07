---
name: autodev-code
description: 进行代码实现。
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

## 前端 HTML 实现分支

HTML 转前端已经并入 `/autodev-code`。它不是独立 workflow 节点，也不再产生 `frontend_in_progress` / `frontend_done` checkpoint；完成后仍按本技能统一收尾推进到 `code_done`。本分支只处理 HTML/DOM/设计导出稿到真实工程代码的实现方式。
用户本轮直接提供的 HTML/DOM 素材、`{FEATURE_DIR}/frontend-html/` 素材和内部 route SKILL/references 由本文的前端 Route 强制闸门管理。

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

1. 先读项目说明，再扫真实源码；项目约束优先于本分支默认规则。
2. 组件来源优先级：公共组件库 -> `architecture/components` -> 项目本地组件 -> 已安装且真实使用的组件库 -> 用户提供兜底组件库 -> 相似页面 -> fidelity-only。
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

分支返回契约：两个 HTML 路线完成后都必须返回 `/autodev-code` 主流程，由 code 根技能继续项目级验证、统一前端回检、 `code_done` checkpoint 推进。HTML 分支内部不得发起独立回检选择，不得调用或引用已移除的 `autodev-frontend`、`frontend_done` 或内部回检路线。返回主流程时必须带回回检输入：生成/修改的目标源码路径、原始 HTML 路径、可用的 `.frontend/html-analysis/*.json` 路径（没有则写 none）、`PLAN.md` 路径（没有则写 none）、`uiLibraryTarget`、`antdMode`、`auditRequired`。

## 准入检查


```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

识别规则：按项目 manifest 的单/多模块入口生成；`path` 用模块目录绝对路径，`compile_command` 以该目录为 cwd 执行（命令本身不要再写 `cd`）；无法确定时停止并询问用户，不得开始编码。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 执行协议

### 建立执行上下文与任务队列

- 使用`write_todos`，把 `plan.json`（如有） 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度。

###  选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目。

###  执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。先依各 的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。
3. 改代码前做有界探索定位真实文件与既有模式：只读上游产物或 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 任务需要写 / 改测试时，遵循 `${pluginPath}/skills/references/test-quality.md`：站在 seam 上验证、期望值来自独立事实源（勿同义反复）、mock 只在系统边界。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（存在 `plan.json` 时优先 `plan.json.tasks[].validationCommands`；缺失或契约未列出 `plan.json` 时，基于 specs、项目脚本选最小可行验证）。每次验证完成后用 `hooks/evidence_store.py append` 向 `evidence/EVIDENCE.jsonl` 末尾追加一条 evidence，记录 taskId（无 plan 时使用本轮轻量任务 ID）、specRefs、designRefs（无 design 契约时可为空）、changedFiles、validation.command/exitCode/result；`ev_XXXX` 按全流顺序自动递增，不按阶段重排，不得插入旧记录前、重编号、截断、重写、删除 `EVIDENCE.index.json` 后重建或手动修改 `EVIDENCE.index.json`。若 append 或 checkpoint 报 `evidence_stream_rewritten_or_truncated` / `missing_evidence_index_for_nonempty_stream`，必须恢复被改写前的 `EVIDENCE.jsonl` / `EVIDENCE.index.json`，无法恢复时停止并向用户报告。通过 → 状态「完成」；存在 `plan.json` 时还要将新增 evidenceId 写回 `plan.json.tasks[].evidenceIds`，`PLAN.md` 若存在再同步人类视图。失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。
7. 若 `SMOKE_TEST_PLAN.json`存在，按其中 `tests[]` 生成或补齐旁路冒烟测试源码/脚本。每条 smoke 必须按计划中的 `seam` 站在公开边界上验证，不测私有方法、不查内部实现细节；按 `verticalSlice` 一次只实现一个最小闭环，不把多个场景合成一条大烟测；按 `mockPolicy` 只 mock 系统边界，不 mock 自有模块或内部协作者。冒烟测试必须是 opt-in：Java/Spring 可用 `*SmokeIT` + `-Psmoke`，前端可用 `tests/smoke/` + 单独 smoke script，CLI/API 可用 `scripts/smoke/`；这些源码/脚本只用于本地验证，可以放在业务项目测试目录，但不得进入业务项目 Git 托管。生成后必须确保 `sourcePath` 被目标项目 Git 忽略，优先把精确路径或窄范围 AutoDev smoke 模式写入 `.git/info/exclude`，不要把 smoke 源码 `git add`，也不得让默认 `validationCommands` 无意中跑到慢/脆的冒烟。全部强 validation 通过后，运行：

```bash
python "${pluginPath}/hooks/run_advisory_smoke.py" --feature "${feature}"
```

`run_advisory_smoke.py` 会写入 `SMOKE_RESULT.json` 并向 `EVIDENCE.jsonl` 追加 `action=smoke` evidence。冒烟 PASS/FAIL/BLOCKED/SKIPPED 都只作为旁路风险信号：不得把 smoke evidence 写入 `plan.json.tasks[].evidenceIds`，不得把冒烟失败改成任务失败，不得因为 `SMOKE_RESULT.json.verdict` 非 PASS 而阻断 `code_done`。但如果 `SMOKE_TEST_PLAN.json.tests[]` 非空，必须产出覆盖每个 `SMK-xxx` 的 `SMOKE_RESULT.json`。

若 `run_advisory_smoke.py` 在执行前置检查阶段返回非 0（例如 `sourcePath` 对应测试源码不存在、测试条目非法、命令缺失、sourcePath 已被 Git 跟踪或未被 Git ignore 命中），这表示 Code 阶段尚未按 `SMOKE_TEST_PLAN.json` 补齐本地冒烟测试资产；必须先补齐测试源码/修正计划/更新 `.git/info/exclude` 后重跑。只有冒烟命令已经实际执行后的 PASS/FAIL/BLOCKED/SKIPPED 结果才属于不阻断流转的旁路风险信号。

策略边界：`plan.json.tasks[].validationCommands`、`action=validation` evidence 与 `code_done_gate` 仍是强门禁；`SMOKE_TEST_PLAN.json` / `SMOKE_RESULT.json` 只表达旁路冒烟风险。不要把启动/主链路 smoke 命令同时放进强门禁和 advisory smoke；除非用户明确要求恢复阻断式 startup gate，否则不得让 `SMOKE_RESULT.json.verdict` 影响 `code_done` 流转。

> 一致性：任务的依据在对应上游产物里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

###  全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（至少编译）。失败回到相关任务，不推进。

如本轮触发 HTML 分支，或变更了前端源码（`.tsx` / `.jsx` / `.ts` / `.js` / `.vue` 及相关样式文件），项目级验证通过后必须运行统一前端回检；只有用户明确要求“跳过回检 / 先不回检 / 不要跑回检 / 先不验证”时才跳过，并在最终摘要写 `reviewStatus=skipped-by-user`。默认命令：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件

为完成任务必须改队列未直接提到的业务文件，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 若 `plan.json`存在：`plan.json` 中所有任务为完成态，每个任务至少有一条通过的 evidence；若 `plan.json`不存在：本轮轻量任务队列全部完成，并在 evidence 中记录对应 specs/proposal 依据。
- `evidence/EVIDENCE.jsonl` 与 `evidence/EVIDENCE.index.json` 完整性校验通过，不存在截断、重写、重排、重编号或 index 缺失绕过。
- 若 `SMOKE_TEST_PLAN.json`存在：已按计划生成/补齐冒烟测试源码并确认其被目标项目 Git 忽略，已运行 `run_advisory_smoke.py`；`SMOKE_RESULT.json` 已写入。`SMOKE_RESULT.json.verdict` 为 `FAIL` / `BLOCKED` / `SKIPPED` 时，记录为风险但不阻断本阶段流转。
- 必要验证通过；项目编译通过（code_done execute hook 会在推进前再次校验 plan/evidence 闭环）。
- HTML 分支或前端源码变更已完成统一前端回检，或用户明确跳过；仍有 `must-fix` / 执行异常时不得推进 `code_done`。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**