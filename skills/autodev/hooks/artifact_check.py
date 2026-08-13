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
from hooks.e2e_trust_common import (  # noqa: E402
    DIAGNOSTICS_DIR,
    is_fresh,
    normalize_relative_path,
    validate_execution_evidence_chain,
    validate_execution_hash_chain,
    validate_execution_log_chain,
    validate_scan_current,
)
from hooks.implementation_scope import load_scope, scope_path  # noqa: E402
from hooks.artifact_ref_validator import (  # noqa: E402
    design_marker_value,
    load_design_contract,
    validate_plan_design_coverage,
    validate_task_artifact_refs,
)
from hooks.plan_json import (  # noqa: E402
    failed_tasks,
    load_and_validate_plan,
    plan_json_path,
    unfinished_tasks,
)
from hooks.code_task_context import resolve_task_refs  # noqa: E402
from hooks.plan_granularity import validate_plan_task_granularity_item  # noqa: E402

E2E_ID = re.compile(r"\bE2E-[A-Za-z0-9_-]+-\d{3}\b")
REQ_ID = re.compile(r"\bREQ-\d{3}\b")
SCN_ID = re.compile(r"\bSCN-\d{3}\b")
TASK_ID = re.compile(r"\bT\d{3}\b")
EVIDENCE_ID = re.compile(r"\bev_\d{4}\b")
SPEC_REQUIREMENT_DEF_RE = re.compile(r"^###\s+Requirement\s+\[(REQ-\d{3})\]:\s+.+$", re.MULTILINE)
SPEC_SCENARIO_DEF_RE = re.compile(r"^####\s+Scenario\s+\[(SCN-\d{3})\]:\s+.+$", re.MULTILINE)
# 带 REQ-/SCN- 记号的标题行；与上面两个正则的差集就是索引器看不见的写法
CONTRACT_HEADING_CANDIDATE = re.compile(r"^#{1,6}[ \t]+.*?\b(?:REQ|SCN)-\S")
# 二级标题（操作段）；`###` 不算，否则 Requirement 标题会被当成段边界
SECTION_HEADING = re.compile(r"^##(?!#)\s+\S")
REMOVED_SECTION = re.compile(
    r"^##\s+REMOVED\s+Requirements\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
REMOVED_FIELD = re.compile(
    r"^\*\*(?P<name>Reason|Migration)[:：]\*\*(?P<value>.*)$", re.MULTILINE
)
# `[能力名]` 这类待填槽位。排除 `[REQ-001]`/`[SCN-001]`（真 ID 语法）、
# Markdown 链接 `[文字](url)`、以及任务勾选框 `[ ]` / `[x]`。
PLACEHOLDER_BRACKET = re.compile(
    r"\[(?!(?:REQ|SCN)-\d{3}\])(?![ xX]\])(?P<slot>[^\]\n]{1,40})\](?!\()"
)
PLACEHOLDER_WORD = re.compile(r"TBD|待补充|待提供|待定|占位", re.IGNORECASE)
PLACEHOLDER_TEXT = re.compile(r"\[[^\]\n]*\]|TBD|待补充|待提供|待定|占位", re.IGNORECASE)
# 规格决策 DEC-NNN：specs 阶段在 proposal `## Decision Log` 定义，design 追踪表引用。
# 与技术决策 `D-NNN`（plan 阶段自产，见 hooks/plan_json.TECH_DECISION_ID_RE）不是一回事，
# 两个正则互不误抓：`\bD-\d{3}\b` 要求 D 后紧跟 `-`，`DEC-001` 的 D 后是 E。
SPEC_DECISION_ID = re.compile(r"\bDEC-\d{3}\b")
SPEC_DECISION_HEADING = re.compile(r"^###\s+(DEC-\d{3})\s*[:：]", re.MULTILINE)
DECISION_LOG_SECTION = re.compile(
    r"^##\s+Decision Log\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
# proposal 的 `## Capabilities` 段：到下一个同级标题为止
CAPABILITIES_SECTION = re.compile(
    r"^##\s+Capabilities\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
# `- \`order-export\`: 说明`。占位 `[capability-name]` 与「无」都匹配不上 kebab-case
CAPABILITY_ITEM = re.compile(
    r"^[ \t]*[-*][ \t]+`?(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)`?[ \t]*[:：]",
    re.MULTILINE,
)
# `### New Capabilities` 等分组小标题，切开 `## Capabilities` 段的三组
CAPABILITY_GROUP_HEADING = re.compile(
    r"^###\s+(?P<group>New|Modified|Removed)\s+Capabilities\s*$",
    re.MULTILINE,
)
# spec 的 `## ADDED Requirements` 等操作段
SPEC_OPERATION_SECTION = re.compile(
    r"^##\s+(?P<operation>ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
GROUP_TO_OPERATION = {"New": "ADDED", "Modified": "MODIFIED", "Removed": "REMOVED"}
DESIGN_API_DEF_RE = re.compile(r"^\|\s*(API-\d{3})\s*\|", re.MULTILINE)
DESIGN_DATA_DEF_RE = re.compile(r"^\|\s*(DATA-\d{3})\s*\|", re.MULTILINE)
DESIGN_DECISION_DEF_RE = re.compile(r"^\|\s*(D-\d{3})\s*\|", re.MULTILINE)
DETAIL_DESIGN_ID = re.compile(r"\bDD-\d{2,3}\b")
REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_TYPES = {"startup", "api", "ui", "cli", "migration", "health", "custom"}
SMOKE_SEAM_TYPES = {"startup", "api", "http", "ui", "cli", "job", "migration", "health", "custom"}
SMOKE_RESULTS = {"pass", "fail", "blocked", "skipped"}
SMOKE_SOURCE_PREFIXES = (
    "src/test/",
    "test/smoke/",
    "tests/smoke/",
    "scripts/smoke/",
    "e2e/smoke/",
    "cypress/e2e/smoke/",
    "playwright/smoke/",
)
# 表格单元格恰好为「待确认 / 读码差异」时命中；`风险/待确认` 这类枚举说明或「」引用不命中
PENDING_CELL = re.compile(r"\|\s*(待确认|读码差异)\s*\|")
FENCE_OPEN_LINE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
BLOCKQUOTE_LINE = re.compile(r"^[ \t]{0,3}>")
TEMPLATE_WRAPPER_HEADINGS = {"# 技术设计模板", "# 计划模板"}


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
            target=f"{artifact}:{lineno}",
        )
    return failures


TERMINAL_PASS = {"PASS", "PASS_WITH_WARNINGS"}
REVIEW_VERDICTS = {"PASS", "PASS_WITH_WARNINGS", "FAIL", "DEGRADED"}
REQUIREMENTS_EVAL_VERDICT_SECTION = re.compile(
    r"^##\s+Verdict\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
REQUIREMENTS_EVAL_BLOCKERS_SECTION = re.compile(
    r"^##\s+Blockers?\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
REVIEW_VERDICT_TOKEN = re.compile(r"[A-Za-z_]+")
REVIEW_BLOCKER_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?P<text>.*\S)[ \t]*$", re.MULTILINE)
REVIEW_BLOCKER_EMPTY = re.compile(r"^(?:none|n/?a|null|nil|-+|无|没有|暂无|不适用)\b", re.IGNORECASE)


def spec_files(ctx: HookContext) -> list[Path]:
    return sorted(
        path
        for path in ctx.feature_dir.glob("specs/**/*.md")
        if path.is_file() and path.stat().st_size > 0
    )


def _implementation_scope_contract_errors(ctx: HookContext) -> tuple[str, list[str]]:
    """Load the optional scope contract without breaking legacy Features."""

    manifest = scope_path(ctx.feature_dir)
    if not manifest.is_file():
        return "full_stack", []
    return load_scope(ctx.feature_dir, required=True)


def _report_implementation_scope_errors(ctx: HookContext) -> int:
    _, errors = _implementation_scope_contract_errors(ctx)
    failures = 0
    for error in errors:
        failures += fail_line(
            ctx,
            "invalid_implementation_scope",
            f" detail={error}",
            target=error,
        )
    return failures


def validate_proposal_contract(ctx: HookContext) -> int:
    proposal = ctx.file("proposal.md")
    if not is_nonempty(proposal):
        return fail_line(ctx, "missing_proposal")

    text = read_text(proposal)
    failures = validate_no_template_guidance(ctx, proposal, text)
    failures += _report_implementation_scope_errors(ctx)
    required_sections = [
        "Why",
        "What Changes",
        "Capabilities",
        "Impact",
        "Out of Scope",
        # design 的 `Decision` 列按 DEC-NNN 引用本节；节不存在时那些引用
        # 无处解析，缺口要在 specs 阶段就报，不能拖到 plan 才发现。
        "Decision Log",
        # 只校验节存在。`Status` 取值不查：「已确认」是模型能自己给自己写的
        # 状态词，给它加校验只会教出伪造，不会换来真实裁定。
        "Open Questions",
    ]
    for section in required_sections:
        if not has_heading(text, section):
            failures += fail_line(
                ctx,
                "invalid_proposal_missing_section",
                f" section={section!r}",
                target=section,
            )
    return failures


def has_heading(text: str, name: str) -> bool:
    """Whether ``name`` appears as a Markdown heading, not just anywhere in prose.

    Substring matching made "delete the whole section" a free pass: a proposal
    that merely mentions "Open Questions" in a sentence satisfied it. Requiring
    a heading is what makes the section actually mandatory.
    """
    pattern = r"^#{1,6}[ \t]+.*" + re.escape(name)
    return re.search(pattern, text, re.MULTILINE) is not None


def proposal_capability_groups(text: str) -> dict[str, str]:
    """Map each capability under ``## Capabilities`` to its New/Modified/Removed group.

    The section body runs from the ``## Capabilities`` heading to the next
    same-level heading, then splits at the ``### <group> Capabilities`` subheadings.
    Template placeholders (``[capability-name]``) and the ``无`` filler used for
    empty groups never match the kebab-case pattern, so they drop out without
    special-casing. Items listed before any group heading are ignored: they have
    no declared operation to check against.
    """
    section = CAPABILITIES_SECTION.search(text)
    if not section:
        return {}
    body = section.group("body")

    groups: dict[str, str] = {}
    headings = list(CAPABILITY_GROUP_HEADING.finditer(body))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        for item in CAPABILITY_ITEM.finditer(body[heading.end() : end]):
            groups[item.group("name")] = heading.group("group")
    return groups


def proposal_capabilities(text: str) -> set[str]:
    """Kebab-case capability names listed under ``## Capabilities``, any group."""
    section = CAPABILITIES_SECTION.search(text)
    if not section:
        return set()
    return {
        match.group("name")
        for match in CAPABILITY_ITEM.finditer(section.group("body"))
    }


def spec_operations_with_requirements(text: str) -> set[str]:
    """Operations whose section actually defines a Requirement.

    Presence of the heading is not the signal. Every spec carries all three
    sections so the file shape stays uniform, and the unused ones are left
    empty -- so what distinguishes a New capability from a Modified one is
    which sections have Requirements under them, not which headings exist.
    """
    return {
        match.group("operation")
        for match in SPEC_OPERATION_SECTION.finditer(text)
        if SPEC_REQUIREMENT_DEF_RE.search(match.group("body"))
    }


def spec_capability_dirs(ctx: HookContext) -> set[str]:
    """Capability directory names following the ``specs/<capability>/spec.md`` shape."""
    return {
        path.parent.name
        for path in ctx.feature_dir.glob("specs/*/spec.md")
        if path.is_file() and path.stat().st_size > 0
    }


def validate_capability_spec_correspondence(ctx: HookContext) -> int:
    """Both directions of ``Capabilities`` <-> ``specs/<capability>/spec.md``.

    Listing a capability without writing its spec, and shipping a spec the
    proposal never claims, are both mechanical facts about files. Neither is
    something the agent should be asked to self-certify in its reply.
    """
    proposal = ctx.file("proposal.md")
    if not is_nonempty(proposal):
        # `proposal_contract` owns this failure; do not report it twice.
        return 0

    text = read_text(proposal)
    if not CAPABILITIES_SECTION.search(text):
        # Missing section is `invalid_proposal_missing_section`, not a mismatch.
        return 0

    declared = proposal_capabilities(text)
    present = spec_capability_dirs(ctx)

    failures = 0
    missing_specs = sorted(declared - present)
    if missing_specs:
        failures += fail_line(
            ctx,
            "proposal_capability_missing_spec",
            f" capabilities={','.join(missing_specs)}",
            target=",".join(missing_specs),
        )
    unlisted = sorted(present - declared)
    if unlisted:
        failures += fail_line(
            ctx,
            "spec_missing_proposal_capability",
            f" capabilities={','.join(unlisted)}",
            target=",".join(unlisted),
        )
    failures += _capability_operation_failures(ctx, text, declared & present)
    return failures


def _capability_operation_failures(
    ctx: HookContext,
    proposal_text: str,
    capabilities: set[str],
) -> int:
    """Check each capability's declared group against the operations its spec uses.

    The rule is deliberately asymmetric. A declared group always obliges the
    matching operation, but only ``New`` also forbids the others: a brand-new
    capability has no pre-existing Requirements to modify or remove, so content
    under those sections contradicts the declaration. A ``Modified`` capability
    adding a Requirement alongside its edits is ordinary, and flagging it would
    make the check fire on correct specs.
    """
    groups = proposal_capability_groups(proposal_text)
    failures = 0
    for capability in sorted(capabilities):
        group = groups.get(capability)
        if group is None:
            # Listed outside any group heading; `proposal_contract` owns the shape.
            continue
        expected = GROUP_TO_OPERATION[group]
        spec = ctx.feature_dir / "specs" / capability / "spec.md"
        if not is_nonempty(spec):
            continue
        actual = spec_operations_with_requirements(read_text(spec))
        if expected not in actual:
            failures += fail_line(
                ctx,
                "capability_operation_missing",
                f" capability={capability} group={group} expected={expected}",
                target=capability,
                fields={"group": group, "expected": expected},
            )
        if group == "New":
            contradicting = sorted(actual - {"ADDED"})
            if contradicting:
                failures += fail_line(
                    ctx,
                    "capability_operation_contradicts_new",
                    f" capability={capability} operations={','.join(contradicting)}",
                    target=capability,
                    fields={"operations": "/".join(contradicting)},
                )
    return failures


def validate_specs_contract(ctx: HookContext) -> int:
    specs = spec_files(ctx)
    if not specs:
        return fail_line(ctx, "missing_specs")

    failures = _report_implementation_scope_errors(ctx)
    for spec in specs:
        text = read_text(spec)
        rel = spec.relative_to(ctx.feature_dir)
        failures += validate_no_template_guidance(ctx, spec, text)
        _, duplicate_reasons = _spec_definition_index(text)
        for reason in duplicate_reasons:
            failures += fail_line(ctx, reason, f" file={rel}", target=str(rel))
        if not re.search(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\b", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_operation_header", f" file={rel}", target=str(rel))
        if not re.search(r"^###\s+Requirement\s+\[REQ-\d{3}\]:\s+.+", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_requirement", f" file={rel}", target=str(rel))
        if not re.search(r"^####\s+Scenario\s+\[SCN-\d{3}\]:\s+.+", text, re.MULTILINE):
            failures += fail_line(ctx, "invalid_spec_missing_scenario", f" file={rel}", target=str(rel))
        malformed = malformed_contract_headings(text)
        if malformed:
            failures += fail_line(
                ctx,
                "spec_contract_heading_malformed",
                f" file={rel} headings={'; '.join(malformed)}",
                target=str(rel),
                fields={"headings": "; ".join(malformed)},
            )
        barren = requirements_without_scenario(text)
        if barren:
            failures += fail_line(
                ctx,
                "spec_requirement_without_scenario",
                f" file={rel} requirements={','.join(barren)}",
                target=str(rel),
                fields={"requirements": ",".join(barren)},
            )
        orphans = scenarios_without_requirement(text)
        if orphans:
            failures += fail_line(
                ctx,
                "spec_scenario_without_requirement",
                f" file={rel} scenarios={','.join(orphans)}",
                target=str(rel),
                fields={"scenarios": ",".join(orphans)},
            )
        disordered = out_of_order_ids(text)
        if disordered:
            failures += fail_line(
                ctx,
                "spec_id_out_of_order",
                f" file={rel} ids={','.join(disordered)}",
                target=str(rel),
                fields={"ids": ",".join(disordered)},
            )
        missing_fields = removed_requirements_missing_fields(text)
        if missing_fields:
            failures += fail_line(
                ctx,
                "removed_requirement_missing_field",
                f" file={rel} fields={','.join(missing_fields)}",
                target=str(rel),
                fields={"fields": ",".join(missing_fields)},
            )
        residue = placeholder_residue(text)
        if residue:
            failures += fail_line(
                ctx,
                "spec_placeholder_residue",
                f" file={rel} placeholders={'; '.join(sorted(set(residue))[:8])}",
                target=str(rel),
                fields={"placeholders": "; ".join(sorted(set(residue))[:8])},
            )
    failures += _duplicate_ids_across_specs(ctx, specs)
    return failures


def _duplicate_ids_across_specs(ctx: HookContext, specs: list[Path]) -> int:
    """One REQ/SCN ID must name one thing across the whole feature.

    ``collect_spec_definition_index`` merges every spec's IDs into one flat set
    that five validators consume, ``_validate_scenario_coverage`` among them.
    A number reused in a second capability collapses into a single entry there,
    so covering one of them marks both covered -- the same vacuous-coverage
    shape the ID convention mismatch produced.
    """
    owners: dict[str, list[str]] = {}
    for spec in specs:
        text = read_text(spec)
        rel = spec.relative_to(ctx.feature_dir).as_posix()
        for pattern in (SPEC_REQUIREMENT_DEF_RE, SPEC_SCENARIO_DEF_RE):
            for spec_id in set(pattern.findall(text)):
                owners.setdefault(spec_id, []).append(rel)

    failures = 0
    for spec_id, files in sorted(owners.items()):
        if len(files) > 1:
            failures += fail_line(
                ctx,
                "duplicate_spec_id_across_specs",
                f" id={spec_id} files={','.join(sorted(files))}",
                target=spec_id,
                fields={"files": ",".join(sorted(files))},
            )
    return failures


def malformed_contract_headings(text: str) -> list[str]:
    """Heading lines carrying a REQ-/SCN- token that the ID indexer will not see.

    The indexer accepts exactly one spelling. A heading one bracket or one ``#``
    away from it is not a syntax error anywhere — it simply vanishes, and the
    Requirement it names drops out of every downstream coverage check while the
    file still passes because its *other* headings are well formed. That silent
    partial loss is why this has to be caught at the spec itself.
    """
    malformed: list[str] = []
    for line in text.splitlines():
        if not CONTRACT_HEADING_CANDIDATE.match(line):
            continue
        if SPEC_REQUIREMENT_DEF_RE.match(line) or SPEC_SCENARIO_DEF_RE.match(line):
            continue
        malformed.append(line.strip())
    return malformed


def out_of_order_ids(text: str) -> list[str]:
    """REQ/SCN IDs whose number does not exceed every ID before it in the file.

    The rule is ascending, not contiguous. "删除后 ID 不复用" guarantees gaps
    (delete REQ-002 and 001/003 remain), so requiring contiguity would fire on
    exactly the state the other rule mandates. Equal numbers are left to the
    duplicate check so one mistake is not reported twice.
    """
    violations: list[str] = []
    for pattern in (SPEC_REQUIREMENT_DEF_RE, SPEC_SCENARIO_DEF_RE):
        highest = 0
        for match in pattern.finditer(text):
            number = int(match.group(1).rsplit("-", 1)[1])
            if number < highest:
                violations.append(match.group(1))
            highest = max(highest, number)
    return violations


def scenarios_without_requirement(text: str) -> list[str]:
    """Scenario IDs that no Requirement owns.

    ``requirements_without_scenario`` checks the other direction. A Scenario
    placed before the file's first Requirement, or directly under an operation
    section heading, belongs to nothing -- it is indexed as a defined scenario
    and then demands coverage for a behaviour no Requirement states.
    """
    orphans: list[str] = []
    current_requirement: str | None = None
    for line in text.splitlines():
        if SECTION_HEADING.match(line):
            # A new `## ` section closes the Requirement that came before it.
            current_requirement = None
            continue
        requirement = SPEC_REQUIREMENT_DEF_RE.match(line)
        if requirement:
            current_requirement = requirement.group(1)
            continue
        scenario = SPEC_SCENARIO_DEF_RE.match(line)
        if scenario and current_requirement is None:
            orphans.append(scenario.group(1))
    return orphans


def removed_requirements_missing_fields(text: str) -> list[str]:
    """``<REQ-ID>:<field>`` for REMOVED Requirements lacking Reason/Migration.

    A removal that does not say why or how to migrate leaves downstream stages
    guessing what to do with the old entry point.
    """
    section = REMOVED_SECTION.search(text)
    if not section:
        return []
    body = section.group("body")
    missing: list[str] = []
    matches = list(SPEC_REQUIREMENT_DEF_RE.finditer(body))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        block = body[match.end() : end]
        found = {
            field.group("name"): field.group("value").strip()
            for field in REMOVED_FIELD.finditer(block)
        }
        for name in ("Reason", "Migration"):
            value = found.get(name)
            if not value or PLACEHOLDER_TEXT.search(value):
                missing.append(f"{match.group(1)}:{name}")
    return missing


def placeholder_residue(text: str) -> list[str]:
    """Template placeholders left in a generated artifact.

    ``[REQ-001]`` / ``[SCN-001]`` are the real ID syntax and Markdown links are
    ordinary prose, so both are excluded -- what is left is a slot the author
    was supposed to fill.
    """
    residue = [match.group(0) for match in PLACEHOLDER_BRACKET.finditer(text)]
    residue += [match.group(0) for match in PLACEHOLDER_WORD.finditer(text)]
    return residue


def requirements_without_scenario(text: str) -> list[str]:
    """Requirement IDs whose own block carries no Scenario heading.

    A file-level "has at least one REQ and one SCN" check passes when three
    Requirements share a single Scenario, so slice per Requirement instead.
    Uses the same two module-level patterns the ID indexer uses; introducing a
    third spelling here is what let coverage go vacuous before.
    """
    matches = list(SPEC_REQUIREMENT_DEF_RE.finditer(text))
    barren: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        # A new `## ` section ends the Requirement even without a following `### `.
        next_section = re.search(r"^##\s+\S", block, re.MULTILINE)
        if next_section:
            block = block[: next_section.start()]
        if not SPEC_SCENARIO_DEF_RE.search(block):
            barren.append(match.group(1))
    return barren


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
            failures += fail_line(ctx, "invalid_design_missing_section", f" section={section!r}", target=section)

    no_http_api = design_marker_value(text, "x-auto-no-http-api")
    no_sql = design_marker_value(text, "x-auto-no-sql")
    if no_http_api is None:
        failures += fail_line(ctx, "missing_design_api_marker")
    if no_sql is None:
        failures += fail_line(ctx, "missing_design_data_marker")
    design_ids, _ = collect_design_definition_index(ctx)
    if no_http_api is True and design_ids["API"]:
        failures += fail_line(ctx, "design_api_marker_conflicts_with_definitions")
    if no_sql is True and design_ids["DATA"]:
        failures += fail_line(ctx, "design_data_marker_conflicts_with_definitions")
    pending = PENDING_CELL.findall(text)
    if pending:
        failures += fail_line(
            ctx,
            "design_has_pending_cells",
            f" count={len(pending)}",
            target=f"{len(pending)} 处",
        )
    failures += _unresolved_decision_refs(ctx, text)
    return failures


def _unresolved_decision_refs(ctx: HookContext, design_text: str) -> int:
    """Every ``DEC-NNN`` design cites must exist in the proposal's Decision Log.

    The lookup is scoped to the section, not the whole file: a ``### DEC-001:``
    heading dropped anywhere else in the proposal would otherwise resolve the
    reference, which makes the check report something weaker than it claims.

    Reference resolution only. Whether a decision is well argued, and whether
    every Requirement needs one, are judgements a script cannot make -- and the
    2026-07-29 trace showed that demanding a self-issued status word here just
    teaches the model to forge one. Existence is a fact about files, so that is
    all this checks; ``无`` stays a legal cell value.
    """
    cited = set(SPEC_DECISION_ID.findall(design_text))
    if not cited:
        return 0

    proposal = ctx.file("proposal.md")
    if not is_nonempty(proposal):
        # `proposal_contract` owns a missing proposal; do not report it twice.
        return 0

    section = DECISION_LOG_SECTION.search(read_text(proposal))
    defined = set(SPEC_DECISION_HEADING.findall(section.group("body"))) if section else set()
    missing = sorted(cited - defined)
    if not missing:
        return 0
    return fail_line(
        ctx,
        "design_decision_ref_unresolved",
        f" ids={','.join(missing)}",
        target=",".join(missing),
    )


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
            failures += fail_line(ctx, reason, f" file={rel}", target=str(rel))
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
        failures += fail_line(ctx, reason, target="design.md")
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


def _known_evidence_ids(ctx: HookContext) -> set[str]:
    try:
        return {
            evidence_id
            for evidence_id in (
                record.get("evidenceId")
                for record in read_records(stream_path(ctx.feature_dir))
            )
            if isinstance(evidence_id, str)
        }
    except EvidenceStoreError:
        return set()


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
        skill = record.get("skill")
        if not isinstance(evidence_id, str) or not isinstance(spec_refs, list):
            continue
        if skill != "autodev-e2e" or record.get("action") != "validation":
            continue
        validation = record.get("validation")
        if not isinstance(validation, dict):
            continue
        if str(validation.get("result", "")).lower() != "pass" or validation.get("exitCode") != 0:
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

    missing_rows = defined_scenarios - seen_scenarios
    if missing_rows:
        failures += fail_line(ctx, "missing_scenario_coverage_rows", f" field={field} ids={','.join(sorted(missing_rows))}")
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


def requirements_eval_verdict(text: str) -> str | None:
    section = REQUIREMENTS_EVAL_VERDICT_SECTION.search(text)
    if section is None:
        return None
    for line in section.group("body").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = (token.upper() for token in REVIEW_VERDICT_TOKEN.findall(stripped))
        unique = sorted({token for token in tokens if token in REVIEW_VERDICTS})
        return unique[0] if len(unique) == 1 else None
    return None


def requirements_eval_has_blockers(text: str) -> bool:
    section = REQUIREMENTS_EVAL_BLOCKERS_SECTION.search(text)
    if section is None:
        return False
    for match in REVIEW_BLOCKER_ITEM.finditer(section.group("body")):
        item = match.group("text").strip().strip("`*_ ").strip()
        if item and not REVIEW_BLOCKER_EMPTY.match(item):
            return True
    return False


def validate_requirements_eval_verdict(ctx: HookContext) -> int:
    eval_path = ctx.file("REQUIREMENTS_EVAL.md")
    if not is_nonempty(eval_path):
        return fail_line(
            ctx,
            "missing_requirements_eval",
            target="REQUIREMENTS_EVAL.md",
            repair="生成非空 REQUIREMENTS_EVAL.md 后重新完成 review 阶段。",
        )

    text = read_text(eval_path)
    verdict = requirements_eval_verdict(text)
    if verdict is None:
        return fail_line(
            ctx,
            "invalid_requirements_eval_verdict",
            target="REQUIREMENTS_EVAL.md",
            repair="在独立的 `## Verdict` 段写入唯一结论：PASS、PASS_WITH_WARNINGS、FAIL 或 DEGRADED。",
        )
    if verdict not in TERMINAL_PASS:
        return fail_line(
            ctx,
            "non_terminal_requirements_eval_verdict",
            f" verdict={verdict}",
            target="REQUIREMENTS_EVAL.md",
            repair="完成 blocker 修复并重新 review，只有 PASS 或 PASS_WITH_WARNINGS 可以结束阶段。",
        )
    if requirements_eval_has_blockers(text):
        return fail_line(
            ctx,
            "blocker_with_pass_requirements_eval_verdict",
            f" verdict={verdict}",
            target="REQUIREMENTS_EVAL.md",
            repair="Blockers 段仍有条目时将 verdict 记为 FAIL；修复并复审后再写 PASS 类结论。",
        )
    return 0


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
    for index, target in enumerate(targets):
        context = f"targets[{index}]"
        if not isinstance(target, dict):
            failures += fail_line(ctx, "invalid_unit_test_target", f" item={context}")
            continue
        failures += _check_string_field(ctx, target, "targetId", context=context)
        _, _, trace_failures = _check_trace_refs(ctx, target, context=context, require_task=True, require_evidence=True)
        failures += trace_failures
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


def _e2e_log_records(ctx: HookContext, *, pass_claimed: bool) -> tuple[list[dict], int]:
    path = ctx.file("e2e-run.log")
    if not is_nonempty(path):
        return [], fail_line(ctx, "missing_e2e_run_log")
    records: list[dict] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            if not records and line_no == 1 and not raw.lstrip().startswith(("{", "[")):
                if pass_claimed:
                    return [], fail_line(ctx, "legacy_e2e_log_cannot_support_pass")
                info(ctx, "legacy_e2e_log_read_only_degrade")
                return [], 0
            return [], fail_line(ctx, "invalid_e2e_run_log_json", f" line={line_no}")
        if not isinstance(value, dict):
            return [], fail_line(ctx, "invalid_e2e_run_log_record", f" line={line_no}")
        records.append(value)
    failures = 0
    run_ids: set[str] = set()
    for record in records:
        if record.get("kind") == "note":
            if any(
                not isinstance(record.get(field), str) or not record.get(field)
                for field in ("ts", "phase", "text")
            ):
                failures += fail_line(ctx, "invalid_e2e_note_record")
            continue
        if record.get("kind") != "verdict_run":
            failures += fail_line(ctx, "invalid_e2e_run_log_kind")
            continue
        run_id = record.get("runId")
        if not isinstance(run_id, str) or not run_id:
            failures += fail_line(ctx, "missing_e2e_log_run_id")
        elif run_id in run_ids:
            failures += fail_line(ctx, "duplicate_e2e_log_run_id", f" runId={run_id}")
        else:
            run_ids.add(run_id)
    return records, failures


def _e2e_evidence_index(ctx: HookContext) -> tuple[dict[str, dict], dict[str, str], int]:
    try:
        records = read_records(stream_path(ctx.feature_dir))
    except EvidenceStoreError as exc:
        return {}, {}, fail_line(ctx, "invalid_evidence_stream", f" detail={exc}")
    result: dict[str, dict] = {}
    run_ids: dict[str, str] = {}
    failures = 0
    for record in records:
        evidence_id = record.get("evidenceId")
        if isinstance(evidence_id, str):
            result[evidence_id] = record
        e2e_run = record.get("e2eRun")
        run_id = e2e_run.get("runId") if isinstance(e2e_run, dict) else None
        if isinstance(run_id, str):
            if run_id in run_ids:
                failures += fail_line(
                    ctx,
                    "duplicate_e2e_evidence_run_id",
                    f" runId={run_id} evidenceIds={run_ids[run_id]},{evidence_id}",
                )
            else:
                run_ids[run_id] = str(evidence_id)
    return result, run_ids, failures


def _valid_e2e_steps(ctx: HookContext, case: dict, *, context: str) -> int:
    failures = 0
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        return fail_line(ctx, "invalid_e2e_steps", f" item={context}")
    for index, step in enumerate(steps):
        step_context = f"{context}.steps[{index}]"
        if not isinstance(step, dict):
            failures += fail_line(ctx, "invalid_e2e_step", f" item={step_context}")
            continue
        for field in ("action", "expected"):
            failures += _check_string_field(ctx, step, field, context=step_context)
        verification = step.get("verification")
        if not isinstance(verification, dict):
            failures += fail_line(ctx, "missing_e2e_step_verification", f" item={step_context}")
            continue
        if str(verification.get("type", "")).lower() not in {"ui", "api", "database"}:
            failures += fail_line(ctx, "invalid_e2e_verification_type", f" item={step_context}")
        failures += _check_string_field(ctx, verification, "details", context=step_context)
    if case.get("uiRequired") is True and case.get("priority") in {"P0", "P1"}:
        final = steps[-1] if isinstance(steps[-1], dict) else {}
        verification = final.get("verification") if isinstance(final.get("verification"), dict) else {}
        if str(verification.get("type", "")).lower() != "ui":
            failures += fail_line(ctx, "ui_p0_p1_requires_final_ui_assertion", f" item={context}")
    return failures


def _diagnostic_paths_valid(ctx: HookContext, execution: dict, *, context: str) -> int:
    failures = 0
    paths = execution.get("diagnosticPaths")
    if not isinstance(paths, dict):
        return fail_line(ctx, "missing_e2e_diagnostic_paths", f" item={context}")
    for field in ("trace", "screenshot", "console", "network", "report"):
        if field not in paths:
            failures += fail_line(ctx, "missing_e2e_diagnostic_path_field", f" item={context} field={field}")
            continue
        value = paths.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            failures += fail_line(ctx, "invalid_e2e_diagnostic_path", f" item={context} field={field}")
            continue
        try:
            relative, resolved = normalize_relative_path(ctx.feature_dir, value, field)
        except ValueError:
            failures += fail_line(ctx, "e2e_diagnostic_path_outside_feature", f" item={context} field={field}")
            continue
        if not relative.startswith(DIAGNOSTICS_DIR + "/"):
            failures += fail_line(ctx, "e2e_diagnostic_path_outside_directory", f" item={context} field={field}")
        elif not resolved.is_file():
            failures += fail_line(ctx, "e2e_diagnostic_path_missing", f" item={context} field={field}")
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
    root_verdict = data.get("verdict")
    if not isinstance(root_verdict, str) or root_verdict.upper() not in {"PASS", "FAIL", "BLOCKED"}:
        failures += fail_line(ctx, "invalid_e2e_result_summary_verdict")
    elif ctx.requires_artifact("E2E_RESULT.json") and root_verdict.upper() not in TERMINAL_PASS:
        failures += fail_line(ctx, "non_terminal_e2e_result_verdict")
    if root_verdict == "PASS" and data.get("verdictSource") != "finalize":
        failures += fail_line(ctx, "e2e_pass_requires_finalize_source")

    current = data.get("currentRound")
    if not isinstance(current, dict):
        failures += fail_line(ctx, "missing_e2e_current_round")
        current = {}
    elif (
        not isinstance(current.get("index"), int)
        or current.get("index", 0) < 1
        or current.get("kind") not in {"initial", "repair"}
        or not isinstance(current.get("startedAt"), str)
    ):
        failures += fail_line(ctx, "invalid_e2e_current_round")
    repair_rounds = data.get("repairRounds")
    if not isinstance(repair_rounds, int) or not 0 <= repair_rounds <= 3:
        failures += fail_line(ctx, "invalid_e2e_repair_rounds")

    pass_claimed = root_verdict == "PASS"
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return failures + fail_line(ctx, "invalid_e2e_result_cases")
    pass_claimed = pass_claimed or any(
        isinstance(case, dict) and case.get("verdict") == "PASS" for case in cases
    )
    log_records, log_failures = _e2e_log_records(ctx, pass_claimed=pass_claimed)
    failures += log_failures
    verdict_logs = {
        record.get("runId"): record
        for record in log_records
        if record.get("kind") == "verdict_run" and isinstance(record.get("runId"), str)
    }
    evidence_by_id, evidence_run_ids, evidence_failures = _e2e_evidence_index(ctx)
    failures += evidence_failures
    quality_gate = data.get("qualityGate") if isinstance(data.get("qualityGate"), dict) else None
    if pass_claimed:
        _, quality_errors = validate_scan_current(ctx.feature_dir, quality_gate)
        for error in quality_errors:
            failures += fail_line(ctx, "invalid_e2e_quality_gate", f" detail={error}")

    all_execution_run_ids: set[str] = set()
    for index, case in enumerate(cases):
        context = f"cases[{index}]"
        if not isinstance(case, dict):
            failures += fail_line(ctx, "invalid_e2e_result_case", f" item={context}")
            continue
        failures += _check_string_field(ctx, case, "caseId", context=context)
        case_id = case.get("caseId")
        if isinstance(case_id, str) and not E2E_ID.fullmatch(case_id):
            failures += fail_line(ctx, "invalid_e2e_result_case_id", f" item={context}")
        _, _, trace_failures = _check_trace_refs(
            ctx,
            case,
            context=context,
            require_task=True,
            require_evidence=case.get("verdict") == "PASS",
        )
        failures += trace_failures
        if case.get("executionMode") not in {"browser", "api", "mixed", "database_assisted"}:
            failures += fail_line(ctx, "invalid_e2e_execution_mode", f" item={context}")
        if case.get("priority") not in {"P0", "P1", "P2"}:
            failures += fail_line(ctx, "invalid_e2e_priority", f" item={context}")
        if not isinstance(case.get("uiRequired"), bool):
            failures += fail_line(ctx, "invalid_e2e_ui_required", f" item={context}")
        failures += _valid_e2e_steps(ctx, case, context=context)
        case_verdict = case.get("verdict")
        if case_verdict not in {"PASS", "FAIL", "BLOCKED", "SKIP"}:
            failures += fail_line(ctx, "invalid_e2e_result_verdict", f" item={context}")
        if case_verdict == "PASS" and case.get("verdictSource") != "finalize":
            failures += fail_line(ctx, "e2e_case_pass_requires_finalize_source", f" item={context}")
        if root_verdict == "PASS" and case_verdict not in {"PASS", "SKIP"}:
            failures += fail_line(ctx, "e2e_result_summary_mismatch", f" item={context}")
        if case_verdict == "SKIP" and not isinstance(case.get("reason"), str):
            failures += fail_line(ctx, "e2e_skip_requires_reason", f" item={context}")

        executions = case.get("executions")
        if not isinstance(executions, list):
            executions = []
            if case_verdict == "PASS":
                failures += fail_line(ctx, "e2e_pass_without_executions", f" item={context}")
        current_pass = False
        for execution_index, execution in enumerate(executions):
            execution_context = f"{context}.executions[{execution_index}]"
            if not isinstance(execution, dict):
                failures += fail_line(ctx, "invalid_e2e_execution", f" item={execution_context}")
                continue
            run_id = execution.get("runId")
            if not isinstance(run_id, str) or not run_id:
                failures += fail_line(ctx, "missing_e2e_execution_run_id", f" item={execution_context}")
                continue
            if run_id in all_execution_run_ids:
                failures += fail_line(ctx, "duplicate_e2e_execution_run_id", f" runId={run_id}")
            all_execution_run_ids.add(run_id)
            failures += _diagnostic_paths_valid(ctx, execution, context=execution_context)
            process_code = execution.get("processExitCode")
            gate_code = execution.get("gateExitCode")
            result = execution.get("result")
            if not isinstance(process_code, int) or not isinstance(gate_code, int):
                failures += fail_line(ctx, "invalid_e2e_exit_code", f" item={execution_context}")
            elif gate_code == 0 and process_code != 0:
                failures += fail_line(ctx, "e2e_gate_cannot_relax_process", f" item={execution_context}")
            if result not in {"PASS", "FAIL", "FLAKY", "BLOCKED"}:
                failures += fail_line(ctx, "invalid_e2e_execution_result", f" item={execution_context}")
            if execution.get("executionPhase") != "verdict" or execution.get("executionAdapter") != "playwright_test":
                failures += fail_line(ctx, "invalid_e2e_execution_adapter", f" item={execution_context}")
            evidence_id = execution.get("evidenceId")
            evidence = evidence_by_id.get(evidence_id) if isinstance(evidence_id, str) else None
            if evidence is None:
                failures += fail_line(ctx, "missing_e2e_execution_evidence", f" item={execution_context}")
                continue
            if evidence.get("skill") != "autodev-e2e" or evidence.get("action") != "validation":
                failures += fail_line(ctx, "invalid_e2e_execution_evidence_source", f" item={execution_context}")
            for error in validate_execution_evidence_chain(
                execution,
                evidence,
                case.get("caseId"),
                case.get("taskId"),
                case.get("specRefs"),
            ):
                failures += fail_line(
                    ctx,
                    "invalid_e2e_evidence_chain",
                    f" item={execution_context} detail={error}",
                )
            log = verdict_logs.get(run_id)
            if log is None:
                failures += fail_line(ctx, "missing_e2e_verdict_log", f" item={execution_context}")
            else:
                for error in validate_execution_log_chain(
                    execution,
                    evidence_id,
                    log,
                    case.get("caseId"),
                    case.get("taskId"),
                    case.get("specRefs"),
                ):
                    failures += fail_line(
                        ctx,
                        "e2e_log_execution_mismatch",
                        f" item={execution_context} detail={error}",
                    )
            if (
                result == "PASS"
                and gate_code == 0
                and execution.get("roundIndex") == current.get("index")
                and is_fresh(evidence.get("createdAt"), current.get("startedAt"))
            ):
                current_pass = True
                for error in validate_execution_hash_chain(
                    ctx.feature_dir, quality_gate, execution, evidence
                ):
                    failures += fail_line(
                        ctx,
                        "invalid_e2e_hash_chain",
                        f" item={execution_context} detail={error}",
                    )
        if case_verdict == "PASS" and not current_pass:
            failures += fail_line(ctx, "e2e_pass_without_fresh_execution", f" item={context}")

    log_run_ids = {
        record.get("runId")
        for record in log_records
        if record.get("kind") == "verdict_run" and isinstance(record.get("runId"), str)
    }
    if all_execution_run_ids != log_run_ids:
        failures += fail_line(ctx, "e2e_execution_log_run_set_mismatch")
    if all_execution_run_ids != set(evidence_run_ids):
        failures += fail_line(ctx, "e2e_execution_evidence_run_set_mismatch")

    coverage = data.get("scenarioCoverage")
    if isinstance(coverage, list):
        for row_index, row in enumerate(coverage):
            if not isinstance(row, dict) or str(row.get("verdict", "")).lower() != "pass":
                continue
            for evidence_id in row.get("evidenceIds", []) if isinstance(row.get("evidenceIds"), list) else []:
                evidence = evidence_by_id.get(evidence_id)
                if (
                    evidence is None
                    or evidence.get("skill") != "autodev-e2e"
                    or evidence.get("action") != "validation"
                ):
                    failures += fail_line(
                        ctx,
                        "invalid_e2e_coverage_evidence_source",
                        f" item=scenarioCoverage[{row_index}] evidenceId={evidence_id}",
                    )
                elif not is_fresh(evidence.get("createdAt"), current.get("startedAt")):
                    failures += fail_line(
                        ctx,
                        "stale_e2e_coverage_evidence",
                        f" item=scenarioCoverage[{row_index}] evidenceId={evidence_id}",
                    )
    failures += _validate_scenario_coverage(
        ctx,
        data,
        field="scenarioCoverage",
        required=True,
        require_pass_evidence=True,
        covering_evidence=_e2e_scenario_covering_evidence(ctx),
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
    return failures


def _artifact_issue_target(issue: dict, fallback: str) -> str:
    task_ids = issue.get("taskIds")
    task_id = ""
    if isinstance(task_ids, list):
        task_id = ",".join(str(item) for item in task_ids if item)

    field = issue.get("field")
    location = ".".join(
        item for item in (task_id, str(field) if field else "") if item
    )
    current_value = issue.get("currentValue")
    if current_value not in (None, ""):
        return f"{location} {current_value}".strip()
    return location or fallback


def _emit_artifact_issue(ctx: HookContext, issue: dict, fallback: str) -> int:
    reason = str(issue.get("reason") or "invalid_artifact_ref")
    detail = str(issue.get("detail") or "")
    return fail_line(
        ctx,
        reason,
        f" {detail}" if detail else "",
        target=_artifact_issue_target(issue, fallback),
        fields={"detail": detail},
    )


def _validate_plan_json_traceability(ctx: HookContext, data: dict) -> int:
    failures = 0
    spec_ids, spec_failures = collect_spec_definition_index(ctx)
    design_contract, design_issues = load_design_contract(ctx.feature_dir)
    failures += spec_failures
    for issue in design_issues:
        failures += _emit_artifact_issue(ctx, issue, "design.md")

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return failures

    for index, task in enumerate(raw_tasks):
        context = f"tasks[{index}]"
        if not isinstance(task, dict):
            continue
        task_id = task.get("id") if isinstance(task.get("id"), str) else context

        spec_refs = _string_list_value(task.get("specRefs")) or []
        req_refs = set(REQ_ID.findall(" ".join(spec_refs)))
        scn_refs = _scenario_refs_from_spec_refs(spec_refs)
        if not req_refs:
            failures += fail_line(ctx, "missing_plan_json_requirement_ref", f" task={task_id}", target=task_id)
        if not scn_refs:
            failures += fail_line(ctx, "missing_plan_json_scenario_ref", f" task={task_id}", target=task_id)
        for req_id in sorted(req_refs):
            if req_id not in spec_ids["REQ"]:
                failures += fail_line(
                    ctx,
                    "unknown_plan_json_requirement_ref",
                    f" task={task_id} id={req_id}",
                    target=f"{task_id}.specRefs {req_id}",
                )
        for scn_id in sorted(scn_refs):
            if scn_id not in spec_ids["SCN"]:
                failures += fail_line(
                    ctx,
                    "unknown_plan_json_scenario_ref",
                    f" task={task_id} id={scn_id}",
                    target=f"{task_id}.specRefs {scn_id}",
                )

        for issue in validate_task_artifact_refs(
            ctx.feature_dir,
            task,
            design_contract=design_contract,
        ):
            failures += _emit_artifact_issue(ctx, issue, task_id)
    for issue in validate_plan_design_coverage(design_contract, raw_tasks):
        reason = str(issue.get("reason") or "")
        fallback = {
            "missing_design_api_id": "API Decisions",
            "missing_design_data_id": "Data Decisions",
        }.get(reason, "plan.json")
        failures += _emit_artifact_issue(ctx, issue, fallback)
    return failures


def _plan_task_string_list(task: dict, field: str) -> list[str]:
    return _string_list_value(task.get(field)) or []


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
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}", target=str(error))
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json", target="plan.json")

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
                target=task_id,
                fields={"detail": error.get("detail", "")},
                diagnostics=error,
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
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}", target=str(error))
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json", target="plan.json")

    covered_refs: set[str] = set()
    raw_tasks = data.get("tasks")
    if isinstance(raw_tasks, list):
        for task in raw_tasks:
            if not isinstance(task, dict):
                continue
            covered_refs.update(_covered_spec_scenario_refs(_plan_task_string_list(task, "specRefs"), refs_by_id))

    missing_refs = expected_refs - covered_refs
    if missing_refs:
        failures += fail_line(
            ctx,
            "missing_plan_scenario_coverage",
            f" ids={','.join(sorted(missing_refs))}",
            target=",".join(sorted(missing_refs)),
        )
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
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}", target=str(error))
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json", target="plan.json")

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
            failures += fail_line(
                ctx,
                error.get("reason", "invalid_artifact_ref"),
                suffix,
                target=task_id,
                fields={"detail": detail},
            )
    return failures


def validate_plan_json_initial_tasks(ctx: HookContext) -> int:
    if not ctx.requires_artifact("plan.json") and not is_nonempty(ctx.file("plan.json")):
        if is_nonempty(ctx.file("PLAN.md")):
            return fail_line(
                ctx,
                "missing_plan_json",
                " detail=PLAN.md_present_but_not_machine_source",
                target="plan.json（当前只有 PLAN.md，它是投影视图不是事实源）",
            )
        info(ctx, "plan_json_not_in_contract_degrade")
        return 0
    data, errors = load_and_validate_plan(ctx.file("plan.json"), require_initial_status=True)
    failures = 0
    for error in errors:
        failures += fail_line(ctx, "invalid_plan_json", f" detail={error}", target=str(error))
    if data is not None and not errors and data.get("taskSetStatus") != "finalized":
        failures += fail_line(ctx, "plan_task_set_not_finalized", target="plan.json.taskSetStatus")
    return failures


def validate_plan_json_contract(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        if is_nonempty(ctx.file("PLAN.md")):
            return fail_line(
                ctx,
                "missing_plan_json",
                " detail=PLAN.md_present_but_not_machine_source",
                target="plan.json（当前只有 PLAN.md，它是投影视图不是事实源）",
            )
        info(ctx, "plan_json_not_in_contract_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json)
    failures = 0
    if errors:
        for error in errors:
            failures += fail_line(ctx, "invalid_plan_json", f" detail={error}", target=str(error))
        return failures
    if data is None:
        return fail_line(ctx, "missing_plan_json", target="plan.json")
    implementation_scope, scope_errors = _implementation_scope_contract_errors(ctx)
    for error in scope_errors:
        failures += fail_line(ctx, "invalid_implementation_scope", f" detail={error}", target=error)
    if scope_path(ctx.feature_dir).is_file() and not scope_errors:
        plan_scope = data.get("implementationScope")
        if plan_scope != implementation_scope:
            failures += fail_line(
                ctx,
                "plan_implementation_scope_mismatch",
                f" plan={plan_scope!r} feature={implementation_scope!r}",
                target=f"plan={plan_scope!r} feature={implementation_scope!r}",
            )
    failures += _validate_plan_json_traceability(ctx, data)
    return failures


def validate_plan_task_detail_schema(ctx: HookContext) -> int:
    plan_json = ctx.file("plan.json")
    if not ctx.requires_artifact("plan.json") and not is_nonempty(plan_json):
        info(ctx, "plan_task_detail_schema_not_in_contract_degrade")
        return 0

    data, errors = load_and_validate_plan(plan_json, require_task_details=True)
    failures = 0
    for error in errors:
        failures += fail_line(ctx, "invalid_plan_json", f" detail={error}", target=str(error))
    if data is None:
        return failures or fail_line(ctx, "missing_plan_json", target="plan.json")
    return failures


def validate_code_done_gate(ctx: HookContext) -> int:
    if not ctx.requires_artifact("evidence/EVIDENCE.jsonl"):
        info(ctx, "code_done_gate_not_in_contract_degrade")
        return 0
    failures = 0
    for error in check_code_done(ctx.feature_dir):
        failures += fail_line(ctx, "invalid_code_done_gate", f" detail={error}")
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


def validate_e2e_cases_contract(ctx: HookContext) -> int:
    """``E2E_TEST_CASES.yaml`` 与运行日志的形状检查。

    与 ``e2e_result_json`` 分工：那个查执行结果的 JSON 结构，这个查用例文件
    本身——每个用例带得上 E2E/REQ/SCN 三种 ID、声明了执行方式与 UI 需求、
    并且真有一份运行日志。结果 JSON 合法不代表用例文件写全了。
    """
    cases = ctx.file("E2E_TEST_CASES.yaml")
    log = ctx.file("e2e-run.log")

    if not is_nonempty(cases):
        if not ctx.requires_artifact("E2E_TEST_CASES.yaml"):
            info(ctx, "e2e_cases_not_in_contract_degrade")
            return 0
        return fail_line(ctx, "missing_e2e_cases")

    failures = 0
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
    yaml_case_ids = set(E2E_ID.findall(cases_text))
    result_path = ctx.file("E2E_RESULT.json")
    if is_nonempty(result_path):
        try:
            result_data = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result_data = None
        result_cases = result_data.get("cases") if isinstance(result_data, dict) else None
        if isinstance(result_cases, list):
            result_case_ids = {
                case.get("caseId")
                for case in result_cases
                if isinstance(case, dict) and isinstance(case.get("caseId"), str)
            }
            missing_in_result = yaml_case_ids - result_case_ids
            missing_in_yaml = result_case_ids - yaml_case_ids
            if missing_in_result:
                failures += fail_line(
                    ctx,
                    "e2e_case_ids_missing_in_result",
                    f" ids={','.join(sorted(missing_in_result))}",
                )
            if missing_in_yaml:
                failures += fail_line(
                    ctx,
                    "e2e_case_ids_missing_in_yaml",
                    f" ids={','.join(sorted(missing_in_yaml))}",
                )
    return failures


VALIDATORS = {
    "e2e_cases_contract": validate_e2e_cases_contract,
    "proposal_contract": validate_proposal_contract,
    "specs_contract": validate_specs_contract,
    "capability_spec_correspondence": validate_capability_spec_correspondence,
    "design_contract": validate_design_contract,
    "plan_json_contract": validate_plan_json_contract,
    "plan_json_initial_tasks": validate_plan_json_initial_tasks,
    "plan_task_granularity": validate_plan_task_granularity,
    "plan_scenario_coverage": validate_plan_scenario_coverage,
    "plan_ref_resolution": validate_plan_ref_resolution,
    "plan_task_detail_schema": validate_plan_task_detail_schema,
    "evidence_detail_quality": validate_evidence_detail_quality,
    "code_done_gate": validate_code_done_gate,
    "evidence_integrity": validate_evidence_integrity,
    "requirements_eval_verdict": validate_requirements_eval_verdict,
    "unit_test_result_json": validate_unit_test_result_json,
    "e2e_result_json": validate_e2e_result_json,
    "verify_decision_json": validate_verify_decision_json,
    "fix_request_json": validate_fix_request_json,
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
        # 必备产物缺失是 specs/plan 最常见的失败，而它发生在 validator 跑起来之前。
        # 不在这里补一条结构化失败行，调用方只能拿到一句没有修复动作的兜底文本。
        fail_line(
            HookContext(skill=skill, slug=slug, root=workspace_root),
            error.reason,
            f" detail={error.detail}" if error.detail else "",
            target=error.detail,
        )
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
