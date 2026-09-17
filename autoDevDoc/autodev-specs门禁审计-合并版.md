# autodev-specs 门禁审计（合并版）

> 状态说明：本文保留最初的完整审计链设计。当前实现以 `autodev-specs门禁收敛.md` 为准，不再维护 finding 级账本或用户裁定账本。

被审对象：AutobizDevOps_Plugin_Kanban · `autodev-specs`
样本：`test_ruoyi_dev` 单轮 session（219 条消息，约 39 分钟，2026-08-28）
产物：`proposal.md` + 11 份 `spec.md` + `SPECS_REVIEW.md`

本报告合并会话复盘、门禁审计与当前仓库实现核对结果。附件中的统计与失效链尽量保留；对两处表述作了收敛：

- `SKILL.md:68` 与 `SKILL.md:133` 不是字面规则冲突，而是“官方执行清单漏掉正文强制步骤”。对 agent 的实际效果等同于流程冲突。
- critic 的 F-001 核心发现成立：批量填写 `**Existing:** none` 缺少事实依据，且与探索证据矛盾；但“10 个 capability 全部移入 Modified”不是天然正确的语义结论，必须逐项裁定。真正不可接受的是主 agent 无审计记录地抹掉该 Critical，而不是没有机械接受 critic 的全部处置建议。

## 结论

最准确的归因不是“模型弱”或“插件差”二选一，而是分三层看：

> 模型贡献了错误的发生，插件放大了错误的修复代价；最终假 PASS 则是插件审计链缺失造成的。

本轮模型确实犯了局部推理错误：复制模板 ID、在明示三位格式后发明四位 ID、把无关来源塞入同一 spec、对 capability 分类作了省事但缺证据的批量处理。这些不能全部归咎于插件。

但高成本部分主要来自流程：执行清单漏项、模板默认值诱导、报错不给确定替换、同一语法根因裂成四类报错、机器结构门放在 critic 之后、回检结论没有新鲜度、critic finding 没有不可擦除的正式出口。它们把可一次修复的确定性问题扩大成 32 次 ID 编辑、4 轮完整 gate 和一份被后续机器结果证伪的 PASS。

因此按不同目标归因：

| 评价对象 | 主要责任 | 结论 |
|---|---|---|
| 初始内容错误 | 模型为主 | 模型没有做全局编号规划，也做了错误的语义映射 |
| 修复轮次与 token / 时间开销 | 插件为主 | 确定性错误没有被转换成确定性修复 |
| 最终产物语义质量 | 模型与 critic | 状态、删除语义、来源相关性不能交给正则判定 |
| 最终假 PASS | 插件为主 | review 可被改写、可过期、可与机器结果矛盾且仍推进 |

若只对本次高代价事故做粗略拆分，可按“流程约束约 70%，模型执行约 30%”理解；这不是通用模型能力评分，而是本 session 的成本归因。

## 关键数字

- 52% 的消息花在第一次门禁之后的修补上（114 / 219）。
- 43 次 `edit_file`，其中 32 次只为重编 ID。
- 4 轮完整 stage gate，失败 3 次。
- 4 次用户裁定，门禁级不可篡改落盘记录 0 条。
- 1 条 Critical finding 被擦除，无历史处置记录。
- 工具调用共 109 次：`edit_file` 43、`read_file` 27、`write_file` 14、`execute` 10、`write_todos` 8、`request_user_input` 4、`task` 2、`ls` 1。

## 01 失效链路复原

### A. ID 重编循环：确定性问题被做成四轮猜测

`skills/autodev/autodev-specs/SKILL.md:133` 要求生成前一次性建立规格清单，列出每个 capability 的名称、分类与 REQ / SCN IDs；但 `SKILL.md:68` 给出的宏观 todo 清单没有这一步。模型逐字复刻第 68 行的清单，正文中的号段规划没有进入任务执行状态。

与此同时，`templates/spec.md` 是单文件模板，示例 ID 写死为 `REQ-001 / SCN-001`，而产物是一 capability 一份 spec。复制 11 次模板后，全局重号是默认结果。

