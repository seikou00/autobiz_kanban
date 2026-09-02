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


def source_ids_for_target(data: Optional[Dict[str, Any]], target: str) -> Set[str]:
    return {
        requirement["sourceId"]
        for requirement in source_requirement_index(data).values()
        if isinstance(requirement.get("sourceId"), str)
        and isinstance(requirement.get("targets"), list)
        and target in requirement["targets"]
    }


def resolve_source_requirement_refs(
    feature_dir: Path,
    refs: List[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    validation_errors = validate_source_context_refs(feature_dir) if refs else []
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
) -> Tuple[List[str], List[str]]:
    """校验 source-context.json，返回 (errors, warnings)。

    errors 只保留会让下游引用悬空的项：ID 可解析、requirement 可被
    ``SRC-NNN-RNNN`` 引用、targets 可用于阶段过滤、PRD 与 json 的来源集合一致。
    其余字段下游只做透传渲染，一律降级为 warnings，避免 discuss 阶段的
    严格度阻断 Plan / Code。逐字原文与表格行覆盖由 ``sync`` 子命令生成，不再校验。
    """
    data, load_errors = load_source_context(feature_dir)
    errors = list(load_errors)  # type: List[str]
    warnings = []  # type: List[str]
    if data is None:
        if expected_source_ids:
            errors.append(
                "PRD 登记了外部资料，必须生成 source-context.json；"
                "修复：运行 source_context.py sync --feature-dir <Feature 目录>"
            )
        return errors, warnings

    if data.get("version") != 1:
        errors.append("source-context.json.version 必须为 1；修复：把 version 字段设为整数 1")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(
            "source-context.json.sources 必须是非空数组；"
            "修复：运行 source_context.py sync --feature-dir <Feature 目录> 依据 PRD 来源表重建"
        )
        return errors, warnings

    seen_sources = set()  # type: Set[str]
    seen_items = set()  # type: Set[str]
    seen_requirements = set()  # type: Set[str]

    for source_index, source in enumerate(sources):
        context = "sources[%d]" % source_index
        if not isinstance(source, dict):
            errors.append("%s 必须是对象" % context)
            continue
        source_id = source.get("id")
        if not isinstance(source_id, str) or SOURCE_ID_RE.fullmatch(source_id) is None:
            errors.append("%s.id 格式非法；修复：改为 SRC-001 形式的三位编号" % context)
            continue
        if source_id in seen_sources:
            errors.append(
                "source-context.json 来源 ID 重复: %s；修复：保留一条，其余改用未使用的 SRC-NNN"
                % source_id
            )
        seen_sources.add(source_id)

        name = source.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append("%s.name 缺失" % source_id)
        availability = source.get("availability")
        if availability not in VALID_AVAILABILITY:
            warnings.append(
                "%s.availability 非法；可选值: %s"
                % (source_id, "/".join(sorted(VALID_AVAILABILITY)))
            )
        read_status = source.get("readStatus")
        if read_status not in VALID_READ_STATUS:
            warnings.append(
                "%s.readStatus 非法；可选值: %s"
                % (source_id, "/".join(sorted(VALID_READ_STATUS)))
            )
        elif availability != "never_provided" and read_status != "complete":
            warnings.append("%s 尚未完整读取（readStatus=%s）" % (source_id, read_status))
        if source.get("freshness", "unknown") not in VALID_FRESHNESS:
            warnings.append(
                "%s.freshness 非法；可选值: %s"
                % (source_id, "/".join(sorted(VALID_FRESHNESS)))
            )
        digest = source.get("sha256")
        if availability != "never_provided" and (
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
        ):
            warnings.append(
                "%s.sha256 缺失或格式不对；修复：运行 source_context.py sync 自动写入" % source_id
            )

        if availability == "never_provided":
            warnings.append(
                "%s 标记为从未提供；出口只有三个：当场提供资料、移除该依赖、或暂停等待材料"
                % source_id
            )
            continue

        _, path_error = _safe_snapshot_path(feature_dir, source_id, source.get("path"))
        if path_error:
            warnings.append(
                "%s；修复：运行 source_context.py sync 自动写入快照路径" % path_error
            )

        items = source.get("items")
        if not isinstance(items, list) or not items:
            warnings.append(
                "%s.items 为空；修复：运行 source_context.py sync 依据快照生成原文条目" % source_id
            )
            continue

        undecided = 0
        for item_index, item in enumerate(items):
            item_context = "%s.items[%d]" % (source_id, item_index)
            if not isinstance(item, dict):
                warnings.append("%s 必须是对象" % item_context)
                continue
            item_id = item.get("id")
            if (
                not isinstance(item_id, str)
                or SOURCE_ITEM_RE.fullmatch(item_id) is None
                or not item_id.startswith(source_id + "-I")
            ):
                warnings.append("%s.id 格式非法（应为 %s-Innn）" % (item_context, source_id))
            elif item_id in seen_items:
                warnings.append("原文条目 ID 重复: %s" % item_id)
            else:
                seen_items.add(item_id)
            if not isinstance(item.get("location"), str) or not item.get("location").strip():
                warnings.append("%s.location 缺失" % item_context)
            if not isinstance(item.get("original"), str) or not item.get("original").strip():
                warnings.append("%s.original 缺失" % item_context)

            disposition = item.get("disposition")
            if disposition not in VALID_DISPOSITIONS:
                warnings.append(
                    "%s.disposition 非法；可选值: %s"
                    % (item_context, "/".join(sorted(VALID_DISPOSITIONS)))
                )
            requirements = item.get("requirements")
            if not isinstance(requirements, list):
                if requirements is not None:
                    warnings.append("%s.requirements 必须是数组" % item_context)
                requirements = []
            if disposition == "requirement" and not requirements:
                warnings.append("%s 标为 requirement 但没有提取 requirements" % item_context)
            if disposition == "superseded" and not isinstance(item.get("replacedBy"), str):
                warnings.append("%s.replacedBy 缺失" % item_context)

            for requirement_index, requirement in enumerate(requirements):
                requirement_context = "%s.requirements[%d]" % (item_context, requirement_index)
                if not isinstance(requirement, dict):
                    errors.append("%s 必须是对象" % requirement_context)
                    continue
                requirement_id = requirement.get("id")
                if (
                    not isinstance(requirement_id, str)
                    or SOURCE_REQUIREMENT_RE.fullmatch(requirement_id) is None
                    or not requirement_id.startswith(source_id + "-R")
                ):
                    errors.append(
                        "%s.id 格式非法；修复：改为 %s-R001 形式，下游 task-groups.json 按该 ID 引用"
                        % (requirement_context, source_id)
                    )
                elif requirement_id in seen_requirements:
                    errors.append(
                        "要求 ID 重复: %s；修复：同一 ID 只能出现一次，重复项改用未使用编号"
                        % requirement_id
                    )
                else:
                    seen_requirements.add(requirement_id)
                text = requirement.get("text")
                if not isinstance(text, str) or not text.strip():
                    errors.append(
                        "%s.text 缺失；修复：用一句话写明这条要求约束了什么" % requirement_context
                    )
                targets = requirement.get("targets")
                if not isinstance(targets, list) or not targets:
                    errors.append(
                        "%s.targets 必须是非空数组；修复：从 %s 中选择该要求需要送达的阶段"
                        % (requirement_context, "/".join(sorted(VALID_TARGETS)))
                    )
                else:
                    invalid_targets = [t for t in targets if t not in VALID_TARGETS]
                    if invalid_targets:
                        errors.append(
                            "%s.targets 非法: %s；可选值: %s"
                            % (
                                requirement_context,
                                ",".join(map(str, invalid_targets)),
                                "/".join(sorted(VALID_TARGETS)),
                            )
                        )
                    if len(targets) != len(set(targets)):
                        errors.append("%s.targets 不得重复" % requirement_context)

            if disposition == "background" and not requirements:
                undecided += 1
        if undecided == len(items):
            warnings.append(
                "%s 的 %d 行全部停留在 disposition=background 且无 requirements；"
                "sync 只生成原文，逐行判定仍需完成" % (source_id, undecided)
            )

    if expected_source_ids is not None:
        missing = sorted(expected_source_ids - seen_sources)
        unknown = sorted(seen_sources - expected_source_ids)
        if missing:
            errors.append(
                "source-context.json 缺少 PRD 来源: %s；"
                "修复：运行 source_context.py sync 依据 PRD 来源表补齐" % ", ".join(missing)
            )
        if unknown:
            errors.append(
                "source-context.json 存在 PRD 未登记来源: %s；"
                "修复：在 PRD 的「外部资料与实现约束」补登记，或从 json 中移除"
                % ", ".join(unknown)
            )
    return errors, warnings


def validate_source_context_refs(
    feature_dir: Path,
    expected_source_ids: Optional[Set[str]] = None,
) -> List[str]:
    """下游门禁用：只返回阻断项，不带 discuss 阶段的提示。"""
    errors, _ = validate_source_context(feature_dir, expected_source_ids)
    return errors


def _snapshot_for_source(feature_dir: Path, source_id: str, declared: Any) -> Optional[Path]:
    """定位来源快照：优先用已登记的 path，否则取 sources/SRC-NNN/ 下的第一个文件。"""
    candidate, error = _safe_snapshot_path(feature_dir, source_id, declared)
    if error is None and candidate is not None:
        return candidate
    source_dir = feature_dir / "sources" / source_id
    if not source_dir.is_dir():
        return None
    files = sorted(
        entry for entry in source_dir.iterdir()
        if entry.is_file() and not entry.name.startswith(".") and entry.stat().st_size > 0
    )
    return files[0] if files else None


def _sync_items(
    source_id: str,
    rows: List[Tuple[str, str]],
    existing_items: Any,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """按快照行重建 items，按 location 保留模型已填的 disposition / requirements。"""
    kept = {}  # type: Dict[str, Dict[str, Any]]
    if isinstance(existing_items, list):
        for item in existing_items:
            if isinstance(item, dict) and isinstance(item.get("location"), str):
                kept[item["location"]] = item

    items = []  # type: List[Dict[str, Any]]
    reused = 0
    for index, (location, text) in enumerate(rows, start=1):
        previous = kept.get(location)
        item = {
            "id": "%s-I%03d" % (source_id, index),
            "location": location,
            "original": text,
            "disposition": "background",
            "requirements": [],
        }
        if previous is not None:
            reused += 1
            if previous.get("disposition") in VALID_DISPOSITIONS:
                item["disposition"] = previous["disposition"]
            if isinstance(previous.get("requirements"), list):
                item["requirements"] = previous["requirements"]
            if isinstance(previous.get("replacedBy"), str):
                item["replacedBy"] = previous["replacedBy"]
        items.append(item)
    return items, len(items) - reused, reused


def sync_source_context(feature_dir: Path, only: Optional[str] = None) -> Tuple[int, List[str]]:
    """依据 PRD 来源表与 sources/ 快照重建 source-context.json 的机械字段。

    模型只需保留两个判断动作：给每行标 disposition，给 requirement 行写 text 与 targets。
    """
    try:
        from hooks.source_references import extract_source_references, has_source_section
    except ImportError:  # 直接以脚本方式运行时，仓库根目录不在 sys.path 上
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from hooks.source_references import extract_source_references, has_source_section

    messages = []  # type: List[str]
    prd = feature_dir / "PRD.md"
    if not prd.is_file():
        print(
            "ERROR: 未找到 %s。修复：先生成 PRD.md，再运行 sync。" % prd,
            file=sys.stderr,
        )
        return 1, messages
    content = prd.read_text(encoding="utf-8")
    if not has_source_section(content):
        print(
            "ERROR: PRD.md 缺少「外部资料与实现约束」章节。"
            "修复：先在 PRD.md 增加该章节并登记 SRC-NNN，没有外部资料时正文写「无」。",
            file=sys.stderr,
        )
        return 1, messages
    references = extract_source_references(content)
    if not references:
        messages.append("PRD 未登记外部资料，无需生成 source-context.json")
        return 0, messages

    data, load_errors = load_source_context(feature_dir)
    if load_errors:
        print("ERROR: %s 修复：修正 JSON 语法或删除该文件后重跑 sync。" % load_errors[0], file=sys.stderr)
        return 1, messages
    existing_by_id = {}  # type: Dict[str, Dict[str, Any]]
    if isinstance(data, dict) and isinstance(data.get("sources"), list):
        for source in data["sources"]:
            if isinstance(source, dict) and isinstance(source.get("id"), str):
                existing_by_id[source["id"]] = source

    sources = []  # type: List[Dict[str, Any]]
    pending = 0
    for reference in references:
        source_id = reference.source_id
        if only and source_id != only:
            if source_id in existing_by_id:
                sources.append(existing_by_id[source_id])
            continue
        previous = existing_by_id.get(source_id, {})
        snapshot = _snapshot_for_source(feature_dir, source_id, previous.get("path"))
        source = {
            "id": source_id,
            "name": reference.name or previous.get("name") or source_id,
            "availability": previous.get("availability") or "snapshot_only",
            "readStatus": previous.get("readStatus") or "complete",
            "freshness": previous.get("freshness", "unknown"),
        }
        if snapshot is None:
            source["availability"] = "never_provided"
            source["readStatus"] = "unreadable"
            source["items"] = []
            sources.append(source)
            messages.append(
                "%s 未找到快照（sources/%s/ 为空）：请提供资料、移除该依赖或暂停" % (source_id, source_id)
            )
            continue
        relative = snapshot.resolve(strict=False).relative_to(feature_dir.resolve(strict=False))
        source["path"] = "/".join(relative.parts)
        source["sha256"] = snapshot_sha256(snapshot)
        _, rows = _read_snapshot(snapshot)
        if not rows:
            messages.append(
                "%s 快照 %s 未解析出表格/字段行，items 保持原样，请人工登记要点"
                % (source_id, source["path"])
            )
            source["items"] = previous.get("items") if isinstance(previous.get("items"), list) else []
        else:
            items, added, reused = _sync_items(source_id, rows, previous.get("items"))
            source["items"] = items
            undecided = sum(
                1 for item in items
                if item["disposition"] == "background" and not item["requirements"]
            )
            pending += undecided
            messages.append(
                "%s: 共 %d 行（新增 %d，保留判定 %d），待判定 %d 行"
                % (source_id, len(items), added, reused, undecided)
            )
        sources.append(source)

    payload = {"version": 1, "sources": sources}
    source_context_path(feature_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    messages.append(
        "已写入 %s。下一步：为每行填写 disposition；标为 requirement 的行补 requirements[].text 与 targets"
        % source_context_path(feature_dir)
    )
    if pending:
        messages.append("仍有 %d 行 disposition=background 且无 requirements，请逐行判定" % pending)
    return 0, messages


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
    sync_parser = subparsers.add_parser(
        "sync", help="依据 PRD 来源表与 sources/ 快照生成 source-context.json 的机械字段"
    )
    sync_parser.add_argument("--feature-dir", required=True)
    sync_parser.add_argument("--source", default=None, help="只同步指定的 SRC-NNN")
    args = parser.parse_args(argv)

    if args.command == "sync":
        code, messages = sync_source_context(Path(args.feature_dir), args.source)
        for message in messages:
            print(message)
        return code

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
