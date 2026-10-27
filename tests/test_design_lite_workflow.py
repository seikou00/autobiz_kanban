"""Compact and legacy designs preserve the real Specs -> Design -> Plan path."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_specs_lite_workflow as specs_fixture
from artifact_check import HookContext, validate_design_contract
from hooks.design_contract_lock import sync_design_contract_lock, load_confirmed_design_contract
from hooks.implementation_scope import write_scope
from hooks.plan_scope import write_partition
from hooks.stage_gate import validate_stage

ROOT = Path(__file__).resolve().parents[1]

DESIGN = """# Export design

- x-auto-no-http-api: {no_api}
- x-auto-no-sql: {no_sql}

## Change / 变更与证据

Reuse the export entry. src/export.py:1 returns the task identifier.
{reason}

## Technical Design / 技术决策

| ID | Decision | Rationale |
|---|---|---|
| D-001 | Reuse export service | Preserve the current task response |
{contracts}

## Spec Traceability / 行为覆盖与验证

| Requirement | Scenarios | Design Coverage | Verification |
|---|---|---|---|
| REQ-001 | SCN-001 | {coverage} | An export request returns its task identifier |
"""

API = """
### API Decisions
| ID | Method | Path / Entry | Request | Response | Errors | Auth/Tenant/Audit |
|---|---|---|---|---|---|---|
| API-001 | POST | /exports | order filters | task identifier | invalid filter | existing user permission |
"""
DATA = """
### Data Decisions
| ID | Table/Model | Change | Fields | Index/Migration | Rollback |
|---|---|---|---|---|---|
| DATA-001 | export | add task | task identifier | unique identifier | remove task table |
"""
SECOND_REQUIREMENT = """
### Requirement REQ-002: Cancel export

Cancel a pending export.

#### Scenario SCN-002: Cancelled

