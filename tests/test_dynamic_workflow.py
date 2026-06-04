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

from board_core.contracts import BoardConfigError, load_board_config, load_repo_workflow_contracts  # noqa: E402
from board_core.state_store import write_state_records  # noqa: E402
from board_core.workflow import build_workflow_shell  # noqa: E402
from board_core.workflow_compiler import compile_board_config, load_effective_board_config  # noqa: E402
from hooks.route_checkpoint import resolve_route  # noqa: E402
from hooks.update_checkpoint import prepare_checkpoint_update, write_hook_logs  # noqa: E402


DYNAMIC_SKILL = "autodev-dynamic-quality-gate"


def quality_overlay(*, phase: str = "Dev", insert_after: str = "dev.plan") -> dict:
    return {
        "profile": "quality",
        "nodes": [
            {
                "id": f"{phase.lower()}.quality",
                "phase": phase,
                "label": "质量门禁",
                "skill": DYNAMIC_SKILL,
                "insertAfter": insert_after,
                "checkpointPrefix": "quality_gate",
                "artifacts": {
                    "inputs": [
                        {"id": "design", "label": "技术设计", "path": "design.md", "required": True},
                        {"id": "plan", "label": "执行计划", "path": "PLAN.md", "required": True},
                    ],
                    "outputs": [
                        {
                            "id": "quality_gate",
                            "label": "质量门禁报告",
                            "path": "QUALITY_GATE.md",
                            "required": True,
                        }
                    ],
                },
                "validators": [],
            }
        ],
    }


def ops_overlay(*, enabled: bool = False) -> dict:
    overlay = {
        "profile": "ops-gate",
        "nodes": [
            {
                "id": "ops.gate",
                "phase": "Ops",
                "label": "发布门禁",
                "skill": DYNAMIC_SKILL,
                "insertAfter": "ops.cicd",
                "checkpointPrefix": "ops_gate",
                "artifacts": {
                    "inputs": [
                        {
                            "id": "cicd_checklist",
                            "label": "CI/CD 清单",
                            "path": "CICD_CHECKLIST.md",
                            "required": True,
                        }
                    ],
                    "outputs": [
                        {"id": "ops_gate", "label": "发布门禁报告", "path": "OPS_GATE.md", "required": True}
                    ],
                },
                "validators": [],
            }
        ],
    }
    if enabled:
        overlay["enabledDynamicPhases"] = ["Ops"]
    return overlay


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / ".autobizdevops" / "features" / "alpha").mkdir(parents=True)
    return workspace


def make_collection_project(root: Path) -> tuple[Path, Path]:
    collection = root / "collection"
    project = collection / "proj"
    (project / ".autobizdevops" / "features" / "alpha").mkdir(parents=True)
    return collection, project


def write_overlay(workspace: Path, profile: str, overlay: dict) -> None:
    overlay_dir = workspace / ".autobizdevops" / "workflow.d"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    (overlay_dir / f"{profile}.json").write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")


def record(checkpoint: str, *, profile: str = "quality") -> dict[str, str]:
    return {
        "feature": "alpha",
        "owner": "owner",
        "checkpoint": checkpoint,
        "stage": "test",
        "iteration": "1",
        "updated_at": "2026-05-25 12:00:00",
        "workflowProfile": profile,
    }


