# autodev-specs 门禁复审（0902 会话）

被审对象：`AutobizDevOps_Plugin_Kanban` · `dev.specs`
样本：`协议授权` feature 单轮 session（117 条消息，2026-09-02），插件版本早于 `f0f5ed4`
对照样本：`autodev-specs门禁审计-合并版.md`（219 条消息，2026-08-28，收敛前）

## 结论

**整体不再严厉，但有三处「严得不对」。**

`08-30` 那轮收敛是有效的：阻断器从审计链收敛到四个形状检查后，同类任务的消息数、工具调用数、ID 重编次数都掉了一半左右。本轮 49 个模型轮次里，真正被门禁拖走的只有 8 轮。

剩下的成本不来自「规则多」，而来自三类具体缺陷：一条**没有出口又不证明任何东西**的覆盖门禁、两个**误伤真实业务文本**的正则、以及**143 个失败原因没有修复动作**。对弱模型来说，这三类的共同后果不是"多改几轮"，而是**产物被污染**——模型为了过门禁往 spec 里塞 ID，用户当场质疑（msg 113），模型自己承认是"机械映射"。

## 一、调用与修复次数

| 项 | 本轮 | 08-28 对照 |
|---|---|---|
| 消息总数 | 117 | 219 |
| 主线程模型调用 | 51（技能 49 + 追问 2） | — |
| 子代理派发 | 4（Explore 1、gate-fixer 1、critic 2） | 2 |
| 工具调用 | 64 | 109 |
| `edit_file` | 9 | 43（其中 32 次只为重编 ID） |
| 门禁执行 | 5（structure 4 + final 1） | 4 轮 |
| 门禁失败 | 2 | 3 |
| 修复轮次 | 2 | 4 |
| 用户裁定 | 1 轮 2 项 | 4 次 |

工具分布：`read_file` 21、`execute` 13、`edit_file` 9、`write_file` 7、`write_todos` 5、`task` 4、`ls` 2、`glob` 2、`request_user_input` 1。

轮次去向（49 轮）：探索与读文档 17（35%）、真正产出 spec 内容 8（16%）、门禁修复循环 8（16%）、收尾续办流程 5（10%）、返工 10（20%）。

### 返工明细（10 轮 / 13 次工具调用）

| # | 轮次 | 事件 | 成本 |
|---|---|---|---|
| 1 | 6→7 | 自写的 source-context 解析脚本判断有 bug，多跑一次调试 | 1 execute |
| 2 | 22→23 | `write_file` 被截断，改成小块重写 | 1 轮 |
| 3 | 27→28 | `write_file` 再次截断，转而派发 gate-fixer | 1 轮 |
| 4 | 29→32 | gate-fixer 只返回 `Task completed`，实际只修了一半；主代理重跑门禁、读文件、手工补 7 处 | 4 轮 + 7 edit |
| 5 | 35→36 | critic 首次返回被 hook 判为 `CRITIC_FINDINGS_BLOCK_INVALID`，整轮 critic 重跑 | 1 次完整子代理 |
| 6 | 40 | 多跑一次 structure（改动未触及契约，非必要） | 1 execute |
| 7 | 46→47 | `python3` 在 Windows 上 `Permission denied`，改 `python` | 1 execute |

其中 #5 已由 `f0f5ed4` / `2e547ba` 修复，本轮之后的版本不会再现。

## 二、三处「严得不对」

### A. `spec_source_requirement_missing`：没有出口，也不证明任何东西

单次报错列出 **112 个 ID**（`SRC-001-R001`…`SRC-004-R112`），没有 `problem` / `action` / `route`。

链路：

1. `hooks/source_context.py:602` `sync_source_context` 把 docx 快照**逐表格行**炸成 items，并反复提示"仍有 N 行 disposition=background 且无 requirements，请逐行判定"。
2. `skills/autobiz/.../references/source-context.md:29` 的示例把 `targets` 写成六个全选。上游弱模型照抄，于是 112 条版式行全部带上 `spec`。
3. `SKILL.md` 要求「`targets` 含 `spec` 的每个 `SRC-NNN-RNNN` 必须写入对应 Requirement 或 Scenario」。
4. `artifact_check.py:679` 的判定是 `referenced_source_requirement_ids(text)` —— **纯正则匹配 ID 是否出现在文本里**。

于是模型在 msg 27 直接推理出：「It likely checks that each SRC-NNN-RNNN ID string appears somewhere」，然后把 18 个 ID 拼进 REQ-103 一句话，把 94 个拼进 REQ-301/302。门禁全绿。

