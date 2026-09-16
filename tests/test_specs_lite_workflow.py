"""Minimal specs still protect references and advance the real workflow."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "skills" / "autodev" / "hooks"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from artifact_check import HookContext, collect_spec_definition_index  # noqa: E402
from board_core.state_store import load_state_json_records, write_state_records  # noqa: E402
from hooks.implementation_scope import scope_path, write_scope  # noqa: E402
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.stage_gate import validate_stage  # noqa: E402
from hooks.update_checkpoint import main as update_checkpoint  # noqa: E402


PROPOSAL = """# Export

## Why

Users need exported orders.

## What Changes

Allow export requests.

## Capabilities

- `order-export`: Export orders.

## Impact

Order service and export clients.
"""

SPEC = """# Export

### Requirement REQ-001: Request an export

The system returns an export task identifier.

#### Scenario SCN-001: Accepted request

- **WHEN** a user requests an export
- **THEN** return the task identifier
"""


class SpecsLiteWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve() / "demo"
        self.project.mkdir()
        init_workspace(self.project)
        create_feature(self.project, "alpha")
        self.feature = self.project / ".autobizdevops/features/alpha"
        self.proposal = self.feature / "proposal.md"
        self.proposal.write_text(PROPOSAL, encoding="utf-8")
        self.spec = self.feature / "specs/order-export/spec.md"
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(SPEC, encoding="utf-8")
        (self.feature / "PRD.md").write_text("# Order export\n", encoding="utf-8")
        self.ui = {
            "version": 1,
            "featureId": "alpha",
            "uiRequired": False,
            "decisionStatus": "locked",
            "decisionSource": "user_confirmed",
            "notApplicableReason": "API only",
            "lockedAtCheckpoint": "specs_done",
            "pages": [],
            "interactions": [],
            "visualSources": [],
            "capabilities": [],
        }
        self.write_ui()
        records, errors, exists = load_state_json_records(self.project)
        self.assertTrue(exists)
        self.assertEqual(errors, [])
        records["alpha"]["checkpoint"] = "specs_in_progress"
        records["alpha"]["stage"] = "Specs"
        write_state_records(self.project, records)

    def write_ui(self):
        (self.feature / "UI_CONTEXT.json").write_text(json.dumps(self.ui), encoding="utf-8")

    def gate(self):
        return validate_stage(workspace=self.project, feature="alpha", stage="dev.specs")

    def advance(self, checkpoint):
        output = io.StringIO()
        env = {
            "PLUGIN_WORKSPACE": str(self.project.parent),
            "PROJECT_DIR": self.project.name,
            "FEATURE_ID": "alpha",
        }
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = update_checkpoint(["--checkpoint", checkpoint])
        return code, output.getvalue()

    def test_minimal_artifacts_advance_to_design_without_review_report(self):
        self.assertFalse((self.feature / "SPECS_REVIEW.md").exists())
        self.assertFalse((self.feature / "design.md").exists())
        code, output = self.advance("specs_done")
        self.assertEqual(code, 0, output)
        records, errors, _ = load_state_json_records(self.project)
        self.assertEqual(errors, [])
        self.assertEqual(records["alpha"]["checkpoint"], "specs_done")
        code, output = self.advance("design_in_progress")
        self.assertEqual(code, 0, output)

    def test_final_gate_preserves_downstream_id_index(self):
        result = self.gate()
        self.assertTrue(result.ok, result.errors)
        index, failures = collect_spec_definition_index(HookContext(skill="autodev-plan", slug="alpha", root=self.project))
        self.assertEqual(failures, 0)
        self.assertEqual(index, {"REQ": {"REQ-001"}, "SCN": {"SCN-001"}})

    def test_optional_sections_and_inline_references_do_not_block(self):
        self.spec.write_text(
            "## REMOVED Requirements\n\n" + SPEC
            + "\nSource: SRC-001-R001; 返回状态可为“待补充”。\n> 保留原文说明。\n",
            encoding="utf-8",
        )
        self.proposal.write_text(PROPOSAL.replace("- `order-export`", "### New Capabilities\n\n- `order-export`"), encoding="utf-8")
        result = self.gate()
        self.assertTrue(result.ok, result.errors)

    def test_legacy_grouped_proposal_and_operation_sections_still_pass(self):
        self.proposal.write_text(PROPOSAL.replace("- `order-export`", "### Modified Capabilities\n\n- `order-export`"), encoding="utf-8")
        self.spec.write_text("## MODIFIED Requirements\n\n" + SPEC + "\n## REMOVED Requirements\n", encoding="utf-8")
        result = self.gate()
        self.assertTrue(result.ok, result.errors)

    def test_only_capabilities_section_is_mandatory_in_proposal(self):
        self.proposal.write_text("# Export\n\n## Capabilities\n\n- order-export: Export.\n", encoding="utf-8")
        result = self.gate()
        self.assertTrue(result.ok, result.errors)
        self.proposal.write_text("# Export\n\nCapabilities are described here.\n", encoding="utf-8")
        result = self.gate()
        self.assertFalse(result.ok)
        self.assertIn("invalid_proposal_missing_section", str(result.errors))

    def test_missing_capability_spec_blocks_completion_without_state_change(self):
        self.proposal.write_text(PROPOSAL.replace("## Impact", "- billing: Billing.\n\n## Impact"), encoding="utf-8")
        code, output = self.advance("specs_done")
        self.assertNotEqual(code, 0)
        self.assertIn("proposal_capability_missing_spec", output)
        records, _, _ = load_state_json_records(self.project)
        self.assertEqual(records["alpha"]["checkpoint"], "specs_in_progress")

    def test_empty_spec_and_proposal_are_rejected(self):
        for path in (self.spec, self.proposal):
            with self.subTest(path=path.name):
                original = path.read_text()
                path.write_text("")
                self.assertFalse(self.gate().ok)
                path.write_text(original)

    def test_id_placeholders_are_invalid_even_when_another_requirement_is_valid(self):
        self.spec.write_text(SPEC + "\n### Requirement REQ-NNN: Extra\n\n#### Scenario SCN-NNN: Extra\n", encoding="utf-8")
        result = self.gate()
        self.assertFalse(result.ok)
        self.assertIn("spec_contract_heading_malformed", str(result.errors))

    def test_ui_references_are_still_required_and_resolved(self):
        self.ui["uiRequired"] = True
        self.ui["capabilities"] = [{
            "capabilityId": "order-export", "uiRequired": True,
            "pageRefs": [], "interactionRefs": [],
            "visualSourceRefs": [], "specRefs": ["REQ-001", "SCN-001"],
        }]
        self.write_ui()
        result = self.gate()
        self.assertTrue(result.ok, result.errors)
        self.ui["capabilities"][0]["specRefs"] = ["REQ-001", "SCN-999"]
        self.write_ui()
        result = self.gate()
        self.assertFalse(result.ok)
        self.assertIn("specRefs_unknown_scenario:SCN-999", str(result.errors))

    def test_unlocked_ui_context_blocks_completion(self):
        self.ui["decisionStatus"] = "confirmed"
        self.write_ui()
        result = self.gate()
        self.assertFalse(result.ok)
        self.assertIn("ui_context_not_locked", str(result.errors))

    def test_duplicate_ids_and_missing_scenarios_still_block(self):
        variants = {
            "duplicate_requirement_id": SPEC + SPEC.replace("SCN-001", "SCN-002"),
            "duplicate_scenario_id": SPEC + SPEC.replace("REQ-001", "REQ-002"),
            "spec_requirement_without_scenario": SPEC + "\n### Requirement REQ-002: Cancel export\n",
        }
        for reason, text in variants.items():
            with self.subTest(reason=reason):
                self.spec.write_text(text, encoding="utf-8")
                result = self.gate()
                self.assertFalse(result.ok)
                self.assertIn(reason, str(result.errors))

    def test_implementation_scope_validation_is_preserved(self):
        write_scope(self.feature, "backend_only")
        result = self.gate()
        self.assertTrue(result.ok, result.errors)
        scope_path(self.feature).write_text('{"scope": "unknown"}', encoding="utf-8")
        result = self.gate()
        self.assertFalse(result.ok)
        self.assertIn("invalid_implementation_scope", str(result.errors))


if __name__ == "__main__":
    unittest.main()
