#!/usr/bin/env python3
"""Run Autodev artifact checks from board_config.json."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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
from board_core.contracts import (  # noqa: E402
    BoardConfigError,
    load_board_config,
    load_record_workflow_contracts,
    load_repo_workflow_contracts,
)
from board_core.state_store import load_state_json_records_result  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE, configured_profile_names  # noqa: E402
from hooks.evidence_integrity_gate import check_code_done, check_integrity, check_plan_evidence_refs  # noqa: E402
from hooks.evidence_store import EvidenceStoreError, read_records, stream_path, validate_detail_fields  # noqa: E402
from hooks.plan_json import (  # noqa: E402
    failed_tasks,
    load_and_validate_plan,
    plan_json_path,
    unfinished_tasks,
)
from hooks.code_task_context import resolve_task_refs  # noqa: E402
from hooks.plan_granularity import validate_plan_task_granularity_item  # noqa: E402
from hooks.resolve_frontend_html_route import (  # noqa: E402
    FrontendRouteError,
    ROUTE_ABSOLUTE,
    ROUTE_MISSING,
    ROUTE_NONE,
    ROUTE_SPEC_DRIVEN,
    ROUTE_STANDARD,
    evidence_path as frontend_evidence_path,
    read_json as read_frontend_json,
    resolve_frontend_route,
)
from hooks.ui_context import (  # noqa: E402
    UIContextError,
    load_ui_context,
    ui_context_path,
)

FRONTEND_REVIEW_PASS = {"passed", "has-suggestions", "skipped-by-user"}
UI_APPLICABILITIES = {"required", "not_applicable", "manual", "missing"}
E2E_ID = re.compile(r"\bE2E-[A-Za-z0-9_-]+-\d{3}\b")
REQ_ID = re.compile(r"\bREQ-\d{3}\b")
SCN_ID = re.compile(r"\bSCN-\d{3}\b")
TASK_ID = re.compile(r"\bT\d{3}\b")
EVIDENCE_ID = re.compile(r"\bev_\d{4}\b")
SMOKE_ID = re.compile(r"SMK-\d{3}")
SPEC_REQUIREMENT_DEF_RE = re.compile(r"^###\s+Requirement\s+\[(REQ-\d{3})\]:\s+.+$", re.MULTILINE)
SPEC_SCENARIO_DEF_RE = re.compile(r"^####\s+Scenario\s+\[(SCN-\d{3})\]:\s+.+$", re.MULTILINE)
DESIGN_API_DEF_RE = re.compile(r"^\|\s*(API-\d{3})\s*\|", re.MULTILINE)
DESIGN_DATA_DEF_RE = re.compile(r"^\|\s*(DATA-\d{3})\s*\|", re.MULTILINE)
DESIGN_DECISION_DEF_RE = re.compile(r"^\|\s*(D-\d{3})\s*\|", re.MULTILINE)
DETAIL_DESIGN_ID = re.compile(r"\bDD-\d{2,3}\b")
REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_TYPES = {"startup", "api", "ui", "cli", "migration", "health", "custom"}
SMOKE_SEAM_TYPES = {"startup", "api", "http", "ui", "cli", "job", "migration", "health", "custom"}
SMOKE_RESULTS = {"pass", "fail", "blocked", "skipped"}
SMOKE_VERDICTS = {"PASS", "FAIL", "BLOCKED", "SKIPPED", "NOT_APPLICABLE"}
SMOKE_SOURCE_PREFIXES = (
    "src/test/",
    "test/smoke/",
    "tests/smoke/",
    "scripts/smoke/",
    "e2e/smoke/",
    "cypress/e2e/smoke/",
    "playwright/smoke/",
)
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
def _spec_definition_index(text: str) -> tuple[dict[str, set[str]], list[str]]:
    req_ids = SPEC_REQUIREMENT_DEF_RE.findall(text)
    scn_ids = SPEC_SCENARIO_DEF_RE.findall(text)
    failures: list[str] = []
    if len(req_ids) != len(set(req_ids)):
        failures.append("duplicate_requirement_id")
    if len(scn_ids) != len(set(scn_ids)):
        failures.append("duplicate_scenario_id")
    return {"REQ": set(req_ids), "SCN": set(scn_ids)}, failures


def collect_spec_definition_index(ctx: HookContext) -> tuple[dict[str, set[str]], int]:
    failures = 0
    index = {"REQ": set(), "SCN": set()}
    for spec in spec_files(ctx):
        definitions, duplicate_reasons = _spec_definition_index(read_text(spec))
        rel = spec.relative_to(ctx.feature_dir)
        for reason in duplicate_reasons:
            failures += fail_line(ctx, reason, f" file={rel}")
        index["REQ"].update(definitions["REQ"])
        index["SCN"].update(definitions["SCN"])
    return index, failures


def _design_definition_index(text: str) -> tuple[dict[str, set[str]], list[str]]:
    api_ids = DESIGN_API_DEF_RE.findall(text)
    data_ids = DESIGN_DATA_DEF_RE.findall(text)
    decision_ids = DESIGN_DECISION_DEF_RE.findall(text)
    failures: list[str] = []
    if len(api_ids) != len(set(api_ids)):
        failures.append("duplicate_design_api_id")
    if len(data_ids) != len(set(data_ids)):
        failures.append("duplicate_design_data_id")
    if len(decision_ids) != len(set(decision_ids)):
        failures.append("duplicate_design_decision_id")
    return {"API": set(api_ids), "DATA": set(data_ids), "D": set(decision_ids)}, failures


def collect_design_definition_index(ctx: HookContext) -> tuple[dict[str, set[str]], int]:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return {"API": set(), "DATA": set(), "D": set()}, 0
    definitions, duplicate_reasons = _design_definition_index(read_text(design))
    failures = 0
    for reason in duplicate_reasons:
        failures += fail_line(ctx, reason)
    return definitions, failures


def load_json_artifact(ctx: HookContext, name: str, *, required: bool = True) -> tuple[dict | None, int]:
    path = ctx.file(name)
    if not is_nonempty(path):
        if not required:
            info(ctx, "json_artifact_missing_degrade", f" file={name}")
            return None, 0
        return None, fail_line(ctx, "missing_json_artifact", f" file={name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, fail_line(ctx, "invalid_json_artifact", f" file={name} detail={exc}")
    if not isinstance(data, dict):
        return None, fail_line(ctx, "invalid_json_artifact_root", f" file={name}")
    return data, 0


def _string_list_value(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return result


def _scenario_refs_from_spec_refs(spec_refs: list[str]) -> set[str]:
    return set(SCN_ID.findall(" ".join(spec_refs)))


def _check_scenario_ref_projection(
    ctx: HookContext,
    item: dict,
    spec_refs: list[str],
    *,
    context: str,
) -> int:
    failures = 0
    projected = _scenario_refs_from_spec_refs(spec_refs)
    for field in ("scenarioRef", "scenarioId"):
        value = item.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or value not in projected:
            failures += fail_line(ctx, "scenario_ref_not_projected_from_spec_refs", f" item={context} field={field} value={value}")
    return failures


def _known_plan_task_ids(ctx: HookContext) -> set[str]:
    plan, errors = load_and_validate_plan(plan_json_path(ctx.feature_dir))
    return set() if errors or plan is None else {str(task.get("id")) for task in plan.get("tasks", []) if isinstance(task, dict)}


def _load_plan_data(ctx: HookContext) -> dict | None:
    plan, errors = load_and_validate_plan(plan_json_path(ctx.feature_dir))
    return None if errors or plan is None else plan


def _plan_ui_task_ids(ctx: HookContext) -> set[str]:
    plan = _load_plan_data(ctx)
    if plan is None:
        return set()
    return {
        task["id"]
        for task in plan.get("tasks", [])
        if isinstance(task, dict)
        and isinstance(task.get("id"), str)
        and task.get("uiRequired") is True
    }


def _known_evidence_ids(ctx: HookContext) -> set[str]:
    try:
        return {
            evidence_id
            for record in read_records(stream_path(ctx.feature_dir))
            if isinstance((evidence_id := record.get("evidenceId")), str)
        }
    except EvidenceStoreError:
        return set()


def _evidence_records_by_id(ctx: HookContext) -> dict[str, dict]:
    try:
        return {
            evidence_id: record
            for record in read_records(stream_path(ctx.feature_dir))
            if isinstance((evidence_id := record.get("evidenceId")), str)
        }
    except EvidenceStoreError:
        return {}


def _evidence_stream_exists(ctx: HookContext) -> bool:
    return stream_path(ctx.feature_dir).is_file()


def _check_evidence_stream_for_refs(ctx: HookContext, evidence_ids: list[str], *, context: str) -> int:
    if not evidence_ids:
        return 0
    if not _evidence_stream_exists(ctx):
        return fail_line(ctx, "missing_evidence_stream_for_json_refs", f" item={context}")
    try:
        read_records(stream_path(ctx.feature_dir))
    except EvidenceStoreError as exc:
        return fail_line(ctx, "invalid_evidence_stream_for_json_refs", f" item={context} detail={exc}")
    return 0


def _check_string_field(ctx: HookContext, item: dict, field: str, *, context: str, required: bool = True) -> int:
    value = item.get(field)
    if value is None and not required:
        return 0
    if not isinstance(value, str) or not value.strip():
        return fail_line(ctx, "invalid_json_field", f" item={context} field={field}")
    return 0


def _check_bool_field(ctx: HookContext, item: dict, field: str, *, context: str, required: bool = True) -> int:
    value = item.get(field)
    if value is None and not required:
        return 0
    if not isinstance(value, bool):
        return fail_line(ctx, "invalid_json_field", f" item={context} field={field}")
    return 0


def _check_string_array_field(
    ctx: HookContext,
    item: dict,
    field: str,
    *,
    context: str,
    required: bool = True,
    allow_empty: bool = False,
    item_re: re.Pattern[str] | None = None,
) -> tuple[list[str], int]:
    value = item.get(field)
    if value is None and not required:
        return [], 0
    values = _string_list_value(value)
    if values is None:
        return [], fail_line(ctx, "invalid_json_array_field", f" item={context} field={field}")
    failures = 0
    if required and not allow_empty and not values:
        failures += fail_line(ctx, "missing_json_array_items", f" item={context} field={field}")
    if item_re is not None:
        for entry in values:
            if not item_re.fullmatch(entry):
                failures += fail_line(ctx, "invalid_json_array_item", f" item={context} field={field} value={entry}")
    return values, failures


def _check_trace_refs(
    ctx: HookContext,
    item: dict,
    *,
    context: str,
    require_task: bool = False,
    require_evidence: bool = False,
    require_spec_refs: bool = True,
) -> tuple[list[str], list[str], int]:
    failures = 0
    known_tasks = _known_plan_task_ids(ctx)
    known_evidence = _known_evidence_ids(ctx)
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures

    task_id = item.get("taskId")
    if task_id is None and not require_task:
        pass
    elif not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        failures += fail_line(ctx, "invalid_json_task_id", f" item={context} taskId={task_id}")
    elif known_tasks and task_id not in known_tasks:
        failures += fail_line(ctx, "unknown_json_task_id", f" item={context} taskId={task_id}")

    spec_refs, spec_ref_failures = _check_string_array_field(
        ctx,
        item,
        "specRefs",
        context=context,
        required=require_spec_refs,
    )
    failures += spec_ref_failures
    req_refs = set(REQ_ID.findall(" ".join(spec_refs)))
    scenario_refs = _scenario_refs_from_spec_refs(spec_refs)
    if spec_refs and not req_refs:
        failures += fail_line(ctx, "missing_json_requirement_ref", f" item={context}")
    if spec_refs and not scenario_refs:
        failures += fail_line(ctx, "missing_json_scenario_ref", f" item={context}")
    for req_id in sorted(req_refs):
        if req_id not in spec_ids["REQ"]:
            failures += fail_line(ctx, "unknown_json_requirement_ref", f" item={context} id={req_id}")
    for scn_id in sorted(scenario_refs):
        if scn_id not in spec_ids["SCN"]:
            failures += fail_line(ctx, "unknown_json_scenario_ref", f" item={context} id={scn_id}")

    evidence_ids, evidence_failures = _check_string_array_field(
        ctx,
        item,
        "evidenceIds",
        context=context,
        required=require_evidence,
        item_re=EVIDENCE_ID,
    )
    failures += evidence_failures
    if require_evidence:
        failures += _check_evidence_stream_for_refs(ctx, evidence_ids, context=context)
    for evidence_id in evidence_ids:
        if known_evidence and evidence_id not in known_evidence:
            failures += fail_line(ctx, "unknown_json_evidence_id", f" item={context} evidenceId={evidence_id}")
    failures += _check_scenario_ref_projection(ctx, item, spec_refs, context=context)
    return spec_refs, evidence_ids, failures


def _smoke_source_path_allowed(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or normalized.endswith("/"):
        return False
    if any(part == ".." for part in normalized.split("/")):
        return False
    return any(normalized.startswith(prefix) for prefix in SMOKE_SOURCE_PREFIXES)


def _git_repo_root(root: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return Path(value).resolve() if value else None


def _git_path_state(root: Path, source_path: str) -> tuple[bool, bool] | None:
    repo_root = _git_repo_root(root)
    if repo_root is None:
        return None
    absolute_path = (root / source_path).resolve()
    try:
        relative_path = absolute_path.relative_to(repo_root)
    except ValueError:
        return None
    rel = relative_path.as_posix()
    try:
        tracked = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", rel],
            text=True,
            capture_output=True,
            check=False,
        ).returncode == 0
        ignored = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "--quiet", "--no-index", "--", rel],
            text=True,
            capture_output=True,
            check=False,
        ).returncode == 0
    except OSError:
        return None
    return tracked, ignored


def _check_smoke_source_git_ignored(ctx: HookContext, source_path: str, *, test_id: str) -> int:
    state = _git_path_state(ctx.root, source_path)
    if state is None:
        info(ctx, "smoke_source_git_ignore_check_skipped", f" id={test_id} path={source_path}")
        return 0
    tracked, ignored = state
    failures = 0
    if tracked:
        failures += fail_line(ctx, "tracked_smoke_source_file", f" id={test_id} path={source_path}")
    if not ignored:
        failures += fail_line(ctx, "unignored_smoke_source_file", f" id={test_id} path={source_path}")
    return failures


def _load_ui_context_for_json_checks(ctx: HookContext) -> tuple[dict | None, int]:
    if not ctx.requires_artifact("UI_CONTEXT.json") and not is_nonempty(ui_context_path(ctx.feature_dir)):
        return None, 0
    try:
        data = load_ui_context(ctx.feature_dir)
    except UIContextError as exc:
        return None, fail_line(ctx, "invalid_ui_context_json", f" detail={exc}")
    if data is None:
        if ctx.requires_artifact("UI_CONTEXT.json"):
            return None, fail_line(ctx, "missing_json_artifact", " file=UI_CONTEXT.json")
        return None, 0
    return data, 0


def _ui_scenario_refs(ui_data: dict | None) -> set[str]:
    if not isinstance(ui_data, dict) or ui_data.get("uiRequired") is not True:
        return set()
    refs: set[str] = set()
    for field in ("capabilities", "interactions"):
        values = ui_data.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            if field == "capabilities" and item.get("uiRequired") is False:
                continue
            spec_refs = item.get("specRefs")
            if isinstance(spec_refs, list):
                refs.update(_scenario_refs_from_spec_refs([ref for ref in spec_refs if isinstance(ref, str)]))
    return refs


def _item_maps_to_ui(
    *,
    task_id: object,
    spec_refs: list[str],
    ui_scenarios: set[str],
    ui_task_ids: set[str],
) -> bool:
    return (
        isinstance(task_id, str)
        and task_id in ui_task_ids
    ) or bool(_scenario_refs_from_spec_refs(spec_refs) & ui_scenarios)


def _check_ui_ref_field(
    ctx: HookContext,
    item: dict,
    field: str,
    *,
    context: str,
    known_refs: set[str],
    required: bool,
) -> tuple[list[str], int]:
    refs, failures = _check_string_array_field(
        ctx,
        item,
        field,
        context=context,
        required=required,
        allow_empty=not required,
    )
    for ref in refs:
        if ref not in known_refs:
            failures += fail_line(ctx, "unknown_json_ui_ref", f" item={context} field={field} ref={ref}")
    return refs, failures


def _check_ui_projection(
    ctx: HookContext,
    item: dict,
    *,
    context: str,
    spec_refs: list[str],
    ui_data: dict | None,
    require_ui_required_when_mapped: bool,
    require_refs_when_ui: bool,
    validate_ref_fields: bool,
) -> int:
    if ui_data is None:
        return 0

    failures = 0
    ui_required_value = item.get("uiRequired")
    if ui_required_value is not None and not isinstance(ui_required_value, bool):
        failures += fail_line(ctx, "invalid_json_field", f" item={context} field=uiRequired")

    feature_ui_required = ui_data.get("uiRequired") is True
    ui_scenarios = _ui_scenario_refs(ui_data)
    ui_task_ids = _plan_ui_task_ids(ctx)
    maps_to_ui = _item_maps_to_ui(
        task_id=item.get("taskId"),
        spec_refs=spec_refs,
        ui_scenarios=ui_scenarios,
        ui_task_ids=ui_task_ids,
    )

    if not feature_ui_required:
        if ui_required_value is True:
            failures += fail_line(ctx, "ui_required_true_when_feature_not_ui", f" item={context}")
        for field in ("pageRefs", "interactionRefs", "visualSourceRefs"):
            refs = _string_list_value(item.get(field)) if field in item else []
            if refs:
                failures += fail_line(ctx, "ui_refs_when_feature_not_ui", f" item={context} field={field}")
        return failures

    if require_ui_required_when_mapped and maps_to_ui and ui_required_value is not True:
        failures += fail_line(ctx, "missing_json_ui_required_projection", f" item={context}")
    if ui_required_value is False and maps_to_ui:
        failures += fail_line(ctx, "json_ui_required_false_for_ui_item", f" item={context}")
    if ui_required_value is True and not maps_to_ui:
        failures += fail_line(ctx, "json_ui_required_true_for_non_ui_item", f" item={context}")

    if not validate_ref_fields:
        return failures

    indexes = ui_context_indexes(ui_data)
    should_require_refs = require_refs_when_ui and ui_required_value is True
    for field, known in (
        ("pageRefs", indexes["page"]),
        ("interactionRefs", indexes["interaction"]),
        ("visualSourceRefs", indexes["visualSource"]),
    ):
        required = should_require_refs and field in {"pageRefs", "interactionRefs"}
        _, field_failures = _check_ui_ref_field(
            ctx,
            item,
            field,
            context=context,
            known_refs=known,
            required=required,
        )
        failures += field_failures
    return failures


def _check_smoke_scenario_refs(
    ctx: HookContext,
    item: dict,
    *,
    context: str,
    spec_ids: dict[str, set[str]],
) -> int:
    values, failures = _check_string_array_field(
        ctx,
        item,
        "scenarioRefs",
        context=context,
        required=True,
    )
    if not values:
        return failures
    if len(values) != 1:
        failures += fail_line(ctx, "invalid_smoke_vertical_slice_scope", f" item={context} field=scenarioRefs")
    scenario_ids: set[str] = set()
    for value in values:
        found = set(SCN_ID.findall(value))
        if not found:
            failures += fail_line(ctx, "missing_smoke_scenario_ref", f" item={context} value={value}")
        scenario_ids.update(found)
    if len(scenario_ids) != 1:
        failures += fail_line(ctx, "invalid_smoke_vertical_slice_scope", f" item={context} scenarioCount={len(scenario_ids)}")
    for scenario_id in sorted(scenario_ids):
        if scenario_id not in spec_ids["SCN"]:
            failures += fail_line(ctx, "unknown_smoke_scenario_ref", f" item={context} id={scenario_id}")
    return failures


def _check_smoke_tdd_contract(ctx: HookContext, item: dict, *, context: str) -> int:
    failures = 0
    seam = item.get("seam")
    if not isinstance(seam, dict):
        failures += fail_line(ctx, "missing_smoke_seam", f" item={context}")
    else:
        seam_type = seam.get("type")
        if not isinstance(seam_type, str) or seam_type.strip().lower() not in SMOKE_SEAM_TYPES:
            failures += fail_line(ctx, "invalid_smoke_seam_type", f" item={context}")
        failures += _check_string_field(ctx, seam, "entrypoint", context=f"{context}.seam")
        failures += _check_string_field(ctx, seam, "observable", context=f"{context}.seam")

    vertical_slice = item.get("verticalSlice")
    if not isinstance(vertical_slice, dict):
        failures += fail_line(ctx, "missing_smoke_vertical_slice", f" item={context}")
    else:
        failures += _check_string_field(ctx, vertical_slice, "trigger", context=f"{context}.verticalSlice")
        failures += _check_string_field(ctx, vertical_slice, "expectedOutcome", context=f"{context}.verticalSlice")

    mock_policy = item.get("mockPolicy")
    if not isinstance(mock_policy, dict):
        failures += fail_line(ctx, "missing_smoke_mock_policy", f" item={context}")
    else:
        if mock_policy.get("externalOnly") is not True:
            failures += fail_line(ctx, "invalid_smoke_mock_policy", f" item={context}")
        _, mock_failures = _check_string_array_field(
            ctx,
            mock_policy,
            "allowedMocks",
            context=f"{context}.mockPolicy",
            required=False,
            allow_empty=True,
        )
        failures += mock_failures
    return failures


def _smoke_plan_tests(ctx: HookContext) -> tuple[dict[str, dict], int]:
    data, failures = load_json_artifact(ctx, "SMOKE_TEST_PLAN.json", required=False)
    if data is None:
        return {}, failures
    tests = data.get("tests")
    if not isinstance(tests, list):
        return {}, failures + fail_line(ctx, "invalid_smoke_test_plan_items")
    result: dict[str, dict] = {}
    for index, item in enumerate(tests):
        if not isinstance(item, dict):
            failures += fail_line(ctx, "invalid_smoke_test_item", f" item=tests[{index}]")
            continue
        test_id = item.get("id")
        if isinstance(test_id, str) and SMOKE_ID.fullmatch(test_id):
            result[test_id] = item
    return result, failures


def _scenario_covering_evidence(ctx: HookContext) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    try:
        records = read_records(stream_path(ctx.feature_dir))
    except EvidenceStoreError:
        return result
    for record in records:
        evidence_id = record.get("evidenceId")
        spec_refs = record.get("specRefs")
        if not isinstance(evidence_id, str) or not isinstance(spec_refs, list):
            continue
        scenario_refs = _scenario_refs_from_spec_refs([ref for ref in spec_refs if isinstance(ref, str)])
        for scenario_ref in scenario_refs:
            result.setdefault(scenario_ref, set()).add(evidence_id)
    return result


def _e2e_scenario_covering_evidence(ctx: HookContext) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    try:
        records = read_records(stream_path(ctx.feature_dir))
    except EvidenceStoreError:
        return result
    for record in records:
        evidence_id = record.get("evidenceId")
        spec_refs = record.get("specRefs")
        node_id = record.get("nodeId")
        skill = record.get("skill")
        if not isinstance(evidence_id, str) or not isinstance(spec_refs, list):
            continue
        if node_id != "dev.e2e" and skill != "autodev-e2e":
            continue
        scenario_refs = _scenario_refs_from_spec_refs([ref for ref in spec_refs if isinstance(ref, str)])
        for scenario_ref in scenario_refs:
            result.setdefault(scenario_ref, set()).add(evidence_id)
    return result


def _validate_scenario_coverage(
    ctx: HookContext,
    data: dict,
    *,
    field: str,
    required: bool,
    require_pass_evidence: bool,
    covering_evidence: dict[str, set[str]] | None = None,
    spec_ids: dict[str, set[str]] | None = None,
    ui_data: dict | None = None,
    validate_ui_applicability: bool = False,
) -> int:
    failures = 0
    if spec_ids is None:
        spec_ids, spec_failures = collect_spec_definition_index(ctx)
        failures += spec_failures
    defined_scenarios = set(spec_ids["SCN"])
    matrix = data.get(field)
    if matrix is None:
        if required:
            return failures + fail_line(ctx, "missing_scenario_coverage", f" field={field}")
        return failures
    if not isinstance(matrix, list):
        return failures + fail_line(ctx, "invalid_scenario_coverage", f" field={field}")

    seen_scenarios: set[str] = set()
    known_evidence = _known_evidence_ids(ctx)
    evidence_by_scenario = covering_evidence if covering_evidence is not None else _scenario_covering_evidence(ctx)
    allowed_verdicts = {"pass", "fail", "manual", "missing"}
    ui_scenarios = _ui_scenario_refs(ui_data) if validate_ui_applicability else set()
    feature_ui_required = ui_data.get("uiRequired") is True if ui_data is not None else False
    for index, row in enumerate(matrix):
        context = f"{field}[{index}]"
        if not isinstance(row, dict):
            failures += fail_line(ctx, "invalid_scenario_coverage_row", f" item={context}")
            continue
        scenario_ref = row.get("scenarioRef")
        if not isinstance(scenario_ref, str) or scenario_ref not in defined_scenarios:
            failures += fail_line(ctx, "unknown_scenario_coverage_ref", f" item={context} id={scenario_ref}")
            continue
        if scenario_ref in seen_scenarios:
            failures += fail_line(ctx, "duplicate_scenario_coverage_row", f" item={context} id={scenario_ref}")
        seen_scenarios.add(scenario_ref)
        row_verdict = row.get("verdict")
        normalized_verdict = row_verdict.lower() if isinstance(row_verdict, str) else ""
        if normalized_verdict not in allowed_verdicts:
            failures += fail_line(ctx, "invalid_scenario_coverage_verdict", f" item={context}")
        row_evidence, row_evidence_failures = _check_string_array_field(
            ctx,
            row,
            "evidenceIds",
            context=context,
            required=normalized_verdict == "pass" and require_pass_evidence,
            item_re=EVIDENCE_ID,
        )
        failures += row_evidence_failures
        failures += _check_evidence_stream_for_refs(ctx, row_evidence, context=context)
        for evidence_id in row_evidence:
            if known_evidence and evidence_id not in known_evidence:
                failures += fail_line(ctx, "unknown_scenario_coverage_evidence_id", f" item={context} evidenceId={evidence_id}")
        if normalized_verdict == "pass" and require_pass_evidence:
            covering_ids = evidence_by_scenario.get(scenario_ref, set())
            if not row_evidence:
                failures += fail_line(ctx, "scenario_coverage_pass_without_evidence", f" item={context} id={scenario_ref}")
            elif not any(evidence_id in covering_ids for evidence_id in row_evidence):
                failures += fail_line(ctx, "scenario_coverage_pass_evidence_mismatch", f" item={context} id={scenario_ref}")
        if validate_ui_applicability:
            applicability = row.get("uiApplicability")
            if not isinstance(applicability, str) or applicability not in UI_APPLICABILITIES:
                failures += fail_line(ctx, "invalid_scenario_coverage_ui_applicability", f" item={context}")
            elif ui_data is not None:
                scenario_is_ui = scenario_ref in ui_scenarios
                if not feature_ui_required and applicability != "not_applicable":
                    failures += fail_line(ctx, "invalid_scenario_coverage_ui_applicability", f" item={context} expected=not_applicable")
                elif feature_ui_required and scenario_is_ui:
                    expected = normalized_verdict if normalized_verdict in {"manual", "missing"} else "required"
                    if applicability != expected:
                        failures += fail_line(ctx, "invalid_scenario_coverage_ui_applicability", f" item={context} expected={expected}")
                elif feature_ui_required and not scenario_is_ui and applicability != "not_applicable":
                    failures += fail_line(ctx, "invalid_scenario_coverage_ui_applicability", f" item={context} expected=not_applicable")

    missing_rows = defined_scenarios - seen_scenarios
    if missing_rows:
        failures += fail_line(ctx, "missing_scenario_coverage_rows", f" field={field} ids={','.join(sorted(missing_rows))}")
    return failures


def _check_verify_ui_summary(
    ctx: HookContext,
    data: dict,
    *,
    ui_data: dict | None,
) -> int:
    if ui_data is None:
        return 0
    failures = 0
    ui_summary = data.get("uiSummary")
    if not isinstance(ui_summary, dict):
        return fail_line(ctx, "missing_verify_ui_summary")
    feature_ui_required = ui_data.get("uiRequired") is True
    if ui_summary.get("uiRequired") is not feature_ui_required:
        failures += fail_line(ctx, "verify_ui_summary_required_mismatch")

    fields = {
        "passedUiScenarioRefs": set(data.get("passedScenarioRefs", [])) if isinstance(data.get("passedScenarioRefs"), list) else set(),
        "failedUiScenarioRefs": set(data.get("failedScenarioRefs", [])) if isinstance(data.get("failedScenarioRefs"), list) else set(),
        "manualUiScenarioRefs": set(data.get("manualVerificationRefs", [])) if isinstance(data.get("manualVerificationRefs"), list) else set(),
        "missingUiScenarioRefs": set(data.get("missingScenarioRefs", [])) if isinstance(data.get("missingScenarioRefs"), list) else set(),
    }
    ui_scenarios = _ui_scenario_refs(ui_data)
    not_applicable_expected = set()
    matrix = data.get("scenarioCoverage")
    if isinstance(matrix, list):
        for row in matrix:
            if not isinstance(row, dict):
                continue
            scenario_ref = row.get("scenarioRef")
            if isinstance(scenario_ref, str) and scenario_ref not in ui_scenarios:
                not_applicable_expected.add(scenario_ref)

    for field, decision_refs in fields.items():
        refs, ref_failures = _check_string_array_field(
            ctx,
            ui_summary,
            field,
            context="VERIFY_DECISION.uiSummary",
            required=True,
            allow_empty=True,
        )
        failures += ref_failures
        ref_set = set(refs)
        if feature_ui_required:
            expected = decision_refs & ui_scenarios
            if ref_set != expected:
                failures += fail_line(ctx, "verify_ui_summary_decision_mismatch", f" field={field}")
        elif ref_set:
            failures += fail_line(ctx, "verify_ui_summary_when_feature_not_ui", f" field={field}")

    not_applicable, not_applicable_failures = _check_string_array_field(
        ctx,
        ui_summary,
        "notApplicableScenarioRefs",
        context="VERIFY_DECISION.uiSummary",
        required=True,
        allow_empty=True,
    )
    failures += not_applicable_failures
    if set(not_applicable) != not_applicable_expected:
        failures += fail_line(ctx, "verify_ui_summary_not_applicable_mismatch")
    return failures


def _check_verify_ui_pass_evidence(
    ctx: HookContext,
    data: dict,
    *,
    ui_data: dict | None,
) -> int:
    if ui_data is None or ui_data.get("uiRequired") is not True:
        return 0
    failures = 0
    ui_scenarios = _ui_scenario_refs(ui_data)
    if not ui_scenarios:
        return 0
    e2e_evidence = _e2e_scenario_covering_evidence(ctx)
    manual_refs = set(data.get("manualVerificationRefs", [])) if isinstance(data.get("manualVerificationRefs"), list) else set()
    matrix = data.get("scenarioCoverage")
    if not isinstance(matrix, list):
        return 0
    for index, row in enumerate(matrix):
        if not isinstance(row, dict):
            continue
        scenario_ref = row.get("scenarioRef")
        verdict = row.get("verdict")
        if not isinstance(scenario_ref, str) or scenario_ref not in ui_scenarios:
            continue
        if isinstance(verdict, str) and verdict.lower() == "pass":
            evidence_ids = _string_list_value(row.get("evidenceIds")) or []
            if not any(evidence_id in e2e_evidence.get(scenario_ref, set()) for evidence_id in evidence_ids):
                failures += fail_line(ctx, "verify_ui_pass_without_e2e_evidence", f" item=scenarioCoverage[{index}] id={scenario_ref}")
        elif isinstance(verdict, str) and verdict.lower() == "manual" and scenario_ref not in manual_refs:
            failures += fail_line(ctx, "verify_ui_manual_decision_mismatch", f" item=scenarioCoverage[{index}] id={scenario_ref}")
    return failures


def _check_failed_ui_refs(ctx: HookContext, data: dict, *, ui_data: dict | None) -> int:
    value = data.get("failedUiRefs")
    if value is None:
        return 0
    if not isinstance(value, dict):
        return fail_line(ctx, "invalid_json_field", " item=FIX_REQUEST field=failedUiRefs")
    if ui_data is None:
        return fail_line(ctx, "ui_projection_without_ui_context", " item=FIX_REQUEST.failedUiRefs")
    failures = 0
    indexes = ui_context_indexes(ui_data)
    for field, known in (
        ("pageRefs", indexes["page"]),
        ("interactionRefs", indexes["interaction"]),
        ("visualSourceRefs", indexes["visualSource"]),
    ):
        _, field_failures = _check_ui_ref_field(
            ctx,
            value,
            field,
            context="FIX_REQUEST.failedUiRefs",
            known_refs=known,
            required=False,
        )
        failures += field_failures
    return failures


def _known_design_refs(ctx: HookContext) -> set[str]:
    design_ids, _ = collect_design_definition_index(ctx)
    refs = {
        f"design.md#{item}"
        for kind in ("API", "DATA", "D")
        for item in design_ids[kind]
    }
    detail = ctx.file("DETAIL_DESIGN.md")
    if is_nonempty(detail):
        refs.update(f"DETAIL_DESIGN.md#{item}" for item in DETAIL_DESIGN_ID.findall(read_text(detail)))
    return refs


def _effective_needs_fix_targets(ctx: HookContext) -> set[str]:
    result = load_state_json_records_result(ctx.root)
    if result.exists and not result.errors:
        record = result.records.get(ctx.slug)
        if record is not None:
            try:
                return set(load_record_workflow_contracts(REPO_ROOT, record, workspace=ctx.root).allowed_next.get("needs_fix", frozenset()))
            except BoardConfigError:
                pass
    try:
        return set(load_repo_workflow_contracts(REPO_ROOT, workspace=ctx.root).allowed_next.get("needs_fix", frozenset()))
    except BoardConfigError:
        return set()


def _check_verify_scenario_decisions(
    ctx: HookContext,
    data: dict,
    *,
    defined_scenarios: set[str],
    passed: list[str],
    failed: list[str],
    manual: list[str],
    missing: list[str],
) -> int:
    failures = 0
    passed_set = set(passed)
    failed_set = set(failed)
    manual_set = set(manual)
    missing_set = set(missing)
    overlaps = (
        (passed_set & failed_set)
        | (passed_set & manual_set)
        | (passed_set & missing_set)
        | (failed_set & manual_set)
        | (failed_set & missing_set)
        | (manual_set & missing_set)
    )
    if overlaps:
        failures += fail_line(ctx, "duplicate_verify_scenario_decision", f" ids={','.join(sorted(overlaps))}")

    decided = passed_set | failed_set | manual_set | missing_set
    missing_decisions = defined_scenarios - decided
    if missing_decisions:
        failures += fail_line(ctx, "missing_verify_scenario_decision", f" ids={','.join(sorted(missing_decisions))}")

    verdict = data.get("verdict")
    if isinstance(verdict, str):
        normalized_verdict = verdict.lower()
        if normalized_verdict == "pass" and (failed_set or manual_set or missing_set or missing_decisions):
            failures += fail_line(ctx, "invalid_verify_decision_summary")
        if normalized_verdict == "fail" and not (failed_set or missing_set):
            failures += fail_line(ctx, "invalid_verify_decision_summary")
        if normalized_verdict == "manual" and not manual_set:
            failures += fail_line(ctx, "invalid_verify_decision_summary")

    matrix = data.get("scenarioCoverage")
    if not isinstance(matrix, list):
        return failures
    for index, row in enumerate(matrix):
        context = f"scenarioCoverage[{index}]"
        if not isinstance(row, dict):
            continue
        scenario_ref = row.get("scenarioRef")
        row_verdict = row.get("verdict")
        if not isinstance(scenario_ref, str) or scenario_ref not in defined_scenarios or not isinstance(row_verdict, str):
            continue
        normalized_row_verdict = row_verdict.lower()
        mismatch = False
        if normalized_row_verdict == "pass":
            mismatch = scenario_ref not in passed_set
        elif normalized_row_verdict == "fail":
            mismatch = scenario_ref not in failed_set
        elif normalized_row_verdict == "manual":
            mismatch = scenario_ref not in manual_set
        elif normalized_row_verdict == "missing":
            mismatch = scenario_ref not in missing_set
        if mismatch:
            failures += fail_line(ctx, "verify_scenario_coverage_decision_mismatch", f" item={context} id={scenario_ref}")
    return failures


def validate_review_findings_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "REVIEW_FINDINGS.json",
        required=ctx.requires_artifact("REVIEW_FINDINGS.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_review_findings_version")
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.upper() not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "DEGRADED"}:
        failures += fail_line(ctx, "invalid_review_findings_verdict")
    elif ctx.requires_artifact("REVIEW_FINDINGS.json") and verdict.upper() not in TERMINAL_PASS:
        failures += fail_line(ctx, "non_terminal_review_findings_verdict")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return failures + fail_line(ctx, "invalid_review_findings_items")
    ui_data, ui_failures = _load_ui_context_for_json_checks(ctx)
    failures += ui_failures
    severities = {"blocker", "high", "medium", "low", "info", "minor", "important"}
    for index, finding in enumerate(findings):
        context = f"findings[{index}]"
        if not isinstance(finding, dict):
            failures += fail_line(ctx, "invalid_review_finding", f" item={context}")
            continue
        failures += _check_string_field(ctx, finding, "id", context=context)
        failures += _check_string_field(ctx, finding, "message", context=context)
        severity = finding.get("severity")
        if not isinstance(severity, str) or severity.strip().lower() not in severities:
            failures += fail_line(ctx, "invalid_review_finding_severity", f" item={context}")
        spec_refs, _, trace_failures = _check_trace_refs(ctx, finding, context=context, require_task=True, require_evidence=True)
        failures += trace_failures
        failures += _check_ui_projection(
            ctx,
            finding,
            context=context,
            spec_refs=spec_refs,
            ui_data=ui_data,
            require_ui_required_when_mapped=True,
            require_refs_when_ui=True,
            validate_ref_fields=True,
        )
        suggested = finding.get("suggestedCheckpoint")
        if suggested is not None and (not isinstance(suggested, str) or not suggested.strip()):
            failures += fail_line(ctx, "invalid_json_field", f" item={context} field=suggestedCheckpoint")
    return failures


def validate_unit_test_result_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "UNIT_TEST_RESULT.json",
        required=ctx.requires_artifact("UNIT_TEST_RESULT.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_unit_test_result_version")
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.upper() not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED"}:
        failures += fail_line(ctx, "invalid_unit_test_result_verdict")
    elif ctx.requires_artifact("UNIT_TEST_RESULT.json") and verdict.upper() not in TERMINAL_PASS:
        failures += fail_line(ctx, "non_terminal_unit_test_result_verdict")
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        return failures + fail_line(ctx, "invalid_unit_test_targets")
    ui_data, ui_failures = _load_ui_context_for_json_checks(ctx)
    failures += ui_failures
    for index, target in enumerate(targets):
        context = f"targets[{index}]"
        if not isinstance(target, dict):
            failures += fail_line(ctx, "invalid_unit_test_target", f" item={context}")
            continue
        failures += _check_string_field(ctx, target, "targetId", context=context)
        spec_refs, _, trace_failures = _check_trace_refs(ctx, target, context=context, require_task=True, require_evidence=True)
        failures += trace_failures
        failures += _check_ui_projection(
            ctx,
            target,
            context=context,
            spec_refs=spec_refs,
            ui_data=ui_data,
            require_ui_required_when_mapped=True,
            require_refs_when_ui=False,
            validate_ref_fields=False,
        )
        result = target.get("result")
        if not isinstance(result, str) or result.upper() not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED", "SKIP"}:
            failures += fail_line(ctx, "invalid_unit_test_target_result", f" item={context}")
        elif (
            isinstance(verdict, str)
            and verdict.upper() in TERMINAL_PASS
            and result.upper() in {"FAIL", "BLOCKED"}
        ):
            failures += fail_line(ctx, "unit_test_result_summary_mismatch", f" item={context}")
        failures += _check_string_field(ctx, target, "command", context=context)
        coverage = target.get("coverage")
        if coverage is not None and not isinstance(coverage, (dict, list, int, float, str)):
            failures += fail_line(ctx, "invalid_json_field", f" item={context} field=coverage")
    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
    )
    return failures


def validate_e2e_result_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "E2E_RESULT.json",
        required=ctx.requires_artifact("E2E_RESULT.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_e2e_result_version")
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.upper() not in {"PASS", "PASS_WITH_WARNINGS", "FAIL", "BLOCKED"}:
        failures += fail_line(ctx, "invalid_e2e_result_summary_verdict")
    elif ctx.requires_artifact("E2E_RESULT.json") and verdict.upper() not in TERMINAL_PASS:
        failures += fail_line(ctx, "non_terminal_e2e_result_verdict")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return failures + fail_line(ctx, "invalid_e2e_result_cases")
    ui_data, ui_failures = _load_ui_context_for_json_checks(ctx)
    failures += ui_failures
    for index, case in enumerate(cases):
        context = f"cases[{index}]"
        if not isinstance(case, dict):
            failures += fail_line(ctx, "invalid_e2e_result_case", f" item={context}")
            continue
        failures += _check_string_field(ctx, case, "caseId", context=context)
        case_id = case.get("caseId")
        if isinstance(case_id, str) and not E2E_ID.fullmatch(case_id):
            failures += fail_line(ctx, "invalid_e2e_result_case_id", f" item={context}")
        spec_refs, _, trace_failures = _check_trace_refs(ctx, case, context=context, require_task=True, require_evidence=True)
        failures += trace_failures
        failures += _check_ui_projection(
            ctx,
            case,
            context=context,
            spec_refs=spec_refs,
            ui_data=ui_data,
            require_ui_required_when_mapped=True,
            require_refs_when_ui=True,
            validate_ref_fields=True,
        )
        failures += _check_string_field(ctx, case, "executionMode", context=context)
        steps = case.get("steps")
        if not isinstance(steps, list):
            failures += fail_line(ctx, "invalid_json_array_field", f" item={context} field=steps")
        verdict = case.get("verdict")
        if not isinstance(verdict, str) or verdict.upper() not in {"PASS", "FAIL", "BLOCKED", "SKIP"}:
            failures += fail_line(ctx, "invalid_e2e_result_verdict", f" item={context}")
        elif isinstance(data.get("verdict"), str) and data["verdict"].upper() in TERMINAL_PASS and verdict.upper() in {"FAIL", "BLOCKED"}:
            failures += fail_line(ctx, "e2e_result_summary_mismatch", f" item={context}")
    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
    )
    return failures


def validate_verify_decision_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "VERIFY_DECISION.json",
        required=ctx.requires_artifact("VERIFY_DECISION.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_verify_decision_version")
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.lower() not in {"pass", "fail", "manual"}:
        failures += fail_line(ctx, "invalid_verify_decision_verdict")
    next_checkpoint = data.get("nextCheckpoint")
    if not isinstance(next_checkpoint, str) or next_checkpoint not in {"verify_done", "needs_fix", "verify_in_progress"}:
        failures += fail_line(ctx, "invalid_verify_next_checkpoint")
    elif isinstance(verdict, str):
        normalized_verdict = verdict.lower()
        if normalized_verdict == "pass" and next_checkpoint != "verify_done":
            failures += fail_line(ctx, "invalid_verify_decision_transition")
        if normalized_verdict == "fail" and next_checkpoint != "needs_fix":
            failures += fail_line(ctx, "invalid_verify_decision_transition")
        if normalized_verdict == "manual" and next_checkpoint not in {"verify_in_progress", "needs_fix"}:
            failures += fail_line(ctx, "invalid_verify_decision_transition")

    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures
    defined_scenarios = set(spec_ids["SCN"])
    covered_by_evidence = _scenario_covering_evidence(ctx)
    known_evidence = _known_evidence_ids(ctx)
    ui_data, ui_failures = _load_ui_context_for_json_checks(ctx)
    failures += ui_failures

    passed, passed_failures = _check_string_array_field(
        ctx,
        data,
        "passedScenarioRefs",
        context="VERIFY_DECISION",
        required=True,
        allow_empty=True,
    )
    failed, failed_failures = _check_string_array_field(
        ctx,
        data,
        "failedScenarioRefs",
        context="VERIFY_DECISION",
        required=True,
        allow_empty=True,
    )
    manual, manual_failures = _check_string_array_field(
        ctx,
        data,
        "manualVerificationRefs",
        context="VERIFY_DECISION",
        required=True,
        allow_empty=True,
    )
    missing, missing_failures = _check_string_array_field(
        ctx,
        data,
        "missingScenarioRefs",
        context="VERIFY_DECISION",
        required=False,
        allow_empty=True,
    )
    evidence_ids, evidence_failures = _check_string_array_field(
        ctx,
        data,
        "evidenceIds",
        context="VERIFY_DECISION",
        required=True,
        item_re=EVIDENCE_ID,
    )
    failures += passed_failures + failed_failures + manual_failures + missing_failures + evidence_failures
    failures += _check_evidence_stream_for_refs(ctx, evidence_ids, context="VERIFY_DECISION")
    for field, scenario_refs in (
        ("passedScenarioRefs", passed),
        ("failedScenarioRefs", failed),
        ("manualVerificationRefs", manual),
        ("missingScenarioRefs", missing),
    ):
        for scenario_ref in scenario_refs:
            if scenario_ref not in defined_scenarios:
                failures += fail_line(ctx, "unknown_verify_scenario_ref", f" field={field} id={scenario_ref}")
    for evidence_id in evidence_ids:
        if known_evidence and evidence_id not in known_evidence:
            failures += fail_line(ctx, "unknown_verify_evidence_id", f" evidenceId={evidence_id}")

    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
        covering_evidence=covered_by_evidence,
        spec_ids=spec_ids,
        ui_data=ui_data,
        validate_ui_applicability=ui_data is not None,
    )
    failures += _check_verify_scenario_decisions(
        ctx,
        data,
        defined_scenarios=defined_scenarios,
        passed=passed,
        failed=failed,
        manual=manual,
        missing=missing,
    )

    passed_without_evidence = [scenario for scenario in passed if not covered_by_evidence.get(scenario)]
    if passed_without_evidence:
        failures += fail_line(ctx, "verify_passed_scenario_without_evidence", f" ids={','.join(sorted(passed_without_evidence))}")
    failures += _check_verify_ui_summary(ctx, data, ui_data=ui_data)
    failures += _check_verify_ui_pass_evidence(ctx, data, ui_data=ui_data)
    return failures


def validate_fix_request_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "FIX_REQUEST.json",
        required=ctx.requires_artifact("FIX_REQUEST.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_fix_request_version")
    for field in ["featureId", "sourceCheckpoint", "sourceNodeId", "suggestedCheckpoint", "rootCause", "blockingReason", "createdAt"]:
        failures += _check_string_field(ctx, data, field, context="FIX_REQUEST")
    root_cause = data.get("rootCause")
    if isinstance(root_cause, str) and root_cause not in {
        "requirement_ambiguous",
        "spec_gap",
        "design_conflict",
        "implementation_bug",
        "test_bug",
        "environment_issue",
        "permission_issue",
        "dependency_issue",
        "unknown",
    }:
        failures += fail_line(ctx, "invalid_fix_request_root_cause")
    suggested = data.get("suggestedCheckpoint")
    allowed_fix_targets = _effective_needs_fix_targets(ctx)
    if isinstance(suggested, str) and allowed_fix_targets and suggested not in allowed_fix_targets:
        failures += fail_line(ctx, "invalid_fix_request_suggested_checkpoint")
    human_action = data.get("humanActionRequired")
    if not isinstance(human_action, bool):
        failures += fail_line(ctx, "invalid_json_field", " item=FIX_REQUEST field=humanActionRequired")
    failed_spec_refs, spec_failures = _check_string_array_field(
        ctx,
        data,
        "failedSpecRefs",
        context="FIX_REQUEST",
        required=False,
    )
    failures += spec_failures
    failed_evidence_ids, evidence_failures = _check_string_array_field(
        ctx,
        data,
        "failedEvidenceIds",
        context="FIX_REQUEST",
        required=False,
        item_re=EVIDENCE_ID,
    )
    failures += evidence_failures
    failures += _check_evidence_stream_for_refs(ctx, failed_evidence_ids, context="FIX_REQUEST")
    _, design_failures = _check_string_array_field(
        ctx,
        data,
        "failedDesignRefs",
        context="FIX_REQUEST",
        required=False,
    )
    failures += design_failures

    spec_ids, spec_id_failures = collect_spec_definition_index(ctx)
    failures += spec_id_failures
    for req_id in set(REQ_ID.findall(" ".join(failed_spec_refs))):
        if req_id not in spec_ids["REQ"]:
            failures += fail_line(ctx, "unknown_fix_request_requirement_ref", f" id={req_id}")
    for scn_id in _scenario_refs_from_spec_refs(failed_spec_refs):
        if scn_id not in spec_ids["SCN"]:
            failures += fail_line(ctx, "unknown_fix_request_scenario_ref", f" id={scn_id}")
    known_evidence = _known_evidence_ids(ctx)
    for evidence_id in failed_evidence_ids:
        if known_evidence and evidence_id not in known_evidence:
            failures += fail_line(ctx, "unknown_fix_request_evidence_id", f" evidenceId={evidence_id}")
    known_design_refs = _known_design_refs(ctx)
    for design_ref in data.get("failedDesignRefs", []) if isinstance(data.get("failedDesignRefs"), list) else []:
        if isinstance(design_ref, str) and known_design_refs and design_ref not in known_design_refs:
            failures += fail_line(ctx, "unknown_fix_request_design_ref", f" ref={design_ref}")
    ui_data, ui_failures = _load_ui_context_for_json_checks(ctx)
    failures += ui_failures
    failures += _check_failed_ui_refs(ctx, data, ui_data=ui_data)
    return failures


def validate_ui_context_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "UI_CONTEXT.json",
        required=ctx.requires_artifact("UI_CONTEXT.json"),
    )
    if data is None:
        return failures
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures
    for error in validate_ui_context_data(
        data,
        feature_id=ctx.slug,
        require_locked=ctx.requires_artifact("UI_CONTEXT.json"),
        defined_requirements=spec_ids["REQ"],
        defined_scenarios=spec_ids["SCN"],
    ):
        failures += fail_line(ctx, "invalid_ui_context_json", f" detail={error}")
    return failures


def _load_ui_context_for_projection(ctx: HookContext) -> tuple[dict | None, int]:
    try:
        data = load_ui_context(ctx.feature_dir)
    except UIContextError as exc:
        return None, fail_line(ctx, "invalid_ui_context_json", f" detail={exc}")
    if data is None:
        if ctx.requires_artifact("UI_CONTEXT.json"):
            return None, fail_line(ctx, "missing_json_artifact", " file=UI_CONTEXT.json")
        info(ctx, "ui_context_not_in_contract_degrade")
        return None, 0
    return data, 0


def _visual_source_expected_frontend_route(source: dict) -> str | None:
    route = source.get("route")
    if isinstance(route, str) and route in {
        ROUTE_SPEC_DRIVEN,
        ROUTE_ABSOLUTE,
        ROUTE_STANDARD,
        ROUTE_MISSING,
    }:
        return route
    source_type = source.get("type")
    if source_type == "high_fidelity_html":
        return ROUTE_ABSOLUTE
    if source_type == "standard_html":
        return ROUTE_STANDARD
    return None


def validate_plan_ui_projection(ctx: HookContext) -> int:
    ui_data, failures = _load_ui_context_for_projection(ctx)
    if ui_data is None:
        return failures
    indexes = ui_context_indexes(ui_data)
    visual_sources_by_id = {
        item["sourceId"]: item
        for item in ui_data.get("visualSources", [])
        if isinstance(item, dict) and isinstance(item.get("sourceId"), str)
    }
    capabilities = [
        item
        for item in ui_data.get("capabilities", [])
        if isinstance(item, dict) and item.get("uiRequired") is not False
    ]

    plan_data, errors = load_and_validate_plan(plan_json_path(ctx.feature_dir))
    if errors or plan_data is None:
        return failures

    raw_tasks = plan_data.get("tasks")
    if not isinstance(raw_tasks, list):
        return failures

    feature_ui_required = ui_data.get("uiRequired") is True
    ui_task_count = 0
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            continue
        task_id = task.get("id") if isinstance(task.get("id"), str) else f"tasks[{index}]"
        task_ui_required = task.get("uiRequired") is True
        ui_refs = task.get("uiRefs")

        if not feature_ui_required:
            if task_ui_required:
                failures += fail_line(ctx, "plan_ui_task_when_feature_not_ui", f" task={task_id}")
            if isinstance(ui_refs, dict) and ui_refs:
                failures += fail_line(ctx, "plan_ui_refs_when_feature_not_ui", f" task={task_id}")
            continue

        if not task_ui_required:
            if isinstance(ui_refs, dict) and ui_refs:
                failures += fail_line(ctx, "plan_ui_refs_for_non_ui_task", f" task={task_id}")
            continue

        if task_ui_required:
            ui_task_count += 1
            if not isinstance(ui_refs, dict):
                failures += fail_line(ctx, "plan_ui_task_missing_uiRefs", f" task={task_id}")
                continue
            for field, known in (
                ("pageRefs", indexes["page"]),
                ("interactionRefs", indexes["interaction"]),
                ("visualSourceRefs", indexes["visualSource"]),
            ):
                refs = _string_list_value(ui_refs.get(field))
                if refs is None:
                    failures += fail_line(ctx, "invalid_plan_ui_refs", f" task={task_id} field={field}")
                    continue
                if field in {"pageRefs", "interactionRefs"} and not refs:
                    failures += fail_line(ctx, "missing_plan_ui_refs", f" task={task_id} field={field}")
                for ref in refs:
                    if ref not in known:
                        failures += fail_line(ctx, "unknown_plan_ui_ref", f" task={task_id} field={field} ref={ref}")
            frontend_route = ui_refs.get("frontendRoute")
            if not isinstance(frontend_route, str) or frontend_route not in {
                ROUTE_NONE,
                ROUTE_SPEC_DRIVEN,
                ROUTE_ABSOLUTE,
                ROUTE_STANDARD,
                ROUTE_MISSING,
            }:
                failures += fail_line(ctx, "invalid_plan_ui_frontend_route", f" task={task_id}")
                continue
            visual_refs = _string_list_value(ui_refs.get("visualSourceRefs")) or []
            task_spec_refs = set(_string_list_value(task.get("specRefs")) or [])
            matching_capabilities = [
                capability
                for capability in capabilities
                if task_spec_refs.intersection(_string_list_value(capability.get("specRefs")) or [])
            ]
            if matching_capabilities and any(
                "visualSourceRefs" in capability for capability in matching_capabilities
            ):
                expected_visual_refs = {
                    ref
                    for capability in matching_capabilities
                    for ref in (_string_list_value(capability.get("visualSourceRefs")) or [])
                }
                if set(visual_refs) != expected_visual_refs:
                    failures += fail_line(
                        ctx,
                        "plan_ui_visual_source_projection_mismatch",
                        (
                            f" task={task_id} expected={','.join(sorted(expected_visual_refs)) or 'none'}"
                            f" actual={','.join(sorted(visual_refs)) or 'none'}"
                        ),
                    )
                if not expected_visual_refs and frontend_route != ROUTE_SPEC_DRIVEN:
                    failures += fail_line(
                        ctx,
                        "plan_ui_route_without_visual_source",
                        f" task={task_id} expected={ROUTE_SPEC_DRIVEN} actual={frontend_route}",
                    )
            for visual_ref in visual_refs:
                visual_source = visual_sources_by_id.get(visual_ref)
                if visual_source is None:
                    continue
                expected_route = _visual_source_expected_frontend_route(visual_source)
                if expected_route is not None and frontend_route != expected_route:
                    failures += fail_line(
                        ctx,
                        "plan_ui_frontend_route_mismatch",
                        f" task={task_id} visualSource={visual_ref} expected={expected_route} actual={frontend_route}",
                    )

    if feature_ui_required and ui_task_count == 0:
        failures += fail_line(ctx, "plan_ui_required_without_ui_task")
    return failures


def validate_smoke_test_plan_json(ctx: HookContext) -> int:
    data, failures = load_json_artifact(
        ctx,
        "SMOKE_TEST_PLAN.json",
        required=ctx.requires_artifact("SMOKE_TEST_PLAN.json"),
    )
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_smoke_test_plan_version")
    failures += _check_string_field(ctx, data, "featureId", context="SMOKE_TEST_PLAN")
    if data.get("flowBlocking") is not False:
        failures += fail_line(ctx, "invalid_smoke_flow_blocking")

    tests = data.get("tests")
    if not isinstance(tests, list):
        return failures + fail_line(ctx, "invalid_smoke_test_plan_items")
    if not tests:
        skip_reason = data.get("skipReason")
        if not isinstance(skip_reason, str) or not skip_reason.strip():
            failures += fail_line(ctx, "missing_smoke_skip_reason")
        return failures

    known_tasks = _known_plan_task_ids(ctx)
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    failures += spec_failures
    seen: set[str] = set()
    for index, item in enumerate(tests):
        context = f"tests[{index}]"
        if not isinstance(item, dict):
            failures += fail_line(ctx, "invalid_smoke_test_item", f" item={context}")
            continue
        test_id = item.get("id")
        if not isinstance(test_id, str) or not SMOKE_ID.fullmatch(test_id):
            failures += fail_line(ctx, "invalid_smoke_test_id", f" item={context}")
        elif test_id in seen:
            failures += fail_line(ctx, "duplicate_smoke_test_id", f" item={context} id={test_id}")
        else:
            seen.add(test_id)

        task_id = item.get("taskId")
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            failures += fail_line(ctx, "invalid_smoke_task_id", f" item={context} taskId={task_id}")
        elif known_tasks and task_id not in known_tasks:
            failures += fail_line(ctx, "unknown_smoke_task_id", f" item={context} taskId={task_id}")

        failures += _check_string_field(ctx, item, "title", context=context)
        smoke_type = item.get("smokeType")
        if not isinstance(smoke_type, str) or smoke_type.strip().lower() not in SMOKE_TYPES:
            failures += fail_line(ctx, "invalid_smoke_type", f" item={context}")
        failures += _check_string_field(ctx, item, "command", context=context)
        source_path = item.get("sourcePath")
        if not isinstance(source_path, str) or not _smoke_source_path_allowed(source_path):
            failures += fail_line(ctx, "invalid_smoke_source_path", f" item={context} path={source_path}")
        expected_signals, expected_failures = _check_string_array_field(
            ctx,
            item,
            "expectedSignals",
            context=context,
            required=True,
        )
        failures += expected_failures
        if not expected_signals:
            failures += fail_line(ctx, "missing_smoke_expected_signals", f" item={context}")
        _, precondition_failures = _check_string_array_field(
            ctx,
            item,
            "preconditions",
            context=context,
            required=False,
            allow_empty=True,
        )
        failures += precondition_failures
        timeout_seconds = item.get("timeoutSeconds")
        if timeout_seconds is not None and (not isinstance(timeout_seconds, int) or timeout_seconds <= 0):
            failures += fail_line(ctx, "invalid_smoke_timeout", f" item={context}")
        failures += _check_smoke_tdd_contract(ctx, item, context=context)
        failures += _check_smoke_scenario_refs(ctx, item, context=context, spec_ids=spec_ids)
    return failures


def validate_smoke_result_json(ctx: HookContext) -> int:
    result_path = ctx.file("SMOKE_RESULT.json")
    if not is_nonempty(result_path):
        planned_tests, plan_failures = _smoke_plan_tests(ctx)
        if ctx.requires_artifact("SMOKE_RESULT.json") or planned_tests:
            return plan_failures + fail_line(ctx, "missing_json_artifact", " file=SMOKE_RESULT.json")
        info(ctx, "json_artifact_missing_degrade", " file=SMOKE_RESULT.json")
        return plan_failures

    data, failures = load_json_artifact(ctx, "SMOKE_RESULT.json", required=True)
    if data is None:
        return failures
    if data.get("version") != 1:
        failures += fail_line(ctx, "invalid_smoke_result_version")
    failures += _check_string_field(ctx, data, "featureId", context="SMOKE_RESULT")
    if data.get("flowBlocking") is not False:
        failures += fail_line(ctx, "invalid_smoke_flow_blocking")
    verdict = data.get("verdict")
    normalized_verdict = verdict.upper() if isinstance(verdict, str) else ""
    if normalized_verdict not in SMOKE_VERDICTS:
        failures += fail_line(ctx, "invalid_smoke_result_verdict")

    results = data.get("results")
    if not isinstance(results, list):
        return failures + fail_line(ctx, "invalid_smoke_result_items")

    planned_tests, plan_failures = _smoke_plan_tests(ctx)
    failures += plan_failures
    expected_ids = set(planned_tests)
    seen_ids: set[str] = set()
    evidence_records = _evidence_records_by_id(ctx)
    known_tasks = _known_plan_task_ids(ctx)
    non_pass_results: list[str] = []
    result_statuses: list[str] = []
    for index, item in enumerate(results):
        context = f"results[{index}]"
        if not isinstance(item, dict):
            failures += fail_line(ctx, "invalid_smoke_result_item", f" item={context}")
            continue
        test_id = item.get("testId")
        if not isinstance(test_id, str) or not SMOKE_ID.fullmatch(test_id):
            failures += fail_line(ctx, "invalid_smoke_result_test_id", f" item={context}")
        else:
            if test_id in seen_ids:
                failures += fail_line(ctx, "duplicate_smoke_result_test_id", f" item={context} id={test_id}")
            seen_ids.add(test_id)
            if expected_ids and test_id not in expected_ids:
                failures += fail_line(ctx, "unknown_smoke_result_test_id", f" item={context} id={test_id}")

        task_id = item.get("taskId")
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            failures += fail_line(ctx, "invalid_smoke_result_task_id", f" item={context} taskId={task_id}")
        elif known_tasks and task_id not in known_tasks:
            failures += fail_line(ctx, "unknown_smoke_result_task_id", f" item={context} taskId={task_id}")

        failures += _check_string_field(ctx, item, "command", context=context)
        exit_code = item.get("exitCode")
        if not isinstance(exit_code, int):
            failures += fail_line(ctx, "invalid_smoke_result_exit_code", f" item={context}")
        result = item.get("result")
        normalized_result = result.strip().lower() if isinstance(result, str) else ""
        if normalized_result not in SMOKE_RESULTS:
            failures += fail_line(ctx, "invalid_smoke_result_status", f" item={context}")
        else:
            result_statuses.append(normalized_result)
            if normalized_result != "pass":
                non_pass_results.append(str(test_id))
        if normalized_result == "pass" and isinstance(exit_code, int) and exit_code != 0:
            failures += fail_line(ctx, "smoke_result_exit_code_mismatch", f" item={context}")

        evidence_id = item.get("evidenceId")
        if not isinstance(evidence_id, str) or not EVIDENCE_ID.fullmatch(evidence_id):
            failures += fail_line(ctx, "invalid_smoke_result_evidence_id", f" item={context}")
        else:
            record = evidence_records.get(evidence_id)
            if not record:
                failures += fail_line(ctx, "unknown_smoke_result_evidence_id", f" item={context} evidenceId={evidence_id}")
            elif record.get("action") != "smoke":
                failures += fail_line(ctx, "smoke_result_evidence_not_smoke", f" item={context} evidenceId={evidence_id}")
            else:
                smoke = record.get("smoke")
                record_test_id = smoke.get("testId") if isinstance(smoke, dict) else None
                if isinstance(test_id, str) and record_test_id != test_id:
                    failures += fail_line(ctx, "smoke_result_evidence_test_mismatch", f" item={context} evidenceId={evidence_id}")

        output_tail_path = item.get("outputTailPath")
        if normalized_result in {"fail", "blocked"} and (not isinstance(output_tail_path, str) or not output_tail_path.strip()):
            failures += fail_line(ctx, "missing_smoke_result_output_tail", f" item={context}")
        if normalized_result in {"fail", "blocked"}:
            failures += _check_string_field(ctx, item, "failureSummary", context=context)

    missing_results = expected_ids - seen_ids
    if missing_results and (ctx.requires_artifact("SMOKE_RESULT.json") or expected_ids):
        failures += fail_line(ctx, "missing_smoke_result_rows", f" ids={','.join(sorted(missing_results))}")
    result_status_set = set(result_statuses)
    if normalized_verdict == "PASS" and (not results or non_pass_results):
        detail = f" ids={','.join(sorted(non_pass_results))}" if non_pass_results else ""
        failures += fail_line(ctx, "invalid_smoke_result_summary", detail)
    if normalized_verdict == "FAIL" and "fail" not in result_status_set:
        failures += fail_line(ctx, "invalid_smoke_result_summary")
    if normalized_verdict == "BLOCKED" and ("fail" in result_status_set or "blocked" not in result_status_set):
        failures += fail_line(ctx, "invalid_smoke_result_summary")
    if normalized_verdict == "SKIPPED" and result_status_set != {"skipped"}:
        failures += fail_line(ctx, "invalid_smoke_result_summary")
    if normalized_verdict == "NOT_APPLICABLE" and results:
        failures += fail_line(ctx, "invalid_smoke_result_summary")
    for test_id, plan_item in planned_tests.items():
        source_path = plan_item.get("sourcePath")
        if isinstance(source_path, str) and source_path:
            if not (ctx.root / source_path).is_file():
                failures += fail_line(ctx, "missing_smoke_source_file", f" id={test_id} path={source_path}")
            else:
                failures += _check_smoke_source_git_ignored(ctx, source_path, test_id=test_id)
    return failures


def _boolean_marker_value(text: str, marker: str) -> bool | None:
    match = re.search(rf"{re.escape(marker)}\W*:\W*(true|false)\b", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _design_escape_hatches(ctx: HookContext) -> tuple[bool, bool]:
    design = ctx.file("design.md")
    if not is_nonempty(design):
        return False, False
    text = read_text(design)
    return (
        _boolean_marker_value(text, "x-auto-no-http-api") is True,
        _boolean_marker_value(text, "x-auto-no-sql") is True,
    )


def _validate_plan_json_traceability(ctx: HookContext, data: dict) -> int:
    failures = 0
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    design_ids, design_failures = collect_design_definition_index(ctx)
    no_http_api, no_sql = _design_escape_hatches(ctx)
    failures += spec_failures + design_failures

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return failures

    covered_api_refs: set[str] = set()
    covered_data_refs: set[str] = set()
    for index, task in enumerate(raw_tasks):
        context = f"tasks[{index}]"
        if not isinstance(task, dict):
            continue
        task_id = task.get("id") if isinstance(task.get("id"), str) else context

        spec_refs = _string_list_value(task.get("specRefs")) or []
        req_refs = set(REQ_ID.findall(" ".join(spec_refs)))
        scn_refs = _scenario_refs_from_spec_refs(spec_refs)
        if not req_refs:
            failures += fail_line(ctx, "missing_plan_json_requirement_ref", f" task={task_id}")
        if not scn_refs:
            failures += fail_line(ctx, "missing_plan_json_scenario_ref", f" task={task_id}")
        for req_id in sorted(req_refs):
            if req_id not in spec_ids["REQ"]:
                failures += fail_line(ctx, "unknown_plan_json_requirement_ref", f" task={task_id} id={req_id}")
        for scn_id in sorted(scn_refs):
            if scn_id not in spec_ids["SCN"]:
                failures += fail_line(ctx, "unknown_plan_json_scenario_ref", f" task={task_id} id={scn_id}")

        design_refs = _string_list_value(task.get("designRefs")) or []
        design_ref_text = " ".join(design_refs)
        api_refs = set(_string_list_value(task.get("apiIds")) or []) | set(re.findall(r"\bAPI-\d{3}\b", design_ref_text))
        data_refs = set(_string_list_value(task.get("dataIds")) or []) | set(re.findall(r"\bDATA-\d{3}\b", design_ref_text))
        decision_refs = set(_string_list_value(task.get("decisionIds")) or []) | set(re.findall(r"\bD-\d{3}\b", design_ref_text))
        covered_api_refs.update(api_refs)
        covered_data_refs.update(data_refs)

        if not decision_refs:
            failures += fail_line(ctx, "missing_plan_json_decision_ref", f" task={task_id}")
        for api_id in sorted(api_refs):
            if api_id not in design_ids["API"]:
                failures += fail_line(ctx, "unknown_plan_json_api_ref", f" task={task_id} id={api_id}")
        for data_id in sorted(data_refs):
            if data_id not in design_ids["DATA"]:
                failures += fail_line(ctx, "unknown_plan_json_data_ref", f" task={task_id} id={data_id}")
        for decision_id in sorted(decision_refs):
            if decision_id not in design_ids["D"]:
                failures += fail_line(ctx, "unknown_plan_json_decision_ref", f" task={task_id} id={decision_id}")
    if not no_http_api:
        if not design_ids["API"]:
            failures += fail_line(ctx, "missing_design_api_id")
        else:
            for api_id in sorted(design_ids["API"] - covered_api_refs):
                failures += fail_line(ctx, "missing_plan_json_api_coverage", f" id={api_id}")
    if not no_sql:
        if not design_ids["DATA"]:
            failures += fail_line(ctx, "missing_design_data_id")
        else:
            for data_id in sorted(design_ids["DATA"] - covered_data_refs):
                failures += fail_line(ctx, "missing_plan_json_data_coverage", f" id={data_id}")
    return failures


def _plan_task_string_list(task: dict, field: str) -> list[str]:
    return _string_list_value(task.get(field)) or []


def _plan_task_ui_refs(task: dict, field: str) -> list[str]:
    ui_refs = task.get("uiRefs")
    if not isinstance(ui_refs, dict):
        return []
    return _string_list_value(ui_refs.get(field)) or []


def _spec_scenario_refs_by_path(ctx: HookContext) -> dict[str, set[str]]:
    refs_by_id: dict[str, set[str]] = {}
    for spec in spec_files(ctx):
        rel = spec.relative_to(ctx.feature_dir).as_posix()
        for scn_id in SPEC_SCENARIO_DEF_RE.findall(read_text(spec)):
            refs_by_id.setdefault(scn_id, set()).add(f"{rel}#{scn_id}")
    return refs_by_id


def _covered_spec_scenario_refs(spec_refs: list[str], refs_by_id: dict[str, set[str]]) -> set[str]:
    covered: set[str] = set()
    for raw_ref in spec_refs:
        if not isinstance(raw_ref, str):
            continue
        path_part, _, anchor = raw_ref.partition("#")
        scenario_ids = SCN_ID.findall(anchor or raw_ref)
        if not scenario_ids:
            continue
        normalized_path = path_part.strip().replace("\\", "/")
        for scn_id in scenario_ids:
            if normalized_path:
                covered.add(f"{normalized_path}#{scn_id}")
            elif len(refs_by_id.get(scn_id, set())) == 1:
                covered.update(refs_by_id[scn_id])
    return covered


def validate_plan_task_granularity(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        info(ctx, "plan_task_granularity_not_in_contract_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json)
    failures = 0
    if errors:
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json")

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return failures
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            continue
        task_id = task.get("id") if isinstance(task.get("id"), str) else f"tasks[{index}]"
        task_errors = validate_plan_task_granularity_item(task, task_id=task_id)
        for error in task_errors:
            failures += fail_line(
                ctx,
                error["reason"],
                " " + error.get("detail", ""),
            )
    return failures


def validate_plan_scenario_coverage(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        info(ctx, "plan_scenario_coverage_not_in_contract_degrade")
        return 0

    refs_by_id = _spec_scenario_refs_by_path(ctx)
    expected_refs = set().union(*refs_by_id.values()) if refs_by_id else set()
    if not expected_refs:
        info(ctx, "plan_scenario_coverage_no_specs_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json)
    failures = 0
    if errors:
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json")

    covered_refs: set[str] = set()
    raw_tasks = data.get("tasks")
    if isinstance(raw_tasks, list):
        for task in raw_tasks:
            if not isinstance(task, dict):
                continue
            covered_refs.update(_covered_spec_scenario_refs(_plan_task_string_list(task, "specRefs"), refs_by_id))

    missing_refs = expected_refs - covered_refs
    if missing_refs:
        failures += fail_line(ctx, "missing_plan_scenario_coverage", f" ids={','.join(sorted(missing_refs))}")
    return failures


def validate_plan_ref_resolution(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        info(ctx, "plan_ref_resolution_not_in_contract_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json, require_task_details=True)
    failures = 0
    if errors:
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json")

    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return failures
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task_id = task.get("id") if isinstance(task.get("id"), str) else f"tasks[{index}]"
        _, _, ref_errors = resolve_task_refs(ctx.feature_dir, task)
        for error in ref_errors:
            detail = error.get("detail", "")
            suffix = f" task={task_id} detail={detail}" if detail else f" task={task_id}"
            failures += fail_line(ctx, error.get("reason", "invalid_artifact_ref"), suffix)
    return failures


def validate_plan_json_initial_tasks(ctx: HookContext) -> int:
    if not ctx.requires_artifact("plan.json") and not is_nonempty(ctx.file("plan.json")):
        if is_nonempty(ctx.file("PLAN.md")):
            return fail_line(ctx, "missing_plan_json", " detail=PLAN.md_present_but_not_machine_source")
        info(ctx, "plan_json_not_in_contract_degrade")
        return 0
    data, errors = load_and_validate_plan(ctx.file("plan.json"), require_initial_status=True)
    failures = 0
    for error in errors:
        failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
    if data is not None and not errors and data.get("taskSetStatus") != "finalized":
        failures += fail_line(ctx, "plan_task_set_not_finalized")
    return failures


def validate_plan_json_contract(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        if is_nonempty(ctx.file("PLAN.md")):
            return fail_line(ctx, "missing_plan_json", " detail=PLAN.md_present_but_not_machine_source")
        info(ctx, "plan_json_not_in_contract_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json)
    failures = 0
    if errors:
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json")
    failures += _validate_plan_json_traceability(ctx, data)
    failures += validate_plan_ui_projection(ctx)
    return failures


def validate_plan_task_detail_schema(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        info(ctx, "plan_task_detail_schema_not_in_contract_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json, require_task_details=True)
    failures = 0
    for error in errors:
        failures += fail_line(ctx, "invalid_plan_json", f" detail={error}")
    if data is None:
        return failures or fail_line(ctx, "missing_plan_json")
    return failures


def validate_code_done_gate(ctx: HookContext) -> int:
    if not ctx.requires_artifact("evidence/EVIDENCE.jsonl"):
        info(ctx, "code_done_gate_not_in_contract_degrade")
        return 0
    failures = 0
    for error in check_code_done(ctx.feature_dir):
        failures += fail_line(ctx, "invalid_code_done_gate", f" detail={error}")
    return failures


def validate_frontend_route_gate(ctx: HookContext) -> int:
    try:
        ui_context = load_ui_context(ctx.feature_dir)
    except UIContextError as exc:
        return fail_line(ctx, "invalid_ui_context_json", f" detail={exc}")
    if isinstance(ui_context, dict) and ui_context.get("uiRequired") is False:
        return 0

    evidence_file = frontend_evidence_path(ctx.root, ctx.slug)
    evidence = read_frontend_json(evidence_file)

    if not evidence:
        try:
            resolved = resolve_frontend_route(ctx.root, ctx.slug, write_evidence=False)
        except FrontendRouteError as exc:
            return fail_line(ctx, "invalid_frontend_route_source", f" detail={exc}")
        if resolved.get("triggered"):
            return fail_line(
                ctx,
                "missing_frontend_route_evidence",
                f" route={resolved.get('route')} evidence={evidence_file}",
            )
        return 0

    route = evidence.get("route")
    if route == ROUTE_NONE and evidence.get("triggered") is not True:
        return 0
    if route == ROUTE_SPEC_DRIVEN:
        review_status = evidence.get("reviewStatus")
        if review_status not in FRONTEND_REVIEW_PASS:
            return fail_line(
                ctx,
                "frontend_review_not_passed_or_skipped",
                f" reviewStatus={review_status!r} evidence={evidence_file}",
            )
        return 0
    if route == ROUTE_MISSING and evidence.get("source") == "UI_CONTEXT.json":
        review_status = evidence.get("reviewStatus")
        if review_status not in FRONTEND_REVIEW_PASS:
            return fail_line(
                ctx,
                "frontend_review_not_passed_or_skipped",
                f" reviewStatus={review_status!r} evidence={evidence_file}",
            )
        return 0
    if route == ROUTE_MISSING:
        return fail_line(ctx, "frontend_html_source_missing", f" evidence={evidence_file}")
    if route not in {ROUTE_ABSOLUTE, ROUTE_STANDARD}:
        return fail_line(ctx, "invalid_frontend_route", f" route={route!r} evidence={evidence_file}")

    failures = 0
    required_flags = (
        "routeSkillRead",
        "routeSkillReadComplete",
        "routeTodosCreated",
        "routeTodosCompleted",
        "parserRead",
    )
    for flag in required_flags:
        if evidence.get(flag) is not True:
            failures += fail_line(ctx, f"frontend_route_{flag}_missing", f" evidence={evidence_file}")

    review_status = evidence.get("reviewStatus")
    if review_status not in FRONTEND_REVIEW_PASS:
        failures += fail_line(
            ctx,
            "frontend_review_not_passed_or_skipped",
            f" reviewStatus={review_status!r} evidence={evidence_file}",
        )
    route_run_id = evidence.get("routeRunId")
    review_route_run_id = evidence.get("reviewRouteRunId")
    if isinstance(route_run_id, str) and route_run_id.strip() and review_route_run_id != route_run_id:
        failures += fail_line(
            ctx,
            "frontend_review_route_run_mismatch",
            f" routeRunId={route_run_id!r} reviewRouteRunId={review_route_run_id!r} evidence={evidence_file}",
        )
    return failures


def validate_evidence_integrity(ctx: HookContext) -> int:
    if not ctx.requires_artifact("evidence/EVIDENCE.jsonl"):
        info(ctx, "evidence_not_in_contract_degrade")
        return 0
    failures = 0
    for error in check_integrity(ctx.feature_dir, require_index=True):
        failures += fail_line(ctx, "invalid_evidence_stream", f" detail={error}")
    if is_nonempty(plan_json_path(ctx.feature_dir)):
        for error in check_plan_evidence_refs(ctx.feature_dir):
            failures += fail_line(ctx, "invalid_evidence_trace", f" detail={error}")
    return failures


def validate_evidence_detail_quality(ctx: HookContext) -> int:
    evidence_stream = stream_path(ctx.feature_dir)
    if not is_nonempty(evidence_stream):
        info(ctx, "evidence_detail_quality_no_stream_degrade")
        return 0

    try:
        records = read_records(evidence_stream)
    except EvidenceStoreError as exc:
        return fail_line(ctx, "invalid_evidence_stream", f" detail={exc}")

    failures = 0
    for index, record in enumerate(records, start=1):
        if "detailVersion" not in record:
            continue
        evidence_id = record.get("evidenceId")
        evidence_label = evidence_id if isinstance(evidence_id, str) and evidence_id.strip() else f"line{index}"
        for error in validate_detail_fields(record):
            failures += fail_line(
                ctx,
                "invalid_evidence_detail",
                f" evidence={evidence_label} detail={error}",
            )
    return failures


def _ctx_requiring_json(ctx: HookContext, artifact: str) -> HookContext:
    if ctx.requires_artifact(artifact):
        return ctx
    return HookContext(
        skill=ctx.skill,
        slug=ctx.slug,
        root=ctx.root,
        required_inputs=ctx.required_inputs,
        required_outputs=(*ctx.required_outputs, artifact),
    )


def validate_e2e_report_contract(ctx: HookContext) -> int:
    info(ctx, "legacy_markdown_validator_uses_json_source", " validator=e2e_report_contract json=E2E_RESULT.json")
    json_failures = validate_e2e_result_json(_ctx_requiring_json(ctx, "E2E_RESULT.json"))
    if json_failures:
        return json_failures
    cases = ctx.file("E2E_TEST_CASES.yaml")
    log = ctx.file("e2e-run.log")
    failures = 0

    if not is_nonempty(cases):
        return fail_line(ctx, "missing_e2e_cases")
    if not is_nonempty(log):
        failures += fail_line(ctx, "missing_e2e_run_log")

    cases_text = read_text(cases)
    if not E2E_ID.search(cases_text):
        failures += fail_line(ctx, "missing_e2e_case_id")
    if not REQ_ID.search(cases_text):
        failures += fail_line(ctx, "missing_e2e_requirement_id")
    if not SCN_ID.search(cases_text):
        failures += fail_line(ctx, "missing_e2e_scenario_id")
    if "execution_mode:" not in cases_text:
        failures += fail_line(ctx, "missing_e2e_execution_mode")
    if "ui_required:" not in cases_text:
        failures += fail_line(ctx, "missing_e2e_ui_required")
    return failures


def validate_verify_report_contract(ctx: HookContext) -> int:
    info(ctx, "legacy_markdown_validator_uses_json_source", " validator=verify_report_contract json=VERIFY_DECISION.json")
    return validate_verify_decision_json(_ctx_requiring_json(ctx, "VERIFY_DECISION.json"))


VALIDATORS = {
    "proposal_contract": validate_proposal_contract,
    "specs_contract": validate_specs_contract,
    "ui_context_json": validate_ui_context_json,
    "design_contract": validate_design_contract,
    "plan_json_contract": validate_plan_json_contract,
    "plan_json_initial_tasks": validate_plan_json_initial_tasks,
    "plan_task_granularity": validate_plan_task_granularity,
    "plan_scenario_coverage": validate_plan_scenario_coverage,
    "plan_ref_resolution": validate_plan_ref_resolution,
    "plan_task_detail_schema": validate_plan_task_detail_schema,
    "plan_ui_projection": validate_plan_ui_projection,
    "plan_finished_tasks": validate_plan_finished_tasks,
    "frontend_route_gate": validate_frontend_route_gate,
    "evidence_detail_quality": validate_evidence_detail_quality,
    "code_done_gate": validate_code_done_gate,
    "evidence_integrity": validate_evidence_integrity,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
    "review_findings_json": validate_review_findings_json,
    "unit_test_report_contract": validate_unit_test_report_contract,
    "unit_test_result_json": validate_unit_test_result_json,
    "e2e_report_contract": validate_e2e_report_contract,
    "e2e_result_json": validate_e2e_result_json,
    "verify_report_contract": validate_verify_report_contract,
    "verify_decision_json": validate_verify_decision_json,
    "fix_request_json": validate_fix_request_json,
    "smoke_test_plan_json": validate_smoke_test_plan_json,
    "smoke_result_json": validate_smoke_result_json,
    "plan_initial_tasks": validate_plan_initial_tasks,
    "plan_execution_contract": validate_plan_execution_contract,
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
