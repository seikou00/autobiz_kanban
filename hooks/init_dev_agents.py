#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Initialize code-workspace AGENTS.md from sys/{SYSTEM_ID}/AGENTS.md."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Mapping

try:
    from paths import get_sys_agents_md_path, normalize_system_no
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from paths import get_sys_agents_md_path, normalize_system_no  # type: ignore[no-redef]


DEFAULT_SYSTEM_NO = "lf3905"
SYSTEM_ID_ENV = "SYSTEM_ID"
SYSTEM_NO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
DOCUMENT_MAP_HEADING = "## 文档地图"
MARKDOWN_PATH_PATTERN = re.compile(r"[A-Za-z0-9_{}$./\\:-]+\.md", re.IGNORECASE)


class DevAgentsInitError(Exception):
    """Raised when AGENTS.md cannot be initialized safely."""


def resolve_system_no(env: Mapping[str, str] | None = None) -> str:
    if env is None:
        system_no = str(os.getenv("SYSTEM_ID") or "").strip()
    else:
        system_no = str(env.get(SYSTEM_ID_ENV, "") or "").strip()
    return system_no or DEFAULT_SYSTEM_NO


def validate_system_no(system_no: str) -> None:
    if not SYSTEM_NO_PATTERN.fullmatch(system_no) or not normalize_system_no(system_no):
        raise DevAgentsInitError(
            "invalid SYSTEM_ID: only letters, digits, dots, underscores, and hyphens are allowed"
        )


def resolve_sys_agents_md_path(system_no: str, plugin_root: Path | None = None) -> tuple[Path, bool]:
    source = get_sys_agents_md_path(system_no, plugin_root)
    if source.is_file() or normalize_system_no(system_no) == normalize_system_no(DEFAULT_SYSTEM_NO):
        return source, False

    fallback_source = get_sys_agents_md_path(DEFAULT_SYSTEM_NO, plugin_root)
    if fallback_source.is_file():
        return fallback_source, True

    return source, False


def extract_document_map_paths(agents_md: Path) -> list[Path]:
    section = _extract_document_map_section(agents_md.read_text(encoding="utf-8"))
    if not section:
        return []

    sys_dir = agents_md.parent
    seen: set[str] = set()
    paths: list[Path] = []
    for raw_path in MARKDOWN_PATH_PATTERN.findall(section):
        relative_path = _normalize_document_map_path(raw_path, sys_dir)
        if _is_root_agents_path(relative_path):
            continue

        key = relative_path.as_posix().casefold()
        if key not in seen:
            seen.add(key)
            paths.append(relative_path)
    return paths


def _extract_document_map_section(content: str) -> str:
    lines: list[str] = []
    in_document_map = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if stripped == DOCUMENT_MAP_HEADING:
                in_document_map = True
                continue
            if in_document_map:
                break

        if in_document_map:
            lines.append(line)

    return "\n".join(lines)


def _normalize_document_map_path(raw_path: str, sys_dir: Path) -> Path:
    path_text = raw_path.strip().strip("\"'")
    if _is_absolute_or_url(path_text):
        raise DevAgentsInitError(f"invalid AGENTS.md document map path: {raw_path}")

    posix_path = path_text.replace("\\", "/")
    sys_name = sys_dir.name

    if posix_path.startswith("{project_root}/"):
        relative_text = posix_path.removeprefix("{project_root}/")
    elif posix_path.startswith("{workspace}/"):
        relative_text = posix_path.removeprefix("{workspace}/")
    elif posix_path.startswith("{PLUGIN_DIR}/sys/"):
        relative_text = _strip_sys_prefix(posix_path.removeprefix("{PLUGIN_DIR}/"), sys_name, raw_path)
    elif posix_path.startswith("sys/"):
        relative_text = _strip_sys_prefix(posix_path, sys_name, raw_path)
    elif posix_path.startswith("./"):
        relative_text = posix_path.removeprefix("./")
    elif posix_path.startswith("{"):
        raise DevAgentsInitError(f"invalid AGENTS.md document map path: {raw_path}")
    else:
        relative_text = posix_path

    parts = relative_text.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise DevAgentsInitError(f"invalid AGENTS.md document map path: {raw_path}")
    if not parts[-1].casefold().endswith(".md"):
        raise DevAgentsInitError(f"invalid AGENTS.md document map path: {raw_path}")

    return Path(*parts)