这些行的实际内容是版式事实（"申请书模板的 表1 第2行 为：被授权主体信息"），三套模板字段结构相同，只有可容纳协议个数不同——真正的行为差异只有一条。critic 提了 `RV-F002`，主模型判为"仅列出，不改产物"；用户在 msg 113 直接质疑，模型承认"确实偏机械映射"。

**这条门禁同时太严和太松**：太严——112 条无豁免、无粒度、无分组出口；太松——满足它只需要 ID 字符串出现。弱模型唯一理性选择就是凑。

修复方向：

- 在 `repair_registry.py` 注册 `spec_source_requirement_missing` / `_unknown`（含 `design_` / `requirements_eval_` 同族），给出确定动作与 `route`。
- 判定改为「ID 出现在 `## Source References` 映射表且该行有非空 REQ/SCN 映射」——机器可判、可追溯，且不污染 Requirement 正文。
- 上游收口：`source-context.md` 的示例改成单一 `targets`，并明确纯版式/布局行用 `disposition: background`，不产 `requirements`。这是唯一能把 112 降到个位数的地方。

### B. 占位符正则误伤真实业务文本

`artifact_check.py:119-122`：

```python
PLACEHOLDER_WORD = re.compile(r"\b(?:REQ|SCN)-NNN\b|TBD|待补充|待提供|待定|占位", re.IGNORECASE)
PLACEHOLDER_BRACKET = re.compile(r"\[(?!(?:REQ|SCN)-(?:\d+|NNN)\])(?![ xX]\])(?P<slot>[^\]\n]{1,40})\](?!\()")
```

- `占位` 是裸子串，命中业务原文「或对应模板未埋入**占位符**无法完成填充」。
- `[-X]` 命中文件名规则「`申请书-<企业名称>[-X]`」中的真实业务约定。

代价不只是一轮门禁：模型为了让正则闭嘴，把「占位符」改写成「模板填充标记」、把 `-X` 改成「数字后缀（如 -1、-2）」——**为迁就正则而改写业务术语，产物质量是净损失**。08-28 那轮已经记过同一个正则误伤 `[REQ-1001]`，根因没动。

修复方向：把 `占位` 从 `PLACEHOLDER_WORD` 去掉（`TBD/待补充/待提供/待定` 已覆盖真实残留），`PLACEHOLDER_BRACKET` 改为只匹配 `templates/spec.md` 实际出现的槽位词表，而不是任意 40 字符方括号。

### C. 143 个失败原因没有修复动作

`repair_registry.py` 注册 88 条，`fail_line` 调用点用到 194 个 reason，**143 个未注册**。未注册时 `lookup()` 返回 `None` → 不输出 `payload` → `hooks/json_writer_common.py:333` 组装的 JSON 里就没有 `problem` / `action` / `route`；`POST_SKILL_REPAIR` 行则被 JSON 解析整段丢弃。

这直接违反 AGENTS.md「编写脚本返回错误的时候，应打印错误修复方式」，而且有连带损伤：`agents/specs-gate-fixer.md` 的修复循环第 2 步是「按 `route` 分流」——对没有 `route` 的失败项无从下手。本轮 gate-fixer 修完带 route 的那条就停了，返回空结论，四轮主代理成本由此产生。

未注册的大头在 e2e / verify / utest（`invalid_e2e_*` 约 40 条），dev.specs 上主要是 source requirement 一族。

修复方向：加一条枚举式契约测试，断言所有 `fail_line` reason 都在 `REPAIRS` 里；同时给 gate-fixer 的结论块加机器校验，避免"返回 Task completed 就算完成"。

## 三、两处一行修复

| 位置 | 问题 | 修复 |
|---|---|---|
| `skills/references/ui-continuation-guide.md:38` | 全仓 124 处 `python`、2 处 `python3`，这一处每轮在 Windows 上稳定失败一次 | 改 `python` |
| `SKILL.md` 回检节 | 结构门禁通过后又多跑一次（msg 40） | 正文已写「仅契约变更才重跑」，模型仍保守重跑；可在收尾清单里点明 |

## 四、给弱模型的整体判断

不建议再削减门禁数量。四个阻断器都只判形状，本轮没有一个是"理解不了所以过不去"。真正卡住弱模型的是**确定性问题没有被转成确定性修复**：报 112 个 ID 而不说怎么改、报"残留槽位"而实际是业务术语、报错没有 route 导致修复子代理停摆。

优先级：C（补 repair 注册 + 契约测试）> A（覆盖判定改映射表 + 上游 targets 收口）> B（正则收窄）> 两处一行修复。
