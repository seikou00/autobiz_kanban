from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from board_core.state_store import load_state_json_records, write_state_records
from hooks.init_workspace import create_feature, init_workspace
from inspect_state import _load_board_config, build_run_payload


class ArchivedFeatureStatusTest(unittest.TestCase):
    def test_uses_state_selected_archive_iteration_for_artifacts_and_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / "demo"
            project.mkdir()
            init_workspace(project)

            feature = "archived-feature"
            create_feature(project, feature)
            active_dir = project / ".autobizdevops" / "features" / feature
            archive_dir = project / ".autobizdevops" / "archive"
            (archive_dir / f"{feature}-iter1").mkdir()

            (active_dir / "PRD.md").write_text("# current iteration\n", encoding="utf-8")
            selected_archive_dir = archive_dir / f"{feature}-iter2"
            active_dir.replace(selected_archive_dir)

            records, errors, exists = load_state_json_records(project)
            self.assertTrue(exists)
            self.assertEqual(errors, [])
            records[feature]["checkpoint"] = "archived"
            records[feature]["stage"] = "归档"
            records[feature]["iteration"] = "2"
            write_state_records(project, records)

            payload = build_run_payload(project, feature, _load_board_config())

        run = payload["run"]
        expected_dir = f".autobizdevops/archive/{feature}-iter2"
        self.assertEqual(run["hookLogRefs"][0]["path"], f"{expected_dir}/hooks.ndjson")
        self.assertIn(
            {"path": expected_dir, "purpose": "artifacts"},
            run["watchRefs"],
        )

        prd_node = next(node for node in run["nodes"] if node["id"] == "biz.prd")
        prd_artifact = next(artifact for artifact in prd_node["artifacts"] if artifact["id"] == "prd")
        self.assertEqual(prd_artifact["path"], f"{expected_dir}/PRD.md")
        self.assertEqual(prd_artifact["artifactStatus"], "generated")
        self.assertEqual(prd_artifact["artifactStatusLabel"], "已生成")


if __name__ == "__main__":
    unittest.main()
