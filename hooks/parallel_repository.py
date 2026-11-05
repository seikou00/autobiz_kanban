"""Physical-repository locks and durable target reservations.

Acquire these locks before a run lock. Model editing never holds an OS lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json


def coordinates(binding: dict) -> tuple[Path, str]:
    repo = Path(binding["gitRoot"]).resolve()
    result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode or not result.stdout.strip():
        raise ValueError("parallel_repository_identity_unavailable:" + result.stderr.strip())
    common = (repo / result.stdout.strip()).resolve()
    branch = (binding.get("integration") or {}).get("branchName") or binding.get("baseBranch")
    if not branch:
        raise ValueError("parallel_repository_target_missing")
    identity = os.path.normcase(str(common)) + "\0refs/heads/" + branch
    return common / "autobiz-runtime", hashlib.sha256(identity.encode()).hexdigest()


def repository_lock(binding: dict, timeout: float | None = 14400) -> FileLock:
    base, _ = coordinates(binding)
    return FileLock(base / "repository.lock", timeout=timeout)


def reservation(binding: dict) -> dict:
    base, target = coordinates(binding)
    path = base / "targets" / (target + ".json")
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("parallel_repository_reservation_invalid")
    return value


def assert_reservation(binding: dict, workspace: Path, feature: str, run_id: str,
                       wave: int | None = None) -> None:
    saved = reservation(binding)
    if saved and (saved.get("workspace") != str(workspace.resolve()) or saved.get("feature") != feature
                  or saved.get("runId") != run_id or saved.get("wave") != wave):
        raise ValueError("parallel_repository_target_reserved")


def reserve(binding: dict, workspace: Path, feature: str, run_id: str, wave: int,
            base_sha: str) -> None:
    assert_reservation(binding, workspace, feature, run_id, wave)
    base, target = coordinates(binding)
    atomic_write_json(base / "targets" / (target + ".json"), {
        "workspace": str(workspace.resolve()), "feature": feature, "runId": run_id,
        "wave": wave, "baseSha": base_sha,
    })


def release(binding: dict, workspace: Path, feature: str, run_id: str, wave: int) -> None:
    assert_reservation(binding, workspace, feature, run_id, wave)
    base, target = coordinates(binding)
    (base / "targets" / (target + ".json")).unlink(missing_ok=True)


def release_if_owned(binding: dict, workspace: Path, feature: str, run_id: str, wave: int) -> None:
    saved = reservation(binding)
    if (saved.get("workspace"), saved.get("feature"), saved.get("runId"), saved.get("wave")) == (
            str(workspace.resolve()), feature, run_id, wave):
        release(binding, workspace, feature, run_id, wave)
