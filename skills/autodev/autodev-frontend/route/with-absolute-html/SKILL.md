---
name: route-with-absolute-html
description: 绝对定位高保真 HTML 的独立 route 技能。它负责定义绝对定位 / 碎片 div / Figma 导出类 HTML 的适用条件、读取顺序、转交给本目录 `deps/html-parser.md` 的时机，以及失败兜底方式。
---

# 绝对定位高保真 HTML 路线

这是独立的绝对定位高保真 HTML route 技能目录。

- 当前 route 技能入口：`SKILL.md`
- 当前 route 技能依赖：`deps/`
- 当前 route 技能参考：`references/`
- 当前 route 技能脚本：`scripts/`

本文中提到的 `SKILL.md` 均指仓库根技能 `../../SKILL.md`。
命令示例兼容两种工作目录：
- 如果当前目录是 `SKILL_ROOT`（`autodev-frontend` 技能根目录），使用 `route/with-absolute-html/scripts/...`
- 如果当前目录已经是 `route/with-absolute-html/`，使用 `scripts/...`

## 1. 这条路线什么时候使用

当满足任一条件时，使用本路线：

| 输入 | 处理 |
| --- | --- |
| 用户上传的 HTML 主要由绝对定位 / 碎片 div 驱动 | 走本路线 |
| 用户直接粘贴的 HTML 明显是设计导出稿 / 坐标稿 | 走本路线 |
| 用户提供的设计产物已经能导出或拿到实际 HTML，且结构明显偏导出稿 | 走本路线 |

绝对定位强制信号（命中任一条就**禁止**转去标准 HTML 路线）：

- 用户明确说“高保真”“设计稿导出”“Figma/MasterGo 导出”“绝对定位”
- 全局或局部大量 `position:absolute`
- utility class / Tailwind 中大量 `left-[...]`、`top-[...]`、`w-[...]`、`h-[...]`
- 存在大量 `clip-path`、`data:image/svg+xml`、渐变、阴影、像素级尺寸
- 页面主体是视觉拼装容器，而不是原生语义表单 / 表格 / DOM 流布局

即使分析脚本把这类输入判成普通 `html`，也不能改走 `../with-standard-html/SKILL.md`；只能继续走本路线，并把分析结果视为“低置信度辅助信息”。

如果用户提供的是高保真 HTML，但 DOM 结构标准、语义明确、主要布局不是由绝对定位碎片驱动，则不使用本路线，应转去 `../with-standard-html/SKILL.md`。

当满足以下条件时，不走本路线：

| 输入 | 处理 |
| --- | --- |
| 只有截图 / 图片，没有 HTML | 停下并要求用户补 HTML 文件或内容 |
| 只有 Figma / 设计链接，但拿不到实际 HTML | 停下并要求用户补 HTML 文件或内容 |
| 只有 HTML URL，但最终拿不到实际内容 | 停下并要求用户补 HTML 文件或内容 |

## 2. 这条路线只负责什么

本文件只负责：总原则、读取顺序、转交给 `deps/html-parser.md` 的时机、失败兜底。

它不展开实现细则，不替代根技能 `../../SKILL.md` 的全局优先级，也不重复 `deps/html-parser.md` 的执行规则。

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

### 3.5 脚本职责与地位

在 `with-absolute-html` 路线里，**默认先执行脚本**，不要让模型自行决定跳过。

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

以下是默认主流程。进入本 route 后，脚本是**默认必跑**；任何异常都按 §7 降级处理，不阻塞后续步骤。

1. **读取 HTML 来源。**
2. **默认必跑 `prepare_html_analysis.py`。** 使用完整命令模板，参数齐全后再执行：
   - `SKILL_ROOT` 目录：
     ```
     python route/with-absolute-html/scripts/prepare_html_analysis.py \
       --project-root . \
       --task-stem <task-stem> \
       --html-file <HTML_PATH>
     ```
   - `route/with-absolute-html/` 目录：
     ```
     python scripts/prepare_html_analysis.py \
       --project-root ../.. \
       --task-stem <task-stem> \
       --html-file <HTML_PATH>
     ```
   - `<task-stem>` 由调用方自定义，建议格式 `task-1` / `task-<页面短名>`（如 `task-1-create-course`），仅用于命名产物；全套技能里所有出现 `<task-stem>` 的路径都指同一个值。
   - 同一页面多个 HTML 片段时，重复 `--html-file` 或用逗号分隔。
   - 对多片段输入，脚本会生成 merged analysis input 做聚合分析；但原始 HTML 仍然是视觉真相。
   - 只要原始 HTML 与脚本结论冲突，就优先相信原始 HTML。
   - 但脚本本身任何异常（语法错误、参数错误、依赖缺失、IO 错误、子进程崩溃等），都按 §7 走降级路径继续，不要原地卡住或反复重试。
