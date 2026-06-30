---
name: code-frontend-with-standard-html
description: 处理 /autodev-code 内部标准 DOM / 语义明确 HTML 的路线技能。适用于标准 DOM、style 标签、class 命名清晰、表单/按钮/label/flex 结构明显的 HTML 输入；既覆盖普通 HTML，也覆盖结构标准但视觉仍要求高保真的 HTML。
---

# 标准 HTML 路线

这是 `/autodev-code` 内部的标准 HTML 实现路线目录。

- 当前路线入口：`SKILL.md`
- 当前路线依赖：`deps/`
- 当前路线参考：`references/`
- 当前路线脚本：`scripts/`

本文中提到的 `SKILL.md` 均指 code 根技能 `../../../SKILL.md`。若本文和 code 根技能冲突，以 code 根技能的 Source/Method Bundle 优先级、HTML 分支总控契约和 code_done 收尾规则为准。

## 0. WriteTodos Protocol

进入本 route 后，必须立即创建固定的 top-level `write_todos`。可见清单只放下面 7 个一级任务，不要把细节步骤拆成嵌套 todo，也不要改写 ID。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --emit-route-todos --format markdown
```

| id | title | doneWhen | evidenceKey |
| --- | --- | --- | --- |
| `STD-01-route-confirm` | 确认 standard route | Standard DOM evidence is recorded and absolute/Figma/MasterGo hard signals are cleared. | `absoluteSignalsCleared` |
| `STD-02-project-context` | 读取项目上下文 | Nearest AGENTS.md, architecture, component/API docs, similar pages, and source evidence are checked. | `projectContextRead` |
| `STD-03-page-modules` | 建立页面模块清单 | Entry, page sections, local components, reusable logic, styles/assets, and existing-page delta are listed. | `moduleTodosReady` |
| `STD-04-conversion-matrix` | 建立转换矩阵 | Project/AntD/AntD Mobile/native mapping and `uiLibraryTarget`/`antdMode` are decided. | `conversionTodosReady` |
| `STD-05-antd-audit` | 完成 Ant Design 审计判定 | `auditRequired` is decided; desktop Ant Design audit is run or explicitly not applicable. | `auditRequired` |
| `STD-06-parser-handoff` | 转交 standard-html-parser | All handoff state is ready and `deps/standard-html-parser.md` is now allowed to be read. | `routeTodosReadyForParser` |
| `STD-07-return-to-code` | 返回 autodev-code 主流程 | Generated targets, HTML source, analysis none, PLAN path, `uiLibraryTarget`, `antdMode`, and `auditRequired` are handed back. | `returnToCodeReady` |

创建可见 todos 后，必须把完整 ID 镜像写入机器证据：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-created --todo-id STD-01-route-confirm --todo-id STD-02-project-context --todo-id STD-03-page-modules --todo-id STD-04-conversion-matrix --todo-id STD-05-antd-audit --todo-id STD-06-parser-handoff --todo-id STD-07-return-to-code --json
```

每完成一个一级任务，立即记录：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todo-completed --todo-id <STD-ID> --json
```

只有 `STD-06-parser-handoff` 完成后，才允许读取 `deps/standard-html-parser.md`。全部 7 个 ID 完成后，再运行：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-completed --json
```

## 1. 什么时候使用

当输入满足以下特征时，使用本路线：

- 标准 DOM 结构明显
- 存在 `<style>` 或清晰 class 规则
- `form` / `input` / `button` / `label` / `textarea` / `select` / `flex` 布局明显
- 页面不是靠绝对定位碎片容器拼装主结构
- 目标是优先映射 React + AntD / 真实项目组件，同时保留需要的视觉还原度

如果 HTML 主要由坐标、绝对定位、碎片 div 或 Figma/MasterGo 导出结构驱动，不使用本路线，应回到 `../with-absolute-html/SKILL.md`。

补充硬门槛：如果命中以下任一“绝对定位 / 导出稿”信号，**即使 DOM 看起来较规整，也不能走本路线**：

- 用户明确标注“绝对定位 HTML”“Figma/MasterGo 导出 div 稿”“纯坐标还原稿”
- 存在全局 absolute 规则，如 `body { * { position: absolute; } }`
- utility class 中出现大量 `left-[...]` / `top-[...]` / `clip-path` / data-svg
- 一个页面内出现多组梯形、迷你趋势图、像素级卡片矩阵、复杂壳层布局

命中以上任一条，必须返回 `../with-absolute-html/SKILL.md`。

