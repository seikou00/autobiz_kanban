---
name: autodev-frontend
description: Dev 阶段 frontend_before_specs workflow profile 的 HTML 转前端实现节点。用于在行为规格生成前，基于 PRD、HTML、接口说明和现有前端工程完成前端实现；仅保留标准 HTML、绝对定位高保真 HTML 两种实现路线，以及用户确认后的 review 路线。
version: v1.1.08041
---

# /autodev-frontend - HTML 转前端实现

使用任何 `request_user_input` 前，必须先读取并遵循 `${pluginPath}/skills/references/ask-user-question.md`。

本技能是 `frontend_before_specs` workflow profile 中的正式 Dev 节点。它只保留两种实现路线：

| 输入形态 | 路线 |
| --- | --- |
| Figma/低代码导出的高保真 HTML、绝对定位/大量 inline style/像素坐标 HTML、纯 div 视觉稿 | `route/with-absolute-html/SKILL.md` |
| 普通静态 HTML、复制的 DOM 片段、小型静态站点、语义化 HTML/CSS/JS，或用户明确说 HTML 转 React/TSX/Vite/Next | `route/with-standard-html/SKILL.md` |

主线完成后，如用户明确确认回检，进入 `route/review/SKILL.md`。不要再使用这三条 route 之外的实现路线。

## 流程状态

进入本技能前，当前 workflow profile 必须是 `frontend_before_specs`。如果由 `prd_done` 直接进入本技能，先使用统一脚本推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint frontend_in_progress --workflow-profile frontend_before_specs
```

完成前端实现和必要验证后：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint frontend_done
```

完成后汇报变更文件、验证命令和未覆盖风险。

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。

## 路由规则

`frontend_before_specs` 只有一个正式 workflow 节点和一组 checkpoint：`frontend_in_progress -> frontend_done`。标准 HTML 与绝对定位 HTML 是同一节点内的两种内部路线，不是两个串行阶段。

### 高保真 / 绝对定位信号

命中任一条时进入 `route/with-absolute-html/SKILL.md`：

- 用户明确标注“高保真 HTML”“设计导出 HTML”“绝对定位”“Figma/MasterGo 导出”
- 大量 `position: absolute`、`left/top`、固定像素宽高、`clip-path`、`data:image/svg+xml`、渐变、阴影
- 页面主体由碎片 `div`、梯形块、迷你趋势图、像素级卡片矩阵、复杂壳层布局组成

### 标准 HTML 信号

未命中绝对定位信号，且存在标准 DOM、表单、表格、按钮、label、flex/grid、清晰 class/style 或普通静态页面结构时，进入 `route/with-standard-html/SKILL.md`。

如果用户没有提供 HTML 文件、HTML 片段或可读取的 HTML 内容，停止并要求补充 HTML；不要根据 PRD 或截图直接生成页面。

## 输入边界

| 来源 | 负责内容 |
| --- | --- |
| `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` | 字段、文案、交互、任务边界、页面业务语义，仅作 HTML 实现校对依据 |
| 接口说明文档 | 接口路径、请求方式、参数、响应字段、枚举/状态约束 |
| HTML | 布局、结构、间距、视觉层级、组件槽位与视觉契约 |

边界优先级：