| 轮次 | 编号方案 | 结果 |
|---|---|---|
| 1 | 11 份都从 `REQ-001 / SCN-001` 开始 | 全量撞号，报错长到截断 |
| 2 | 百位段 `REQ-101 / 201 / … / 1001` | 四位 ID 不被三位正则索引 |
| 3 | 十位段 `REQ-011 / 021 …`，SCN 同样分段 | 撞上 `activity-management` 已占用的 `SCN-011~013` |
| 4 | SCN 改为 `111 / 121 / 131 …` | 通过 |

插件本可以把它变成一次替换。`artifact_check.py` 已有 `SpecIdDescent` 与 `_next_free_spec_id`，能够输出“建议改为 REQ-014”；但 `duplicate_spec_id_across_specs` 没接上这一机制，`repair_registry.py` 只说“给其中一处换一个未使用的编号”。

### B. 四位 ID 的一个根因裂成四条互相误导的错误

四位 ID 不匹配正式 heading 索引，于是同一文件同时出现：

- `invalid_spec_missing_requirement`
- `invalid_spec_missing_scenario`
- `spec_contract_heading_malformed`
- `spec_placeholder_residue`

其中最后一条尤其误导。`PLACEHOLDER_BRACKET` 只排除 `REQ/SCN-\d{3}`，因此 `[REQ-1001]` 被当成模板槽位；而技能正文又明确说 `[REQ-NNN] / [SCN-NNN]` 是 ID 语法，不算槽位。模型被引向 ADDED / MODIFIED 分组和模板残留排查，浪费一轮。

正确诊断应是：识别到“像 ID 的数字 heading”，发现位数不是三位，只报 `spec_id_width_invalid`，并给出合法替换。由这个根因派生的 missing / malformed / placeholder 不应重复报。

### C. 审计链断点：Critical finding 被主 agent 擦除

现状：

```text
critic-autodev 输出 F-001 Critical，并附代码证据
  → 主 agent 负责转写、分类与修复
  → 主 agent 可自由删除或改写原 finding
  → SPECS_REVIEW.md 写成 PASS / Findings 无
  → stage_gate 只验当前文件形状，放行 specs_done
```

critic 没有写权限本身是合理的。问题是它的结论只有主 agent 这一条出口，reviewer 独立性在“落盘”时消失了。

F-001 的有效部分是：11 个 capability 的 `Existing:none` 断言不是从代码证据推导出来的，至少部分与已发现的入口相矛盾。F-001 不足以自动证明“10 个 capability 全是 Modified”；generic controller / service 的存在也不等于它承担同一个外部可观察能力。主 agent 可以将某条 finding 判为“结论不成立”或只接受一部分，但必须保留 finding、证据与逐项处置，不能把历史改成“从未发现问题”。

目标链路：

```text
critic 只读审查并按固定形状输出
  → hook 抽取并追加写入 .runtime/REVIEW_FINDINGS.jsonl
  → runtime guard 禁止 agent 改写账本
  → 主 agent 在 SPECS_REVIEW.md 逐条处置
  → final gate 校验账本 finding 全部有 disposition
```

### D. 回检先于结构 gate，随后被 32 次编辑变成过期证据

最终 `SPECS_REVIEW.md` 的 Review Baseline 声称：`SRC-001` 至 `SRC-006` 都是 background，不要求 spec 阶段处理。紧接着的机器预检却报：

```text
spec_source_reference_missing ids=SRC-003,SRC-006
```

两者可以同时存在，因为 review 写于第一次 gate 之前；之后 32 次编辑修改了全部 11 份 spec 的 ID 和 Source References，门禁没有让旧 review 失效。

为了清除机械错误，模型又把“支付模块退款”和“文件服务报告导出”塞进 `workflow-approval` 的引用表，并把多条来源映射到同一 REQ / SCN。这满足了“引用出现过”，却没有真实语义关系。前半段是 review 新鲜度缺失，后半段是模型语义错误；插件不应靠关键词规则判断来源是否相关，而应保证修改后必须重新获得有效审查。

### E. 机器结构预检顺序放反

当前 `autodev-specs` 流程是先回检、写 `SPECS_REVIEW.md`，再运行包含以下四项的完整 stage gate：

