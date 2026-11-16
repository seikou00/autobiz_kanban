import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hooks import render_collected_session_context as session


class SessionCollectorTimeoutTest(unittest.TestCase):
    def test_install_and_collector_timeouts_fall_back_to_platform_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / "platform knowledge"
            platform.mkdir()
            (platform / "agents.manifest.json").write_text(json.dumps({
                "schemaVersion": "v1",
                "systems": [{
                    "systemId": "SYS",
                    "deployUnits": [
                        {"deployUnitId": "UNIT", "agentsPath": "unit.md"},
                        {"deployUnitId": "DUP"},
                        {"deployUnitId": "DUP"},
                    ],
                }],
            }), encoding="utf-8")
            (platform / "unit.md").write_text("Platform unit knowledge", encoding="utf-8")
            plugin_sys = root / "sys"
            plugin_sys.mkdir()
            (plugin_sys / "agents.manifest.json").write_text(json.dumps({
                "schemaVersion": "v1",
                "systems": [{"systemId": "SYS", "deployUnits": [{"deployUnitId": "UNIT"}]}],
            }), encoding="utf-8")
            now = 0.0
            timeouts = []

            def timeout(command, *, timeout, **kwargs):
                nonlocal now
                timeouts.append(timeout)
                now += timeout
                raise subprocess.TimeoutExpired(command, timeout)

            with (
                patch.object(session.time, "monotonic", side_effect=lambda: now),
                patch.object(session.subprocess, "run", side_effect=timeout),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(session.main([
                    "--selected-deployUnit", '[{"deployUnitId": "UNIT"}]',
                    "--knowledge-collector", str(root / "collect-knowledge.js"),
                    "--knowledge-path", str(plugin_sys),
                    "--knowledge-path-platform", json.dumps([{"path": str(platform)}]),
                ]), 0)

            result = json.loads(stdout.getvalue())
            self.assertEqual(timeouts, [3, 3])
            self.assertTrue(result["ok"])
            self.assertIn(str(platform), result["message"])
            self.assertIn("Platform unit knowledge", result["sessionContext"])
            self.assertEqual(result["agentmdLoadStatus"][0]["source"], "remote")
