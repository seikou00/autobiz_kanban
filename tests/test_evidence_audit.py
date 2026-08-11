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

from tests.test_plan_json_and_evidence import valid_plan, write_test_plan  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "evidence_audit.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class EvidenceAuditTest(unittest.TestCase):
    def test_report_classifies_missing_stream_and_done_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(feature_dir, valid_plan(status="done", evidence_ids=[]))

            result = _run("report", "--feature-dir", str(feature_dir))

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            codes = {finding["code"] for finding in payload["findings"]}
            self.assertIn("missing_evidence_stream", codes)
            self.assertIn("done_without_evidence", codes)

    def test_reset_invalid_tasks_moves_untrusted_done_task_to_todo_in_batch_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            write_test_plan(feature_dir, valid_plan(status="done", evidence_ids=[]))

            result = _run("audit", "--feature-dir", str(feature_dir), "--reset-invalid-tasks")

            self.assertNotEqual(result.returncode, 0)
            batch_path = feature_dir / "plans" / "B001" / "plan.json"
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            task = batch["tasks"][0]
            self.assertEqual(task["status"], "todo")
            self.assertEqual(task["completionEvidenceIds"], [])
            self.assertIsNone(task["latestPassEvidenceId"])
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["batches"][0]["status"], "todo")

    def test_reset_invalid_tasks_clears_handoff_and_reactivates_reset_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            first = valid_plan(status="done", evidence_ids=[])
            first_task = first["tasks"][0]
            second = json.loads(json.dumps(first_task))
            second["id"] = "T002"
            second["title"] = "second"
            second["goal"] = "deliver second"
            second["status"] = "todo"
            second["deps"] = ["T001"]
            second["acceptanceCriteria"][0]["id"] = "AC-T002-01"
            second["validationCommands"][0]["id"] = "VAL-T002-01"
            second["validationCommands"][0]["covers"] = ["AC-T002-01"]

            (feature_dir / "plan.json").write_text(
                json.dumps(
                    {
                        "featureId": "alpha",
                        "status": "awaiting_next_conversation",
                        "taskSetStatus": "finalized",
                        "activeBatchId": None,
                        "nextBatchId": "B002",
                        "taskValidationPolicy": {
                            "mode": "defer_to_test_stages",
                            "orchestration": "inline",
                            "codeGate": "batch_compile_only",
                            "maxTestStageRepairAttempts": 3,
                        },
                        "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
                        "batchValidationProfiles": {
                            "backend": {
                                "commands": [
                                    {
                                        "argv": [sys.executable, "-m", "compileall", "-q", "hooks"],
                                        "cwd": ".",
                                        "kind": "compile",
                                        "required": True,
                                    }
                                ]
                            }
                        },
                        "batches": [
                            {
                                "id": "B001",
                                "path": "plans/B001/plan.json",
                                "title": "first",
                                "specRoots": ["specs/capability/spec.md"],
                                "executionLane": "backend",
                                "deps": [],
                                "taskIds": ["T001"],
                                "status": "done",
                            },
                            {
                                "id": "B002",
                                "path": "plans/B002/plan.json",
                                "title": "second",
                                "specRoots": ["specs/capability/spec.md"],
                                "executionLane": "backend",
                                "deps": ["B001"],
                                "taskIds": ["T002"],
                                "status": "todo",
                            },
                        ],
                        "projectValidationCommands": first["projectValidationCommands"],
                        "projectCheckEvidenceIds": [],
                        "latestProjectCheckEvidenceId": None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            for batch_id, batch_task, status, completed_count, completed_at in (
                ("B001", first_task, "done", 1, "2026-07-10T00:00:00Z"),
                ("B002", second, "todo", 0, None),
            ):
                path = feature_dir / "plans" / batch_id / "plan.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "featureId": "alpha",
                            "batchId": batch_id,
                            "title": batch_id,
                            "executionLane": "backend",
                            "status": status,
                            "taskCount": 1,
                            "completedTaskCount": completed_count,
                            "completionEvidenceIds": [],
                            "batchValidation": {
                                "profile": "backend",
                                "status": "passed" if status == "done" else "pending",
                                "commands": [
                                    {
                                        "id": f"BATCH-{batch_id}-VAL-001",
                                        "argv": [sys.executable, "-m", "compileall", "-q", "hooks"],
                                        "cwd": ".",
                                        "kind": "compile",
                                        "required": True,
                                    }
                                ],
                                "evidenceIds": [],
                                "latestPassEvidenceIds": [],
                                "activeRunId": None,
                            },
                            "startedAt": None,
                            "completedAt": completed_at,
                            "tasks": [batch_task],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            (feature_dir / "BATCH_HANDOFF.json").write_text(
                json.dumps({"status": "awaiting_next_conversation", "nextBatchId": "B002"}),
                encoding="utf-8",
            )

            result = _run("audit", "--feature-dir", str(feature_dir), "--reset-invalid-tasks")

            self.assertNotEqual(result.returncode, 0)
            root = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(root["status"], "in_progress")
            self.assertEqual(root["activeBatchId"], "B001")
            self.assertEqual(root["nextBatchId"], "B002")
            self.assertFalse((feature_dir / "BATCH_HANDOFF.json").exists())


if __name__ == "__main__":
    unittest.main()
