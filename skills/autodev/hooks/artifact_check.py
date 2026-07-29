#!/usr/bin/env python3
"""Run Autodev artifact checks from board_config.json."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from common import (
    HookCheckError,
    HookContext,
    fail_line,
    info,
    is_nonempty,
    load_artifact_config,
    plan_task_blocks,
    read_text,
    task_count,
    task_statuses,
    validate_required_files,
)
from board_core.contracts import BoardConfigError, load_board_config, load_repo_workflow_contracts  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, configured_profile_names  # noqa: E402


PENDING_STATUS = re.compile(r"待做|进行中|in[-_ ]?progress|todo|pending", re.IGNORECASE)
# 表格单元格恰好为「待确认 / 读码差异」时命中；`风险/待确认` 这类枚举说明或「」引用不命中
PENDING_CELL = re.compile(r"\|\s*(待确认|读码差异)\s*\|")
CAP_ID_HEADER = re.compile(r"^Capability-ID:\s*`?(CAP-[a-z0-9][a-z0-9-]*)`?\s*$", re.MULTILINE)
REQ_HEADING = re.compile(r"^###\s+REQ-([a-z0-9][a-z0-9-]*?)-(\d{3}):\s*\S", re.MULTILINE)
SCN_HEADING = re.compile(r"^####\s+SCN-([a-z0-9][a-z0-9-]*?)-(\d{3})-(\d{2}):\s*\S", re.MULTILINE)
REQ_CANDIDATE_HEADING = re.compile(r"^###\s+(REQ-[^:\s]+):\s*\S", re.MULTILINE)
SCN_CANDIDATE_HEADING = re.compile(r"^####\s+(SCN-[^:\s]+):\s*\S", re.MULTILINE)
OPERATION_HEADING = re.compile(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\b", re.MULTILINE)
VALID_OPERATIONS = {"ADDED", "MODIFIED", "REMOVED", "RENAMED"}
TASK_STATUS_LINE = re.compile(r"^[ \t]*[-*][ \t]*\*\*状态:\*\*[ \t]*(.+)$", re.MULTILINE)
TASK_EVIDENCE_LINE = re.compile(r"^[ \t]*[-*][ \t]*\*\*完成记录:\*\*[ \t]*(.+)$", re.MULTILINE)
# design 决策表首列 ID（API/DATA/D；EVD 是证据不是待实现决策，不参与覆盖检查）
DESIGN_DECISION_ROW = re.compile(r"^\|\s*`?((?:API|DATA)-\d{1,3}|D-\d{1,3})`?\s*\|", re.MULTILINE)
FENCE_OPEN_LINE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
BLOCKQUOTE_LINE = re.compile(r"^[ \t]{0,3}>")
TEMPLATE_WRAPPER_HEADINGS = {"# 技术设计模板", "# 计划模板"}
# proposal Open Questions：「已确认」不是可以自己给自己发的状态，必须带跨文件证据
PENDING_MARKERS = {"待确认", "读码差异"}
RESOLVED_STATUS = "已确认"
DEC_ID = re.compile(r"\bDEC-\d{1,3}\b")
DEC_HEADING = re.compile(r"^###\s+(DEC-\d{1,3})\s*[:：]", re.MULTILINE)
DECISION_FIELD = re.compile(
    r"^[ \t]*[-*][ \t]*\*{0,2}(决定|为什么|否决|约束)\*{0,2}[:：]\*{0,2}[ \t]*(.*)$",
    re.MULTILINE,
)
CONSTRAINT_ID = re.compile(r"\b(?:REQ-[a-z0-9][a-z0-9-]*-\d{3}|CAP-[a-z0-9][a-z0-9-]*)\b")
PLACEHOLDER_TEXT = re.compile(r"\[[^\]]*\]|TBD|待补充|待提供|待定|占位", re.IGNORECASE)
NORMALIZE_STRIP = re.compile(r"[\s？?。.，,、；;：:！!「」“”\"'`*（）()]+")
PLAN_INITIAL_REPAIRS = {
    "missing_plan": "创建 PLAN.md，并按 autodev-plan/templates/plan.md 生成任务总览、任务详情和 Contract Coverage。",
    "invalid_plan_structure": "在 PLAN.md 中补齐二级标题「## 任务总览」和「## 任务详情」。",
    "missing_plan_contract_coverage": "在 PLAN.md 中补齐「## Contract Coverage / 契约覆盖」及覆盖表。",
    "invalid_plan_no_tasks": "在「任务详情」中至少新增一个「### TASK-NNN: 任务名」任务块，并在任务总览增加同 ID 行。",
    "missing_task_statuses": "在每个「### TASK-NNN:」任务块内加入独立一行「- **状态:** 待做」；保留列表符号、半角冒号和中文状态。",
    "invalid_initial_task_status": "将每个任务块的状态行改为「- **状态:** 待做」；Plan 阶段不得写 pending、进行中或完成。",
    "task_missing_completion_record_field": "在报错 task 的任务块内加入独立一行「- **完成记录:** 无」；保留列表符号和半角冒号。",
    "invalid_initial_completion_record": "将报错 task 的完成记录行改为「- **完成记录:** 无」；执行证据只由 Code 阶段回写。",
    "plan_has_pending_cells": "逐个消解 PLAN.md 表格中的「待确认」或「读码差异」单元格并写入确定值；无法裁定时停留在 Plan 阶段。",
}


def section_text(text: str, heading: str) -> str:
    """返回 `## <heading>` 小节正文（到下一个二级标题为止）；无该节返回空串。"""
    match = re.search(
        rf"^##\s+{re.escape(heading)}\b[^\n]*\n(.*?)(?=^##\s|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def parse_capability_index(text: str) -> list[dict[str, str]] | None:
    """解析 proposal 的 Capability Index 表。

    返回 None 表示 proposal 没有该节（legacy 格式）；返回 [] 表示有节但无有效行（「无」场景）。
    含 `[` 的占位行不算有效行，由 proposal_contract 单独报错。
    """
    body = section_text(text, "Capability Index")
    if not body:
        return None
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`").strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        first = cells[0]
        if not first or set(first) <= {"-", ":", " "} or first == "Capability ID":
            continue
        if any("[" in cell for cell in cells[:4]):
            continue
        rows.append(
            {
                "cap_id": first,
                "name": cells[1],
                "operations": cells[2],
                "path": cells[3],
                "status": cells[4],
            }
        )
    return rows


def index_placeholder_rows(text: str) -> int:
    body = section_text(text, "Capability Index")
    count = 0
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("|") and "[" in line and "Capability ID" not in line:
            count += 1
    return count


def parse_operations_cell(cell: str) -> set[str]:
    tokens = re.split(r"[,，、/+\s]+", cell.strip())
    return {token.upper() for token in tokens if token} & VALID_OPERATIONS


def spec_actual_operations(text: str) -> set[str]:
    """spec 内实际承载 REQ 的操作段集合（strict 模式使用）。"""
    operations: set[str] = set()
    for match in OPERATION_HEADING.finditer(text):
        start = match.end()
        next_section = re.search(r"^##\s", text[start:], re.MULTILINE)
        body = text[start : start + next_section.start()] if next_section else text[start:]
        if REQ_HEADING.search(body):
            operations.add(match.group(1))
    return operations


def malformed_contract_headings(
    text: str,
    candidate_pattern: re.Pattern[str],
    valid_ids: set[str],
) -> list[str]:
    return [
        match.group(1)
        for match in candidate_pattern.finditer(text)
        if match.group(1) not in valid_ids
    ]


def parse_open_questions(text: str) -> tuple[bool, list[dict[str, str]]] | None:
    """解析 proposal 的 Open Questions 表。

    返回 ``(legacy, rows)``；``None`` 表示 proposal 没有该节。``legacy=True`` 表示表头缺
    ``Resolution``/``Decision`` 列（老 proposal），只走「无待确认单元格」的宽松检查。
    正文写「无」或没有表格时返回空行列表。
    """
    body = section_text(text, "Open Questions")
    if not body:
        return None

    header: list[str] = []
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`").strip() for cell in line.strip("|").split("|")]
        if not cells or all(not cell or set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if not header:
            header = cells
            continue
        row = dict(zip(header, cells))
        if not row.get("ID"):
            continue
        rows.append(row)

    # 没有表格（正文写「无」）不算 legacy，没有可降级的内容
    legacy = bool(header) and not {"Resolution", "Decision"} <= set(header)
    if legacy:
        return True, rows
    return False, [
        {
            "id": row.get("ID", ""),
            "question": row.get("Question", ""),
            "resolution": row.get("Resolution", ""),
            "decision": row.get("Decision", ""),
            "status": row.get("Status", ""),
        }
        for row in rows
    ]


def decision_log_entries(text: str) -> dict[str, dict[str, str]]:
    """解析 proposal 的 Decision Log：``{DEC-001: {决定/为什么/否决/约束}}``。"""
    body = section_text(text, "Decision Log")
    entries: dict[str, dict[str, str]] = {}
    headings = list(DEC_HEADING.finditer(body))
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        fields = {name: value.strip() for name, value in DECISION_FIELD.findall(body[start:end])}
        entries[match.group(1)] = fields
    return entries


def is_filled(value: str | None) -> bool:
    """非空且不是 `[占位]` / TBD / 待补充 这类占位文本。"""
    return bool(value) and not PLACEHOLDER_TEXT.search(value)


def restates_question(resolution: str, question: str) -> bool:
    """Resolution 只是 Question 的复述（去标点空白后被 Question 包含）。

    很短的回答（如「是」「支持」）天然会是问句的子串，不当作复述；真正的空转由
    ``is_filled`` 与 Decision 绑定检查兜住。
    """
    normalized_resolution = NORMALIZE_STRIP.sub("", resolution)
    normalized_question = NORMALIZE_STRIP.sub("", question)
    if not normalized_resolution or not normalized_question:
        return False
    if normalized_resolution == normalized_question:
        return True
    return len(normalized_resolution) >= 8 and normalized_resolution in normalized_question


def find_template_guidance_residue(text: str) -> list[tuple[int, str]]:
    """Return ``(line number, kind)`` for template-only markup in an artifact.

    Autodev contract artifacts use paragraphs, lists, and tables for explanatory
    content. Markdown blockquotes are reserved for template authoring guidance
    and must not survive generation. Fenced code blocks are ignored so examples
    containing shell redirects or quoted Markdown do not false-positive.
    """
    residues: list[tuple[int, str]] = []
    fence_char = ""
    fence_length = 0
    seen_content = False

    for lineno, line in enumerate(text.splitlines(), 1):
        if fence_char:
            stripped = line.lstrip(" \t")
            run_length = len(stripped) - len(stripped.lstrip(fence_char))
            if run_length >= fence_length and not stripped[run_length:].strip():
                fence_char = ""
                fence_length = 0
            continue

        fence_match = FENCE_OPEN_LINE.match(line)
        if fence_match:
            fence = fence_match.group("fence")
            info = fence_match.group("info").strip().lower()
            if info in {"markdown", "md"} and not seen_content:
                residues.append((lineno, "outer_markdown_fence"))
            fence_char = fence[0]
            fence_length = len(fence)
            seen_content = True
            continue

        stripped = line.strip()
        if stripped in TEMPLATE_WRAPPER_HEADINGS:
            residues.append((lineno, "wrapper_heading"))
        if BLOCKQUOTE_LINE.match(line):
            residues.append((lineno, "blockquote"))
        if stripped:
            seen_content = True

    return residues


def validate_no_template_guidance(
    ctx: HookContext,
    path: Path,
    text: str,
) -> int:
    """Reject template authoring guidance copied into a generated artifact."""
    try:
        artifact = path.relative_to(ctx.feature_dir).as_posix()
    except ValueError:
        artifact = str(path)

    failures = 0
    for lineno, kind in find_template_guidance_residue(text):
        failures += fail_line(
            ctx,
            "artifact_template_guidance_residue",
            f" file={artifact!r} line={lineno} kind={kind}",
            repair="删除报错 file/line 的模板说明、外层 Markdown 围栏或引用块，只保留实际产物内容。",
        )
    return failures


VALID_VERDICT = re.compile(r"verdict\s*[:=]\s*(PASS_WITH_WARNINGS|PASS|FAIL|DEGRADED)\b", re.IGNORECASE)
TERMINAL_PASS = {"PASS", "PASS_WITH_WARNINGS"}
UNIT_TEST_VERDICT = re.compile(
    r"verdict\W*[:=]\W*(PASS_WITH_WARNINGS|PASS|FAIL|BLOCKED)\b",
    re.IGNORECASE,
)
UNIT_TEST_PASS = {"PASS", "PASS_WITH_WARNINGS"}


def spec_files(ctx: HookContext) -> list[Path]:
    return sorted(
        path
        for path in ctx.feature_dir.glob("specs/**/*.md")
        if path.is_file() and path.stat().st_size > 0
    )


def spec_declared_ids(ctx: HookContext) -> set[str] | None:
    """specs/** 中真实存在的 REQ / CAP 稳定 ID；无 spec 文件时返回 None。"""
    specs = spec_files(ctx)
    if not specs:
        return None
    declared: set[str] = set()
    for spec in specs:
        text = read_text(spec)
        declared.update(f"REQ-{cap}-{number}" for cap, number in REQ_HEADING.findall(text))
        declared.update(CAP_ID_HEADER.findall(text))
    return declared


def validate_open_questions_rows(
    ctx: HookContext,
    text: str,
    rows: list[dict[str, str]],
    spec_ids: set[str] | None,
) -> int:
    """每行「已确认」都必须带跨文件证据，只翻 Status 不算消解。

    证据链：``Resolution`` 是裁定的具体结论（非占位、非问题复述）→ ``Decision`` 指向
    ``Decision Log`` 中一条填齐的 ``DEC-NNN`` → 该决策的 ``约束`` 落到 specs 中真实存在的
    ``REQ``/``CAP``。任一环断裂则该行未消解。
    """
    decisions = decision_log_entries(text)
    failures = 0
    for row in rows:
        qid = row["id"]
        status = row["status"]
        if status in PENDING_MARKERS:
            # 已由 proposal_open_questions_pending 统一报，不逐行重复
            continue
        if status != RESOLVED_STATUS:
            failures += fail_line(
                ctx, "open_questions_status_invalid", f" id={qid!r} status={status!r}"
            )
            continue

        if not is_filled(row["resolution"]):
            failures += fail_line(ctx, "open_questions_resolution_missing", f" id={qid!r}")
        elif restates_question(row["resolution"], row["question"]):
            failures += fail_line(
                ctx, "open_questions_resolution_restates_question", f" id={qid!r}"
            )

        decision_match = DEC_ID.search(row["decision"])
        if not decision_match:
            failures += fail_line(ctx, "open_questions_decision_missing", f" id={qid!r}")
            continue
        decision_id = decision_match.group(0)
        entry = decisions.get(decision_id)
        if entry is None or not is_filled(entry.get("决定")) or not is_filled(entry.get("为什么")):
            failures += fail_line(
                ctx, "open_questions_decision_not_in_log", f" id={qid!r} decision={decision_id!r}"
            )
            continue

        if spec_ids is None:
            # 缺 specs 由 specs_contract 单独报，这里不重复
            continue
        if not set(CONSTRAINT_ID.findall(entry.get("约束", ""))) & spec_ids:
            failures += fail_line(
                ctx, "open_questions_decision_unbound", f" id={qid!r} decision={decision_id!r}"
            )
    return failures


def validate_proposal_contract(ctx: HookContext) -> int:
    proposal = ctx.file("proposal.md")
    if not is_nonempty(proposal):
        return fail_line(ctx, "missing_proposal")

    text = read_text(proposal)
    failures = validate_no_template_guidance(ctx, proposal, text)
    required_sections = [
        "Why",
        "What Changes",
        "Capability Index",
        "Impact",
        "Out of Scope",
        "Decision Log",
    ]
    for section in required_sections:
        if section not in text:
            failures += fail_line(ctx, "invalid_proposal_missing_section", f" section={section!r}")

    placeholder_rows = index_placeholder_rows(text)
    if placeholder_rows:
        failures += fail_line(ctx, "capability_index_placeholder_row", f" rows={placeholder_rows}")

    index_rows = parse_capability_index(text)
    if index_rows:
        for row in index_rows:
            expected_cap_id = f"CAP-{row['name']}"
            expected_path = f"specs/{row['name']}/spec.md"
            if row["cap_id"] != expected_cap_id or row["path"] != expected_path:
                failures += fail_line(
                    ctx,
                    "capability_index_row_mismatch",
                    f" cap_id={row['cap_id']!r} path={row['path']!r}",
                )
            if not parse_operations_cell(row["operations"]):
                failures += fail_line(
                    ctx, "capability_index_invalid_operations", f" cap_id={row['cap_id']!r}"
                )

    open_questions = section_text(text, "Open Questions")
    if PENDING_CELL.search(open_questions):
        failures += fail_line(ctx, "proposal_open_questions_pending")

    # 按标题查，比 required_sections 的子串判断更严：删掉整节不再是免检出口
    parsed = parse_open_questions(text)
    if parsed is None:
        failures += fail_line(
            ctx, "invalid_proposal_missing_section", " section='Open Questions'"
        )
    else:
        legacy, rows = parsed
        if legacy:
            info(ctx, "open_questions_legacy_degrade")
        else:
            failures += validate_open_questions_rows(ctx, text, rows, spec_declared_ids(ctx))
    return failures


def validate_specs_contract(ctx: HookContext) -> int:
    specs = spec_files(ctx)
    if not specs:
        return fail_line(ctx, "missing_specs")

    failures = 0
    seen_req_ids: dict[str, str] = {}
    seen_scn_ids: dict[str, str] = {}
    actual_paths: set[str] = set()

    for spec in specs:
        text = read_text(spec)
        rel = spec.relative_to(ctx.feature_dir)
        rel_posix = rel.as_posix()
        failures += validate_no_template_guidance(ctx, spec, text)
        actual_paths.add(rel_posix)
        capability = spec.parent.name

        if not OPERATION_HEADING.search(text):
            failures += fail_line(ctx, "invalid_spec_missing_operation_header", f" file={rel}")

        cap_header = CAP_ID_HEADER.search(text)
        if cap_header:
            # 新格式：稳定 ID 严格校验
            if cap_header.group(1) != f"CAP-{capability}":
                failures += fail_line(
                    ctx,
                    "invalid_spec_capability_id",
                    f" file={rel} cap_id={cap_header.group(1)!r}",
                )
            req_matches = list(REQ_HEADING.finditer(text))
            scn_matches = list(SCN_HEADING.finditer(text))
            valid_req_ids = {
                f"REQ-{match.group(1)}-{match.group(2)}" for match in req_matches
            }
            valid_scn_ids = {
                f"SCN-{match.group(1)}-{match.group(2)}-{match.group(3)}"
                for match in scn_matches
            }
            malformed_req_ids = malformed_contract_headings(
                text, REQ_CANDIDATE_HEADING, valid_req_ids
            )
            malformed_scn_ids = malformed_contract_headings(
                text, SCN_CANDIDATE_HEADING, valid_scn_ids
            )
            if malformed_req_ids:
                failures += fail_line(
                    ctx,
                    "invalid_spec_requirement_heading",
                    (
                        f" file={rel} found={malformed_req_ids!r}"
                        f" expected='### REQ-{capability}-NNN: <title>'"
                    ),
                )
            elif not req_matches:
                failures += fail_line(ctx, "invalid_spec_missing_requirement", f" file={rel}")
            if malformed_scn_ids:
                failures += fail_line(
                    ctx,
                    "invalid_spec_scenario_heading",
                    (
                        f" file={rel} found={malformed_scn_ids!r}"
                        f" expected='#### SCN-{capability}-NNN-NN: <title>'"
                    ),
                )
            elif not scn_matches:
                failures += fail_line(ctx, "invalid_spec_missing_scenario", f" file={rel}")

            req_ids_in_file: set[str] = set()
            for match in req_matches:
                req_id = f"REQ-{match.group(1)}-{match.group(2)}"
                if match.group(1) != capability:
                    failures += fail_line(
                        ctx, "spec_req_capability_mismatch", f" file={rel} id={req_id}"
                    )
                if req_id in seen_req_ids:
                    failures += fail_line(
                        ctx,
                        "duplicate_requirement_id",
                        f" id={req_id} files={seen_req_ids[req_id]},{rel_posix}",
                    )
                seen_req_ids.setdefault(req_id, rel_posix)
                req_ids_in_file.add(req_id)

            for match in scn_matches:
                scn_id = f"SCN-{match.group(1)}-{match.group(2)}-{match.group(3)}"
                parent_req = f"REQ-{match.group(1)}-{match.group(2)}"
                if parent_req not in req_ids_in_file:
                    failures += fail_line(
                        ctx, "scenario_without_requirement", f" file={rel} id={scn_id}"
                    )
                if scn_id in seen_scn_ids:
                    failures += fail_line(
                        ctx,
                        "duplicate_scenario_id",
                        f" id={scn_id} files={seen_scn_ids[scn_id]},{rel_posix}",
                    )
                seen_scn_ids.setdefault(scn_id, rel_posix)
        else:
            # legacy 格式：保持旧校验，避免既有 feature 无法回放
            if not re.search(r"^###\s+(Requirement:\s+.+|REQ-[a-z0-9-]+-\d{3}:)", text, re.MULTILINE):
                failures += fail_line(ctx, "invalid_spec_missing_requirement", f" file={rel}")
            if not re.search(r"^####\s+(Scenario:\s+.+|SCN-[a-z0-9-]+-\d{3}-\d{2}:)", text, re.MULTILINE):
                failures += fail_line(ctx, "invalid_spec_missing_scenario", f" file={rel}")

    # Capability Index 双射与 Operations 一致性（proposal 为新格式时机械校验）
    proposal = ctx.file("proposal.md")
    if is_nonempty(proposal):
        index_rows = parse_capability_index(read_text(proposal))
        if index_rows is not None:
            index_paths = {row["path"] for row in index_rows}
            for row in index_rows:
                if row["path"] not in actual_paths:
                    failures += fail_line(
                        ctx, "capability_index_missing_spec", f" path={row['path']!r}"
                    )
                    continue
                spec_path = ctx.feature_dir / row["path"]
                spec_text = read_text(spec_path)
                if CAP_ID_HEADER.search(spec_text):
                    declared = parse_operations_cell(row["operations"])
                    actual = spec_actual_operations(spec_text)
                    valid_req_ids = {
                        f"REQ-{match.group(1)}-{match.group(2)}"
                        for match in REQ_HEADING.finditer(spec_text)
                    }
                    has_invalid_req_structure = not valid_req_ids or bool(
                        malformed_contract_headings(
                            spec_text, REQ_CANDIDATE_HEADING, valid_req_ids
                        )
                    )
                    if declared != actual and not has_invalid_req_structure:
                        failures += fail_line(
                            ctx,
                            "capability_operations_mismatch",
                            f" cap_id={row['cap_id']!r} declared={sorted(declared)} actual={sorted(actual)}",
                        )
            for path in sorted(actual_paths - index_paths):
                failures += fail_line(ctx, "spec_not_in_capability_index", f" path={path!r}")
    return failures


def repo_root_from_this_file() -> Path:
    return Path(__file__).resolve().parents[3]


def validate_design_contract(ctx: HookContext) -> int:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return fail_line(ctx, "missing_design")

    text = read_text(design)
    failures = validate_no_template_guidance(ctx, design, text)
    required_sections = [
        "Context / 输入上下文",
        "Code Evidence",
        "Spec Traceability",
        "API Decisions",
        "Data Decisions",
        "Technical Design",
        "Risks / Open Questions",
    ]
    for section in required_sections:
        if section not in text:
            failures += fail_line(ctx, "invalid_design_missing_section", f" section={section!r}")

    if not re.search(r"x-auto-no-http-api\W*:\W*(true|false)", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_api_marker")
    if not re.search(r"x-auto-no-sql\W*:\W*(true|false)", text, re.IGNORECASE):
        failures += fail_line(ctx, "missing_design_data_marker")
    pending = PENDING_CELL.findall(text)
    if pending:
        failures += fail_line(ctx, "design_has_pending_cells", f" count={len(pending)}")
    return failures


def validate_plan_initial_tasks(ctx: HookContext) -> int:
    plan = ctx.file("PLAN.md")
    if not is_nonempty(plan):
        return fail_line(
            ctx,
            "missing_plan",
            repair=PLAN_INITIAL_REPAIRS["missing_plan"],
        )
    plan_text = read_text(plan)
    failures = validate_no_template_guidance(ctx, plan, plan_text)
    if "任务总览" not in plan_text or "任务详情" not in plan_text:
        failures += fail_line(
            ctx,
            "invalid_plan_structure",
            repair=PLAN_INITIAL_REPAIRS["invalid_plan_structure"],
        )
    # 新模板要求单一覆盖表；legacy 双覆盖表 PLAN 仍可回放
    if "Contract Coverage" not in plan_text and "Specs 行为覆盖" not in plan_text:
        failures += fail_line(
            ctx,
            "missing_plan_contract_coverage",
            repair=PLAN_INITIAL_REPAIRS["missing_plan_contract_coverage"],
        )
    if task_count(plan) <= 0:
        failures += fail_line(
            ctx,
            "invalid_plan_no_tasks",
            repair=PLAN_INITIAL_REPAIRS["invalid_plan_no_tasks"],
        )
    statuses = task_statuses(plan)
    if not statuses:
        failures += fail_line(
            ctx,
            "missing_task_statuses",
            repair=PLAN_INITIAL_REPAIRS["missing_task_statuses"],
        )
    elif any("待做" not in status for status in statuses):
        failures += fail_line(
            ctx,
            "invalid_initial_task_status",
            repair=PLAN_INITIAL_REPAIRS["invalid_initial_task_status"],
        )
    # 新格式任务块必须带「完成记录」字段且初始为「无」（code 阶段回写的落点）
    missing_completion_records: list[str] = []
    invalid_completion_records: list[str] = []
    for task_id, block in plan_task_blocks(plan_text).items():
        evidence = TASK_EVIDENCE_LINE.search(block)
        if not evidence:
            missing_completion_records.append(task_id)
        elif evidence.group(1).strip() != "无":
            invalid_completion_records.append(task_id)
    if missing_completion_records:
        failures += fail_line(
            ctx,
            "task_missing_completion_record_field",
            f" tasks={','.join(missing_completion_records)}",
            repair=PLAN_INITIAL_REPAIRS["task_missing_completion_record_field"],
        )
    if invalid_completion_records:
        failures += fail_line(
            ctx,
            "invalid_initial_completion_record",
            f" tasks={','.join(invalid_completion_records)}",
            repair=PLAN_INITIAL_REPAIRS["invalid_initial_completion_record"],
        )
    pending = PENDING_CELL.findall(plan_text)
    if pending:
        failures += fail_line(
            ctx,
            "plan_has_pending_cells",
            f" count={len(pending)}",
            repair=PLAN_INITIAL_REPAIRS["plan_has_pending_cells"],
        )
    return failures


def validate_plan_execution_contract(ctx: HookContext) -> int:
    from plan_execution_check import main as plan_execution_check_main

    return plan_execution_check_main(
        [ctx.slug, "--workspace-root", str(ctx.root)]
    )


def validate_plan_finished_tasks(ctx: HookContext) -> int:
    plan = ctx.file("PLAN.md")
    if not is_nonempty(plan):
        # PLAN.md not in this workflow's contract (e.g. lean): degrade,
        # task closure lives in the completion summary instead.
        if not ctx.requires_artifact("PLAN.md"):
            info(ctx, "plan_not_in_contract_degrade")
            return 0
        return fail_line(ctx, "missing_plan")
    plan_text = read_text(plan)
    failures = validate_no_template_guidance(ctx, plan, plan_text)
    if task_count(plan) <= 0:
        failures += fail_line(ctx, "invalid_plan_no_tasks")
    statuses = task_statuses(plan)
    if not statuses:
        failures += fail_line(ctx, "missing_task_statuses")
    elif any(PENDING_STATUS.search(status) for status in statuses):
        failures += fail_line(ctx, "plan_has_pending_tasks")
    elif any("失败" in status for status in statuses):
        failures += fail_line(ctx, "plan_has_failed_tasks")
    elif any("完成" not in status for status in statuses):
        failures += fail_line(ctx, "invalid_task_status")
    pending = PENDING_CELL.findall(plan_text)
    if pending:
        failures += fail_line(ctx, "plan_has_pending_cells", f" count={len(pending)}")
    # 状态「完成」的任务必须留下执行证据；防止批量刷状态的橡皮图章
    for task_id, block in plan_task_blocks(plan_text).items():
        status_match = TASK_STATUS_LINE.search(block)
        status = status_match.group(1).strip() if status_match else ""
        if "完成" in status:
            evidence = TASK_EVIDENCE_LINE.search(block)
            value = evidence.group(1).strip() if evidence else ""
            if value in ("", "无"):
                failures += fail_line(ctx, "task_missing_completion_evidence", f" task={task_id}")
    # design 每个 API/DATA/D 决策必须在 PLAN 中出现（实现任务引用或「无需实现」标注行）
    design = ctx.file("design.md")
    if is_nonempty(design):
        for decision_id in sorted(set(DESIGN_DECISION_ROW.findall(read_text(design)))):
            if decision_id not in plan_text:
                failures += fail_line(ctx, "design_decision_uncovered", f" id={decision_id}")
    return failures


def validate_requirements_eval_verdict(ctx: HookContext) -> int:
    eval_report = ctx.file("REQUIREMENTS_EVAL.md")
    if not is_nonempty(eval_report):
        return fail_line(ctx, "missing_requirements_eval")

    content = read_text(eval_report)
    if not re.search(r"verdict\s*[:=]", content, re.IGNORECASE):
        return fail_line(ctx, "missing_verdict_in_eval")
    verdict_match = VALID_VERDICT.search(content)
    if not verdict_match:
        return fail_line(ctx, "invalid_verdict")
    if verdict_match.group(1).upper() not in TERMINAL_PASS:
        return fail_line(ctx, "non_terminal_verdict")
    return 0


def validate_unit_test_report_contract(ctx: HookContext) -> int:
    report = ctx.file("UNIT_TEST_REPORT.md")
    log = ctx.file("test-output.log")
    failures = 0

    if not is_nonempty(report):
        return fail_line(ctx, "missing_unit_test_report")
    if not is_nonempty(log):
        failures += fail_line(ctx, "missing_test_output_log")

    content = read_text(report)
    required_sections = [
        "Test Plan",
        "Execution Summary",
        "Coverage Matrix",
        "Failure Analysis",
        "Fix Attempts",
        "Commands",
        "Handoff",
    ]
    for section in required_sections:
        if section not in content:
            failures += fail_line(ctx, "invalid_unit_test_report_missing_section", f" section={section!r}")

    if not re.search(r"verdict\W*[:=]", content, re.IGNORECASE):
        failures += fail_line(ctx, "missing_unit_test_verdict")
    else:
        verdict_match = UNIT_TEST_VERDICT.search(content)
        if not verdict_match:
            failures += fail_line(ctx, "invalid_unit_test_verdict")
        elif verdict_match.group(1).upper() not in UNIT_TEST_PASS:
            failures += fail_line(ctx, "non_terminal_unit_test_verdict")

    if "test-output.log" not in content:
        failures += fail_line(ctx, "missing_test_log_reference")
    if not re.search(r"\|\s*Source\s*\|\s*Requirement\s*\|\s*Test\s*\|\s*Result\s*\|", content):
        failures += fail_line(ctx, "missing_coverage_matrix_table")
    if not re.search(r"\|\s*ID\s*\|\s*Classification\s*\|\s*Files Changed\s*\|", content):
        failures += fail_line(ctx, "missing_fix_attempts_table")
    return failures


VALIDATORS = {
    "proposal_contract": validate_proposal_contract,
    "specs_contract": validate_specs_contract,
    "design_contract": validate_design_contract,
    "plan_initial_tasks": validate_plan_initial_tasks,
    "plan_execution_contract": validate_plan_execution_contract,
    "plan_finished_tasks": validate_plan_finished_tasks,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
    "unit_test_report_contract": validate_unit_test_report_contract,
}


def validate_skill_config_schema(
    repo_root: Path,
    skill: str,
    *,
    workspace_root: Path | None = None,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
) -> None:
    config = load_artifact_config(
        repo_root,
        skill,
        workspace_root=workspace_root,
        workflow_profile=workflow_profile,
        workflow_decisions=workflow_decisions,
    )
    for validator in config.validators:
        if validator not in VALIDATORS:
            raise HookCheckError("unknown_validator", f"{skill}:{validator}")


def validate_config_schema(
    repo_root: Path,
    skill: str,
    *,
    workspace_root: Path | None = None,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
) -> None:
    if skill != "all":
        validate_skill_config_schema(
            repo_root,
            skill,
            workspace_root=workspace_root,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
        )
        return

    try:
        profiles = (
            configured_profile_names(load_board_config(repo_root / "board_core" / "board_config.json"))
            if workflow_profile == BASE_WORKFLOW_PROFILE
            else (workflow_profile,)
        )
    except BoardConfigError as error:
        raise HookCheckError("invalid_board_config", str(error)) from error

    try:
        for profile in profiles:
            contracts = load_repo_workflow_contracts(
                repo_root,
                workspace=workspace_root,
                profile=profile,
                workflow_decisions=workflow_decisions,
            )
            for contract in contracts.skill_contracts.values():
                for validator in contract.validators:
                    if validator not in VALIDATORS:
                        raise HookCheckError("unknown_validator", f"{contract.skill}:{validator}")
    except BoardConfigError as error:
        raise HookCheckError("invalid_board_config", str(error)) from error


def run_precheck(
    repo_root: Path,
    workspace_root: Path,
    skill: str,
    slug: str,
    *,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
    workflow_record: dict | None = None,
) -> tuple[int, str]:
    try:
        config = load_artifact_config(
            repo_root,
            skill,
            workspace_root=workspace_root,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
            workflow_record=workflow_record,
        )
        validate_required_files(workspace_root, slug, config.required_inputs)
    except HookCheckError as error:
        reason = f"{skill} precheck failed for {slug}: {error.reason}"
        if error.detail:
            reason = f"{reason} ({error.detail})"
        return 1, reason
    return 0, f"PRE_SKILL_PASS skill={skill}"


def run_postcheck(
    repo_root: Path,
    workspace_root: Path,
    skill: str,
    slug: str,
    *,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
    workflow_record: dict | None = None,
) -> tuple[int, str]:
    try:
        config = load_artifact_config(
            repo_root,
            skill,
            workspace_root=workspace_root,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
            workflow_record=workflow_record,
        )
        validate_required_files(workspace_root, slug, config.required_outputs)
        for validator in config.validators:
            if validator not in VALIDATORS:
                raise HookCheckError("unknown_validator", f"{skill}:{validator}")
    except HookCheckError as error:
        reason = f"{skill} postcheck failed for {slug}: {error.reason}"
        if error.detail:
            reason = f"{reason} ({error.detail})"
        return 1, reason

    ctx = HookContext(
        skill=skill,
        slug=slug,
        root=workspace_root,
        required_inputs=config.required_inputs,
        required_outputs=config.required_outputs,
    )
    failures = 0
    for validator in config.validators:
        failures += VALIDATORS[validator](ctx)
    if failures:
        return 1, f"POST_SKILL_FAIL skill={skill} failures={failures}"
    return 0, f"POST_SKILL_PASS skill={skill}"


def run_check(
    kind: str,
    repo_root: Path,
    workspace_root: Path,
    skill: str,
    slug: str,
    *,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
) -> int:
    if kind == "precheck":
        code, message = run_precheck(
            repo_root,
            workspace_root,
            skill,
            slug,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
        )
    elif kind == "postcheck":
        code, message = run_postcheck(
            repo_root,
            workspace_root,
            skill,
            slug,
            workflow_profile=workflow_profile,
            workflow_decisions=workflow_decisions,
        )
    else:
        print(f"UNKNOWN_CHECK kind={kind}", file=sys.stderr)
        return 1
    print(message)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Autodev artifact checks")
    parser.add_argument("kind", choices=("precheck", "postcheck", "schema"))
    parser.add_argument("skill")
    parser.add_argument("slug", nargs="?")
    parser.add_argument("--repo-root", default=str(repo_root_from_this_file()))
    parser.add_argument("--workspace-root", default=str(Path.cwd().resolve()))
    parser.add_argument("--workflow-profile", default=BASE_WORKFLOW_PROFILE)
    parser.add_argument(
        "--workflow-decision",
        action="append",
        default=[],
        help="workflow decision in stage=enabled|skipped form; may be repeated",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    workspace_root = Path(args.workspace_root).resolve()
    workflow_decisions: dict[str, str] = {}
    for raw_decision in args.workflow_decision:
        if "=" not in raw_decision:
            print(f"SCHEMA_FAIL skill={args.skill} reason=invalid_workflow_decision detail={raw_decision}")
            return 1
        stage_id, decision = raw_decision.split("=", 1)
        workflow_decisions[stage_id.strip()] = decision.strip()

    if args.kind == "schema":
        try:
            validate_config_schema(
                repo_root,
                args.skill,
                workspace_root=workspace_root,
                workflow_profile=args.workflow_profile,
                workflow_decisions=workflow_decisions,
            )
        except HookCheckError as error:
            detail = f" detail={error.detail}" if error.detail else ""
            print(f"SCHEMA_FAIL skill={args.skill} reason={error.reason}{detail}")
            return 1
        print(f"SCHEMA_PASS skill={args.skill}")
        return 0

    if not args.slug:
        print(f"{args.kind.upper()}_FAIL skill={args.skill} reason=missing_slug_argument")
        return 1
    return run_check(
        args.kind,
        repo_root,
        workspace_root,
        args.skill,
        args.slug,
        workflow_profile=args.workflow_profile,
        workflow_decisions=workflow_decisions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
