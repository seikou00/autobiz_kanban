from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import ArtifactSpec, SkillContract  # noqa: E402
from hooks.compile_skill_contracts import END_MARKER, compile_skill_content  # noqa: E402
from hooks.render_skill_contract import contract_to_dict  # noqa: E402


def make_contract(
    *,
    inputs: tuple[ArtifactSpec, ...] = (),
    outputs: tuple[ArtifactSpec, ...] = (),
) -> SkillContract:
    return SkillContract(
        node_id="dev.sample",
        label="示例阶段",
        group="Dev",
        skill="autodev-sample",
        checkpoints=("sample_in_progress", "sample_done"),
        inputs=inputs,
        outputs=outputs,
        validators=(),
    )


class CompileSkillContractsTests(unittest.TestCase):
    def test_compiles_only_visible_contract_block_without_final_rules(self) -> None:
        contract = make_contract(
            inputs=(
                ArtifactSpec(id="prd", label="PRD文档", path="PRD.md", required=True),
                ArtifactSpec(id="notes", label="补充说明", path="notes.md", required=False),
            ),
            outputs=(
                ArtifactSpec(id="report", label="报告", path="REPORT.md", required=True),
                ArtifactSpec(id="log", label="运行日志", path="run.log", required=False),
            ),
        )
        content = """---
name: autodev-sample
---

```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```

# Body
旧流程正文。
"""

        compiled = compile_skill_content(content, contract)

        self.assertIn("<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->", compiled)
        self.assertIn("- `PRD.md`：PRD文档（必需）", compiled)
        self.assertIn("- `REPORT.md`：报告（必需）", compiled)
        self.assertIn("# Body", compiled)
        self.assertNotIn("AUTOBIZDEVOPS_ARTIFACT_RULES", compiled)

    def test_recompiling_removes_legacy_artifact_rules_and_is_idempotent(self) -> None:
        first = make_contract(
            outputs=(ArtifactSpec(id="report", label="报告", path="REPORT.md", required=True),)
        )
        content = """---
name: autodev-sample
---

# Body

<!-- AUTOBIZDEVOPS_ARTIFACT_RULES:BEGIN -->
old generated rules
<!-- AUTOBIZDEVOPS_ARTIFACT_RULES:END -->
"""

        compiled = compile_skill_content(content, first)

        self.assertNotIn("AUTOBIZDEVOPS_ARTIFACT_RULES", compiled)
        self.assertTrue(compiled.index(END_MARKER) < compiled.index("# Body"))
        self.assertEqual(compiled, compile_skill_content(compiled, first))

    def test_contract_to_dict_exposes_machine_readable_contract(self) -> None:
        contract = make_contract(
            inputs=(ArtifactSpec(id="prd", label="PRD文档", path="PRD.md", required=True),),
            outputs=(ArtifactSpec(id="report", label="报告", path="REPORT.md", required=False),),
        )

        payload = contract_to_dict(contract)

        self.assertEqual(payload["skill"], "autodev-sample")
        self.assertEqual(payload["required_inputs"], ["PRD.md"])
        self.assertEqual(payload["required_outputs"], [])
        self.assertEqual(payload["inputs"][0]["id"], "prd")
        self.assertFalse(payload["outputs"][0]["required"])


if __name__ == "__main__":
    unittest.main()
