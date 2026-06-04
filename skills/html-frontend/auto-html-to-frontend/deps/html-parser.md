---
name: html-parser
description: 统一负责含 HTML 路线的整页还原、结构定性、局部组件替换，并直接写入项目目标目录。组件来源优先走项目 architecture 中的公共组件说明，其次才是本地组件与项目已安装组件库。
---

# HTML 解析器

只有在 `route/route-with-html.md` 已确认用户提供了 HTML 文件、片段或链接之后，才使用这个依赖。

这是含 HTML 路线里唯一负责“整页还原 + 局部组件替换”的核心依赖。

## 0. HTML 即视觉契约（最高优先级规则）

只要用户提供了高保真 HTML，**字体、字色、背景色、边框、圆角、阴影、渐变、不透明度等表现层属性一律以 HTML 还原为准**，不再强行套用 design system / token。

design system / token 只用于：

- HTML 未给出的交互态（hover / focus / disabled / active）
- 跨页面共享的语义色（success / error / warning / info）且 HTML 未指定
- AntD 组件本身未被 HTML 覆盖的默认外观

冲突时 **HTML 赢**。例如不要把 HTML 的 `#FE2842` / `4px` 圆角 / `28px` KPI 数字主动改成 token 的 `#FF4D4F` / `2px` / `32px`。把 HTML 中实际命中的视觉值保留为页面内常量或局部样式；只对 HTML 没明确覆盖的部分调用 token。

## 输出约定

根据 HTML 类型，产出以下一种主结果：

| HTML 类型 | 输出 |
| --- | --- |
| 可直接映射的框架/组件 HTML | 目标项目页面/组件文件 + 样式文件 |
| 原生语义 HTML | 目标项目页面/组件文件 + 样式文件 |
| 纯 `div` / 绝对定位 / Figma 导出 | 分阶段分析结果 + 目标项目页面/组件文件 + 样式文件 |
| 局部片段 | 目标项目中的局部代码修改 + 缺失区域说明 |

默认直接写入项目中应该生成或修改的目标目录，不再默认产出 `output/transfer/task-{N}.tsx`、`output/transfer/task-{N}.less` 这种中间文件。

## 1. 读取输入

按需读取：

- 用户提供的 HTML
- 当前工作区 `prd.md`
- 当前工作区接口说明文档（如 `api.md`、`接口文档*.md`、`接口说明*.md`）
- 当前目标文件（如果这是增量修改）
- 项目 `architecture/` 中与应用结构、技术栈、组件使用说明相关的文档
- `references/page-archetypes.md`（用于先定页面大骨架）
- `references/interaction-contracts.md`（用于浮层、工具栏、表单布局等高风险交互）
- `references/components-match-rules.md`（用于区域到组件的细粒度匹配）
- 只有在必要时才读取相关项目组件源码

组件来源遵循 `SKILL.md` §组件来源优先级。

如果 HTML 来自 URL 且无法获取实际内容，停止并要求用户提供 HTML 文件或内容。

## 2. 确认目标技术栈

优先读取项目 `architecture/` 中的技术栈说明。
如果项目 `architecture/` 没有说明，再必要时扫描真实项目：

```bash
rg '"(react|vue|antd|antd-mobile|element-plus|@ant-design/pro-components|@ant-design/icons|ahooks|@tanstack/react-query)"' package.json
rg --files -g "*.tsx" -g "*.jsx" -g "*.vue" src
```

输出目标必须锁定为项目真实技术栈：

- React -> `.tsx` / `.jsx` + 项目实际样式方案
- Vue -> `.vue` + 项目实际样式方案
- 未知 -> 按 React + AntD 保守回退，但不要只停在分析产物

## 3. 分类 HTML

转换前必须先分类：

