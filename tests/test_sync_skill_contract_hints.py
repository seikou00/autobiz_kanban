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
        self.assertIn(
            'python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-sample --feature "{FEATURE_ID}" --json',
            compiled,
        )
        self.assertIn("无 `FEATURE_ID` 时可省略 `--feature` 查看基线契约。", compiled)
        self.assertNotIn("无 `{FEATURE_ID}` 时可省略", compiled)
        self.assertNotIn("无 `$FEATURE_ID` 时可省略", compiled)
        self.assertIn("Source Bundle", compiled)
        self.assertIn("Method Bundle", compiled)
        # Drop semantics: degrade speaks about optional inputs; the external
        # concept and any "ask the user to supply it" path are gone.
        self.assertIn("`required: false` 的输入", compiled)
        self.assertIn("索要", compiled)
        self.assertIn("正式流程产物 input", compiled)
        self.assertIn("内部 route SKILL/deps", compiled)
        self.assertIn("用户本轮直接提供的材料", compiled)
        self.assertNotIn("也不要为其设想任何分支", compiled)
        self.assertNotIn("external: true", compiled)
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

    def test_autodev_code_html_route_is_not_bound_to_source_bundle(self) -> None:
        content = (ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("不要求 Source Bundle 中存在 `frontend_html`", content)
        self.assertIn("用户本轮直接粘贴或提供了可读取的 HTML/DOM 片段", content)
        self.assertNotIn("Source Bundle 中存在 `frontend_html`", content)
        self.assertNotIn("frontend_html.extract.degrade", content)

    def test_autodev_code_html_route_gates_internal_branching_with_write_todos(self) -> None:
        content = (ROOT / "skills" / "autodev" / "autodev-code" / "SKILL.md").read_text(encoding="utf-8")

        route_index = content.index("内部分流：")
        queue_index = content.index("1. 先建立本分支任务队列。")
        branch_index = content.index("2. 判断 HTML 路线并读取对应 SKILL：")

        self.assertLess(route_index, queue_index)
        self.assertLess(queue_index, branch_index)
        self.assertNotIn("> 若进入本分支", content)
        self.assertIn("若当前运行模式支持 `write_todos`，必须先把本分支主线写成可见清单", content)
        self.assertIn("未完成这一步，不得进入后续分流", content)
        self.assertIn("判断 HTML 路线并读取对应 SKILL", content)
        self.assertIn("执行本分支验证，确认已回到 `/autodev-code` 主流程后", content)
        self.assertIn("不得把本分支视为完成", content)


if __name__ == "__main__":
    unittest.main()
