---
name: standard-html-parser
description: 处理标准 DOM / 语义明确 HTML 的独立 route 依赖。该依赖以 `standard-html-parser` 名义承载标准 HTML 转 React 工程代码能力，用于把 HTML、静态页面或复制 markup 转为现有工程中的可维护 React 代码或组件；同时遵循根 SKILL.md 的组件优先级、图标/图表规则、技术栈兜底和回检契约。
---

# 标准 HTML 解析器

本依赖只在上层 route 技能 `../SKILL.md` 已经把当前输入明确判定为“标准 DOM / 语义明确 HTML”后进入。它替代原有薄版 `standard-html-parser`，作为 `with-standard-html` 路线的 HTML 转工程代码主执行技能。

本文中提到的 `SKILL.md` 均指仓库根技能 `../../../SKILL.md`。若本文和根技能冲突，以根技能的全局优先级、图标/图表规则、依赖安装确认规则和回检决策规则为准。

## 1. 入口契约

进入本依赖前，必须已经完成：

1. 明确把输入判定为 `standard-html`，即标准 DOM、语义结构、表单 / 表格 / flex / grid / class 规则较清晰。
2. 确认未命中 `../../with-absolute-html/SKILL.md` 定义的强制绝对定位 / 设计导出稿信号。
3. 准备原始 HTML 与目标工程上下文。
4. 保留原始 HTML 作为最终视觉与内容事实源。

## 2. 核心定位

本依赖的目标不是把 `class` 简单替换成 `className`，而是把原始 HTML 转成真实工程中可维护、可运行、可复用的 React 代码。

必须同时做到：

- 保留源 HTML 的业务语义、内容完整度、视觉结构和交互意图。
- 按根技能优先级复用项目公共组件、本地组件和已安装且真实使用的组件库。
- 对产品 UI 中的表单、表格、筛选、导航、反馈、上传、弹窗等结构，优先映射到真实项目组件或 Ant Design。
- 对品牌化、文章化、营销化、异形视觉和高度自定义布局，保留原始语义 HTML / CSS，不强行 AntD 化。
- 在主线阶段完成组件、数据、常量、类型、helper、hook、图标和图表配置抽取，不把维护性工作留到回检阶段。

## 3. 读取顺序

1. 读取原始 HTML 文件或用户提供的 HTML 内容。
2. 从 HTML 来源目录和目标工程目录向上查找并读取 `AGENT.md` / `AGENTS.md`，更近的规则优先。
3. 读取项目 `architecture/`、组件说明、API 说明和相似页面。
4. 扫描真实源码确认导入路径、组件导出、组件用法、样式方案、路由结构、包管理器和已安装依赖。
5. 如决定使用 Ant Design，读取 `../references/ant-design-conversion.md`；若该文件不存在，则按本文 `§7 Ant Design 转换规则` 执行。
6. 如涉及 YAPI 或真实接口，读取顶层 `../../../deps/yapi-fetcher.md` 的产物或项目 API helper。

## 4. Craft：编码前简报

写代码前先形成一个短实现简报，并据此落地：

1. 源 HTML 范围、依赖、外部资源和可用资产。
2. 目标工程位置、路由位置和文件组织。
3. 组件边界与抽取标准。
4. 项目组件 / Ant Design / 原生 HTML 的映射计划；当 Ant Design 适用或可能适用时，必须补充 Ant Design 映射矩阵。
5. 样式策略：CSS Modules、Less、Tailwind、普通 CSS、styled-components 或项目既有方案。
6. 需要保留或重建的交互：tab、展开收起、表单提交、分页、筛选、弹窗、上传、排序等。
7. 需要支持的状态：默认、loading、empty、error、disabled、hover/focus、responsive。
8. 验证计划：静态检查、构建、运行、浏览器预览、响应式检查。

若用户要求像素级或高保真迁移，源 HTML 就是设计简报；不要主动归一化成 AntD 默认外观。若用户要求工程化清理或后台产品 UI，允许在不丢失语义和业务层级的前提下把标准控件映射到项目组件或 Ant Design。

### 4.1 Ant Design 映射矩阵

当 Ant Design 适用或可能适用时，编码前创建 Ant Design 映射矩阵。小页面保持紧凑，复杂后台 / 产品 UI 需要覆盖全部候选结构：

| 源 HTML | 意图 | Ant Design 组件 | 转换? | 原因 |
| --- | --- | --- | --- | --- |
| `<button class="primary">` | 主操作 | `Button type="primary"` | yes | 行为型控件 |
| `<table>` | 记录表格 | `Table` | yes | 结构化行列数据 |
| `<select>` | 单选选择 | `Select` | yes | 产品表单控件 |
| 品牌视觉容器 | 自定义视觉布局 | 原生 React / CSS | no | 保真优先 |

