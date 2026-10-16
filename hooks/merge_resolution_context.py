"""Prepare immutable, indexed reading material for a conflict editing agent.

The caller holds the candidate/run locks. Nothing here changes the candidate
worktree or its index; snapshots live in the artifact run instead.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from hooks.json_writer_common import atomic_write_json, feature_dir
from hooks.merge_resolution import git, git_output
from hooks.parallel_runtime import manifest_path, run_dir
from hooks.plan_json import batch_plan_path


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _blob(path: Path, sha: str) -> bytes:
    try:
        result = subprocess.run(["git", "cat-file", "blob", sha], cwd=path,
                                capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("candidate_context_blob_timeout") from exc
    if result.returncode:
        raise ValueError("candidate_context_blob_failed:" + sha)
    return result.stdout


def _text(content: bytes) -> str | None:
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _hunks(content: bytes) -> list[dict[str, Any]]:
    text = _text(content)
    if text is None:
        return []
    lines = text.splitlines(keepends=True)
    hunks = []
    start = None
    width = 0
    for position, line in enumerate(lines):
        marker = re.match(r"^(<{7,})(?: .*)?\r?\n?$", line)
        if start is None and marker:
            start, width = position, len(marker[1])
        elif start is not None and re.match(r"^>{%d}(?: .*)?\r?\n?$" % width, line):
            hunks.append({"id": len(hunks) + 1, "startLine": start + 1,
                          "endLine": position + 1, "content": "".join(lines[start:position + 1])})
            start = None
    return hunks


def _business_context(workspace: Path, feature: str, manifest: dict[str, Any],
                      record: dict[str, Any], path: Path, files: list[str],
                      parents: list[str]) -> dict[str, Any]:
    incoming = list(record.get("batchIds") or [])
    ours: set[str] = set()
    for train in manifest.get("mergeTrains", {}).values():
        if (not isinstance(train, dict) or train.get("status") != "promoted"
                or train.get("repositoryRef") != record.get("repositoryRef")):
            continue
        sha, base = train.get("candidateSha") or train.get("promotedSha"), train.get("baseSha")
        if not sha or not base:
            continue
        changed = git_output(path, "--literal-pathspecs", "diff", "--name-only", "-z", base, sha, "--", *files)
        if changed and git(path, "merge-base", "--is-ancestor", sha, parents[0]).returncode == 0:
            ours.update(train.get("batchIds") or [])
    batches = {}
    for batch_id in sorted(set(incoming) | ours):
        batch = manifest.get("batches", {}).get(batch_id) or {}
        plan = batch_plan_path(feature_dir(workspace, feature), batch_id).resolve()
        data = None
        plan_status = "missing"
        if plan.is_file():
            try:
                data = json.loads(plan.read_text(encoding="utf-8"))
                plan_status = "available" if isinstance(data, dict) else "invalid"
            except (OSError, ValueError):
                plan_status = "invalid"
        evidence = []
        for stage, state in (batch.get("stageStates") or {}).items():
            if isinstance(state, dict):
                evidence.append({"stage": stage, "status": state.get("status"),
                                 "evidenceIds": state.get("evidenceIds", []),
                                 "evidencePath": state.get("evidencePath"),
                                 "availability": "available" if state.get("evidencePath")
                                 and Path(state["evidencePath"]).is_file() else "missing"})
        task_ids = batch.get("taskIds") or []
        if not task_ids and isinstance(data, dict):
            task_ids = [task["id"] for task in data.get("tasks", [])
                        if isinstance(task, dict) and isinstance(task.get("id"), str)]
        batches[batch_id] = {"taskIds": task_ids, "planPath": str(plan),
                             "planStatus": plan_status, "planSnapshot": data,
                             "stageEvidence": evidence, "evidenceIds": batch.get("evidenceIds", [])}
    return {"incomingBatchIds": incoming, "oursRelatedBatchIds": sorted(ours),
            "oursTraceStatus": "available" if ours else "unavailable",
            "batches": batches}


def prepare_context(workspace: Path, feature: str, run_id: str, manifest: dict[str, Any],
                    record: dict[str, Any], path: Path, files: list[str],
                    parents: list[str]) -> dict[str, str]:
    """Create once, then validate and reuse even after partial unstaged edits."""
    directory = run_dir(workspace, feature, run_id).resolve()
    stages: dict[str, dict[str, dict[str, str]]] = {name: {} for name in files}
    raw = git_output(path, "ls-files", "--unmerged", "-z")
    for entry in raw.split("\0"):
        if not entry:
            continue
        info, name = entry.split("\t", 1)
        if name in stages:
            mode, sha, stage = info.split()
            stages[name][stage] = {"mode": mode, "blobSha": sha}
    identity = {"schemaVersion": 1, "runId": run_id, "feature": feature,
                "repositoryRef": record["repositoryRef"], "wave": record["wave"],
                "worktreePath": str(path), "parents": parents, "files": stages,
                "deliveryCommits": record.get("deliveryCommits", {})}
    identity_digest = _digest(_json_bytes(identity))
    # Hash the repository identity, avoiding collisions caused by sanitizing refs.
    repo_key = _digest(str(record["repositoryRef"]).encode())[:16]
    root = directory / "resolution-context" / repo_key / f"wave-{record['wave']:03d}" / identity_digest
    index_path = root / "index.json"
    locations = {"runDir": str(directory), "manifestPath": str(manifest_path(workspace, feature, run_id).resolve()),
                 "contextPath": str(index_path), "identityDigest": identity_digest}
    previous = record.get("resolutionContext")
    if previous and (not isinstance(previous, dict) or previous.get("identityDigest") != identity_digest
                     or previous.get("contextPath") != str(index_path)):
        raise ValueError("candidate_context_identity_changed")
    if index_path.exists() or previous:
        try:
            content = index_path.read_bytes()
            index = json.loads(content)
            digest = _digest(content)
            if (index["identity"] != identity or (previous and previous.get("contextDigest") != digest)):
                raise ValueError("candidate_context_integrity_failed")
            for relative, expected in index["assets"].items():
                asset = root / relative
                if not asset.resolve().is_relative_to(root.resolve()) or _digest(asset.read_bytes()) != expected:
                    raise ValueError("candidate_context_integrity_failed")
            return {**locations, "contextDigest": digest}
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("candidate_context_missing_or_invalid") from exc
    if not any(stages.values()):
        raise ValueError("candidate_context_unmerged_stages_missing")
    root.mkdir(parents=True, exist_ok=True)
    assets: dict[str, str] = {}

    def save(relative: str, content: bytes) -> str:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        assets[relative] = _digest(content)
        return str(target)

    entries = []
    for number, name in enumerate(files, 1):
        prefix = f"files/{number:03d}"
        entry: dict[str, Any] = {"path": name, "candidatePath": str(path / name), "sides": {}}
        contents = {}
        for stage, side in (("1", "base"), ("2", "ours"), ("3", "theirs")):
            blob = stages[name].get(stage)
            content = _blob(path, blob["blobSha"]) if blob else None
            contents[side] = content
            entry["sides"][side] = ({"exists": True, **blob, "textUtf8": _text(content) is not None,
                                    "snapshotPath": save(f"{prefix}/{side}", content)}
                                   if content is not None else {"exists": False, "snapshotPath": None})
        current = (path / name).read_bytes() if (path / name).is_file() else b""
        # Legacy prepared records may already contain partial edits. Prefer the
        # saved original conflict for the reading snapshot, never overwrite edits.
        original = (record.get("conflictContext") or {}).get("conflictMarkers", {}).get(name)
        if (record.get("resolution") and isinstance(original, str)
                and _hunks(original.encode("utf-8"))):
            current = original.encode("utf-8")
        entry["conflictedSnapshotPath"] = save(f"{prefix}/conflicted", current)
        hunks = _hunks(current)
        entry["hunksPath"] = save(f"{prefix}/conflicts.json", _json_bytes(hunks))
        entry["hunks"] = [{key: value for key, value in hunk.items() if key != "content"} for hunk in hunks]
        changes = []
        for side in ("ours", "theirs"):
            base_text = _text(contents["base"] or b"")
            side_text = _text(contents[side] or b"")
            if base_text is None or side_text is None:
                changes.append(f"base -> {side}: binary or non-UTF8; consult byte-preserving snapshots.\n")
            else:
                for line in difflib.unified_diff(base_text.splitlines(keepends=True), side_text.splitlines(keepends=True),
                                                 fromfile="base", tofile=side):
                    changes.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        entry["changesPath"] = save(f"{prefix}/changes.diff", "".join(changes).encode("utf-8"))
        entries.append(entry)
    business_path = save("business.json", _json_bytes(_business_context(workspace, feature, manifest, record, path, files, parents)))
    index = {"schemaVersion": 1, "identity": identity, **locations,
             "sideMeaning": {"base": "Git index stage 1, not the candidate baseSha",
                             "ours": "Git index stage 2, current candidate side",
                             "theirs": "Git index stage 3, incoming delivery side"},
             "businessContextPath": business_path, "files": entries, "assets": assets,
             "snapshotRole": "Original reading context; candidate files may contain newer partial edits."}
    # Publish last: an interrupted preparation can rebuild unpublished assets.
    atomic_write_json(index_path, index)
    return {**locations, "contextDigest": _digest(index_path.read_bytes())}
