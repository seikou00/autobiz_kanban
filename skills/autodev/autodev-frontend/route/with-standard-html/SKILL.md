---
name: route-with-standard-html
description: 处理标准 DOM / 语义明确 HTML 的独立 route 技能。适用于标准 DOM、`<style>`、class 命名清晰、表单/按钮/label/flex 结构明显的 HTML 输入；既覆盖普通 HTML，也覆盖结构标准但视觉仍要求高保真的 HTML。
---

# 标准 HTML 路线

这是独立的标准 HTML route 技能目录。

- 当前 route 技能入口：`SKILL.md`
- 当前 route 技能依赖：`deps/`
- 当前 route 技能参考：`references/`
- 当前 route 技能脚本：`scripts/`

本文中提到的 `SKILL.md` 均指仓库根技能 `../../SKILL.md`。

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

## 2. 读取顺序

1. 读取原始 HTML
2. 读取项目 `AGENTS.md`、`architecture/`、组件说明、相似页面和真实源码证据
3. 转交给 `deps/standard-html-parser.md`
   - 该依赖已作为 `standard-html-parser` 承载标准 HTML 转 React 工程代码能力，是本路线主执行入口
   - 如决定使用或可能使用 Ant Design，按需读取 `references/ant-design-conversion.md`，并由 `standard-html-parser` 在编码前完成映射矩阵、实现后完成覆盖审计
   - 覆盖审计使用 `scripts/audit_antd_coverage.py`，它只扫描 JSX / TSX 源码；发现候选项时退出码为 1，表示待处理清单，不表示脚本故障

## 3. 边界

- 本路线只负责标准 DOM / 语义明确 HTML
- 不修改原有绝对定位高保真路线
- 不依赖 `route/with-absolute-html/` 下的 absolute 分析脚本
- 不依赖标准 HTML 分析脚本或 `output/html-analysis/*.json` 前置产物；`scripts/audit_antd_coverage.py` 只在 Ant Design 转换后做源码覆盖审计，不作为 HTML 解析前置步骤
