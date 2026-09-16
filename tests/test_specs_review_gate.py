"""The legacy review validator remains usable by explicitly configured workflows."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

from artifact_check import (  # noqa: E402
    HookContext,
    validate_specs_review_verdict,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402


FINDING_ROW = (
    "| RV-20260830T101500Z-ab12cd34-F001 | Major | 报表导出未写字段口径 | "
    "specs/export/spec.md:31 | 已补 SCN-012 |"
)


def review_text(
    verdict: str = "PASS_WITH_WARNINGS",
    findings: list[str] | None = None,
    unresolved: str = "无",
) -> str:
    findings_body = "\n".join(
        ["| ID | 严重度 | 结论 | 证据 | 处置 |", "|----|----|----|----|----|"] + list(findings)
    ) if findings else "无"
    return (
        "# Specs Review\n\n"
        f"## Verdict\n\n{verdict}\n\n"
        f"## Findings\n\n{findings_body}\n\n"
        f"## Unresolved\n\n{unresolved}\n"
    )


class SpecsReviewGateTest(unittest.TestCase):
    def _feature(self, tmp: str) -> tuple[Path, Path]:
        project = Path(tmp).resolve() / "demo"
        project.mkdir()
        init_workspace(project)
        create_feature(project, "alpha")
        return project, project / ".autobizdevops" / "features" / "alpha"

    def _run(self, project: Path) -> tuple[int, str]:
        ctx = HookContext(skill="autodev-specs", slug="alpha", root=project)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failures = validate_specs_review_verdict(ctx)
        return failures, buffer.getvalue()

    def _write(self, feature_dir: Path, text: str) -> None:
        (feature_dir / "SPECS_REVIEW.md").write_text(text, encoding="utf-8")

    def test_three_sections_are_enough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text())
            failures, output = self._run(project)
            self.assertEqual(failures, 0, output)

    def test_missing_artifact_is_blocked(self) -> None:
        """回检跑过了，什么都没落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp)
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("missing_specs_review", output)

    def test_non_terminal_verdict_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text(verdict="FAIL"))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("non_terminal_specs_review_verdict", output)

    def test_ambiguous_verdict_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text(verdict="PASS 但 FAIL"))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("invalid_specs_review_verdict", output)

    def test_missing_findings_section_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(
                feature_dir,
                "# Specs Review\n\n## Verdict\n\nPASS\n\n## Unresolved\n\n无\n",
            )
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("missing_specs_review_findings", output)

    def test_missing_unresolved_section_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, "# Specs Review\n\n## Verdict\n\nPASS\n\n## Findings\n\n无\n")
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("missing_specs_review_unresolved", output)

    def test_unresolved_entry_blocks_the_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(
                feature_dir,
                review_text(unresolved="- F-003 报表导出口径需用户在两方案间取舍"),
            )
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("unresolved_specs_review_finding", output)

    def test_finding_wording_is_not_machine_judged(self) -> None:
        """严重度、分类、处置措辞都交给 critic 与主模型，机器不设闭集。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            row = "| F-001 | 很严重 | 漏了导出口径 | spec.md:31 | 已知悉，下轮处理 |"
            self._write(feature_dir, review_text(findings=[row]))
            failures, output = self._run(project)
            self.assertEqual(failures, 0, output)


if __name__ == "__main__":
    unittest.main()
