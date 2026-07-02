from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from hooks.run_advisory_smoke import main as smoke_main, run_advisory_smoke  # noqa: E402
from hooks.evidence_store import read_records, stream_path  # noqa: E402


class AdvisorySmokeRunnerTest(unittest.TestCase):
    def _feature_dir(self, workspace: Path) -> Path:
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        feature_dir.mkdir(parents=True, exist_ok=True)
        return feature_dir

    def test_failing_smoke_command_writes_result_and_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = self._feature_dir(workspace)
            source = workspace / "tests" / "smoke" / "cap_smoke.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("raise SystemExit(3)\n", encoding="utf-8")
            command = f'"{sys.executable}" tests/smoke/cap_smoke.py'
            (feature_dir / "SMOKE_TEST_PLAN.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "flowBlocking": False,
                        "skipReason": "",
                        "tests": [
                            {
                                "id": "SMK-001",
                                "taskId": "T001",
                                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                                "title": "cap smoke",
                                "smokeType": "cli",
                                "sourcePath": "tests/smoke/cap_smoke.py",
                                "command": command,
                                "expectedSignals": ["exit 0"],
                                "timeoutSeconds": 60,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(run_advisory_smoke(workspace, "alpha"), 0)

            result = json.loads((feature_dir / "SMOKE_RESULT.json").read_text(encoding="utf-8"))
            self.assertEqual(result["verdict"], "FAIL")
            self.assertEqual(result["results"][0]["result"], "fail")
            records = read_records(stream_path(feature_dir))
            self.assertEqual(records[0]["action"], "smoke")
            self.assertEqual(records[0]["smoke"]["testId"], "SMK-001")

    def test_missing_smoke_plan_writes_not_applicable_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = self._feature_dir(workspace)

            self.assertEqual(run_advisory_smoke(workspace, "alpha"), 0)

            result = json.loads((feature_dir / "SMOKE_RESULT.json").read_text(encoding="utf-8"))
            self.assertEqual(result["verdict"], "NOT_APPLICABLE")
            self.assertEqual(result["results"], [])

    def test_missing_source_path_returns_error_without_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = self._feature_dir(workspace)
            (feature_dir / "SMOKE_TEST_PLAN.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "flowBlocking": False,
                        "tests": [
                            {
                                "id": "SMK-001",
                                "taskId": "T001",
                                "scenarioRefs": ["specs/cap/spec.md#SCN-001"],
                                "title": "cap smoke",
                                "smokeType": "cli",
                                "sourcePath": "tests/smoke/missing.py",
                                "command": f'"{sys.executable}" tests/smoke/missing.py',
                                "expectedSignals": ["exit 0"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(smoke_main(["--workspace", str(workspace), "--feature", "alpha"]), 1)
            self.assertFalse((feature_dir / "SMOKE_RESULT.json").exists())

    def test_invalid_smoke_test_item_returns_error_without_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = self._feature_dir(workspace)
            (feature_dir / "SMOKE_TEST_PLAN.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "flowBlocking": False,
                        "tests": ["bad"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(smoke_main(["--workspace", str(workspace), "--feature", "alpha"]), 1)
            self.assertFalse((feature_dir / "SMOKE_RESULT.json").exists())


if __name__ == "__main__":
    unittest.main()
