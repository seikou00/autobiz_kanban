import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
from pathlib import Path
from unittest.mock import call, patch

from hooks import render_collected_session_context as session
from hooks import sync_agents


class SessionCollectorTimeoutTest(unittest.TestCase):
    def test_pull_knowledge_npm_timeout_still_writes_manifest_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge_path = root / "sys"
            knowledge_path.mkdir()
            manifest = {"systems": [{"systemId": "SYS", "deployUnits": [{"deployUnitId": "UNIT"}]}]}
            (knowledge_path / "agents.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            config_path = root / "board_config.json"
            platform = sync_agents._platform_key()
            config_path.write_text(json.dumps({
                "supported_deploy_units": ["OLD"],
                "inspectCommands": {platform: {"knowledge_path": "${pluginPath}/sys"}},
            }), encoding="utf-8")
            now = 0.0
            timeouts = []

            def clone(*_args):
                nonlocal now
                now += 4
                return {"commit": "test"}

            def timeout(command, *, timeout, **kwargs):
                nonlocal now
                timeouts.append(timeout)
                now += timeout
                raise subprocess.TimeoutExpired(command, timeout)

            with (
                patch.object(sync_agents, "_resolve_repo", return_value=("repo", "main", "")),
                patch.object(sync_agents, "_log_environment"),
                patch.object(sync_agents, "sync_repo", side_effect=clone),
                patch.object(sync_agents, "get_agents_root", return_value=knowledge_path),
                patch.object(sync_agents, "build_sync_payload", partial(sync_agents.build_sync_payload, root)),
                patch.object(session.time, "monotonic", side_effect=lambda: now),
                patch.object(session, "_dependency_files_present", return_value=False),
                patch.object(session, "_run_with_timeout", side_effect=timeout),
                patch.object(sync_agents, "merge_supported_units_into_board_config", partial(
                    sync_agents.merge_supported_units_into_board_config, config_path=config_path,
                )),
                patch.object(sync_agents, "merge_knowledge_path_into_board_config", partial(
                    sync_agents.merge_knowledge_path_into_board_config, config_path=config_path,
                )),
                redirect_stderr(io.StringIO()),
                redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(sync_agents.main(["--write-board-config"]), 0)

            self.assertEqual(timeouts, [6])
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["boardConfigWritten"])
            self.assertEqual(result["supported_deploy_units_source"], "agents.manifest.json")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["supported_deploy_units"], ["UNIT"])
            self.assertEqual(config["inspectCommands"][platform]["knowledge_path"], str(knowledge_path))

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
                patch.object(session, "_run_with_timeout", side_effect=timeout),
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

    def test_windows_timeout_uses_files_and_terminates_process_tree(self):
        command = ["npm.cmd", "install"]
        expired = subprocess.TimeoutExpired(command, 3)
        with (
            patch.object(session.sys, "platform", "win32"),
            patch.object(session.subprocess, "Popen") as popen,
            patch.object(session.subprocess, "run") as run,
        ):
            process = popen.return_value
            process.pid = 123
            process.wait.side_effect = [expired, subprocess.TimeoutExpired(command, 1)]
            process.poll.return_value = None
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                session._run_with_timeout(command, timeout=3)

        self.assertIs(raised.exception, expired)
        capture = popen.call_args.kwargs
        self.assertTrue(hasattr(capture["stdout"], "fileno"))
        self.assertTrue(hasattr(capture["stderr"], "fileno"))
        self.assertEqual(process.wait.call_args_list, [call(timeout=3), call(timeout=1)])
        self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/PID", "123"])
        self.assertEqual(run.call_args.kwargs["timeout"], 1)
        process.kill.assert_called_once()
