# 技术方案（申报草稿）

适用项目：AutoBizDevOps / autobiz_kanban。依据当前仓库版本 `0461924`、插件声明版本 `1.0.87` 整理，核查日期：2026-10-08。

以下第一至四部分可作为申报正文；第五至七部分供准备证明材料、补充数据和核对口径使用。文中的“效果”指代码支持的机制效果，未将预期收益写成已经取得的生产指标。

## 一、项目技术架构

本项目面向企业软件研发中需求理解易偏差、设计与实现易脱节、并行开发易冲突、AI 生成结果难验证等问题，构建覆盖 Biz（需求）、Dev（开发）、Ops（交付）的 AI 研发流程插件。项目采用“宿主平台承载模型与工具能力、插件编排研发流程、结构化契约约束任务执行、运行证据支撑交付判断”的技术架构，将需求澄清、行为规格、技术设计、任务拆解、编码、评审、测试、CI/CD 对接和归档纳入统一流程。

![图1：总体技术架构与实现边界](/Users/seikou/Documents/GitHub/autobiz_kanban/output/pdf/autobiz-technical-architecture.png![]())

图 1 展示系统组件及其归属，各部分职责如下：

1. **宿主与外部依赖。** 宿主 AI Coding 平台提供大模型调用、工具执行、代理运行及看板交互等能力；Git 与业务代码仓库、企业知识库、MCP 服务和 CI/CD 平台提供版本管理、知识来源及发布支持。这些运行环境位于插件实现边界之外。
2. **接入与适配。** 通过插件声明、技能与角色配置、工具调用钩子和外部服务配置，向宿主暴露状态查询、流程执行、产物检查及交付对接入口。
3. **编排与控制。** 以 `board_config.json` 定义节点、状态转换、输入输出及运行策略，编译当前 Feature 的有效流程；结合结构化契约、写入约束、DAG 调度、运行租约和恢复检查点，管理阶段推进及任务执行边界。
4. **执行与支撑。** 将需求、规格、设计、计划、实现、评审和测试封装为可复用技能，由宿主承载角色代理执行；提供工作区隔离、候选合并、端到端验证、证据审计、知识筛选和来源追踪等支撑组件。
5. **状态与产物。** 将状态、计划、需求文档、设计快照、运行清单、验证日志和来源记录保存为工作区内的结构化产物，作为流程展示、阶段交接、恢复与一致性核验的依据。

项目核心实现使用 Python，JavaScript 用于宿主工作流编排及知识文件处理，Git 用于代码版本管理和并行工作区隔离，JSON/JSONL 用于机器可校验的状态、计划与证据存储，Markdown 用于人可阅读的研发产物。该选型与现有插件式、文件工作区式交付场景相匹配，便于与开发工具集成、调试和审计。

Code 阶段的运行过程单独以图 2 展示。编排组件根据依赖与容量调度批次，各批次在隔离工作区内依次完成实现、评审和单测阶段记录，再进入候选合并；实际合并状态反馈给调度器以释放后续依赖。全部交付批次合并后，进行最终端到端验证和证据聚合。该图表达执行顺序、并发和反馈关系，与图 1 的组件结构分别呈现。

![图2：Code阶段执行流程](/Users/seikou/Documents/GitHub/autobiz_kanban/output/pdf/autobiz-code-execution-flow.png)

## 二、关键技术及应用

**1. 大模型驱动的技能化执行与角色协作**

针对需求理解、代码分析和程序生成等非结构化任务，项目通过宿主平台调用大模型，并将研发活动封装为需求澄清、规格编写、设计、计划、编码、评审和测试等可复用技能。不同角色按照明确的输入、职责和交付物协作，工具钩子负责补充任务上下文并约束阶段行为，使跨阶段工作能够依据产物持续推进。角色分工随任务复杂度配置，简单任务可采用精简流程，避免引入不必要的协作成本。

**2. 配置驱动的工作流与状态管理**

为使 AI 执行过程遵循确定的研发顺序，项目采用配置驱动的工作流与状态机，统一定义阶段节点、状态转换、输入输出产物和校验规则。系统根据当前需求生成有效流程，支持标准路线、精简路线及可选详细设计节点，并通过统一脚本更新状态、生成展示视图。阶段推进时同步检查迁移条件和产物完整性，使看板状态与执行依据保持一致，减少流程跳步和上下文衔接不清的问题。