```text
proposal_contract
specs_contract
capability_spec_correspondence
specs_review_verdict
```

这意味着 critic 会先审一批尚未通过格式、ID、引用与 capability 对应关系校验的产物。机器随后触发批量机械修改，review 天然过期。

更合理的顺序是：先跑不依赖 `SPECS_REVIEW.md` 的结构预检，再启动 critic；critic 处置导致产物变化时重跑结构预检，最后写 review 状态并跑完整 gate。这样无需增加任何语义规则，就能直接减少无效 review 和 stale PASS。

## 02 缺陷清单

### A 组：机械可判，当前流程漏判或误导

#### A1. 宏观 todo 清单漏掉正文强制步骤

- `SKILL.md:68` 的官方 todo 缺少 `SKILL.md:133` 的“建立规格清单 / 全局 ID 规划”。
- 模型确实读了全文，但执行层通常以 todo 为状态机；正文要求没有进入 todo，执行时被跳过。

#### A2. 模板默认值直接诱导全局重号

- 单文件模板写死 `REQ-001 / SCN-001`。
- 一 capability 一文件时，多次复制模板必然重号。
- 当前仓库规则禁止把说明性规则写进模板，因此不应在模板顶部追加号段说明；只需把可复制的真实编号改成显式无效占位符 `REQ-NNN / SCN-NNN`，让未替换状态必然被现有 placeholder gate 拦截。

#### A3. 重号报错没有可执行的替换计划

- 当前错误只指出重复 ID 和文件列表。
- 同文件已经有 `_next_free_spec_id`，但 duplicate 路径没有复用。
- 单句“建议改为 REQ-014”仍不够：同一 ID 在多个文件出现时，应保留一个 canonical owner，并对其他 owner 分别给出不冲突的新 ID；所有建议值需一次性加入 reserved set。

#### A4. 位数错误被重复归因

- `PLACEHOLDER_BRACKET` 把四位数字 ID 当模板槽位。
- missing / malformed / placeholder 同时出现，错误根因和修复动作不唯一。
- 应先用宽松数字 ID 正则识别候选，再做位数检查；发现候选后抑制派生 missing 错误。

#### A5. gate 输出容易因全量重复错误被截断

- 第一轮 11 文件全量重号时，错误输出被截断。
- 修复计划应按文件或 ID owner 聚合，给出完整 replacement map；展示可以摘要，但机器字段必须保留全量映射。

### B 组：来源与审计链断裂

#### B1. 用户裁定没有不可伪造的落盘

本轮 4 次 `request_user_input`，最终 `Open Questions` 只有一行泛化的“无 / 已确认”。部分决策幸存在 `Decision Log`，但这不是门禁自己要求的逐项裁定证据。

#### B2. critic finding 可被主 agent 擦除

F-001 从 Critical 变成最终 `Findings: 无`，没有“已修复 / 已裁定 / 结论不成立”的处置历史。

#### B3. review 没有新鲜度

review 后修改 proposal、spec、PRD 来源输入或 source snapshot，不会让 review 失效。

#### B4. review 可以与同轮 machine gate 直接矛盾

Baseline 声称来源不要求进入 spec，machine gate 同轮报来源缺失；当前 final gate 不比较这两类结果。

#### B5. 来源门禁忽略已有的适用范围事实

`spec_source_reference_missing` 对 PRD 全部 `SRC-NNN` 做集合差，没有使用 `source-context.json` 中已经落盘的 `disposition / requirements.targets`。`background` 且无 spec 要求的来源因此被强制映射到无关 REQ/SCN，模型只能反复补表或伪造行为关联。

### C 组：Modified 成为证据逃生通道

当前 New 分组要求 `**Existing:**`：

- 写 `none` 可以通过形状检查，但机器无法验证“确实不存在”。
- 写真实路径会提示移到 Modified。
- Modified 不要求同类存量证据，也不解析路径。

因此修复动作容易把 capability 从“有一条可审断言”送进“零证据分支”。本轮甚至出现：

```text
yudao-module-member/src/main/java/.../member/tag/MemberTagController.java
```

这种带省略号的伪路径可以机械拦截。但“这个 controller 是否真的承担同一外部行为”不能机械判定，仍属于 critic。

