import json
import tempfile
import unittest
from pathlib import Path

from hooks.implementation_scope import (
    DEFAULT_SCOPE,
    load_scope,
    scope_split_path,
    validate_scope_payload,
    write_scope,
)
from hooks.plan_json import implementation_scope_task_errors
from hooks.plan_scope import load_plan_scope, write_partition
from hooks.plan_writer import _feature_scope_report, _task_group_preflight_errors


class ImplementationScopeTest(unittest.TestCase):
    @staticmethod
    def _task_group(*, ui_required: bool) -> dict:
        group = {
            "featureId": "feature-a",
            "groups": [
                {
                    "id": "T001",
                    "title": "deliver scoped behavior",
                    "executionMode": "code",
                    "deps": [],
                    "uiRequired": ui_required,
                    "workspaceRef": "default",
                    "specRefs": [
                        "specs/cap/spec.md#REQ-001",
                        "specs/cap/spec.md#SCN-001",
                    ],
                    "mergedScenarioRefs": [],
                    "apiIds": [],
                    "validationBoundary": "the public behavior has an executable validation seam",
                }
            ],
        }
        if ui_required:
            group["groups"][0]["uiRefs"] = {
                "pageRefs": ["PAGE-001"],
                "interactionRefs": [],
                "visualSourceRefs": [],
                "frontendRoute": "spec-driven-ui",
            }
        return group

    def test_legacy_feature_defaults_to_full_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scope, errors = load_scope(Path(temp) / "feature")

        self.assertEqual(scope, DEFAULT_SCOPE)
        self.assertEqual(errors, [])

    def test_scope_file_round_trips_and_validates_feature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp) / "feature-a"
            path = write_scope(feature_dir, "backend_only")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["implementationScope"], "backend_only")
            self.assertEqual(validate_scope_payload(payload, feature="feature-a"), [])
            self.assertEqual(load_scope(feature_dir), ("backend_only", []))
            split = scope_split_path(feature_dir).read_text(encoding="utf-8")
            self.assertIn("backend_only", split)
            self.assertIn("页面布局", split)

    def test_invalid_scope_payload_is_rejected(self) -> None:
        errors = validate_scope_payload(
            {"featureId": "feature-a", "implementationScope": "mobile_only", "source": "user_confirmed"},
            feature="feature-a",
        )
        self.assertIn("implementationScope_invalid", errors)

    def test_backend_only_rejects_frontend_task(self) -> None:
        errors = implementation_scope_task_errors(
            "backend_only",
            [{"id": "T001", "uiRequired": True}],
        )
        self.assertEqual(errors, ["T001.implementation_scope_backend_only_required:backend_only"])

    def test_frontend_only_rejects_backend_task(self) -> None:
        errors = implementation_scope_task_errors(
            "frontend_only",
            [{"id": "T001", "uiRequired": False}],
        )
        self.assertEqual(errors, ["T001.implementation_scope_frontend_only_required:frontend_only"])

    def test_full_stack_allows_both_lanes(self) -> None:
        errors = implementation_scope_task_errors(
            "full_stack",
            [{"id": "T001", "uiRequired": False}, {"id": "T002", "uiRequired": True}],
        )
        self.assertEqual(errors, [])

    def test_plan_writer_preflight_rejects_frontend_group_for_backend_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp) / "feature-a"
            write_scope(feature_dir, "backend_only")

            errors = _task_group_preflight_errors(feature_dir, self._task_group(ui_required=True))

        self.assertTrue(any(error["reason"] == "implementation_scope_frontend_task_forbidden" for error in errors))


