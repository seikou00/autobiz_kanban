from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

from artifact_check import (  # noqa: E402
    HookContext,
    requirements_eval_has_blockers,
    requirements_eval_verdict,
    validate_requirements_eval_verdict,
)


def eval_report(verdict: str, blockers: str = "- none") -> str:
    return "\n".join(
        [
            "# Requirements Evaluation",
            "",
            "## Verdict",
            "",
            verdict,
            "",
            "## Blockers",
            "",
            blockers,
            "",
            "## Warnings",
            "",
            "- none",
            "",
        ]
    )


class RequirementsEvalVerdictTest(unittest.TestCase):
    def test_reads_supported_verdicts(self) -> None:
        for verdict in ("PASS", "PASS_WITH_WARNINGS", "FAIL", "DEGRADED"):
            with self.subTest(verdict=verdict):
                self.assertEqual(requirements_eval_verdict(eval_report(verdict)), verdict)

    def test_rejects_placeholder_or_missing_verdict(self) -> None:
        self.assertIsNone(
            requirements_eval_verdict("## Verdict\n\nPASS | PASS_WITH_WARNINGS | FAIL | DEGRADED\n")
        )
        self.assertIsNone(requirements_eval_verdict("# Requirements Evaluation\n"))

    def test_distinguishes_empty_and_real_blockers(self) -> None:
        for blockers in ("- none", "- 无", "- 暂无"):
            with self.subTest(blockers=blockers):
                self.assertFalse(requirements_eval_has_blockers(eval_report("PASS", blockers)))
        self.assertTrue(requirements_eval_has_blockers(eval_report("PASS", "- 订单金额精度未校验")))


class RequirementsEvalGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "demo"
        self.feature_dir = self.project / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        self.ctx = HookContext(skill="autodev-reviewer", slug="alpha", root=self.project)

    def validate(self, report: Optional[str]) -> Tuple[int, str]:
        if report is not None:
            (self.feature_dir / "REQUIREMENTS_EVAL.md").write_text(report, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            failures = validate_requirements_eval_verdict(self.ctx)
        return failures, output.getvalue()

    def test_accepts_terminal_report_without_blockers(self) -> None:
        for verdict in ("PASS", "PASS_WITH_WARNINGS"):
            with self.subTest(verdict=verdict):
                failures, output = self.validate(eval_report(verdict))
                self.assertEqual(failures, 0, output)

    def test_rejects_missing_non_terminal_and_blocked_reports(self) -> None:
        failures, output = self.validate(None)
        self.assertGreater(failures, 0)
        self.assertIn("missing_requirements_eval", output)

        failures, output = self.validate(eval_report("FAIL"))
        self.assertGreater(failures, 0)
        self.assertIn("non_terminal_requirements_eval_verdict", output)

        failures, output = self.validate(eval_report("PASS", "- 登录流程仍然失败"))
        self.assertGreater(failures, 0)
        self.assertIn("blocker_with_pass_requirements_eval_verdict", output)


if __name__ == "__main__":
    unittest.main()
