"""Review reuse is limited to unchanged upstream contracts and related tasks."""
import copy
import tempfile
import unittest
from pathlib import Path

from hooks.plan_review import review_changes, review_context_digest


def task(task_id, scenario, deps=()):
    return {"id": task_id, "goal": task_id, "deps": list(deps),
            "specRefs": [f"specs/cap/spec.md#{scenario}"]}


class PlanReviewTest(unittest.TestCase):
    def plan(self):
        return {"reviewContextDigest": "same-context", "tasks": [
            task("T001", "SCN-001"), task("T002", "SCN-002", ["T001"]),
            task("T003", "SCN-003", ["T002"]), task("T004", "SCN-004"),
            task("T005", "SCN-001"),
        ]}

    def test_first_plan_requires_full_review(self):
        result = review_changes(None, self.plan())
        self.assertEqual(result["reviewScope"], "full")
        self.assertEqual(result["affectedTaskIds"], ["T001", "T002", "T003", "T004", "T005"])

    def test_unchanged_plan_has_no_new_review_work(self):
        result = review_changes(self.plan(), self.plan())
        self.assertEqual(result["reviewScope"], "none")
        self.assertEqual(result["affectedTaskIds"], [])

    def test_local_change_includes_downstream_and_scenario_collaborators(self):
        before = self.plan()
        after = copy.deepcopy(before)
        after["tasks"][0]["goal"] = "Corrected delivery"
        result = review_changes(before, after)
        self.assertEqual(result["reviewScope"], "affected")
        self.assertEqual(result["changedTaskIds"], ["T001"])
        self.assertEqual(result["affectedTaskIds"], ["T001", "T002", "T003", "T005"])

    def test_api_collaborators_are_included_without_synthetic_dependency_edges(self):
        before = self.plan()
        before["tasks"][0]["apiIds"] = ["API-001"]
        before["tasks"][3]["designRefs"] = ["design.md#API-001"]
        after = copy.deepcopy(before)
        after["tasks"][0]["testPoints"] = ["Reject unavailable API input"]
        self.assertIn("T004", review_changes(before, after)["affectedTaskIds"])

    def test_removed_dependencies_keep_the_previous_downstream_in_scope(self):
        before = self.plan()
        after = copy.deepcopy(before)
        after["tasks"][1]["deps"] = []
        result = review_changes(before, after)
        self.assertEqual(result["affectedTaskIds"], ["T002", "T003"])

    def test_added_and_removed_tasks_are_explicit(self):
        before = self.plan()
        after = copy.deepcopy(before)
        after["tasks"] = after["tasks"][:-1] + [task("T009", "SCN-001")]
        result = review_changes(before, after)
        self.assertEqual(result["addedTaskIds"], ["T009"])
        self.assertEqual(result["removedTaskIds"], ["T005"])

    def test_shared_context_change_requires_full_review(self):
        before = self.plan()
        after = copy.deepcopy(before)
        after["reviewContextDigest"] = "updated-design"
        result = review_changes(before, after)
        self.assertTrue(result["contextChanged"])
        self.assertEqual(result["reviewScope"], "full")

    def test_legacy_draft_without_context_cannot_reuse_a_review(self):
        before = self.plan()
        before.pop("reviewContextDigest")
        self.assertEqual(review_changes(before, self.plan())["reviewScope"], "full")

    def test_context_tracks_specs_design_scope_ui_sources_and_repository_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            feature = Path(folder)
            baseline = review_context_digest(feature, {"default": "/repo"})
            for filename in ("proposal.md", "design.md", ".design-contract.lock.json", "IMPLEMENTATION_SCOPE.json",
                             "UI_CONTEXT.json", "source-context.json", "specs/cap/spec.md"):
                with self.subTest(filename=filename):
                    path = feature / filename
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("updated contract")
                    self.assertNotEqual(review_context_digest(feature, {"default": "/repo"}), baseline)
                    path.unlink()
            self.assertNotEqual(review_context_digest(feature, {"default": "/other-repo"}), baseline)
