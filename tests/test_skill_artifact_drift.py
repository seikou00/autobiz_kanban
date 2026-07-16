from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import ArtifactSpec, SkillContract  # noqa: E402
from hooks.check_skill_artifact_drift import detect_artifact_drift_in_content  # noqa: E402


def make_contract() -> SkillContract:
    return SkillContract(
        node_id="dev.sample",
        label="示例阶段",
        group="Dev",
        skill="autodev-sample",
        checkpoints=("sample_in_progress", "sample_done"),
        inputs=(ArtifactSpec(id="prd", label="PRD文档", path="PRD.md", required=True),),
        outputs=(ArtifactSpec(id="plan", label="计划", path="PLAN.md", required=True),),
        validators=(),
    )


def detect(content: str) -> list:
    return detect_artifact_drift_in_content(
        content=content,
        contract=make_contract(),
        path=Path("SKILL.md"),
        known_artifact_paths={"PRD.md", "PLAN.md", "VERIFY_REPORT.md", "completion-proposal.json"},
    )


class SkillArtifactDriftTests(unittest.TestCase):
    def test_flags_removed_formal_artifact_in_completion_gate(self) -> None:
        findings = detect(
            """
## 完成条件
- [ ] `{工作目录}/VERIFY_REPORT.md` 已生成
"""
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].artifact, "VERIFY_REPORT.md")

    def test_allows_current_contract_artifacts_in_gate_context(self) -> None:
        findings = detect(
            """
## 完成条件
- [ ] `{工作目录}/PLAN.md` 已生成
"""
        )

        self.assertEqual(findings, [])

    def test_allows_explanatory_or_intermediate_references(self) -> None:
        findings = detect(
            """
## 参考文件
- 历史上可能存在 `{工作目录}/VERIFY_REPORT.md`，这里只作说明。
- reviewer 可使用 `{工作目录}/completion-proposal.json` 作为中间辅助文件。
"""
        )

        self.assertEqual(findings, [])

    def test_spec_template_does_not_emit_stable_id_instruction_section(self) -> None:
        template = (ROOT / "skills" / "autodev" / "autodev-specs" / "templates" / "spec.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("## 稳定 ID 规范", template)
        self.assertIn("### Requirement [REQ-001]:", template)
        self.assertIn("#### Scenario [SCN-001]:", template)

    def test_plan_and_code_skills_document_batch_validation_boundary(self) -> None:
        plan_skill = (ROOT / "skills" / "autodev" / "autodev-plan" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        code_skill = (ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("add-batch-validation-command", plan_skill)
        self.assertIn("TASK 禁止配置 compile/build/typecheck/lint", plan_skill)
        self.assertIn("requiredAction=run_batch_check", code_skill)
        self.assertIn("fix_batch_and_retry_same_run", code_skill)
        self.assertIn("attemptType=batch_revalidation", code_skill)


if __name__ == "__main__":
    unittest.main()
