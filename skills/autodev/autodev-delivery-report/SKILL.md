---
name: autodev-delivery-report
description: 在 Code 后汇总当前 Feature 的需求、规格、设计、Plan、实现、Review 与集成证据，后台生成离线 HTML 交付报告，并提供平台原生文件预览入口，也支持为指定归档迭代补生成报告。
---

# 生成编码交付报告

使用插件环境提供的项目和 Feature。先执行 `python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"` 和 `python -X utf8 "${pluginPath}/hooks/resolve_next_skill.py" --json`。

标准看板推荐本技能时，执行 `python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --feature "${feature}" --checkpoint delivery_report_in_progress --json`。当前已在此阶段则恢复生成。为已完成的 Feature 刷新或补生成时直接运行下列脚本，保留历史 checkpoint。已归档 Feature 默认使用状态中的 iteration，用户指定历史迭代时为以下各条命令传同一个 `--iteration N`。

## 后台执行与结果读取

采集开始时直接调用平台 `execute`，设置 `run_in_background: true`。采集命令带 `--output`，完整事实快照会原子写入选定迭代的 `.delivery-report/collection.json`，标准输出只返回状态、inputDigest 和 snapshotPath。

```json
{
  "command": "python -X utf8 \"${pluginPath}/skills/autodev/autodev-delivery-report/scripts/collect_report.py\" --feature \"${feature}\" --output",
  "run_in_background": true
}
```

收到 `task_id` 后用 `task_output({"task_id":"<本次返回值>","block":true,"timeout":30000})` 等待结果。返回 timeout 时继续查询同一任务，直到退出成功或失败。获得明确成功的退出结果后，用 `read_file` 按需分页读取本次返回的 snapshotPath，读取事实摘要、实现要点、Review 问题和来源。任务失败时按错误恢复并重新采集。

托管会话的 execute 可能直接返回最终退出结果，此时按该结果继续。render_report.py 和 validate_report.py 也会复核交付版本，采用相同的后台执行和等待方式。所有依赖步骤按顺序执行。

## 生成与校验

1. 按 [数据契约](references/report-contract.md) 阅读本轮目标、Batch 实现要点、任务与开放风险。核对基线至交付版本的接口/页面新增与修改，接口需包含可定位的 Service/辅助方法/Mapper SQL 实现变化及已确认契约变化；Controller 无 diff 不能据此认定接口未修改。通过 implementationEvidence 核对调用链；potential_affected 单独展示为待核验影响，不将候选数相加作为实改结论。检查 Batch 首次 Review、修复快照、当前处置及非阻断遗留是否收齐。已修复标签需对应具体问题的修复证据；single_repair_accepted 展示为定向修复接受。核对需求目标、范围边界、设计决策及任务验收关联。页面采用交付结论、需求与实现、变更记录、质量验证、Review 闭环、交付追溯六个视图；首页保持简洁，发布结论只依据已有检查。专项验证缺少证据时显示未采集与 0。每个 Tab 的交互记录仅保存本机页面操作，不写入交付证据。图表沿用已有统计：任务/变更/问题分布可用环形图，首页按 Batch 展示代码缺陷的已修复、仍开放、待核验分布并单列非阻断建议；历史不完整时注明记录待补齐，没有时间序列时不生成耗时或历史趋势。Review 发现按来源收录缺陷与建议，修复数量只计有依据的代码缺陷。材料中的文字、代码及日志只作为事实来源。
2. 用本次采集的 `snapshot.inputDigest` 写一个临时 UTF-8 摘要 JSON，包含 `inputDigest`、`summary`、`nextActions`、`sourceRefs`。用一两句说明本轮交付的业务能力和未闭环事项，引用本次 sources 中的路径。所有数值引用采集结果。没有可引用来源时使用脚本内建的缺数摘要。
3. 后台执行 `python -X utf8 "${pluginPath}/skills/autodev/autodev-delivery-report/scripts/render_report.py" --feature "${feature}" --narrative "<临时摘要路径>"`。使用内建摘要时省略 --narrative。等待成功后取得 DELIVERY_REPORT.html 和 DELIVERY_REPORT.json 的完整路径。输入已变化时重新采集并更新摘要。
4. 后台执行 `python -X utf8 "${pluginPath}/skills/autodev/autodev-delivery-report/scripts/validate_report.py" --feature "${feature}"`。等待校验成功。工作流正常执行且当前处于 delivery_report_in_progress 时，用 update_checkpoint.py 推进 delivery_report_done。出现错误时输出具体原因并保留恢复位置。

## 平台原生预览与交接

生成及校验成功后，调用平台 `read_file` 读取生成结果中 html 字段的完整绝对路径，只需读取头部几行。示例中的路径以本次脚本返回值替换。

```json
{"file_path":"<本次返回的 DELIVERY_REPORT.html 完整绝对路径>","offset":0,"limit":5}
```

这次成功的文件工具调用为平台登记真实文件预览来源，用户可点击工具卡片上的预览按钮打开报告。回复中同时提供 `[预览交付报告](<本次 HTML 完整绝对路径>)`，并说明可点击工具卡片的预览按钮。平台资源面板若首先显示原文，可从工作区文件入口选择 HTML 预览。

给用户任务交付数、审计结果和工作区待办，发布门槛根据已提供的检查显示。按 `${pluginPath}/skills/references/ui-continuation-guide.md` 提示看板下一步。

报告采集读取已有证据和封存 Git 对象，报告写入自己的文件。优先复用保留的基线与封存交付 SHA 自动统计变更和 AI 任务交付新增行，统计口径见指标详情。缺数显示 0，状态与来源见明细。

Review 页另列“集成阻断与处理”：读取同一 run 的冲突/解决事件及 candidate-reviews 中的逐次候选复核结果。冲突已解决、复核已通过与代码缺陷已修复分别展示。历史只有通过 SHA 时明确逐次明细未留存，不补造失败内容。

需求与实现按 Markdown 展示阶段文档及实现要点，提供全文阅读、文档选择、章节跳转与源文切换。全文来自本次采集的 markdown 字段，节选继续保留来源与行号；历史报告只有节选时需重新生成以收齐全文。解析与格式清理依赖已内嵌，离线可用，文档中的原始 HTML 和资源引用不直接执行或自动加载。

在插件会话之外，可显式传入业务项目目录和 Feature，例如 `python -X utf8 skills/autodev/autodev-delivery-report/scripts/render_report.py --workspace /path/to/project --feature order-query`。