class DynamicWorkflowCompilerTests(unittest.TestCase):
    def test_frontend_profile_changes_workflow_shell_nodes(self) -> None:
        standard = load_effective_board_config(ROOT / "board_core" / "board_config.json", repo_root=ROOT)
        frontend = load_effective_board_config(
            ROOT / "board_core" / "board_config.json",
            repo_root=ROOT,
            profile="frontend_before_specs",
        )

        standard_nodes = [node["id"] for node in build_workflow_shell(standard)["nodes"]]
        frontend_nodes = [node["id"] for node in build_workflow_shell(frontend)["nodes"]]

        self.assertNotIn("dev.frontend", standard_nodes)
        self.assertLess(frontend_nodes.index("biz.prd"), frontend_nodes.index("dev.frontend"))
        self.assertLess(frontend_nodes.index("dev.frontend"), frontend_nodes.index("dev.specs"))

    def test_dev_overlay_inserts_node_and_derives_route_contracts(self) -> None:
        config = compile_board_config(
            load_board_config(ROOT / "board_core" / "board_config.json"),
            repo_root=ROOT,
            profile="quality",
            overlays=[quality_overlay()],
        )

        node_ids = [node["id"] for node in config["workflow"]["nodes"]]
        self.assertLess(node_ids.index("dev.plan"), node_ids.index("dev.quality"))
        self.assertLess(node_ids.index("dev.quality"), node_ids.index("dev.code"))
        transitions = config["workflow"]["checkpoints"]["transitions"]
        self.assertEqual(transitions["plan_done"], ["quality_gate_in_progress"])
        self.assertEqual(transitions["quality_gate_done"], ["code_in_progress"])

        nodes = {node["id"]: node for node in config["workflow"]["nodes"]}
        plan_done = {state["id"]: state for state in nodes["dev.plan"]["states"]}["done"]
        self.assertEqual(plan_done["nextAction"]["slashSkill"], DYNAMIC_SKILL)

    def test_ops_overlay_is_blocked_by_default_phase_policy(self) -> None:
        with self.assertRaisesRegex(BoardConfigError, "disabled by phase policy"):
            load_repo_workflow_contracts(ROOT, profile="ops-gate", overlays=[ops_overlay()])

    def test_ops_overlay_can_be_enabled_without_core_code_changes(self) -> None:
        contracts = load_repo_workflow_contracts(ROOT, profile="ops-gate", overlays=[ops_overlay(enabled=True)])

        self.assertIn("ops_gate_in_progress", contracts.known_checkpoints)
        self.assertEqual(contracts.allowed_next["cicd_done"], frozenset({"ops_gate_in_progress"}))
        self.assertEqual(contracts.allowed_next["ops_gate_done"], frozenset({"archived"}))

    def test_missing_required_input_fails_dependency_graph(self) -> None:
        overlay = quality_overlay()
        overlay["nodes"][0]["artifacts"]["inputs"].append(
            {"id": "missing", "label": "缺失", "path": "MISSING.md", "required": True}
        )

        with self.assertRaisesRegex(BoardConfigError, "required input is not produced upstream: MISSING.md"):
            load_repo_workflow_contracts(ROOT, profile="quality", overlays=[overlay])


