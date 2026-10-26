"""Tests for hooks/list_supported_deploy_ids.py: collector + manifest ID merge."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.list_supported_deploy_ids import (  # noqa: E402
    get_supported_deploy_ids,
    main,
)
from hooks.render_collected_session_context import KnowledgeCollectorError  # noqa: E402

COLLECTOR_TARGET = "hooks.list_supported_deploy_ids.list_supported_deploy_units"


class ListSupportedDeployIdsTest(unittest.TestCase):
    def test_supported_deploy_ids_merge_collector_and_manifest_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {
                "systems": [
                    {"deployUnits": [
                        {"deployUnitId": "U2"},
                        {"deployUnitId": "U3"},
                        {"deployUnitId": "U3"},
                    ]},
                    {"serviceUnits": [{"serviceUnitId": "U4"}]},
                ],
            }
            (Path(directory) / "agents.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with patch(COLLECTOR_TARGET, return_value=["U1", "U2"]) as collector:
                ids = get_supported_deploy_ids(
                    "collector.js", knowledge_path=directory, node_command="node-custom"
                )

        self.assertEqual(ids, ["U1", "U2", "U3", "U4"])
        collector.assert_called_once_with(
            "collector.js", knowledge_path=directory,
            node_command="node-custom", npm_command="npm",
        )

    def test_supported_deploy_ids_uses_manifest_when_collector_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "agents.manifest.json").write_text(
                '{"systems":[{"deployUnits":[{"deployUnitId":"U1"}]}]}',
                encoding="utf-8",
            )
            with patch(
                COLLECTOR_TARGET, side_effect=KnowledgeCollectorError("node unavailable"),
            ):
                ids = get_supported_deploy_ids("collector.js", knowledge_path=directory)
        self.assertEqual(ids, ["U1"])

    def test_cli_outputs_array_and_fails_if_both_sources_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch(COLLECTOR_TARGET, return_value=["U1"]) as collector, \
                    contextlib.redirect_stdout(stdout):
                code = main(["--knowledge-path", directory])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue()), ["U1"])
            self.assertEqual(
                collector.call_args.args[0], str(ROOT / "hooks" / "collect-knowledge.js")
            )

            stdout = io.StringIO()
            with patch(
                COLLECTOR_TARGET, side_effect=KnowledgeCollectorError("node unavailable"),
            ), contextlib.redirect_stdout(stdout):
                code = main(["--knowledge-path", directory])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stdout.getvalue()), [])


if __name__ == "__main__":
    unittest.main()
