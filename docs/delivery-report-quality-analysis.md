# 交付报告图表与 Code Review 展示分析

Markdown 阅读、Batch Review 闭环图及来源筛选的最小修复均已实现。随后对用户提供的 new_rouyi 真实运行进行了只读核查，并完成第 5 节的候选复核留存与集成过程展示：修改插件 Workflow、运行记录写入器及报告采集/模板，不改平台代码或历史业务产物。

## 1. 原累计折线为什么信息量低

`assets/report.js` 的 `batchLine()` 按 Plan 顺序，分别累计任务数与已交付任务数；`report-charts.js` 在起点补零后画线。它没有使用任务开始、完成或 Review 时间。

当各 Batch 各有一个任务、全部完成交付时，两条线必然重叠成等差斜线。它只重复交付率和 Batch 数量，不能说明耗时、吞吐、代码规模或质量收敛。截图正符合这种形状。

### 已实施：首页改为 Review 问题闭环分布

使用每批次的堆叠条形图，展示已确认代码缺陷的“已修复、仍开放、待核验”数量，并单列非阻断建议数量。横轴/行标签保留 Batch，但不再累计，能直接定位问题集中与待处理的批次。点击分段进入具体问题、原始发现和修复依据。总计沿用同一问题去重集合，首页不额外铺长表格。

- 已修复：必须匹配具体问题的修复接受证据；不能因最终 Review passed 关闭所有历史问题。
- 仍开放：已记录且没有关闭证据的代码缺陷。
- 待核验：发现已留存，但身份、处置或修复结果尚无法确认。
- 非阻断建议：与代码缺陷分别展示，不能计入代码缺陷修复率。
- 历史不完整的 Batch 明确标记部分记录，不能把缺少发现记录画成“零缺陷”或“一次通过”。没有数据时展示 0 和原因。

如果仍需折线图，放在 Review 详情页，改用真实发现与解决时间，绘制“累计发现、累计修复、待处理存量”。每个问题只使用首次发现与明确解决事件；回溯记录没有时间时不编造时间、不按 Batch 顺序代替时间，也不绘制伪趋势。当前闭环分布更适合作为第一步。

## 2. 修复前 Review 为什么可能只看到遗留问题

| 代码中确认的现象 | 原因与影响 |
|---|---|
| 正常 Code Review 的 advisory 不进入“Review 发现” | `parallel_batch_stage.py::_record_review_advisories()` 同时写入 `reviewAdvisories` 和 `deferredIssues(kind=review_advisory, stage=review)`。`delivery_report/review.py` 将它们映射为 origin=deferred，并仅以 origin=review 设置 reviewFinding；因此它是 Review 来源，却只显示在遗留筛选。 |
| 分类显示有数量，问题表却为 0 | `report.js::risks()` 将“问题类型”和“类别”叠加筛选，但类别计数使用全部 report.risks，表格使用当前类型集合。选择 Review 发现而数据只有 advisory 时，类别“全部”仍有数字，表格却为空。已用正常协议夹具复现。 |
| 只看最终 passed 证据无法恢复首次代码缺陷 | 普通 passed 证据主要记录结果和 advisory，不保证 metadata.findings 存在；失败由 fail_stage 保存到 engineering/<Batch>/repairs/，修复接受保留 repairedFailure/repairResolution。不能只读取最终 metadata.findings。 |

当前 `read_review()` 已读取 manifest 登记的 Review 历史证据、只读修复快照、当前 failure、repairedFailure 和 repairResolution。现有测试覆盖“首次失败 → 定向修复接受 → 已修复标签”，也覆盖摘要损坏、其他 run、缺少历史和通用 passed 不自动修复。因此，截图缺少代码缺陷不能直接归因为“只采集了最终 Review”；还应核对真实运行是否留存了对应证据、版本是否更新、问题是否已被类型筛选隐藏。

### 已实施的最小修复

1. 将“来源阶段”和“当前处置”分开：sourceStage=review 标识评审来源，findingType 区分 code_defect/advisory，origin=deferred 保留遗留处置。reviewRecords 统计全部评审来源记录，reviewAdvisories 统计建议；reviewFindings/reviewFinding 保持代码缺陷的兼容口径，reviewFixed 只统计有修复依据的代码缺陷。相同问题合并时保留已确认的缺陷身份。
2. 分类计数使用当前问题类型集合，与表格匹配；显示当前组合条件、匹配数量与一键重置。无结果时明确“当前筛选无匹配”，不暗示整份报告零问题。
3. 保留并验证首次 Review 的修复快照与最终接受证据。历史缺少快照时可用最终明确的 repairedFailure 回溯，并标记“修复记录回溯”；未记录的问题不从聊天内容猜测补齐。
4. 在 Batch Review 历程增加发现记录来源、历史是否完整、原始结果与当前处置，便于区分“首次未发现问题”“历史未留存”和“有记录但待核验”。报告保持只读，不修改 Code 运行记录或业务源码。
5. 完成以上问题去重、类型与处置规则后，再替换首页图表，避免把当前漏分类的数据画成新的假结论。

## 3. 核对截图对应运行所需的证据

