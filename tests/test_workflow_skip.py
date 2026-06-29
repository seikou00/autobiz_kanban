from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import inspect_state  # noqa: E402
from board_core.contracts import (  # noqa: E402
    BoardConfigError,
    load_board_config,
    load_record_workflow_contracts,
)
from board_core.state_store import (  # noqa: E402
    load_state_json_records_result,
    normalize_state_records,
    parse_state_json_records,
    state_json_content_from_records,
    write_state_records,
)
from board_core.workflow import (  # noqa: E402
    derive_node_status,
    landing_checkpoint_after_skip,
    skippable_node_ids,
    validate_skip_request,
)
from board_core.workflow_compiler import (  # noqa: E402
    WorkflowCompileError,
    compile_board_config,
    compile_node_subset,
    configured_skip_policy,
    normalize_workflow_skipped_nodes,
)
from hooks.route_checkpoint import resolve_route  # noqa: E402
from hooks.update_checkpoint import (  # noqa: E402
    main as update_checkpoint_main,
    prepare_checkpoint_update,
    prepare_skip_update,
    validate_fix_request_for_needs_fix,
)


LEAN_NODE_IDS = ["dev.specs", "dev.code", "ops.archive"]


def base_config() -> dict:
    return load_board_config(ROOT / "board_core" / "board_config.json")


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / ".autobizdevops" / "features").mkdir(parents=True)
    return workspace


def seed_feature(
    workspace: Path,
    checkpoint: str,
    *,
    skipped: list[str] | None = None,
    artifacts: list[str] = (),
) -> None:
    record = {
        "feature": "alpha",
        "owner": "owner",
        "checkpoint": checkpoint,
        "stage": "",
        "iteration": "1",
        "updated_at": "2026-06-11 12:00:00",
    }
    if skipped:
        record["workflowSkippedNodes"] = skipped
    (workspace / ".autobizdevops" / "state.json").write_text(
        state_json_content_from_records({"alpha": record}),
        encoding="utf-8",
    )
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    feature_dir.mkdir(parents=True, exist_ok=True)
    for name in artifacts:
        path = feature_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\ncontent\n", encoding="utf-8")


E2E_PRECHECK_ARTIFACTS = [
    "proposal.md",
    "specs/alpha.md",
    "design.md",
    "PLAN.md",
    "REQUIREMENTS_EVAL.md",
]


