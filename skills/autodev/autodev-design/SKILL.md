---
name: autodev-design
description: 基于行为规格与必要代码证据生成精简技术设计并锁定 Design 到 Plan 的契约。
version: v2.0
---

# /autodev-design — Technical Design

产物目录：`${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/`。默认由主会话生成 `design.md` 与 `.design-contract.lock.json`。

## 输入与查证

```bash
python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint design_in_progress --stage "技术设计"
```

- 读取 proposal、specs、`IMPLEMENTATION_SCOPE.json`、`UI_CONTEXT.json` 与已有设计；复用已有代码证据，只补查会影响当前技术决策的入口和必要直接依赖，不修改业务代码。
- 局部查证由主会话完成；独立检索确有价值时，向 `Explore-autodev` 提供 1–3 个具体问题、目录/入口和已有证据。证据足以支持设计即停止；未找到返回范围与未知项，不自动扩大为全仓调查。
- 只有影响行为、公开契约或实现路径的真实未决项才提问，届时读取 `${pluginPath}/skills/references/ask-user-question.md`。已确认结论直接复用，无未决项直接生成；采纳的方案和信息实体落盘后才移除未决标记。
- 新增或改变可观察行为先更新对应 Specs；上游存在缺失、冲突时解决行为问题再定稿，不在 Design 中另建行为事实源。

## 产出

使用 `templates/design.md`，保留变更与证据、技术决策、行为覆盖与验证；风险按需添加。旧设计可保留原章节，无需整体重写。

- 变更与证据写影响范围及真实代码锚点；无需独立 EVD 编号或固定证据表，不重复上游需求。
- 技术决策使用稳定 `D-NNN` 表格行（如 `D-001`），写方案与理由。接口/数据契约涉及 `API-NNN` / `DATA-NNN` 时才读取 `references/contracts.md`；无对应变更保留 `x-auto-no-http-api: true` / `x-auto-no-sql: true` 和简短原因，不生成空表。
- Spec Traceability 表用 `Requirement | Scenarios | Design Coverage | Verification` 关联本期 REQ/SCN 与真实 API/DATA/D，说明可观察的验证结果；后续 Plan 引用稳定 ID。规格决策 `DEC-NNN`（如 `DEC-001`）只引用 proposal 的 `## Decision Log`，不新增或当成技术决策。
- 仅在有实际风险时写风险、影响和应对；关键接口、数据约束和依赖未解决前不得锁定设计。

## 自检与完成

主会话一次核对行为遗漏、方案合理性、验证思路和关键代码事实。破坏性接口、数据迁移、跨仓协议等高风险变更或用户要求独立审查时，才读取 `${pluginPath}/skills/references/review-protocol-design.md`；常规任务不派发 critic、不生成固定回检报告。

```bash
python -X utf8 "${pluginPath}/hooks/design_contract_lock.py" sync --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/stage_gate.py" validate --stage dev.design --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint design_done
```

失败按返回的产物和问题修复；涉及真实取舍再问用户。产物变化才重做受影响的查证、自检和契约锁；同一问题修复后仍失败则报告阻断。`design_done` 保留平台 Human Gate，不得自行增加文本确认或绕过该命令。简报产物与验证结果，再按 `${pluginPath}/skills/references/ui-continuation-guide.md` 处理阶段完成。
