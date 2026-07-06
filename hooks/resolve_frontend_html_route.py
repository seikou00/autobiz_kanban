#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve and record the required frontend HTML route for autodev-code."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.paths import get_plugin_output_workspace, resolve_env_feature  # noqa: E402


EVIDENCE_NAME = "FRONTEND_ROUTE.json"
ROUTE_ABSOLUTE = "absolute-html"
ROUTE_STANDARD = "standard-html"
ROUTE_SPEC_DRIVEN = "spec-driven-ui"
ROUTE_MISSING = "missing-html"
ROUTE_NONE = "none"
VALID_ROUTES = {ROUTE_ABSOLUTE, ROUTE_STANDARD, ROUTE_SPEC_DRIVEN, ROUTE_MISSING, ROUTE_NONE}
VALID_REVIEW_STATUSES = {"passed", "has-suggestions", "skipped-by-user", "failed"}

FRONTEND_ROOT = ROOT / "skills" / "autodev" / "autodev-code" / "references" / "frontend-html"
ROUTE_SKILLS = {
    ROUTE_ABSOLUTE: FRONTEND_ROOT / "with-absolute-html" / "SKILL.md",
    ROUTE_STANDARD: FRONTEND_ROOT / "with-standard-html" / "SKILL.md",
}
PARSERS = {
    ROUTE_ABSOLUTE: FRONTEND_ROOT / "with-absolute-html" / "references" / "html-parser.md",
    ROUTE_STANDARD: FRONTEND_ROOT / "with-standard-html" / "references" / "standard-html-parser.md",
}

DOC_NAMES = ("PLAN.md", "DETAIL_DESIGN.md", "design.md", "proposal.md", "PRD.md")
HTML_PATH_RE = re.compile(r"[A-Za-z]:[^\s\"'<>|]+?\.html?", re.IGNORECASE)
FRONTEND_INTENT_RE = re.compile(
    r"(前端|页面|组件|Vue|React|ElementUI|AntD|tsx|jsx|\.vue)", re.IGNORECASE
)
HTML_INTENT_RE = re.compile(r"(HTML|DOM|设计导出|设计稿|静态页面|Figma|MasterGo)", re.IGNORECASE)


class FrontendRouteError(ValueError):
    """Raised when the frontend route cannot be resolved from machine facts."""


def _import_ui_context_helpers():
    try:
        from hooks.ui_context import UIContextError, load_ui_context  # noqa: PLC0415
        from hooks.plan_json import load_plan  # noqa: PLC0415
    except Exception:
        return None, None, None
    return UIContextError, load_ui_context, load_plan


def feature_dir(workspace: Path, feature: str) -> Path:
    return workspace / ".autobizdevops" / "features" / feature


def evidence_path(workspace: Path, feature: str) -> Path:
    return feature_dir(workspace, feature) / EVIDENCE_NAME


def read_text(path: Path, *, limit: int = 2_000_000) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return content[:limit]


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_path(raw: str, cwd: Path | None = None) -> Path:
    path = Path(raw.strip().strip("\"'").replace("\\", "/")).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    return path.resolve(strict=False)


def existing_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            result.append(path)
    return result


def collect_doc_texts(fd: Path) -> tuple[str, list[Path]]:
    docs: list[Path] = []
    chunks: list[str] = []
    for name in DOC_NAMES:
        path = fd / name
        if path.is_file():
            docs.append(path)
            chunks.append(read_text(path))
    specs_dir = fd / "specs"
    if specs_dir.is_dir():
        for path in sorted(specs_dir.glob("**/*.md")):
            if path.is_file():
                docs.append(path)
                chunks.append(read_text(path, limit=300_000))
    return "\n".join(chunks), docs


def collect_html_sources(workspace: Path, feature: str, html_files: Iterable[str]) -> list[Path]:
    fd = feature_dir(workspace, feature)
    candidates: list[Path] = [normalize_path(raw) for raw in html_files if raw.strip()]

    html_dir = fd / "frontend-html"
    if html_dir.is_dir():
        candidates.extend(path.resolve(strict=False) for path in html_dir.rglob("*.htm*"))

    docs_text, _ = collect_doc_texts(fd)
    candidates.extend(normalize_path(match.group(0)) for match in HTML_PATH_RE.finditer(docs_text))
    return existing_paths(candidates)