class SkipCompileTests(unittest.TestCase):
    def test_standard_skip_marks_node_and_bridges_transitions(self) -> None:
        effective = compile_board_config(
            copy.deepcopy(base_config()), repo_root=ROOT, skipped_nodes=["dev.utest"]
        )
        nodes = effective["workflow"]["nodes"]
        by_id = {node["id"]: node for node in nodes}

        self.assertIn("dev.utest", by_id)
        self.assertTrue(by_id["dev.utest"].get("skipped"))
        self.assertFalse(by_id["dev.review"].get("skipped"))

        transitions = effective["workflow"]["checkpoints"]["transitions"]
        self.assertEqual(transitions["requirements_eval_done"], ["e2e_in_progress"])
        self.assertNotIn("unit_test_in_progress", transitions)
        self.assertNotIn("unit_test_done", transitions)
        self.assertNotIn("unit_test_in_progress", effective["workflow"]["checkpoints"]["stageLabels"])

        dropped = effective["workflowDroppedInputs"]
        self.assertEqual(dropped["dev.e2e"], ["UNIT_TEST_REPORT.md", "test-output.log"])
        self.assertEqual(dropped["dev.verify"], ["UNIT_TEST_REPORT.md", "test-output.log"])
        self.assertEqual(effective["workflowSkippedNodes"], ["dev.utest"])

        # The skipped producer's artifacts vanish from downstream bundles.
        e2e_input_paths = [artifact["path"] for artifact in by_id["dev.e2e"]["artifacts"]["inputs"]]
        self.assertNotIn("UNIT_TEST_REPORT.md", e2e_input_paths)
        self.assertNotIn("test-output.log", e2e_input_paths)
        self.assertIn("REQUIREMENTS_EVAL.md", e2e_input_paths)

    def test_skip_plan_removes_needs_fix_target(self) -> None:
        effective = compile_board_config(
            copy.deepcopy(base_config()), repo_root=ROOT, skipped_nodes=["dev.plan"]
        )
        transitions = effective["workflow"]["checkpoints"]["transitions"]
        self.assertEqual(transitions["specs_done"], ["code_in_progress"])
        self.assertNotIn("plan_in_progress", transitions["needs_fix"])

    def test_skip_unknown_node_rejected(self) -> None:
        with self.assertRaises(WorkflowCompileError):
            compile_board_config(
                copy.deepcopy(base_config()), repo_root=ROOT, skipped_nodes=["dev.nope"]
            )

    def test_skip_every_node_rejected(self) -> None:
        all_ids = [node["id"] for node in base_config()["workflow"]["nodes"]]
        with self.assertRaises(WorkflowCompileError):
            compile_board_config(
                copy.deepcopy(base_config()), repo_root=ROOT, skipped_nodes=all_ids
            )

    def test_profile_dynamic_node_skippable(self) -> None:
        effective = compile_board_config(
            copy.deepcopy(base_config()),
            repo_root=ROOT,
            profile="frontend_before_specs",
            skipped_nodes=["dev.frontend"],
        )
        by_id = {node["id"]: node for node in effective["workflow"]["nodes"]}
        self.assertTrue(by_id["dev.frontend"].get("skipped"))
        transitions = effective["workflow"]["checkpoints"]["transitions"]
        self.assertEqual(transitions["prd_done"], ["specs_in_progress"])
        self.assertNotIn("frontend_in_progress", transitions)

    def test_dynamic_stage_enabled_then_skipped(self) -> None:
        effective = compile_board_config(
            copy.deepcopy(base_config()),
            repo_root=ROOT,
            workflow_decisions={"detail_design_before_code": "enabled"},
            skipped_nodes=["dev.detail_design"],
        )
        by_id = {node["id"]: node for node in effective["workflow"]["nodes"]}
        self.assertTrue(by_id["dev.detail_design"].get("skipped"))
        transitions = effective["workflow"]["checkpoints"]["transitions"]
        self.assertEqual(transitions["plan_done"], ["code_in_progress"])
        self.assertNotIn("detail_design_in_progress", transitions.get("needs_fix", []))

    def test_lean_subset_with_skip(self) -> None:
        effective = compile_node_subset(
            base_config(), LEAN_NODE_IDS, skipped_nodes=["dev.specs"]
        )
        by_id = {node["id"]: node for node in effective["workflow"]["nodes"]}
        self.assertEqual(list(by_id), LEAN_NODE_IDS)
        self.assertTrue(by_id["dev.specs"].get("skipped"))
        self.assertEqual(
            effective["workflowDroppedInputs"],
            {
                "dev.code": ["proposal.md", "specs/**/*.md", "PRD.md", "design.md", "PLAN.md", "plan.json"],
                "ops.archive": ["CICD_CHECKLIST.md"],
            },
        )
        checkpoints = effective["workflow"]["checkpoints"]
        self.assertEqual(checkpoints["initial"], ["code_in_progress"])
        self.assertEqual(checkpoints["transitions"]["code_done"], ["archived"])
        self.assertEqual(effective["workflowSkippedNodes"], ["dev.specs"])

    def test_configured_skip_policy_defaults(self) -> None:
        self.assertEqual(configured_skip_policy(base_config()), {"lockedNodes": ()})
        config = copy.deepcopy(base_config())
        config["workflow"]["skipPolicy"] = {"lockedNodes": ["dev.code", "dev.code"]}
        self.assertEqual(configured_skip_policy(config), {"lockedNodes": ("dev.code",)})
        config["workflow"]["skipPolicy"] = {"lockedNodes": [123]}
        with self.assertRaises(WorkflowCompileError):
            configured_skip_policy(config)

    def test_normalize_workflow_skipped_nodes(self) -> None:
        self.assertEqual(normalize_workflow_skipped_nodes(None), ())
        self.assertEqual(
            normalize_workflow_skipped_nodes(["dev.utest", "dev.utest", " dev.e2e "]),
            ("dev.utest", "dev.e2e"),
        )
        with self.assertRaises(WorkflowCompileError):
            normalize_workflow_skipped_nodes([123])


