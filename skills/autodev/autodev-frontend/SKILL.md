---
name: autodev-frontend
description: Dev 阶段 frontend_before_specs workflow profile 的 HTML 转前端实现节点。用于在行为规格生成前，基于 PRD、HTML、接口说明和现有前端工程完成前端实现；高保真/绝对定位 HTML 走本技能路线，普通静态 HTML 可分流到 html-to-react。
version: v1.1.1604
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-frontend --feature "$FEATURE_ID" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件，不要读取清单之外的阶段产物作为硬依赖。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）；按它决定读哪些部分、如何提取上下文。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止；契约未列出的产物不要硬等。
- **降级语义**：`external: true` 的输入不在本工作流内生成；缺失时按其 `extract.degrade` 的退化读法继续执行，不要因缺失而停止。

无 `$FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；用于前端代码探索、实现和验证。

# /autodev-frontend - HTML 转前端实现

本技能是 `frontend_before_specs` workflow profile 中的正式 Dev 节点。它处理用户明确提供的 HTML、截图或页面说明，并在进入行为规格前完成前端实现准备或代码落地。

面向“已有前端工程中的实现工作”。用户提供高保真/绝对定位/Figma 风格 HTML 时，先进入 `route/route-with-html.md`，按 HTML-first 主线完成 Stage 1 分析与转交，再由 `deps/html-parser.md` 指导落代码；用户提供普通静态 HTML、复制的 DOM 片段、小型静态站点，或明确要求 HTML 转 React/TSX/Vite/Next 时，分流到 `../autodev-html-to-react/SKILL.md` 的规则执行。`{FEATURE_DIR}/PRD.md` 与本地接口说明文档按可用情况用于校正文案、字段和交互边界。

不要再走单独的 PRD 解析、YAPI 获取、接口联调或中间任务文件生成流程。

## 流程状态

进入本技能前，当前 workflow profile 必须是 `frontend_before_specs`。如果由 `prd_done` 直接进入本技能，先使用统一脚本推进 checkpoint：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint frontend_in_progress --workflow-profile frontend_before_specs
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

完成前端实现和必要验证后：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint frontend_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

完成后汇报变更文件、验证命令、未覆盖风险，并提示下一步进入 `/autodev-specs`。

## 路线入口

HTML 路线的触发条件、失败条件、默认读取顺序与转交规则，以本节分流规则和 `route/route-with-html.md` 为权威来源。

如果用户未提供 HTML、截图或明确页面目标，先澄清输入，不要编造页面代码。

### 入口分流

`frontend_before_specs` 只有一个正式 workflow 节点和一组 checkpoint：`frontend_in_progress -> frontend_done`。`/autodev-frontend` 与 `/html-to-react` 不是两个串行阶段，而是同一节点下的两种入口。

| 输入形态 | 入口 |
| --- | --- |
| Figma/低代码导出的高保真 HTML、绝对定位/大量 inline style/像素坐标 HTML、纯 div 视觉稿 | 继续走本技能：`route/route-with-html.md` → `scripts/prepare_html_analysis.py` → `deps/html-parser.md` |
| 普通静态 HTML、复制的 DOM 片段、小型静态站点、语义化 HTML/CSS/JS，或用户明确说 HTML 转 React/TSX/Vite/Next | 分流到 `../autodev-html-to-react/SKILL.md`，不运行 `prepare_html_analysis.py` |

分流到 `html-to-react` 时，仍然沿用本节点的状态推进：如当前仍在 `prd_done`，先推进到 `frontend_in_progress` 并写入 `--workflow-profile frontend_before_specs`；完成转换和验证后推进到 `frontend_done`。

## 一页总览

| 维度 | 默认策略 |
| --- | --- |
| 项目说明来源 | `CODE_WORKSPACE/architecture/` → 源码扫描 → 技能内参考 |
| 需求来源 | `{FEATURE_DIR}/PRD.md`；如用户额外提供 `prd.md`，只作为补充校对 |
| 接口来源 | `CODE_WORKSPACE` 中的接口说明文档；不再走 YAPI、API helper、Mock 或联调流程 |
| HTML 主线 | 高保真/绝对定位 HTML：`route/route-with-html.md` → `scripts/prepare_html_analysis.py` → `deps/html-parser.md`；普通静态 HTML：`../autodev-html-to-react/SKILL.md` |
| 组件来源 | 项目公共组件 → 本地组件 → 已安装且在用的组件库 → 相似页面 → fidelity-only |
| 技术栈兜底 | 无法确认时按 React + AntD 继续 |
| 图标 | 默认检测与生成 |
| 图表 | 默认检测与生成 |
| 布局策略 | 保留高保真样式；宽高少写死，优先继承、拉伸、比例布局 |
| 代码质量 | 补简洁注释；优先抽取共享常量、类型、helper、hook 或配置 |

## 输入补充

进入高保真 HTML 路线后，先按 `route/route-with-html.md` 完成 HTML-first 读取与 Stage 1 handoff，再按可用情况补充。进入 `html-to-react` 分流时，按 `../autodev-html-to-react/SKILL.md` 的 intake/craft/extract/layout/publish 流程执行，再补充以下上下文：

- `{FEATURE_DIR}/PRD.md`
- 用户提供或 `CODE_WORKSPACE` 内的接口说明文档
- `CODE_WORKSPACE/architecture/` 中的结构、技术栈和组件说明

如果 PRD 或接口说明文档不存在，要在交接和汇报中明确说明缺失；不要改回旧的解析、抓取或联调流程。

## 核心原则

| 编号 | 原则 |
| --- | --- |
| 1 | 进入 HTML 转前端节点后，先判断 HTML 格式；高保真/绝对定位 HTML 走 `route/route-with-html.md`，普通静态 HTML 走 `../autodev-html-to-react/SKILL.md` |
| 2 | 先读项目说明文档，再扫项目源码 |
| 3 | 先读 `{FEATURE_DIR}/PRD.md`，字段、文案、交互和任务边界以它为准 |
| 4 | 先读接口说明文档，请求/响应字段和约束以它为准 |
| 5 | 先恢复整页与结构，再做局部组件化 |
| 6 | 没有 HTML、截图或明确页面目标时不生成页面代码 |
| 7 | 技术栈无明确证据时，不停在分析阶段；按 React + AntD 兜底交付 |
| 8 | 图标属于页面语义的一部分，默认检测与生成 |
| 9 | 图表属于页面信息结构的一部分，默认检测与生成 |
| 10 | 高保真 HTML 是视觉契约；不要因组件默认行为改变布局、边框、方向、形态或状态表达 |
| 11 | 同一行原子内容必须保持同一行，例如数字 + 单位、金额 + 币种、百分比 + `%`、主值 + 短尾标 |
| 12 | 大页面和大模块优先使用可伸缩布局，不要层层写死宽高 |
| 13 | 生成代码时，对函数、类型、关键变量和复杂逻辑补简洁注释 |

## 输入边界

| 来源 | 负责内容 |
| --- | --- |
| `{FEATURE_DIR}/PRD.md` | 字段、文案、交互、任务边界、页面业务语义 |
| 接口说明文档 | 接口路径、请求方式、参数、响应字段、枚举/状态约束 |
| HTML | 布局、结构、间距、视觉层级、组件槽位与视觉契约 |

边界优先级：

- 布局和视觉以 HTML 为准。
- 业务文案和交互以 `{FEATURE_DIR}/PRD.md` 为准。
- 数据字段与接口约束以接口说明文档为准。
- 如果三者互相冲突，先保留 HTML 布局，再在汇报中明确冲突点；不要私自编造第四套口径。

## 资源地图

| 需求 | 读取 |
| --- | --- |
| HTML 分析与 Stage 1 handoff | 高保真/绝对定位 HTML：`scripts/prepare_html_analysis.py` → `deps/html-parser.md`；普通静态 HTML：`../autodev-html-to-react/SKILL.md` |
| 项目公共组件索引、说明与示例 | 优先读 `CODE_WORKSPACE/architecture/publicComponents.md` |
| 项目共享组件使用方式 | 若没有 `publicComponents.md`，读 `CODE_WORKSPACE/architecture/shared-components.md` |
| 项目应用结构说明、技术栈、组件说明 | 优先读 `CODE_WORKSPACE/architecture/`；没有时再扫源码 |
| 页面原型与大骨架判断（按需读取） | `references/page-archetypes.md` |
| 组件槽位与高风险交互参考（按需读取） | `references/component-slot-map.md`、`references/components-match-rules.md` |
| 脱离式浮层、工具栏、表单布局等交互约束（按需读取） | `references/interaction-contracts.md` |
| 转换纯 div / Figma 风格 HTML | 优先读 `references/pure-div-core.md`；只有在需要给弱模型或下游模型传紧凑提示词时再读 `references/llm-prompt-template.md`；只有必要时再读 `pure-div-converter.md` |

## 全局优先级

### 组件来源优先级

| 优先级 | 来源 |
| --- | --- |
| 1 | `CODE_WORKSPACE/architecture/publicComponents.md` |
| 2 | `CODE_WORKSPACE/architecture/shared-components.md` |
| 3 | 项目本地 `components` / `src/components` |
| 4 | 当前工程已安装并实际在用的组件库 |
| 5 | 相似页面模式 |
| 6 | fidelity-only |

规则：

- `publicComponents.md` 是最高优先级说明源。
- 第 1、2 层命中时，先按文档理解用途、props、导入方式与示例，再用真实源码确认导出与路径。
- 没有源码、导出或真实使用示例时，不能因为规则命中就强行使用。

### 项目说明优先级

| 优先级 | 来源 |
| --- | --- |
| 1 | `CODE_WORKSPACE/architecture/` 下的说明文档 |
| 2 | 项目真实源码结构与相似页面 |
| 3 | 技能内参考文档 |

### 技术栈兜底

当项目技术栈无法从以下证据识别时：

| 顺序 | 证据 |
| --- | --- |
| 1 | `CODE_WORKSPACE/architecture/` 说明文档 |
| 2 | 真实源码扫描结果 |

统一按 React + AntD 继续执行。

补充规则：

- 用户明确指定 Vue / React / 其它框架时，用户指令优先。
- 汇报时必须明确标记“使用 React + AntD 兜底”。

## 图标 / 图表权威规则

### 图标来源顺序

| 优先级 | 来源 |
| --- | --- |
| 1 | `CODE_WORKSPACE/architecture/` 中定义的图标组件 / 图标规则 |
| 2 | 项目本地 icon 组件、svg 资产、iconfont、统一包装层 |
| 3 | 已安装且在真实源码中实际使用过的图标库 |
| 4 | React + AntD 的 `@ant-design/icons` |

图标规则：

- 有明确形状或类名时保留同一或最接近的图标；只有语义时按常见业务语义选。
- 纯图标按钮必须补 `Tooltip` 和 `aria-label`。
- 交接必须说明图标来源层级与关键图标映射。

### 图表来源顺序

| 优先级 | 来源 |
| --- | --- |
| 1 | `CODE_WORKSPACE/architecture/` 中定义的图表组件 / 图表规则 |
| 2 | 项目本地 chart 组件、可视化包装层、统计卡片与图表容器 |
| 3 | 当前工程已安装并且在真实源码中实际使用过的图表库 |
| 4 | `ECharts` |

图表规则：

- 匹配优先级固定为：图表 > 百分比进度条 > 渐进 / 渐变等装饰性背景或线条。
- “已安装”不构成证据，必须有真实导入或页面使用。
- 折线、面积背景、柱条、扇区、漏斗等图表形态优先按图表还原，不要因为 `linear-gradient` 就降级为背景装饰。
- 有明确类型则保留同类型；只有业务语义时按数据结构选最保守的表达。
- 无真实图表实现证据时兜底 `ECharts`。
- 交接必须说明图表来源层级、类型映射，以及是否使用项目现有图库或 `ECharts`。

## 高保真 HTML 全局约束

| 场景 | 全局规则 |
| --- | --- |
| HTML 样式优先级 | 已有高保真时，表现层样式以高保真还原为准，不再强要求 token 化 |
| 默认外观 | 不要把 AntD / Element 默认外观当成视觉事实 |
| 组件默认行为冲突 | 必须显式配置 props 或补样式 |
| 行内原子内容 | 不要拆坏数字 + 单位、金额 + 币种、数值 + `%` 等同一视觉行 |
| 大页面布局 | 父级已决定宽高和分栏关系时，子级优先继承 / 拉伸 / 比例，不重复写死整套宽高 |
| 父级 flex 继承 | 父级已是 flex 且子项明显等分时，子级优先 `flex: 1` / 百分比 / 轨道继承，不要重复写死子项宽度 |
| 上下结构方向 | 区域明显上下排列时，优先保留普通块流或使用 `flex-direction: column`，不要误做成左右结构 |
| 失败回退 | 先退回更小槽位，再不行退回 fidelity-only |

## 执行清单

| 步骤 | 动作 |
| --- | --- |
| 1 | 确认当前 Feature、workflow profile、checkpoint 和目标代码工程 |
| 2 | 判断 HTML 格式：高保真/绝对定位 HTML 进入 `route/route-with-html.md`；普通静态 HTML 进入 `../autodev-html-to-react/SKILL.md` |
| 3 | 补充读取 `CODE_WORKSPACE/architecture/`、`{FEATURE_DIR}/PRD.md` 与接口说明文档 |
| 4 | 生成或修改与扫描结果匹配的框架代码；若无明确技术栈，则按 React + AntD 兜底 |
| 5 | 检测并生成图标 / 图表，校验来源层级、类型映射、可访问性 |
| 6 | 对关键组件槽位反推 props / mode 与补充样式，避免默认行为偏离高保真 |
| 7 | 校验行内原子内容未误拆行、布局容器未层层写死宽高 |
| 8 | 生成代码时补简洁注释；对同业务的变量、枚举、列定义、图表配置做局部抽取 |
| 9 | 校验生成文件，更新 checkpoint，并汇报变更文件、实际读取的 PRD / 接口文档、是否触发兜底和剩余风险 |
