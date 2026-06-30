---
name: code-frontend-with-absolute-html
description: /autodev-code 内部绝对定位高保真 HTML 路线。它负责定义绝对定位 / 碎片 div / Figma 导出类 HTML 的适用条件、主流程 write_todos 与失败兜底方式。
---

# 绝对定位高保真 HTML 路线

这是 `/autodev-code` 内部的绝对定位高保真 HTML 实现路线目录。

- 当前路线入口：`SKILL.md`
- 当前路线依赖：`deps/`
- 当前路线参考：`references/`
- 当前路线脚本：`scripts/`

本文中提到的 `SKILL.md` 均指 code 根技能 `../../../SKILL.md`。若本文和 code 根技能冲突，以 code 根技能的 Source/Method Bundle 优先级、HTML 分支总控契约和 code_done 收尾规则为准。
命令示例兼容两种工作目录：
- 如果当前目录是 `autodev-code` 技能根目录，使用 `deps/frontend-html/with-absolute-html/scripts/...`
- 如果当前目录已经是 `deps/frontend-html/with-absolute-html/`，使用 `scripts/...`

## 0. WriteTodos Protocol

进入本 route 后，必须立即创建固定的 top-level `write_todos`。可见清单只放下面 7 个一级任务，不要把细节步骤拆成嵌套 todo，也不要改写 ID。

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --emit-route-todos --format markdown
```

| id | title | doneWhen | evidenceKey |
| --- | --- | --- | --- |
| `ABS-01-html-source` | 读取 HTML 来源 | HTML source paths are confirmed and readable. | `htmlSourcePaths` |
| `ABS-02-project-context` | 读取项目上下文 | AGENTS.md, architecture, component docs, similar pages, and source evidence are checked or marked unavailable. | `projectContextRead` |
| `ABS-03-page-modules` | 建立页面模块清单 | Entry, visual sections, local components, style/assets, and existing-page delta are listed. | `pageModuleTodosReady` |
| `ABS-04-analysis-script` | 执行 absolute 分析脚本 | `prepare_html_analysis.py` is attempted and artifacts or downgrade reason are recorded. | `analysisScriptStatus` |
| `ABS-05-context-handoff` | 读取分析上下文并确定 handoff | Checklist/handoff or downgrade path is read, then original HTML is rechecked as visual truth. | `contextHandoffReady` |
| `ABS-06-parser-handoff` | 转交 html-parser | `hasManifest` state is decided and `deps/html-parser.md` is now allowed to be read. | `routeTodosReadyForParser` |
| `ABS-07-return-to-code` | 返回 autodev-code 主流程 | Generated targets, HTML source, analysis JSON/none, PLAN path, and review inputs are handed back. | `returnToCodeReady` |

创建可见 todos 后，必须把完整 ID 镜像写入机器证据：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-created --todo-id ABS-01-html-source --todo-id ABS-02-project-context --todo-id ABS-03-page-modules --todo-id ABS-04-analysis-script --todo-id ABS-05-context-handoff --todo-id ABS-06-parser-handoff --todo-id ABS-07-return-to-code --json
```

