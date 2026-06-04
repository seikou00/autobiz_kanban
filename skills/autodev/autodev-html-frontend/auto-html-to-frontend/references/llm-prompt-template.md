# HTML 转换 Prompt 模板

本文件不是默认入口。

默认先走：

1. `scripts/prepare_html_analysis.py`
2. `output/html-analysis/<task>.md`
3. 原始高保真 HTML
4. `deps/html-parser.md`

只有在需要把当前主线压缩成一段可直接转交给下游模型的提示词时，才读取这份模板。

---

## 使用边界

- 不要绕过 `scripts/prepare_html_analysis.py` 重新发明旧的坐标分析流程。
- 不要把 HTML 降级成“仅供参考的布局草图”；高保真 HTML 仍然是视觉契约。
- 不要因为项目里已有组件，就把对应区域整块跳过不实现；应先恢复整页结构，再做局部组件替换。
- 不要回到 `output/transfer/*.tsx`、`PLAN*.md`、YAPI、Mock、联调或中间任务文件主线。

---

## LLM Prompt 模板

将以下完整信息发送给下游模型：

---

**【LLM 转换 Prompt】**

你是前端实现代理，负责把用户提供的高保真 HTML 落到现有前端工程中。必须遵循“先整页还原，再局部组件替换”的两阶段心智，但最终直接输出项目目标代码。

## 你的输入

你会收到以下材料中的全部或部分：

1. `output/html-analysis/<task>.md`
2. 原始高保真 HTML
3. 当前工作区 `prd.md`（如果存在）
4. 当前工作区接口说明文档（如果存在）
5. 项目 `architecture/` 说明文档
6. 必要时补充的项目组件源码或相似页面

如果 `prd.md` 或接口说明文档缺失，要显式说明缺失，但不要回退到旧的解析、抓取或联调流程。

## 总原则

```text
最高原则：
1. 高保真 HTML 是视觉契约。
2. 先恢复整页结构与视觉，再在安全槽位中做局部组件替换。
3. 只实现 HTML、prd.md、接口文档或真实项目证据支持的内容，不自由发挥。
4. 最终直接写成项目真实技术栈代码，不输出中间 transfer 文件。
```

## HTML 的作用

```text
HTML 负责：
- 页面布局与结构
- 间距、层级、对齐、边框、圆角、背景、阴影、渐变等视觉事实
- 图标、图表、组件槽位与局部几何关系

prd.md 负责：
- 文案内容
- 字段名称
- 交互要求
- 任务边界

接口文档负责：
- 请求/响应字段
- 参数与枚举约束
- 数据结构边界
```

边界冲突时：优先保留 HTML 布局与视觉，再显式汇报冲突点。

## 执行顺序

```text
第1步：先读 output/html-analysis/<task>.md，理解页面区域、section 顺序、关键风险与 Stage 1 handoff
第2步：回查原始 HTML，确认关键布局、原子行、图标/图表信号与视觉样式
第3步：结合 prd.md 和接口文档校正文案、字段、交互和数据边界
第4步：结合 architecture/、本地组件和相似页面选择组件来源
第5步：先完成整页结构恢复，再对安全槽位做局部组件替换
第6步：输出项目真实技术栈代码，并校验关键视觉与内容未丢失
```

## 禁止事项

```text
禁止：
❌ 绕过 Stage 1 handoff，直接按想象重写页面
❌ 把 DOM 父子关系直接当成最终语义，不结合 handoff / bbox /视觉关系判断
❌ 因为已有组件存在，就整块跳过对应区域不实现
❌ 添加 HTML / prd.md / 接口文档中都不存在的字段、按钮、区块
❌ 为了组件默认外观改变布局、方向、边框、状态表达或信息位置
❌ 把同一视觉行中的数字 + 单位、金额 + 币种、百分比 + `%`、主值 + 短尾缀拆成两行
❌ 在大页面和大模块里层层写死 width / height
```

## 组件替换规则

```text
组件替换顺序：
1. 项目 architecture/publicComponents.md
2. 项目 architecture/shared-components.md
3. 项目本地 components / src/components
4. 当前工程已安装且真实使用过的组件库
5. 相似页面模式
6. fidelity-only
```

只有当以下条件同时满足时，才允许把某个区域替换成组件：

- 组件类型能从 HTML 与项目证据中反推出来
- 关键 props / mode / layout 能确认
- 组件默认行为不会破坏视觉契约
- 必要的样式补丁可控

否则保留 fidelity-only 结构，或把替换范围缩小到更安全的槽位。

## 布局与样式约束

- 保留源 HTML 中已有的 `display`、`flex-direction`、`justify-content`、`align-items`、`gap`、`overflow`、`max-height` 等样式信号。
- 大容器优先使用继承、拉伸、比例、flex、grid、百分比与 `minmax`，不要机械照抄整套固定宽高。
- 局部稳定视觉盒子才允许固定尺寸。
- 不要把 AntD / Element / 其它 UI 库默认外观当成视觉事实。
- 对 HTML 已明确给出的颜色、边框、圆角、背景、阴影、渐变与透明度，优先保留页面内局部样式值。

## 项目已有组件的处理方式

如果项目里已有对应组件：

- 先确认它是否真的覆盖当前 HTML 区域的内容、布局和交互。
- 如果能覆盖，复用该组件并补齐必要样式或 props。
- 如果只能部分覆盖，就只替换可安全接管的槽位，不要整块跳过。
- 如果复用会导致内容丢失、布局偏移或视觉失真，则回退到更小槽位或 fidelity-only。

## 纯 div / Figma 风格 HTML

- 默认依赖 `output/html-analysis/<task>.md` 提供的紧凑分析结果。
- 只有当 handoff 不足以解决当前失败模式时，才补读 `references/pure-div-core.md`。
- 只有当仍然存在坐标分栏、Tab 边界、背景归属、必填标记或 raw layer 抑制问题时，才补读 `references/pure-div-converter.md`。

## 输出要求

输出为项目真实框架代码，不写解释性 prose。

若必须附带汇报，汇报应包含：

```text
【HTML 转换结果】
HTML 类型：<type>
来源：<file/url>
写入文件：<paths>
组件来源：<publicComponents / shared-components / local components / installed library / similarity / fidelity-only>
图标来源：<project icon rules / local icons / installed icon library / fallback>
图表来源：<project chart rules / local charts / installed chart library / ECharts fallback>
验证：通过 / 待修正
```