| 类型 | 识别信号 | 处理方式 |
| --- | --- | --- |
| 框架/组件 HTML | AntD / Element / 组件式标签明显 | 保留结构并映射到项目组件 |
| utility class 高保真 HTML | Tailwind / 原子 class 中直接写 `w-[...]`、`h-[...]`、`flex`、`gap-*`、`rounded` | 先恢复 class 对应样式，再做组件识别 |
| 原生语义 HTML | 有 `form`、`table`、`button`、`input`、`section` 等 | 直接转换为框架组件 |
| 纯 div | 基本都是 `div` / `span`、内联样式多 | 结合纯 div 规则处理 |
| Figma / 绝对定位导出 | `position:absolute` 高占比、坐标值大、类名生成感强 | 先走分阶段恢复，再生成代码 |
| 局部片段 | 壳层不完整或只是一段组件片段 | 只转换当前区域 |

默认分析产物：

```text
output/html-analysis/task-{N}.json
output/html-analysis/task-{N}.md
```

其中 `.md` 是主 Stage 1 handoff，`.json` 用作定点校验源。

## 3.1 检测图标信号

在 HTML 转代码前，额外抽取图标信号：

| 信号类别 | 具体信号 |
| --- | --- |
| SVG / 向量 | `svg` / `path` / `use` / `symbol` |
| 图片类图标 | `img`、雪碧图、背景图里的小图标 |
| 类名类图标 | `i` 标签、`icon-*`、`iconfont`、`anticon`、`el-icon` |
| 交互类图标 | 纯图标按钮、表格操作图标、输入框前后缀图标、状态图标 |
| 语义类图标 | `prd.md` / 文案中只有语义、没有具体图形的操作词 |

处理规则：来源与兜底遵循 `SKILL.md` §图标来源顺序；Stage 1 handoff 中记录关键图标映射，供 Stage 2 和最终代码生成使用。

## 3.2 检测图表信号

在 HTML 转代码前，额外抽取图表信号：

| 信号类别 | 具体信号 |
| --- | --- |
| 直接图表信号 | `canvas`、图表 `svg`、坐标轴、图例、tooltip、series、数据标签、趋势线 |
| 图形结构信号 | 柱状图、条形图、折线图、面积图、饼图、环图、雷达图、漏斗图、散点图、仪表盘 |
| 页面结构信号 | 统计卡片 + 趋势区、时间维度切换、图表筛选器、图表 Tab |
| 容器命名信号 | 类名、数据属性、容器命名里的 `chart`、`graph`、`trend`、`analysis`、`echarts`、`g2`、`plot` |
| 语义信号 | `prd.md` / 文案中只有语义、没有具体图形的分析类模块 |

处理规则：

| 场景 | 规则 |
| --- | --- |
| 匹配优先级 | 固定按 图表 > 百分比进度条 > 渐进 / 渐变等装饰性背景或线条 |
| 折线 / 面积背景 / 柱条 / 饼 / 漏斗等图表形态 | 优先按图表还原，不要先降级成 `linear-gradient` 装饰或 `progress-line` |
| 同区域同时出现多种图表信号 | 先按图表信号计分，`linearGradient` 只作为辅助线索 |
| 其余来源、兜底与汇报 | 遵循 `SKILL.md` §图表来源顺序；handoff 中记录类型映射与图表来源 |

## 4. 组件来源选择

完整顺序与通用规则见 `SKILL.md` §组件来源优先级（图标 / 图表同表）。HTML 转换阶段的额外细则：

- 一次只对一个区域做组件替换，逐区评估
- 命中第 1、2 层时，先按项目文档理解用途 / props / 示例，再用真实源码确认导入路径
- 项目自身组件体系（前 3 层）无法覆盖时才进入已安装组件库层
- 优先用 `references/page-archetypes.md` 判断页面属于 detail / list-table / compare-detail / tabbed-report / modal-form 哪一类，再决定骨架
- 对筛选区、表格、头像列、图标按钮、状态点、图表、数值行、大布局容器等区域，优先参考 `references/components-match-rules.md`
- 对 `+N`、更多、工具栏分组、横向紧凑表单、预览可移植性等交互，优先参考 `references/interaction-contracts.md`

## 4.1 反推组件 props 与样式补丁

在把某个区域替换成组件之前，先为该槽位显式确认四件事：

