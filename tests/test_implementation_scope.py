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
from hooks.plan_writer import _task_group_preflight_errors


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


if __name__ == "__main__":
    unittest.main()
