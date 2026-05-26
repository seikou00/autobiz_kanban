from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "hooks" / "compile_evidence_recorder.py"


def run_recorder(payload: dict, *, session_dir: Path, session_id: str = "test-session") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AUTOBIZ_COMPILE_EVIDENCE_SESSION_DIR"] = str(session_dir)
    env["AUTOBIZ_COMPILE_EVIDENCE_SESSION_ID"] = session_id
    return subprocess.run(
        [sys.executable, str(RECORDER)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


class CompileEvidenceRecorderTest(unittest.TestCase):
    def test_records_build_to_remembered_checkpoint_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = root / "session"
            state_workspace = root / "state-workspace"
            code_workspace = root / "code-workspace"
            code_workspace.mkdir()

            update_result = run_recorder(
                {
                    "tool_name": "execute",
                    "cwd": str(root),
                    "tool_input": {
                        "command": (
                            f"/bin/zsh -lc 'python hooks/update_checkpoint.py --workspace {state_workspace} "
                            "--feature alpha --checkpoint code_in_progress'"
                        )
                    },
                    "tool_response": {"exit_code": 0, "stdout": "checkpoint updated"},
                },
                session_dir=session_dir,
            )
            self.assertEqual(update_result.returncode, 0)

            build_result = run_recorder(
                {
                    "tool_name": "execute",
                    "cwd": str(code_workspace),
                    "tool_input": {"command": "mvn compile"},
                    "tool_response": {"exit_code": 0, "stdout": "[INFO] BUILD SUCCESS"},
                },
                session_dir=session_dir,
            )
            self.assertEqual(build_result.returncode, 0)

            evidence_path = state_workspace / ".autobizdevops" / "compile-evidence.ndjson"
            records = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["cwd"], str(code_workspace.resolve(strict=False)))
            self.assertEqual(records[0]["command"], "mvn compile")
            self.assertEqual(records[0]["exit_code"], 0)
            self.assertIn("BUILD SUCCESS", records[0]["output_tail"])

    def test_buffers_build_until_checkpoint_workspace_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = root / "session"
            state_workspace = root / "state-workspace"
            code_workspace = root / "code-workspace"
            code_workspace.mkdir()

            build_result = run_recorder(
                {
                    "tool_name": "execute",
                    "cwd": str(code_workspace),
                    "tool_input": {"command": "npm run build"},
                    "tool_response": {"exitCode": 0, "stdout": "Compiled successfully"},
                },
                session_dir=session_dir,
            )
            self.assertEqual(build_result.returncode, 0)
            self.assertFalse((state_workspace / ".autobizdevops" / "compile-evidence.ndjson").exists())

            update_result = run_recorder(
                {
                    "tool_name": "execute",
                    "cwd": str(root),
                    "tool_input": {
                        "command": (
                            f"python hooks/update_checkpoint.py --workspace={state_workspace} "
                            "--feature=alpha --checkpoint=code_done"
                        )
                    },
                    "tool_response": {"exit_code": 0, "stdout": "checkpoint updated"},
                },
                session_dir=session_dir,
            )
            self.assertEqual(update_result.returncode, 0)

            evidence_path = state_workspace / ".autobizdevops" / "compile-evidence.ndjson"
            records = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["cwd"], str(code_workspace.resolve(strict=False)))
            self.assertEqual(records[0]["command"], "npm run build")
            self.assertEqual(records[0]["exit_code"], 0)
            self.assertIn("Compiled successfully", records[0]["output_tail"])

    def test_invalid_payload_never_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["AUTOBIZ_COMPILE_EVIDENCE_SESSION_DIR"] = str(Path(tmp) / "session")
            result = subprocess.run(
                [sys.executable, str(RECORDER)],
                input="{not json",
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
