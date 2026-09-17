"""End-to-end coverage for the one-shot Plan v2 publishing path."""

from __future__ import annotations

import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.design_contract_lock import sync_design_contract_lock  # noqa: E402
from hooks.plan_json import load_plan_bundle  # noqa: E402
from hooks.parallel_validation_ownership import validation_ownership_errors  # noqa: E402
from hooks.utest_plan_contract import load_utest_plan  # noqa: E402
from hooks.code_task_context import build_context, resolve_task_refs  # noqa: E402
from hooks.implementation_scope import write_scope  # noqa: E402
from hooks.plan_scope import write_partition  # noqa: E402


class PlanV2Test(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "schemaVersion": "autodev.plan.v2", "featureId": "alpha",
            "tasks": [{
                "id": "T001", "outcome": "Users retrieve capability state", "workspace": "default",
                "dependsOn": [],
                "implementationPoints": ["Expose the confirmed capability response"],
                "testPoints": ["Return the current value", "Handle an unavailable value"],
                "refs": {
                    "requirements": ["specs/cap/spec.md#REQ-001"],
                    "scenarios": ["specs/cap/spec.md#SCN-001"],
                    "api": ["API-001"], "data": ["DATA-001"], "decisions": ["D-001"],
                },
                "verification": {"intent": "Retrieval returns the current value or the confirmed unavailable response"},
            }],
        }

    def _publish(self, workspace: Path, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks/plan_writer.py"), "publish-plan",
             "--workspace", str(workspace), "--feature", "alpha",
             "--code-workspace", str(ROOT), "--body-stdin"],
            input=json.dumps(payload), text=True, capture_output=True, check=False,
        )

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
                        "design": ["API-001", "DATA-001"],
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

    def test_publish_plan_v2_derives_visual_sources_from_all_matching_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            (feature / "UI_CONTEXT.json").write_text(json.dumps({
                "uiRequired": True,
                "visualSources": [{"sourceId": "VIS-001"}, {"sourceId": "VIS-002"}],
                "capabilities": [
                    {
                        "capabilityId": "first-ui", "uiRequired": True,
                        "specRefs": ["specs/cap/spec.md#SCN-001"],
                        "visualSourceRefs": ["VIS-001"],
                    },
                    {
                        "capabilityId": "second-ui", "uiRequired": True,
                        "specRefs": ["specs/cap/spec.md#SCN-001"],
                        "visualSourceRefs": ["VIS-002"],
                    },
                ],
            }), encoding="utf-8")
            payload = {
                "schemaVersion": "autodev.plan.v2", "featureId": "alpha",
                "tasks": [{
                    "id": "T001", "outcome": "Users configure the capability", "workspace": "default",
                    "dependsOn": [],
                    "implementationPoints": ["Render the confirmed configuration flow"],
                    "testPoints": ["Prove both visual capability paths are available"],
                    "refs": {
                        "requirements": ["specs/cap/spec.md#REQ-001"],
                        "scenarios": ["specs/cap/spec.md#SCN-001"],
                        "api": ["API-001"], "design": ["API-001", "DATA-001"],
                        "data": ["DATA-001"], "decisions": ["D-001"],
                    },
                    "verification": {"intent": "Users can complete the configuration flow"},
                    "ui": {"pages": ["PAGE-001"], "interactions": ["UIX-001"], "route": "spec-driven-ui"},
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
            task = load_plan_bundle(feature).batches["B001"]["tasks"][0]
            self.assertEqual(task["uiRefs"]["visualSourceRefs"], ["VIS-001", "VIS-002"])

    def test_publish_plan_v2_accepts_unbracketed_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            (feature / "specs" / "cap" / "spec.md").write_text(
                "\n".join([
                    "## ADDED Requirements",
                    "### Requirement REQ-001: capability",
                    "#### Scenario SCN-001: happy path",
                ]),
                encoding="utf-8",
            )
            payload = {
                "schemaVersion": "autodev.plan.v2", "featureId": "alpha",
                "tasks": [{
                    "id": "T001", "outcome": "Users retrieve the capability state",
                    "workspace": "default", "dependsOn": [],
                    "implementationPoints": ["Expose the confirmed capability state"],
                    "testPoints": ["Verify the bounded capability response"],
                    "refs": {
                        "requirements": ["specs/cap/spec.md#REQ-001"],
                        "scenarios": ["specs/cap/spec.md#SCN-001"],
                        "api": ["API-001"], "design": ["API-001", "DATA-001"],
                        "data": ["DATA-001"], "decisions": ["D-001"],
                    },
                    "verification": {"intent": "The capability state is observable within its boundary"},
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
            task = load_plan_bundle(feature).batches["B001"]["tasks"][0]
            specs, design, errors = resolve_task_refs(feature, task)
            self.assertEqual(errors, [])
            self.assertEqual({item["anchor"] for item in specs}, {"REQ-001", "SCN-001"})
            self.assertEqual({item["anchor"] for item in design}, {"API-001", "DATA-001", "D-001"})

    def test_publish_plan_v2_projects_file_level_source_refs_to_code_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            snapshot = feature / "sources" / "SRC-001" / "capability.md"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text("The external capability has a bounded timeout.", encoding="utf-8")
            (feature / "source-context.json").write_text(json.dumps({
                "version": 1,
                "sources": [{
                    "id": "SRC-001", "name": "Capability contract",
                    "path": "sources/SRC-001/capability.md",
                    "availability": "snapshot_only", "readStatus": "complete", "freshness": "unknown",
                }],
            }), encoding="utf-8")
            spec = feature / "specs" / "cap" / "spec.md"
            spec.write_text(spec.read_text(encoding="utf-8") + "\n".join([
                "", "## Source References / 外部资料引用", "",
                "| Source ID | Requirement / Scenario | Usage |",
                "|---|---|---|",
                "| SRC-001 | REQ-001 / SCN-001 | Capability timeout contract |",
            ]), encoding="utf-8")

            result = self._publish(workspace, self._payload())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            task = load_plan_bundle(feature).batches["B001"]["tasks"][0]
            self.assertEqual(task["sourceRefs"], ["SRC-001"])
            context = build_context(workspace=workspace, feature="alpha", task_id="T001")
            self.assertTrue(context.ok, context.errors)
            self.assertEqual(context.data["taskContract"]["sourceRefs"], ["SRC-001"])
            self.assertEqual(context.data["resolvedSourceRefs"][0]["sourcePath"], "sources/SRC-001/capability.md")

    def test_plan_design_scope_uses_confirmed_lock_not_mutable_design_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            design = feature / "design.md"
            design.write_text(
                "\n".join(line for line in design.read_text(encoding="utf-8").splitlines() if "D-001" not in line),
                encoding="utf-8",
            )
            payload = self._payload()
            payload["tasks"][0]["refs"]["decisions"] = []

            result = self._publish(workspace, payload)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing_plan_json_decision_coverage", result.stdout)

    def test_mapped_source_without_context_or_snapshot_does_not_block_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            (feature / "source-context.json").write_text(json.dumps({
                "version": 1,
                "sources": [{
                    "id": "SRC-001", "name": "Capability contract",
                    "path": "sources/SRC-001/missing.md",
                    "availability": "snapshot_only", "readStatus": "complete", "freshness": "unknown",
                }],
            }), encoding="utf-8")
            spec = feature / "specs" / "cap" / "spec.md"
            spec.write_text(spec.read_text(encoding="utf-8") + "\n".join([
                "", "## Source References / 外部资料引用", "",
                "| Source ID | Requirement / Scenario | Usage |",
                "|---|---|---|",
                "| SRC-001 | REQ-001 / SCN-001 | Capability timeout contract |",
            ]), encoding="utf-8")

            result = self._publish(workspace, self._payload())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            context = build_context(workspace=workspace, feature="alpha", task_id="T001")
            self.assertTrue(context.ok, context.errors)
            self.assertEqual(context.data["resolvedSourceRefs"][0]["resolution"], "snapshot_missing")

            (feature / "source-context.json").write_text(json.dumps({
                "version": 1,
                "sources": [{
                    "id": "SRC-001", "name": "Capability contract",
                    "availability": "never_provided", "readStatus": "unreadable", "freshness": "unknown",
                }],
            }), encoding="utf-8")
            context = build_context(workspace=workspace, feature="alpha", task_id="T001")
            self.assertTrue(context.ok, context.errors)
            self.assertEqual(context.data["resolvedSourceRefs"][0]["resolution"], "never_provided")

            (feature / "source-context.json").unlink()
            context = build_context(workspace=workspace, feature="alpha", task_id="T001")
            self.assertTrue(context.ok, context.errors)
            self.assertEqual(context.data["resolvedSourceRefs"][0]["resolution"], "not_registered")

    def test_forward_dependencies_preserve_ids_and_publish_in_runtime_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            payload = self._payload()
            upstream = copy.deepcopy(payload["tasks"][0])
            upstream["id"] = "T009"
            upstream["refs"]["decisions"] = []
            payload["tasks"][0]["dependsOn"] = ["T009"]
            payload["tasks"].append(upstream)
            result = self._publish(workspace, payload)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            bundle = load_plan_bundle(feature)
            self.assertEqual(bundle.batches["B001"]["tasks"][0]["id"], "T009")
            self.assertEqual(bundle.batches["B002"]["tasks"][0]["deps"], ["T009"])
            context = build_context(workspace=workspace, feature="alpha", task_id="T001")
            self.assertTrue(context.ok, context.errors)
            self.assertEqual({ref["anchor"] for ref in context.data["resolvedDesignRefs"]}, {"API-001", "DATA-001", "D-001"})
            markdown = (feature / "PLAN.md").read_text()
            self.assertIn("Handle an unavailable value", markdown)
            self.assertIn("验证意图", markdown)

    def test_invalid_inputs_fail_before_publishing_any_bundle(self) -> None:
        mutations = [
            (lambda p: p["tasks"][0]["dependsOn"].append("T404"), "plan_v2_dependency_unknown"),
            (lambda p: p["tasks"][0]["dependsOn"].append("T001"), "plan_v2_dependency_cycle"),
            (lambda p: p["tasks"].append(copy.deepcopy(p["tasks"][0])), "plan_v2_task_id_duplicate"),
            (lambda p: p["tasks"][0]["refs"].update({"scenario": []}), "plan_v2_task_refs_field_unknown"),
            (lambda p: p["tasks"][0]["verification"].update({"command": "pytest"}), "plan_v2_task_verification_field_unknown"),
            (lambda p: p["tasks"][0]["verification"].clear(), "plan_v2_task_verification_intent_invalid"),
            (lambda p: p["tasks"][0].pop("workspace"), "plan_v2_task_workspace_missing"),
        ]
        for mutate, reason in mutations:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                workspace, feature = self._feature(Path(directory))
                payload = self._payload()
                mutate(payload)
                result = self._publish(workspace, payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stdout)
                self.assertFalse((feature / "plan.json").exists())
                self.assertFalse((feature / "PLAN.md").exists())

    def test_scope_partitions_match_publisher_and_stage_gate(self) -> None:
        from hooks.plan_writer import _plan_v2_to_groups, _task_group_preflight_errors
        # artifact_check is loaded by the production stage-gate environment.
        hooks_dir = str(ROOT / "skills/autodev/hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        from artifact_check import _validate_plan_json_traceability, validate_plan_scenario_coverage
        from common import HookContext

        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            spec = feature / "specs/cap/spec.md"
            spec.write_text(spec.read_text() + "#### Scenario SCN-002: later\n#### Scenario SCN-003: unassigned\n")
            design = feature / "design.md"
            design.write_text(design.read_text() + "\n| D-002 | later decision | later | none | 已确认 |\n")
            self.assertTrue(sync_design_contract_lock(workspace, "alpha").ok)
            write_scope(feature, "full_stack")
            _, errors = write_partition(feature, {
                "includedScenarioRefs": ["specs/cap/spec.md#SCN-001"],
                "deferredScenarioRefs": ["specs/cap/spec.md#SCN-002"],
                "includedDesignIds": ["API-001", "DATA-001", "D-001"], "deferredDesignIds": ["D-002"],
            })
            self.assertEqual(errors, [])
            payload = self._payload()
            result = self._publish(workspace, payload)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            bundle = load_plan_bundle(feature)
            tasks = bundle.batches["B001"]["tasks"]
            context = build_context(workspace=workspace, feature="alpha", task_id="T001")
            self.assertTrue(context.ok, context.errors)
            self.assertEqual(bundle.root["scopeReport"]["scenario"]["unpartitioned"], ["specs/cap/spec.md#SCN-003"])
            self.assertIn("SCN-003", (feature / "PLAN.md").read_text())
            ctx = HookContext(skill="autodev-plan", slug="alpha", root=workspace)
            self.assertEqual(_validate_plan_json_traceability(ctx, {"tasks": tasks}), 0)
            self.assertEqual(validate_plan_scenario_coverage(ctx), 0)
            _, errors = write_partition(feature, {"includedScenarioRefs": ["specs/cap/spec.md#SCN-999"]})
            self.assertEqual(errors, [])
            issues = _task_group_preflight_errors(feature, _plan_v2_to_groups(payload))
            self.assertIn("implementation_scope_unknown_ref", [issue["reason"] for issue in issues])
            self.assertGreater(validate_plan_scenario_coverage(ctx), 0)

    def test_ui_projection_allows_read_only_pages_and_deferred_ui_scope(self) -> None:
        hooks_dir = str(ROOT / "skills/autodev/hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        from artifact_check import validate_plan_ui_projection
        from common import HookContext

        with tempfile.TemporaryDirectory() as directory:
            workspace, feature = self._feature(Path(directory))
            ctx = HookContext(skill="autodev-plan", slug="alpha", root=workspace)
            ui = {
                "uiRequired": True, "pages": [{"pageId": "PAGE-001"}],
                "interactions": [], "visualSources": [],
                "capabilities": [{"uiRequired": True, "specRefs": ["specs/cap/spec.md#SCN-001"], "visualSourceRefs": []}],
            }
            read_only_task = {
                "id": "T001", "uiRequired": True, "specRefs": ["specs/cap/spec.md#SCN-001"],
                "uiRefs": {"pageRefs": ["PAGE-001"], "interactionRefs": [], "visualSourceRefs": [], "frontendRoute": "spec-driven-ui"},
            }
            with patch("artifact_check._load_ui_context_for_projection", return_value=(ui, 0)):
                with patch("artifact_check.load_and_validate_plan", return_value=({"tasks": [read_only_task]}, [])):
                    self.assertEqual(validate_plan_ui_projection(ctx), 0)
                with patch("artifact_check.load_and_validate_plan", return_value=({"tasks": [{"id": "T002", "uiRequired": False}]}, [])):
                    self.assertGreater(validate_plan_ui_projection(ctx), 0)
                    write_scope(feature, "backend_only")
                    self.assertEqual(validate_plan_ui_projection(ctx), 0)
                    write_scope(feature, "full_stack")
                    write_partition(feature, {
                        "includedScenarioRefs": [], "deferredScenarioRefs": ["specs/cap/spec.md#SCN-001"],
                    })
                    self.assertEqual(validate_plan_ui_projection(ctx), 0)


if __name__ == "__main__":
    unittest.main()