class SkipContractsTests(unittest.TestCase):
    def record(self, skipped: list[str]) -> dict:
        return {
            "workflowProfile": "standard",
            "workflowTemplate": "standard",
            "workflowDecisions": {},
            "workflowSkippedNodes": skipped,
        }

    def test_record_contracts_exclude_skipped_node(self) -> None:
        contracts = load_record_workflow_contracts(ROOT, self.record(["dev.utest"]))

        self.assertNotIn("unit_test_in_progress", contracts.known_checkpoints)
        self.assertNotIn("unit_test_done", contracts.known_checkpoints)
        self.assertEqual(
            contracts.allowed_next["requirements_eval_done"], frozenset({"e2e_in_progress"})
        )
        self.assertNotIn("unit_test_in_progress", contracts.start_checkpoint_to_skill)
        self.assertNotIn("autodev-utest", contracts.skill_contracts)
        self.assertEqual(contracts.skipped_skills, {"autodev-utest": "dev.utest"})

        e2e = contracts.contract_for_skill("autodev-e2e")
        self.assertNotIn("UNIT_TEST_REPORT.md", e2e.required_inputs)
        # Dropped inputs leave the bundle entirely.
        self.assertNotIn("UNIT_TEST_REPORT.md", [artifact.path for artifact in e2e.inputs])
        self.assertNotIn("test-output.log", [artifact.path for artifact in e2e.inputs])

    def test_contract_for_skipped_skill_reports_skip(self) -> None:
        contracts = load_record_workflow_contracts(ROOT, self.record(["dev.utest"]))
        with self.assertRaises(BoardConfigError) as ctx:
            contracts.contract_for_skill("autodev-utest")
        self.assertIn("已在当前 workflow 中被跳过", str(ctx.exception))