每完成一个一级任务，立即记录：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todo-completed --todo-id <ABS-ID> --json
```

只有 `ABS-06-parser-handoff` 完成后，才允许读取 `deps/html-parser.md`。全部 7 个 ID 完成后，再运行：

```bash
python "{PLUGIN_ROOT}/hooks/resolve_frontend_html_route.py" --feature "{FEATURE_ID}" --mark route-todos-completed --json
```

## 1. 这条路线什么时候使用

当满足任一条件时，使用本路线：

| 输入 | 处理 |
| --- | --- |
| 用户上传的 HTML 页面主体、关键分区或多个视觉块主要由绝对定位 / 碎片 div 驱动 | 走本路线 |
| 用户直接粘贴的 HTML 明显是设计导出稿 / 坐标稿 | 走本路线 |
| 用户提供的设计产物已经能导出或拿到实际 HTML，且结构明显偏导出稿 | 走本路线 |

绝对定位强制信号（命中任一条，且该信号已覆盖页面主体、关键分区或多个视觉块时，就**禁止**转去标准 HTML 路线）：

- 用户明确说“高保真”“设计稿导出”“Figma/MasterGo 导出”“绝对定位”
- 全局或局部大量 `position:absolute`
- utility class / Tailwind 中大量 `left-[...]`、`top-[...]`、`w-[...]`、`h-[...]`
- 存在大量 `clip-path`、`data:image/svg+xml`、渐变、阴影、像素级尺寸
- 页面主体、关键分区或多个视觉块是视觉拼装容器，而不是原生语义表单 / 表格 / DOM 流布局

即使分析脚本把这类输入判成普通 `html`，也不能改走 `../with-standard-html/SKILL.md`；只能继续走本路线，并把分析结果视为“低置信度辅助信息”。

如果用户提供的是高保真 HTML，但绝对定位只出现在局部、稀疏、装饰性区域，页面主体仍由标准 DOM / flex / grid 驱动，则不使用本路线，应转去 `../with-standard-html/SKILL.md`。

当满足以下条件时，不走本路线：

| 输入 | 处理 |
| --- | --- |
| 只有截图 / 图片，没有 HTML | 返回 `/autodev-code` 主流程；若任务明确要求 HTML 还原则停止并要求补充 HTML |
| 只有 Figma / 设计链接，但拿不到实际 HTML | 返回 `/autodev-code` 主流程；若任务明确要求 HTML 还原则停止并要求补充 HTML |
| 只有 HTML URL，但最终拿不到实际内容 | 停下并要求用户补 HTML 文件或内容 |

## 2. 这条路线只负责什么

本文件只负责：总原则、主流程 write_todos、交接边界与失败兜底。

它不展开实现细则，不替代 code 根技能 `../../../SKILL.md` 的全局优先级，也不重复 `deps/html-parser.md` 的执行规则。
本路线的真实执行顺序见第 3 节；未完成第 3 节前，不得提前转交 `deps/html-parser.md`。

## 3. 路线总原则

### 3.1 HTML 是视觉契约

只要用户已经提供这类高保真绝对定位 HTML：

- 表现层样式以高保真还原为准
- 不再强要求字体、字号、字色、背景色、边框、边框色、`border-radius` 等去对齐 token
- design system / token 只用于 HTML 没给出的交互态和默认组件细节

### 3.2 先整页恢复，再局部组件化

本路线统一使用两阶段心智：

| 阶段 | 目标 |
| --- | --- |
| Stage 1 | 先恢复整页视觉与结构 |
| Stage 2 | 再替换已经证明安全的组件槽位 |

### 3.3 有高保真时，不为“工程化”主动改视觉

不要为了更像 design system / AntD 默认样式 / 更整齐 / 更像某个已有页面，就改掉高保真里已经明确给出的视觉结果。

### 3.4 同一页面多个 HTML 先统一分区，再决定拆分

如果用户提供的是同一页面的多个 HTML 片段：

- 先判断这些片段是否属于同一业务页面的不同视觉分区
- 只要属于同一页面，就先统一成一个页面级实现方案，再按视觉分区与复用价值拆出同目录局部组件
- 默认保留“页面壳层 + 数据编排”在主页面文件，把筛选区、统计区、图表区、表格区、弹窗区等清晰分区拆到同目录局部组件

### 3.5 脚本任务块与地位

在 `with-absolute-html` 路线里，脚本执行是主流程 write_todos 的独立任务块，**默认必跑**，不要让模型自行决定跳过。

这样做的目的不是让脚本替代原始 HTML，而是把下面这些容易不断膨胀的识别逻辑尽量下沉到脚本：

- 内容盘点
- 页面分区与 archetype 判断
- field / table / chart / icon / interaction 检测
- 项目组件扫描
- UI 库检测
- replacement slots 候选生成

脚本地位：

- **必须执行**：用于统一识别入口，减少模型自由发挥。
- **不能越权**：脚本产物只是辅助材料，原始 HTML 仍然是视觉真相。
- **冲突时原始 HTML 赢**：只要脚本结论与原始 HTML 冲突，以原始 HTML 为准。

出现以下任一情况时，即使脚本已跑，也必须把它降级为“辅助信息”，不要把它当主依据：

- 脚本只识别出极少区域，但原稿明显有多个大块
- 图表、分页、时间线、上传区、Tab 内左右栏等明显区域没被识别出来
- 文本被异常合并，或区域命名明显失真
- 脚本给出的组件 / 图标 / 图表判断与你直接读原始 HTML 的结果冲突
- 你已经能从原始 HTML 直接稳定看出布局、内容和槽位关系，而脚本摘要反而在打断判断
- 对表格多、图表多、左右对比强、时间线/上传/分页并存、模块边界复杂的页面，默认把脚本视为“盘点材料”，不要把它视为结构裁判

## 4. 默认读取顺序

进入本 route 后，先执行 §0 的固定 top-level todos；**未完成 `ABS-06-parser-handoff` 前不得提前转交 `deps/html-parser.md`**。下面是每个 ID 的执行说明，不要把这些说明改造成额外的可见 todo。

| id | 执行说明 |
| --- | --- |
| `ABS-01-html-source` | 读取并确认 HTML 来源；多片段输入先判断是否属于同一页面，并统一成页面级实现方案。 |
| `ABS-02-project-context` | 读取项目 `AGENTS.md`、`architecture/`、组件说明、相似页面/模块和真实源码证据；不存在的资料标记为“无可用证据”。 |
| `ABS-03-page-modules` | 基于原始 HTML 和项目证据列出入口、视觉分区、局部组件、样式/资产、已有页面增量范围；页面模块清单不承担脚本执行。 |
| `ABS-04-analysis-script` | 默认必跑 `prepare_html_analysis.py`；确认 `--project-root`、`--task-stem`、`--html-file` 后执行并检查 `.frontend/html-analysis/<task-stem>.md/.json/-checklist.md`；失败按 §7 降级。 |
| `ABS-05-context-handoff` | 产物齐全时先读 checklist，再读完整版 handoff，最后回到原始 HTML；降级时直接以原始 HTML 为主。低置信度或 conservative 时，不让 `replacementSlots` 主导大块组件化。 |
| `ABS-06-parser-handoff` | 决定 `hasManifest=true/false`，记录到交接状态，完成该 ID 后才读取 `deps/html-parser.md`。 |
| `ABS-07-return-to-code` | 主线完成后输出交付总结，带回目标源码路径、原始 HTML 路径、analysis JSON 或 none、PLAN 路径、`uiLibraryTarget`、`antdMode`、`auditRequired`，再返回 `/autodev-code`。 |

脚本命令模板：

```bash
python deps/frontend-html/with-absolute-html/scripts/prepare_html_analysis.py \
  --project-root . \
  --task-stem <task-stem> \
  --html-file <HTML_PATH>
