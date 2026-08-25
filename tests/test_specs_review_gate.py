"""dev.specs 的回检必须落盘成产物，否则协议写得再严也没有门。

trace 里的失败形状：`render_review_protocol.py` 渲染了完整的分类表与产出义务，
critic 也确实报出了遗漏，但【回检结论】一条都没输出，
`update_checkpoint.py --checkpoint specs_done` 照样成功——因为 dev.specs 的
outputs 只有 proposal.md 与 specs/**/*.md，回检没有校验对象。

形状照抄 `validate_requirements_eval_verdict`（dev.review 早就是这么做的）：
产物缺失 fail、verdict 非终态 fail、baseline 不全 fail，外加一条交叉校验——
必查项标了「发现问题」却一条 Finding 都没有，就是自相矛盾的 PASS。
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
    SPECS_REVIEW_BASELINE_ITEMS,
    HookContext,
    review_verdict,
    specs_review_baseline,
    specs_review_findings,
    validate_specs_review_verdict,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.render_review_protocol import render  # noqa: E402


BASELINE_EVIDENCE = {
    "需求覆盖": "PRD F1-F11 对到 REQ-001..REQ-018",
    "实现范围符合性": "backend_only；逐条 SCN 无页面表述",
    "操作分类与代码事实": "git grep dcpa 无既有入口",
    "上游资料引用": "SRC-001..003 均落在 REQ-004",
    "待确认项消解": "Open Questions 三行 Status=已确认",
}

FINDING_ROW = (
    "| F-001 | critic-autodev | Major | 报表导出未写字段口径 | "
    "specs/export/spec.md:31 | 产物可修 | 已补 SCN-012 |"
)


def review_text(
    verdict: str = "PASS_WITH_WARNINGS",
    results: dict[str, str] | None = None,
    evidence: dict[str, str] | None = None,
    findings: list[str] | None = None,
    unresolved: str = "无",
    baseline_items: tuple[str, ...] = SPECS_REVIEW_BASELINE_ITEMS,
) -> str:
    results = results or {}
    evidence = {**BASELINE_EVIDENCE, **(evidence or {})}
    rows = "\n".join(
        f"| {item} | {results.get(item, '通过')} | {evidence.get(item, '证据')} |"
        for item in baseline_items
    )
    findings_body = "\n".join(
        ["| ID | 来源 | 原文严重度 | 结论 | 证据 | 分类 | 处置 |", "|----|----|----|----|----|----|----|"]
        + list(findings)
    ) if findings else "无"
    return (
        "# Specs Review\n\n"
        f"## Verdict\n\n{verdict}\n\n"
        f"## Review Baseline\n\n"
        "| 必查项 | 结论 | 证据 |\n|--------|------|------|\n"
        f"{rows}\n\n"
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

    def test_complete_review_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text())
            failures, output = self._run(project)
            self.assertEqual(failures, 0, output)

    def test_missing_artifact_is_blocked(self) -> None:
        """这就是 trace 的状态：回检跑过了，什么都没落盘。"""
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

    def test_incomplete_baseline_is_blocked(self) -> None:
        """必查项少一项，就等于那一项没查——遗漏必须以失败的形式出现。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(
                feature_dir,
                review_text(baseline_items=("需求覆盖", "上游资料引用")),
            )
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("specs_review_baseline_incomplete", output)
            self.assertIn("实现范围符合性", output)
            self.assertIn("操作分类与代码事实", output)

    def test_baseline_result_outside_the_closed_set_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text(results={"需求覆盖": "基本通过"}))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("specs_review_baseline_invalid_result", output)

    def test_pass_without_evidence_is_blocked(self) -> None:
        """空证据的「通过」是自证，不构成回检。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text(evidence={"操作分类与代码事实": ""}))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("specs_review_baseline_missing_evidence", output)
            self.assertIn("操作分类与代码事实", output)

    def test_flagged_item_without_any_finding_is_blocked(self) -> None:
        """`blocker_with_pass_requirements_eval_verdict` 的同构检查。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(feature_dir, review_text(results={"实现范围符合性": "发现问题"}))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("specs_review_baseline_finding_mismatch", output)

    def test_flagged_item_with_a_finding_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(
                feature_dir,
                review_text(results={"实现范围符合性": "发现问题"}, findings=[FINDING_ROW]),
            )
            failures, output = self._run(project)
            self.assertEqual(failures, 0, output)

    def test_major_finding_without_disposition_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            row = "| F-001 | critic-autodev | Major | 漏了导出口径 | spec.md:31 |  |  |"
            self._write(feature_dir, review_text(findings=[row]))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("specs_review_finding_missing_disposition", output)

    def test_minor_finding_without_disposition_is_allowed(self) -> None:
        """协议只要求 Critical / Major 逐条落分类，Minor 不强制。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            row = "| F-002 | critic-autodev | Minor | 措辞不统一 | spec.md:12 |  |  |"
            self._write(feature_dir, review_text(findings=[row]))
            failures, output = self._run(project)
            self.assertEqual(failures, 0, output)

    def test_invented_category_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            row = "| F-001 | critic-autodev | Critical | 漏了 | spec.md:1 | 已知悉 | 下轮再说 |"
            self._write(feature_dir, review_text(findings=[row]))
            failures, output = self._run(project)
            self.assertGreaterEqual(failures, 1)
            self.assertIn("specs_review_finding_invalid_category", output)

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

    def test_evidence_may_cite_ids_and_links(self) -> None:
        """证据列本来就会写 `[SCN-012]` 和 Markdown 链接；粗判方括号会把真实行丢掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp)
            self._write(
                feature_dir,
                review_text(
                    evidence={
                        "需求覆盖": "PRD F1 落在 [SCN-012]，详见 [spec](specs/x/spec.md)",
                    }
                ),
            )
            failures, output = self._run(project)
            self.assertEqual(failures, 0, output)

    def test_template_placeholders_do_not_count_as_content(self) -> None:
        """模板原样交上来等于没写：占位行不计入 baseline，也不算 Finding。"""
        template = (
            ROOT / "skills" / "autodev" / "autodev-specs" / "templates" / "specs-review.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(specs_review_baseline(template), {})
        self.assertEqual(specs_review_findings(template), [])
        self.assertIsNone(review_verdict(template))


class SpecsReviewWiringTest(unittest.TestCase):
    """校验器只进 VALIDATORS 不进 board_config，等于写了一段永不执行的死代码。"""

    def _specs_node(self) -> dict:
        config = json.loads(
            (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        )
        return next(
            node for node in config["workflow"]["nodes"] if node.get("id") == "dev.specs"
        )

    def test_validator_is_registered_on_dev_specs(self) -> None:
        self.assertIn("specs_review_verdict", self._specs_node()["validators"])

    def test_review_artifact_is_a_required_output(self) -> None:
        outputs = self._specs_node()["artifacts"]["outputs"]
        review = next(item for item in outputs if item["path"] == "SPECS_REVIEW.md")
        self.assertTrue(review["required"])

    def test_protocol_tells_the_stage_to_persist_its_conclusions(self) -> None:
        output = render("dev.specs")

        self.assertIn("SPECS_REVIEW.md", output)
        for section in ("## Verdict", "## Review Baseline", "## Findings", "## Unresolved"):
            self.assertIn(section, output)

    def test_protocol_baseline_items_match_the_validator_closed_set(self) -> None:
        """协议定义必查什么，校验器判定查没查——两边错开就等于没门。"""
        output = render("dev.specs")

        for item in SPECS_REVIEW_BASELINE_ITEMS:
            self.assertIn(item, output)

    def test_protocol_fixes_the_critic_input_materials(self) -> None:
        output = render("dev.specs")

        for material in (
            "PRD.md",
            "proposal.md",
            "specs/**/*.md",
            "IMPLEMENTATION_SCOPE.json",
            "source-context.json",
            "**Existing:**",
        ):
            self.assertIn(material, output)


if __name__ == "__main__":
    unittest.main()
