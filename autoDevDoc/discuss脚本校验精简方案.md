# discuss 阶段脚本校验精简（不改技能文本）— 方案与实施结果

- 前提：`skills/**/*.md` 技能文本经人工反复调试，视为既定事实源，本次零改动。
- 背景数据见 [discuss技能模型调用量化分析.md](discuss技能模型调用量化分析.md)。
- 结果：`biz_validate.py prd` 的阻断断言 **55 → 27**，其中面向模型的产物结构类 **52 → 24**，新增 21 条不阻断的提示。

---

## 一、判定原则

一条检查只有同时满足三条才配当阻断项，否则删除、降为 warning、或改由脚本自动生成：

1. **有技能文本依据**——模型能从 `SKILL.md` / `references/*.md` 推导出来。推导不出来 = 模型必须读 `.py` 源码，违反 AGENTS.md。
2. **下游会崩**——被 `artifact_ref_validator.py` / `code_task_context.py` / `artifact_check.py` 真实消费。
3. **模型能做**——逐字原文、行覆盖、SHA256、ID 编号属于机械推导，由脚本生成。

---

## 二、删除的检查（无技能文本依据 / 与技能正面冲突）

| 位置 | 原检查 | 删除理由 |
|------|-------|---------|
| `prd_rules.FORMAL_PRD_TITLE` | 首行必须是 `# 需求正式稿` | 技能无此要求，模板首行是 `# 需求摘要`；下游按标题名定位章节，**从不读首行** |
| `prd_rules.REQUIRED_PRD_SECTIONS` 前 4 项 | 必须有 `用户故事/验收口径/验收标准/关键约束` | 全仓库只在 `prd_rules.py` 与 `tests/` 出现；且 `prd-formatter.md:41` 的二选一规则让「选流程图」必然失败 |
| `prd_rules.DISCUSSION_SECTION_TITLES` | 禁止 `历次讨论记录` | `SKILL.md` §讨论沉淀生成第 6 条**要求写这一章** |
| `prd_rules.FORBIDDEN_PRD_SECTION_TITLES` | 禁止 `待确认事项` 等 5 个标题 | `待确认事项` 是第 4 条要求的章节；该规则形成**「删掉章节就能过门禁」**的反向激励 |
| `source_context` 逐字定位 | `original 无法在快照中定位` | 逐字转录改由 `sync` 生成 |
| `source_context` 行覆盖 | `未登记表格/字段行` | 本次 112 个 item，模型只能自写生成器绕过；改由 `sync` 生成 |

这些常量在仓库内没有其他消费者，删除不影响任何下游阶段。

## 三、降为 warning 的检查

- `source_context`：`name`、`availability`/`readStatus`/`freshness` 枚举、`readStatus != complete`、`sha256` 格式、`path` 位置、`items` 非空、item `id`/`location`/`original`、`disposition` 枚举与互斥规则、`replacedBy`、`never_provided` —— 这些字段下游只做透传渲染，`source_requirement_index()` 之外的消费者从不基于它们分支。
- `source_references`：非「外部接口」行的 6 字段完整性。技能文本只对 `类型=外部接口/第三方接口` 提出字段完整性要求。
- `_check_done_checkpoint`：见 §4.1。
- 新增一条：某个来源的全部 item 都停留在 `disposition=background` 且无 `requirements` 时提示"逐行判定仍需完成"，堵住「sync 完就交差」的口子。

## 四、四项脚本改造

### 4.1 `--draft`：打断"先声明完成才能验证完成"

`_check_done_checkpoint()` 从 error 降为 warning；新增 `--draft` 连该提示一起跳过。模型可在产物成型的任意时刻自检，返工范围从"整篇"降到"一节"。技能正文里的原命令保持可用。

### 4.2 每条 error 补齐"修复：…"

统一格式 `<错误事实>；修复：<可执行动作>`。硬性标准：**模型不打开任何 `.py` 文件就能改对**。`tests/test_biz_validate_prd.py::test_every_error_carries_a_repair_hint` 把这条锁进测试。

### 4.3 `source_context.py sync`：机械字段交给脚本

```bash
python "${pluginPath}/hooks/source_context.py" sync --feature-dir "<Feature 目录>" [--source SRC-001]
```

1. 从 `PRD.md` 的「外部资料与实现约束」表读出全部 `SRC-NNN`；
2. 定位 `sources/SRC-NNN/` 下的快照，写入 `path` / `sha256` / `readStatus`；
3. 逐行生成 `items[]`——`id`（`SRC-NNN-Innn`）、`location`、`original` 全部由脚本填；
4. 按 `location` 匹配**保留模型已填的 `disposition` / `requirements` / `replacedBy`**，新行缺省 `background`；
5. 打印新增 / 保留 / 待判定行数。

模型只剩两个判断动作：给每行标 `disposition`，给 requirement 行写 `text` + `targets`。

### 4.4 拆分 `validate_source_context_refs()`：阻止严格度泄漏到下游

