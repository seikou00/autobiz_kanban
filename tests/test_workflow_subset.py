from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_core.contracts import (  # noqa: E402
    BoardConfigError,
    load_board_config,
    load_record_workflow_contracts,
)
from board_core.state_store import normalize_state_records  # noqa: E402
from board_core.workflow_closure import solve_node_closure  # noqa: E402
from board_core.workflow_compiler import (  # noqa: E402
    WorkflowCompileError,
    compile_board_config,
    compile_node_subset,
    configured_template_options,
    configured_workflow_templates,
    load_record_effective_board_config,
    resolve_template_subset,
)


LEAN_NODE_IDS = ["dev.specs", "dev.code", "ops.archive"]


def base_config() -> dict:
    return load_board_config(ROOT / "board_core" / "board_config.json")


class CompileNodeSubsetTest(unittest.TestCase):
    def test_standard_compile_unchanged_by_refactor(self) -> None:
        base = base_config()
        effective = compile_board_config(copy.deepcopy(base), repo_root=ROOT)
        self.assertEqual(
            [node["id"] for node in effective["workflow"]["nodes"]],
            [node["id"] for node in base["workflow"]["nodes"]],
        )
        self.assertEqual(effective["workflow"]["checkpoints"], base["workflow"]["checkpoints"])

    def test_profile_and_decision_compiles_still_work(self) -> None:
        base = base_config()
        frontend = compile_board_config(copy.deepcopy(base), repo_root=ROOT, profile="frontend_before_specs")
        self.assertIn("dev.frontend", [node["id"] for node in frontend["workflow"]["nodes"]])

        detail = compile_board_config(
            copy.deepcopy(base),
            repo_root=ROOT,
            workflow_decisions={"detail_design_before_code": "enabled"},
        )
        self.assertIn("dev.detail_design", [node["id"] for node in detail["workflow"]["nodes"]])

    def test_lean_subset_externalizes_dropped_producer_inputs(self) -> None:
        effective = compile_node_subset(base_config(), LEAN_NODE_IDS)
        self.assertEqual(
            [node["id"] for node in effective["workflow"]["nodes"]],
            LEAN_NODE_IDS,
        )
        self.assertEqual(
            effective["workflowExternalizedInputs"],
            {
                "dev.specs": ["PRD.md"],
                "dev.code": ["design.md", "PLAN.md"],
                "ops.archive": ["CICD_CHECKLIST.md"],
            },
        )
        code_node = effective["workflow"]["nodes"][1]
        externalized = [
            artifact
            for artifact in code_node["artifacts"]["inputs"]
            if artifact.get("external")
        ]
        self.assertEqual({artifact["path"] for artifact in externalized}, {"design.md", "PLAN.md"})
        # Externalized inputs become optional so prechecks do not block on them.
        self.assertTrue(all(artifact["required"] is False for artifact in externalized))

    def test_lean_subset_relinks_chain_and_filters_checkpoints(self) -> None:
        effective = compile_node_subset(base_config(), LEAN_NODE_IDS)
        checkpoints = effective["workflow"]["checkpoints"]
        self.assertEqual(checkpoints["initial"], ["specs_in_progress"])
        self.assertEqual(checkpoints["transitions"]["specs_done"], ["code_in_progress"])
        self.assertEqual(checkpoints["transitions"]["code_done"], ["archived"])
        self.assertNotIn("discuss_in_progress", checkpoints["stageLabels"])
        self.assertNotIn("plan_in_progress", checkpoints["stageLabels"])
        self.assertIn("needs_fix", checkpoints["stageLabels"])

        specs_node = effective["workflow"]["nodes"][0]
        done_state = next(state for state in specs_node["states"] if state["id"] == "done")
        self.assertEqual(done_state["nextAction"]["slashSkill"], "autodev-code")

    def test_subset_rejects_unknown_and_empty_selection(self) -> None:
        with self.assertRaises(WorkflowCompileError):
            compile_node_subset(base_config(), ["dev.unknown"])
        with self.assertRaises(WorkflowCompileError):
            compile_node_subset(base_config(), [])

    def test_forced_externalization(self) -> None:
        effective = compile_node_subset(
            base_config(),
            ["dev.specs", "dev.plan", "dev.code", "ops.archive"],
            externalized_inputs={"dev.code": ["PLAN.md"]},
        )
        self.assertEqual(
            effective["workflowExternalizedInputs"],
            {
                "dev.specs": ["PRD.md"],
                "dev.code": ["PLAN.md"],
                "ops.archive": ["CICD_CHECKLIST.md"],
            },
        )


class SolveNodeClosureTest(unittest.TestCase):
    def test_selecting_prd_pulls_discuss(self) -> None:
        result = solve_node_closure(base_config(), ["biz.prd"])
        self.assertEqual(result.nodes, ("biz.discuss", "biz.prd"))
        self.assertEqual(result.added, ("biz.discuss",))
        self.assertEqual(result.externalized, {})

    def test_no_auto_include_marks_entry_and_externalizes(self) -> None:
        result = solve_node_closure(base_config(), ["dev.code"], auto_include_producers=False)
        self.assertEqual(result.nodes, ("dev.code",))
        self.assertEqual(
            result.externalized,
            {"dev.code": ("proposal.md", "specs/**/*.md", "design.md", "PLAN.md")},
        )
        self.assertEqual(result.entry_nodes, ("dev.code",))

    def test_closure_result_compiles_as_subset(self) -> None:
        base = base_config()
        result = solve_node_closure(base, ["dev.code"], auto_include_producers=False)
        effective = compile_node_subset(
            base,
            list(result.nodes),
            externalized_inputs={node: list(paths) for node, paths in result.externalized.items()},
        )
        self.assertEqual([node["id"] for node in effective["workflow"]["nodes"]], ["dev.code"])

    def test_unknown_selection_rejected(self) -> None:
        with self.assertRaises(WorkflowCompileError):
            solve_node_closure(base_config(), ["dev.unknown"])


