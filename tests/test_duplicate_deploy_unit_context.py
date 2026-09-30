"""Duplicate manifest IDs must not disable unrelated session knowledge."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks.agents_repo import AgentsManifestError, build_sync_payload, load_manifest
from hooks.render_collected_session_context import render as render_collected
from hooks.render_session_context import render as render_legacy


class DuplicateDeployUnitContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sys = self.root / "sys"
        (self.sys / "S1").mkdir(parents=True)
        (self.sys / "S2").mkdir()
        (self.sys / "S1" / "AGENTS.md").write_text("# S1 system\n", encoding="utf-8")
        (self.sys / "S2" / "AGENTS.md").write_text("# S2 system\n", encoding="utf-8")
        (self.sys / "S1" / "normal.md").write_text("# normal unit\n", encoding="utf-8")
        (self.sys / "agents.manifest.json").write_text(json.dumps({
            "schemaVersion": "v1",
            "systems": [
                {"systemId": "S1", "deployUnits": [
                    {"deployUnitId": "U.normal", "agentsPath": "S1/normal.md"},
                    {"deployUnitId": "U.duplicate", "agentsPath": "S1/AGENTS.md"},
                ]},
                {"systemId": "S2", "deployUnits": [
                    {"deployUnitId": "U.duplicate", "agentsPath": "S2/AGENTS.md"},
                ]},
            ],
        }), encoding="utf-8")
        self.local = self.root / "local"
        self.local.mkdir()
        (self.local / "AGENTS.md").write_text("# ambiguous local content\n", encoding="utf-8")

    def selected(self):
        return [
            {"deployUnitId": "U.duplicate", "localRepoPath": str(self.local)},
            {"deployUnitId": "U.normal", "localRepoPath": str(self.local)},
        ]

    def test_strict_manifest_loading_still_rejects_duplicate(self):
        with self.assertRaisesRegex(AgentsManifestError, "deployUnitId 全局重复"):
            load_manifest(self.root)

    def test_sync_keeps_healthy_units_available(self):
        payload = build_sync_payload(self.root)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["supported_deploy_units"], ["U.normal", "U.duplicate"])
        self.assertEqual(payload["duplicate_deploy_unit_ids"], ["U.duplicate"])

    def test_legacy_loader_isolates_selected_duplicate(self):
        result = render_legacy(self.selected(), plugin_root=self.root)
        duplicate, normal = result["agentmdLoadStatus"]
        self.assertEqual(
            result["message"],
            "remote 1 / local 0 / 缺 1；清单中重复的 deployUnitId: U.duplicate",
        )
        self.assertFalse(duplicate["loaded"])
        self.assertIn("deployUnitId 重复: U.duplicate", duplicate["message"])
        self.assertTrue(normal["loaded"])
        self.assertEqual(normal["source"], "remote")
        self.assertIn("# S1 system", result["sessionContext"])
        self.assertIn("# normal unit", result["sessionContext"])
        self.assertNotIn("# S2 system", result["sessionContext"])
        self.assertNotIn("# ambiguous local content", result["sessionContext"])

        normal_only = render_legacy(self.selected()[1:], plugin_root=self.root)
        self.assertEqual(normal_only["message"], "remote 1 / local 0 / 缺 0")

    def test_collected_loader_skips_ambiguous_id(self):
        with patch(
            "hooks.render_collected_session_context.list_supported_deploy_units",
            return_value=["U.normal", "U.duplicate"],
        ), patch(
            "hooks.render_collected_session_context._deploy_unit_prompt",
            return_value="# collected normal unit\n",
        ) as collect:
            result = render_collected(
                self.selected(), plugin_root=self.root, knowledge_path=str(self.sys)
            )
        duplicate, normal = result["agentmdLoadStatus"]
        self.assertEqual(
            result["message"],
            "remote 1 / local 0 / 缺 1；清单中重复的 deployUnitId: U.duplicate",
        )
        self.assertFalse(duplicate["loaded"])
        self.assertIn("deployUnitId 重复: U.duplicate", duplicate["message"])
        self.assertTrue(normal["loaded"])
        self.assertIn("# collected normal unit", result["sessionContext"])
        self.assertNotIn("# ambiguous local content", result["sessionContext"])
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(collect.call_args.args[1], "U.normal")


if __name__ == "__main__":
    unittest.main()
