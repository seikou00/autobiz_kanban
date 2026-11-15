# 数据契约 v1

活动迭代从根 plan.json 及 plans/Bxxx/plan.json 加载 Plan bundle。个别 Batch 缺件时保留根索引中的任务分母，可验证的 Batch 照常统计。每个 Batch 的 deliveryRunId、mergeCommitSha 对应 manifest 的 run、Batch 和集成提交。任务的 latestImplementationEvidenceId 指向 evidence/EVIDENCE.jsonl 中的实现记录。其 runId 属于任务本次执行，.task-runs 中的 parallelRunId 指向交付运行。已实现、已集成及用户目录同步分别展示。按 capability、任务的 specRefs/designRefs 追溯设计与规格。

collect_report.py 输出结构化事实。sources 包含 Feature 相对路径与 SHA-256，以及 kind 为 git_diff 的封存版本差异引用，gaps 只记录真实来源缺失或无效，diagnostics 记录识别覆盖和正常阶段说明。metrics 的 value 可以是 null，displayValue 是页面及导出的值。0% 分母为零时保留缺数状态。审计策略 report_only 的失败照实显示。生成时间、摘要与模板版本由 render_report.py 绑定到最终 reportId。validate_report.py 复算输入并验证离线页面与 JSON 一致。

平台会话通过 execute 的 run_in_background=true 执行采集。传入 --output 时，完整事实写入本迭代的 .delivery-report/collection.json，标准输出返回 ok、featureId、inputDigest、status 和 snapshotPath。收到 task_id 后通过 task_output 等到命令退出成功，再读取本次 snapshotPath。未传 --output 时沿用完整 JSON 标准输出。render 和 validate 同样通过平台后台任务等待机制执行，保持采集、摘要、生成、校验、状态推进的先后顺序。

报告通过校验后，用平台 read_file 读取返回的 HTML 绝对路径头部，登记平台可信文件预览来源。用户点击文件工具卡片的预览按钮，或回复中的本地文件链接打开。HTML 资源面板的默认显示模式由平台决定，工作区文件入口支持 HTML 渲染。

可选的 DELIVERY_MEASUREMENTS.json 用于接入外部已有结果。文件放在相同 Feature/归档迭代内，featureId 及 repositoryHeads 绑定所有相关仓库的本次交付 SHA。unit、e2e 是计划用例数组，行字段为 id、taskId、title、result、reason。result 使用 PASS、PASS_WITH_WARNINGS、FAIL、BLOCKED、SKIP 或 NOT_RUN。用例按类型及 ID 去重，并以已执行数计算执行覆盖。lineCoverage、branchCoverage 各提供非负整数 covered 和 total。aiGeneratedLines 是上游记录的累计生成行数，有有效记录时优先使用。缺少此字段时自动从已交付 TASK 的实现证据取文件集合，与基线到交付提交的源码新增行交叉核对并去重。此时 aiLines.basis 为 delivered_task_diff，统计本轮交付保留下来的新增源码行，页面显示该口径。文件匹配使用任务运行中的仓库根目录和 workspacePrefix，支持绝对路径、Windows 路径、仓库前缀及工作区相对路径。缺少 changedFiles 时，依次读取 fileChanges、任务起止文件快照、可核对的 Batch startSha/preparationBaseSha 至 commitSha 的变更文件，再与最终交付差异核对。verified_existing、verify 及明确 noCodeChange 任务贡献有依据的 0 行。部分归属失败时保留已核验数量，在 aiLines.details 中逐任务给出原始路径、候选路径及原因。排除 .frontend、.engineering、运行日志和生成目录。默认页面名称为 AI 交付新增行，版本绑定的 aiGeneratedLines 累计记录显示为 AI 累计生成行。可选 gates 对象的 tests、security、architecture、confirmation 取 passed 或 failed，来源应是同版本的显式评估。version 和 buildNumber 可记录与该批交付匹配的版本和构建号。仅传入已有字段。具有相同版本绑定的 UNIT_TEST_RESULT.json.targets 和 E2E_RESULT.json.cases 也可读入。未绑定的历史结果在缺口中说明。