3. **检查产物。** 期望产物：
    - `output/html-analysis/<task-stem>.md`
    - `output/html-analysis/<task-stem>.json`
   - `output/html-analysis/<task-stem>-checklist.md`
   - 产物齐全 → 进入第 4 步；任一缺失或脚本非 0 退出 → 按 §7 降级路径继续，不要在此卡住。
   - `page-layout.html`、`whole-page-reference.html`、`section-html/` 都视为调试产物；没有显式调试需求时，不要默认要求生成。
4. **读取上下文。**
   - 若脚本产物齐全：先读取 `<task-stem>-checklist.md`，再读取 `<task-stem>.md`（完整版 handoff），最后回到原始高保真 HTML 做主判断。
   - 若走降级路径：直接以原始高保真 HTML 为主。
   - 若 checklist / handoff 标记 `componentizationMode=conservative` 或 `analysisConfidence.level != high`：先锁定原始 HTML 的宏观布局、模块边界、表格/图表/时间线/上传等所有权，再把脚本产物仅用于缺项点查与 whole-section 保留，不要让 `replacementSlots` 主导大块组件化。
5. **转交 `deps/html-parser.md`。**
   - `hasManifest=true`：表示脚本产物可用，但它们只是辅助。
   - `hasManifest=false`：表示脚本失败，后续完全以原始 HTML 为主。
6. 完成主线代码生成、页面拆分 / 抽取与最低校验。
7. 主线完成后先输出交付总结（若走降级路径，须在总结里显式声明"已跳过 Stage 1 脚本及原因"），再立刻确认是否进入 `../review/SKILL.md`；建议不要在当前线程执行下一技能；具体询问格式与强制动作遵循 `deps/html-parser.md` 末尾"汇报后的强制动作"，未获用户答复前停止，不自动继续。

## 5. 转交规则

完成 §4 第 4 步后，默认直接转交：

- 目标文件：`deps/html-parser.md`
- 默认入口：从 `§3 分类 HTML` 开始
- 转交时附带状态：`hasManifest=true`（脚本产物齐全，仅作辅助）或 `hasManifest=false`（已走 §7 降级）；`hasManifest=false` 时 `deps/html-parser.md` 直接以原始 HTML 为唯一视觉源继续，不要再要求脚本

## 6. 增量修改

如果项目里已经存在目标页面：

1. 先读取当前页面文件
2. 以高保真 HTML 对比差异
3. 只改变化部分
4. 保留原文件中无关的逻辑、导入和状态管理

## 7. 失败兜底（降级路径）

核心原则：**脚本异常永远不阻塞主流程**。任何脚本失败都按下面的统一降级处理。

- URL 拿不到真实 HTML 时，停下并要求用户补 HTML 文件或内容（这是输入缺失，不是脚本失败）
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
  2. 直接进入 §4 第 5 步，转交 `deps/html-parser.md`，并带 `hasManifest=false` 状态
  3. 由 `deps/html-parser.md` 以原始 HTML 为唯一视觉源继续整页恢复与组件化
  4. 在最终交付总结里显式列出"已跳过 Stage 1 脚本"和具体原因（如"argparse 缺参数"、"语法错误 line N"、"FileNotFoundError"）
- 唯一例外（不走降级、必须先修复）：模型自己虚构了"已跳过原因"而实际并未执行脚本。脚本必须至少被真实尝试执行一次，并捕获真实异常信息

## 8. 和其它文件的边界

| 文件 | 一句话职责 |
| --- | --- |
| `../../SKILL.md` | 总入口 + 全局优先级与执行清单 |
| `SKILL.md` | 绝对定位高保真 HTML route 入口、读取顺序与转交规则 |
| `deps/html-parser.md` | 真正把路走完（分类、整页恢复、组件替换、写代码） |
| `../review/SKILL.md` | 用户确认后进入回检路由 |
