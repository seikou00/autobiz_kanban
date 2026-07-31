"""spec 稳定 ID 的唯一写法必须被索引识别，且零覆盖必须被拦。

仓库统一使用括号式稳定 ID：

    ### Requirement [REQ-001]:            #### Scenario [SCN-001]:

历史上 `autodev-specs/SKILL.md` 与 specs 模板一度教 `REQ-<capability>-NNN`
这种带 capability 前缀的写法，而下游 JSON 校验器（review / utest / e2e /
verify）的 scenario 索引只识别括号式。索引取不到 ID 时 `defined_scenarios`
为空集，`missing_scenario_coverage_rows` 用 `defined_scenarios - seen_scenarios`
判完整性，差集恒空——覆盖门被真空满足，零覆盖的 VERIFY_DECISION 能一路放行
到 verify_done。

修复方向是让「技能教的」与「校验器索引的」收敛到同一种写法，因此本文件钉三件事：
括号式能被索引、模板与校验器的正则彼此吻合、零覆盖必须被拦。
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
from hooks.update_checkpoint import prepare_checkpoint_update  # noqa: E402

SPEC_BODY = """## ADDED Requirements

### Requirement [REQ-001]: 创建导出任务

系统 SHALL 支持创建导出任务。

#### Scenario [SCN-001]: 创建成功

当用户提交导出请求时，系统 SHALL 返回任务号。

#### Scenario [SCN-002]: 参数非法

当参数非法时，系统 SHALL 返回 400。
"""

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

    def test_bracketed_headings_are_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp, SPEC_BODY)
            ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
            index, failures = collect_spec_definition_index(ctx)
            self.assertEqual(failures, 0)
            self.assertEqual(index["REQ"], {"REQ-001"})
            self.assertEqual(index["SCN"], {"SCN-001", "SCN-002"})

    def test_bracketed_headings_resolve_path_qualified_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._feature(tmp, SPEC_BODY)
            ctx = HookContext(skill="autodev-verify", slug="alpha", root=project)
            refs = _spec_scenario_refs_by_path(ctx)
            self.assertIn("SCN-001", refs)
            self.assertEqual(
                refs["SCN-001"],
                {"specs/order-export/spec.md#SCN-001"},
            )

    def test_spec_template_matches_indexer_patterns(self) -> None:
        """模板教的写法必须正好是索引器认的写法，否则覆盖门会重新真空。"""
        text = SPEC_TEMPLATE.read_text(encoding="utf-8")
        self.assertTrue(
            SPEC_REQUIREMENT_DEF_RE.search(text),
            f"{SPEC_TEMPLATE} 的 Requirement 标题不被 SPEC_REQUIREMENT_DEF_RE 识别，"
            "修复：模板改用 '### Requirement [REQ-NNN]: <标题>'",
        )
        self.assertTrue(
            SPEC_SCENARIO_DEF_RE.search(text),
            f"{SPEC_TEMPLATE} 的 Scenario 标题不被 SPEC_SCENARIO_DEF_RE 识别，"
            "修复：模板改用 '#### Scenario [SCN-NNN]: <标题>'",
        )

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