**3. 贯穿规格、设计与计划的结构化契约**

多阶段生成容易出现文档解释不一致、任务遗漏或实现范围漂移。项目通过结构化契约连接行为规格、技术设计和执行计划，为接口、数据和设计决策建立稳定标识，并在设计确认后生成契约快照。计划阶段读取已确认的设计快照，检查引用关系、任务粒度和场景覆盖情况，使不存在的接口引用、设计约束缺失等问题能够在执行前被发现，为后续编码提供明确且可核验的边界。

**4. 基于依赖关系的并行调度与隔离集成**

针对可拆分的开发任务，项目以有向无环图（DAG）描述批次依赖，通过独立 Git Worktree 隔离各批次的代码修改，并结合运行租约控制任务领取与恢复。调度器根据依赖状态和空闲槽位动态启动任务，上游批次实际合并后才释放下游依赖；关键阶段保持串行，普通批次支持乐观或保守调度。批次交付通过候选合并流程汇集，检查代码基线并处理冲突，使并行实现与有序集成相衔接，减少不必要的整批等待。

**5. 分阶段验证与交付证据一致性校验**

项目将代码评审、单元测试和合并后的端到端验证纳入执行链路，并保存相应结果作为交付判断依据。验证记录关联计划版本、代码提交、依赖快照和测试环境，通过追加式 JSONL、独立日志、文件锁、原子写入及 SHA-256 摘要进行存储和核验。结束阶段聚合既有证据，识别缺失、损坏或版本过期的记录，降低使用旧验证结果确认新代码的风险。延期测试及非阻塞问题保留明确记录，阻塞性问题参与最终完成判断，使交付状态及其依据能够被复核。

**6. 企业知识上下文组织与来源追踪**

企业研发需要结合产品规则、服务说明和外部需求材料理解任务。项目根据发布单元及知识文档元数据筛选相关文件，组织为模型可使用的上下文，并记录外部材料的来源、读取状态、快照和引用关系。该方式利用既有文档资产控制上下文范围，使业务依据能够在需求、规格和后续研发阶段回查；同时通过 MCP 配置预留与外部服务的接入能力，按实际环境扩展工具支持。

**7. 基于 Human Gate 的人机确认机制**

项目通过 Human Gate 脚本与 AI 宿主联动，在技术设计完成前引入人工确认。当 AI 尝试将技术设计阶段推进为完成时，插件通过工具执行前钩子识别该操作，向宿主返回人工确认指令及提示内容；宿主暂缓执行，并提醒用户检查技术设计产物，待用户确认后再放行阶段推进。插件负责定义确认时机和提醒内容，宿主负责用户交互与执行放行，将人工审核嵌入 AI 工具调用流程，使技术设计的生成与人工确认形成明确交接。

底层采用 Python 实现运行内核，JavaScript 承担宿主工作流编排和知识文件处理，JSON/JSONL 保存结构化事实，Markdown 承载可阅读产物，与当前插件式、工作区内运行的交付方式相适应。并发容量可按任务依赖、冲突情况和环境资源调整，实际提效幅度通过项目运行数据衡量。

## 三、创新性、先进性与技术亮点

本项目的创新性主要体现在面向企业研发交付的工程机制与组合应用。

**亮点一：将研发规范转化为可执行契约。** 项目将需求、规格、设计、计划和实现之间的交接关系具体化为节点契约、稳定标识、设计快照、引用检查和工具钩子。系统能够在执行路径中检查边界及产物一致性，使过程规范具备可执行性。

**亮点二：以真实依赖和隔离工作区支撑并行交付。** 项目将任务依赖、并发容量、工作区隔离、运行租约和候选合并联合设计。调度器随运行状态变化补充可执行任务，并将上游实际合并作为下游依赖释放条件，使并发与代码集成建立明确关系。

**亮点三：形成与代码版本关联的交付证据。** 验证记录关联计划版本、代码提交、依赖快照及测试环境，结束阶段聚合既有证据并检查一致性。缺失、损坏或过期证据可被识别，延期问题能够显式保留，从而提升交付结论的可解释性和可复核性。

**亮点四：将持续执行中的异常恢复纳入设计。** 项目为任务租约、证据写入、阶段回退及 Git 合并与计划状态同步提供恢复路径。其中，合并意图和恢复检查点用于处理“Git 已合并、计划尚未写回”的中间状态，避免简单重跑导致状态混乱。

