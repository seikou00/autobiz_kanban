"""spec 稳定 ID 的两种标题写法都必须被索引识别，且零覆盖必须被拦。

以下写法等价：

    ### Requirement REQ-001:              #### Scenario SCN-001:
    ### Requirement [REQ-001]:            #### Scenario [SCN-001]:

历史上 `autodev-specs/SKILL.md` 与 specs 模板一度教 `REQ-<capability>-NNN`
这种带 capability 前缀的写法，而下游 JSON 校验器（review / utest / e2e /
verify）的 scenario 索引曾只识别括号式。索引取不到 ID 时 `defined_scenarios`
为空集，`missing_scenario_coverage_rows` 用 `defined_scenarios - seen_scenarios`
判完整性，差集恒空——覆盖门被真空满足，零覆盖的 VERIFY_DECISION 能一路放行
到 verify_done。

本文件钉三件事：两种写法都能被索引、模板不会把方括号写成硬约束、零覆盖必须被拦。
"""

from __future__ import annotations

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
    SPEC_REQUIREMENT_DEF_RE,
    SPEC_SCENARIO_DEF_RE,
    HookContext,
    _spec_scenario_refs_by_path,
    collect_spec_definition_index,
)
from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.result_writer_common import collect_scenario_ids  # noqa: E402
from hooks.update_checkpoint import prepare_checkpoint_update  # noqa: E402

SPEC_BODY = """## ADDED Requirements

### Requirement REQ-001: 创建导出任务

系统 SHALL 支持创建导出任务。

#### Scenario SCN-001: 创建成功

当用户提交导出请求时，系统 SHALL 返回任务号。

#### Scenario SCN-002: 参数非法

当参数非法时，系统 SHALL 返回 400。
"""

SPEC_BODY_BRACKETED = (
    SPEC_BODY.replace("REQ-001:", "[REQ-001]:")
    .replace("SCN-001:", "[SCN-001]:")
    .replace("SCN-002:", "[SCN-002]:")
)

SPEC_TEMPLATE = ROOT / "skills" / "autodev" / "autodev-specs" / "templates" / "spec.md"


class SpecIdConventionTest(unittest.TestCase):
    def _feature(self, tmp: str, spec_body: str) -> tuple[Path, Path]:
        project = Path(tmp).resolve() / "demo"
        project.mkdir()
        init_workspace(project)
        create_feature(project, "alpha")
        feature_dir = project / ".autobizdevops" / "features" / "alpha"
        spec = feature_dir / "specs" / "order-export" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(spec_body, encoding="utf-8")
        return project, feature_dir

    def test_headings_with_or_without_brackets_are_indexed(self) -> None:
        for body in (SPEC_BODY, SPEC_BODY_BRACKETED):
            with self.subTest(bracketed="[REQ-001]" in body), tempfile.TemporaryDirectory() as tmp:
                project, feature_dir = self._feature(tmp, body)
                ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
                index, failures = collect_spec_definition_index(ctx)
                self.assertEqual(failures, 0)
                self.assertEqual(index["REQ"], {"REQ-001"})
                self.assertEqual(index["SCN"], {"SCN-001", "SCN-002"})
                self.assertEqual(collect_scenario_ids(feature_dir), ["SCN-001", "SCN-002"])

    def test_headings_with_or_without_brackets_resolve_path_qualified_refs(self) -> None:
        for body in (SPEC_BODY, SPEC_BODY_BRACKETED):
            with self.subTest(bracketed="[REQ-001]" in body), tempfile.TemporaryDirectory() as tmp:
                project, _ = self._feature(tmp, body)
                ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
                refs = _spec_scenario_refs_by_path(ctx)
                self.assertEqual(
                    refs["SCN-001"],
                    {"specs/order-export/spec.md#SCN-001"},
                )

    def test_spec_template_uses_non_copyable_id_placeholders(self) -> None:
        """模板保留正式 heading 形状，但不能携带可复制的真实全局 ID。"""
        text = SPEC_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("### Requirement REQ-NNN:", text)
        self.assertIn("#### Scenario SCN-NNN:", text)
        removed = text.split("## REMOVED Requirements", 1)[1]
        self.assertIn("#### Scenario SCN-NNN:", removed)
        self.assertIsNone(SPEC_REQUIREMENT_DEF_RE.search(text))
        self.assertIsNone(SPEC_SCENARIO_DEF_RE.search(text))

    def test_zero_coverage_verify_decision_is_blocked(self) -> None:
        """覆盖门不得被零覆盖的 VERIFY_DECISION 真空满足。"""
        with tempfile.TemporaryDirectory() as tmp:
            project, feature_dir = self._feature(tmp, SPEC_BODY)
            (feature_dir / "proposal.md").write_text("# proposal\n", encoding="utf-8")
            (feature_dir / "design.md").write_text(
                "# design\n\n- D-001: 决策\n", encoding="utf-8"
            )
            (feature_dir / "VERIFY_REPORT.md").write_text("all pass\n", encoding="utf-8")
            record = append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "specRefs": ["specs/order-export/spec.md#REQ-001"],
                    "designRefs": ["design.md#D-001"],
                    "changedFiles": ["src/example.py"],
                    "validation": {
                        "command": "echo ok",
                        "exitCode": 0,
                        "result": "pass",
                    },
                },
            )
            (feature_dir / "VERIFY_DECISION.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "verdict": "pass",
                        "nextCheckpoint": "verify_done",
                        "evidenceIds": [record["evidenceId"]],
                        "scenarioCoverage": [],
                        "passedScenarioRefs": [],
                        "failedScenarioRefs": [],
                        "manualVerificationRefs": [],
                        "missingScenarioRefs": [],
                        "summary": {
                            "total": 0,
                            "passed": 0,
                            "failed": 0,
                            "manual": 0,
                            "missing": 0,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            from board_core.state_store import (
                load_state_json_records,
                write_state_records,
            )

            records, _, _ = load_state_json_records(project)
            current = dict(records["alpha"])
            current["checkpoint"] = "verify_in_progress"
            current["stage"] = "verify_in_progress"
            records["alpha"] = current
            write_state_records(project, records)

            result = prepare_checkpoint_update(
                workspace=project, feature="alpha", checkpoint="verify_done"
            )
            self.assertFalse(
                result.ok,
                "零 scenario 覆盖的 VERIFY_DECISION 不得推进 verify_done",
            )
            joined = " ".join(str(error) for error in (result.errors or ()))
            self.assertIn("missing_scenario_coverage_rows", joined)


if __name__ == "__main__":
    unittest.main()