仓库统计针对封存的 baseSha 到 headSha，Git numstat 给出文本源文件的新增和删除，过滤构建目录、生成目录和 lock 文件。模块按仓库内一级目录归集。读取基线和交付提交的完整 Git 树，接口和页面均分别统计新增、修改、删除，按仓库和入口去重。Spring 支持 Get/Post/Put/Patch/Delete/Head/OptionsMapping 及 RequestMapping 的字面量单/多路径和 RequestMethod。比较完整 handler 方法体，沿静态可解析的本地方法调用、接口实现、DTO 类型和 MyBatis Mapper 引用核对影响，既有 URL 的 Handler、已定位的方法体或 SQL 变化计为修改。参数/返回类型结构变化登记为 contract_affected，属于已确认契约修改，纳入主卡 api/apiModified，apiContractModified 给出其中契约修改数，apiImplementationModified 给出实现链路修改数。apiContractAffected 保留为契约修改的兼容指标，与主卡不重复相加。其他只有类型关联证据的变化登记为 potential_affected 和 apiPotentialAffected，独立展示为待核验影响。契约类型影响按静态结构判断，字段实际使用和复杂动态调用需要核对。移除整类导入与所有方法之间的递归类型扩散。OpenAPI JSON 的 operation 变化进入实现修改，引用 schema 变化进入契约修改，两者均进入接口修改总数，同一入口按实现修改优先只计一次。页面沿路由到组件及其本地依赖解析，支持对象路由、JSX Route、相对嵌套路由及静态可定位的 lazy/import。路由未登记且位于 pages/views/screens 的页面文件按文件记录，目录角色判断兼容 Pages 等大小写，实际文件路径保留大小写。内部 children/components 通过引用归属主页面，独立路由仍优先登记。此类页面的 basis 为 page_file，同一已登记路由的组件不会重复算文件。动态注册、运行时反射及无法解析的依赖给出缺口，计数只覆盖可核对的入口。变更明细保留入口、操作和 affectedFiles。基线分支保留时直接使用 manifest 封存的 baseSha/headSha。

