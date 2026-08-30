#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for artifact reference validation (hooks/artifact_ref_validator.py)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.artifact_ref_validator import (  # noqa: E402
    ArtifactRefError,
    design_marker_value,
    load_design_contract,
    validate_artifact_ref,
    validate_plan_design_coverage,
    validate_task_artifact_refs,
    validate_task_design_contract,
)


class ArtifactRefValidatorTests(unittest.TestCase):
    def test_template_marker_placeholder_is_not_a_boolean(self) -> None:
        text = "- **x-auto-no-http-api:** [true/false]\n"
        self.assertIsNone(design_marker_value(text, "x-auto-no-http-api"))

    def test_design_marker_cannot_conflict_with_defined_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "design.md").write_text(
                "- **x-auto-no-http-api:** true\n"
                "- **x-auto-no-sql:** true\n"
                "| API-001 | GET | /users |\n"
                "| DATA-001 | users | add |\n",
                encoding="utf-8",
            )
            _, errors = load_design_contract(base)
            reasons = {item["reason"] for item in errors}
            self.assertIn("design_api_marker_conflicts_with_definitions", reasons)
            self.assertIn("design_data_marker_conflicts_with_definitions", reasons)

    def test_task_validator_enforces_no_api_and_no_sql_markers(self) -> None:
        contract = {
            "ids": {"API": {"API-001"}, "DATA": {"DATA-001"}, "D": set()},
            "noHttpApi": True,
            "noSql": True,
        }
        errors = validate_task_design_contract(
            contract,
            {"id": "T001", "apiIds": ["API-001"], "dataIds": ["DATA-001"]},
        )
        self.assertEqual(
            {item["reason"] for item in errors},
            {
                "plan_api_ref_forbidden_by_design_marker",
                "plan_data_ref_forbidden_by_design_marker",
            },
        )

    def test_invalid_design_ref_cannot_satisfy_coverage(self) -> None:
        contract = {
            "ids": {"API": set(), "DATA": set(), "D": {"D-001"}},
            "noHttpApi": True,
            "noSql": True,
        }
        errors = validate_plan_design_coverage(
            contract,
            [{"id": "T001", "designRefs": ["design.md#D-999"]}],
        )
        self.assertIn(
            "missing_plan_json_decision_coverage",
            {item["reason"] for item in errors},
        )

    def test_valid_design_ref_with_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "design.md").write_text(
                "| API-001 | GET /users | List users |\n"
                "| API-002 | POST /users | Create user |\n",
                encoding="utf-8",
            )
            validate_artifact_ref(base, "design.md#API-001", design=True)

    def test_valid_design_ref_short_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "design.md").write_text("| DATA-001 | User | User entity |\n", encoding="utf-8")
            validate_artifact_ref(base, "#DATA-001", design=True)

    def test_invalid_ref_missing_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with self.assertRaises(ArtifactRefError) as ctx:
                validate_artifact_ref(base, "design.md - API-001", design=True)
            self.assertEqual(ctx.exception.reason, "invalid_artifact_ref_format")
            self.assertIn("缺少 # 符号", ctx.exception.detail)

    def test_invalid_ref_wrong_anchor_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with self.assertRaises(ArtifactRefError) as ctx:
                validate_artifact_ref(base, "design.md#INVALID", design=True)
            self.assertEqual(ctx.exception.reason, "invalid_artifact_ref_format")
            self.assertIn("格式非法", ctx.exception.detail)

    def test_invalid_ref_wrong_anchor_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "design.md").write_text("# Design\n", encoding="utf-8")
            with self.assertRaises(ArtifactRefError) as ctx:
                validate_artifact_ref(base, "design.md#REQ-001", design=True)
            self.assertEqual(ctx.exception.reason, "invalid_artifact_ref_type")
            self.assertIn("只允许 API/DATA/D", ctx.exception.detail)

    def test_invalid_ref_file_not_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with self.assertRaises(ArtifactRefError) as ctx:
                validate_artifact_ref(base, "missing.md#API-001", design=True)
            self.assertEqual(ctx.exception.reason, "missing_ref_file")
            self.assertIn("文件不存在", ctx.exception.detail)

    def test_invalid_ref_anchor_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "design.md").write_text("| API-001 | GET /users | List users |\n", encoding="utf-8")
            with self.assertRaises(ArtifactRefError) as ctx:
                validate_artifact_ref(base, "design.md#API-999", design=True)
            self.assertEqual(ctx.exception.reason, "missing_ref_anchor")
            self.assertIn("anchor 不存在", ctx.exception.detail)

    def test_valid_spec_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            specs_dir = base / "specs"
            specs_dir.mkdir()
            (specs_dir / "user.md").write_text(
                "### Requirement REQ-001: User can login\n"
                "#### Scenario SCN-001: Valid credentials\n",
                encoding="utf-8",
            )
            validate_artifact_ref(base, "specs/user.md#REQ-001", design=False)
            validate_artifact_ref(base, "specs/user.md#SCN-001", design=False)

    def test_cache_does_not_skip_format_or_type_checks(self) -> None:
        """The cache parameter must never bypass format/type/file/anchor checks.

        A prior, unsafe implementation returned early on a cache hit keyed only
        by the raw ref string, so a deleted file or a type change would still
        pass. The current contract is: cache is accepted but ignored, and every
        check always runs against the filesystem as it exists right now.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            design_file = base / "design.md"
            design_file.write_text("| API-001 | GET /users | List users |\n", encoding="utf-8")

            cache: dict[str, bool] = {}
            validate_artifact_ref(base, "design.md#API-001", design=True, cache=cache)

            design_file.unlink()

            with self.assertRaises(ArtifactRefError) as ctx:
                validate_artifact_ref(base, "design.md#API-001", design=True, cache=cache)
            self.assertEqual(ctx.exception.reason, "missing_ref_file")

    def test_validate_task_artifact_refs_all_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "design.md").write_text(
                "| API-001 | GET /users | List users |\n"
                "| DATA-001 | User | User entity |\n",
                encoding="utf-8",
            )
            specs_dir = base / "specs"
            specs_dir.mkdir()
            (specs_dir / "user.md").write_text(
                "### Requirement [REQ-001]: User management\n", encoding="utf-8"
            )

            task = {
                "id": "T001",
                "designRefs": ["design.md#API-001", "design.md#DATA-001"],
                "specRefs": ["specs/user.md#REQ-001"],
            }

            errors = validate_task_artifact_refs(base, task)
            self.assertEqual(errors, [])

    def test_validate_task_with_invalid_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            task = {
                "id": "T001",
                "designRefs": [
                    "design.md - API-001",  # Missing #
                    "design.md#INVALID",  # Invalid format
                    "missing.md#API-002",  # File not exists
                ],
                "specRefs": ["#REQ-001"],  # File not exists
            }

            errors = validate_task_artifact_refs(base, task)
            self.assertGreaterEqual(len(errors), 3)
            self.assertTrue(any("缺少 # 符号" in e["detail"] for e in errors))
            self.assertTrue(any("格式非法" in e["detail"] for e in errors))


if __name__ == "__main__":
    unittest.main()