- 布局和视觉以 HTML 为准。
- 业务文案和交互以 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/PRD.md` 为准。
- 数据字段与接口约束以接口说明文档为准。
- 如果三者互相冲突，先保留 HTML 布局，再在汇报中明确冲突点；不要私自编造第四套口径。

## 核心原则

| 编号 | 原则 |
| --- | --- |
| 1 | 先读项目说明文档，再扫项目源码。 |
| 2 | 先复用项目组件体系，再回退到本地组件、已安装组件库或用户提供的兜底组件库。 |
| 3 | 先恢复整页与结构，再做局部组件化。 |
| 4 | 高保真 HTML 是视觉契约；不要因组件默认行为改变布局、边框、方向、形态或状态表达。 |
| 5 | 同一行原子内容必须保持同一行，例如数字 + 单位、金额 + 币种、百分比 + `%`、主值 + 短尾标。 |
| 6 | 大页面和大模块优先使用可伸缩布局，不要层层写死宽高。 |
| 7 | 图标属于页面语义的一部分，默认检测与生成。 |
| 8 | 图表必须用真实图表库实现；具体来源、询问、兜底和交付状态见 §图表来源顺序。 |
| 9 | HTML 路线里仅 `with-absolute-html` 默认先执行对应分析脚本；`with-standard-html` 直接进入 `standard-html-parser`，不要求标准 HTML 分析脚本或 `output/html-analysis/*.json` 前置产物。脚本产物不得压过原始 HTML，当两者冲突时，以原始 HTML 为准 |
| 10 | 主线交付总结不是整轮终点；交付总结后必须执行 §回检决策规则。 |

## 全局优先级

### 项目说明优先级

| 优先级 | 来源 |
| --- | --- |
| 1 | 系统约束 |
| 2 | 其它项目说明文档 |
| 3 | 项目真实源码结构（必要时生成 `output/scan-result.json`） |
| 4 | 技能内参考文档 |

规则：

- 应用结构、API、技术栈、路由/菜单/权限、状态管理、样式体系都先看项目说明文档。
- 系统约束中的项目约束优先于本技能规则；如冲突，以 系统约束为准，除非系统级指令另有要求。
- 没有项目说明时再扫源码，不要凭技能默认值覆盖真实工程模式。

### 组件来源优先级

| 优先级 | 来源 |
| --- | --- |
| 1 | 系统约束中给出的公共组件库路径与使用规则 |
| 2 | 项目 `architecture/components/` |
| 3 | 项目本地 `components` / `src/components` |
| 4 | 当前工程已安装并实际在用的组件库 |
| 5 | 用户提供的兜底组件库 |
| 6 | 相似页面模式 |
| 7 | fidelity-only |

规则：

- 系统约束 是项目总说明与组件策略的第一入口。
- 如果 系统约束 已经明确约定组件、页面目录、布局骨架或实现方式，优先遵守。
- 如果 系统约束 给出公共组件库路径，先按该路径扫描真实组件源码与使用方式；通常是当前工程上一级目录的 `components/`。
- 文档层命中时，先按说明理解用途、props、导入方式与示例，再用真实源码确认导出与路径。
- 没有源码、导出或真实使用示例时，不能因为规则命中就强行使用。
- 如果当前工程缺少所需组件库，按 §依赖安装确认规则 执行。

### 依赖安装确认规则

- 不静默新增组件库、图标库、图表库或样式依赖。
- 需要新增依赖时，先判断项目使用 `pnpm` 还是 `npm`：`pnpm-lock.yaml` -> `package-lock.json` -> `package.json` 的 `packageManager` 字段 -> 真实项目命令痕迹。
- 向用户说明待新增依赖、用途和影响，获得明确确认后再安装。
- 如果本次安装了依赖，最终汇报必须列出新增库。

### 技术栈兜底

当项目技术栈无法从以下证据识别时：

| 顺序 | 证据 |
| --- | --- |
| 1 | 系统约束 |
| 2 | 其它项目说明文档 |
| 3 | `output/scan-result.json` |
| 4 | 真实源码扫描结果 |

统一按以下规则继续执行：

- 桌面端页面：React + `antd`
- 移动端页面：React + `antd-mobile`

用户明确指定 Vue / React / 其它框架时，用户指令优先。汇报时必须明确标记使用了哪一种兜底或候选方案。

## 图标 / 图表权威规则

### 图标来源顺序

| 优先级 | 来源 |
| --- | --- |
| 1 | 系统约束 或其它项目说明中定义的图标组件 / 图标规则 |
| 2 | 项目本地 icon 组件、svg 资产、iconfont、统一包装层 |
| 3 | 已安装且在真实源码中实际使用过的图标库 |
| 4 | React + AntD 的 `@ant-design/icons` |

规则：

- 有明确形状或类名时保留同一或最接近的图标；只有语义时按常见业务语义选。
- 对 HTML 里提供的 `svg` / `data:image/svg+xml`，先区分它是明显的小型 icon 还是迷你图表 / sparkline / 趋势线。
- 明显的小型 icon 要先匹配项目图标体系或已安装图标库里外观足够接近的 icon；找不到时才保留真实原始 SVG。
- 占位几何、装饰 path、背景碎片不能直接当最终 icon。
- `sparkline`、迷你趋势线、迷你面积图即使尺寸小，也仍按图表处理。
- 纯图标按钮必须补 `Tooltip` 和 `aria-label`。
- 汇报必须说明图标来源层级与关键图标映射。

### 图表来源顺序

| 优先级 | 来源 |
| --- | --- |
| 1 | 系统约束 或其它项目说明中定义的图表组件 / 图表规则 |
| 2 | 项目本地 chart 组件、可视化包装层、统计卡片与图表容器 |
| 3 | 当前工程已安装并且在真实源码中实际使用过的图表库 |
| 4 | 用户明确指定的图表库 |
| 5 | `ECharts` 默认兜底方案 |

规则：

- `package.json` 出现但源码无导入/使用证据，不可直接视为可用图库。
- 只要需求或 HTML 语义是图表，默认必须用真实图表库实现；唯一例外是用户明确说明只需静态展示或视觉占位。
- 折线、面积、柱状、条形、饼图、环图、漏斗图、sparkline、迷你趋势图等任意图表形态，都不得退回成静态 SVG、纯 CSS 图形、`linear-gradient` 背景、结构式假图表、统计卡片、表格、进度条或简化趋势块。
- 当本次任务需要真实图表，但前 3 级都无证据时，必须先询问用户：使用 `ECharts`、改用其它指定图库，或等待用户安装。
- 若流程中漏问且必须继续，默认按 `ECharts` 真实图表方案处理，并在交付中标注采用了默认方案。
- 安装完成前结果只能标记为待安装 / 待完成，不得视为最终交付。
- 交付必须包含图表来源层级、业务语义到最终图表类型的映射、是否触发 `ECharts` 兜底、本次是否新增图表依赖。

## 高保真 HTML 全局约束

| 场景 | 全局规则 |
| --- | --- |
| HTML 样式优先级 | 已有高保真时，表现层样式以高保真还原为准，不再强要求 token 化。 |
| 默认外观 | 不要把 AntD / Element 默认外观当成视觉事实。 |
| 组件默认行为冲突 | 必须显式配置 props 或补样式。 |
| 行内原子内容 | 不要拆坏数字 + 单位、金额 + 币种、数值 + `%` 等同一视觉行。 |
| 大页面布局 | 父级已决定宽高和分栏关系时，子级优先继承 / 拉伸 / 比例，不重复写整套宽高。 |
| 父级 flex 继承 | 父级已是 flex 且子项明显等分时，子项优先 `flex: 1` / 百分比 / grid 轨道继承。 |
| 上下结构方向 | 区域明显上下排列时，优先保留普通块流或 `flex-direction: column`。 |
| 失败回退 | 先退回更小槽位，再不行退回 fidelity-only。 |

## 回检决策规则

- 主线交付总结和是否进入 review 是两个连续但不同的步骤。
- 主线 route 可以汇报结果，但不能用“如需我可以继续回检”替代真实决策。
- 顶层收到主线结果后，必须立即发起是否进入 `route/review/SKILL.md` 的确认。
- 若当前运行模式支持 `request_user_input`，必须按共享 `ask-user-question.md` 协议发起 `继续回检 (Recommended)` / `先不回检` 选择。
- 若当前运行模式不支持 `request_user_input`，必须显式追问：`是否现在进入回检流程？请回复“继续回检”或“先不回检”。`
- 未拿到用户明确答复前，不得自动进入 review，也不得把主线交付当成整轮结束。

## 执行清单

1. 确认 Feature、workflow profile、checkpoint 和 `CODE_WORKSPACE`。
2. 读取 `inspect_skill_contract.py` 输出的 Source Bundle / Method Bundle。
3. 优先读取 系统约束，再读取项目说明、组件文档和目标代码。
4. 确认用户提供了 HTML 文件、HTML 片段或可读取 HTML 内容；否则停止要求补充 HTML。
5. 按路由规则进入 `route/with-absolute-html/SKILL.md` 或 `route/with-standard-html/SKILL.md`。
6. 生成或修改与项目结构匹配的前端代码。
7. 校验字段、标题、按钮、表格列、Tab、展开/收起区、图标、图表和明显交互没有增减或丢失。
8. 运行项目适配的验证命令；无法运行时说明原因。
9. 汇报页面名称、页面位置、变更文件、跳过步骤、是否触发兜底、验证状态、剩余风险和新增依赖。
10. 主线完成后必须按 §回检决策规则 确认是否执行 review；只有用户明确确认后，才进入 `route/review/SKILL.md`。