`resolve_source_requirement_refs()`（Code 阶段）、`validate_plan_source_coverage()`（Plan 阶段）、`artifact_check.py`（Specs / Reviewer / E2E）此前都**无条件调用完整的 38 条校验**——discuss 阶段的严格度会连带阻断下游。现在：

- `validate_source_context(feature_dir, ids) -> (errors, warnings)`：discuss 阶段用。
- `validate_source_context_refs(feature_dir, ids) -> errors`：下游门禁用，只保留引用完整性，丢弃 warning。

---

## 五、实施结果

### 5.1 阻断断言实测

| 模块 | 改前 | 改后（阻断） | 改后（warning） |
|------|-----|------------|----------------|
| `biz_validate.validate_prd` | 6 | 3 | — |
| `_check_done_checkpoint` | 1 | 0 | 1 |
| `_implementation_scope_errors` | 1 | 1 | — |
| `source_references` 来源表 | 6 | 5 | 1 |
| `source_context` | 38 | 15 | 19 |
| state.json 基础设施 | 3 | 3 | — |
| **合计** | **55** | **27** | **21** |

面向模型的产物结构类断言 **52 → 24**。`source_context` 保留的 15 条中，13 条是模型需要满足的引用完整性规则（source / requirement 的 ID 格式与唯一性、`text`、`targets` 非空与枚举、PRD ↔ json 集合一致），2 条是 JSON 结构防御。

### 5.2 端到端行为验证

```
$ biz_validate.py prd --feature 协议授权 --draft        # PRD 有 SRC 但无 source-context.json
[未通过] - PRD 登记了外部资料，必须生成 source-context.json；修复：运行 source_context.py sync --feature-dir <Feature 目录>

$ source_context.py sync --feature-dir <Feature 目录>
SRC-001: 共 4 行（新增 4，保留判定 0），待判定 4 行
已写入 .../source-context.json。下一步：为每行填写 disposition；标为 requirement 的行补 requirements[].text 与 targets

$ biz_validate.py prd --feature 协议授权 --draft
[通过]  提示（不阻断，共 1 条）:
        ~ SRC-001 的 4 行全部停留在 disposition=background 且无 requirements；sync 只生成原文，逐行判定仍需完成

$ biz_validate.py prd --feature 协议授权              # checkpoint 仍是 prd_in_progress
[通过]  提示（不阻断，共 1 条）:
        ~ checkpoint 当前为 prd_in_progress，尚未到 prd_done；修复：产物定稿后运行 update_checkpoint.py --checkpoint prd_done
```

技能正文按原样产出的 PRD（`# 需求摘要` + 讨论沉淀八章节，含 `待确认事项` 与 `历次讨论记录`）现在直接通过。`【待确认】` 残留仍阻断，报错带行号并明确「不得靠删除整段待确认内容通过校验」。

### 5.3 技能文本零改动下的 `sync` 发现路径

技能正文没有、也不需要提到 `sync`。模型只会在三条报错/提示里遇到它，每条都带完整命令：

- `PRD 登记了外部资料，必须生成 source-context.json`
- `SRC-NNN.items 为空`
- `SRC-NNN.sha256 缺失或格式不对`

### 5.4 改动清单

| 文件 | 动作 |
|------|-----|
| `skills/autobiz/hooks/prd_rules.py` | 删 3 组常量；`REQUIRED_PRD_SECTIONS` 只留 `外部资料与实现约束`；新增 `pending_marker_lines()` |
| `skills/autobiz/hooks/biz_validate.py` | 删首行/讨论标题/禁用标题三段检查；`validate_prd` 增 `draft` 参数并返回 `warnings`；`main()` 增 `--draft` 与分层输出；全部 error 补修复指引 |
| `hooks/source_context.py` | `validate_source_context` 返回 `(errors, warnings)`；新增 `validate_source_context_refs()`；新增 `sync` 子命令与 `_snapshot_for_source()` / `_sync_items()` |
| `hooks/source_references.py` | 新增 `split_source_reference_section()`；非接口行字段完整性降 warning |
| `hooks/artifact_ref_validator.py` | `validate_plan_source_coverage` 改调 refs 版 |
| `skills/autodev/hooks/artifact_check.py` | 2 处下游门禁改调 refs 版 |
| `tests/test_biz_validate_prd.py` | fixture 换成技能正文真实产出结构；删 D 类用例；新增 8 个用例 |
| `tests/test_source_context.py` | 元组断言；行覆盖/逐字原文改为「不再阻断」；新增 `sync` 幂等性用例 |

### 5.5 测试

`1022 passed`。剩余 5 failed / 3 errors（`test_render_*`、`test_task_runner`、`test_code_stage_iterative_editing`）在改动前的干净 HEAD 上完全相同，与本次改动无关。

## 六、遗留

- `validate_source_context_refs()` 有意丢弃 warning，避免 discuss 阶段的严格度泄漏到 Plan / Code；warning 目前只在 `biz_validate.py` 打印。
- `sync` 依赖 `_read_snapshot()`：docx / xlsx / csv / tsv / md 能解析出表格行；pdf 与其他格式只出全文，此时 `items` 保留原样并提示人工登记。
