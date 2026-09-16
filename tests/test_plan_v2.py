"""End-to-end coverage for the one-shot Plan v2 publishing path."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.design_contract_lock import sync_design_contract_lock  # noqa: E402
from hooks.plan_json import load_plan_bundle  # noqa: E402
from hooks.parallel_validation_ownership import validation_ownership_errors  # noqa: E402
from hooks.utest_plan_contract import load_utest_plan  # noqa: E402


class PlanV2Test(unittest.TestCase):
    def _feature(self, root: Path) -> tuple[Path, Path]:
        workspace = root / "workspace"
        feature = workspace / ".autobizdevops" / "features" / "alpha"
        feature.mkdir(parents=True)
        (workspace / ".autobizdevops" / "state.json").write_text(
            json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {
                "alpha": {"checkpoint": "plan_in_progress", "workflowProfile": "standard", "workflowDecisions": {}, "workflowTemplate": "standard"}
            }}),
            encoding="utf-8",
        )
        spec = feature / "specs" / "cap"
        spec.mkdir(parents=True)
        (spec / "spec.md").write_text(
            "## ADDED Requirements\n### Requirement [REQ-001]: capability\n#### Scenario [SCN-001]: happy path\n",
            encoding="utf-8",
        )
        (feature / "design.md").write_text(
            "\n".join([
                "# Design",
                "## 3. API Decisions / 接口决策",
                "- x-auto-no-http-api: false",
                "| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit | Status |",
                "|----|--------|--------------|---------|----------|--------|-------------------|--------|",
                "| API-001 | GET | /cap | - | - | - | - | 已确认 |",
                "## 4. Data Decisions / 数据决策",
                "- x-auto-no-sql: false",
                "| ID | Table/Model | Change | Fields | Index/Migration | Rollback | Status |",
                "|----|-------------|--------|--------|-----------------|----------|--------|",
                "| DATA-001 | cap | update | value | - | - | 已确认 |",
                "## 5. Technical Design / 技术设计",
                "### Decisions",
                "| ID | Decision | Rationale | Alternatives | Status |",
                "|----|----------|-----------|--------------|--------|",
                "| D-001 | use service | simple | none | 已确认 |",
            ]),
            encoding="utf-8",
        )
        result = sync_design_contract_lock(workspace, "alpha")
        self.assertTrue(result.ok, result.errors)
        return workspace, feature

    def test_publish_plan_v2_derives_runtime_contract_without_file_or_command_hints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            payload = {
                "schemaVersion": "autodev.plan.v2",
                "featureId": "alpha",
                "tasks": [{
                    "id": "T001",
                    "outcome": "Users can retrieve the capability state",
                    "workspace": "default",
                    "dependsOn": [],
                    "implementationPoints": ["Expose the capability state through the confirmed API"],
                    "testPoints": ["Verify the response shape for an available capability"],
                    "refs": {
                        "requirements": ["specs/cap/spec.md#REQ-001"],
                        "scenarios": ["specs/cap/spec.md#SCN-001"],
                        "api": ["API-001"],
                        "design": ["API-001", "DATA-001", "D-001"],
                        "data": ["DATA-001"],
                        "decisions": ["D-001"],
                    },
                    "verification": {"intent": "The response exposes the expected capability state"},
                }],
            }
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "hooks" / "plan_writer.py"), "publish-plan",
                    "--workspace", str(workspace), "--feature", "alpha",
                    "--code-workspace", str(ROOT), "--body-stdin",
                ],
                input=json.dumps(payload), text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            bundle = load_plan_bundle(feature)
            task = bundle.batches["B001"]["tasks"][0]
            self.assertEqual(task["goal"], payload["tasks"][0]["outcome"])
            self.assertEqual(task["scope"]["paths"], [])
            self.assertEqual(task["implementationPoints"], payload["tasks"][0]["implementationPoints"])
            self.assertEqual(task["testPoints"], payload["tasks"][0]["testPoints"])
            self.assertEqual(task["validationCommands"], [])
            self.assertEqual(task["validationTestPlan"][0]["id"], "TEST-T001-01")
            self.assertTrue((feature / "PLAN.md").is_file())
            self.assertEqual(validation_ownership_errors(bundle.root, bundle.batches), [])
            test_plan = load_utest_plan(feature)
            self.assertEqual(test_plan["batches"][0]["tasks"][0]["verificationIntent"], payload["tasks"][0]["verification"]["intent"])
            self.assertEqual(test_plan["batches"][0]["tasks"][0]["testPoints"], payload["tasks"][0]["testPoints"])


if __name__ == "__main__":
    unittest.main()