class SkipRequestRuleTests(unittest.TestCase):
    def nodes(self) -> list[dict]:
        return [
            {"id": "a", "checkpoints": ["a_in_progress", "a_done"]},
            {"id": "b", "checkpoints": ["b_in_progress", "b_done"]},
            {"id": "c", "checkpoints": ["c_in_progress", "c_done"]},
            {"id": "z", "checkpoints": ["archived"]},
        ]

    def test_skip_current_in_progress_node(self) -> None:
        self.assertEqual(validate_skip_request(self.nodes(), "b_in_progress", ["b"]), [])
        self.assertEqual(
            landing_checkpoint_after_skip(self.nodes(), "b_in_progress", ["b"]),
            "c_in_progress",
        )

    def test_skip_future_node_keeps_checkpoint(self) -> None:
        self.assertEqual(validate_skip_request(self.nodes(), "b_in_progress", ["c"]), [])
        self.assertIsNone(landing_checkpoint_after_skip(self.nodes(), "b_in_progress", ["c"]))

    def test_skip_current_and_next_lands_on_following(self) -> None:
        self.assertEqual(validate_skip_request(self.nodes(), "b_in_progress", ["b", "c"]), [])
        self.assertEqual(
            landing_checkpoint_after_skip(self.nodes(), "b_in_progress", ["b", "c"]),
            "archived",
        )

    def test_rejects_done_current_node(self) -> None:
        errors = validate_skip_request(self.nodes(), "b_done", ["b"])
        self.assertTrue(any("已到 b_done" in error for error in errors))

    def test_rejects_completed_node(self) -> None:
        errors = validate_skip_request(self.nodes(), "b_in_progress", ["a"])
        self.assertTrue(any("已完成" in error for error in errors))

    def test_rejects_outside_node_checkpoint(self) -> None:
        errors = validate_skip_request(self.nodes(), "needs_fix", ["b"])
        self.assertTrue(any("不属于任何节点" in error for error in errors))

    def test_rejects_locked_node(self) -> None:
        errors = validate_skip_request(
            self.nodes(), "b_in_progress", ["b"], locked_nodes=["b"]
        )
        self.assertTrue(any("锁定" in error for error in errors))

    def test_rejects_already_skipped_node(self) -> None:
        nodes = self.nodes()
        nodes[2]["skipped"] = True
        errors = validate_skip_request(nodes, "b_in_progress", ["c"])
        self.assertTrue(any("已被跳过" in error for error in errors))

    def test_rejects_unknown_node(self) -> None:
        errors = validate_skip_request(self.nodes(), "b_in_progress", ["nope"])
        self.assertTrue(any("未知节点" in error for error in errors))

    def test_rejects_skipping_every_node(self) -> None:
        errors = validate_skip_request(self.nodes(), "a_in_progress", ["a", "b", "c", "z"])
        self.assertTrue(any("不能跳过全部" in error for error in errors))

    def test_rejects_skip_without_landing(self) -> None:
        errors = validate_skip_request(self.nodes(), "b_in_progress", ["b", "c", "z"])
        self.assertTrue(any("没有可落地" in error for error in errors))

    def test_skippable_node_ids(self) -> None:
        nodes = self.nodes()
        nodes[2]["skipped"] = True
        self.assertEqual(skippable_node_ids(nodes, "b_in_progress"), ["b", "z"])
        self.assertEqual(
            skippable_node_ids(nodes, "b_in_progress", locked_nodes=["z"]), ["b"]
        )

    def test_derive_node_status_skipped(self) -> None:
        node = {"id": "dev.utest", "skipped": True}
        self.assertEqual(derive_node_status(3, 5, "e2e_in_progress", node, {}), "skipped")
        self.assertEqual(derive_node_status(3, 9, "archived", node, {}), "skipped")


class SkipStateStoreTests(unittest.TestCase):
    def test_records_preserve_skipped_nodes(self) -> None:
        raw = {
            "alpha": {
                "feature": "alpha",
                "checkpoint": "e2e_in_progress",
                "workflowSkippedNodes": ["dev.utest"],
            }
        }
        records, errors = normalize_state_records(raw)
        self.assertEqual(errors, [])
        self.assertEqual(records["alpha"]["workflowSkippedNodes"], ["dev.utest"])

        content = state_json_content_from_records(records)
        reparsed, reparse_errors = parse_state_json_records(content)
        self.assertEqual(reparse_errors, [])
        self.assertEqual(reparsed["alpha"]["workflowSkippedNodes"], ["dev.utest"])

    def test_checkpoint_on_skipped_node_rejected(self) -> None:
        raw = {
            "alpha": {
                "feature": "alpha",
                "checkpoint": "unit_test_in_progress",
                "workflowSkippedNodes": ["dev.utest"],
            }
        }
        records, errors = normalize_state_records(raw)
        self.assertEqual(records, {})
        self.assertTrue(any("未知 checkpoint" in error for error in errors))

    def test_invalid_skip_field_rejected(self) -> None:
        raw = {
            "alpha": {
                "feature": "alpha",
                "checkpoint": "e2e_in_progress",
                "workflowSkippedNodes": [123],
            }
        }
        records, errors = normalize_state_records(raw)
        self.assertEqual(records, {})
        self.assertTrue(any("workflowSkippedNodes 无效" in error for error in errors))