```

如果当前目录已经是 `deps/frontend-html/with-absolute-html/`：

```bash
python scripts/prepare_html_analysis.py \
  --project-root <CODE_WORKSPACE> \
  --task-stem <task-stem> \
  --html-file <HTML_PATH>
```

`<task-stem>` 建议使用 `task-1` / `task-<页面短名>`；同一页面多个 HTML 片段时重复 `--html-file` 或用逗号分隔。脚本产物与原始 HTML 冲突时，原始 HTML 赢。

## 5. 转交规则

完成第 4 节的 write_todos 和上下文检查后，默认直接转交 `deps/html-parser.md`。

- 默认入口：从 `§3 分类 HTML` 开始
- 转交时附带状态：`hasManifest=true`（脚本产物齐全，仅作辅助）或 `hasManifest=false`（已走 §7 降级）
- `hasManifest=false` 时 `deps/html-parser.md` 直接以原始 HTML 为唯一视觉源继续，不要再要求脚本

## 6. 增量修改

如果项目里已经存在目标页面：

1. 先读取当前页面文件
2. 以高保真 HTML 对比差异
3. 只改变化部分
4. 保留原文件中无关的逻辑、导入和状态管理

## 7. 失败兜底（降级路径）

核心原则：**脚本异常永远不阻塞主流程**。任何脚本失败都按下面的统一降级处理。

- URL 拿不到真实 HTML 时，停下并要求用户补 HTML，或返回 `/autodev-code` 主流程判断是否可按 specs/design/PLAN 直接实现（这是输入缺失，不是脚本失败）
- `prepare_html_analysis.py` 出现以下任一情况都按"降级路径"继续：
  - Python 运行时缺失 / 被禁用 / 版本不兼容
  - 脚本依赖（标准库以外）安装失败
  - 脚本本身语法错误、文件被截断、import 失败
  - 缺少必填参数（`--project-root` / `--task-stem` / `--html-file`）导致 argparse 报错
  - 路径写错、HTML 源不存在
  - 文件 IO 错误（磁盘满 / 权限不足）
  - 子进程崩溃、超时、产物写入残缺
  - 任何未在上面列举的异常
- 降级路径动作：
  1. 不再重试脚本，也不要原地等待用户补救
  2. 直接进入 §4 第 4 步，转交 `deps/html-parser.md`，并带 `hasManifest=false` 状态
  3. 由 `deps/html-parser.md` 以原始 HTML 为唯一视觉源继续整页恢复与组件化
  4. 在最终交付总结里显式列出"已跳过 Stage 1 脚本"和具体原因（如"argparse 缺参数"、"语法错误 line N"、"FileNotFoundError"）
- 唯一例外（不走降级、必须先修复）：模型自己虚构了"已跳过原因"而实际并未执行脚本。脚本必须至少被真实尝试执行一次，并捕获真实异常信息

## 8. 和其它文件的边界

| 文件 | 一句话职责 |
| --- | --- |
| `../../../SKILL.md` | `/autodev-code` 总入口 + 全局优先级与执行清单 |
| `SKILL.md` | 绝对定位高保真 HTML 路线入口、读取顺序与转交规则 |
| `deps/html-parser.md` | 真正把路走完（分类、整页恢复、组件替换、写代码） |
| `/autodev-code` 主流程 | HTML 上下文完成后执行项目级验证、统一前端回检、模块编译清单校验与 `code_done` 推进 |
