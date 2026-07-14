"""Artifact scanning — check file existence per node."""

from __future__ import annotations

from pathlib import Path


GLOB_CHARS = frozenset("*?[")
GLOB_ARTIFACT_CONTRACTS = {
    "specs": {"path": "specs/**/*.md", "suffix": ".md"},
    "code_exploration_cache": {
        "path": "cache/code-exploration/**/*.json",
        "suffix": ".json",
    },
}
ARTIFACT_STATUS_LABELS = {
    "generated": "已生成",
    "missing": "未生成",
}


def _has_glob(path: str) -> bool:
    return any(char in path for char in GLOB_CHARS)


def _relative_path(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _validate_artifact_glob(artifact: dict, path: str) -> str:
    artifact_id = artifact.get("id")
    contract = GLOB_ARTIFACT_CONTRACTS.get(artifact_id)
    if contract is None:
        raise ValueError(f"unsupported artifact glob: {artifact_id}:{path}")
    expected_path = contract["path"]
    if path != expected_path:
        raise ValueError(f"{artifact_id} glob path must be {expected_path}: {path}")
    return contract["suffix"]


def _artifact_label(artifact: dict) -> str:
    label = artifact.get("label")
    return label if isinstance(label, str) and label.strip() else artifact["id"]


def _set_artifact_status(entry: dict, status: str) -> None:
    entry["artifactStatus"] = status
    entry["artifactStatusLabel"] = ARTIFACT_STATUS_LABELS[status]



def _scan_glob_artifact(feature_dir: Path, workspace: Path, artifact: dict) -> dict:
    path = artifact["path"]
    suffix = _validate_artifact_glob(artifact, path)
    matches = sorted(
        _relative_path(match, workspace)
        for match in feature_dir.glob(path)
        if match.is_file() and match.suffix == suffix
    )
    entry: dict = {
        "id": artifact["id"],
        "artifactLabel": _artifact_label(artifact),
        # 无匹配（feature 初始化阶段）时兜底回显占位 glob，供看板展示；
        # 有真实文件时仍展开真实文件列表。
        "paths": matches or [_relative_path(feature_dir / path, workspace)],
    }
    if matches:
        _set_artifact_status(entry, "generated")
    else:
        _set_artifact_status(entry, "missing")
    return entry


def _scan_file_artifact(feature_dir: Path, workspace: Path, artifact: dict) -> dict:
    artifact_path = feature_dir / artifact["path"]
    entry: dict = {
        "id": artifact["id"],
        "artifactLabel": _artifact_label(artifact),
        "path": _relative_path(artifact_path, workspace),
    }
    if artifact_path.is_file():
        _set_artifact_status(entry, "generated")
    else:
        _set_artifact_status(entry, "missing")
    return entry


def scan_artifacts(
    feature_dir: Path, workspace: Path, artifacts_config: list[dict],
) -> list[dict]:
    """Check file existence for each artifact definition."""
    result: list[dict] = []
    for art in artifacts_config:
        if _has_glob(art["path"]):
            result.append(_scan_glob_artifact(feature_dir, workspace, art))
        else:
            result.append(_scan_file_artifact(feature_dir, workspace, art))
    return result