1. 组件候选
2. 关键 props / mode / layout
3. 额外样式补丁
4. 失败时的回退方案

判断信号优先来自：

- 原始 HTML 的几何形态、边框、背景、圆角、对齐、间距、信息位置
- `output/html-analysis/task-{N}.md` 中的区域语义与 bbox
- 项目真实源码里的组件 props 使用方式和样式覆写方式

通用规则：

- 只有组件类型和关键 props 都能从高保真 HTML 反推出来时，才允许整块替换
- 如果组件默认行为与原稿冲突，先显式配置 props，再补样式；不能直接接受默认外观
- 如果为适配组件而必须改变上下布局、边框状态、信息位置、几何形状或文本节奏，则该组件不适合当前槽位
- 如果原稿中的一组内容属于同一视觉行，就不能因为节点拆分、块级元素、默认 `Descriptions` / `Space` / `Typography` 排版或错误的 `display` 推断而把它拆成两行
- 如果父级容器已经决定了区域宽高或分栏关系，子级不要重复把宽高全部写死；优先用 flex / grid / 百分比 / 拉伸约束继承父布局
- 如果源 HTML 已经给出 `display`、`flex-direction`、`justify-content`、`align-items`、`gap`、`overflow` 等样式信号，转换时优先保留，不要在组件化过程中丢失
- 对高保真 HTML，优先保留原有视觉样式和层级关系，不要为了“更工程化”随意重排或重写布局语义
- 宽高约束以“少写死、够还原”为原则：能继承父级、靠 flex / grid / 百分比 / 拉伸约束表达的，不要重复写整套固定宽高；只有局部稳定视觉盒子才使用固定尺寸
- 如果父容器已经是 `flex`，且子项视觉上明显是等分列 / 等分行，优先把子项改写成 `flex: 1`、等分 grid 轨道或明确比例；不要在每个子项上继续写死同一套宽度
- 如果一个区域从标题、正文、数值块到辅助信息都明显按纵向堆叠，优先保留普通文档流或使用 `display: flex; flex-direction: column;`；不要为了统一模板把它误写成横向 `flex`
- 如果一组卡片在视觉上明显属于等分布局，允许把多个固定宽度卡片转换成 `flex: 1` / 栅格等分列
- 如果一组卡片在视觉上明显属于不等分布局，允许把固定宽度抽象成固定 `flex-basis` + 其余列自适应，或抽象成明确比例列
- 最外层页面容器不要机械照抄 HTML 的最大固定宽高；优先保留内容上限和内边距，再让页面外层由父容器或业务壳层决定伸缩

高风险组件必须额外检查：

- `Badge` / `Tag`：
  - 彩色小圆点 + 相邻状态文本，且没有完整 pill 背景/边框/包裹块时，优先判定为 `Badge` 或自定义 dot + text，而不是 `Tag`
  - 只有当整个区域本身就是带背景、边框、圆角的标签块时，才允许使用 `Tag`
- `Descriptions`：
  - 只有当 label-value 说明表语义明确，且 `layout`、`column`、`bordered`、冒号、间距都能贴近原稿时，才允许使用
  - 上下布局且无边框的详情块，不能因为“像详情”就直接套默认 `Descriptions`
- `Progress`：
  - 条状进度必须映射到 `type="line"`
  - 只有存在明确圆形或仪表盘几何特征时，才允许 `circle` / `dashboard`
  - 必须同时反推 `percent`、`status`、`showInfo`、颜色和尺寸
- 漏斗图：
  - 如果原稿已明显表现为逐层收窄的梯形 / 多段漏斗，优先按真实漏斗层级和收窄关系还原，不要降级成普通纵向卡片堆叠
  - 必须保留层级顺序、每层相对宽度差、标签 / 数值对应关系，以及必要的分层间距
  - 若使用图表库实现，优先选择可直接表达 funnel 的方案；若走 fidelity 结构，也要保持梯形几何而不是简单矩形
