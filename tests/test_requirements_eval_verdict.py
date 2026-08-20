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
    requirements_eval_baseline_rows,
    requirements_eval_has_blockers,
    requirements_eval_has_warnings,
    requirements_eval_verdict,
    validate_requirements_eval_verdict,
)

BASELINE_ROW = "| specs/order-export/spec.md / REQ-001 | 移除 | `specs/order-export/spec.md` |"
BASELINE_TEMPLATE_ROWS = "\n".join(
    [
        "| Requirement | 预期形态 | 基准来源 |",
        "|---|---|---|",
        "| specs/[capability]/spec.md / Requirement / Scenario | 新增 / 修改 / 移除 / 无代码改动 | `specs/...` |",
    ]
)


def eval_report(
    verdict: str,
    blockers: str = "- none",
    baseline: str = BASELINE_ROW,
    warnings: str = "- none",
) -> str:
    return "\n".join(
        [
            "# Requirements Evaluation",
            "",
            "## Verdict",
            "",
            verdict,
            "",
            "## Review Baseline",
            "",
            baseline,
            "",
            "## Blockers",
            "",
            blockers,
            "",
            "## Warnings",
            "",
            warnings,
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

    def test_distinguishes_empty_and_real_warnings(self) -> None:
        for warnings in ("- none", "- 无", "- 暂无"):
            with self.subTest(warnings=warnings):
                self.assertFalse(requirements_eval_has_warnings(eval_report("PASS", warnings=warnings)))
        self.assertTrue(
            requirements_eval_has_warnings(
                eval_report("PASS_WITH_WARNINGS", warnings="- ID: W-001\n  风险: 缺少浏览器兼容性验证")
            )
        )

    def test_counts_only_real_baseline_rows(self) -> None:
        self.assertEqual(requirements_eval_baseline_rows(eval_report("PASS")), 1)
        self.assertEqual(
            requirements_eval_baseline_rows(eval_report("PASS", baseline=BASELINE_TEMPLATE_ROWS)),
            0,
        )
        self.assertEqual(requirements_eval_baseline_rows("# Requirements Evaluation\n"), 0)


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

    def test_accepts_terminal_verdicts_matching_finding_sections(self) -> None:
        failures, output = self.validate(eval_report("PASS"))
        self.assertEqual(failures, 0, output)

        failures, output = self.validate(
            eval_report(
                "PASS_WITH_WARNINGS",
                warnings="- ID: W-001\n  风险: 缺少浏览器兼容性验证",
            )
        )
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

    def test_rejects_pass_without_declared_baseline(self) -> None:
        """基准没写就 PASS，说明 reviewer 没先定预期形态就去读代码了。"""
        failures, output = self.validate(eval_report("PASS", baseline=BASELINE_TEMPLATE_ROWS))
        self.assertGreater(failures, 0)
        self.assertIn("missing_requirements_eval_baseline", output)

    def test_rejects_terminal_verdict_warning_mismatch(self) -> None:
        failures, output = self.validate(
            eval_report("PASS", warnings="- ID: W-001\n  风险: 缺少浏览器兼容性验证")
        )
        self.assertGreater(failures, 0)
        self.assertIn("warning_with_plain_pass_requirements_eval_verdict", output)

        failures, output = self.validate(eval_report("PASS_WITH_WARNINGS"))
        self.assertGreater(failures, 0)
        self.assertIn("missing_warning_for_pass_with_warnings_verdict", output)

    def test_requires_external_interface_coverage_for_feature_prd(self) -> None:
        (self.feature_dir / "PRD.md").write_text(
            """# 需求正式稿

## 外部资料与实现约束

| ID | 类型 | 名称 | 地址/路径 | 约束范围 | 必读阶段 | 状态 |
|---|---|---|---|---|---|---|
| SRC-001 | 外部接口 | 支付 API | https://example.test/openapi | REQ-001 | Specs、Plan、Code、Reviewer、E2E | 可访问 |
""",
            encoding="utf-8",
        )

        failures, output = self.validate(eval_report("PASS"))
        self.assertGreater(failures, 0)
        self.assertIn("missing_requirements_eval_external_interface_section", output)

        incomplete_report = eval_report("PASS") + """## External Interface Coverage

| Source ID | Source Contract Evidence | Design | Implementation | Verification | Status |
|---|---|---|---|---|---|
| SRC-001 | POST /payments | API-001 | src/payment.py | 无 | covered |
"""
        failures, output = self.validate(incomplete_report)
        self.assertGreater(failures, 0)
        self.assertIn("incomplete_requirements_eval_external_interface_coverage", output)

        report = eval_report("PASS") + """## External Interface Coverage

| Source ID | Source Contract Evidence | Design | Implementation | Verification | Status |
|---|---|---|---|---|---|
| SRC-001 | POST /payments | API-001 | src/payment.py | gateway integration test | covered |
"""
        failures, output = self.validate(report)
        self.assertEqual(failures, 0, output)


if __name__ == "__main__":
    unittest.main()