class SkipUpdateCheckpointTests(unittest.TestCase):
    def test_skip_future_node_keeps_checkpoint_and_unlocks_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(
                workspace,
                "requirements_eval_done",
                artifacts=E2E_PRECHECK_ARTIFACTS,
            )

            # Without the skip the bridge transition is illegal.
            blocked = prepare_checkpoint_update(
                workspace=workspace, feature="alpha", checkpoint="e2e_in_progress"
            )
            self.assertFalse(blocked.ok)
            self.assertTrue(any("非法转移" in error for error in blocked.errors))

            result = prepare_skip_update(
                workspace=workspace, feature="alpha", skip_nodes=["dev.utest"]
            )
            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.old_checkpoint, "requirements_eval_done")
            self.assertEqual(result.new_checkpoint, "requirements_eval_done")
            self.assertEqual(
                result.records["alpha"]["workflowSkippedNodes"], ["dev.utest"]
            )
            write_state_records(workspace, result.records)

            advanced = prepare_checkpoint_update(
                workspace=workspace, feature="alpha", checkpoint="e2e_in_progress"
            )
            self.assertTrue(advanced.ok, advanced.errors)
            self.assertEqual(advanced.new_checkpoint, "e2e_in_progress")

    def test_skip_current_node_moves_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(
                workspace,
                "unit_test_in_progress",
                artifacts=E2E_PRECHECK_ARTIFACTS,
            )

            result = prepare_skip_update(
                workspace=workspace, feature="alpha", skip_nodes=["dev.utest"]
            )
            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.new_checkpoint, "e2e_in_progress")
            write_state_records(workspace, result.records)

            stored = load_state_json_records_result(workspace).records["alpha"]
            self.assertEqual(stored["checkpoint"], "e2e_in_progress")
            self.assertEqual(stored["workflowSkippedNodes"], ["dev.utest"])

    def test_skip_current_node_blocked_when_landing_precheck_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(
                workspace,
                "unit_test_in_progress",
                artifacts=[name for name in E2E_PRECHECK_ARTIFACTS if name != "REQUIREMENTS_EVAL.md"],
            )

            result = prepare_skip_update(
                workspace=workspace, feature="alpha", skip_nodes=["dev.utest"]
            )
            self.assertFalse(result.ok)
            self.assertTrue(
                any("autodev-e2e precheck failed" in error for error in result.lifecycle_errors),
                result.errors,
            )

    def test_skip_rejects_completed_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "unit_test_in_progress")

            result = prepare_skip_update(
                workspace=workspace, feature="alpha", skip_nodes=["dev.code"]
            )
            self.assertFalse(result.ok)
            self.assertTrue(any("已完成" in error for error in result.errors))

    def test_skip_rejects_locked_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "unit_test_in_progress")

            with mock.patch(
                "hooks.update_checkpoint.configured_skip_policy",
                return_value={"lockedNodes": ("dev.utest",)},
            ):
                result = prepare_skip_update(
                    workspace=workspace, feature="alpha", skip_nodes=["dev.utest"]
                )
            self.assertFalse(result.ok)
            self.assertTrue(any("锁定" in error for error in result.errors))

    def test_skip_rejects_already_skipped_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "e2e_in_progress", skipped=["dev.utest"])

            result = prepare_skip_update(
                workspace=workspace, feature="alpha", skip_nodes=["dev.utest"]
            )
            self.assertFalse(result.ok)
            self.assertTrue(any("已被跳过" in error for error in result.errors))

    def test_skip_rejects_missing_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "unit_test_in_progress")

            result = prepare_skip_update(
                workspace=workspace, feature="ghost", skip_nodes=["dev.utest"]
            )
            self.assertFalse(result.ok)
            self.assertTrue(any("不存在" in error for error in result.errors))


