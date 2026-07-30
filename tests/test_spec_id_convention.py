"""spec 稳定 ID 的两种标题写法必须被同一套索引识别。

合并 dev_0803 与 dev_workflow_py 后，仓库里同时存在两种 spec 标题写法：

    ### Requirement [REQ-001]:            #### Scenario [SCN-001]:
    ### REQ-<capability>-001:             #### SCN-<capability>-001-01:

`autodev-specs/SKILL.md` 与 specs 模板教的是后者，而下游 JSON 校验器
（review / utest / e2e / verify）的 scenario 索引一度只识别前者。索引取不到
ID 时 `defined_scenarios` 为空集，`missing_scenario_coverage_rows` 用
`defined_scenarios - seen_scenarios` 判完整性，差集恒空——覆盖门被真空满足，
零覆盖的 VERIFY_DECISION 能一路放行到 verify_done。

本文件把「两种写法都要能索引」和「零覆盖必须被拦」钉死。
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
    HookContext,
    _spec_scenario_refs_by_path,
    collect_spec_definition_index,
)
from hooks.evidence_store import append_evidence  # noqa: E402
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.update_checkpoint import prepare_checkpoint_update  # noqa: E402

BRACKETED_SPEC = """## ADDED Requirements

### Requirement [REQ-001]: 创建导出任务

系统 SHALL 支持创建导出任务。

#### Scenario [SCN-001]: 创建成功

当用户提交导出请求时，系统 SHALL 返回任务号。

#### Scenario [SCN-002]: 参数非法

当参数非法时，系统 SHALL 返回 400。
"""

PLAIN_SPEC = """Capability-ID: CAP-order-export

## ADDED Requirements

### REQ-order-export-001: 创建导出任务

系统 SHALL 支持创建导出任务。

#### SCN-order-export-001-01: 创建成功

当用户提交导出请求时，系统 SHALL 返回任务号。

#### SCN-order-export-001-02: 参数非法

当参数非法时，系统 SHALL 返回 400。
"""


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

    def test_both_heading_styles_are_indexed(self) -> None:
        cases = (
            (BRACKETED_SPEC, {"REQ-001"}, {"SCN-001", "SCN-002"}),
            (
                PLAIN_SPEC,
                {"REQ-order-export-001"},
                {"SCN-order-export-001-01", "SCN-order-export-001-02"},
            ),
        )
        for spec_body, want_req, want_scn in cases:
            with tempfile.TemporaryDirectory() as tmp:
                project, _ = self._feature(tmp, spec_body)
                ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
                index, failures = collect_spec_definition_index(ctx)
                self.assertEqual(failures, 0)
                self.assertEqual(index["REQ"], want_req)
                self.assertEqual(index["SCN"], want_scn)

    def test_both_heading_styles_resolve_path_qualified_refs(self) -> None:
        cases = (
            (BRACKETED_SPEC, "SCN-001"),
            (PLAIN_SPEC, "SCN-order-export-001-01"),
        )
        for spec_body, scenario_id in cases:
            with tempfile.TemporaryDirectory() as tmp:
                project, _ = self._feature(tmp, spec_body)
                ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
                refs = _spec_scenario_refs_by_path(ctx)
                self.assertIn(scenario_id, refs)
                self.assertEqual(
                    refs[scenario_id],
                    {f"specs/order-export/spec.md#{scenario_id}"},
                )

    def test_zero_coverage_verify_decision_is_blocked_for_both_styles(self) -> None:
        """覆盖门不得因标题写法不同而被真空满足。"""
        for spec_body in (BRACKETED_SPEC, PLAIN_SPEC):
            with tempfile.TemporaryDirectory() as tmp:
                project, feature_dir = self._feature(tmp, spec_body)
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