把矩阵作为转换清单使用。每个产品 UI 控件、数据面、导航模式、反馈元素、overlay、表单控件候选，都要进入矩阵并给出 convert / keep 决策。

## 5. Parse And Normalize

优先用结构化解析、DOM 观察和项目上下文理解 HTML，不要只靠正则替换。

转换时必须处理：

- `class` -> `className`
- `for` -> `htmlFor`
- HTML 内联样式 -> React style 对象或组件样式类。
- SVG 属性 -> React 兼容命名。
- 重复内容 -> 数据数组 + map 渲染。
- 表单控件 -> 按项目惯例转成受控或非受控模式。
- 内联脚本 -> React state、事件处理、effect 或小工具函数。
- CDN 脚本 -> 尽量替换为包依赖或 React 原生实现，不默认保留第三方 CDN。
- 不安全或过时写法 -> 删除或改写；除非用户明确要求保留，否则避免 `dangerouslySetInnerHTML`。

不得静默丢弃：

- 文案、字段、按钮、表格列、状态标签、图标、图表、分页、tab、弹层、上传区。
- 源 HTML 中可识别的默认值、禁用态、选中态、校验提示、空态、错误态和 loading 态。
- 图片、字体、SVG、背景图等资产；无法取得时在交付中明确说明。

## 6. 组件复用与抽取

### 6.1 组件来源

组件来源严格遵循根 `SKILL.md`：

1. 项目 `AGENTS.md` 指定的公共组件库。
2. 项目 `architecture/components/`。
3. 项目本地 `components` / `src/components`。
4. 当前工程已安装并在源码中真实使用的组件库。
5. 用户提供的兜底组件库。
6. 相似页面模式。
7. fidelity-only。

没有源码、导出或真实用例证据时，不要强行使用某个组件。

### 6.2 抽取标准

使用 Bounded Responsibility Standard：只有当组件有清晰语义责任，并至少命中一个强信号时才抽取。

强信号包括：

- 代表主要页面区域或业务对象。
- 同一视觉 / 语义模式出现至少两次，或列表有三项及以上。
- 拥有交互、状态、生命周期或可访问性逻辑。
- 留在父组件中会让父组件难以阅读，通常是区域约 50 行以上 JSX 或混合多个无关职责。

保持内联的情况：

- 小型一次性结构。
- 与父组件强耦合。
- 抽出来只会传递 props 包一层 `div`。
- 视觉上只是布局壳层且没有独立语义。

常见抽取目标：

- 页面壳层、导航、筛选区、统计区、表格区、图表区、详情区、弹窗区。
- 重复卡片、功能行、表单分组、图片列表、数据视图。
- 大型静态数据数组、表格 `columns`、选择项 `options`、tab `items`。
- 重复行为的 hook、utility、formatter。
- 重复出现且语义稳定的颜色、间距、字体、阴影、动效 token。

## 7. Ant Design 转换规则

### 7.1 何时使用 Ant Design

按以下顺序判断：

1. 项目 `AGENTS.md` 或组件说明已有组件库规则时，先遵守项目规则。
2. 用户明确要求 Ant Design，或 HTML 明显是后台 / 产品 / 管理端 UI 时，使用 Ant Design 映射标准控件。
3. 新 React 项目中，如果 HTML 包含表单、表格、导航、反馈、仪表盘控件，默认可使用 Ant Design。
4. 现有项目未安装 Ant Design 且用户未明确要求时，新增依赖前必须按根技能规则向用户确认。
5. 异形视觉、营销内容、文章内容、自定义插画和 Ant Design 会明显降低保真的区域，保留原生 / 自定义 React markup。

### 7.2 版本门槛

写 Ant Design 代码前必须检查：

- `package.json`、lockfile、现有 imports 和源码用法。
- Ant Design major 版本。仅支持 v4 / v5 口径。
- v4 项目保留 `antd/dist/antd.css`、Less 变量主题、`visible` 等本地既有约定。
- v5 项目优先遵守 `antd/dist/reset.css`、`ConfigProvider`、theme token、`App` provider、`open` props 和 `items` API 等本地既有约定。
- 项目有 AntD wrapper 时，用 wrapper，不直接导入裸组件。
- `@ant-design/icons` 要和项目版本及已有用法匹配。

如果项目使用其它 AntD major，必须询问用户是对齐 v4/v5、跳过 AntD 转换，还是使用项目当前组件体系。

### 7.3 常见映射

