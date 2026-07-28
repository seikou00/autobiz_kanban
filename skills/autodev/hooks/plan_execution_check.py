#!/usr/bin/env python3
"""Code 入场校验：PLAN.md 引用完整性、任务 DAG 合法性。

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
    if design_path.is_file() and design_path.stat().st_size > 0:
        design_ids = set(DESIGN_ID_ROW.findall(read_text(design_path)))

    # ① 引用完整性（上游为 legacy 无 ID 时跳过对应维度）
    for task_id, block in tasks.items():
        req_refs, scn_refs, design_refs = ref_tokens(block)
        if spec_req_ids:
            for ref in sorted(req_refs - spec_req_ids):
                failures += fail("missing_req_ref", f"task={task_id} id={ref}")
        if spec_scn_ids:
            for ref in sorted(scn_refs - spec_scn_ids):
                failures += fail("missing_scn_ref", f"task={task_id} id={ref}")
        if design_ids:
            for ref in sorted(design_refs - design_ids):
                failures += fail("missing_design_ref", f"task={task_id} id={ref}")
    if tasks and not spec_req_ids:
        print("PLAN_CHECK_INFO reason=legacy_specs_skip_req_ref_check")
    if tasks and not design_ids:
        print("PLAN_CHECK_INFO reason=legacy_design_skip_design_ref_check")

    # ② DAG 合法性
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
