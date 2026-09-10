from __future__ import annotations

import copy
import unittest

from hooks.parallel_runtime import plan_contract_snapshot, plan_drift_details
from hooks.plan_json import PlanBundle


def _bundle() -> PlanBundle:
    root = {
        "batches": [
            {"id": "B001", "status": "todo", "deps": []},
            {"id": "B002", "status": "todo", "deps": ["B001"]},
        ]
    }
    batches = {
        "B001": {
            "tasks": [{
                "id": "T001",
                "workspaceRef": "api",
                "scope": {"paths": ["src/a.py"], "workspaceRoots": {"api": "."}},
                "goal": "implement A",
            }]
        },
        "B002": {
            "tasks": [{
                "id": "T002",
                "workspaceRef": "api",
                "scope": {"paths": ["src/b.py"], "workspaceRoots": {"api": "."}},
                "goal": "implement B",
            }]
        },
    }
    return PlanBundle(root=root, batches=batches, tasks=[], task_batches={})


class PlanDriftDiagnosticsTest(unittest.TestCase):
    def test_snapshot_ignores_execution_state_but_keeps_contract(self) -> None:
        original = _bundle()
        updated = copy.deepcopy(original)
        updated.root["batches"][0]["status"] = "done"
        updated.batches["B001"]["tasks"][0]["status"] = "done"
        self.assertEqual(plan_contract_snapshot(original), plan_contract_snapshot(updated))

        updated.batches["B001"]["tasks"][0]["goal"] = "changed goal"
        self.assertNotEqual(plan_contract_snapshot(original), plan_contract_snapshot(updated))

    def test_drift_details_are_compact_and_actionable(self) -> None:
        original = _bundle()
        expected = plan_contract_snapshot(original)
        current = copy.deepcopy(original)
        current.root["batches"].append({"id": "B003", "status": "todo", "deps": []})
        current.batches["B003"] = {
            "tasks": [{
                "id": "T003",
                "workspaceRef": "api",
                "scope": {"paths": ["src/c.py"], "workspaceRoots": {"api": "."}},
                "goal": "implement C",
            }]
        }
        current.root["batches"][1]["deps"] = []
        current.batches["B001"]["tasks"][0]["goal"] = "changed goal"

        details = plan_drift_details(expected, current)
        self.assertEqual(details["addedBatches"], ["B003"])
        self.assertEqual(details["removedBatches"], [])
        self.assertEqual(details["modifiedBatches"], ["B001"])
        self.assertEqual(details["dependencyChanges"][0]["batchId"], "B002")
        self.assertEqual(details["action"], "restore_original_plan_or_create_new_run")
        self.assertNotIn("changed goal", str(details))


if __name__ == "__main__":
    unittest.main()
