from __future__ import annotations

import unittest

from hooks.parallel_conflict_policy import analyze_parallel_conflict_policy
from hooks.plan_json import PlanBundle


def _bundle(policy: dict, tasks_by_batch: dict[str, list[dict]]) -> PlanBundle:
    entries = []
    batches = {}
    task_batches = {}
    tasks = []
    for index, (batch_id, batch_tasks) in enumerate(tasks_by_batch.items(), start=1):
        entries.append({
            "id": batch_id,
            "executionLane": "backend",
            "workspaceRef": "default",
            "deps": [],
            "taskIds": [task["id"] for task in batch_tasks],
            "status": "todo",
            "executionStage": ({
                "proto": "proto",
                "shared": "integration",
                "database": "global",
                "configuration": "global",
            }.get(next(iter({touch.get("kind") for task in batch_tasks for touch in task.get("touches", []) if isinstance(touch, dict)}), "code"), "parallel")),
        })
        batches[batch_id] = {"batchId": batch_id, "tasks": batch_tasks}
        tasks.extend(batch_tasks)
        for task in batch_tasks:
            task_batches[task["id"]] = batch_id
    return PlanBundle(
        root={"parallelPolicy": policy, "batches": entries},
        batches=batches,
        tasks=tasks,
        task_batches=task_batches,
    )


class ParallelConflictPolicyTest(unittest.TestCase):
    def test_special_touch_kinds_get_single_owner_stages_and_dependencies(self) -> None:
        policy = {
            "enabled": True,
            "has_pb_change": True,
            "global_change_confirmations": {
                "database": {"confirmed": True, "batchId": "B003"},
            },
        }
        bundle = _bundle(policy, {
            "B001": [{"id": "T001", "workspaceRef": "default", "touches": [{"path": "proto/user.proto", "kind": "proto"}]}],
            "B003": [{"id": "T003", "workspaceRef": "default", "touches": [{"path": "db/migration.sql", "kind": "database"}]}],
            "B002": [{"id": "T002", "workspaceRef": "default", "touches": [{"path": "src/user.go", "kind": "code"}]}],
            "B004": [{"id": "T004", "workspaceRef": "default", "touches": [{"path": "cmd/main.go", "kind": "shared"}]}],
        })
        result = analyze_parallel_conflict_policy(bundle)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["stages"], {"B001": "proto", "B002": "parallel", "B003": "global", "B004": "integration"})
        self.assertEqual(result["dependencies"]["B002"], ["B001", "B003"])
        self.assertEqual(result["dependencies"]["B004"], ["B001", "B002", "B003"])

    def test_normal_overlap_is_warning_and_missing_touches_is_error(self) -> None:
        policy = {"enabled": True, "has_pb_change": False, "global_change_confirmations": {}}
        bundle = _bundle(policy, {
            "B001": [{"id": "T001", "workspaceRef": "default", "touches": [{"path": "src/main.go", "kind": "code"}]}],
            "B002": [{"id": "T002", "workspaceRef": "default", "touches": [{"path": "src/main.go", "kind": "code"}]}],
            "B003": [{"id": "T003", "workspaceRef": "default"}],
        })
        result = analyze_parallel_conflict_policy(bundle)
        self.assertIn("T003.touches_missing", result["errors"])
        self.assertEqual(result["warnings"][0]["type"], "normal_touch_overlap")

    def test_ambiguous_workspace_batch_is_rejected_by_policy(self) -> None:
        policy = {"enabled": True, "has_pb_change": False, "global_change_confirmations": {}}
        bundle = _bundle(policy, {
            "B001": [
                {"id": "T001", "workspaceRef": "api", "touches": [{"path": "src/a.go", "kind": "code"}]},
                {"id": "T002", "workspaceRef": "web", "touches": [{"path": "src/b.ts", "kind": "code"}]},
            ],
        })
        result = analyze_parallel_conflict_policy(bundle)
        self.assertIn("B001.workspaceRef_ambiguous", result["errors"])


if __name__ == "__main__":
    unittest.main()