def _strip_sys_prefix(posix_path: str, sys_name: str, raw_path: str) -> str:
    parts = posix_path.split("/")
    if len(parts) < 3 or parts[0] != "sys" or parts[1].casefold() != sys_name.casefold():
        raise DevAgentsInitError(f"invalid AGENTS.md document map path: {raw_path}")
    return "/".join(parts[2:])


def _is_absolute_or_url(path_text: str) -> bool:
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", path_text):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", path_text):
        return True
    return Path(path_text).is_absolute()


def _is_root_agents_path(relative_path: Path) -> bool:
    return len(relative_path.parts) == 1 and relative_path.name.casefold() == "agents.md"


def _resolve_sys_child(sys_dir: Path, relative_path: Path) -> Path:
    current = sys_dir
    for part in relative_path.parts:
        exact = current / part
        if exact.exists() or exact.is_symlink():
            current = exact
            continue

        if current.is_dir():
            folded_part = part.casefold()
            for candidate in sorted(current.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
                if candidate.name.casefold() == folded_part:
                    current = candidate
                    break
            else:
                current = exact
        else:
            current = exact
    return current


def _current_platform() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "win32"
    return sys.platform


def _link_relative(source: Path, target: Path) -> None:
    relative_source = os.path.relpath(source, target.parent)
    target.symlink_to(relative_source)


def _create_relative_symlink(source: Path, target: Path, kind: str) -> dict[str, object]:
    platform = _current_platform()
    link = {
        "kind": kind,
        "source": str(source),
        "target": str(target),
        "created": False,
        "skipped": False,
        "platform": platform,
        "link_type": "",
        "fallback": False,
    }

    if target.exists() or target.is_symlink():
        link["skipped"] = True
        return link

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _link_relative(source, target)
    except (OSError, ValueError) as symlink_error:
        if platform != "win32":
            raise DevAgentsInitError(f"failed to link {source} to {target}: {symlink_error}") from symlink_error

        try:
            shutil.copy2(source, target)
        except (OSError, shutil.Error) as copy_error:
            raise DevAgentsInitError(
                f"failed to link or copy {source} to {target}: "
                f"symlink failed: {symlink_error}; copy failed: {copy_error}"
            ) from copy_error

        link["created"] = True
        link["link_type"] = "copy"
        link["fallback"] = True
        return link

    link["created"] = True
    link["link_type"] = "symlink"
    return link


def init_dev_agents(
    code_workspace: Path,
    *,
    env: Mapping[str, str] | None = None,
    plugin_root: Path | None = None,
) -> dict[str, object]:
    workspace = code_workspace.expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise DevAgentsInitError(f"code workspace does not exist: {workspace}")

    system_no = resolve_system_no(env)
    validate_system_no(system_no)

    source, fallback_used = resolve_sys_agents_md_path(system_no, plugin_root)
    target = workspace / "AGENTS.md"

    result = {
        "ok": True,
        "created": False,
        "skipped": False,
        "system_no": system_no,
        "source": str(source),
        "target": str(target),
        "fallback": fallback_used,
        "message": "",
    }

    if not source.is_file():
        raise DevAgentsInitError(f"sys AGENTS.md not found: {source}")

    companion_sources: list[tuple[Path, Path]] = []
    for relative_path in extract_document_map_paths(source):
        companion_source = _resolve_sys_child(source.parent, relative_path)
        if not companion_source.is_file():
            raise DevAgentsInitError(f"sys document map file not found: {companion_source}")
        companion_sources.append((companion_source, workspace / relative_path))

    links = [_create_relative_symlink(source, target, "agents")]
    for companion_source, companion_target in companion_sources:
        links.append(_create_relative_symlink(companion_source, companion_target, "companion"))

    agents_link = links[0]
    result["created"] = agents_link["created"]
    result["skipped"] = agents_link["skipped"]
    result["links"] = links

    created_count = sum(1 for link in links if link["created"])
    skipped_count = sum(1 for link in links if link["skipped"])
    fallback_note = f" (fallback from SYSTEM_ID={system_no})" if fallback_used else ""
    result["message"] = (
        f"AGENTS.md initialized from sys/{source.parent.name}{fallback_note}: "
        f"{created_count} linked, {skipped_count} skipped"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Dev AGENTS.md from sys SYSTEM_ID")
    parser.add_argument("--code-workspace", required=True, help="Code workspace path")
    args = parser.parse_args()

    try:
        result = init_dev_agents(Path(args.code_workspace))
    except DevAgentsInitError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(result["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
