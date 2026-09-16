---
name: autodev-specs
description: 将需求转为 proposal 与按能力拆分的行为规格，供后续设计、计划和验收使用。
version: v1.17.0916
---

# /autodev-specs — Proposal + Behavior Specs

本阶段回答为什么做、系统应表现为什么行为，只生成 `proposal.md` 和 `specs/<capability>/spec.md`。技术方案与 `design.md` 由后续 `/autodev-design` 负责，不编写业务代码、测试、配置或迁移脚本。

产物目录：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/`。

## 读取与澄清

- 读取 `PRD.md`、已有 proposal/specs 和相关代码、接口、测试。没有 PRD 时以用户需求为输入。
- 存在 `source-context.json` 时，读取 `targets` 含 `spec` 的要求及对应 `sources/SRC-NNN/` 快照；已保存的资料不重复索取。
- 读取 `IMPLEMENTATION_SCOPE.json`：`backend_only` 只描述后端可实现、可验证的行为；`frontend_only` 只描述前端行为，后端作为外部依赖；缺失时兼容为 `full_stack`。
- 仅澄清影响行为、范围或验收结果的缺口与矛盾，按 `${pluginPath}/skills/references/ask-user-question.md` 提问。复用用户已确认的信息，不重复确认；不能把未确认的关键行为写成定论。
- 对齐后的领域术语按 `${pluginPath}/skills/references/domain-context.md` 回写会话工作区 `CONTEXT.md`。

开始生成时更新状态：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_in_progress
```

## 生成 proposal 与 specs

先确定 capability 清单，再按 [proposal 模板](templates/proposal.md) 和 [spec 模板](templates/spec.md) 生成产物。

- proposal 默认写 Why、What Changes、Capabilities、Impact；影响面基于实际调研。Capabilities 使用 `- capability-name: 说明` 列表，名称为 kebab-case，与 `specs/<capability>/spec.md` 双向对应。
- 非目标、关键取舍、问题记录和来源引用按需添加，没有内容直接省略。关键行为决定落实到 Requirement/Scenario；需要供设计引用的取舍可在 `## Decision Log` 下使用 `### DEC-NNN: 标题`，已有 DEC 引用保持稳定。
- 每个 capability 一个 spec，只描述可验证的外部行为，不写实现步骤、类名、SQL 或任务拆分。
- Requirement 使用 `### Requirement REQ-NNN: 标题`；其所属 Scenario 使用 `#### Scenario SCN-NNN: 标题`，说明 WHEN 触发条件和 THEN 预期结果。ID 外允许方括号。
- REQ/SCN 使用三位数字，在同一 feature 内分别全局唯一；新编号使用未占用值，既有编号不重排、不复用。
- 每条 Requirement 至少一个 Scenario。覆盖需求涉及的角色、主流程及有意义的异常和权限分支；相同行为不为不同角色重复抄写。
- `ADDED / MODIFIED / REMOVED Requirements` 章节按需使用，无内容省略。修改写完整的新行为；移除描述旧入口触发时的预期响应，必要时说明原因及兼容方式，不要求固定字段。
- 来源可以用简短说明、链接或按需添加的映射表记录。应消费相关资料中的行为约束，不为凑表重复需求。

## UI 上下文

保留 `UI_CONTEXT.json` 作为前端范围事实源，按 `${pluginPath}/skills/autobiz/references/ui-context.md` 维护。缺失时依据已确认范围补齐，不从关键词猜测前端范围。

- 生成或修改统一使用 `${pluginPath}/hooks/ui_context_writer.py`。
- `uiRequired=true` 时至少有一个独立 UI capability，其 `specRefs` 回链真实 REQ/SCN；有高保真输入才绑定真实 VIS，无则 `visualSourceRefs=[]`。
- `uiRequired=false` 时不生成 UI capability，并填写 `notApplicableReason`。
- specs 完成时锁定 UI 决策：`decisionStatus=locked`、`lockedAtCheckpoint=specs_done`。

## 检查与完成

按 `${pluginPath}/skills/references/review-protocol-specs.md` 核对需求覆盖、行为一致性和实现范围，修正明确遗漏；需要用户取舍的事项先澄清。无需单独生成回检报告。

提交完成状态时会自动执行产物校验，检查能力对应、REQ/SCN 及场景归属、UI 和范围契约：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
```

校验失败时按返回的产物与问题修复后重试；需要行为裁定的先问用户，不通过补空表或改状态词绕过问题。连续出现相同失败时报告具体阻塞，不重复空转。

完成后简要汇报能力与产物位置，并按 `${pluginPath}/skills/references/ui-continuation-guide.md` 衔接下一阶段。