补充说明：

- 如果用户明确要求高保真，但 HTML 本身仍然是标准 DOM、表单结构、表格结构或正常的 flex / grid 布局，本路线仍然适用。
- “是否高保真”不是排除条件；“是否主要由绝对定位碎片结构驱动”才是本路线的核心分流条件。

## 2. 默认读取顺序 / 主流程 write_todos

进入本路线后，先执行 §0 的固定 top-level todos；**未完成 `STD-06-parser-handoff` 前不得提前转交 `deps/standard-html-parser.md`**。这里的 `write_todos` 是路线执行时必须显式维护的可见清单，不是项目业务 API，也不是新增脚本要求。

本路线不新增标准 HTML 前置分析脚本，不复用 `../with-absolute-html/` 下的 absolute 分析脚本，也不要求生成 `.frontend/html-analysis` 前置产物。原始 HTML 始终是视觉、内容和语义事实源。

| id | 执行说明 |
| --- | --- |
| `STD-01-route-confirm` | 读取原始 HTML 来源；确认标准 DOM、语义结构、表单/表格/flex/grid/class 证据；检查是否命中 absolute/Figma/MasterGo 强信号。命中强信号时停止本路线并返回 `../with-absolute-html/SKILL.md`；否则记录 `routeType=standard-html`、`absoluteSignalsCleared=true`。 |
| `STD-02-project-context` | 从 HTML 来源目录和目标工程目录向上查找 `AGENT.md` / `AGENTS.md`；读取 `architecture/`、组件说明、API 说明、相似页面和真实源码证据。 |
| `STD-03-page-modules` | 按页面模块列出入口、分区、局部组件、复用逻辑、样式文件和资产；已有目标页时先读现有文件并按原始 HTML 限定增量修改范围；记录 `moduleTodosReady=true`。 |
| `STD-04-conversion-matrix` | 按区域明确项目组件 / Ant Design / Ant Design Mobile / 原生 React + CSS 映射策略；决定 `uiLibraryTarget` 和 `antdMode`；需要桌面 Ant Design 评估时读取 `references/ant-design-conversion.md`；记录 `conversionTodosReady=true`。 |
| `STD-05-antd-audit` | 仅当 `uiLibraryTarget=antd` 且 `antdMode=required|selected` 时启用桌面 Ant Design 审计并记录 `auditRequired=true`；不适用时记录 `auditRequired=false`。需要审计时实现后运行 `scripts/audit_antd_coverage.py`，对候选项转换或加 `antd-audit-ignore`。 |
| `STD-06-parser-handoff` | 准备完整交接状态：`routeType=standard-html`、`absoluteSignalsCleared=true`、`moduleTodosReady=true`、`conversionTodosReady=true`、`uiLibraryTarget=<project|antd|antd-mobile|native>`、`antdMode=<required|candidate|selected|notApplicable>`、`auditRequired=<true|false>`。完成该 ID 后才读取 `deps/standard-html-parser.md`。 |
| `STD-07-return-to-code` | `standard-html-parser` 主线完成后输出交付总结，带回目标源码路径、原始 HTML 路径、analysis JSON 路径（通常为 none）、PLAN 路径、`uiLibraryTarget`、`antdMode`、`auditRequired`，再返回 `/autodev-code`。 |

## 3. 交接状态

完成第 2 节的 `write_todos` 后，默认直接转交 `deps/standard-html-parser.md`，并带上以下状态：

```text
routeType=standard-html
absoluteSignalsCleared=true
moduleTodosReady=true
conversionTodosReady=true
uiLibraryTarget=<project|antd|antd-mobile|native>
antdMode=<required|candidate|selected|notApplicable>
auditRequired=<true|false>
```

如果任一 ready 状态无法成立，必须先补齐对应清单；只有命中绝对定位 / 设计导出稿强信号时，才停止本路线并返回 `../with-absolute-html/SKILL.md`。

## 4. 边界

- 本路线只负责标准 DOM / 语义明确 HTML
- 不修改绝对定位高保真路线的执行结果，只在命中强信号时返回 `../with-absolute-html/SKILL.md`
- 不依赖 `../with-absolute-html/` 下的 absolute 分析脚本
- 不新增标准 HTML 分析脚本，也不依赖 `.frontend/html-analysis/*.json` 前置产物
- `scripts/audit_antd_coverage.py` 只在桌面 Ant Design 转换后做源码覆盖审计，不作为 HTML 解析前置步骤，也不用于 Ant Design Mobile