| HTML / 语义 | React / AntD 映射 |
| --- | --- |
| action button / action link | `Button`，保留 `htmlType`、`danger`、`disabled`、`loading`、`href` |
| text/password/search/textarea | `Input` / `Input.Password` / `Input.Search` / `Input.TextArea` |
| select / radio / checkbox / switch / slider | `Select` / `Radio.Group` / `Checkbox.Group` / `Switch` / `Slider` |
| date/time/range | `DatePicker` / `TimePicker` / 对应 range picker |
| real form | `Form` + `Form.Item`，规则来自 HTML validation 和源提示 |
| upload/dropzone | `Upload`，保留 `multiple`、`accept`、列表与 preview 语义 |
| record table | `Table`，抽取 `columns`、`dataSource`、`rowKey` |
| simple repeated record | `List` 或自定义 map；不要把所有内容都包成 `Card` |
| metric | `Statistic`，保留 prefix/suffix/precision |
| status/category/chip | `Tag` / `Badge` |
| nav/sidebar/top menu | `Menu`，不要误转成 `Tabs` |
| same-page tab panels | `Tabs`，稳定 `key`，保留 active/default state |
| breadcrumb/steps/pagination | `Breadcrumb` / `Steps` / `Pagination` |
| modal/side panel/confirm | `Modal` / `Drawer` / `Popconfirm`，按 v4/v5 使用 `visible` 或 `open` |
| accordion/timeline/tree/calendar | `Collapse` / `Timeline` / `Tree` / `Calendar` |
| inline persistent message | `Alert` |
| loading/placeholder/progress | `Spin` / `Skeleton` / `Progress` |
| empty/success/error result | `Empty` / `Result` |

### 7.4 数据模型

写 JSX 前先抽数据：

- `options`：`Select`、`Radio.Group`、`Checkbox.Group`、`Segmented`、`AutoComplete` 等。
- `items`：`Menu`、`Tabs`、`Breadcrumb`、`Steps`、`Dropdown` 等。
- `columns`：`Table` 列定义。
- `dataSource`：表格、列表和仪表盘数据。
- `treeData`：树形选择、树菜单、层级数据。
- `initialValues`：表单默认值。

稳定 key 来源优先级：源 id、`name`、`value`、`href`、`data-*`、业务行 id、标签 slug；数组 index 只能最后兜底。

### 7.5 覆盖审计

当 Ant Design 被用户要求、被本路线选择，或被后台 / 产品 UI 强烈暗示时，实现后必须做 Ant Design 覆盖审计。

审计 JSX / TSX 源码，不审计浏览器运行时 DOM；Ant Design 组件自身会渲染原生 `button`、`input`、`table` 等 DOM，不能据此误判。

重点检查源码中残留的原生产品 UI 候选：`button`、`input`、`textarea`、`select`、`option`、`form`、`table`、`thead`、`tbody`、`tr`、`td`、`dialog`、`details`、`summary`、`progress`、`meter`、tablist、modal、alert、pagination、upload、menu、filter、validation hint。

每个剩余候选必须二选一：

- 转换成合适的项目组件或 Ant Design 组件。
- 明确说明保留 native/custom 的原因，例如自定义视觉、编辑内容、可访问性 / 保真约束、项目依赖不可用。

从本技能仓库根目录运行：

```bash
python route/with-standard-html/scripts/audit_antd_coverage.py <target-react-project-or-src> --format markdown
```

脚本只扫描 `.tsx` / `.jsx` 源码。退出码 `0` 表示未发现候选项；退出码 `1` 表示发现可能遗漏的 Ant Design 转换候选项，应把输出作为待处理清单继续转换或说明，不视为脚本故障。

如果候选必须保留原生实现，在附近添加 `antd-audit-ignore` 注释并写明原因。本路线不要求新增前置 JSON 或 HTML 分析脚本。

### 7.6 禁止误用

- 不要把每个区域都转成 `Card`。
- 不要用 `Space` / `Flex` 替代页面级布局。
- 不要把路由导航转成 `Tabs`。
- 不要把简单营销 / 文章内容强行转成 AntD Typography。
- 不要把对比型静态内容表误转成复杂 `Table`，除非它是记录数据表。
- 不要为了“组件化”降低源 HTML 的视觉结构和业务层级。
- 不要在 Ant Design 转换后留下未审计 / 未说明的原生产品控件或数据面。

## 8. 图标与图表

### 8.1 图标

图标来源、兜底和汇报遵循根 `SKILL.md`。补充执行规则：

- 有明确形状或语义时，先匹配项目图标体系或已安装且真实使用的图标库。
- 标准 HTML 里的真实 `svg` / `symbol` / `use` / `path` 可作为匹配依据。
- 明显小型 icon 找不到足够接近的真实组件时，才保留原始 SVG。
- `sparkline`、迷你趋势线、迷你面积图仍按图表处理，不能降级成 icon。
- 纯图标按钮必须补 `Tooltip` 与 `aria-label`。

### 8.2 图表

图表来源、询问 / 兜底链、禁止退化和交付必含内容统一遵循根 `SKILL.md §图表来源顺序`。

只要需求或 HTML 语义是图表，默认使用真实图表库或项目图表组件；不得退化为静态 SVG、CSS 渐变、结构式假图表、进度条或统计卡片。