class DynamicWorkflowRuntimeTests(unittest.TestCase):
    def test_route_checkpoint_requires_profile_choice_at_prd_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True, exist_ok=True)
            (feature_dir / "PRD.md").write_text("prd", encoding="utf-8")
            write_state_records(workspace, {"alpha": record("prd_done", profile="standard")})

            payload, exit_code = resolve_route(workspace, "alpha")

            self.assertEqual(exit_code, 0, payload)
            self.assertTrue(payload["requiresProfileChoice"])
            choices = {choice["id"]: choice for choice in payload["profileChoices"]}
            self.assertEqual(choices["standard"]["recommendedNextSkill"], "autodev-specs")
            self.assertEqual(choices["frontend_before_specs"]["recommendedNextSkill"], "autodev-frontend")

    def test_resolve_next_skill_cli_outputs_route_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "PRD.md").write_text("prd", encoding="utf-8")
            write_state_records(workspace, {"alpha": record("prd_done", profile="standard")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "hooks" / "resolve_next_skill.py"),
                    "--workspace",
                    str(workspace),
                    "--feature",
                    "alpha",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["recommendedNextSkill"], "autodev-specs")
            self.assertTrue(payload["requiresProfileChoice"])

    def test_standard_profile_allows_specs_and_rejects_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "PRD.md").write_text("prd", encoding="utf-8")
            write_state_records(workspace, {"alpha": record("prd_done", profile="standard")})

            specs = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="specs_in_progress",
            )
            frontend = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="frontend_in_progress",
            )

            self.assertTrue(specs.ok, specs.errors)
            self.assertFalse(frontend.ok)
            self.assertIn("未知 checkpoint: frontend_in_progress", "\n".join(frontend.errors))

    def test_frontend_profile_requires_frontend_before_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "PRD.md").write_text("prd", encoding="utf-8")
            write_state_records(workspace, {"alpha": record("prd_done", profile="frontend_before_specs")})

            skipped = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="specs_in_progress",
            )
            started = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="frontend_in_progress",
            )
            self.assertFalse(skipped.ok)
            self.assertIn("prd_done -> specs_in_progress", "\n".join(skipped.errors))
            self.assertTrue(started.ok, started.errors)

            write_state_records(workspace, started.records)
            finished = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="frontend_done",
            )
            self.assertTrue(finished.ok, finished.errors)
            write_state_records(workspace, finished.records)
            specs = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="specs_in_progress",
            )
            self.assertTrue(specs.ok, specs.errors)

    def test_inspect_hides_frontend_node_for_standard_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collection, project = make_collection_project(Path(tmp))
            (project / ".autobizdevops" / "features" / "alpha" / "PRD.md").write_text("prd", encoding="utf-8")
            write_state_records(project, {"alpha": record("prd_done", profile="standard")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "inspect_state.py"),
                    "--workspace",
                    str(collection),
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
            workflow_nodes = [node["id"] for node in payload["workflow"]["nodes"]]
            run_nodes = [node["id"] for node in payload["run"]["nodes"]]
            self.assertNotIn("dev.frontend", workflow_nodes)
            self.assertNotIn("dev.frontend", run_nodes)

    def test_inspect_shows_frontend_node_for_frontend_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collection, project = make_collection_project(Path(tmp))
            (project / ".autobizdevops" / "features" / "alpha" / "PRD.md").write_text("prd", encoding="utf-8")
            write_state_records(project, {"alpha": record("frontend_in_progress", profile="frontend_before_specs")})

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "inspect_state.py"),
                    "--workspace",
                    str(collection),
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
            workflow_nodes = [node["id"] for node in payload["workflow"]["nodes"]]
            self.assertIn("dev.frontend", workflow_nodes)
            self.assertEqual(payload["run"]["currentNodeId"], "dev.frontend")

    def test_route_checkpoint_uses_feature_workflow_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_overlay(workspace, "quality", quality_overlay())
            write_state_records(workspace, {"alpha": record("plan_done")})

            payload, exit_code = resolve_route(workspace, "alpha")

            self.assertEqual(exit_code, 0, payload)
            self.assertEqual(payload["workflowProfile"], "quality")
            self.assertEqual(payload["nextAction"]["slashSkill"], DYNAMIC_SKILL)

    def test_dynamic_lifecycle_checks_outputs_and_logs_dynamic_node_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            write_overlay(workspace, "quality", quality_overlay())
            (feature_dir / "design.md").write_text("design", encoding="utf-8")
            (feature_dir / "PLAN.md").write_text("plan", encoding="utf-8")
            write_state_records(workspace, {"alpha": record("plan_done")})

            started = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="quality_gate_in_progress",
            )
            self.assertTrue(started.ok, started.errors)
            write_state_records(workspace, started.records)

            finished = prepare_checkpoint_update(
                workspace=workspace,
                feature="alpha",
                checkpoint="quality_gate_done",
            )
            self.assertFalse(finished.ok)
            self.assertIn("QUALITY_GATE.md", "\n".join(finished.errors))

            write_hook_logs(finished, workspace=workspace, feature="alpha")
            hook_log = feature_dir / "hooks.ndjson"
            records = [json.loads(line) for line in hook_log.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(item["nodeId"] == "dev.quality" for item in records))


if __name__ == "__main__":
    unittest.main()
