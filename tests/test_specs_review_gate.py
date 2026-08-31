"""dev.specs 的回检必须落盘成产物，否则协议写得再严也没有门。

机器只判三件事：`## Verdict` 是终态、`## Findings` 有内容、`## Unresolved` 已清空。
回检内容本身（需求覆盖、范围、分类事实、来源引用、待确认消解）由 critic 判定：
用正则去核对固定表格和分类措辞，只会推着模型改词，不会提高审查质量。
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
    review_verdict,
    validate_specs_review_verdict,
)
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.render_review_protocol import render  # noqa: E402


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

    def test_untouched_template_cannot_pass(self) -> None:
        template = (
            ROOT / "skills" / "autodev" / "autodev-specs" / "templates" / "specs-review.md"
        ).read_text(encoding="utf-8")
        self.assertIsNone(review_verdict(template))


class SpecsReviewWiringTest(unittest.TestCase):
    """dev.specs 只留四个阻断器；账本与新鲜度是日志，不决定能否进下一阶段。"""

    BLOCKING = [
        "proposal_contract",
        "specs_contract",
        "capability_spec_correspondence",
        "specs_review_verdict",
    ]

    def _specs_node(self) -> dict:
        config = json.loads(
            (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        )
        return next(
            node for node in config["workflow"]["nodes"] if node.get("id") == "dev.specs"
        )

    def test_dev_specs_blocks_on_exactly_four_validators(self) -> None:
        self.assertEqual(self._specs_node()["validators"], self.BLOCKING)

    def test_review_artifact_is_a_required_output(self) -> None:
        outputs = self._specs_node()["artifacts"]["outputs"]
        review = next(item for item in outputs if item["path"] == "SPECS_REVIEW.md")
        self.assertTrue(review["required"])

    def test_protocol_tells_the_stage_to_persist_its_conclusions(self) -> None:
        output = render("dev.specs")

        self.assertIn("SPECS_REVIEW.md", output)
        for section in ("## Verdict", "## Findings", "## Unresolved"):
            self.assertIn(section, output)
        self.assertNotIn("## Review Baseline", output)

    def test_protocol_hands_the_five_review_items_to_critic(self) -> None:
        output = render("dev.specs")

        for item in (
            "需求覆盖",
            "实现范围符合性",
            "操作分类与代码事实",
            "上游资料引用",
            "待确认项消解",
        ):
            self.assertIn(item, output)

    def test_protocol_does_not_force_a_rerun_on_every_edit(self) -> None:
        """critic 提出的问题由主模型收口；只有行为契约变了才重跑回检。"""
        output = render("dev.specs")

        self.assertIn("不必重新调用 critic", output)
        self.assertNotIn("specs_review_state.py", output)

    def test_protocol_fixes_the_critic_input_materials(self) -> None:
        output = render("dev.specs")

        for material in (
            "PRD.md",
            "proposal.md",
            "specs/**/*.md",
            "IMPLEMENTATION_SCOPE.json",
            "source-context.json",
            "现有 specs 与源码",
        ):
            self.assertIn(material, output)


if __name__ == "__main__":
    unittest.main()
