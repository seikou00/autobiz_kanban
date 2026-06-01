"""Artifact scanning — check file existence per node."""

from __future__ import annotations

from pathlib import Path


GLOB_CHARS = frozenset("*?[")
SPECS_GLOB_PATH = "specs/**/*.md"
ARTIFACT_STATUS_LABELS = {
    "generated": "已生成",
    "missing": "未生成",
}


def _has_glob(path: str) -> bool:
    return any(char in path for char in GLOB_CHARS)


def _relative_path(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _validate_specs_glob(artifact: dict, path: str) -> None:
    if artifact.get("id") != "specs" or not path.startswith("specs/"):
        raise ValueError(f"only specs artifacts may use glob paths: {path}")
    if path.count("/**/") != 1:
        raise ValueError(f"specs glob path must contain exactly one '/**/': {path}")
    if path != SPECS_GLOB_PATH:
        raise ValueError(f"specs glob path must be {SPECS_GLOB_PATH}: {path}")


def _artifact_label(artifact: dict) -> str:
    label = artifact.get("label")
    return label if isinstance(label, str) and label.strip() else artifact["id"]


def _set_artifact_status(entry: dict, status: str) -> None:
    entry["artifactStatus"] = status
    entry["artifactStatusLabel"] = ARTIFACT_STATUS_LABELS[status]


def _scan_glob_artifact(feature_dir: Path, workspace: Path, artifact: dict) -> dict:
    path = artifact["path"]
    _validate_specs_glob(artifact, path)
    matches = sorted(
        _relative_path(match, workspace)
        for match in feature_dir.glob(path)
        if match.is_file() and match.suffix == ".md"
    )
    entry: dict = {
        "id": artifact["id"],
        "artifactLabel": _artifact_label(artifact),
        "paths": matches,
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