任务和 Batch 实现要点来自对应 Batch Plan 的 implementationPoints。原文直接展示在任务表、Batch 汇总及详情，并进入摘要导出。Review 读取当前阶段 Evidence 中的 findings/reviewAdvisories、manifest 的 failure/repairResolution、保留的 engineering 修复上下文，以及 manifest/aggregate 的 deferredIssues。历史 Review 从当前 run、Batch 的 stageStates.review.evidenceIds 对应的 stages/<batch>/review/*.json 读取，核对 Evidence 摘要与 planRevision；历史 commit 保留原值，当前 Evidence 另核对交付 commit。首次失败没有 Stage Evidence 时，读取 engineering/<batch>/repairs/<内容摘要>.json，校验 schema、run、Batch 和文件内容摘要；仅剩最终 repairedFailure 时以 repair_reference 明确标为修复记录回溯，未知时间和初始 SHA 保留空值。quality.checks 中的 Batch Review 增加 history、verified 和 historyComplete，引用的历史来源失效时保留已知数量并标记 partial，历史记录按已有时间及轮次排序。

已明确接受定向修复且与 failure 的问题 ID 或消息、文件、任务匹配的 finding 标为 resolved，页面显示“已修复”，reviewFixed 单列计数，保留 resolutionSourceRefs/reviewHistoryRefs。一般通过结果不自动关闭历史问题；其他未验证问题继续开放。当前证据无效或修复记录互相矛盾时不推断修复成功。当前 Code 的 single_repair_accepted 表示一次定向修复被接受，页面不会把它表述成额外执行了复审。非阻断遗留保留 blocksWorkflow=false 和原始 disposition/status。按问题 ID 或同 Batch 相同问题合并重复投影，sourceRefs 合并保留。sourceStage=review 保留 Review 来源，findingType 区分 code_defect/advisory；reviewFinding 及 reviewFindings 保持代码缺陷的兼容口径，reviewRecords 统计全部评审记录，reviewAdvisories 单列建议。kind=review_advisory、stage=review 及 Review 证据中的建议都可进入 Review 来源筛选；origin=deferred 保留遗留处置，不改变来源身份。合并记录保留已确认的代码缺陷身份，建议不增加代码缺陷或修复数量。Review 页采用单层并列视图：全部问题、Review 发现、架构红线、架构质量、安全漏洞、代码缺陷、Review 建议、已修复、非阻断遗留及其他实际类别。只保留一个筛选状态，各视图直接筛选全部问题，数字与结果一致；不再按来源/状态叠加下层类别。旧“架构复杂度”归入“架构质量”以保留历史记录。

临时摘要只接受以下字段，sourceRefs 引用 collect_report.py 返回的真实路径。发布脚本限制字段及快照，技能负责对照证据核实叙述。

集成过程单列为 integration.items，不增加 Batch 代码缺陷或 reviewFixed 数量。从同一 run 的 events.jsonl 配对 merge_train_conflicted / merge_train_conflict_resolved，展示涉及 Batch、文件、原始事件行号及解决候选 SHA，已解决显示“冲突已解决”。候选 Review 的每次返回由 Workflow 先调用 parallel_merge_train.py record-candidate-review 持久化到 candidate-reviews/<reviewId>.json，再按原有结果继续或报错；模型调用异常同样留存，保存失败时停止推广。记录 schema 为 autodev.candidate-review.v1，绑定 Feature/run/repository/wave/baseSha/candidateSha、封存交付及实际 reviewScope，保留完整 reviewResult、调用标识、记录时间和运行内序号。reviewId 绑定事实内容，digest 绑定完整记录，同一记录重试复用且不覆盖，按运行内序号恢复先后顺序。

报告验证记录身份和摘要，将相同仓库、wave、基线及封存交付范围的候选复核归为一组，保留首次结果及全部历史。后续通过显示“复核已通过”，不据此宣称逐个代码缺陷已修复；历史候选的失败记录保留待核验状态，当前候选失败在结论中保持阻断。旧运行仅有 reviewedCandidateSha 时说明逐次明细未留存，无法追补未知失败内容。集成记录参与 inputDigest、HTML/JSON 文件对校验与摘要导出，旧报告缺少 integration 字段时按空记录兼容。

```json
{"inputDigest":"<本次采集值>","summary":"<业务交付结论>","nextActions":[],"sourceRefs":["PRD.md"]}
```

文件对发布中断时再次运行 render_report.py 或 validate_report.py 续写同一对已校验文件。可从已完成的状态或指定归档迭代单独生成，历史补生成不推进阶段。产物同步沿用现有同步技能和大小限制。

诊断字段包含 id、reasonCode、category、scope、affectedMetrics、affectedFiles、sourceRefs 和 nextAction。同一原因及版本范围聚合一条，多个指标通过 diagnosticIds 引用。gaps 中 category=missing，diagnostics 中 category=coverage/info。已知的内部实现文件尚未关联入口时只给覆盖说明，主卡保留可核验数量。not_collected 表示流程未采集，not_applicable 表示不适用或无有效分母，均继续显示 0 并在阶段说明呈现。正常未运行测试不会产生数据缺口。旧版 {field,reason} 在页面显示为历史待复核。

阶段 Markdown 产物的 markdown 字段保存完整原文，参与 inputDigest 和来源哈希校验，不受摘要长度限制；sections 按标题提取正文、行号与来源，每节最多 2800 字符并标记节选。围栏代码中的标题不作为文档章节。deliveryBrief 从 PRD/proposal/design/DETAIL_DESIGN 的目标、范围边界、技术决策章节选取摘要，每类最多三节、每节最多 1200 字符，缺失显示 0。首页只展示研发交付结论、四个关键指标、最多三条待办和流程产物。交付内容、specRefs/designRefs/acceptance 与交付状态放入需求与实现；接口/页面与代码明细放入变更记录；仓库版本、审计及数据说明放入交付追溯。验收原文不作为测试已通过的证明。风险页提供 Batch Review 历程、已修复筛选及开放问题待办。这些内容同时进入离线 HTML、JSON 和摘要导出。

需求与实现提供 Markdown 全文阅读区，支持文档选择、章节跳转、预览/源文切换，以及同文档锚点与已收录的相对 .md 链接；交付摘要、阶段文档节选和实现要点也按 Markdown 显示，保留来源路径与行号。标题、强调、嵌套列表、任务列表、引用、GFM 表格、行内及围栏代码均可离线渲染。代码块保留语言标记和缩进，表格与长代码在阅读区内独立滚动。历史报告缺少 markdown 时仅渲染已保存节选，并明确提示重新生成以读取全文。

解析采用本地固定版本 marked 16.4.2 和 DOMPurify 3.4.5，再按允许的格式输出；解析器、清理器、阅读组件及依赖许可一起内嵌 HTML，参与 templateDigest 校验。文档不作为可执行指令：原始 HTML 按文字显示，仅允许无属性的 br/kbd/sup/sub 格式；移除脚本协议和可执行属性。文档图片保留说明与引用，不自动访问本地或远程资源。http(s)/mailto 链接由用户点击打开，文档内跳转不改变六视图 Tab 路由，也纳入对应 Tab 的本机交互记录。

接口修改依据为接口实际行为及契约变化，Controller 文件无需发生 diff。Java 成员解析支持省略 public 的接口/Mapper 声明、包级辅助方法、平衡括号注解、继承及 super 调用、方法引用与本地可解析返回类型的连续调用。重载依次使用参数数量及可确认的参数类型；多实现时使用可定位的 Qualifier/Resource，无法确定的目标继续标记 partial。类型优先匹配全名、导入与同模块，避免同名类型跨服务串联。只沿实际可解析的调用和字段/枚举常量引用比较，不扩散至类中的全部方法；新增未引用错误码只给待核验影响。Java 指纹忽略注释与空白。implementationEvidence 保留变更方法/字段/SQL、基线或交付版本、源码位置及入口到变更点的调用链，进入页面详情和摘要导出。

页面统一使用深蓝结论区、蓝色核心指标、绿色通过或修复、橙色待核验、红色失败/阻断、紫色契约修改。零风险保持中性色。概览优先展示核心指标与进度；正文按内容分组卡片，表格展示操作标签与影响链路，手机端显示为记录卡片。阶段文档展开及原始来源保留。

页面信息架构为六个 Tab：交付结论、需求与实现、变更记录、质量验证、Review 闭环、交付追溯。结论区区分证据不足、部分交付、存在阻断、编码已交付但验证待补齐、可进入发布评估；报告不表示已实际发布。已知失败检查或阻断问题优先于已交付数量；全部交付但存在未评估检查、开放事项、识别边界或来源缺口时，保留待验证结论。

质量验证矩阵按验证概览、正确性、忠实性、一致性、完备性、安全性、可审计性、可控性、差异清单组织。只使用当前报告已有编译、用例、任务引用、显式 gate 和审计结果。需求引用已建立显示“已留存”，不替代需求语义已完整覆盖的证明。命名/配置一致性、专项边界/特殊字符、敏感字段/密码/SQL 注入、监控告警/应急处置没有专项结果时保持未采集与 0，不从参考图或代码片段推断通过。

架构红线、架构质量、安全漏洞位于 Review 问题的上层并列视图，与“全部问题”“Review 发现”等同级，仅保留一组入口，不在质量验证页重复添加卡片。当前均为显式占位：quality.specialAssessments 的 id 分别为 architectureRedlines、architectureQuality、securityVulnerabilities，value=0、status=pending、sourceRefs=[]，对应空视图及摘要标注待补充。架构质量当前使用记录项数量，不预设评分或百分比。占位不代表专项检查已通过，不计入已发现风险、不产生数据缺口，也不改变已有发布 gate。后续专项数据接入另行补充；旧报告缺少该字段时仍提供三个待补充的 0 值视图。

表格支持本地排序；变更页支持搜索、分类及每页 15 条分页。每页提供一个“本页交互记录”面板，记录实际打开/查看、筛选、搜索、排序、展开/折叠、查看/关闭明细、翻页和导出。记录按 reportId 分版本保存到浏览器 localStorage，每个 Tab 最多 50 条，可单独清空；存储不可用时退化为本次打开期间的内存记录。页面操作记录与 DELIVERY_REPORT.json、inputDigest、任务执行证据完全独立，不进入交付事实摘要。动画包含结论装饰、Tab 入场、计数和详情，遵循 prefers-reduced-motion。

Review 问题主表采用问题摘要、关联范围、级别/阻断、处置状态四列，摘要占约一半宽度，默认最多三行并提供“查看完整内容”。来源与类别作为摘要附属信息，模块与负责人合并到范围列，避免长文撑高九个等宽列。交付后待办复用三行问题摘要；详情始终读取完整原始问题对象，JSON 和摘要导出不截断。手机端继续按记录卡片展示，排序与上层单层筛选保持可用。

问题的 task/module 保留 Review 原始定位，另以 planScope 补充经核对的 Plan 归属（taskIds、modules、basis、sourceRefs）。原问题明确指定任务时只关联该任务的模块，原始模块优先；只指定 Batch 时列出该 Batch 的任务及计划模块，页面明确标为“Batch 任务/Batch 模块”。不将 Batch 范围回写为问题的精确 taskId，不通过问题文案猜测定位；未知任务不回退到其他任务。旧报告缺少 planScope 时从已保存的 report.tasks 中关联显示，实际无依据时继续显示 0。主表、待办、详情及导出沿用同一归属口径。

图表由离线 SVG/CSS 组件 report-charts.js 生成并内嵌 HTML，组件内容参与 templateDigest 与文件对校验。首页任务环形图按当前任务快照分为已交付、已实现待交付、未实现、待核验；无有效分母时显示 0 与对应说明。首页 Batch Review 堆叠条形图按去重后的评审代码缺陷展示已修复、仍开放、待核验及其他关闭，非阻断建议单列且不计入缺陷修复数。条形比较每个批次的当前问题数，不累计任务，不伪造耗时或时间趋势。没有有效且完整的留存 Review 记录时标记记录待补齐，不将缺数解释为零缺陷；长列表在卡片内滚动，点击分段查看原始发现与修复证据。

变更页环形图取 apiAdded/apiModified/apiDeleted，包含已确认契约修改并保持待核验影响单列。源码增删条形图取已有 modules.added/deleted，沿用后台源码过滤口径；无法合并的仓库区间分别展示。Review 图按当前风险记录的已修复、仍开放、其他关闭状态分组，不表示全仓安全检查通过。每个图例/节点可查看已绑定来源，支持悬停数值、键盘操作和条形分段溯源，并接入当前 Tab 的本机交互记录。动画尊重减少动画偏好，空数据不绘制虚构分区或时间趋势。
