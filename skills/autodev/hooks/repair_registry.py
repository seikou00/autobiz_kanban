#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物契约预检的修复信息注册表。

每个 reason 一条记录，回答四个问题：哪个产物、定位到哪、什么问题、怎么改。
放在注册表而不是散在 142 个 ``fail_line`` 调用点，是为了让「新增一个失败但
没写修复动作」能被枚举式契约测试直接抓住——调用点只负责传 ``target``。

``problem`` / ``action`` 用 ``str.format_map`` 渲染，可用占位符：
``{target}`` 以及调用点通过 ``fields=`` 传入的任意键；缺失的键渲染成空串，
预检不能因为文案参数缺失而崩掉。
"""

from __future__ import annotations

from typing import Dict, NamedTuple


# route 取值闭集。语义见 skills/references/review-protocol.md 的映射表。
ROUTE_FIX_CURRENT = "fix_current"      # 在当前阶段按 action 修
ROUTE_RETURN_SPECS = "return_specs"    # 停止当前阶段，回 dev.specs
ROUTE_RETURN_PLAN = "return_plan"      # 停止当前阶段，回 dev.plan
ROUTE_ASK_USER = "ask_user"            # 必须回到用户确认，禁止自行填值

ROUTES = (ROUTE_FIX_CURRENT, ROUTE_RETURN_SPECS, ROUTE_RETURN_PLAN, ROUTE_ASK_USER)


# plan 机器产物的编辑禁令。只在 plan.json / PLAN.md 相关错误里输出，
# 不在技能正文里重复枚举。
PLAN_NO_HAND_EDIT = (
    "禁止直接编辑 plan.json / plans/Bxxx/plan.json / PLAN.md（PLAN.md 是投影视图）。"
)


class Repair(NamedTuple):
    artifact: str
    problem: str
    action: str
    route: str = ROUTE_FIX_CURRENT


class _BlankDict(dict):
    def __missing__(self, key: str) -> str:  # pragma: no cover - 平凡分支
        return ""


def render(template: str, target: str, fields: Dict[str, str] | None = None) -> str:
    values = _BlankDict(fields or {})
    values.setdefault("target", target)
    try:
        return template.format_map(values)
    except (IndexError, ValueError):
        # 文案里出现了不合法的花括号；宁可原样输出也不要让预检挂掉。
        return template


# --------------------------------------------------------------------------
# 入场 / 早退路径（run_postcheck 在跑 validator 之前就可能失败）
# --------------------------------------------------------------------------

_ENTRY: Dict[str, Repair] = {
    "missing_required_artifacts": Repair(
        artifact="{target}",
        problem="本阶段的必备产物缺失或为空：{target}",
        action=(
            "先把缺失产物生成出来再重跑预检。proposal.md / specs/<capability>/spec.md 由 "
            "/autodev-specs 生成；design.md 由 /autodev-plan 生成；plan.json 与 PLAN.md 一律"
            "通过 hooks/plan_writer.py 生成，" + PLAN_NO_HAND_EDIT
        ),
    ),
    "missing_feature_dir": Repair(
        artifact="{target}",
        problem="Feature 目录不存在：{target}",
        action="确认 --feature / FEATURE_ID 与看板上的 Feature 一致；不一致时用正确的 feature 重跑，不要手工创建目录。",
    ),
    "invalid_board_config": Repair(
        artifact="board_core/board_config.json",
        problem="工作流契约加载失败：{target}",
        action="这是配置问题不是产物问题：修正 board_config.json 后重跑；不要为了绕过它改产物。",
    ),
    "unknown_validator": Repair(
        artifact="board_core/board_config.json",
        problem="节点声明了未注册的 validator：{target}",
        action="在 skills/autodev/hooks/artifact_check.py 的 VALIDATORS 中注册该 validator，或从 board_config.json 移除该声明。",
    ),
}


# --------------------------------------------------------------------------
# dev.specs
# --------------------------------------------------------------------------

_SPECS: Dict[str, Repair] = {
    "missing_proposal": Repair(
        artifact="proposal.md",
        problem="proposal.md 不存在或为空",
        action="生成 proposal.md，必备章节为 Why / What Changes / Capabilities / Impact / Out of Scope / Decision Log / Open Questions。",
    ),
    "missing_specs": Repair(
        artifact="specs/**/spec.md",
        problem="specs/ 下没有任何非空 spec.md",
        action="至少生成一个 specs/<capability>/spec.md，capability 名取自 proposal.md 的「## Capabilities」节。",
    ),
    "invalid_proposal_missing_section": Repair(
        artifact="proposal.md",
        problem="proposal.md 缺少必备章节「{target}」",
        action="在 proposal.md 补齐「## {target}」节；Open Questions 无待确认项时正文写「无」。",
    ),
    "artifact_template_guidance_residue": Repair(
        artifact="{target}",
        problem="{target} 里残留了模板的写作指引或占位符，不是真实内容",
        action="删掉模板指引行与 [占位符]，换成本 Feature 的真实内容；整段无内容时写「无」，不要保留模板示例。",
    ),
    "invalid_implementation_scope": Repair(
        artifact=".autobizdevops/features/<feature>/implementation-scope.json",
        problem="实现范围声明不合法：{target}",
        action="使用 hooks/implementation_scope.py set 写入 full_stack、backend_only 或 frontend_only，并确保 featureId 与当前 Feature 一致。",
    ),
    "invalid_spec_missing_operation_header": Repair(
        artifact="{target}",
        problem="{target} 没有任何操作段标题",
        action="补上「## ADDED Requirements」/「## MODIFIED Requirements」/「## REMOVED Requirements」中至少一个，并把 Requirement 放到对应段下。",
    ),
    "invalid_spec_missing_requirement": Repair(
        artifact="{target}",
        problem="{target} 没有任何合法 Requirement",
        action="按「### Requirement [REQ-NNN]: <标题>」写出 Requirement（NNN 三位数字，方括号不能省）。",
    ),
    "invalid_spec_missing_scenario": Repair(
        artifact="{target}",
        problem="{target} 没有任何合法 Scenario",
        action="按「#### Scenario [SCN-NNN]: <标题>」补 Scenario，并归属到本文件已有的 Requirement 下。",
    ),
    "spec_contract_heading_malformed": Repair(
        artifact="{target}",
        problem="{target} 中的契约标题写法不规范，索引器读不到：{headings}",
        action=(
            "把报错的标题改成规范写法：「### Requirement [REQ-NNN]: <标题>」/"
            "「#### Scenario [SCN-NNN]: <标题>」。NNN 是三位数字，方括号和层级都不能省——"
            "索引器只认这一种写法，其余写法会被静默跳过，该 Requirement 对下游覆盖检查等于不存在。"
        ),
    ),
    "spec_requirement_without_scenario": Repair(
        artifact="{target}",
        problem="{target} 中这些 Requirement 自身块内没有 Scenario：{requirements}",
        action=(
            "为报错的每个 Requirement 补至少一个「#### Scenario [SCN-NNN]: <标题>」；"
            "REMOVED Requirement 用 Scenario 描述旧入口被触发时的期望响应。"
        ),
    ),
    "spec_scenario_without_requirement": Repair(
        artifact="{target}",
        problem="{target} 中这些 Scenario 不归属任何 Requirement：{scenarios}",
        action=(
            "把报错的每个 Scenario 移到它所属的「### Requirement [REQ-NNN]:」标题之下；"
            "Scenario 出现在首个 Requirement 之前或操作段标题正下方时不归属任何 Requirement。"
        ),
    ),
    "spec_id_out_of_order": Repair(
        artifact="{target}",
        problem="{target} 中这些 REQ/SCN 编号没有按文档顺序递增：{ids}",
        action=(
            "按文档顺序重排 REQ/SCN 编号，使其数值递增。"
            "允许跳号（删除后 ID 不复用会留下空档），但后出现的编号不得小于先出现的。"
        ),
    ),
    "removed_requirement_missing_field": Repair(
        artifact="{target}",
        problem="{target} 的 REMOVED Requirement 缺字段：{fields}",
        action=(
            "为「## REMOVED Requirements」下报错的 Requirement 补齐"
            "「**Reason:** <移除原因>」与「**Migration:** <迁移方式>」，写实际内容而非占位符。"
        ),
    ),
    "spec_placeholder_residue": Repair(
        artifact="{target}",
        problem="{target} 中残留模板槽位：{placeholders}",
        action=(
            "把报错的模板槽位替换成实际内容。"
            "`[REQ-NNN]` / `[SCN-NNN]` 是 ID 语法不算槽位，Markdown 链接也不算。"
        ),
    ),
    "spec_source_reference_missing": Repair(
        artifact="specs/**/spec.md",
        problem="PRD 外部资料索引中的这些来源未被任何 spec 保留：{target}",
        action=(
            "在相关 spec 的 `## Source References / 外部资料引用` 表补齐 SRC-NNN 与 REQ/SCN 映射；"
            "会改变外部可观察行为的约束还必须写入 Requirement/Scenario，纯实现约束注明不扩写行为。"
        ),
    ),
    "spec_source_reference_unknown": Repair(
        artifact="specs/**/spec.md",
        problem="spec 引用了 PRD 外部资料索引中不存在的来源：{target}",
        action="修正或移除这些 SRC-NNN；确有新资料时先回 PRD 登记稳定 ID，再重新生成 specs。",
    ),
    "spec_source_reference_incomplete": Repair(
        artifact="specs/**/spec.md",
        problem="这些来源引用缺少 Requirement/Scenario 映射或 Usage：{target}",
        action="在 Source References 表补齐每个 SRC-NNN 对应的 REQ/SCN 和实际用途；只写来源 ID 不算跨阶段传递。",
    ),
    "duplicate_spec_id_across_specs": Repair(
        artifact="specs/**/spec.md",
        problem="{target} 在多个 spec 中重复定义：{files}",
        action=(
            "ID 在同一 feature 内必须全局唯一——覆盖检查按扁平 ID 集合判定，"
            "重号会让覆盖其中一个就算覆盖全部。给其中一处换一个未使用的编号，"
            "并同步所有引用它的地方。"
        ),
    ),
    "duplicate_requirement_id": Repair(
        artifact="{target}",
        problem="{target} 中存在重复的 REQ 编号",
        action="REQ 编号在同一 Feature 内全局唯一：把重复项改成未使用的新编号；已删除的编号不复用。",
    ),
    "duplicate_scenario_id": Repair(
        artifact="{target}",
        problem="{target} 中存在重复的 SCN 编号",
        action="SCN 编号在同一 Feature 内全局唯一：把重复项改成未使用的新编号；已删除的编号不复用。",
    ),
    "proposal_capability_missing_spec": Repair(
        artifact="specs/**/spec.md",
        problem="proposal.md 列出的这些 capability 没有对应 spec：{target}",
        action=(
            "为报错的每个 capability 生成 specs/<capability>/spec.md；"
            "若该 capability 不该单独成 spec，回 proposal.md 将其移除或并入其他 capability。"
        ),
    ),
    "spec_missing_proposal_capability": Repair(
        artifact="proposal.md",
        problem="这些 spec 目录在 proposal.md 的 Capabilities 中没有出处：{target}",
        action=(
            "把报错的每个 capability 按 New / Modified / Removed 补进 proposal.md 的「## Capabilities」节；"
            "若该 spec 不属于本轮范围，删除对应 specs/<capability>/ 目录。"
        ),
    ),
    "capability_operation_missing": Repair(
        artifact="specs/{target}/spec.md",
        problem="capability {target} 在 proposal 中声明为 {group}，但 spec 的「## {expected} Requirements」段下没有 Requirement",
        action=(
            "在 specs/{target}/spec.md 的「## {expected} Requirements」段下写出 Requirement；"
            "若该能力实际不是 {group}，改 proposal.md 把它挪到正确的分组。"
        ),
    ),
    "capability_operation_contradicts_new": Repair(
        artifact="specs/{target}/spec.md",
        problem="capability {target} 声明为 New，却在 {operations} 段下写了 Requirement",
        action=(
            "specs/{target}/spec.md 声明为 New，不该有存量需求可改可删："
            "把 {operations} 段下的 Requirement 移到「## ADDED Requirements」（段标题可以保留，留空即可）；"
            "若该能力实际是在改存量，改 proposal.md 把它挪到 Modified / Removed 组。"
        ),
    ),
}


# --------------------------------------------------------------------------
# dev.plan — design.md
# --------------------------------------------------------------------------

_DESIGN: Dict[str, Repair] = {
    "missing_design": Repair(
        artifact="design.md",
        problem="design.md 不存在或为空",
        action="生成 design.md，必备章节为 Context / 输入上下文、Code Evidence、Spec Traceability、API Decisions、Data Decisions、Technical Design、Risks / Open Questions。",
    ),
    "invalid_design_contract": Repair(
        artifact="design.md",
        problem="design.md 无法作为设计契约读取：{detail}",
        action="修复 design.md 的编码或读取错误，确保文件为有效 UTF-8 文本，再重新生成 Plan。",
    ),
    "invalid_design_missing_section": Repair(
        artifact="design.md",
        problem="design.md 缺少必备章节「{target}」",
        action="在 design.md 补齐「{target}」节，写本 Feature 的真实内容；无内容时写「无」并说明理由。",
    ),
    "missing_design_api_marker": Repair(
        artifact="design.md",
        problem="design.md 缺少 x-auto-no-http-api 标记",
        action="在 API Decisions 节写入 `x-auto-no-http-api: true|false`：本轮不涉及 HTTP 接口写 true，涉及写 false 并列出 API-NNN 定义。",
    ),
    "missing_design_data_marker": Repair(
        artifact="design.md",
        problem="design.md 缺少 x-auto-no-sql 标记",
        action="在 Data Decisions 节写入 `x-auto-no-sql: true|false`：本轮不涉及数据结构变更写 true，涉及写 false 并列出 DATA-NNN 定义。",
    ),
    "design_api_marker_conflicts_with_definitions": Repair(
        artifact="design.md",
        problem="x-auto-no-http-api=true 与已有 API 定义冲突：{detail}",
        action="若本轮存在 HTTP API，将 x-auto-no-http-api 改为 false；否则删除不属于本轮的 API-NNN 定义。",
    ),
    "design_data_marker_conflicts_with_definitions": Repair(
        artifact="design.md",
        problem="x-auto-no-sql=true 与已有 DATA 定义冲突：{detail}",
        action="若本轮存在数据结构变更，将 x-auto-no-sql 改为 false；否则删除不属于本轮的 DATA-NNN 定义。",
    ),
    "design_has_pending_cells": Repair(
        artifact="design.md",
        problem="design.md 仍有待确认单元格（{target}）",
        action="这些内容影响行为且没有依据，禁止自行填值：把每一处待确认整理成问题回到用户确认，拿到答复后再写入 design.md。",
        route=ROUTE_ASK_USER,
    ),
    "design_decision_ref_unresolved": Repair(
        artifact="design.md",
        problem="design.md 引用的 DEC 编号在 proposal.md 的「## Decision Log」节内不存在：{target}",
        action=(
            "在 proposal.md 的「## Decision Log」节下补上「### DEC-NNN: <标题>」，"
            "或把该单元格改成实际存在的编号／「无」。只认该节内的定义，写在 proposal 别处不算。"
            "技术决策用 Design Coverage 列的 D-NNN，不要写进 Decision 列。"
        ),
    ),
    "design_source_reference_missing": Repair(
        artifact="design.md",
        problem="design.md 的 External Source Coverage 未覆盖 PRD 来源：{target}",
        action="逐项打开 PRD 登记的原始地址/路径，在 External Source Coverage 补齐设计覆盖与消费证据；资料不可访问时停止 Plan，不得假装已消费。",
    ),
    "design_source_reference_unknown": Repair(
        artifact="design.md",
        problem="design.md 的 External Source Coverage 引用了 PRD 未定义的来源：{target}",
        action="修正或移除这些 SRC-NNN；新资料必须先回 PRD 登记稳定 ID，不能由 design 私自创建来源编号。",
    ),
    "design_external_interface_api_reference_missing": Repair(
        artifact="design.md",
        problem="这些 PRD 外部接口来源没有关联到 API Decisions：{target}",
        action="在 API Decisions 的 Source Refs 列关联对应 SRC-NNN，并按原接口资料核对 method/path、鉴权、请求响应、错误和超时。",
    ),
    "design_source_consumption_evidence_missing": Repair(
        artifact="design.md",
        problem="这些来源只有 ID，没有完整的关联需求、设计覆盖或原件消费证据：{target}",
        action="打开每个 SRC-NNN 原件，在 External Source Coverage 补齐关联 REQ/SCN、API/DATA/D 设计项与可核对的地址/版本/契约事实。",
    ),
    "design_source_consumption_blocked": Repair(
        artifact="design.md",
        problem="这些来源仍处于阻断或不可访问状态：{target}",
        action="停止 Plan；取得可访问原件或由用户移除该实现依赖后，重新完成 External Source Coverage。",
        route=ROUTE_ASK_USER,
    ),
    "duplicate_design_api_id": Repair(
        artifact="design.md",
        problem="design.md 中存在重复的 API 编号",
        action="API 编号在同一 Feature 内唯一：把重复项改成未使用的新编号，并同步 plan 中引用它的任务。",
    ),
    "duplicate_design_data_id": Repair(
        artifact="design.md",
        problem="design.md 中存在重复的 DATA 编号",
        action="DATA 编号在同一 Feature 内唯一：把重复项改成未使用的新编号，并同步 plan 中引用它的任务。",
    ),
    "duplicate_design_decision_id": Repair(
        artifact="design.md",
        problem="design.md 中存在重复的 D（技术决策）编号",
        action="D 编号在同一 Feature 内唯一：把重复项改成未使用的新编号，并同步 plan 中引用它的任务。",
    ),
}


# --------------------------------------------------------------------------
# dev.plan — plan.json / 引用 / 覆盖 / 粒度
# --------------------------------------------------------------------------

_PLAN: Dict[str, Repair] = {
    "missing_plan_json": Repair(
        artifact="plan.json",
        problem="plan.json 不存在或为空{target}",
        action="plan.json 是任务 DAG 的机器事实源，用 hooks/plan_writer.py 生成。" + PLAN_NO_HAND_EDIT,
    ),
    "invalid_plan_json": Repair(
        artifact="plan.json",
        problem="plan.json 结构不合法：{target}",
        action="用 hooks/plan_writer.py 对应子命令重建，不要就地改字段。" + PLAN_NO_HAND_EDIT,
    ),
    "plan_task_set_not_finalized": Repair(
        artifact="plan.json",
        problem="任务集尚未定稿（taskSetStatus != finalized）",
        action="确认任务分组表已定稿后，用 hooks/plan_writer.py finalize-task-set 定稿。" + PLAN_NO_HAND_EDIT,
    ),
    "plan_implementation_scope_mismatch": Repair(
        artifact="plan.json",
        problem="plan.json 的 implementationScope 与 Feature 声明的实现范围不一致：{target}",
        action="回到 /autodev-plan 重新生成 plan.json，禁止手工修改 uiRequired 绕过范围门禁。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_design_api_id": Repair(
        artifact="design.md",
        problem="design.md 的 API Decisions 没有可引用的 API 编号（{target}）",
        action="在 design.md 的 API Decisions 节补出 API-NNN 定义；本轮确实无 HTTP 接口时把 x-auto-no-http-api 置 true。",
    ),
    "missing_design_data_id": Repair(
        artifact="design.md",
        problem="design.md 的 Data Decisions 没有可引用的 DATA 编号（{target}）",
        action="在 design.md 的 Data Decisions 节补出 DATA-NNN 定义；本轮确实无数据结构变更时把 x-auto-no-sql 置 true。",
    ),
    "missing_plan_json_requirement_ref": Repair(
        artifact="plan.json",
        problem="{target} 没有引用任何 Requirement",
        action="回到任务分组表补上该任务实际实现的 REQ，再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_json_scenario_ref": Repair(
        artifact="plan.json",
        problem="{target} 没有引用任何 Scenario",
        action="回到任务分组表补上该任务实际实现的 SCN（逐条展开、全限定），再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_json_decision_ref": Repair(
        artifact="plan.json",
        problem="{target} 没有引用任何设计依据（API/DATA/D）",
        action="补上该任务对应的 design.md 锚点（API-NNN / DATA-NNN / D-NNN），再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "unknown_plan_json_requirement_ref": Repair(
        artifact="plan.json",
        problem="{target} 引用的 REQ 在 specs 中不存在",
        action=(
            "先核对是不是编号写错：写错就用 hooks/plan_writer.py 改成正确的 REQ。"
            "若 specs 里确实没有这条需求，停止 plan 阶段回 dev.specs 补定义，不要在 plan 里编一个。"
            + PLAN_NO_HAND_EDIT
        ),
    ),
    "unknown_plan_json_scenario_ref": Repair(
        artifact="plan.json",
        problem="{target} 引用的 SCN 在 specs 中不存在",
        action=(
            "先核对是不是编号写错：写错就用 hooks/plan_writer.py 改成正确的 SCN。"
            "若 specs 里确实没有这条场景，停止 plan 阶段回 dev.specs 补定义，不要在 plan 里编一个。"
            + PLAN_NO_HAND_EDIT
        ),
    ),
    "unknown_plan_json_api_ref": Repair(
        artifact="design.md",
        problem="{target} 引用的 API 编号在 design.md 中未定义",
        action="编号写错就用 hooks/plan_writer.py 改正；design.md 确实还没定义就先在 API Decisions 节补出该 API-NNN，再重建任务引用。",
    ),
    "unknown_plan_json_data_ref": Repair(
        artifact="design.md",
        problem="{target} 引用的 DATA 编号在 design.md 中未定义",
        action="编号写错就用 hooks/plan_writer.py 改正；design.md 确实还没定义就先在 Data Decisions 节补出该 DATA-NNN，再重建任务引用。",
    ),
    "unknown_plan_json_decision_ref": Repair(
        artifact="design.md",
        problem="{target} 引用的 D（技术决策）编号在 design.md 中未定义",
        action="编号写错就用 hooks/plan_writer.py 改正；design.md 确实还没定义就先在 Technical Design 节补出该 D-NNN，再重建任务引用。",
    ),
    "plan_api_ref_forbidden_by_design_marker": Repair(
        artifact="plan.json",
        problem="{target} 引用了 API，但 design.md 声明 x-auto-no-http-api=true：{detail}",
        action="Design 是事实源：从任务分组或任务详情中移除该 API 引用，再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "plan_data_ref_forbidden_by_design_marker": Repair(
        artifact="plan.json",
        problem="{target} 引用了 DATA，但 design.md 声明 x-auto-no-sql=true：{detail}",
        action="Design 是事实源：从任务详情中移除该 DATA 引用，再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_json_api_coverage": Repair(
        artifact="plan.json",
        problem="design.md 定义的 API {target} 没有被任何实现任务覆盖",
        action="回到任务分组表，把该 API 分配给实际实现它的任务后重建 Draft；若本轮不实现，回 design.md 明确标注无需实现。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_json_data_coverage": Repair(
        artifact="plan.json",
        problem="design.md 定义的 DATA {target} 没有被任何实现任务覆盖",
        action="回到任务分组表，把该 DATA 分配给实际实现它的任务后重建 Draft；若本轮不实现，回 design.md 明确标注无需实现。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_json_decision_coverage": Repair(
        artifact="plan.json",
        problem="design.md 定义的技术决策 {target} 没有被任何实现任务覆盖",
        action="回到任务分组表，把该 D 分配给落实它的任务后重建 Draft；若本轮不落实，回 design.md 明确标注无需实现。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_scenario_coverage": Repair(
        artifact="plan.json",
        problem="specs 中这些 Scenario 没有被任何任务覆盖：{target}",
        action="回到任务分组表，把报错的每个 SCN 分配给实际实现它的任务，再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "invalid_plan_task_scenario_reference": Repair(
        artifact="plan.json",
        problem="{target} 的 specRefs 里 Scenario 引用没有逐条展开或没有全限定",
        action="把 SCN 引用写成逐条展开的全限定形式（一个 SCN 一项，不用区间/通配），再用 hooks/plan_writer.py 重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "oversized_plan_task_must_split": Repair(
        artifact="plan.json",
        problem="{target} 超出单任务上限：{detail}",
        action="按报错里超限的那个维度拆分该任务（场景/接口/页面/交互），拆成多个仍然是用户可观察 vertical slice 的任务，再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_task_merged_scenario_refs": Repair(
        artifact="plan.json",
        problem="{target} 合并了多个场景但没有列出被合并的 Scenario",
        action="在该任务的合并场景字段里逐条列出被合并的 SCN，再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "invalid_plan_task_merged_scenario_refs": Repair(
        artifact="plan.json",
        problem="{target} 列出的合并场景与实际 specRefs 对不上",
        action="让合并场景列表与该任务 specRefs 中的 SCN 完全一致，再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_plan_task_split_rationale": Repair(
        artifact="plan.json",
        problem="{target} 缺少 splitRationale",
        action=(
            "写清两件事：这个任务合并了哪些场景（逐个点名相关编号）、以及验证边界为什么仍然成立"
            "（要出现具体验证手段，不能只写「便于实现」这类空话），再重建 Draft。" + PLAN_NO_HAND_EDIT
        ),
    ),
    "invalid_plan_task_split_rationale": Repair(
        artifact="plan.json",
        problem="{target} 的 splitRationale 不满足要求：{detail}",
        action=(
            "先读取返回 JSON 的 diagnostics.violations，逐项修复其中列出的字段、编号和验证边界；"
            "不得根据 Scenario 编号猜测拆分归属。若不满足真实共享验证闭环，应回覆盖矩阵按业务闭环拆分，"
            "再重建 Draft。" + PLAN_NO_HAND_EDIT
        ),
    ),
    "invalid_plan_task_matrix_validation": Repair(
        artifact="plan.json",
        problem="{target} 的验证矩阵不合法：{detail}",
        action="按报错维度补齐该任务的验证方式，确保每个场景都有可自行判读的验证，再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "invalid_artifact_ref": Repair(
        artifact="plan.json",
        problem="{target} 的引用格式不合法：{detail}",
        action="引用写成 `<文件路径>#<锚点>` 形式（如 specs/<capability>/spec.md#REQ-001、design.md#API-001），再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "invalid_artifact_ref_format": Repair(
        artifact="plan.json",
        problem="{target} 的引用格式不合法：{detail}",
        action="引用写成 `<文件路径>#<锚点>` 形式（如 specs/<capability>/spec.md#REQ-001、design.md#API-001），再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "invalid_artifact_ref_type": Repair(
        artifact="plan.json",
        problem="{target} 的引用类型与字段不匹配：{detail}",
        action="specRefs 只放 REQ/SCN，designRefs 只放 API/DATA/D；修正任务引用后再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "ambiguous_ref_anchor": Repair(
        artifact="plan.json",
        problem="{target} 的短引用无法唯一定位：{detail}",
        action="把短引用改成带相对文件路径的完整形式 `<文件路径>#<锚点>`，再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_ref_file": Repair(
        artifact="plan.json",
        problem="{target} 引用的文件不存在：{detail}",
        action="核对路径是否写错；文件确实还没生成时，先补齐上游产物（specs 缺失回 dev.specs，design 缺失就地补写），再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
    "missing_ref_anchor": Repair(
        artifact="plan.json",
        problem="{target} 引用的锚点在目标文件中不存在：{detail}",
        action="核对编号是否写错；目标文件确实没有该定义时，specs 缺失回 dev.specs 补，design 缺失就地在 design.md 补，再重建 Draft。" + PLAN_NO_HAND_EDIT,
    ),
}


REPAIRS: Dict[str, Repair] = {}
REPAIRS.update(_ENTRY)
REPAIRS.update(_SPECS)
REPAIRS.update(_DESIGN)
REPAIRS.update(_PLAN)


# ``invalid_plan_json`` 的 detail 是 plan_json.py 的扁平码。高频码给精确动作，
# 其余走 REPAIRS["invalid_plan_json"] 兜底——兜底同样带完整五字段。
_PLAN_JSON_CODE_ACTIONS = (
    ("monolithic_plan_requires_rebuild", "根 plan.json 不得内联 tasks：用 hooks/plan_writer.py 重建成「根索引 + plans/Bxxx/plan.json 批次」结构。"),
    ("legacy_plan_requires_rebuild", "这是旧版 plan.json：用 hooks/plan_writer.py 重建，不要手工补字段。"),
    ("plan_json_missing_feature_id", "plan.json 缺 featureId：用 hooks/plan_writer.py 重建，featureId 必须与当前 Feature 一致。"),
    ("plan_json_status_not_initial", "plan 初始状态必须是 todo：用 hooks/plan_writer.py 重建，不要就地改 status。"),
    ("plan_json_status_not_done", "所有任务完成后状态才应为 done：用任务状态机推进，不要就地改 status。"),
    ("plan_json_status_invalid", "status 取值非法：用 hooks/plan_writer.py 重建。"),
    ("plan_json_taskSetStatus_invalid", "taskSetStatus 取值非法：用 hooks/plan_writer.py 重建并按流程 finalize。"),
    ("plan_json_taskSetDigest_invalid", "taskSetDigest 非法：不要手写，由 hooks/plan_writer.py 定稿时生成。"),
    ("plan_json_implementationScope_invalid", "implementationScope 取值非法：用 hooks/implementation_scope.py set 修正后重建 plan。"),
    ("plan_json_batchPolicy", "批次策略字段必须与约定一致：用 hooks/plan_writer.py 重建，不要手工调整 batchPolicy。"),
    ("dependency_unknown", "任务依赖指向了不存在的任务号：核对任务号后用 hooks/plan_writer.py 修正依赖。"),
    ("dependency_cycle", "任务依赖成环：用 hooks/plan_writer.py 调整依赖，使任务 DAG 无环。"),
    ("verified_existing_create_in_code_forbidden", "verified_existing 任务不得新建文件：改成 create 类执行模式，或去掉新建目标，再重建 Draft。"),
)


def _plan_json_code_action(detail: str) -> str:
    for code, action in _PLAN_JSON_CODE_ACTIONS:
        if code in detail:
            return action + PLAN_NO_HAND_EDIT
    return ""


def lookup(reason: str, detail: str = "") -> Repair | None:
    """返回该 reason 的修复信息；未注册返回 None（调用点退回旧行为）。"""
    repair = REPAIRS.get(reason)
    if repair is None:
        return None
    if reason == "invalid_plan_json":
        action = _plan_json_code_action(detail)
        if action:
            return repair._replace(action=action)
    return repair
