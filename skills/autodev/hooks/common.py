#!/usr/bin/env python3
"""Shared helpers for Autodev board_config artifact checks."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import BoardConfigError, load_record_workflow_contracts, load_repo_workflow_contracts  # noqa: E402
from board_core.workflow_compiler import BASE_WORKFLOW_PROFILE  # noqa: E402


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
    required_inputs: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()

    def requires_artifact(self, name: str) -> bool:
        return name in self.required_inputs or name in self.required_outputs

    @property
    def feature_dir(self) -> Path:
        return self.root / ".autobizdevops" / "features" / self.slug

    def file(self, name: str) -> Path:
        return self.feature_dir / name


def is_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def artifact_exists(feature_dir: Path, name: str) -> bool:
    if any(char in name for char in "*?["):
        return any(path.is_file() and path.stat().st_size > 0 for path in feature_dir.glob(name))

    path = feature_dir / name
    if path.is_dir():
        return any(child.is_file() and child.stat().st_size > 0 for child in path.rglob("*"))
    return is_nonempty(path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def missing_files(root: Path, slug: str, file_names: Iterable[str]) -> list[str]:
    feature_dir = root / ".autobizdevops" / "features" / slug
    return [name for name in file_names if not artifact_exists(feature_dir, name)]


def require_feature_dir(root: Path, slug: str) -> None:
    feature_dir = root / ".autobizdevops" / "features" / slug
    if not feature_dir.is_dir():
        raise HookCheckError("missing_feature_dir", str(feature_dir))


def validate_required_files(root: Path, slug: str, file_names: Iterable[str]) -> None:
    file_names = tuple(file_names)
    if not file_names:
        return
    require_feature_dir(root, slug)
    missing = missing_files(root, slug, file_names)
    if missing:
        raise HookCheckError("missing_required_artifacts", ", ".join(missing))


def load_artifact_config(
    repo_root: Path,
    skill: str,
    *,
    workspace_root: Path | None = None,
    workflow_profile: str = BASE_WORKFLOW_PROFILE,
    workflow_decisions: dict[str, str] | None = None,
    workflow_record: dict | None = None,
) -> ArtifactConfig:
    try:
        if workflow_record is not None:
            contracts = load_record_workflow_contracts(
                repo_root,
                workflow_record,
                workspace=workspace_root,
            )
        else:
            contracts = load_repo_workflow_contracts(
                repo_root,
                workspace=workspace_root,
                profile=workflow_profile,
                workflow_decisions=workflow_decisions,
            )
        contract = contracts.contract_for_skill(skill)
    except BoardConfigError as error:
        raise HookCheckError("invalid_board_config", str(error)) from error

    return ArtifactConfig(
        skill=skill,
        required_inputs=contract.required_inputs,
        required_outputs=contract.required_outputs,
        validators=contract.validators,
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
    return len(
        re.findall(
            r"^###\s+(?:Task\s+\[T\d{3}\]\s*:\s+.+|\d+[.)]\s+.+)$",
            read_text(plan),
            re.MULTILINE,
        )
    )


def task_statuses(plan: Path) -> list[str]:
    if not is_nonempty(plan):
        return []
    statuses: list[str] = []
    for line in read_text(plan).splitlines():
        normalized = re.sub(r"\*+", "", line).strip()
        match = re.match(r"^[-*]\s*(?:状态|Status)\s*[:：]\s*(.+)$", normalized, re.IGNORECASE)
        if match:
            statuses.append(match.group(1).strip())
    return statuses