- 行内原子内容：
  - 数字 + 单位、金额 + 币种、数值 + `%`、统计值 + 短尾缀、主文案 + 短状态尾标，如果原稿处于同一视觉行，必须保持同一行
  - 若默认排版会拆行，优先使用 `inline-flex` / `display: inline-block` / `white-space: nowrap` / `gap` / `align-items: baseline`
  - 只有在原稿本身就换行，或 bbox 明确分成两行时，才允许拆成两行
- 大布局容器：
  - 整页、大分栏、大面板、主内容区、左右区块，优先使用 flex / grid / 百分比 / `minmax` 等活动布局
  - 如果父元素已经有宽高，子元素优先继承、拉伸或按比例分配，不要层层重复写死 `width` / `height`
  - 只有在确实需要锁定某个局部视觉盒子时，才写固定尺寸
  - 明显上下主次结构默认按纵向处理；只有横向并列证据明确时，才使用左右 `flex` 排布

## 5. Stage 1 / Stage 2 一体化执行

当存在 HTML 时，保持两阶段心智，但在同一个依赖里完成：

1. Stage 1：整页视觉恢复 + 结构定性
2. Stage 2：局部组件落地

也就是说：

- 先把整页结构、区域边界和内容恢复稳
- 再在明确槽位里做组件替换，并为高风险槽位补齐 props 与样式还原
- 最终直接写入项目目标目录

## 5.1 端到端示例（happy path）

下面用一段真实的 Tailwind 高保真 HTML 走完整个流程，作为模型对齐参考。
任何环节落不到这里描述的"做什么、产出什么"，就回到上面对应小节复查。

### 输入

```html
<div class="w-[780px] h-[160px] p-4 bg-white rounded border border-gray-300 flex flex-col gap-4">
  <div class="text-base text-gray-800">活跃用户指标</div>
  <div class="w-[748px] h-[88px] flex gap-4">
    <!-- 3 张 metric card，每张含：label + i 图标 + 大数字 + 单位 + 较同期 + 三角 + 百分比 + sparkline -->
    <div class="w-[228px] h-[88px] ...">
      <span>日均DAU</span> ...
      <span class="text-[28px] font-bold text-gray-800 opacity-80">2,256</span>
      <span>人</span>
      <span class="text-xs text-gray-400">较同期</span>
      <svg ...> <!-- 三角箭头 --> </svg>
      <span class="text-xs text-green-500">1.56%</span>
      <svg ...> <!-- 120×32 sparkline polyline --> </svg>
    </div>
    <!-- UV -->
    <!-- 深度用户数 -->
  </div>
</div>
```

### Stage 0：分类与读契约

- **HTML 类型**：utility class / Tailwind 高保真（命中 §3 第 2 行）
- **视觉契约锁定**（§0 规则）：
  - 卡片背景 `bg-white`、边框 `border-gray-300`、圆角 `rounded`（约 4px）
  - KPI 数字 `text-[28px] font-bold text-gray-800 opacity-80`
  - 同行原子内容：`数值 + 单位`、`较同期 + 三角 + 百分比`，必须保持单行
  - 趋势色：上行 `text-green-500`（HTML 实际取 `#00A870`），下行 `#FE2842`
  - **不要**主动把 4px 改 12px、不要把 28px 改 32px、不要把 `#00A870` 改成 token success

### Stage 1：还原结构

| 区域 | 还原结论 |
| --- | --- |
| 外层 Panel | 1 个 `Card`，标题 + 3 列指标 |
| 内层 MetricCard | 3 个，等宽 228px，可抽 `flex: 1` |
| InfoTooltip 槽位 | 每张卡 label 后跟 1 个小问号图标 |
| TrendDelta 槽位 | 每张卡含 "较同期 + 三角 + 百分比" |
| Sparkline 槽位 | 每张卡右侧 120×48 折线+渐变面积 |

输出：

```
output/html-analysis/use-detail-1.md   # Stage 1 handoff
output/html-analysis/use-detail-1.json # 定点校验
```

### Stage 2：组件反推与落地

按 §4.1 给每个高风险槽位写组件契约：

