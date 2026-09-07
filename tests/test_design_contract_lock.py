from __future__ import annotations

import tempfile
import unittest
import subprocess
import sys
import json
from pathlib import Path

from hooks.design_contract_lock import (
    load_confirmed_design_contract,
    sync_design_contract_lock,
    validate_design_contract_lock,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_design(feature_dir: Path) -> None:
    (feature_dir / "design.md").write_text(
        "\n".join([
            "# Design",
            "- x-auto-no-http-api: false",
            "- x-auto-no-sql: false",
            "| ID | Method |",
            "|----|--------|",
            "| API-001 | GET |",
            "| ID | Model |",
            "|----|-------|",
            "| DATA-001 | order |",
            "| ID | Decision |",
            "|----|----------|",
            "| D-001 | strategy |",
        ]),
        encoding="utf-8",
    )


class DesignContractLockTests(unittest.TestCase):
    def test_design_sync_is_the_only_operation_that_refreshes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {"alpha": {}}}),
                encoding="utf-8",
            )
            _write_design(feature_dir)

            created = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "design_contract_lock.py"),
                    "sync",
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            contract, errors = load_confirmed_design_contract(feature_dir, "alpha")
            self.assertEqual(errors, [])
            self.assertEqual(contract["ids"]["API"], {"API-001"})

            (feature_dir / "design.md").write_text(
                (feature_dir / "design.md").read_text(encoding="utf-8") + "\n<!-- changed -->\n",
                encoding="utf-8",
            )
            still_locked, lock_errors = load_confirmed_design_contract(feature_dir, "alpha")
            self.assertEqual(lock_errors, [])
            self.assertEqual(still_locked["sha256"], contract["sha256"])
            self.assertEqual(
                [issue["reason"] for issue in validate_design_contract_lock(feature_dir, "alpha")],
                ["design_contract_lock_outdated"],
            )

            refreshed = sync_design_contract_lock(workspace, "alpha")
            self.assertTrue(refreshed.ok, refreshed.errors)
            self.assertEqual(validate_design_contract_lock(feature_dir, "alpha"), [])


if __name__ == "__main__":
    unittest.main()