class WorkflowTemplateTest(unittest.TestCase):
    def test_registry_contains_three_templates(self) -> None:
        templates = configured_workflow_templates(base_config())
        self.assertEqual(templates["standard"]["kind"], "profile")
        self.assertEqual(templates["lean"]["kind"], "nodeSubset")
        self.assertEqual(templates["lean"]["nodes"], LEAN_NODE_IDS)
        self.assertEqual(templates["custom"]["kind"], "custom")
        options = configured_template_options(base_config())
        self.assertEqual(options[0]["id"], "standard")

    def test_resolve_template_subset(self) -> None:
        self.assertIsNone(resolve_template_subset(base_config(), "standard"))
        lean = resolve_template_subset(base_config(), "lean")
        self.assertEqual(lean, (LEAN_NODE_IDS, {}))
        custom = resolve_template_subset(
            base_config(),
            "custom",
            workflow_nodes=["dev.code"],
            workflow_externalized={"dev.code": ["PLAN.md"]},
        )
        self.assertEqual(custom, (["dev.code"], {"dev.code": ["PLAN.md"]}))
        with self.assertRaises(WorkflowCompileError):
            resolve_template_subset(base_config(), "custom")
        with self.assertRaises(WorkflowCompileError):
            resolve_template_subset(base_config(), "nope")

    def test_record_contracts_for_lean(self) -> None:
        contracts = load_record_workflow_contracts(
            ROOT,
            {"workflowProfile": "standard", "workflowDecisions": {}, "workflowTemplate": "lean"},
        )
        code = contracts.contract_for_skill("autodev-code")
        self.assertEqual(list(code.required_inputs), ["proposal.md", "specs/**/*.md"])
        self.assertIn("specs_in_progress", contracts.known_checkpoints)
        self.assertNotIn("plan_in_progress", contracts.known_checkpoints)
        self.assertEqual(contracts.allowed_next["code_done"], frozenset({"archived"}))

    def test_record_contracts_reject_profile_or_decisions_on_lean(self) -> None:
        with self.assertRaises(BoardConfigError):
            load_record_workflow_contracts(
                ROOT,
                {"workflowProfile": "frontend_before_specs", "workflowTemplate": "lean"},
            )
        with self.assertRaises(BoardConfigError):
            load_record_workflow_contracts(
                ROOT,
                {
                    "workflowProfile": "standard",
                    "workflowTemplate": "lean",
                    "workflowDecisions": {"detail_design_before_code": "enabled"},
                },
            )

    def test_record_effective_config_for_lean(self) -> None:
        effective = load_record_effective_board_config(
            ROOT / "board_core" / "board_config.json",
            repo_root=ROOT,
            record={"workflowTemplate": "lean"},
        )
        self.assertEqual(
            [node["id"] for node in effective["workflow"]["nodes"]],
            LEAN_NODE_IDS,
        )


class StateStoreTemplateRecordTest(unittest.TestCase):
    def test_lean_record_normalizes_with_subset_checkpoint(self) -> None:
        records, errors = normalize_state_records(
            {
                "feat-lean": {
                    "feature": "feat-lean",
                    "checkpoint": "specs_in_progress",
                    "workflowTemplate": "lean",
                }
            }
        )
        self.assertEqual(errors, [])
        record = records["feat-lean"]
        self.assertEqual(record["workflowTemplate"], "lean")
        self.assertEqual(record["checkpoint"], "specs_in_progress")

    def test_lean_record_rejects_out_of_subset_checkpoint(self) -> None:
        records, errors = normalize_state_records(
            {
                "feat-lean": {
                    "feature": "feat-lean",
                    "checkpoint": "plan_in_progress",
                    "workflowTemplate": "lean",
                }
            }
        )
        self.assertEqual(records, {})
        self.assertTrue(any("未知 checkpoint" in error for error in errors))

    def test_custom_record_keeps_nodes_and_externalized(self) -> None:
        records, errors = normalize_state_records(
            {
                "feat-custom": {
                    "feature": "feat-custom",
                    "checkpoint": "code_in_progress",
                    "workflowTemplate": "custom",
                    "workflowNodes": ["dev.code"],
                    "workflowExternalized": {"dev.code": ["proposal.md", "specs/**/*.md", "design.md", "PLAN.md"]},
                }
            }
        )
        self.assertEqual(errors, [])
        record = records["feat-custom"]
        self.assertEqual(record["workflowNodes"], ["dev.code"])
        self.assertIn("dev.code", record["workflowExternalized"])

    def test_legacy_record_defaults_to_standard_template(self) -> None:
        records, errors = normalize_state_records(
            {
                "feat-old": {
                    "feature": "feat-old",
                    "checkpoint": "discuss_in_progress",
                }
            }
        )
        self.assertEqual(errors, [])
        record = records["feat-old"]
        self.assertEqual(record["workflowTemplate"], "standard")
        self.assertNotIn("workflowNodes", record)


if __name__ == "__main__":
    unittest.main()
