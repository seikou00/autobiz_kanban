from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if str(ROOT := Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import append_evidence  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "evidence_audit.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class EvidenceAuditTest(unittest.TestCase):
    def test_reset_invalid_tasks_maps_damaged_sidecar_to_referencing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            (feature_dir / "plan.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "tasks": [
                            {
                                "id": "T001",
                                "title": "one",
                                "status": "done",
                                "deps": [],
                                "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                                "designRefs": [],
                                "apiIds": [],
                                "dataIds": [],
                                "decisionIds": ["D-001"],
                                "validationCommands": [{"command": "echo ok"}],
                                "expectedFiles": [],
                                "evidenceIds": ["ev_0001"],
                                "blockers": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            append_evidence(
                feature_dir,
                {
                    "featureId": "alpha",
                    "checkpoint": "code_in_progress",
                    "nodeId": "dev.code",
                    "skill": "autodev-code",
                    "taskId": "T001",
                    "action": "validation",
                    "specRefs": ["#REQ-001", "#SCN-001"],
                    "designRefs": [],
                    "changedFiles": ["src/one.py"],
                    "validation": {"command": "echo ok", "exitCode": 0, "result": "pass"},
                },
                output_tail="ok\n",
            )
            (feature_dir / "evidence" / "ev_0001.json").write_text("{}\n", encoding="utf-8")

            result = _run("audit", "--feature-dir", str(feature_dir), "--reset-invalid-tasks")

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertIn("T001", payload["invalidTaskIds"])
            updated = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["tasks"][0]["status"], "todo")

    def test_report_classifies_missing_stream_and_done_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            (feature_dir / "plan.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "taskDetailVersion": 1,
                        "featureId": "alpha",
                        "tasks": [
                            {
                                "id": "T001",
                                "title": "one",
                                "goal": "deliver behavior",
                                "status": "done",
                                "deps": [],
                                "scope": {"modules": [], "entrypoints": [], "pages": [], "dataObjects": []},
                                "implementationPoints": ["update", "validate"],
                                "acceptanceCriteria": ["observable"],
                                "nonGoals": [],
                                "specRefs": ["specs/cap/spec.md#REQ-001", "#SCN-001"],
                                "designRefs": [],
                                "apiIds": [],
                                "dataIds": [],
                                "decisionIds": ["D-001"],
                                "validationCommands": [{"command": "echo ok"}],
                                "expectedFiles": [],
                                "evidenceIds": [],
                                "blockers": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = _run("report", "--feature-dir", str(feature_dir))

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            codes = {finding["code"] for finding in payload["findings"]}
            self.assertIn("missing_evidence_stream", codes)
            self.assertIn("done_without_evidence", codes)

    def test_reset_invalid_tasks_moves_untrusted_done_task_to_todo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            plan = {
                "version": 1,
                "taskDetailVersion": 1,
                "featureId": "alpha",
                "tasks": [{"id": "T001", "status": "done", "evidenceIds": []}],
            }
            (feature_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

            result = _run("audit", "--feature-dir", str(feature_dir), "--reset-invalid-tasks")

            self.assertNotEqual(result.returncode, 0)
            updated = json.loads((feature_dir / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["tasks"][0]["status"], "todo")
            self.assertEqual(updated["tasks"][0]["completionEvidenceIds"], [])
            self.assertIsNone(updated["tasks"][0]["latestPassEvidenceId"])


if __name__ == "__main__":
    unittest.main()
