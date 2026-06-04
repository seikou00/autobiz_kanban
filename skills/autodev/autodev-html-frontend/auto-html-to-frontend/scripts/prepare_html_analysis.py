#!/usr/bin/env python3
"""Run HTML analysis and write a compact Stage 1 handoff."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> None:
    os.makedirs(path, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def render_whole_sections(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in manifest.get("sections", []):
        contract = section.get("renderContract") or {}
        if contract.get("mustRenderWholeSection"):
            result.append({
                "ownerPath": section.get("ownerPath", ""),
                "layoutHint": section.get("layoutHint", ""),
                "subsectionTitles": section.get("subsectionTitles", []),
                "tagStripTexts": section.get("tagStripTexts", []),
                "hasSearchBar": bool(contract.get("hasSearchBar")),
                "contentMode": contract.get("contentMode", ""),
            })
    return result


def source_action_texts(full_manifest: dict[str, Any]) -> list[str]:
    texts = []
    for item in full_manifest.get("texts", []):
        if item.get("kind") == "action":
            texts.append(str(item.get("text", "")))
    return unique(texts)


def build_layout_contract(
    html_source_path: str,
    full_manifest: dict[str, Any],
) -> dict[str, Any]:
    sections = full_manifest.get("sections", [])
    regions = full_manifest.get("regions", [])
    return {
        "status": "required",
        "mustReadBeforeStage1": [
            html_source_path,
        ],
        "stage1PassRequires": [
            "the original high-fidelity HTML was used as the primary Stage 1 visual/layout source",
            "the implemented page preserves macro layout before any component replacement",
        ],
        "lightweightBackcheckAfterStage1": [
            "sample-check key sections, actions, and field groups for obvious omissions",
            "use handoff/manifest snippets as a spot-check aid, not as a Stage 1 gate",
            "report omission risks separately from visual fidelity status",
        ],
        "finalBackcheckAfterStage2": [
            "run a final omission check after component replacement",
            "confirm critical fields, actions, statuses, and hints still exist after componentization",
        ],
        "mustCheck": [
            "shell/header/sidebar/breadcrumb ownership",
            "main content width and left offset",
            "left/right panel split and rail/timeline placement",
            "section vertical order and spacing",
            "section width alignment across the page",
        ],
        "sourceRegionCount": len(regions),
        "sourceSectionCount": len(sections),
        "sectionOrder": [
            section.get("ownerPath") or section.get("title") or f"section-{i}"
            for i, section in enumerate(sections, start=1)
        ],
        "passRule": "Mark Stage 1 by visual fidelity first. Use the original high-fidelity HTML as the primary source and report omission spot-checks separately.",
    }


def build_checklist(
    task_stem: str,
    html_source_path: str,
    html_input_path: str,
    output_dir: Path,
    full_manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = str(output_dir / f"{task_stem}.json")
    markdown_path = str(output_dir / f"{task_stem}.md")
    whole_sections = render_whole_sections(full_manifest)
    layout_contract = build_layout_contract(html_source_path, full_manifest)
    return {
        "taskStem": task_stem,
        "htmlSourcePath": html_source_path,
        "htmlInputPath": html_input_path,
        "analysisDir": str(output_dir),
        "files": {
            "manifest": manifest_path,
            "handoffMarkdown": markdown_path,
        },
        "mustExist": [
            manifest_path,
            markdown_path,
        ],
        "summary": full_manifest.get("summary", {}),
        "mustReadFirst": [
            markdown_path,
            html_source_path,
        ],
        "referenceOnly": [],
        "sectionHtmlPolicy": "not-generated-by-default; use original HTML for Stage 1. Generate section HTML only with --emit-section-html for debugging.",
        "sectionHtmlFiles": full_manifest.get("sectionFiles", []),
        "coverageReport": full_manifest.get("coverageReport", {}),
        "layoutContract": layout_contract,
        "localRegressionTargets": full_manifest.get("localRegressionTargets", []),
        "mustRenderWholeSections": whole_sections,
        "sourceActionTexts": source_action_texts(full_manifest),
        "buttonRule": "Do not add buttons or actions that are not present in sourceActionTexts unless prd.md or project evidence explicitly requires them.",
        "stagePassRule": "Stage 1 cannot be marked passed by description alone; include concrete evidence that the whole original HTML was visually restored. Omission checks are reported separately and do not block Stage 1 by default.",
    }


def write_checklist_md(path: Path, checklist: dict[str, Any]) -> None:
    lines = [
        "# HTML Stage 1 Handoff",
        "",
        f"- taskStem: `{checklist['taskStem']}`",
        f"- htmlSourcePath: `{checklist['htmlSourcePath']}`",
        f"- htmlInputPath: `{checklist['htmlInputPath'] or 'none'}`",
        f"- manifest: `{checklist['files']['manifest']}`",
        "",
        "## Read First",
    ]
    for value in checklist["mustReadFirst"]:
        lines.append(f"- `{value}`")
    lines.extend(["", "## Section HTML Policy", f"- {checklist.get('sectionHtmlPolicy', '')}"])
    layout_contract = checklist.get("layoutContract", {})
    lines.extend(["", "## Layout Contract"])
    lines.append(f"- status: {layout_contract.get('status', 'required')}")
    lines.append(f"- passRule: {layout_contract.get('passRule', '')}")
    lines.append("- mustReadBeforeStage1:")
    for value in layout_contract.get("mustReadBeforeStage1", []):
        lines.append(f"  - `{value}`")
    lines.append("- stage1PassRequires:")
    for value in layout_contract.get("stage1PassRequires", []):
        lines.append(f"  - {value}")
    if layout_contract.get("lightweightBackcheckAfterStage1"):
        lines.append("- lightweightBackcheckAfterStage1:")
        for value in layout_contract.get("lightweightBackcheckAfterStage1", []):
            lines.append(f"  - {value}")
    if layout_contract.get("finalBackcheckAfterStage2"):
        lines.append("- finalBackcheckAfterStage2:")
        for value in layout_contract.get("finalBackcheckAfterStage2", []):
            lines.append(f"  - {value}")
    lines.append("- mustCheck:")
    for value in layout_contract.get("mustCheck", []):
        lines.append(f"  - {value}")
    order = layout_contract.get("sectionOrder", [])
    if order:
        lines.append("- sectionOrder:")
        for value in order[:60]:
            lines.append(f"  - `{value}`")
    coverage = checklist.get("coverageReport", {})
    lines.extend(["", "## Coverage"])
    lines.append(f"- mode: {coverage.get('mode', 'whole-html-source')}")
    lines.append(f"- totalTextCount: {coverage.get('totalTextCount', 0)}")
    note = coverage.get("note")
    if note:
        lines.append(f"- note: {note}")
    regression_targets = checklist.get("localRegressionTargets", [])
    if regression_targets:
        lines.extend(["", "## Local Regression Targets"])
        for item in regression_targets[:20]:
            flags = ",".join(item.get("riskFlags", []))
            lines.append(
                f"- owner={item.get('owner')} action={item.get('action')} "
                f"flags={flags} path=`{item.get('path')}`"
            )
    lines.extend(["", "## Whole Sections"])
    if checklist["mustRenderWholeSections"]:
        for item in checklist["mustRenderWholeSections"][:30]:
            lines.append(
                f"- `{item['ownerPath']}` layout={item['layoutHint']} "
                f"hasSearchBar={item['hasSearchBar']} tags={item['tagStripTexts']} subsections={item['subsectionTitles']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Source Actions"])
    if checklist["sourceActionTexts"]:
        for action in checklist["sourceActionTexts"][:40]:
            lines.append(f"- `{action}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Rule", f"- {checklist['buttonRule']}", f"- {checklist['stagePassRule']}"])
    write_text(path, "\n".join(lines) + "\n")


def verify_paths(paths: list[str]) -> None:
    missing = [path for path in paths if not Path(path).exists()]
    if missing:
        raise SystemExit("Missing required analysis artifacts:\n" + "\n".join(missing))


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare HTML analysis and write a compact Stage 1 handoff.")
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--html-file", required=True)
    ap.add_argument("--task-stem", required=True)
    ap.add_argument("--copy-html-input", action="store_true")
    ap.add_argument("--emit-section-html", action="store_true", help="Optional debug mode; default keeps Stage 1 on the whole original HTML.")
    ap.add_argument("--emit-reference-html", action="store_true", help="Optional debug mode; emit page-level helper HTML files.")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    html_file = Path(args.html_file).resolve()
    if not project_root.exists():
        raise SystemExit(f"Project root not found: {project_root}")
    if not html_file.exists():
        raise SystemExit(f"HTML source not found: {html_file}")

    output_root = project_root / "output"
    analysis_dir = output_root / "html-analysis"
    html_input_dir = output_root / "html-input"
    ensure_dir(analysis_dir)

    html_input_path = ""
    analysis_source = html_file
    if args.copy_html_input:
        ensure_dir(html_input_dir)
        html_input_target = html_input_dir / f"{args.task_stem}.html"
        html_input_target.write_bytes(html_file.read_bytes())
        html_input_path = str(html_input_target)
        analysis_source = html_input_target

    analyze_script = Path(__file__).with_name("analyze_absolute_html.py")
    cmd = [
        sys.executable,
        "-B",
        str(analyze_script),
        str(analysis_source),
        "--project-root",
        str(project_root),
        "--out-dir",
        str(analysis_dir),
        "--output-name",
        args.task_stem,
    ]
    if args.emit_section_html:
        cmd.append("--emit-section-html")
    if args.emit_reference_html:
        cmd.append("--emit-reference-html")
    subprocess.run(cmd, check=True)

    manifest_path = analysis_dir / f"{args.task_stem}.json"
    handoff_md_path = analysis_dir / f"{args.task_stem}.md"
    verify_paths([
        str(manifest_path),
        str(handoff_md_path),
    ])
    if args.emit_section_html:
        verify_paths([str(analysis_dir / f"{args.task_stem}-section-html" / "index.md")])
    if args.emit_reference_html:
        verify_paths([
            str(analysis_dir / f"{args.task_stem}-page-layout.html"),
            str(analysis_dir / f"{args.task_stem}-whole-page-reference.html"),
        ])

    full_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checklist = build_checklist(
        task_stem=args.task_stem,
        html_source_path=str(html_file),
        html_input_path=html_input_path,
        output_dir=analysis_dir,
        full_manifest=full_manifest,
    )

    write_checklist_md(handoff_md_path, checklist)

    print(json.dumps({
        "taskStem": args.task_stem,
        "handoffMarkdown": str(handoff_md_path),
        "manifestPath": str(manifest_path),
        "referenceHtmlFiles": {
            "pageLayoutHtml": str(analysis_dir / f"{args.task_stem}-page-layout.html"),
            "wholePageReferenceHtml": str(analysis_dir / f"{args.task_stem}-whole-page-reference.html"),
        } if args.emit_reference_html else {},
        "layoutContract": checklist["layoutContract"]["passRule"],
        "sectionHtmlPolicy": checklist["sectionHtmlPolicy"],
        "wholeSections": [item["ownerPath"] for item in checklist["mustRenderWholeSections"]],
        "sourceActionTexts": checklist["sourceActionTexts"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



