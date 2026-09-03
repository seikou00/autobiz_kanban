#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compute the code candidate digest bound to verification decisions."""

from __future__ import print_function

import hashlib
import subprocess
from pathlib import Path

from hooks.run_context import load as load_run_context


def _git_bytes(root, args):
    process = subprocess.run(
        ["git", "-C", str(root)] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise ValueError(
            "CANDIDATE_DIGEST_UNRESOLVED: git {} failed at {}: {}".format(
                " ".join(args), root, process.stderr.decode("utf-8", errors="replace").strip()
            )
        )
    return process.stdout


def _git_bytes_optional(root, args):
    process = subprocess.run(
        ["git", "-C", str(root)] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return process.stdout if process.returncode == 0 else None


def compute(workspace, feature):
    context = load_run_context(workspace, feature)
    digest = hashlib.sha256()
    digest.update(str(context.get("contextDigest", "")).encode("utf-8"))
    repositories = sorted(
        (
            item for item in context.get("repositories", [])
            if isinstance(item, dict) and isinstance(item.get("root"), str)
        ),
        key=lambda item: str(item.get("repositoryId", "")),
    )
    for repository in repositories:
        root = Path(repository["root"]).resolve()
        digest.update(str(repository.get("repositoryId", "")).encode("utf-8"))
        head = _git_bytes_optional(root, ["rev-parse", "--verify", "HEAD"])
        if head is None:
            digest.update(b"UNBORN\n")
            digest.update(_git_bytes(root, ["diff", "--binary", "--cached", "--", "."]))
            digest.update(_git_bytes(root, ["diff", "--binary", "--", "."]))
        else:
            digest.update(head)
            digest.update(_git_bytes(root, ["diff", "--binary", "HEAD", "--", "."]))
        untracked = _git_bytes(
            root, ["ls-files", "--others", "--exclude-standard", "-z"]
        ).split(b"\0")
        for raw_path in sorted(value for value in untracked if value):
            relative = raw_path.decode("utf-8", errors="surrogateescape")
            if ".autobizdevops" in Path(relative).parts:
                continue
            path = root / relative
            digest.update(raw_path)
            if path.is_file():
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