| 槽位 | 候选组件 | 关键 props / 样式补丁 | 失败回退 |
| --- | --- | --- | --- |
| 外层 Panel | AntD `Card` | `bordered`, `styles.body.padding=16`, `borderRadius=4` | 纯 div + border |
| MetricCard | 不再套 Card | div + flex，避免双重边框 | — |
| label + i 提示 | `Tooltip` + `InfoCircleOutlined` | `fontSize: 12`，颜色取自 HTML | 静态 `?` 文本 |
| 较同期 ↑ 1.56% | `CaretUpFilled / CaretDownFilled` + 文本 | `inline-flex + nowrap`，颜色取自 HTML | 纯文本箭头 `↑` |
| sparkline | `ReactECharts` | 120×48，无坐标轴，渐变面积 + 描边色 | 静态 svg polyline 保留 |

确认完契约后才写文件：

```
src/pages/usage-detail/index.tsx          # 主页面
src/pages/usage-detail/components/
  MetricCard.tsx
  Sparkline.tsx
  TrendDelta.tsx
src/pages/usage-detail/index.module.less   # 仅放 HTML 视觉契约里有、token 无法表达的局部值
```

### Stage 3：校验

按 §8 核对：

- 4px 圆角、28px 字号、`#00A870` / `#FE2842` 已落到代码，未被 token 覆盖
- "2,256 人"、"1.56%"、"较同期" 等原子行没拆行
- 6 个 sparkline 都还在
- import 可解析，关键图标 `InfoCircleOutlined / CaretUpFilled / CaretDownFilled` 没漏
- 内层 MetricCard 没有 double `Card`

### 汇报

```text
【HTML 转换结果】
HTML 类型：utility class / Tailwind 高保真
来源：uploads/使用明细2.html
写入文件：
- src/pages/usage-detail/index.tsx
- src/pages/usage-detail/components/MetricCard.tsx
- src/pages/usage-detail/components/Sparkline.tsx
- src/pages/usage-detail/components/TrendDelta.tsx
- src/pages/usage-detail/index.module.less
组件来源：local components + AntD（fallback）
图标来源：@ant-design/icons（AntD 兜底）
图表来源：ECharts 兜底（项目无现成图表封装）
视觉契约：HTML 优先；圆角 4px / 字号 28px / 趋势色 #00A870 #FE2842 保留
验证：通过
```

## 6. 项目说明优先级

遵循 `SKILL.md` §项目说明优先级。应用结构、API、技术栈、路由/菜单/权限、状态管理、样式体系都先看项目 `architecture/`，缺失时回退到源码扫描。

## 7. 写入文件

默认直接写项目文件，必须跟随项目真实命名与样式约定。

## 8. 校验

交接前检查：

- import 路径可解析
- style class 存在
- 没有残留模板内容
- JSX 中没有 HTML 注释
- 必填字段和按钮文案没有丢失
- 关键图标没有丢失，图标导入与引用可解析
- 纯图标按钮补了 `Tooltip` 和 `aria-label`
- 关键图表没有丢失，图表容器、数据结构和类型映射能自圆其说
- 如果用了第三方图表库，必须有当前工程真实使用证据
- 高风险组件没有发生模式误判，例如 dot status -> Tag、line progress -> circle progress、vertical plain detail -> default Descriptions
- 关键组件的 props / mode / layout 与源稿视觉一致
- 数字 + 单位、金额 + 币种、数值 + `%` 等原子行内内容没有被误拆成两行
- 大页面和大模块没有无意义地层层写死宽高；父子尺寸关系主要通过继承、flex、grid 或百分比表达
- 复用组件都有真实项目证据
- 如果用了项目文档中的组件，也已经确认过源码和导出方式

汇报格式：

```text
【HTML 转换结果】
HTML 类型：<type>
来源：<file/url>
写入文件：<paths>
组件来源：publicComponents / shared-components / local components / installed library / fidelity-only
图标来源：project icon rules / local icons / installed icon library / React + AntD fallback
图表来源：project chart rules / local charts / installed chart library / conservative fallback
验证：通过 / 待修正
```
