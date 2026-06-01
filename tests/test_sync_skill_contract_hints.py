from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import ArtifactSpec, SkillContract  # noqa: E402
from hooks.inspect_skill_contract import contract_to_dict  # noqa: E402
from hooks.sync_skill_contract_hints import HINT_END_MARKER, sync_skill_content  # noqa: E402


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


class SyncSkillContractHintsTests(unittest.TestCase):
    def test_inserts_static_runtime_contract_hint_without_dynamic_summary(self) -> None:
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

        compiled = sync_skill_content(content, contract)

        self.assertIn("<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->", compiled)
        self.assertIn("FEATURE_DIR = {PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}", compiled)
        self.assertNotIn("工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/", compiled)
        self.assertIn('python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-sample --json', compiled)
        self.assertIn("# Body", compiled)
        self.assertNotIn("AUTOBIZDEVOPS_CONTRACT", compiled)
        self.assertNotIn("AUTOBIZDEVOPS_ARTIFACT_RULES", compiled)
        self.assertNotIn("- `PRD.md`：PRD文档（必需）", compiled)
        self.assertNotIn("- `REPORT.md`：报告（必需）", compiled)

    def test_recompiling_removes_legacy_contract_and_artifact_rules(self) -> None:
        first = make_contract(
            outputs=(ArtifactSpec(id="report", label="报告", path="REPORT.md", required=True),)
        )
        content = """---
name: autodev-sample
---

<!-- AUTOBIZDEVOPS_CONTRACT:BEGIN -->
## 流程契约（由 board_config.json 生成）

### 输入产物
- `PRD.md`：PRD文档（必需）
<!-- AUTOBIZDEVOPS_CONTRACT:END -->

# Body

<!-- AUTOBIZDEVOPS_ARTIFACT_RULES:BEGIN -->
old generated rules
<!-- AUTOBIZDEVOPS_ARTIFACT_RULES:END -->
"""

        compiled = sync_skill_content(content, first)

        self.assertNotIn("AUTOBIZDEVOPS_CONTRACT", compiled)
        self.assertNotIn("AUTOBIZDEVOPS_ARTIFACT_RULES", compiled)
        self.assertTrue(compiled.index(HINT_END_MARKER) < compiled.index("# Body"))
        self.assertEqual(compiled, sync_skill_content(compiled, first))

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
