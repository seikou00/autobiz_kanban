#!/usr/bin/env python3
"""Shared helpers for Autodev YAML artifact checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BLOCK_EXIT_CODE = 2


class HookCheckError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


@dataclass(frozen=True)
class ArtifactConfig:
    skill: str
    required_inputs: tuple[str, ...]
    required_outputs: tuple[str, ...]
    validators: tuple[str, ...]


@dataclass(frozen=True)
class HookContext:
    skill: str
    slug: str
    root: Path

    @property
    def feature_dir(self) -> Path:
        return self.root / ".autobizdevops" / "features" / self.slug

    def file(self, name: str) -> Path:
        return self.feature_dir / name


def is_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def missing_files(root: Path, slug: str, file_names: Iterable[str]) -> list[str]:
    feature_dir = root / ".autobizdevops" / "features" / slug
    return [name for name in file_names if not is_nonempty(feature_dir / name)]


def require_feature_dir(root: Path, slug: str) -> None:
    feature_dir = root / ".autobizdevops" / "features" / slug
    if not feature_dir.is_dir():
        raise HookCheckError("missing_feature_dir", str(feature_dir))


def validate_required_files(root: Path, slug: str, file_names: Iterable[str]) -> None:
    require_feature_dir(root, slug)
    missing = missing_files(root, slug, file_names)
    if missing:
        raise HookCheckError("missing_required_artifacts", ", ".join(missing))


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def load_simple_yaml(path: Path) -> dict[str, object]:
    data: dict[str, object] = {}
    current_list: str | None = None
    for lineno, raw_line in enumerate(read_text(path).splitlines(), start=1):
        line = _strip_comment(raw_line).rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current_list is None:
                raise HookCheckError("invalid_yaml", f"{path}:{lineno}: list item without key")
            data.setdefault(current_list, [])
            value = _parse_scalar(line[4:])
            if not value:
                raise HookCheckError("invalid_yaml", f"{path}:{lineno}: empty list item")
            assert isinstance(data[current_list], list)
            data[current_list].append(value)
            continue
        if raw_line.startswith(" "):
            raise HookCheckError("invalid_yaml", f"{path}:{lineno}: unsupported indentation")
        if ":" not in line:
            raise HookCheckError("invalid_yaml", f"{path}:{lineno}: expected key")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise HookCheckError("invalid_yaml", f"{path}:{lineno}: empty key")
        value = value.strip()
        if value:
            data[key] = _parse_scalar(value)
            current_list = None
        else:
            data[key] = []
            current_list = key
    return data


def config_path_for_skill(repo_root: Path, skill: str) -> Path:
    return repo_root / "skills" / "autodev" / skill / "hooks" / "artifact-check.yaml"


def load_artifact_config(repo_root: Path, skill: str) -> ArtifactConfig:
    path = config_path_for_skill(repo_root, skill)
    if not path.is_file():
        raise HookCheckError("missing_artifact_config", str(path))

    data = load_simple_yaml(path)
    config_skill = data.get("skill")
    if config_skill != skill:
        raise HookCheckError("invalid_artifact_config", f"{path}: skill must be {skill}")

    def read_list(name: str) -> tuple[str, ...]:
        value = data.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise HookCheckError("invalid_artifact_config", f"{path}: {name} must be a list")
        return tuple(value)

    return ArtifactConfig(
        skill=skill,
        required_inputs=read_list("required_inputs"),
        required_outputs=read_list("required_outputs"),
        validators=read_list("validators"),
    )


def fail_line(ctx: HookContext, reason: str, extra: str = "") -> int:
    print(f"POST_SKILL_FAIL skill={ctx.skill} reason={reason}{extra}")
    return 1


def warn(ctx: HookContext, reason: str, extra: str = "") -> None:
    print(f"POST_SKILL_WARN skill={ctx.skill} reason={reason}{extra}")


def info(ctx: HookContext, reason: str, extra: str = "") -> None:
    print(f"POST_SKILL_INFO skill={ctx.skill} reason={reason}{extra}")


def task_count(plan: Path) -> int:
    if not is_nonempty(plan):
        return 0
    return len(re.findall(r"^### [0-9]+\.", read_text(plan), re.MULTILINE))


def task_statuses(plan: Path) -> list[str]:
    if not is_nonempty(plan):
        return []
    return [
        match.group(1).strip()
        for match in re.finditer(r"^[ \t]*[-*][ \t]*\*\*状态:\*\*[ \t]*(.+)$", read_text(plan), re.MULTILINE)
    ]