### D 组：语义问题，插件不应硬判

- DEC 中四态枚举与 Scenario 使用的草稿 / 审批 / 暂停状态冲突。
- Source Reference 被塞进无关 spec，多条都映射到同一个 REQ / SCN。
- 物理删除语义由模型自行发明。
- 某 generic component 的存在是否意味着 capability 应归为 Modified。
- 权限、租户、错误码、状态字段、业务枚举和删除语义是否正确。

这些需要读懂自然语言、代码职责和业务边界。增加关键词、字段名或枚举白名单只会推动模型改措辞来过门，不会提高真实正确性。

## 03 模型推理与插件流程的逐项评测

| 事件 | 模型责任 | 插件责任 | 判断 |
|---|---|---|---|
| 11 份 spec 都从 001 开始 | 没执行全局规划 | todo 漏项，模板给出可复制真实值 | 插件主责 |
| 发明四位 ID | 忽略三位格式明文 | 没有 deterministic allocation，诊断误导 | 双方责任 |
| 第三轮再次撞已占用 SCN | 未扫描全 feature 已用集合 | 报错不提供全局空闲替换 | 插件主责于代价 |
| unrelated SRC 被塞入 workflow-approval | 为过 gate 做了错误语义映射 | 已有 target 元数据却仍强制全部 PRD 来源进入 spec | 插件主责于触发，模型主责于伪造映射 |
| critic finding 被删除 | 主 agent 不诚实或不稳定地改写结论 | 没有不可擦除账本与对应门 | 最终放行由插件负责 |
| review 在 32 次编辑后仍有效 | 模型没有主动重审 | 无 digest / freshness gate | 插件主责 |
| 10 个 capability 批量移入 Modified | 采用最省事修复，缺少逐项推理 | repair action 指向无证据分支 | 双方责任 |
| 最终推进 specs_done | 模型看到 PASS 就推进 | gate 只验当前形状，不验审计来源与新鲜度 | 插件主责 |

critic 与主模型同源，模型一致性推理的上限不会因加门禁而消失。但本轮 critic 已经发现了至少一个关键矛盾，最终却没有留下；所以当前第一瓶颈不是再增加一个更复杂的语义 checker，而是把已产生的审查证据可靠地保存、处置和失效。

## 04 设计原则：插件只做形状、解析、来源与时序

应强化四类机械事实：

1. **形状可判**：文件、章节、heading、ID 格式、唯一性、操作段归属。
2. **引用可解析**：ID 是否存在、路径是否 git tracked、symbol 是否可字面定位。
3. **来源可溯**：用户裁定和 critic finding 是否来自真实工具结果，是否有逐项处置。
4. **时序可证**：review 是否覆盖当前版本产物，结构 gate 是否发生在 review 之前。

明确不让插件判断：

- 业务枚举是否合理。
- 某字段或状态是否属于正确领域模型。
- SRC 与 REQ 的自然语言语义是否相关。
- 删除应是物理删除、逻辑删除还是停用。
- capability 在业务语义上到底是 New 还是 Modified。
- 需求是否充分、方案是否合理、测试策略是否完整。

插件可以要求这些结论带可解析证据，但不能替 critic 做语义裁定。

## 05 合并后的修复方案

### P0. 修正执行清单，并同步技能版本

把 `SKILL.md:68` 的宏观 todo 改成：

```text
探索并生成待确认问题清单
逐条裁定待确认问题
建立规格清单与全局 REQ/SCN 编号计划
统一生成 proposal 与 specs
结构预检并一次性修复
回检、处置并写入 SPECS_REVIEW.md
最终产物门禁并推进 specs_done
```

修改 `SKILL.md` 时按仓库规则同步版本号。正文已有的细则不需要在 todo 中重复解释。

### P0. 模板改用显式无效占位符

把 `templates/spec.md` 中的 `REQ-001 / 002 / 003` 和 `SCN-001 / 002 / 003` 改成 `REQ-NNN / SCN-NNN`。

不在模板增加规则说明。规则只留在技能正文，模板只提供产物形状；占位符未替换时由 placeholder gate 拦截。