对先进性的判断：从公开技术路线看，持久化状态、分步执行、工具反馈、人机协作和长任务交接已是代理工程的重要方向。Anthropic 对长任务代理的讨论强调结构化任务记录、Git 历史与端到端验证；LangGraph 文档也将检查点持久化用于中断恢复、人机交互和容错。本项目针对企业研发进一步实现了设计契约、批次调度、候选集成和证据核验，体现的是这些方向在具体交付流程中的工程深度。此判断是基于代码与公开材料的分析，不等同于行业排名或首创认定。[Anthropic 长任务代理实践](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)；[LangGraph 持久化机制](https://docs.langchain.com/oss/python/langgraph/persistence)。

## 四、实际效果与验证情况

当前代码已形成从需求输入到交付归档的阶段化流程配置，并实现契约校验、批次并行、候选合并、证据审计、回退恢复及外部知识接入等关键机制。

2026-10-08 对当前版本的设计契约锁、证据审计、乐观并行、分阶段流水线、执行钩子、阶段回退和外部来源追踪等七个测试文件进行了定向运行，共执行 72 项测试，结果为 **71 项通过、1 项失败**。失败项为 `test_guard_is_registered_before_execute_commands`：测试期待字符串 `python hooks/code_execution_guard.py`，当前配置实际为 `python -X utf8 hooks/code_execution_guard.py`，属于该断言与配置字符串不一致。本次未修改该问题，也未执行全量测试或宿主平台端到端验收。

因此，现阶段可据此陈述关键机制已有代码实现及部分自动化测试支撑；交付周期、人工投入、缺陷率、模型调用成本等业务收益，应结合真实项目基线与运行数据另行量化。

## 五、建议补充的效果数据

| 指标 | 建议统计口径 | 数据来源 |
|---|---|---|
| 需求交付周期 | 同类需求从范围确认到验收的耗时中位数，比较使用前后变化；周期缩短率 =（基线耗时－使用后耗时）/ 基线耗时 | 需求记录、节点时间、验收记录 |
| 人工投入 | 同类需求投入的人时；分别记录确认、修复、冲突处理和发布操作 | 工时记录、人工介入记录 |
| 并行收益 | 相同或可比任务、模型、环境下的串行耗时 / 并行耗时，同时列出冲突处理时间 | 调度 manifest、执行事件、模型与环境记录 |
| 验收质量 | 首次验收通过需求数 / 验收需求总数；另列交付后缺陷及严重程度 | E2E 报告、验收单、缺陷记录 |
| 完成证据有效率 | 证据完整且版本匹配的已完成任务数 / 已完成任务数 | 计划文件、证据审计、最终聚合报告 |
| 人工介入与成本 | 需人工处理的运行数 / 总运行数；模型调用费用按成功交付需求归集 | 运行记录、平台计费、冲突与阻断记录 |

建议至少说明样本量、任务复杂度、统计周期、使用模型和运行环境；不要把默认并发上限直接换算成提效倍数。

## 六、证明材料与附件建议

1. **总体架构与执行流程图。** `output/pdf/autobiz-technical-architecture.pdf` 包含两页独立图：第一页展示系统组件和插件实现边界，第二页展示 Code 阶段执行流程。另提供 `autobiz-technical-architecture.png` 与 `autobiz-code-execution-flow.png`，可分别作为图片附件上传。
2. **一次真实 Feature 的流程截图。** 展示需求、规格、设计、计划、代码和交付的节点状态，并展示对应产物。该图需从宿主看板获取；仓库配置不能替代真实运行截图。
3. **契约约束示例。** 选取设计契约锁、计划引用及引用错误被拦截的真实案例，说明何时阻断、为何阻断、如何恢复。
4. **并行执行与合并示例。** 使用同一运行编号，展示 DAG、同时执行的批次、独立工作区、合并提交与依赖释放，证明是实际并行运行。
5. **验证证据示例。** 展示 Review、UTest、E2E 结果和最终聚合报告，保留运行编号、提交 SHA、结果状态和必要时间信息；延期项也应保留。
6. **量化对比页。** 将第五部分中已有真实数据制成图表，标注样本和口径，作为效果证明。
7. **Human Gate 确认示例。** 展示技术设计完成时宿主弹出的确认提示，以及确认前后阶段状态的变化，说明插件脚本与宿主提醒、确认交互的配合方式。

架构图属于技术说明材料，实际运行截图、日志及指标属于效果证明，两者配合使用更有说服力。

## 七、代码依据与填写口径

| 材料中的论点 | 仓库证据 |
|---|---|
| 项目定位、插件版本及接入方式 | [plugin.json](/Users/seikou/Documents/GitHub/autobiz_kanban/plugin.json:1) |
| 工作流节点、契约、标准/精简及动态路线 | [board_config.json](/Users/seikou/Documents/GitHub/autobiz_kanban/board_core/board_config.json:1)、[workflow_compiler.py](/Users/seikou/Documents/GitHub/autobiz_kanban/board_core/workflow_compiler.py:1324) |
| 设计快照及其使用边界 | [design_contract_lock.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/design_contract_lock.py:102) |
| 工具级检查与写入约束 | [hooks.json](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/hooks.json:1)、[code_execution_guard.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/code_execution_guard.py:1) |
| Human Gate 与宿主联动确认 | [human_gate_design_done.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/human_gate_design_done.py:1)、[钩子注册](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/hooks.json:108) |
| 动态调度、依赖及租约 | [parallel_runtime.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/parallel_runtime.py:529)、[动态补位逻辑](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/parallel_runtime.py:891) |
| 固定分阶段执行与最终验证 | [code-batched-execution.workflow.js](/Users/seikou/Documents/GitHub/autobiz_kanban/workflows/code-batched-execution.workflow.js:1) |
| 合并意图与计划恢复 | [parallel_merge_train.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/parallel_merge_train.py:369) |
| 证据完整性与最终聚合 | [evidence_integrity_gate.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/evidence_integrity_gate.py:48)、[parallel_evidence_aggregate.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/parallel_evidence_aggregate.py:105) |
| 知识筛选与外部来源 | [collect-knowledge.js](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/collect-knowledge.js:1)、[source_context.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/source_context.py:1) |
| 流程回退与交付对接 | [rollback_stage.py](/Users/seikou/Documents/GitHub/autobiz_kanban/hooks/rollback_stage.py:1)、[autoops-cicd/SKILL.md](/Users/seikou/Documents/GitHub/autobiz_kanban/skills/autoops/autoops-cicd/SKILL.md:1) |

填写时需要保留以下边界：

- **创新定位：** 可写工程机制创新、研发交付流程创新与系统集成创新。当前仓库未提供自研基础模型、训练算法、行业首创或领先排名的证明。
- **宿主边界：** 本仓库主要是流程插件与运行脚本，不能把宿主看板、大模型服务或外部发布平台全部归为本仓库自研。
- **人工确认：** `human_gate_design_done.py` 当前只针对实际推进 `design_done` 的命令返回 `decision: "human_gate"` 和提醒文本，其他检查点及 `--dry-run` 不触发。脚本识别确认时机，不自行判断设计质量；提醒界面、等待确认及执行放行由宿主实现。
- **知识能力：** 可写元数据筛选、上下文注入和来源追踪；当前核查材料未证明向量检索、知识图谱或模型微调能力。MCP 为已配置的外部服务接入，外部服务可用性本次未验证。
- **质量策略：** 当前主流程是批次 Review/UTest、候选合并、合并后 E2E、证据聚合。候选独立测试阶段已被当前实现移除，历史 MVP 文档中的 B-INT 流程不宜直接沿用；部分延期项可记录后继续，不可统一表述为“所有单测通过才合并”。
- **复杂冲突：** 自动处理范围有限；`conflict_resolution_agent.py` 的复杂模型辅助解决分支仍返回人工处理提示，不能写“复杂语义冲突全自动消解”。
- **证据保证：** SHA-256、文件锁和索引支持工作区内一致性与完整性检查，不能据此宣称具有第三方存证、不可抵赖或绝对不可篡改能力。
- **效果口径：** 不能据代码规模、代理数量或并发上限直接宣称工时下降、质量提升或成本降低的百分比。

本次定向测试命令：

```bash
python3 -m pytest -q tests/test_design_contract_lock.py tests/test_evidence_audit.py tests/test_optimistic_parallel.py tests/test_parallel_staged_pipeline.py tests/test_code_execution_guard.py tests/test_rollback_stage.py tests/test_external_source_traceability.py
```

运行结果：`1 failed, 71 passed in 24.03s`。失败断言位置：[test_code_execution_guard.py:112](/Users/seikou/Documents/GitHub/autobiz_kanban/tests/test_code_execution_guard.py:112)。