- **WHEN** a pending export is cancelled
- **THEN** return its cancelled state
"""


def design(*, api=False, data=False):
    return DESIGN.format(
        no_api=str(not api).lower(), no_sql=str(not data).lower(),
        reason="No HTTP or schema changes." if not api and not data else "Use the contracts below.",
        contracts=(API if api else "") + (DATA if data else ""),
        coverage=" / ".join(["D-001"] + (["API-001"] if api else []) + (["DATA-001"] if data else [])),
    )


class DesignLiteWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.flow = self.new_feature()

    def new_feature(self):
        fixture = specs_fixture.SpecsLiteWorkflowTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        evidence = fixture.project / "src/export.py"
        evidence.parent.mkdir()
        evidence.write_text("def export(): return 'task-1'\n")
        return fixture

    def check_contract(self, text):
        (self.flow.feature / "design.md").write_text(text, encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            failures = validate_design_contract(HookContext(skill="autodev-design", slug="alpha", root=self.flow.project))
        return failures, output.getvalue()

    def test_compact_designs_lock_and_publish_a_plan_without_review_reports(self):
        for api, data in ((False, False), (True, False), (True, True)):
            with self.subTest(api=api, data=data):
                flow = self.new_feature()
                for checkpoint in ("specs_done", "design_in_progress"):
                    code, output = flow.advance(checkpoint)
                    self.assertEqual(code, 0, output)
                (flow.feature / "design.md").write_text(design(api=api, data=data), encoding="utf-8")
                locked = sync_design_contract_lock(flow.project, "alpha")
                self.assertTrue(locked.ok, locked.errors)
                checked = validate_stage(workspace=flow.project, feature="alpha", stage="dev.design")
                self.assertTrue(checked.ok, checked.errors)
                for checkpoint in ("design_done", "plan_in_progress"):
                    code, output = flow.advance(checkpoint)
                    self.assertEqual(code, 0, output)
                contract, errors = load_confirmed_design_contract(flow.feature, "alpha")
                self.assertEqual(errors, [])
                self.assertEqual(contract["noHttpApi"], not api)
                self.assertEqual(contract["noSql"], not data)
                payload = {
                    "schemaVersion": "autodev.plan", "featureId": "alpha",
                    "tasks": [{
                        "id": "T001", "outcome": "Users request an export and receive its task identifier",
                        "workspace": "default", "dependsOn": [],
                        "implementationPoints": ["Reuse export submission with the confirmed task response"],
                        "testPoints": ["An accepted export returns its task identifier"],
                        "refs": {
                            "requirements": ["specs/order-export/spec.md#REQ-001"],
                            "scenarios": ["specs/order-export/spec.md#SCN-001"],
                            "decisions": ["D-001"], "api": ["API-001"] if api else [],
                            "data": ["DATA-001"] if data else [],
                        },
                        "verification": {"intent": "An export request returns the correct task identifier"},
                    }],
                }
                for command in ("prepare-plan", "publish-plan"):
                    args = [sys.executable, str(ROOT / "hooks/plan_writer.py"), command,
                            "--workspace", str(flow.project), "--feature", "alpha"]
                    if command == "prepare-plan":
                        args += ["--code-workspace", str(ROOT), "--body-stdin"]
                    result = subprocess.run(args, input=json.dumps(payload), capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((flow.feature / "PLAN.md").is_file())
                self.assertFalse((flow.feature / "SPECS_REVIEW.md").exists())
                checked = validate_stage(workspace=flow.project, feature="alpha", stage="dev.plan")
                self.assertTrue(checked.ok, checked.errors)

    def test_legacy_design_sections_and_traceability_stay_valid(self):
        legacy = design().replace("## Change / 变更与证据", "## 1. Context / 输入上下文\n\nReuse export.\n\n## 2. Code Evidence")
        legacy = legacy.replace("| Design Coverage | Verification |", "| Decision | Design Coverage | Evidence |")
        legacy = legacy.replace("|---|---|---|---|\n| REQ", "|---|---|---|---|---|\n| REQ")
        legacy = legacy.replace("| SCN-001 | D-001 | An export request returns its task identifier |", "| SCN-001 | 无 | D-001 | src/export.py:1 |")
        legacy += "\n## API Decisions\nNo HTTP changes.\n\n## Data Decisions\nNo schema changes.\n\n## Risks / Open Questions\n无\n"
        failures, output = self.check_contract(legacy)
        self.assertEqual(failures, 0, output)

    def test_missing_scenario_coverage_blocks_completion(self):
        text = design().replace("| REQ-001 | SCN-001 | D-001 | An export request returns its task identifier |", "")
        failures, output = self.check_contract(text)
        self.assertGreater(failures, 0)
        self.assertIn("design_scenario_uncovered", output)
        self.assertTrue(sync_design_contract_lock(self.flow.project, "alpha").ok)
        code, _ = self.flow.advance("specs_done")
        self.assertEqual(code, 0)
        code, _ = self.flow.advance("design_in_progress")
        self.assertEqual(code, 0)
        code, output = self.flow.advance("design_done")
        self.assertNotEqual(code, 0, output)

    def test_unknown_references_are_actionable_failures(self):
        for original, replacement in (("REQ-001", "REQ-999"), ("SCN-001", "SCN-999"), ("| D-001 | An export", "| D-999 | An export")):
            with self.subTest(replacement=replacement):
                failures, output = self.check_contract(design().replace(original, replacement))
                self.assertGreater(failures, 0)
                self.assertIn("design_traceability_unresolved_ref", output)
                self.assertIn("POST_SKILL_REPAIR", output)

    def test_scenarios_must_reference_their_actual_requirement(self):
        self.flow.spec.write_text(self.flow.spec.read_text() + SECOND_REQUIREMENT)
        _, output = self.check_contract(design().replace("| REQ-001 | SCN-001", "| REQ-002 | SCN-001"))
        self.assertIn("design_traceability_requirement_mismatch", output)

    def test_deferred_scenarios_do_not_require_fake_design_rows(self):
        self.flow.spec.write_text(self.flow.spec.read_text() + SECOND_REQUIREMENT)
        write_scope(self.flow.feature, "backend_only")
        _, errors = write_partition(self.flow.feature, {
            "includedScenarioRefs": ["specs/order-export/spec.md#SCN-001"],
            "deferredScenarioRefs": ["specs/order-export/spec.md#SCN-002"],
        })
        self.assertEqual(errors, [])
        failures, output = self.check_contract(design())
        self.assertEqual(failures, 0, output)

    def test_compact_verification_cannot_be_empty(self):
        failures, output = self.check_contract(design().replace("An export request returns its task identifier", "-"))
        self.assertGreater(failures, 0)
        self.assertIn("design_verification_missing", output)

    def test_removing_verification_column_does_not_bypass_the_contract(self):
        text = design().replace(" | Verification |", " |")
        text = text.replace("|---|---|---|---|\n| REQ", "|---|---|---|\n| REQ")
        text = text.replace(" | An export request returns its task identifier |", " |")
        failures, output = self.check_contract(text)
        self.assertGreater(failures, 0)
        self.assertIn("design_verification_missing", output)

    def test_declared_api_or_data_changes_need_contracts_before_leaving_design(self):
        for marker, reason in (("x-auto-no-http-api", "missing_design_api_id"),
                               ("x-auto-no-sql", "missing_design_data_id")):
            with self.subTest(marker=marker):
                text = design().replace(f"{marker}: true", f"{marker}: false")
                failures, output = self.check_contract(text)
                self.assertGreater(failures, 0)
                self.assertIn(reason, output)

    def test_duplicate_ids_and_pending_decisions_still_block(self):
        for addition, reason in (("\n| D-001 | another design | duplicate |", "duplicate_design_decision_id"),
                                 ("\n| D-002 | other choice | 待确认 |", "design_has_pending_cells")):
            with self.subTest(reason=reason):
                failures, output = self.check_contract(design() + addition)
                self.assertGreater(failures, 0)
                self.assertIn(reason, output)

    def test_no_api_marker_cannot_hide_real_api_definitions(self):
        _, output = self.check_contract(design(api=True).replace("x-auto-no-http-api: false", "x-auto-no-http-api: true"))
        self.assertIn("design_api_marker_conflicts_with_definitions", output)

    def test_heading_names_in_prose_cannot_replace_required_sections(self):
        _, output = self.check_contract(design().replace("## Technical Design / 技术决策", "Technical Design is discussed below."))
        self.assertIn("invalid_design_missing_section", output)