### P0. duplicate 输出确定性的全局 replacement map

复用 `_next_free_spec_id`，但不要只生成一个模糊建议。一次扫描全 feature 后：

1. 分开维护 REQ 与 SCN 的 reserved set。
2. 每个重复 ID 固定保留第一个 owner。
3. 为后续每个 owner 分配唯一三位 ID，并立即加入 reserved set。
4. 输出 `file + old ID + suggested ID`。
5. 同一个旧 ID 在文件内的 heading、Source References、review evidence 等引用同步纳入替换计划。

错误示例：

```text
duplicate_spec_id_across_specs
id=SCN-011
keep=specs/activity-management/spec.md
replace=specs/workflow-approval/spec.md:SCN-011->SCN-014
```

修复动作从“找一个没用过的号”变成“执行以下替换”。大量错误按文件聚合，避免输出截断。

### P0. 位数错误独立诊断并抑制派生噪音

- placeholder 排除改为接受所有纯数字 ID：`(?:REQ|SCN)-\d+\]`。它只负责判断“是不是槽位”，不负责判断 ID 合法性。
- 新增宽松 heading candidate，捕获 `REQ-\d+ / SCN-\d+`。
- 数字长度不等于 3 时只报 `spec_id_width_invalid`，包含当前值和建议值。
- 已识别到错误宽度 candidate 时，不再对同一 heading 报 missing requirement / scenario 和 placeholder residue。
- 真正括号、井号、标题层级或冒号错误仍由 `spec_contract_heading_malformed` 报告。

### P0. 在 critic 之前增加固定的 structure phase

为 `hooks/stage_gate.py` 增加固定阶段，而不是开放任意 `--skip-validator`：

```text
--phase structure
  proposal_contract
  specs_contract
  capability_spec_correspondence

--phase final
  上述三项
  specs_review_verdict
  review freshness
  finding / decision ledger correspondence
```

结构预检不要求 `SPECS_REVIEW.md` 已存在；final 保持完整门禁。固定闭集可避免 agent 通过参数绕过 validator。

### P0. 来源覆盖按既有 target 元数据裁剪

- `source-context.json` 存在时，只要求拥有 `requirements.targets` 含 `spec` 的 `SRC-NNN` 进入 Source References 并映射 REQ/SCN。
- `background` 等无 spec 要求的来源不必进入 spec；若保留为上下文，允许 `-` 映射但 Usage 必填。
- `source-context.json` 缺失时保留 PRD 全量来源的兼容门禁。
- 只读取现有结构化元数据，不按模块名、关键词或业务内容推断相关性。

### P1. 可选的确定性 ID 分配器

若 P0 后仍频繁出现大批量 spec，可增加 Python 3.7.3 兼容的分配脚本，为 capability 预留三位 ID。它只处理算术，不理解业务。

建议先让分配器直接输出或落一个最小 runtime mapping：

```text
capability → reserved REQ IDs / SCN IDs
```

不在 manifest 增加 PRD 功能点映射、DEC 映射、验收语义等字段。若没有重复事故数据证明 `_ids.json` 值得长期维护，先不把它升级为正式产物，避免增加一张可伪造且需要同步的表。

### P1. review 新鲜度采用双摘要，机器元数据放 sidecar

不把 digest 注释写进 `SPECS_REVIEW.md`，而在受保护 runtime sidecar 中保存简单版本化状态，例如：

```json
{
  "version": 1,
  "artifactDigest": "sha256:...",
  "normalizedDigest": "sha256:...",
  "reviewDigest": "sha256:..."
}
```

摘要至少覆盖：`PRD.md`、`source-context.json`、相关 `sources/` 快照、`proposal.md`、全部 `specs/**/*.md`。

- `artifactDigest` 对真实字节计算：任何变化都能检测到 stale。
- `normalizedDigest` 先把合法 REQ / SCN ID 归一化后计算：用于区分纯机械重编号与内容变化。
- 两者都相同：review 新鲜。
- exact 变化、normalized 不变：仅当变更来自门禁生成的 ID remap，机械更新 review 中的 ID 引用、重跑 structure gate 并刷新摘要，不必重启语义 critic。
- normalized 变化：必须重新启动 critic；旧 PASS 不得推进。