class PlanScopePartitionTest(unittest.TestCase):
    @staticmethod
    def _feature_with_partition(temp: str, body: dict) -> Path:
        feature_dir = Path(temp) / "feature-a"
        write_scope(feature_dir, "full_stack")
        _, errors = write_partition(feature_dir, body)
        assert errors == [], errors
        return feature_dir

    def test_feature_without_partition_includes_everything(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp) / "feature-a"
            write_scope(feature_dir, "full_stack")
            scope, errors = load_plan_scope(feature_dir)

        self.assertEqual(errors, [])
        self.assertEqual(scope.declared_kinds, [])
        selection, select_errors = scope.select("scenario", {"a#SCN-001", "a#SCN-002"})
        self.assertEqual(selection.included, {"a#SCN-001", "a#SCN-002"})
        self.assertEqual(selection.deferred, set())
        self.assertEqual(select_errors, [])

    def test_declared_partition_narrows_the_required_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = self._feature_with_partition(temp, {
                "includedScenarioRefs": ["a#SCN-001"],
                "deferredScenarioRefs": ["a#SCN-002"],
            })
            scope, errors = load_plan_scope(feature_dir)

        self.assertEqual(errors, [])
        selection, select_errors = scope.select("scenario", {"a#SCN-001", "a#SCN-002"})
        self.assertEqual(select_errors, [])
        self.assertEqual(selection.included, {"a#SCN-001"})
        self.assertEqual(selection.deferred, {"a#SCN-002"})
        self.assertEqual(selection.unpartitioned, set())

    def test_unpartitioned_scenarios_defer_instead_of_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = self._feature_with_partition(temp, {
                "includedScenarioRefs": ["a#SCN-001"],
                "deferredScenarioRefs": [],
            })
            scope, _ = load_plan_scope(feature_dir)

        selection, select_errors = scope.select("scenario", {"a#SCN-001", "a#SCN-009"})

        self.assertEqual(select_errors, [])
        self.assertEqual(selection.included, {"a#SCN-001"})
        self.assertEqual(selection.unpartitioned, {"a#SCN-009"})

    def test_overlapping_partition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp) / "feature-a"
            write_scope(feature_dir, "full_stack")
            _, errors = write_partition(feature_dir, {
                "includedScenarioRefs": ["a#SCN-001"],
                "deferredScenarioRefs": ["a#SCN-001"],
            })

        self.assertEqual(
            [error["reason"] for error in errors],
            ["implementation_scope_partition_overlap"],
        )
        self.assertIn("SCN-001", errors[0]["repairSuggestion"])

    def test_partition_referring_to_absent_ids_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = self._feature_with_partition(temp, {
                "includedDesignIds": ["API-001"],
                "deferredDesignIds": ["API-404"],
            })
            scope, _ = load_plan_scope(feature_dir)

        _, select_errors = scope.select("design", {"API-001"})

        self.assertEqual(
            [error["reason"] for error in select_errors],
            ["implementation_scope_unknown_ref"],
        )
        self.assertIn("API-404", select_errors[0]["repairSuggestion"])

    def test_partition_survives_a_scope_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = self._feature_with_partition(temp, {
                "includedScenarioRefs": ["a#SCN-001"],
                "deferredScenarioRefs": ["a#SCN-002"],
            })
            write_scope(feature_dir, "backend_only")
            scope, errors = load_plan_scope(feature_dir)
            rewritten_scope, _ = load_scope(feature_dir)

        self.assertEqual(errors, [])
        self.assertEqual(scope.declared_kinds, ["scenario"])
        self.assertEqual(rewritten_scope, "backend_only")

    def test_deferred_and_unnamed_scenarios_are_reported_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp) / "feature-a"
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                "\n".join([
                    "## ADDED Requirements",
                    "### Requirement REQ-001: capability",
                    "#### Scenario SCN-001: this round",
                    "#### Scenario SCN-002: next round",
                    "#### Scenario SCN-003: never named",
                ]),
                encoding="utf-8",
            )
            write_scope(feature_dir, "full_stack")
            write_partition(feature_dir, {
                "includedScenarioRefs": ["specs/cap/spec.md#SCN-001"],
                "deferredScenarioRefs": ["specs/cap/spec.md#SCN-002"],
            })

            report = _feature_scope_report(
                feature_dir,
                [{"id": "T001", "specRefs": ["specs/cap/spec.md#SCN-001"]}],
            )

        self.assertEqual(report["scenario"]["includedCount"], 1)
        self.assertEqual(report["scenario"]["deferred"], ["specs/cap/spec.md#SCN-002"])
        self.assertEqual(report["scenario"]["unpartitioned"], ["specs/cap/spec.md#SCN-003"])

    def test_partition_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            feature_dir = Path(temp) / "feature-a"
            write_scope(feature_dir, "full_stack")
            _, errors = write_partition(feature_dir, {"includedPhases": ["phase1"]})

        self.assertEqual(
            [error["reason"] for error in errors],
            ["implementation_scope_partition_field_unknown"],
        )


if __name__ == "__main__":
    unittest.main()
