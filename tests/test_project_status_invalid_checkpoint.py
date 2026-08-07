from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hooks.init_workspace import init_workspace
from inspect_state import _load_board_config, project_mode


class ProjectStatusInvalidCheckpointTest(unittest.TestCase):
    def test_keeps_invalid_checkpoint_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project = root / "demo"
            project.mkdir()
            init_workspace(project)
            state_json = project / ".autobizdevops" / "state.json"
            state_json.write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "broken-feature": {
                                "feature": "broken-feature",
                                "checkpoint": "missing_checkpoint",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(project_mode(root, ["demo"], _load_board_config()), 0)
            run = json.loads(output.getvalue())["projects"]["demo"]["runs"][0]

        self.assertEqual(run["featureId"], "broken-feature")
        self.assertEqual(run["currentNodeId"], "unknown")
        self.assertEqual(run["currentNodeStatus"], "unknown")
        self.assertEqual(run["currentNodeStatusLabel"], "未知")


if __name__ == "__main__":
    unittest.main()
