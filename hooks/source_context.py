#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read and validate the compact, PRD-owned external source context."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree


SOURCE_CONTEXT_FILE = "source-context.json"
SOURCE_REQUIREMENT_RE = re.compile(r"\bSRC-\d{3}-R\d{3}\b")
SOURCE_ITEM_RE = re.compile(r"^SRC-\d{3}-I\d{3}$")
SOURCE_ID_RE = re.compile(r"^SRC-\d{3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VALID_AVAILABILITY = {"live", "snapshot_only", "never_provided"}
VALID_READ_STATUS = {"complete", "partial", "unreadable"}
VALID_FRESHNESS = {"current", "stale", "unknown"}
VALID_DISPOSITIONS = {
    "requirement",
    "background",
    "non_goal",
    "duplicate",
    "superseded",
}
VALID_TARGETS = {"spec", "design", "plan", "code", "reviewer", "e2e"}
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".xml",
    ".csv",
    ".tsv",
}


def source_context_path(feature_dir: Path) -> Path:
    return feature_dir / SOURCE_CONTEXT_FILE


def referenced_source_requirement_ids(text: str) -> Set[str]:
    return set(SOURCE_REQUIREMENT_RE.findall(text))


def load_source_context(feature_dir: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    path = source_context_path(feature_dir)
    if not path.is_file() or path.stat().st_size <= 0:
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, ["source-context.json 无法读取: %s" % exc]
    if not isinstance(data, dict):
        return None, ["source-context.json 顶层必须是对象"]
    return data, []


def source_requirement_index(data: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result = {}  # type: Dict[str, Dict[str, Any]]
    if not isinstance(data, dict):
        return result
    sources = data.get("sources")
    if not isinstance(sources, list):
        return result
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = source.get("id")
        source_path = source.get("path")
        for item in source.get("items", []) if isinstance(source.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            for requirement in item.get("requirements", []) if isinstance(item.get("requirements"), list) else []:
                if not isinstance(requirement, dict):
                    continue
                requirement_id = requirement.get("id")
                if not isinstance(requirement_id, str):
                    continue
                result[requirement_id] = {
                    "id": requirement_id,
                    "text": requirement.get("text"),
                    "targets": requirement.get("targets"),
                    "sourceId": source_id,
                    "sourcePath": source_path,
                    "availability": source.get("availability"),
                    "readStatus": source.get("readStatus"),
                    "freshness": source.get("freshness", "unknown"),
                    "itemId": item.get("id"),
                    "location": item.get("location"),
                    "original": item.get("original"),
                }
    return result


def source_requirement_ids_for_target(data: Optional[Dict[str, Any]], target: str) -> Set[str]:
    return {
        requirement_id
        for requirement_id, requirement in source_requirement_index(data).items()
        if isinstance(requirement.get("targets"), list) and target in requirement["targets"]
    }


def resolve_source_requirement_refs(
    feature_dir: Path,
    refs: List[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    validation_errors = validate_source_context(feature_dir) if refs else []
    data, load_errors = load_source_context(feature_dir)
    resolved = []  # type: List[Dict[str, Any]]
    errors = [
        {"reason": "invalid_source_context", "detail": error}
        for error in (validation_errors or load_errors)
    ]
    if data is None:
        if refs:
            errors.append({
                "reason": "missing_source_context",
                "detail": SOURCE_CONTEXT_FILE,
            })
        return resolved, errors

    index = source_requirement_index(data)
    seen = set()  # type: Set[str]
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        item = index.get(ref)
        if item is None:
            resolved.append({"ref": ref, "found": False})
            errors.append({
                "reason": "unknown_source_requirement_ref",
                "detail": ref,
            })
            continue
        resolved.append({"ref": ref, "found": True, **item})
    return resolved, errors


def _normalize_evidence(value: str) -> str:
    return re.sub(r"[\s|]+", "", value).casefold()


def _decode_xml_text(element: ElementTree.Element, text_tag: str) -> str:
    return "".join(node.text or "" for node in element.iter(text_tag)).strip()


def _read_docx(path: Path) -> Tuple[str, List[Tuple[str, str]]]:
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(str(path)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    text_tag = namespace + "t"
    paragraphs = [
        _decode_xml_text(paragraph, text_tag)
        for paragraph in root.iter(namespace + "p")
    ]
    rows = []  # type: List[Tuple[str, str]]
    for table_index, table in enumerate(root.iter(namespace + "tbl"), start=1):
        table_rows = list(table.iter(namespace + "tr"))
        for row_index, row in enumerate(table_rows, start=1):
            cells = [
                _decode_xml_text(cell, text_tag)
                for cell in list(row.iter(namespace + "tc"))
            ]
            value = " | ".join(cell for cell in cells if cell)
            if value and row_index > 1:
                rows.append(("表 %d 第 %d 行" % (table_index, row_index), value))
    return "\n".join(value for value in paragraphs if value), rows


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read(name))
    strings = []  # type: List[str]
    for item in root:
        strings.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
    return strings


def _read_xlsx(path: Path) -> Tuple[str, List[Tuple[str, str]]]:
    values = []  # type: List[str]
    rows = []  # type: List[Tuple[str, str]]
    with zipfile.ZipFile(str(path)) as archive:
        shared = _xlsx_shared_strings(archive)
        sheet_names = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        for sheet_index, name in enumerate(sheet_names, start=1):
            root = ElementTree.fromstring(archive.read(name))
            nonempty_index = 0
            for row in (node for node in root.iter() if node.tag.endswith("}row")):
                cells = []  # type: List[str]
                for cell in (node for node in row if node.tag.endswith("}c")):
                    cell_type = cell.attrib.get("t")
                    raw = next((node.text or "" for node in cell if node.tag.endswith("}v")), "")
                    if cell_type == "s" and raw.isdigit() and int(raw) < len(shared):
                        raw = shared[int(raw)]
                    if cell_type == "inlineStr":
                        raw = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                    if raw:
                        cells.append(raw)
                if not cells:
                    continue
                nonempty_index += 1
                value = " | ".join(cells)
                values.append(value)
                if nonempty_index > 1:
                    rows.append(("工作表 %d 第 %s 行" % (sheet_index, row.attrib.get("r", nonempty_index)), value))
    return "\n".join(values), rows


def _read_delimited(path: Path, delimiter: str) -> Tuple[str, List[Tuple[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        parsed = list(csv.reader(handle, delimiter=delimiter))
    rows = []  # type: List[Tuple[str, str]]
    values = []  # type: List[str]
    for index, cells in enumerate(parsed, start=1):
        value = " | ".join(cell.strip() for cell in cells if cell.strip())
        if not value:
            continue
        values.append(value)
        if index > 1:
            rows.append(("第 %d 行" % index, value))
    return "\n".join(values), rows


def _markdown_rows(text: str) -> List[Tuple[str, str]]:
    lines = text.splitlines()
    separators = set()  # type: Set[int]
    separator_re = re.compile(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")
    for index, line in enumerate(lines):
        if separator_re.fullmatch(line):
            separators.add(index)
    rows = []  # type: List[Tuple[str, str]]
    for index, line in enumerate(lines):
        stripped = line.strip()
        if "|" not in stripped or index in separators or index + 1 in separators:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            rows.append(("第 %d 行" % (index + 1), stripped))
    return rows


def _read_pdf(path: Path) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    executable = shutil.which("pdftotext")
    if executable is None:
        return None, []
    completed = subprocess.run(
        [executable, str(path), "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return None, []
    try:
        return completed.stdout.decode("utf-8"), []
    except UnicodeDecodeError:
        return completed.stdout.decode("utf-8", errors="replace"), []


def _read_snapshot(path: Path) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".docx":
            return _read_docx(path)
        if suffix == ".xlsx":
            return _read_xlsx(path)
        if suffix == ".csv":
            return _read_delimited(path, ",")
        if suffix == ".tsv":
            return _read_delimited(path, "\t")
        if suffix == ".pdf":
            return _read_pdf(path)
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            return text, _markdown_rows(text) if suffix in {".md", ".markdown"} else []
    except (OSError, UnicodeDecodeError, csv.Error, zipfile.BadZipFile, ElementTree.ParseError):
        return None, []
    return None, []


def _safe_snapshot_path(feature_dir: Path, source_id: str, raw_path: Any) -> Tuple[Optional[Path], Optional[str]]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "%s 缺少快照 path" % source_id
    path = Path(raw_path)
    if path.is_absolute():
        return None, "%s.path 必须是 Feature 目录相对路径" % source_id
    candidate = (feature_dir / path).resolve(strict=False)
    base = feature_dir.resolve(strict=False)
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        return None, "%s.path 越出 Feature 目录" % source_id
    parts = relative.parts
    if len(parts) < 3 or parts[0] != "sources" or parts[1] != source_id:
        return None, "%s.path 必须位于 sources/%s/" % (source_id, source_id)
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return None, "%s 快照不存在或为空: %s" % (source_id, raw_path)
    return candidate, None


def validate_source_context(
    feature_dir: Path,
    expected_source_ids: Optional[Set[str]] = None,
) -> List[str]:
    data, errors = load_source_context(feature_dir)
    if data is None:
        if expected_source_ids:
            errors.append("PRD 登记了外部资料，必须生成 source-context.json")
        return errors

    if data.get("version") != 1:
        errors.append("source-context.json.version 必须为 1")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("source-context.json.sources 必须是非空数组")
        return errors

    seen_sources = set()  # type: Set[str]
    seen_items = set()  # type: Set[str]
    seen_requirements = set()  # type: Set[str]
    item_originals_by_source = {}  # type: Dict[str, List[str]]
    snapshots = {}  # type: Dict[str, Tuple[Optional[str], List[Tuple[str, str]]]]

    for source_index, source in enumerate(sources):
        context = "sources[%d]" % source_index
        if not isinstance(source, dict):
            errors.append("%s 必须是对象" % context)
            continue
        source_id = source.get("id")
        if not isinstance(source_id, str) or SOURCE_ID_RE.fullmatch(source_id) is None:
            errors.append("%s.id 格式非法" % context)
            continue
        if source_id in seen_sources:
            errors.append("source-context.json 来源 ID 重复: %s" % source_id)
        seen_sources.add(source_id)
        name = source.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("%s.name 缺失" % source_id)
        availability = source.get("availability")
        if availability not in VALID_AVAILABILITY:
            errors.append("%s.availability 非法" % source_id)
        read_status = source.get("readStatus")
        if read_status not in VALID_READ_STATUS:
            errors.append("%s.readStatus 非法" % source_id)
        elif availability != "never_provided" and read_status != "complete":
            errors.append("%s 尚未完整读取；修复：完整读取快照后将 readStatus 设为 complete" % source_id)
        freshness = source.get("freshness", "unknown")
        if freshness not in VALID_FRESHNESS:
            errors.append("%s.freshness 非法" % source_id)
        digest = source.get("sha256")
        if availability != "never_provided" and (
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
        ):
            errors.append("%s.sha256 必须记录 64 位小写十六进制指纹" % source_id)

        snapshot = None  # type: Optional[Path]
        if availability == "never_provided":
            errors.append("%s 从未提供；修复：提供资料、移除依赖或暂停，不能以默认理解推进" % source_id)
            if read_status != "unreadable":
                errors.append("%s 从未提供时 readStatus 必须为 unreadable" % source_id)
            if source.get("items") != []:
                errors.append("%s 从未提供时 items 必须为空数组" % source_id)
            continue
        else:
            snapshot, path_error = _safe_snapshot_path(feature_dir, source_id, source.get("path"))
            if path_error:
                errors.append(path_error)
            elif snapshot is not None:
                snapshots[source_id] = _read_snapshot(snapshot)

        items = source.get("items")
        if not isinstance(items, list) or not items:
            errors.append("%s.items 必须是非空数组" % source_id)
            continue
        originals = []  # type: List[str]
        for item_index, item in enumerate(items):
            item_context = "%s.items[%d]" % (source_id, item_index)
            if not isinstance(item, dict):
                errors.append("%s 必须是对象" % item_context)
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or SOURCE_ITEM_RE.fullmatch(item_id) is None or not item_id.startswith(source_id + "-I"):
                errors.append("%s.id 格式非法" % item_context)
            elif item_id in seen_items:
                errors.append("source-context.json 原文条目 ID 重复: %s" % item_id)
            else:
                seen_items.add(item_id)
            location = item.get("location")
            original = item.get("original")
            if not isinstance(location, str) or not location.strip():
                errors.append("%s.location 缺失" % item_context)
            if not isinstance(original, str) or not original.strip():
                errors.append("%s.original 缺失" % item_context)
                original = ""
            else:
                originals.append(original)
                extracted_text = snapshots.get(source_id, (None, []))[0]
                if extracted_text is not None and _normalize_evidence(original) not in _normalize_evidence(extracted_text):
                    errors.append("%s.original 无法在快照中定位；修复：逐字摘录快照内容并修正 location" % item_context)
            disposition = item.get("disposition")
            if disposition not in VALID_DISPOSITIONS:
                errors.append("%s.disposition 非法" % item_context)
            requirements = item.get("requirements")
            if not isinstance(requirements, list):
                errors.append("%s.requirements 必须是数组" % item_context)
                requirements = []
            if disposition == "requirement" and not requirements:
                errors.append("%s 是有效要求但未提取 requirements" % item_context)
            if disposition != "requirement" and requirements:
                errors.append("%s 非 requirement 条目不得生成 requirements" % item_context)
            if disposition == "superseded":
                replaced_by = item.get("replacedBy")
                if not isinstance(replaced_by, str) or SOURCE_ITEM_RE.fullmatch(replaced_by) is None:
                    errors.append("%s.replacedBy 缺失或格式非法" % item_context)

            for requirement_index, requirement in enumerate(requirements):
                requirement_context = "%s.requirements[%d]" % (item_context, requirement_index)
                if not isinstance(requirement, dict):
                    errors.append("%s 必须是对象" % requirement_context)
                    continue
                requirement_id = requirement.get("id")
                if not isinstance(requirement_id, str) or SOURCE_REQUIREMENT_RE.fullmatch(requirement_id) is None or not requirement_id.startswith(source_id + "-R"):
                    errors.append("%s.id 格式非法" % requirement_context)
                elif requirement_id in seen_requirements:
                    errors.append("source-context.json 要求 ID 重复: %s" % requirement_id)
                else:
                    seen_requirements.add(requirement_id)
                text = requirement.get("text")
                if not isinstance(text, str) or not text.strip():
                    errors.append("%s.text 缺失" % requirement_context)
                targets = requirement.get("targets")
                if not isinstance(targets, list) or not targets:
                    errors.append("%s.targets 必须是非空数组" % requirement_context)
                else:
                    invalid_targets = [target for target in targets if target not in VALID_TARGETS]
                    if invalid_targets:
                        errors.append("%s.targets 非法: %s" % (requirement_context, ",".join(map(str, invalid_targets))))
                    if len(targets) != len(set(targets)):
                        errors.append("%s.targets 不得重复" % requirement_context)
        item_originals_by_source[source_id] = originals

    if expected_source_ids is not None:
        missing = sorted(expected_source_ids - seen_sources)
        unknown = sorted(seen_sources - expected_source_ids)
        if missing:
            errors.append("source-context.json 缺少 PRD 来源: %s" % ", ".join(missing))
        if unknown:
            errors.append("source-context.json 存在 PRD 未登记来源: %s" % ", ".join(unknown))

    for source_id, (_, rows) in snapshots.items():
        originals = [_normalize_evidence(value) for value in item_originals_by_source.get(source_id, [])]
        missing_rows = []  # type: List[str]
        for location, row_text in rows:
            normalized_row = _normalize_evidence(row_text)
            if normalized_row and not any(normalized_row in original or original in normalized_row for original in originals):
                missing_rows.append("%s=%s" % (location, row_text[:80]))
        if missing_rows:
            errors.append(
                "%s 存在未登记表格/字段行: %s；修复：逐行补充 items，或明确 disposition"
                % (source_id, "; ".join(missing_rows[:10]))
            )
    return errors


def snapshot_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="外部资料快照辅助工具")
    subparsers = parser.add_subparsers(dest="command")
    digest_parser = subparsers.add_parser("digest", help="计算已落盘快照的 SHA256")
    digest_parser.add_argument("--feature-dir", required=True)
    digest_parser.add_argument("--path", required=True)
    args = parser.parse_args(argv)

    if args.command != "digest":
        parser.print_help(sys.stderr)
        return 2

    relative = Path(args.path)
    parts = relative.parts
    if relative.is_absolute() or len(parts) < 3 or parts[0] != "sources" or SOURCE_ID_RE.fullmatch(parts[1]) is None:
        print(
            "ERROR: --path 必须是 sources/SRC-NNN/ 下的 Feature 相对路径。"
            " 修复：先把原件或快照放入对应来源目录，再传入该相对路径。",
            file=sys.stderr,
        )
        return 1
    snapshot, error = _safe_snapshot_path(Path(args.feature_dir), parts[1], args.path)
    if error or snapshot is None:
        print(
            "ERROR: %s 修复：确认快照已写入 Feature 的 sources/SRC-NNN/ 且文件非空。"
            % (error or "快照不可读取"),
            file=sys.stderr,
        )
        return 1
    print(snapshot_sha256(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