## 9. Layout

保留源 HTML 的空间层级。除非用户要求视觉重构，否则不要把标准 HTML 改造成另一套视觉。

布局要求：

- 使用语义化 landmark 和可访问结构。
- 样式方案匹配项目：CSS Modules、Less、Tailwind、styled-components、普通 CSS 或项目既有方案。
- Ant Design 组件优先通过 props、组件布局能力和 theme token 表达组件级行为；页面组合与品牌视觉用项目样式补齐。
- 响应式优先用 flex、grid、容器约束、稳定断点和明确 spacing。
- 文本不能溢出按钮、卡片、导航项或固定面板。
- 数字 + 单位、金额 + 币种、百分比 + `%`、主值 + 短尾标等原子内容保持同一视觉行。
- 大页面和大模块优先可伸缩，不要层层写死宽高。
- 保留源 HTML 的品牌、字体、颜色、间距和视觉节奏；只有在用户要求产品 UI 清理或源样式不完整时，才向项目组件默认样式靠拢。

## 10. 写入工程代码

### 10.1 现有工程

- 匹配项目导入别名、文件命名、路由结构、组件组织、样式方案、测试和格式化习惯。
- 避免无关重构。
- 保留用户已有改动。
- 如果目标页面已存在，先读当前文件，对比 HTML 差异，只改变化部分。
- 页面壳层、数据编排和局部组件拆分应和项目已有模式一致。

### 10.2 新 React 工程

只有在用户要求新建工程或当前没有可用 React 工程时才创建。默认 Vite + React + TypeScript，除非用户指定 JavaScript、Next.js 或其它框架。

新工程基础要求：

- `src/main.tsx`、`src/App.tsx` 或项目路由入口清晰。
- 组件放在 `src/components` 或既定结构。
- 静态资源按 import 需求放在 `public` 或 `src/assets`。
- 使用源 HTML 里的真实内容，不填充无关 placeholder。
- 需要 Ant Design 时只做必要配置，不做大范围主题重写。

## 11. 校验

交付前按项目可用命令执行验证：

- 安装 / 构建 / lint / test 中可用且合适的命令。
- 前端任务要启动 dev server，并用浏览器确认页面非空白、资源加载、布局无明显错位。
- 检查桌面和移动视口：溢出、裁切、文本重叠、按钮挤压、表格横向滚动。
- Ant Design 场景检查：样式是否加载、版本 API 是否正确、Form 默认值与校验、Radio/Checkbox/Select/Tabs 状态、Table `rowKey`、Modal/Drawer open/close、feedback API provider context。
- Ant Design 转换被请求或被选择时，运行 `python route/with-standard-html/scripts/audit_antd_coverage.py <target-react-project-or-src> --format markdown` 做覆盖审计；只看 JSX / TSX 源码，不看运行时 DOM；不得留下未审计 / 未说明的原生产品控件、表格、表单控件、弹窗、反馈、分页、上传或导航控件。
- 检查图标 / 图表来源层级和可访问性。
- 回查页面主区域、字段、标题、按钮、表格列、tab、展开收起区、图标、图表和明显交互是否有增减或丢失。

## 12. 汇报格式

主线完成后先汇报：

- 页面名称与页面位置。
- 变更文件。
- 使用的项目组件 / Ant Design / 原生自定义结构映射。
- Ant Design 映射矩阵与覆盖审计结论（仅在 Ant Design 适用、被请求或被选择时）。
- 图标来源层级与关键映射。
- 图表来源层级、类型映射、是否触发 ECharts 兜底、新增依赖。
- 联调状态：真实接口 / YAPI / API helper / Mock。
- 执行过的验证命令和结果。
- 跳过的步骤及原因。
- 新增依赖。
- 剩余风险。

随后必须返回顶层，由顶层立即发起“是否进入回检”的真实决策动作。未拿到用户确认前，不得自动进入 `../../review/SKILL.md`。

## 13. 禁止事项

- 不要停留在 JSX 语法转换，必须交付工程化 React 代码。
- 不要把绝对定位 / Figma 导出稿误走本依赖；命中强信号时返回 `../../with-absolute-html/SKILL.md`。
- 不要静默新增依赖；缺少组件库、图标库或图表库时按根技能确认规则执行。
- 不要保留明显可转为项目组件 / AntD 的后台产品控件为裸 HTML，除非保真或项目规则要求。
- 不要在 AntD 转换后留下未审计 / 未说明的原生产品控件、表单、表格、弹窗、反馈、分页、上传、导航或数据面。
- 不要把自定义视觉内容强行 AntD 化。
- 不要丢失资产、字段、状态、交互和数据列。
- 不要使用不稳定 index key，除非没有更稳定来源。
- 不要无依据使用 `any`。
- 不要把主线总结当成整轮结束；必须回到顶层做回检决策。