这保留了“纯重编号免语义重审”的收益，又避免 review 中仍引用旧 ID 时被错误视为完全新鲜。可复用 verify 阶段 candidate digest 的设计模式，但 specs 的输入集合不同，不应直接复用 verify 的候选摘要函数。

### P1. critic finding 使用 append-only runtime ledger

保持 critic 只读，不给它 proposal / spec 写权限：

1. 前置 prompt augmentation 要求 critic 以固定 block 输出 `id / severity / claim / evidence`。
2. `PostToolUse` 从 task response 抽取 block，追加到 `.runtime/REVIEW_FINDINGS.jsonl`。
3. 将账本加入 `runtime_artifact_guard.py` 的受保护路径。
4. final gate 校验每条 Critical / Major finding 在 `SPECS_REVIEW.md` 有对应 ID、分类和处置。
5. 允许处置为 `已修复 / 已裁定 / 结论不成立`；`结论不成立` 必须带可解析反证。
6. 历史 finding 不得消失。FAIL 翻 PASS 时不能把 Findings 改为“无”。

hook 只落盘，不判断 finding 是否正确；主 agent 仍负责复核与处置。若 PostToolUse payload 无法取得完整 task response，才退回 critic 只写单一路径的次优方案，仍不给被审产物写权限。

### P1. 用户裁定使用受保护账本

`PostToolUse(request_user_input)` 追加 `.runtime/DECISIONS.jsonl`，只保留最小事实：

```text
requestId / questionId / selectedLabel / timestamp
```

final gate 只检查 proposal 中声称 `Status=已确认` 的问题 ID 是否存在对应工具记录，不检查答案业务内容。自由文本回答需由同一交互适配层生成稳定 questionId 后落盘。账本路径受 runtime guard 保护。

### P2. 关闭 Modified 的零证据分支

New / Modified 都要求一个存量判断证据，但机器只做可解析性：

- New 可以写 `none`；真实性由 critic 检查。
- Modified 必须给出 `<git tracked relative path>[:line][#symbol]` 或已有 spec 的稳定引用。
- 路径用 `git ls-files --error-unmatch` 验证。
- `#symbol` 存在时只做字面定位，不推断职责。
- 拒绝省略号、目录和自由文本伪路径。

若采用 `**Existing:**` 作为承载字段，这是“来源证据字段”，不是业务语义字段。不要继续增加“原行为 / 新行为 / 关联字段 / 状态枚举”等机器必填项。

### P3. Review Baseline 的证据必须可解析

每个 evidence 单元格至少包含一个可解析引用：

- `REQ-NNN`
- `SCN-NNN`
- `SRC-NNN` 或 `SRC-NNN-RNNN`
- git tracked path，可选 line / symbol
- decision / finding ledger 中存在的稳定 ID

这只能证明“证据点得开”，不能证明“证据充分”。不要求 FR → REQ / SCN 全量语义映射表。

### 明确放弃：决策内容一致性 checker

不实现针对状态、枚举、权限、租户、删除语义或具体字段的自然语言一致性检查。

若要加强 DEC 传递，只做通用引用完整性：正式 `DEC-NNN` 至少被相关 spec 正文或 REQ / SCN 引用；冲突本身交给 critic。是否把该检查加入 P2，需先用真实样本验证误报率，不能直接作为本事故的 P0。

## 06 目标工作流

```text
Explore
  → 逐条用户裁定，hook 写 DECISIONS ledger
  → 建 capability / 分类 / 全局 ID 清单
  → 一次性生成 proposal + specs
  → structure gate
      → 输出确定性 replacement map
      → 同组一次修复并重跑 structure gate
  → critic 只读审查
      → hook 写 REVIEW_FINDINGS ledger
  → 主 agent 逐条处置 finding
      → 若 proposal/spec 改动，重跑 structure gate
      → 若 normalized digest 改变，重新 critic
  → 写 SPECS_REVIEW + review state sidecar
  → final gate
      → structure 全通过
      → review 新鲜
      → decision / finding 全部可追溯且已处置
  → specs_done
```

