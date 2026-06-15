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

    def test_standard_profile_and_decision_compiles_still_work(self) -> None:
        base = base_config()
        standard = compile_board_config(copy.deepcopy(base), repo_root=ROOT, profile="standard")
        self.assertNotIn("dev.frontend", [node["id"] for node in standard["workflow"]["nodes"]])

        detail = compile_board_config(
            copy.deepcopy(base),
            repo_root=ROOT,
            workflow_decisions={"detail_design_before_code": "enabled"},
        )
        self.assertIn("dev.detail_design", [node["id"] for node in detail["workflow"]["nodes"]])

    def test_lean_subset_drops_broken_inputs(self) -> None:
        effective = compile_node_subset(base_config(), LEAN_NODE_IDS)
        self.assertEqual(
            [node["id"] for node in effective["workflow"]["nodes"]],
            LEAN_NODE_IDS,
        )
        # Inputs whose producer is outside the subset are removed entirely —
        # including optional references like dev.code's PRD.md.
        self.assertEqual(
            effective["workflowDroppedInputs"],
            {
                "dev.specs": ["PRD.md"],
                "dev.code": ["PRD.md", "design.md", "PLAN.md", "frontend-html/**/*"],
                "ops.archive": ["CICD_CHECKLIST.md"],
            },
        )
        code_node = effective["workflow"]["nodes"][1]
        self.assertEqual(
            [artifact["path"] for artifact in code_node["artifacts"]["inputs"]],
            ["proposal.md", "specs/**/*.md"],
        )
        for node in effective["workflow"]["nodes"]:
            for artifact in node["artifacts"]["inputs"]:
                self.assertNotIn("external", artifact)

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

class SolveNodeClosureTest(unittest.TestCase):
    def test_default_keeps_selection_and_drops_with_suggestions(self) -> None:
        result = solve_node_closure(base_config(), ["dev.code"])
        self.assertEqual(result.nodes, ("dev.code",))
        self.assertEqual(result.added, ())
        self.assertEqual(
            result.dropped,
            {
                "dev.code": (
                    "proposal.md",
                    "specs/**/*.md",
                    "PRD.md",
                    "design.md",
                    "PLAN.md",
                    "frontend-html/**/*",
                )
            },
        )
        self.assertEqual(result.entry_nodes, ("dev.code",))
        self.assertEqual(
            result.suggestions,
            {
                "dev.code": {
                    "proposal.md": "dev.specs",
                    "specs/**/*.md": "dev.specs",
                    "PRD.md": "biz.prd",
                    "design.md": "dev.plan",
                    "PLAN.md": "dev.plan",
                }
            },
        )

    def test_default_selecting_prd_drops_discuss_draft(self) -> None:
        result = solve_node_closure(base_config(), ["biz.prd"])
        self.assertEqual(result.nodes, ("biz.prd",))
        self.assertEqual(result.dropped, {"biz.prd": ("PRD_DISCUSS.md",)})
        self.assertEqual(result.suggestions, {"biz.prd": {"PRD_DISCUSS.md": "biz.discuss"}})

    def test_optional_only_drop_is_not_entry_node(self) -> None:
        # With specs+plan selected, dev.code keeps every required input and
        # only loses the optional PRD.md reference — dropped, but not an entry.
        result = solve_node_closure(base_config(), ["dev.specs", "dev.plan", "dev.code"])
        self.assertEqual(result.dropped["dev.code"], ("PRD.md", "frontend-html/**/*"))
        self.assertEqual(result.entry_nodes, ("dev.specs",))

    def test_auto_include_pulls_producers_transitively(self) -> None:
        result = solve_node_closure(base_config(), ["biz.prd"], auto_include_producers=True)
        self.assertEqual(result.nodes, ("biz.discuss", "biz.prd"))
        self.assertEqual(result.added, ("biz.discuss",))
        self.assertEqual(result.dropped, {})
        self.assertEqual(result.suggestions, {})

    def test_closure_result_compiles_as_subset(self) -> None:
        base = base_config()
        result = solve_node_closure(base, ["dev.code"])
        effective = compile_node_subset(base, list(result.nodes))
        self.assertEqual([node["id"] for node in effective["workflow"]["nodes"]], ["dev.code"])
        # Closure preview and subset compilation must agree on what is dropped.
        self.assertEqual(
            {node: tuple(paths) for node, paths in effective["workflowDroppedInputs"].items()},
            result.dropped,
        )

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
        options = {option["id"]: option for option in configured_template_options(base_config())}
        self.assertEqual(list(options)[0], "standard")
        self.assertEqual(options["standard"]["templateType"], "classical")
        self.assertEqual(options["lean"]["templateType"], "nodeSubset")
        self.assertEqual(options["custom"]["templateType"], "custom")
        self.assertEqual(len(options["standard"]["nodes"]), 11)
        self.assertEqual(options["lean"]["nodes"], LEAN_NODE_IDS)
        self.assertEqual(options["custom"]["nodes"], [])
        self.assertEqual(options["custom"]["requiredNodes"], ["dev.code", "ops.archive"])
        self.assertNotIn("requiredNodes", options["standard"])
        self.assertNotIn("requiredNodes", options["lean"])
        self.assertTrue(all("kind" not in option for option in options.values()))

    def test_resolve_template_subset(self) -> None:
        self.assertIsNone(resolve_template_subset(base_config(), "standard"))
        lean = resolve_template_subset(base_config(), "lean")
        self.assertEqual(lean, LEAN_NODE_IDS)
        custom = resolve_template_subset(
            base_config(),
            "custom",
            workflow_nodes=["dev.specs"],
        )
        # custom 强制并集 requiredNodes（必含 dev.code 与 ops.archive）。
        self.assertEqual(custom, ["dev.specs", "dev.code", "ops.archive"])
        baseline = resolve_template_subset(base_config(), "custom")
        self.assertEqual(baseline, ["dev.code", "ops.archive"])
        with self.assertRaises(WorkflowCompileError):
            resolve_template_subset(base_config(), "nope")

    def test_record_contracts_for_lean(self) -> None:
        contracts = load_record_workflow_contracts(
            ROOT,
            {"workflowProfile": "standard", "workflowDecisions": {}, "workflowTemplate": "lean"},
        )
        code = contracts.contract_for_skill("autodev-code")
        self.assertEqual(list(code.required_inputs), ["proposal.md", "specs/**/*.md"])
        # Dropped inputs vanish from the bundle entirely (no optional PRD.md).
        self.assertEqual([artifact.path for artifact in code.inputs], ["proposal.md", "specs/**/*.md"])
        self.assertIn("specs_in_progress", contracts.known_checkpoints)
        self.assertNotIn("plan_in_progress", contracts.known_checkpoints)
        self.assertEqual(contracts.allowed_next["code_done"], frozenset({"archived"}))

    def test_record_contracts_reject_profile_or_decisions_on_lean(self) -> None:
        with self.assertRaises(BoardConfigError):
            load_record_workflow_contracts(
                ROOT,
                {"workflowProfile": "legacy_frontend", "workflowTemplate": "lean"},
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

    def test_custom_record_keeps_nodes_and_discards_legacy_externalized(self) -> None:
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
        # Legacy externalization records load fine but are not carried over:
        # the compiler drops producer-less inputs on its own.
        self.assertNotIn("workflowExternalized", record)

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
