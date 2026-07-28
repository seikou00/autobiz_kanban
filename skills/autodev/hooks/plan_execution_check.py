#!/usr/bin/env python3
"""Code 入场校验：PLAN.md 引用完整性、design 决策覆盖、任务 DAG 合法性。

引用完整性是 PLAN→design 方向（任务引用的 ID 在 design 里存在）；覆盖是
design→PLAN 方向（design 的每个 API/DATA/D 决策都被某个任务的「设计依据」认领，
或在 Contract Coverage 标注「无需实现:<理由>」）。两向都要查：code 只展开任务
引用的决策，只查前一向的话，design 里的决策可以在 PLAN 生成时被静默丢弃。

用法：
    python plan_execution_check.py <feature-slug> [--workspace-root PATH]

退出码：0 = PASS / LEGACY_PLAN_DEGRADE / PLAN_NOT_FOUND（精简工作流）；1 = 校验失败。
失败时逐行输出 `PLAN_CHECK_FAIL reason=... detail=...`，最后输出总verdict行。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from artifact_check import REQ_HEADING, SCN_HEADING, section_text  # noqa: E402
from common import plan_task_blocks, read_text  # noqa: E402


OVERVIEW_ROW = re.compile(
    r"^\|\s*(TASK-\d{3})\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|", re.MULTILINE
)
DESIGN_ID_ROW = re.compile(r"^\|\s*`?((?:API|DATA|EVD)-\d{1,3}|D-\d{1,3})`?\s*\|", re.MULTILINE)
REQ_TOKEN = re.compile(r"\bREQ-[a-z0-9][a-z0-9-]*-\d{3}\b")
SCN_TOKEN = re.compile(r"\bSCN-[a-z0-9][a-z0-9-]*-\d{3}-\d{2}\b")
DESIGN_TOKEN = re.compile(r"\b((?:API|DATA|EVD)-\d{1,3}|D-\d{1,3})\b")
REF_LINE_KEYS = ("规格依据", "场景依据", "设计依据", "代码证据")
# 新格式 design.md 必写这两个标记之一；认得出新格式就不再走 legacy degrade。
DESIGN_FORMAT_MARKER = re.compile(r"x-auto-no-(?:http-api|sql)")
# API/DATA/D 是需要落地的决策；EVD 是代码事实，不需要被任务认领。
DECISION_PREFIXES = ("API-", "DATA-", "D-")
WAIVER_MARKER = "无需实现"
WAIVER_REASON = re.compile(rf"{WAIVER_MARKER}\s*[:：]\s*(\S.*)")
PLACEHOLDER_CELL = re.compile(r"^\[[^\]]*\]$")
SEPARATOR_CHARS = set(":- ")


def fail(reason: str, detail: str = "") -> int:
    suffix = f" detail={detail}" if detail else ""
    print(f"PLAN_CHECK_FAIL reason={reason}{suffix}")
    return 1


def ref_tokens(block: str) -> tuple[set[str], set[str], set[str]]:
    req: set[str] = set()
    scn: set[str] = set()
    design: set[str] = set()
    for line in block.splitlines():
        stripped = line.strip()
        if not any(f"**{key}:**" in stripped for key in REF_LINE_KEYS):
            continue
        req.update(REQ_TOKEN.findall(stripped))
        scn.update(SCN_TOKEN.findall(stripped))
        design.update(DESIGN_TOKEN.findall(stripped))
    return req, scn, design


def coverage_waivers(plan_text: str) -> tuple[set[str], set[str]]:
    """Contract Coverage 中「覆盖任务」列标注「无需实现」的契约 ID。

    返回 ``(已豁免 ID, 理由缺失的 ID)``。豁免必须带理由——否则「无需实现」
    就成了免费出口，反向覆盖校验等于没加。
    """
    body = section_text(plan_text, "Contract Coverage")
    if not body:
        return set(), set()

    waived: set[str] = set()
    reasonless: set[str] = set()
    task_column: int | None = None
    header_seen = False

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= SEPARATOR_CHARS for cell in cells if cell):
            continue
        if not header_seen:
            header_seen = True
            for index, cell in enumerate(cells):
                if "覆盖任务" in cell:
                    task_column = index
            continue
        index = task_column if task_column is not None else 3
        if index >= len(cells) or WAIVER_MARKER not in cells[index]:
            continue
        ids = [
            found
            for found in DESIGN_TOKEN.findall(cells[0])
            if found.startswith(DECISION_PREFIXES)
        ]
        reason = WAIVER_REASON.search(cells[index])
        if reason and not PLACEHOLDER_CELL.match(reason.group(1).strip()):
            waived.update(ids)
        else:
            reasonless.update(ids)
    return waived, reasonless


def detect_cycle(deps: dict[str, set[str]]) -> list[str]:
    indegree = {task: 0 for task in deps}
    for targets in deps.values():
        for target in targets:
            if target in indegree:
                indegree[target] += 1
    # Kahn：deps 记录「task 依赖 target」，反向图无所谓，只判断是否有环
    queue = [task for task, degree in indegree.items() if degree == 0]
    visited = 0
    graph: dict[str, set[str]] = {task: set() for task in deps}
    for task, targets in deps.items():
        for target in targets:
            if target in graph:
                graph[target].add(task)
    while queue:
        node = queue.pop()
        visited += 1
        for follower in graph[node]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    if visited == len(deps):
        return []
    return sorted(task for task, degree in indegree.items() if degree > 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Code 入场校验：PLAN 引用/DAG")
    parser.add_argument("slug")
    parser.add_argument("--workspace-root", default=str(Path.cwd().resolve()))
    args = parser.parse_args(argv)

    feature_dir = Path(args.workspace_root).resolve() / ".autobizdevops" / "features" / args.slug
    plan_path = feature_dir / "PLAN.md"
    if not plan_path.is_file() or plan_path.stat().st_size == 0:
        print("PLAN_CHECK_VERDICT verdict=PLAN_NOT_FOUND")
        return 0

    plan_text = read_text(plan_path)
    tasks = plan_task_blocks(plan_text)

    if not tasks:
        print("PLAN_CHECK_VERDICT verdict=LEGACY_PLAN_DEGRADE")
        return 0

    failures = 0

    # 上游 ID 集合
    spec_req_ids: set[str] = set()
    spec_scn_ids: set[str] = set()
    for spec in sorted(feature_dir.glob("specs/**/*.md")):
        if not spec.is_file() or spec.stat().st_size == 0:
            continue
        text = read_text(spec)
        for match in REQ_HEADING.finditer(text):
            spec_req_ids.add(f"REQ-{match.group(1)}-{match.group(2)}")
        for match in SCN_HEADING.finditer(text):
            spec_scn_ids.add(f"SCN-{match.group(1)}-{match.group(2)}-{match.group(3)}")

    design_path = feature_dir / "design.md"
    design_ids: set[str] = set()
    design_structured = False
    if design_path.is_file() and design_path.stat().st_size > 0:
        design_text = read_text(design_path)
        design_ids = set(DESIGN_ID_ROW.findall(design_text))
        # 「本轮真的无 API/无 SQL」是模板鼓励的合法状态，不能和「legacy 无 ID 体系」
        # 共用同一个跳过分支——否则新格式 design.md 一旦没有 ID 行，引用校验就整体失效。
        design_structured = bool(DESIGN_FORMAT_MARKER.search(design_text))

    # ① 引用完整性（上游为 legacy 无 ID 时跳过对应维度）
    design_checked = bool(design_ids or design_structured)
    claimed_design: set[str] = set()
    for task_id, block in tasks.items():
        req_refs, scn_refs, design_refs = ref_tokens(block)
        claimed_design |= design_refs
        if spec_req_ids:
            for ref in sorted(req_refs - spec_req_ids):
                failures += fail("missing_req_ref", f"task={task_id} id={ref}")
        if spec_scn_ids:
            for ref in sorted(scn_refs - spec_scn_ids):
                failures += fail("missing_scn_ref", f"task={task_id} id={ref}")
        if design_checked:
            for ref in sorted(design_refs - design_ids):
                failures += fail("missing_design_ref", f"task={task_id} id={ref}")
    if tasks and not spec_req_ids:
        print("PLAN_CHECK_INFO reason=legacy_specs_skip_req_ref_check")
    if tasks and not design_checked:
        print("PLAN_CHECK_INFO reason=legacy_design_skip_design_ref_check")

    # ② 反向覆盖：design 的每个 API/DATA/D 决策都要被任务认领，或在 Contract
    #    Coverage 显式标注「无需实现:<理由>」。缺了这一向，裁定门产出的决策可以在
    #    PLAN 生成时被静默丢弃，而 code 只按任务「设计依据」展开，永远读不到它。
    if design_checked:
        waived, reasonless = coverage_waivers(plan_text)
        # 任务认领优先于豁免措辞：模板把「TASK-002 / 无需实现:<原因>」两种写法并排
        # 摆在同一格，已被任务认领的 ID 不该因为同格还留着豁免措辞而报错。
        reasonless -= claimed_design
        for ref in sorted(reasonless):
            failures += fail("waiver_missing_reason", f"id={ref}")
        decisions = {i for i in design_ids if i.startswith(DECISION_PREFIXES)}
        # 理由缺失的已按 waiver_missing_reason 报过，不再重复报为未覆盖：
        # 同一根因报两条只会让失败清单变噪音。
        for ref in sorted(decisions - claimed_design - waived - reasonless):
            failures += fail("uncovered_design_decision", f"id={ref}")

    # ③ DAG 合法性
    overview: dict[str, set[str]] = {}
    for match in OVERVIEW_ROW.finditer(plan_text):
        task_id = match.group(1)
        dep_cell = match.group(3)
        overview[task_id] = set(re.findall(r"TASK-\d{3}", dep_cell))
    if tasks and not overview:
        failures += fail("missing_task_overview_rows")
    for task_id, deps in overview.items():
        for dep in sorted(deps):
            if dep not in overview:
                failures += fail("unknown_dependency", f"task={task_id} dep={dep}")
            if dep == task_id:
                failures += fail("self_dependency", f"task={task_id}")
    cycle_nodes = detect_cycle(overview) if overview else []
    if cycle_nodes:
        failures += fail("dependency_cycle", f"tasks={','.join(cycle_nodes)}")
    overview_set, detail_set = set(overview), set(tasks)
    for task_id in sorted(overview_set - detail_set):
        failures += fail("task_missing_detail", f"task={task_id}")
    for task_id in sorted(detail_set - overview_set):
        failures += fail("task_missing_overview_row", f"task={task_id}")

    if failures:
        print("PLAN_CHECK_VERDICT verdict=FAIL")
        return 1
    print("PLAN_CHECK_VERDICT verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
