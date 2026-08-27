from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from board_core.state_store import (
    load_state_json_records,
    parse_state_json_records_result,
    write_state_records,
)
from hooks.evidence_store import append_evidence
from hooks.init_workspace import create_feature, init_workspace
from hooks.route_checkpoint import resolve_route
from hooks.update_checkpoint import prepare_checkpoint_update, prepare_skip_update
from inspect_state import _load_board_config, project_mode, run_mode


def _capture_json(callable_, *args) -> dict:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = callable_(*args)
    assert exit_code == 0
    return json.loads(output.getvalue())


class NeedsFixStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "demo"
        self.project.mkdir()
        init_workspace(self.project)
        self.feature = "needs-fix-feature"
        create_feature(self.project, self.feature)
        self.feature_dir = self.project / ".autobizdevops" / "features" / self.feature

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _set_checkpoint(
        self,
        checkpoint: str,
        *,
        needs_fix_from: str | None = None,
        stage: str | None = None,
    ) -> None:
        records, errors, exists = load_state_json_records(self.project)
        self.assertTrue(exists)
        self.assertEqual(errors, [])
        record = dict(records[self.feature])
        record["checkpoint"] = checkpoint
        record["stage"] = stage or ("需要修复" if checkpoint == "needs_fix" else checkpoint)
        if needs_fix_from is None:
            record.pop("needsFixFromCheckpoint", None)
        else:
            record["needsFixFromCheckpoint"] = needs_fix_from
        records[self.feature] = record
        write_state_records(self.project, records)

    def _run_verify_writer(self, *args: str) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "hooks" / "verify_decision_writer.py"),
                *args,
                "--workspace",
                str(self.project),
                "--feature",
                self.feature,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _write_verify_artifacts(self) -> None:
        """dev.verify 以 VERIFY_DECISION.json 与证据流为 required 机器事实源。

        走真实 writer 建立产物，避免手写结构与校验器漂移。
        """
        spec = self.feature_dir / "specs" / "cap" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(
            "## ADDED Requirements\n\n"
            "### Requirement [REQ-001]: capability\n\n"
            "#### Scenario [SCN-001]: happy path\n",
            encoding="utf-8",
        )
        (self.feature_dir / "proposal.md").write_text("# proposal\n", encoding="utf-8")
        (self.feature_dir / "design.md").write_text("# design\n\n- D-001: 决策\n", encoding="utf-8")
        (self.feature_dir / "VERIFY_REPORT.md").write_text("verify failed\n", encoding="utf-8")

        record = append_evidence(
            self.feature_dir,
            {
                "featureId": self.feature,
                "checkpoint": "code_in_progress",
                "nodeId": "dev.code",
                "skill": "autodev-code",
                "taskId": "T001",
                "action": "validation",
                "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                "designRefs": ["design.md#D-001"],
                "changedFiles": ["src/example.py"],
                "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
            },
        )

        self._run_verify_writer("init", "--from-specs")
        self._run_verify_writer("derive-scenario-coverage")
        self._run_verify_writer(
            "update-scenario",
            "--scenario-ref",
            "SCN-001",
            "--verdict",
            "fail",
            "--evidence-id",
            record["evidenceId"],
        )
        self._run_verify_writer("set-verdict", "fail")

    def test_checkpoint_update_persists_and_clears_needs_fix_source(self) -> None:
        self._set_checkpoint("verify_in_progress")
        self._write_verify_artifacts()

        blocked = prepare_checkpoint_update(
            workspace=self.project,
            feature=self.feature,
            checkpoint="needs_fix",
        )

        self.assertTrue(blocked.ok, blocked.errors)
        self.assertEqual(
            blocked.records[self.feature]["needsFixFromCheckpoint"],
            "verify_in_progress",
        )
        write_state_records(self.project, blocked.records)
        reloaded, errors, _ = load_state_json_records(self.project)
        self.assertEqual(errors, [])
        self.assertEqual(
            reloaded[self.feature]["needsFixFromCheckpoint"],
            "verify_in_progress",
        )

        unchanged = prepare_checkpoint_update(
            workspace=self.project,
            feature=self.feature,
            checkpoint="needs_fix",
        )
        self.assertTrue(unchanged.ok, unchanged.errors)
        self.assertEqual(
            unchanged.records[self.feature]["needsFixFromCheckpoint"],
            "verify_in_progress",
        )

        (self.feature_dir / "proposal.md").write_text("proposal\n", encoding="utf-8")
        (self.feature_dir / "design.md").write_text("design\n", encoding="utf-8")
        (self.feature_dir / "PLAN.md").write_text("plan\n", encoding="utf-8")
        (self.feature_dir / "plan.json").write_text("{}\n", encoding="utf-8")
        specs_dir = self.feature_dir / "specs"
        specs_dir.mkdir(exist_ok=True)
        (specs_dir / "requirements.md").write_text("requirements\n", encoding="utf-8")

        resumed = prepare_checkpoint_update(
            workspace=self.project,
            feature=self.feature,
            checkpoint="code_in_progress",
        )

        self.assertTrue(resumed.ok, resumed.errors)
        self.assertNotIn("needsFixFromCheckpoint", resumed.records[self.feature])

    def test_inspect_and_route_resolve_blocked_verify_node(self) -> None:
        self._set_checkpoint("needs_fix", needs_fix_from="verify_in_progress")
        config = _load_board_config()

        feature_payload = _capture_json(run_mode, self.project, self.feature, config)
        run = feature_payload["run"]
        statuses = {node["id"]: node["nodeStatus"] for node in run["nodes"]}
        self.assertEqual(run["currentNodeId"], "dev.verify")
        self.assertEqual(statuses["dev.e2e"], "done")
        self.assertEqual(statuses["dev.verify"], "blocked")
        self.assertEqual(statuses["ops.cicd"], "not_started")

        project_payload = _capture_json(project_mode, self.root, ["demo"], config)
        summary = project_payload["projects"]["demo"]["runs"][0]
        self.assertEqual(summary["currentNodeId"], "dev.verify")
        self.assertEqual(summary["currentNodeStatus"], "blocked")

        route, exit_code = resolve_route(self.project, self.feature)
        self.assertEqual(exit_code, 0)
        self.assertTrue(route["ok"])
        self.assertEqual(route["currentNodeId"], "dev.verify")
        self.assertEqual(route["currentNodeStatus"], "blocked")
        self.assertEqual(route["recommendedNextSkill"], "")
        self.assertIn("code_in_progress", route["allowedNextCheckpoints"])
        self.assertEqual(route["skippableNodes"], [])

    def test_e2e_source_marks_only_e2e_as_blocked(self) -> None:
        self._set_checkpoint("needs_fix", needs_fix_from="e2e_in_progress")

        payload = _capture_json(run_mode, self.project, self.feature, _load_board_config())
        run = payload["run"]
        statuses = {node["id"]: node["nodeStatus"] for node in run["nodes"]}

        self.assertEqual(run["currentNodeId"], "dev.e2e")
        self.assertEqual(statuses["dev.utest"], "done")
        self.assertEqual(statuses["dev.e2e"], "blocked")
        self.assertEqual(statuses["dev.verify"], "not_started")

    def test_utest_source_marks_utest_blocked_without_recommending_utest(self) -> None:
        self._set_checkpoint("needs_fix", needs_fix_from="unit_test_in_progress")

        payload = _capture_json(run_mode, self.project, self.feature, _load_board_config())
        run = payload["run"]
        statuses = {node["id"]: node["nodeStatus"] for node in run["nodes"]}
        self.assertEqual(run["currentNodeId"], "dev.utest")
        self.assertEqual(statuses["dev.utest"], "blocked")
        self.assertEqual(statuses["dev.e2e"], "not_started")

        route, exit_code = resolve_route(self.project, self.feature)
        self.assertEqual(0, exit_code)
        self.assertEqual("needs_fix", route["checkpoint"])
        self.assertEqual("", route["recommendedNextSkill"])
        self.assertIn("plan_in_progress", route["allowedNextCheckpoints"])
        self.assertIn("unit_test_in_progress", route["allowedNextCheckpoints"])

    def test_manual_needs_fix_without_source_uses_unique_stage(self) -> None:
        self._set_checkpoint("needs_fix", stage="Specs")

        payload = _capture_json(run_mode, self.project, self.feature, _load_board_config())

        self.assertEqual(payload["run"]["currentNodeId"], "dev.specs")
        statuses = {node["id"]: node["nodeStatus"] for node in payload["run"]["nodes"]}
        self.assertEqual(statuses["biz.prd"], "done")
        self.assertEqual(statuses["dev.specs"], "blocked")
        self.assertEqual(statuses["dev.plan"], "not_started")

        repaired = prepare_checkpoint_update(
            workspace=self.project,
            feature=self.feature,
            checkpoint="needs_fix",
            needs_fix_from_checkpoint="verify_in_progress",
        )

        self.assertTrue(repaired.ok, repaired.errors)
        self.assertEqual(
            repaired.records[self.feature]["needsFixFromCheckpoint"],
            "verify_in_progress",
        )

    def test_needs_fix_rejects_skip_with_blocked_state_message(self) -> None:
        self._set_checkpoint("needs_fix", stage="Specs")

        result = prepare_skip_update(
            workspace=self.project,
            feature=self.feature,
            skip_nodes=["dev.specs"],
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            result.transition_errors,
            ("当前处于 needs_fix 阻断状态，不能跳过节点；请先按修复建议回流到对应节点",),
        )

    def test_invalid_needs_fix_source_is_rejected(self) -> None:
        content = json.dumps({
            "schemaVersion": "autobizdevops.state.v3",
            "features": {
                self.feature: {
                    "feature": self.feature,
                    "checkpoint": "needs_fix",
                    "needsFixFromCheckpoint": "missing_checkpoint",
                }
            },
        })

        result = parse_state_json_records_result(content, workspace=self.project)

        self.assertNotIn(self.feature, result.records)
        self.assertIn(self.feature, result.record_errors)
        self.assertIn("无法映射到节点", result.record_errors[self.feature][0])


if __name__ == "__main__":
    unittest.main()
