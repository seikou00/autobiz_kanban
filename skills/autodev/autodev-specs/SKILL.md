---
name: autodev-specs
description: 将需求转为简短 proposal 与按可独立验收能力组织的行为规格，供设计、计划和验收使用。
version: v2.0
---

# /autodev-specs — Behavior Specs

产物目录：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/`。默认在主会话完成。

## 输入与查证

- 读取 `PRD.md`（缺失用用户需求）、已有 proposal/specs、`IMPLEMENTATION_SCOPE.json` 和 `UI_CONTEXT.json`。实现范围缺失按 `full_stack`；`backend_only` / `frontend_only` 只描述本期对应端的行为。
- 需求以用户与 PRD 为依据。只为已有行为、冲突或入口等具体问题查代码，复用已有证据；得到当前规格所需事实即停止，不要求每条未来需求都有源码证据。
- 局部查证直接完成；有独立检索价值时才向 `Explore-autodev` 提供 1–3 个问题、相关目录/入口和已有证据。未找到时保留搜索范围与未知项，仅为阻断项继续查证。
- 只澄清影响行为、范围或验收的真实未决项，提问时读取 `${pluginPath}/skills/references/ask-user-question.md`；已确认内容直接复用，无未决项直接生成。新对齐术语按 `${pluginPath}/skills/references/domain-context.md` 回写 `CONTEXT.md`。
- 有外部资料时查看 `source-context.json` 索引，只打开约束本期行为的资料/快照；已保存快照视为已提供，不重复索取。

```bash
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_in_progress
```

## 产出

按 `templates/proposal.md` 与 `templates/spec.md` 生成：

- proposal 保留 Why、What Changes、Capabilities、Impact；Capabilities 用 `- capability-name: 说明` 与 `specs/<capability>/spec.md` 对应，不复述 PRD。按可独立验收的行为划分能力。
- 每条行为用 `### Requirement REQ-NNN: 标题`，其场景用 `#### Scenario SCN-NNN: 标题` 和 WHEN/THEN。REQ/SCN 在 Feature 内分别全局唯一，三位编号，已有 ID 不重排、不复用；每条 Requirement 至少一个 Scenario。
- 覆盖主流程及有意义的异常、权限分支；修改写完整新行为，移除说明旧入口的预期结果。不写实现步骤、类名、SQL 或任务拆分。
- 只有约束行为的外部资料才添加 `Source References / 外部资料引用` 表：`Source ID | Requirement / Scenario | Usage`，将真实 `SRC-NNN` 映射到 REQ/SCN，供 Plan 投影。必要的规格取舍可在 proposal 的 `## Decision Log` 定义 `### DEC-NNN: 标题`；已有 DEC 引用保持稳定。
- `UI_CONTEXT.json` 由 `ui_context_writer.py` 维护；仅在需要生成/修改时读取 `${pluginPath}/skills/autobiz/references/ui-context.md`。UI 范围来自已确认事实，有 UI 时 capability 回链真实 REQ/SCN，仅有真实高保真输入才绑定 VIS；无 UI 时填写原因。完成时锁定为 `decisionStatus=locked`、`lockedAtCheckpoint=specs_done`。

## 完成

主会话一次自检需求遗漏、行为矛盾、实现范围和必要来源约束；无问题不生成回检报告。

```bash
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint specs_done
```

更新器校验产物并重建 Plan 前的 `UI_CONTEXT.md`；失败按结构化问题修复，真实取舍回到用户，不补空表或手改状态。同一问题修复后仍失败则报告阻断。简报产物与验证结果，再按 `${pluginPath}/skills/references/ui-continuation-guide.md` 处理阶段完成。