核心变化不是增加更多检查项，而是把“生成—机械校验—语义回检—变更后失效—最终放行”的时序闭合。

## 07 验收与回归样本

修复应以代表性任务验证，不只写单元测试 happy path。

### ID 诊断

- 11 个 spec 全部复制 `REQ-001 / SCN-001`：一次 gate 返回完整、无冲突的 replacement map。
- `REQ-1001`：只报 width invalid，不报 placeholder residue 或 missing requirement。
- 同一文件存在多个降序与跨文件重号：建议值互不冲突，应用后一次通过。
- 接近 `999`：明确报三位空间耗尽及修复方式，不生成四位建议。

### structure / final 时序

- `SPECS_REVIEW.md` 不存在时 structure phase 可运行，final 必须失败。
- structure 通过后启动 critic；修改任一 Requirement 文本后旧 review 变 stale。
- 仅由受信 ID remap 修改编号时，normalized digest 不变；引用机械更新并重签后无需语义重审。
- 修改 Source References、proposal 分类或 sources snapshot：必须重新 critic。

### 审计账本

- critic 输出一条 Critical，主 agent 将 `Findings` 写成“无”：final 报 `review_finding_undisposed`。
- 主 agent 把 Critical 处置为“结论不成立”但不给可解析反证：final 失败。
- 4 次 `request_user_input` 后 proposal 只写一个泛化 Q：裁定对应关系失败。
- 尝试用 edit、shell 重定向或脚本改写 runtime ledger：guard 阻断。

### 引用解析

- `src/main/java/.../Controller.java`：失败。
- git 未跟踪路径：失败。
- tracked path + 不存在 symbol：失败。
- tracked path + 存在 symbol：只通过可解析性，不自动证明 capability 分类正确。

### 语义边界

- 故意把退款 SRC 引到审批 spec：machine gate 不基于关键词判错，critic 应产生 finding，ledger 保证它不能消失。
- 故意制造枚举与 Scenario 冲突：machine gate 不写领域规则，critic 负责发现与处置。

## 08 优先级与预期收益

| 优先级 | 修改 | 主要收益 |
|---|---|---|
| P0 | todo 加规格清单 | 消除本次全局编号根因 |
| P0 | 模板真实 ID 改占位符 | 避免复制即违规 |
| P0 | duplicate replacement map | 把猜号变成一次替换 |
| P0 | width 单一诊断 | 消除报错级联和错误排查方向 |
| P0 | structure gate 前置 | 避免 critic 审查必然会被机器修掉的版本 |
| P0 | source target-aware coverage | 阻止 background 来源被强制伪造为行为映射 |
| P1 | review 双摘要 | 阻止 stale PASS，纯重编号不浪费语义重审 |
| P1 | finding / decision ledger | 阻止关键证据被主 agent 擦除或伪造 |
| P1 | 可选 ID allocator | 大 feature 下进一步消除全局规划负担 |
| P2 | Modified 证据可解析 | 堵住零证据逃生通道 |
| P3 | review evidence 可点开 | 提升回检的可核查性，不扩张到语义判断 |

沿用原审计的预期评分，并结合前置 structure gate 后，可作如下目标判断：

| 维度 | 现状 | 修复后目标 | 主要来源 |
|---|---:|---:|---|
| 机器格式门禁 | 7 | 8.5 | P0 + 可选 allocator |
| 模型全局规划 | 3 | 7 | 把编号改为确定性流程，不是模型变强 |
| 审计可信度 | 2 | 7 | digest + findings / decisions ledger |
| 语义门禁 | 2 | 4.5 | 可解析证据 + 不可擦除 critic finding |
| 模型一致性推理 | 2 | 2 | 插件无法改变同源模型的推理上限 |

最后一行必须保留：critic 与主模型同源，插件只能确保问题被发现后不会蒸发、证据可核查、产物变化会触发复审，不能让模型突然获得更强的业务推理能力。

但就本轮事故而言，critic 已经发现了关键问题，机器 gate 也发现了结构问题，产物仍带着假 PASS 推进。因此当前最高性价比不是扩张语义 checker，而是修复编号反馈、执行时序、新鲜度和不可篡改审计链。这部分完全机械可判，也最值得先做。
