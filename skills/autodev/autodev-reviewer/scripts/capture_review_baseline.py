#!/usr/bin/env python3
"""Capture the Git state that bounds a feature implementation review."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class BaselineCaptureError(RuntimeError):
    """Raised when a requested repository cannot produce a trustworthy baseline."""


def _git(path: Path, *args: str, allow_failure: bool = False) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if allow_failure:
            return ""
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise BaselineCaptureError(f"git {' '.join(args)} failed in {path}: {detail}")
    return result.stdout


def _git_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists():
        raise BaselineCaptureError(f"repository path does not exist: {candidate}")
    root = _git(candidate, "rev-parse", "--show-toplevel").strip()
    return Path(root).resolve()


def _nul_paths(value: str) -> list[str]:
    return sorted({item for item in value.split("\0") if item})


def _load_manifest(path: Path) -> list[tuple[str, Path]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BaselineCaptureError(f"cannot read module manifest {path}: {error}") from error

    modules = payload.get("modules") if isinstance(payload, dict) else None
    if not isinstance(modules, list) or not modules:
        raise BaselineCaptureError(f"module manifest has no modules: {path}")

    repositories: list[tuple[str, Path]] = []
    for index, module in enumerate(modules, start=1):
        if not isinstance(module, dict) or not isinstance(module.get("path"), str):
            raise BaselineCaptureError(f"module #{index} has no valid path in {path}")
        module_id = str(module.get("module") or f"module-{index}")
        repositories.append((module_id, Path(module["path"])))
    return repositories


def _parse_repo(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise BaselineCaptureError("--repo must use id=/absolute/or/relative/path")
    repository_id, raw_path = value.split("=", 1)
    if not repository_id.strip() or not raw_path.strip():
        raise BaselineCaptureError("--repo must include both id and path")
    return repository_id.strip(), Path(raw_path.strip())


def _deduplicate_repositories(candidates: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    by_root: dict[Path, str] = {}
    for candidate_id, candidate_path in candidates:
        root = _git_root(candidate_path)
        by_root.setdefault(root, candidate_id)

    used_ids: set[str] = set()
    result: list[tuple[str, Path]] = []
    for index, (root, candidate_id) in enumerate(sorted(by_root.items(), key=lambda item: str(item[0]))):
        base_id = candidate_id.strip() or root.name or f"repo-{index + 1}"
        repository_id = base_id
        suffix = 2
        while repository_id in used_ids:
            repository_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(repository_id)
        result.append((repository_id, root))
    return result


def _capture_repository(repository_id: str, root: Path) -> dict:
    head_sha = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True).strip()
    status_lines = [
        line
        for line in _git(root, "status", "--short", "--untracked-files=all").splitlines()
        if line
    ]
    tracked_dirty = _nul_paths(_git(root, "diff", "--name-only", "-z", "HEAD"))
    untracked = _nul_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    initial_dirty_paths = sorted(set(tracked_dirty) | set(untracked))
    return {
        "id": repository_id,
        "path": str(root),
        "base_sha": head_sha,
        "branch": branch or None,
        "initial_status": status_lines,
        "initial_dirty_paths": initial_dirty_paths,
        "initial_untracked_paths": untracked,
        "scope_confidence": "full" if not initial_dirty_paths else "partial",
    }


def capture_baseline(
    *,
    output: Path,
    module_manifest: Path | None,
    explicit_repositories: list[str],
    cwd: Path,
) -> dict:
    destination = output.expanduser().resolve()
    if destination.exists():
        raise BaselineCaptureError(f"baseline already exists; refusing to overwrite: {destination}")

    candidates: list[tuple[str, Path]] = []
    capture_sources: list[str] = []
    if module_manifest is not None:
        candidates.extend(_load_manifest(module_manifest.expanduser().resolve()))
        capture_sources.append("module_manifest")
    if explicit_repositories:
        candidates.extend(_parse_repo(value) for value in explicit_repositories)
        capture_sources.append("explicit_repo")
    if not candidates:
        candidates.append(("current", cwd))
        capture_sources.append("cwd")

    repositories = [
        _capture_repository(repository_id, root)
        for repository_id, root in _deduplicate_repositories(candidates)
    ]
    payload = {
        "schema_version": "autobizdevops.review-baseline.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_sources": capture_sources,
        "repositories": repositories,
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, destination)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--module-manifest", type=Path)
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="additional repository in id=path form; may be repeated",
    )
    args = parser.parse_args(argv)

    try:
        payload = capture_baseline(
            output=args.output,
            module_manifest=args.module_manifest,
            explicit_repositories=args.repo,
            cwd=Path.cwd(),
        )
    except BaselineCaptureError as error:
        print(f"REVIEW_BASELINE_ERROR {error}", file=sys.stderr)
        return 2

    print(
        "REVIEW_BASELINE_CAPTURED "
        f"output={args.output.expanduser().resolve()} repositories={len(payload['repositories'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
