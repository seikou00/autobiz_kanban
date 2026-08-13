"""blocker 与 PASS 类结论不得并存，这条必须由 dev.review 的阶段门判定。

reviewer 只被告知「不存在 blocker 时才能 PASS」（reviewer-agent.md 的评分段），
但那是写给模型的措辞，不构成门。真正的判定点是 `requirements_eval_in_progress ->
requirements_eval_done` 上跑的 autodev-reviewer postcheck —— 也就是 SKILL 第 5 步
那条 `update_checkpoint.py --checkpoint requirements_eval_done`。

本文件钉的是：REVIEW_FINDINGS.json 里挂着 severity=blocker 的发现项、或者
REQUIREMENTS_EVAL.md 的 `## Blockers` 段列了条目时，两份产物的结论都不能写成
PASS / PASS_WITH_WARNINGS。
"""

from __future__ import annotations

import contextlib
import io
import json
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
    requirements_eval_has_blockers,
    requirements_eval_verdict,
    validate_review_findings_json,
)


FINDINGS_REASON = "blocker_with_pass_review_findings_verdict"
EVAL_REASON = "blocker_with_pass_requirements_eval_verdict"


def eval_report(verdict: str, blockers: str = "- none") -> str:
    return "\n".join(
        [
            "# Requirements Evaluation",
            "",
            "## Review Mode",
            "",
            "independent_task",
            "",
            "## Verdict",
            "",
            verdict,
            "",
            "## Summary",
            "",
            "实现覆盖 specs 中的全部 Requirement。",
            "",
            "## Blockers",
            "",
            blockers,
            "",
            "## Warnings",
            "",
            "- backend: 日志缺少 traceId",
            "",
            "## Required Next Action",
            "",
            "- 进入 `/autodev-utest`。",
            "",
        ]
    )


def findings_doc(verdict: str, *severities: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "verdict": verdict,
            "findings": [
                {
                    "id": "FIND-{:03d}".format(index + 1),
                    "taskId": "T001",
                    "specRefs": ["specs/order-export/spec.md#REQ-001"],
                    "evidenceIds": ["ev_0001"],
                    "severity": severity,
                    "message": "订单金额未做精度校验",
                }
                for index, severity in enumerate(severities)
            ],
        },
        ensure_ascii=False,
    )


class RequirementsEvalVerdictTest(unittest.TestCase):
    def test_reads_verdict_section(self) -> None:
        for verdict in ("PASS", "PASS_WITH_WARNINGS", "FAIL", "DEGRADED"):
            with self.subTest(verdict=verdict):
                self.assertEqual(requirements_eval_verdict(eval_report(verdict)), verdict)

    def test_tolerates_markup_around_the_verdict(self) -> None:
        self.assertEqual(requirements_eval_verdict("## Verdict\n\n**FAIL** — 存在 blocker\n"), "FAIL")

    def test_unfilled_template_line_is_undecidable(self) -> None:
        # 模板占位行同时列四个 verdict，判不出结论就不该据此拦。
        self.assertIsNone(
            requirements_eval_verdict("## Verdict\n\nPASS | PASS_WITH_WARNINGS | FAIL | DEGRADED\n")
        )

    def test_missing_section_returns_none(self) -> None:
        self.assertIsNone(requirements_eval_verdict("# Requirements Evaluation\n\n## Summary\n\n还行\n"))


class RequirementsEvalBlockersTest(unittest.TestCase):
    def test_none_markers_are_not_blockers(self) -> None:
        for blockers in ("- none", "- None", "- none（详见 Warnings）", "- 无", "- 暂无", "- 没有 blocker 时写 none。"):
            with self.subTest(blockers=blockers):
                self.assertFalse(requirements_eval_has_blockers(eval_report("PASS", blockers)))

    def test_real_entries_are_blockers(self) -> None:
        for blockers in (
            "- backend: 订单金额未做精度校验",
            "* cross-repo: 前后端字段名不一致",
            "1. frontend: 提交按钮未接接口",
            "- 无法保存订单，金额字段被截断",
        ):
            with self.subTest(blockers=blockers):
                self.assertTrue(requirements_eval_has_blockers(eval_report("PASS", blockers)))

    def test_warnings_section_is_not_counted(self) -> None:
        self.assertFalse(requirements_eval_has_blockers(eval_report("PASS_WITH_WARNINGS")))

    def test_merged_blockers_warnings_heading_is_not_parsed(self) -> None:
        # `## Blockers / Warnings` 合并写法下无法区分两类条目，宁可不判也不误拦。
        content = "## Verdict\n\nPASS\n\n## Blockers / Warnings\n\n- backend: 日志缺少 traceId\n"
        self.assertFalse(requirements_eval_has_blockers(content))


class ReviewFindingsVerdictGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "demo"
        self.feature_dir = self.project / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)

    def write(self, *, findings: str | None = None, report: str | None = None) -> None:
        if findings is not None:
            (self.feature_dir / "REVIEW_FINDINGS.json").write_text(findings, encoding="utf-8")
        if report is not None:
            (self.feature_dir / "REQUIREMENTS_EVAL.md").write_text(report, encoding="utf-8")

    def run_validator(self, *, required: bool = True) -> tuple[int, str]:
        ctx = HookContext(
            skill="autodev-reviewer",
            slug="alpha",
            root=self.project,
            required_outputs=("REVIEW_FINDINGS.json",) if required else (),
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failures = validate_review_findings_json(ctx)
        return failures, buffer.getvalue()

    def test_blocker_finding_blocks_both_pass_verdicts(self) -> None:
        self.write(findings=findings_doc("PASS", "blocker"), report=eval_report("PASS"))
        failures, output = self.run_validator()
        self.assertGreaterEqual(failures, 2)
        self.assertIn(FINDINGS_REASON, output)
        self.assertIn(EVAL_REASON, output)
        self.assertIn("FIND-001", output)

    def test_blocker_finding_blocks_pass_with_warnings(self) -> None:
        self.write(
            findings=findings_doc("PASS_WITH_WARNINGS", "blocker"),
            report=eval_report("PASS_WITH_WARNINGS"),
        )
        _, output = self.run_validator()
        self.assertIn(FINDINGS_REASON, output)
        self.assertIn(EVAL_REASON, output)

    def test_markdown_blockers_alone_block_pass(self) -> None:
        # 发现项还没记进 REVIEW_FINDINGS.json，但报告里已经写了 blocker。
        self.write(
            findings=findings_doc("PASS"),
            report=eval_report("PASS", "- backend: 订单金额未做精度校验"),
        )
        _, output = self.run_validator()
        self.assertIn(FINDINGS_REASON, output)
        self.assertIn(EVAL_REASON, output)
        self.assertIn("REQUIREMENTS_EVAL.md#Blockers", output)

    def test_blocker_with_non_pass_findings_verdict_only_flags_the_report(self) -> None:
        # json 侧已经改成 FAIL，报告却还留着 PASS —— 只该报报告那一条。
        self.write(findings=findings_doc("FAIL", "blocker"), report=eval_report("PASS"))
        _, output = self.run_validator(required=False)
        self.assertNotIn(FINDINGS_REASON, output)
        self.assertIn(EVAL_REASON, output)

    def test_blocker_with_fail_verdicts_passes_the_consistency_check(self) -> None:
        self.write(
            findings=findings_doc("FAIL", "blocker"),
            report=eval_report("FAIL", "- backend: 订单金额未做精度校验"),
        )
        _, output = self.run_validator(required=False)
        self.assertNotIn(FINDINGS_REASON, output)
        self.assertNotIn(EVAL_REASON, output)

    def test_blocker_cannot_close_the_stage_in_either_direction(self) -> None:
        """有 blocker 就关不掉阶段：PASS 被新检查拦，非 PASS 被 non_terminal 拦。"""
        self.write(findings=findings_doc("FAIL", "blocker"), report=eval_report("FAIL"))
        failures, output = self.run_validator()
        self.assertGreaterEqual(failures, 1)
        self.assertIn("non_terminal_review_findings_verdict", output)

    def test_non_blocker_severities_do_not_trip_the_gate(self) -> None:
        self.write(findings=findings_doc("PASS", "important", "minor"), report=eval_report("PASS"))
        _, output = self.run_validator()
        self.assertNotIn(FINDINGS_REASON, output)
        self.assertNotIn(EVAL_REASON, output)

    def test_clean_review_passes(self) -> None:
        self.write(findings=findings_doc("PASS"), report=eval_report("PASS"))
        failures, output = self.run_validator()
        self.assertEqual(failures, 0, output)

    def test_missing_report_leaves_the_findings_check_intact(self) -> None:
        # REQUIREMENTS_EVAL.md 在 dev.review 的产物契约里是 required=false，缺了不补报。
        self.write(findings=findings_doc("PASS", "blocker"))
        _, output = self.run_validator()
        self.assertIn(FINDINGS_REASON, output)
        self.assertNotIn(EVAL_REASON, output)


if __name__ == "__main__":
    unittest.main()