- `DELIVERY_REPORT.json`：metrics.reviewFindings/reviewFixed、risks 的 origin/reviewFinding/category/status，以及 quality.checks 中 Batch Review 的 history/verified/historyComplete。
- 对应 `plan.json` / Batch 计划的 deliveryRunId；据此核对同一 run 的 manifest，不能混入其他历史 run。
- `.parallel-runs/<run>/manifest.json` 的 stageStates.review、repairResolution、deferredIssues。
- manifest 引用的 `stages/<Batch>/review/*.json` 和 `engineering/<Batch>/repairs/*.json`，检查 Feature/run/Batch、内容摘要和提交绑定。

数据完整且含首次缺陷时，应直接显示在 Review 发现并按证据标为已修复；只有 advisory 时，展示为 Review 建议与非阻断遗留；历史缺失时明确说明覆盖不足。

## 4. 验收场景

- 只有 Review advisory 时，评审来源视图能看到该建议，代码缺陷和代码缺陷修复率不增加。
- 首次代码缺陷与遗留建议同时存在时，缺陷已修复、建议仍开放，按同一问题去重且保留历史来源。
- 切换类型和类别后，分类数字、表格及空态一致；重置能恢复全部问题。
- 每个 Batch 都完成一个任务时，首页不再出现没有质量差异的累计斜线。
- 两批次同样完成交付但问题闭环不同，图表能清晰展示差异，点击可追溯。
- 没有完整历史或时间的报告仍可阅读，不编造零缺陷、一次通过或时间曲线。

## 5. new_rouyi 真实运行核查

核查目录：`/Users/liuxuyang/Downloads/autobiz_project_test/newrouyi/.autobizdevops`。Feature 为 new_rouyi，8 个 Batch 的 deliveryRunId 均指向 `cw-20261113-144045-607`。本次只读取原始产物，没有改写该目录或重跑 Code。

### 已确认的留存结果

- B001～B008 的 review 均为 status=passed、attempt=1，evidenceIds 仅含各自的 `STAGE-<Batch>-REVIEW-001`。
- 8 份 Review Evidence 的 metadata 均只有工作树、分支、交付提交及 reviewAdvisories，没有 findings、repairedFailure 或 repairDisposition。
- manifest 没有 repairResolution，运行目录没有 `engineering/*/repairs/*.json` 或 REPAIR_CONTEXT.json；events.jsonl 没有 batch_stage_failed 或实施修复事件。
- 10 条非阻断建议完整保留，其中 B007 有 3 条，其余批次各 1 条。新版 read_review 在内存中读取相同产物，去重后为 reviewRecords=10、代码缺陷=0、已修复代码缺陷=0、建议=10。这里的 0 表示没有对应的已记录问题，不证明会话中从未出现问题。
- 目录中的报告生成于北京时间 2026-11-14 22:19:02，仍使用旧版累计 Batch 快照，缺少新增的来源/类型字段。使用新版重新生成能修正建议来源显示，但不能补出未留存的首次缺陷内容。

### 已确认存在的中途阻断记录

events.jsonl 保存了以下合并冲突与 model 解决记录，manifest 中对应候选最终均已 promoted，并保留 reviewedCandidateSha：

| Batch | 候选批次 | 冲突开始（北京时间 2026-11-14） | 冲突解决 |
|---|---|---|---|
| B006 | wave-003 | 16:24:35 | 16:27:16 |
| B004 | wave-004 | 16:31:15 | 16:35:11 |
| B002 | wave-005 | 16:40:20 | 16:47:19 |
| B003 | wave-006 | 17:00:08 | 17:13:01 |
| B007 | wave-007 | 17:17:53 | 17:22:48 |

这些是可证明的“合并冲突已解决”，不能直接计为 5 个“Review 发现的代码缺陷已修复”。目前报告未采集这部分集成过程。是否对应用户记忆中的中途 Review 阻断，现有文件不能进一步证明。

### 额外确认的工作流留存缺口

修复前，归档的 `code-batched-execution.workflow.js::promoteWithDeltaReview()` 在候选差量 Review 失败时直接抛出 candidate_delta_review_failed，未将失败返回对象保存成持久化 Review 证据。通过后，推广操作仅登记 reviewedCandidateSha。因此，如果 Review 在这条路径中先阻断、后续恢复后通过，现有产物无法还原原失败原因及具体问题的修复过程。这是候选集成 Review 的记录缺口，不能仅靠放宽报表解析器解决。

本轮已实施两处最小修复：

1. 报告采集现有 merge_train_conflicted / merge_train_conflict_resolved 及候选 SHA，在“集成阻断与处理”中展示已解决冲突，保持与代码缺陷统计分开。
2. 候选 Review 返回后、抛异常前，将 run/repository/wave/baseSha/candidateSha、原始结果、原因、时间和受影响 Batch 持久化；后续复核通过也保存同版本的处置依据。普通 Batch Review 仍通过 finish-review 的失败登记与现有修复快照留存。只有具体问题与修复依据能够对应时，才显示“已修复”；只有冲突解决证据时显示“冲突已解决”。

新增候选记录接口通过 Base64 传递原始结果，避免错误文案中的引号或命令字符进入 Shell；写入前核对候选、基线和封存范围。记录使用不可变内容摘要及运行内序号，重试不覆盖，保存失败不继续推广。报告按已留存的真实事件与 Review 结果展示，并保留旧版仅有 SHA 的说明。本次历史数据中的 5 次冲突能够恢复，未留存的 Review 失败原文仍无法追补。
