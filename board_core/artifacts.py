"""Artifact scanning — check file existence per node."""

from __future__ import annotations

from pathlib import Path


def scan_artifacts(
    feature_dir: Path, workspace: Path, artifacts_config: list[dict],
) -> list[dict]:
    """Check file existence for each artifact definition."""
    result: list[dict] = []
    for art in artifacts_config:
        art_path = feature_dir / art["path"]
        entry: dict = {
            "id": art["id"],
            "name": art.get("label", art.get("name", "")),
            "path": str(art_path.relative_to(workspace)),
        }
        if art_path.is_file():
            entry["exists"] = True
        else:
            entry["exists"] = False
        result.append(entry)
    return result