def _visual_source_candidate_path(workspace: Path, fd: Path, raw_path: str) -> Path:
    normalized = normalize_path(raw_path, workspace)
    if not normalized.is_file():
        normalized = normalize_path(raw_path, fd)
    return normalized


def _existing_visual_source_paths(workspace: Path, fd: Path, visual_sources: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for source in visual_sources:
        raw_path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        paths.append(_visual_source_candidate_path(workspace, fd, raw_path))
    return existing_paths(paths)


def _missing_declared_html_paths(workspace: Path, fd: Path, visual_sources: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for source in visual_sources:
        if not isinstance(source, dict):
            continue
        source_type = source.get("type")
        route = source.get("route")
        required = source.get("required") is True
        declares_html = (
            source_type in {"high_fidelity_html", "standard_html"}
            or route in {ROUTE_ABSOLUTE, ROUTE_STANDARD, ROUTE_MISSING}
            or required
        )
        raw_path = source.get("path")
        if not declares_html or not isinstance(raw_path, str) or not raw_path.strip():
            continue
        normalized = _visual_source_candidate_path(workspace, fd, raw_path)
        if not normalized.is_file():
            missing.append(str(normalized))
    return missing


def _html_request_message(fd: Path, missing_paths: list[str]) -> str:
    path_hint = f" 缺失路径: {', '.join(missing_paths)}。" if missing_paths else ""
    return (
        "前面阶段已声明需要 HTML/高保真视觉输入，但 code 阶段没有找到可读取的 HTML 文件。"
        f"{path_hint}"
        f"请先引导用户提供 HTML 文件，优先放到 {fd / 'frontend-html'}，"
        "或本轮运行 resolve_frontend_html_route.py 时追加 --html-file <HTML_PATH>。"
        "如果用户不提供，本轮按 spec-driven-ui 的无高保真流程继续，不因缺少 HTML 阻断 code 阶段。"
    )


def _route_from_visual_sources(visual_sources: list[dict[str, Any]], html_sources: list[Path]) -> tuple[str, list[str]]:
    visual_routes = [
        source.get("route")
        for source in visual_sources
        if isinstance(source, dict) and isinstance(source.get("route"), str)
    ]
    if ROUTE_ABSOLUTE in visual_routes:
        return (
            ROUTE_ABSOLUTE if html_sources else ROUTE_MISSING,
            ["UI_CONTEXT visualSources route=absolute-html"],
        )
    if ROUTE_STANDARD in visual_routes:
        return (
            ROUTE_STANDARD if html_sources else ROUTE_MISSING,
            ["UI_CONTEXT visualSources route=standard-html"],
        )
    if ROUTE_MISSING in visual_routes:
        return ROUTE_MISSING, ["UI_CONTEXT visualSources route=missing-html"]
    if ROUTE_SPEC_DRIVEN in visual_routes:
        return ROUTE_SPEC_DRIVEN, ["UI_CONTEXT visualSources route=spec-driven-ui"]
    visual_types = {
        source.get("type")
        for source in visual_sources
        if isinstance(source, dict) and isinstance(source.get("type"), str)
    }
    if "high_fidelity_html" in visual_types:
        return (ROUTE_ABSOLUTE if html_sources else ROUTE_MISSING), ["UI_CONTEXT high_fidelity_html source"]
    if "standard_html" in visual_types:
        return (ROUTE_STANDARD if html_sources else ROUTE_MISSING), ["UI_CONTEXT standard_html source"]
    if html_sources:
        route, reasons = classify_html(html_sources)
        return route, [f"UI_CONTEXT visual source classified as {route}", *reasons]
    if any(source.get("required") is True for source in visual_sources if isinstance(source, dict)):
        return ROUTE_MISSING, ["UI_CONTEXT required visual source is not readable"]
    return ROUTE_SPEC_DRIVEN, ["UI_CONTEXT uiRequired without HTML visual source"]


def _plan_ui_routes(fd: Path) -> list[str]:
    _, _, load_plan = _import_ui_context_helpers()
    if load_plan is None:
        return []
    path = fd / "plan.json"
    if not path.is_file():
        return []
    try:
        data = load_plan(path)
    except Exception:
        return []
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return []
    routes: list[str] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("uiRequired") is not True:
            continue
        ui_refs = task.get("uiRefs")
        if not isinstance(ui_refs, dict):
            continue
        route = ui_refs.get("frontendRoute")
        if isinstance(route, str) and route in VALID_ROUTES:
            routes.append(route)
    return routes


def _route_from_plan(fd: Path) -> tuple[str | None, list[str]]:
    routes = _plan_ui_routes(fd)
    if not routes:
        return None, []
    for route in (ROUTE_MISSING, ROUTE_ABSOLUTE, ROUTE_STANDARD, ROUTE_SPEC_DRIVEN):
        if route in routes:
            return route, [f"plan.json uiRefs frontendRoute={route}"]
    return ROUTE_NONE, ["plan.json UI tasks not found"]


def _with_plan_override_reason(
    route: str,
    route_reasons: list[str],
    plan_route: str | None,
    plan_reasons: list[str],
) -> list[str]:
    if plan_route is None or plan_route == route:
        return route_reasons
    if plan_route in {ROUTE_ABSOLUTE, ROUTE_STANDARD, ROUTE_MISSING, ROUTE_SPEC_DRIVEN}:
        return [
            *route_reasons,
            "plan.json route overridden by HTML/UI_CONTEXT evidence",
            *plan_reasons,
        ]
    return route_reasons


def _ui_context_payload(workspace: Path, feature: str) -> dict[str, Any] | None:
    ui_context_error, load_ui_context, _ = _import_ui_context_helpers()
    if load_ui_context is None:
        return None
    fd = feature_dir(workspace, feature)
    ui_path = fd / "UI_CONTEXT.json"
    if not ui_path.is_file() or ui_path.stat().st_size <= 0:
        return None
    try:
        return load_ui_context(fd)
    except Exception as exc:
        if ui_context_error is not None and isinstance(exc, ui_context_error):
            raise FrontendRouteError(f"invalid UI_CONTEXT.json: {exc}") from exc
        raise


def route_payload_from_ui_context(
    workspace: Path,
    feature: str,
    data: dict[str, Any],
    *,
    html_files: Iterable[str] = (),
) -> dict[str, Any]:
    fd = feature_dir(workspace, feature)
    cli_html_sources = existing_paths(normalize_path(raw) for raw in html_files if raw.strip())
    visual_sources = [
        source
        for source in data.get("visualSources", [])
        if isinstance(source, dict)
    ] if isinstance(data.get("visualSources"), list) else []
    html_sources = existing_paths([*cli_html_sources, *_existing_visual_source_paths(workspace, fd, visual_sources)])
    missing_html_sources = _missing_declared_html_paths(workspace, fd, visual_sources)
    ui_required = data.get("uiRequired") is True
    route_missing_declared = False

    if not ui_required:
        route = ROUTE_NONE
        route_reasons = ["UI_CONTEXT uiRequired=false"]
    else:
        plan_route, plan_reasons = _route_from_plan(fd)
        visual_route, visual_reasons = _route_from_visual_sources(visual_sources, html_sources)
        if visual_route in {ROUTE_ABSOLUTE, ROUTE_STANDARD, ROUTE_MISSING}:
            route = visual_route
            route_reasons = _with_plan_override_reason(route, visual_reasons, plan_route, plan_reasons)
        elif plan_route in {ROUTE_ABSOLUTE, ROUTE_STANDARD}:
            route = ROUTE_MISSING
            route_reasons = [*plan_reasons, "plan.json HTML route has no readable HTML source"]
        elif plan_route == ROUTE_MISSING:
            route = ROUTE_MISSING
            route_reasons = plan_reasons
        elif plan_route == ROUTE_SPEC_DRIVEN:
            route = ROUTE_SPEC_DRIVEN
            route_reasons = plan_reasons
        else:
            route = visual_route
            route_reasons = visual_reasons
        if route == ROUTE_MISSING:
            route_missing_declared = True
            route = ROUTE_SPEC_DRIVEN
            route_reasons = [
                *route_reasons,
                "declared HTML visual source is missing; falling back to spec-driven-ui",
            ]

    payload: dict[str, Any] = {
        "version": 1,
        "feature": feature,
        "uiRequired": ui_required,
        "triggered": ui_required,
        "route": route,
        "source": "UI_CONTEXT.json",
        "visualSourceIds": [
            source["sourceId"]
            for source in visual_sources
            if isinstance(source.get("sourceId"), str)
        ],
        "htmlSourcePaths": [str(path) for path in html_sources],
        "reasons": route_reasons,
        "docPaths": [],
    }
    if route_missing_declared or (missing_html_sources and not html_sources):
        payload["htmlSourceMissing"] = True
        payload["missingHtmlSourcePaths"] = missing_html_sources
        payload["htmlRequestMessage"] = _html_request_message(fd, missing_html_sources)
        payload["htmlFallbackRoute"] = ROUTE_SPEC_DRIVEN
        if missing_html_sources:
            payload["reasons"] = [
                *payload["reasons"],
                "declared HTML visual source is not readable: " + ", ".join(missing_html_sources),
            ]
    if route in ROUTE_SKILLS:
        payload["routeSkillPath"] = str(ROUTE_SKILLS[route])
        payload["parserPath"] = str(PARSERS[route])
        payload.setdefault("routeSkillRead", False)
        payload.setdefault("routeSkillReadComplete", False)
        payload.setdefault("routeTodosCreated", False)
        payload.setdefault("routeTodosCompleted", False)
        payload.setdefault("parserRead", False)
    return payload


def has_frontend_intent(text: str) -> bool:
    if not text.strip():
        return False
    return bool(HTML_INTENT_RE.search(text) or ("前端" in text and FRONTEND_INTENT_RE.search(text)))


def html_metrics(text: str) -> dict[str, int | bool]:
    lowered = text.lower()
    return {
        "absolute": len(re.findall(r"position\s*:\s*absolute", lowered)),
        "left": len(re.findall(r"\bleft\s*:", lowered)),
        "top": len(re.findall(r"\btop\s*:", lowered)),
        "fixed_size": len(re.findall(r"\b(width|height)\s*:\s*\d+(\.\d+)?px", lowered)),
        "z_index": lowered.count("z-index"),
        "figma_like": bool(re.search(r'\bid=["\']?\d+:\d+', text) or "data-name=" in lowered),
        "semantic": len(re.findall(r"</?(form|table|button|label|input|select|textarea)\b", lowered)),
        "layout": lowered.count("display: flex") + lowered.count("display:flex") + lowered.count("display: grid") + lowered.count("display:grid"),
        "class": len(re.findall(r"\bclass\s*=", lowered)),
    }


def classify_html(paths: Iterable[Path]) -> tuple[str, list[str]]:
    combined = "\n".join(read_text(path) for path in paths)
    metrics = html_metrics(combined)
    reasons: list[str] = []

    absolute_votes = 0
    if int(metrics["absolute"]) >= 3:
        absolute_votes += 2
        reasons.append(f"position:absolute count={metrics['absolute']}")
    if int(metrics["left"]) >= 8 and int(metrics["top"]) >= 8:
        absolute_votes += 1
        reasons.append(f"left/top count={metrics['left']}/{metrics['top']}")
    if int(metrics["fixed_size"]) >= 12:
        absolute_votes += 1
        reasons.append(f"fixed px sizes count={metrics['fixed_size']}")
    if int(metrics["z_index"]) >= 5:
        absolute_votes += 1
        reasons.append(f"z-index count={metrics['z_index']}")
    if bool(metrics["figma_like"]):
        absolute_votes += 1
        reasons.append("figma/mastergo-like id or data-name")

    if absolute_votes >= 2:
        return ROUTE_ABSOLUTE, reasons

    standard_reasons: list[str] = []
    if int(metrics["semantic"]):
        standard_reasons.append(f"semantic controls count={metrics['semantic']}")
    if int(metrics["layout"]):
        standard_reasons.append(f"flex/grid count={metrics['layout']}")
    if int(metrics["class"]):
        standard_reasons.append(f"class attributes count={metrics['class']}")
    return ROUTE_STANDARD, standard_reasons or ["html source exists without dominant absolute-position signals"]


def route_payload(
    workspace: Path,
    feature: str,
    *,
    html_files: Iterable[str] = (),
) -> dict[str, Any]:
    ui_context = _ui_context_payload(workspace, feature)
    if ui_context is not None:
        return route_payload_from_ui_context(workspace, feature, ui_context, html_files=html_files)

    fd = feature_dir(workspace, feature)
    docs_text, docs = collect_doc_texts(fd)
    html_sources = collect_html_sources(workspace, feature, html_files)
    triggered = bool(html_sources) or has_frontend_intent(docs_text)
    reasons: list[str] = []
    if html_sources:
        reasons.append("html source found")
    if has_frontend_intent(docs_text):
        reasons.append("feature documents contain frontend/html intent")

    if not triggered:
        route = ROUTE_NONE
        route_reasons = ["no frontend/html route trigger found"]
    elif not html_sources:
        route = ROUTE_MISSING
        route_reasons = ["frontend/html task detected but no readable html source found"]
    else:
        route, route_reasons = classify_html(html_sources)

    payload: dict[str, Any] = {
        "version": 1,
        "feature": feature,
        "triggered": triggered,
        "route": route,
        "htmlSourcePaths": [str(path) for path in html_sources],
        "reasons": [*reasons, *route_reasons],
        "docPaths": [str(path) for path in docs],
    }
    if route in ROUTE_SKILLS:
        payload["routeSkillPath"] = str(ROUTE_SKILLS[route])
        payload["parserPath"] = str(PARSERS[route])
        payload.setdefault("routeSkillRead", False)
        payload.setdefault("routeSkillReadComplete", False)
        payload.setdefault("routeTodosCreated", False)
        payload.setdefault("routeTodosCompleted", False)
        payload.setdefault("parserRead", False)
    return payload


def merge_existing_flags(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    if existing.get("route") != payload.get("route"):
        return payload
    for key in (
        "routeSkillRead",
        "routeSkillReadComplete",
        "routeTodosCreated",
        "routeTodosCompleted",
        "parserRead",
        "reviewStatus",
        "routeSkillReadRanges",
    ):
        if key in existing:
            payload[key] = existing[key]
    return payload


def resolve_frontend_route(
    workspace: Path,
    feature: str,
    *,
    html_files: Iterable[str] = (),
    write_evidence: bool = False,
) -> dict[str, Any]:
    payload = route_payload(workspace, feature, html_files=html_files)
    if write_evidence:
        path = evidence_path(workspace, feature)
        payload = merge_existing_flags(payload, read_json(path))
        write_json(path, payload)
    return payload


def mark_evidence(
    workspace: Path,
    feature: str,
    *,
    mark: str | None = None,
    review_status: str | None = None,
) -> dict[str, Any]:
    path = evidence_path(workspace, feature)
    payload = read_json(path)
    if not payload:
        raise ValueError(f"FRONTEND_ROUTE.json not found: {path}")
    if payload.get("route") not in VALID_ROUTES:
        raise ValueError(f"invalid frontend route evidence: {payload.get('route')}")

    if mark == "route-todos-created":
        payload["routeTodosCreated"] = True
    elif mark == "route-todos-completed":
        payload["routeTodosCompleted"] = True
    elif mark is not None:
        raise ValueError(f"unknown mark: {mark}")

    if review_status is not None:
        if review_status not in VALID_REVIEW_STATUSES:
            allowed = ", ".join(sorted(VALID_REVIEW_STATUSES))
            raise ValueError(f"reviewStatus must be one of: {allowed}")
        payload["reviewStatus"] = review_status

    write_json(path, payload)
    return payload


def resolve_workspace(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    return get_plugin_output_workspace()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve autodev-code frontend HTML route")
    parser.add_argument("--workspace", help="project plugin workspace; defaults to plugin env")
    parser.add_argument("--feature", "-f", help="feature slug; defaults to FEATURE_ID")
    parser.add_argument("--html-file", action="append", default=[], help="HTML source path; may be repeated")
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--mark", choices=("route-todos-created", "route-todos-completed"))
    parser.add_argument("--review-status", choices=tuple(sorted(VALID_REVIEW_STATUSES)))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        workspace = resolve_workspace(args.workspace)
        feature = args.feature.strip() if args.feature else resolve_env_feature(None, required=True)
        if not feature:
            raise ValueError("feature 不能为空")
        if args.mark or args.review_status:
            payload = mark_evidence(
                workspace,
                feature,
                mark=args.mark,
                review_status=args.review_status,
            )
        else:
            payload = resolve_frontend_route(
                workspace,
                feature,
                html_files=args.html_file,
                write_evidence=args.write_evidence,
            )
    except (FrontendRouteError, ValueError) as exc:
        print(f"frontend route resolve failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"frontendRoute={payload.get('route')} triggered={payload.get('triggered')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
