#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test handoff integrity checker for defer_to_test_stages mode."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.plan_json import (
    defer_to_test_stages_enabled,
    load_and_validate_plan,
    plan_json_path,
)


def _sha256_file(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def _check_test_file_exists(
    repo_root: Path, file_info: dict[str, Any]
) -> tuple[bool, str]:
    """Check if test file exists and matches expected attributes.

    Returns:
        (is_valid, error_message)
    """
    repo = file_info.get("repo")
    rel_path = file_info.get("path")
    expected_sha256 = file_info.get("sha256")
    expected_size = file_info.get("sizeBytes")

    if not rel_path:
        return False, "missing_path"

    # Construct full path
    file_path = repo_root / rel_path

    # Check existence
    if not file_path.exists():
        return False, f"file_not_found:{rel_path}"

    # Check it's a regular file
    if not file_path.is_file():
        return False, f"not_regular_file:{rel_path}"

    # Check size
    actual_size = file_path.stat().st_size
    if actual_size == 0:
        return False, f"empty_file:{rel_path}"

    if expected_size and actual_size != expected_size:
        return False, f"size_mismatch:{rel_path}:expected={expected_size}:actual={actual_size}"

    # Check SHA256
    if expected_sha256:
        actual_sha256 = _sha256_file(file_path)
        if actual_sha256 != expected_sha256:
            return False, f"sha256_mismatch:{rel_path}"

    return True, ""


def _check_source_fingerprints(
    repo_root: Path, fingerprints: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    """Check if source files match their fingerprints.

    Returns:
        (all_valid, error_messages)
    """
    errors = []

    for fp in fingerprints:
        rel_path = fp.get("path")
        expected_sha256 = fp.get("sha256")

        if not rel_path:
            errors.append("source_fingerprint_missing_path")
            continue

        file_path = repo_root / rel_path

        if not file_path.exists():
            errors.append(f"source_file_not_found:{rel_path}")
            continue

        if expected_sha256:
            actual_sha256 = _sha256_file(file_path)
            if actual_sha256 != expected_sha256:
                errors.append(f"source_fingerprint_mismatch:{rel_path}")

    return len(errors) == 0, errors


def check_test_handoff_integrity(
    feature_dir: Path, code_workspace: Path
) -> dict[str, Any]:
    """Check integrity of test handoff items.

    Returns a structured result with:
    - ok: bool
    - errors: list of error messages
    - obsolete_items: list of handoff IDs that need regeneration
    - missing_items: list of handoff IDs with missing files
    """
    result = {
        "ok": True,
        "errors": [],
        "obsolete_items": [],
        "missing_items": [],
    }

    plan_path = plan_json_path(feature_dir)
    plan = load_and_validate_plan(plan_path)

    # Only check if defer_to_test_stages is enabled
    if not defer_to_test_stages_enabled(plan):
        return result

    # Get all batch plans
    batches = plan.get("_bundleBatches", {})
    if not isinstance(batches, dict):
        result["ok"] = False
        result["errors"].append("missing_batch_plan_projection")
        return result

    for batch_id, batch in batches.items():
        if not isinstance(batch, dict):
            continue

        test_handoff = batch.get("testHandoff")
        if not isinstance(test_handoff, dict):
            # No test handoff for this batch, skip
            continue

        items = test_handoff.get("items", [])
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            handoff_id = item.get("handoffId")
            asset_status = item.get("assetStatus")

            # Skip if already marked as missing or obsolete
            if asset_status in ("missing", "obsolete"):
                continue

            # Check test files
            files = item.get("files", [])
            if not isinstance(files, list):
                result["errors"].append(f"{handoff_id}:missing_files_list")
                result["ok"] = False
                continue

            for file_info in files:
                if not isinstance(file_info, dict):
                    continue

                is_valid, error = _check_test_file_exists(code_workspace, file_info)
                if not is_valid:
                    result["errors"].append(f"{handoff_id}:{error}")
                    result["missing_items"].append(handoff_id)
                    result["ok"] = False
                    break

            # Check source fingerprints
            source_fps = item.get("sourceFingerprints", [])
            if isinstance(source_fps, list):
                fps_valid, fp_errors = _check_source_fingerprints(
                    code_workspace, source_fps
                )
                if not fps_valid:
                    for error in fp_errors:
                        result["errors"].append(f"{handoff_id}:{error}")
                    result["obsolete_items"].append(handoff_id)
                    result["ok"] = False

    return result


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Check test handoff integrity for defer_to_test_stages mode"
    )
    parser.add_argument(
        "--feature",
        required=True,
        help="Feature directory path",
    )
    parser.add_argument(
        "--code-workspace",
        required=True,
        help="Code workspace (repo root) path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    args = parser.parse_args()

    feature_dir = Path(args.feature).resolve()
    code_workspace = Path(args.code_workspace).resolve()

    if not feature_dir.is_dir():
        print(f"Error: Feature directory not found: {feature_dir}", file=sys.stderr)
        return 1

    if not code_workspace.is_dir():
        print(f"Error: Code workspace not found: {code_workspace}", file=sys.stderr)
        return 1

    try:
        result = check_test_handoff_integrity(feature_dir, code_workspace)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result["ok"]:
                print("✓ Test handoff integrity check passed")
            else:
                print("✗ Test handoff integrity check failed")
                for error in result["errors"]:
                    print(f"  - {error}")
                if result["missing_items"]:
                    print(f"\nMissing test assets: {', '.join(result['missing_items'])}")
                if result["obsolete_items"]:
                    print(f"Obsolete test assets: {', '.join(result['obsolete_items'])}")

        return 0 if result["ok"] else 1

    except Exception as exc:
        print(f"Error checking test handoff integrity: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "errors": [str(exc)]}))
        return 1


if __name__ == "__main__":
    sys.exit(_main())