class SkipCliTests(unittest.TestCase):
    def _env(self, workspace: Path) -> dict[str, str]:
        return {
            "PLUGIN_WORKSPACE": str(workspace.parent),
            "PROJECT_CODE": workspace.name,
            "FEATURE_ID": "alpha",
        }

    def test_cli_skip_node_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp).resolve())
            seed_feature(
                workspace, "unit_test_in_progress", artifacts=E2E_PRECHECK_ARTIFACTS
            )

            buffer = io.StringIO()
            with mock.patch.dict(os.environ, self._env(workspace)):
                with contextlib.redirect_stdout(buffer):
                    code = update_checkpoint_main(["--skip-node", "dev.utest", "--json"])
            payload = json.loads(buffer.getvalue())
            self.assertEqual(code, 0, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["old_checkpoint"], "unit_test_in_progress")
            self.assertEqual(payload["new_checkpoint"], "e2e_in_progress")
            self.assertEqual(payload["skip_nodes"], ["dev.utest"])

            stored = load_state_json_records_result(workspace).records["alpha"]
            self.assertEqual(stored["checkpoint"], "e2e_in_progress")
            self.assertEqual(stored["workflowSkippedNodes"], ["dev.utest"])

            log_path = workspace / ".autobizdevops" / "features" / "alpha" / "hooks.ndjson"
            self.assertTrue(log_path.is_file())
            entries = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(
                any(
                    entry["eventId"] == "node-skip" and entry["eventStatus"] == "success"
                    for entry in entries
                ),
                entries,
            )

            board_buffer = io.StringIO()
            with contextlib.redirect_stdout(board_buffer):
                board_code = inspect_state.run_mode(
                    workspace, "alpha", inspect_state._load_board_config()
                )
            self.assertEqual(board_code, 0)
            board = json.loads(board_buffer.getvalue())
            board_nodes = {node["id"]: node for node in board["run"]["nodes"]}
            self.assertEqual(board_nodes["dev.utest"]["nodeStatus"], "skipped")

            rejected_buffer = io.StringIO()
            with mock.patch.dict(os.environ, self._env(workspace)):
                with contextlib.redirect_stdout(rejected_buffer):
                    rejected = update_checkpoint_main(
                        ["--checkpoint", "unit_test_in_progress", "--json"]
                    )
            self.assertEqual(rejected, 1)
            rejected_payload = json.loads(rejected_buffer.getvalue())
            self.assertTrue(
                any("未知 checkpoint" in error for error in rejected_payload["errors"]),
                rejected_payload,
            )

    def test_cli_requires_exactly_one_mode(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            both = update_checkpoint_main(
                ["--skip-node", "dev.utest", "--checkpoint", "e2e_in_progress"]
            )
            neither = update_checkpoint_main([])
        self.assertEqual(both, 1)
        self.assertEqual(neither, 1)
        self.assertIn("必须且只能", stderr.getvalue())

    def test_cli_skip_rejects_row_modifiers(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = update_checkpoint_main(
                ["--skip-node", "dev.utest", "--owner", "bob"]
            )
        self.assertEqual(code, 1)
        self.assertIn("不能与", stderr.getvalue())


class SkipRouteTests(unittest.TestCase):
    def test_route_bridges_skipped_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "requirements_eval_done", skipped=["dev.utest"])

            payload, exit_code = resolve_route(workspace, "alpha")
            self.assertEqual(exit_code, 0, payload)
            self.assertEqual(payload["allowedNextCheckpoints"], ["e2e_in_progress"])
            self.assertEqual(payload["recommendedNextSkill"], "autodev-e2e")
            self.assertEqual(payload["workflowSkippedNodes"], ["dev.utest"])
            self.assertEqual(
                payload["skippableNodes"],
                ["dev.e2e", "dev.verify", "ops.cicd", "ops.archive"],
            )

    def test_needs_fix_requires_fix_request_and_routes_to_suggested_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "needs_fix")
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"

            self.assertTrue(validate_fix_request_for_needs_fix(workspace, "alpha", "needs_fix"))

            (feature_dir / "FIX_REQUEST.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "sourceCheckpoint": "verify_in_progress",
                        "sourceNodeId": "dev.verify",
                        "suggestedCheckpoint": "code_in_progress",
                        "rootCause": "implementation_bug",
                        "blockingReason": "fix",
                        "humanActionRequired": False,
                        "failedSpecRefs": [],
                        "failedEvidenceIds": [],
                        "failedDesignRefs": [],
                        "createdAt": "2026-06-24T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(validate_fix_request_for_needs_fix(workspace, "alpha", "needs_fix"), ())
            payload, exit_code = resolve_route(workspace, "alpha")

            self.assertEqual(exit_code, 0, payload)
            self.assertEqual(payload["currentNodeId"], "needs_fix")
            self.assertEqual(payload["currentNodeStatus"], "blocked")
            self.assertEqual(payload["allowedNextCheckpoints"], ["code_in_progress"])
            self.assertEqual(payload["recommendedNextSkill"], "autodev-code")
            self.assertEqual(payload["fixRequestErrors"], [])

    def test_needs_fix_rejects_fix_request_to_skipped_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            seed_feature(workspace, "e2e_in_progress", skipped=["dev.plan"])
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            (feature_dir / "FIX_REQUEST.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "sourceCheckpoint": "e2e_in_progress",
                        "sourceNodeId": "dev.e2e",
                        "suggestedCheckpoint": "plan_in_progress",
                        "rootCause": "implementation_bug",
                        "blockingReason": "fix",
                        "humanActionRequired": False,
                        "failedSpecRefs": [],
                        "failedEvidenceIds": [],
                        "failedDesignRefs": [],
                        "createdAt": "2026-06-24T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            blocked = prepare_checkpoint_update(workspace=workspace, feature="alpha", checkpoint="needs_fix")

            self.assertFalse(blocked.ok)
            self.assertIn("suggestedCheckpoint 不在允许回流中", "\n".join(blocked.errors))


class SkipInspectStateTests(unittest.TestCase):
    def test_workflow_marker_includes_skips(self) -> None:
        marker, _, _ = inspect_state.workflow_marker(
            "standard", {}, "standard", None, ["dev.utest"]
        )
        self.assertEqual(marker, "base__skip__dev-utest")

        lean_marker, _, _ = inspect_state.workflow_marker(
            "standard", {}, "lean", None, ["dev.specs"]
        )
        self.assertEqual(lean_marker, "lean__skip__dev-specs")

        plain, _, _ = inspect_state.workflow_marker("standard", {}, "standard", None, None)
        self.assertEqual(plain, "base")

    def test_run_mode_renders_skipped_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # inspect_state.main resolves --workspace before run_mode; do the same
            # so artifact paths stay inside the workspace on macOS /var symlinks.
            workspace = make_workspace(Path(tmp).resolve())
            seed_feature(workspace, "e2e_in_progress", skipped=["dev.utest"])

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = inspect_state.run_mode(
                    workspace, "alpha", inspect_state._load_board_config()
                )
            self.assertEqual(code, 0)
            payload = json.loads(buffer.getvalue())

            run_nodes = {node["id"]: node for node in payload["run"]["nodes"]}
            self.assertEqual(run_nodes["dev.utest"]["nodeStatus"], "skipped")
            self.assertEqual(run_nodes["dev.utest"]["nodeStatusLabel"], "已跳过")
            self.assertEqual(run_nodes["dev.review"]["nodeStatus"], "done")
            self.assertEqual(payload["run"]["currentNodeId"], "dev.e2e")
            self.assertEqual(payload["run"]["workflowSkippedNodes"], ["dev.utest"])
            self.assertEqual(payload["run"]["workflowId"], "base__skip__dev-utest")

            shell_nodes = {node["id"]: node for node in payload["workflow"]["nodes"]}
            self.assertTrue(shell_nodes["dev.utest"].get("skipped"))


if __name__ == "__main__":
    unittest.main()
