from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.state_store import (  # noqa: E402
    STATE_SCHEMA_VERSION,
    check_or_fix_state_sync,
    render_state_md,
    state_json_content_from_records,
    write_state_records,
)
from hooks.update_checkpoint import prepare_checkpoint_update  # noqa: E402


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / ".autobizdevops" / "features").mkdir(parents=True)
    return workspace


def sample_record(checkpoint: str = "discuss_in_progress") -> dict[str, str]:
    return {
        "feature": "alpha",
        "owner": "owner",
        "checkpoint": checkpoint,
        "stage": "需求澄清",
        "iteration": "1",
        "updated_at": "2026-05-25 12:00:00",
    }


class StateStoreTests(unittest.TestCase):
    def test_loads_v2_state_and_repairs_markdown_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            records = {"alpha": sample_record()}
            (workspace / ".autobizdevops" / "state.json").write_text(
                state_json_content_from_records(records),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(result.records["alpha"]["checkpoint"], "discuss_in_progress")
            self.assertEqual(
                (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"),
                render_state_md(records),
            )

    def test_legacy_checkpoint_map_upgrades_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps({"alpha": "discuss_done"}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)
            upgraded = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(upgraded["schemaVersion"], STATE_SCHEMA_VERSION)
            self.assertEqual(upgraded["features"]["alpha"]["checkpoint"], "discuss_done")

    def test_missing_json_migrates_from_state_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "STATE.md").write_text(
                "\n".join(
                    [
                        "# 工程状态",
                        "",
                        "| Feature | 负责人 | checkpoint | 阶段 | 迭代 | 最后更新 |",
                        "|---------|--------|-----------|------|------|---------|",
                        "| alpha | owner | discuss_done | 需求澄清 | 2 | 2026-05-25 12:00:00 |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)
            migrated = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(migrated["features"]["alpha"]["checkpoint"], "discuss_done")
            self.assertIn("自动生成", (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"))

    def test_json_wins_over_stale_state_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("prd_done")})
            (workspace / ".autobizdevops" / "STATE.md").write_text("stale discuss_done\n", encoding="utf-8")

            result = check_or_fix_state_sync(workspace, fix=True)
            state_md = (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8")

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertIn("prd_done", state_md)
            self.assertNotIn("stale discuss_done", state_md)

    def test_unknown_checkpoint_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": STATE_SCHEMA_VERSION,
                        "features": {
                            "alpha": {
                                **sample_record(),
                                "checkpoint": "unknown_checkpoint",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = check_or_fix_state_sync(workspace, fix=True)

            self.assertFalse(result.ok)
            self.assertIn("未知 checkpoint", "\n".join(result.errors))


class StateIntegrationTests(unittest.TestCase):
    def test_update_checkpoint_cli_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                    "--checkpoint",
                    "discuss_in_progress",
                    "--allow-create",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state_json = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            state_md = (workspace / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8")
            self.assertEqual(state_json["features"]["alpha"]["checkpoint"], "discuss_in_progress")
            self.assertIn("discuss_in_progress", state_md)

    def test_update_existing_feature_keeps_lifecycle_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_state_records(workspace, {"alpha": sample_record("discuss_in_progress")})
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "PRD_DISCUSS.md").write_text("discussion", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "update_checkpoint.py"),
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                    "--checkpoint",
                    "discuss_done",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state_json = json.loads((workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_json["features"]["alpha"]["checkpoint"], "discuss_done")

    def test_inspect_uses_json_when_markdown_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_workspace(root / "collection")
            project.rename(root / "collection" / "proj")
            project = root / "collection" / "proj"
            write_state_records(project, {"alpha": sample_record("prd_done")})
            (project / ".autobizdevops" / "STATE.md").write_text("stale discuss_done\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "inspect_state.py"),
                    "--workspace",
                    str(root / "collection"),
                    "--project",
                    "proj",
                    "--mode",
                    "run",
                    "--feature",
                    "alpha",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["run"]["currentNodeId"], "biz.prd")
            self.assertIn("prd_done", (project / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"))

    def test_direct_state_file_edits_are_blocked_by_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            for state_file in (".autobizdevops/state.json", ".autobizdevops/STATE.md"):
                payload = {
                    "tool_name": "write_file",
                    "cwd": str(workspace),
                    "tool_input": {
                        "file_path": state_file,
                        "content": "{}",
                    },
                }

                result = subprocess.run(
                    [sys.executable, str(ROOT / "hooks" / "state_checkpoint.py"), "state-done"],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("state.json 是主事实源", result.stderr)
                self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_code_done_still_runs_compile_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            (feature_dir / "PLAN.md").write_text(
                "\n".join(["### 1. Implement", "- **状态:** 完成", ""]),
                encoding="utf-8",
            )
            write_state_records(workspace, {"alpha": sample_record("code_in_progress")})

            result = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="code_done",
            )

            self.assertFalse(result.ok)
            self.assertIn("缺少 pom.xml", "\n".join(result.errors))

    def test_init_workspace_and_create_feature_use_json_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "proj"
            project.mkdir()

            init_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "init_workspace.py"),
                    "--mode",
                    "createProject",
                    "--workspace",
                    str(project),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            create_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "init_workspace.py"),
                    "--mode",
                    "createFeature",
                    "--workspace",
                    str(root),
                    "--project",
                    "proj",
                    "--feature",
                    "beta",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(create_result.returncode, 0, create_result.stderr)
            state_json = json.loads((project / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_json["schemaVersion"], STATE_SCHEMA_VERSION)
            self.assertEqual(state_json["features"]["beta"]["checkpoint"], "discuss_in_progress")
            self.assertIn("beta", (project / ".autobizdevops" / "STATE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
