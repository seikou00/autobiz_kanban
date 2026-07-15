from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTODEV_HOOKS = ROOT / "skills" / "autodev" / "hooks"
for path in (ROOT, AUTODEV_HOOKS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from artifact_check import (  # noqa: E402
    validate_requirements_eval_verdict,
    validate_review_baseline_contract,
)
from common import HookContext  # noqa: E402


VALID_REPORT = """# Requirements Evaluation

## Review Mode

independent_task

## Review Topology

dual_axis_parallel

## Verdict

PASS_WITH_WARNINGS

## Summary

summary

## Review Scope

scope

## Evidence

evidence

## Repositories Reviewed

repositories

## Axis Summary

| Axis | Status | Findings | Worst Finding |
|---|---|---|---|
| Standards | WARN | 1 | STD-001 |
| Spec | PASS | 0 | none |

## Standards Sources

sources

## Standards Review

findings

## Spec Review

none

## Requirement Coverage

coverage

## E2E Focus

focus

## Blockers

none

## Warnings

warning

## Required Next Action

continue
"""


class RequirementsEvalContractTest(unittest.TestCase):
    def _validate(self, content: str) -> int:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            feature_dir = root / ".autobizdevops" / "features" / "demo"
            feature_dir.mkdir(parents=True)
            (feature_dir / "REQUIREMENTS_EVAL.md").write_text(content, encoding="utf-8")
            with redirect_stdout(StringIO()):
                return validate_requirements_eval_verdict(
                    HookContext(skill="autodev-reviewer", slug="demo", root=root)
                )

    def test_accepts_heading_style_verdict_and_dual_axis_contract(self) -> None:
        self.assertEqual(self._validate(VALID_REPORT), 0)

    def test_rejects_report_without_standards_axis_status(self) -> None:
        report = VALID_REPORT.replace("| Standards | WARN | 1 | STD-001 |\n", "")
        self.assertGreater(self._validate(report), 0)

    def test_rejects_legacy_single_axis_report(self) -> None:
        report = """# Requirements Evaluation

## Verdict

PASS

## Requirement Coverage

legacy
"""
        self.assertGreater(self._validate(report), 0)

    def test_rejects_unknown_review_topology(self) -> None:
        report = VALID_REPORT.replace("dual_axis_parallel", "parallel_maybe")
        self.assertGreater(self._validate(report), 0)

    def test_rejects_terminal_verdict_with_failed_axis(self) -> None:
        report = VALID_REPORT.replace("| Spec | PASS | 0 | none |", "| Spec | FAIL | 1 | SPEC-001 |")
        self.assertGreater(self._validate(report), 0)

    def test_rejects_plain_pass_with_warning_axis(self) -> None:
        report = VALID_REPORT.replace("PASS_WITH_WARNINGS", "PASS")
        self.assertGreater(self._validate(report), 0)

    def test_rejects_inline_mode_with_parallel_topology(self) -> None:
        report = VALID_REPORT.replace("independent_task", "inline_main_agent")
        self.assertGreater(self._validate(report), 0)

    def test_rejects_verdict_value_without_verdict_heading(self) -> None:
        report = VALID_REPORT.replace("## Verdict\n\nPASS_WITH_WARNINGS", "Verdict: PASS_WITH_WARNINGS")
        self.assertGreater(self._validate(report), 0)


class ReviewBaselineContractTest(unittest.TestCase):
    def _validate(self, content: str) -> int:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            feature_dir = root / ".autobizdevops" / "features" / "demo"
            feature_dir.mkdir(parents=True)
            (feature_dir / "review-baseline.json").write_text(content, encoding="utf-8")
            with redirect_stdout(StringIO()):
                return validate_review_baseline_contract(
                    HookContext(skill="autodev-code", slug="demo", root=root)
                )

    def test_accepts_valid_baseline(self) -> None:
        content = """{
          "schema_version": "autobizdevops.review-baseline.v1",
          "repositories": [{
            "id": "backend",
            "path": "/tmp/backend",
            "base_sha": "0123456789abcdef0123456789abcdef01234567",
            "initial_status": [],
            "initial_dirty_paths": [],
            "initial_untracked_paths": [],
            "scope_confidence": "full"
          }]
        }"""
        self.assertEqual(self._validate(content), 0)

    def test_rejects_relative_repository_path(self) -> None:
        content = """{
          "schema_version": "autobizdevops.review-baseline.v1",
          "repositories": [{
            "id": "backend",
            "path": "../backend",
            "base_sha": "0123456789abcdef0123456789abcdef01234567",
            "initial_status": [],
            "initial_dirty_paths": [],
            "initial_untracked_paths": [],
            "scope_confidence": "full"
          }]
        }"""
        self.assertGreater(self._validate(content), 0)

    def test_rejects_noncanonical_sha_length(self) -> None:
        content = """{
          "schema_version": "autobizdevops.review-baseline.v1",
          "repositories": [{
            "id": "backend",
            "path": "/tmp/backend",
            "base_sha": "0123456789abcdef0123456789abcdef012345678",
            "initial_status": [],
            "initial_dirty_paths": [],
            "initial_untracked_paths": [],
            "scope_confidence": "full"
          }]
        }"""
        self.assertGreater(self._validate(content), 0)


if __name__ == "__main__":
    unittest.main()
