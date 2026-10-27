"""Guard the input missing-handling invariant.

Every input declared anywhere in board_config.json must declare an extract with
a non-empty degrade, so contract consumers always know how to handle the input
when it is missing. The external flag was removed in favor of drop semantics;
no artifact may declare it again.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PLAN_SKILL = ROOT / "skills" / "autodev" / "autodev-plan" / "SKILL.md"
PLAN_TASK_PLANNING = PLAN_SKILL.parent / "references" / "task-planning.md"


def _plan_skill_docs() -> str:
    """Load the Plan entrypoint and its optional task-boundary examples."""
    return "\n".join((
        PLAN_SKILL.read_text(encoding="utf-8"),
        PLAN_TASK_PLANNING.read_text(encoding="utf-8"),
    ))


def _board_config() -> dict:
    return json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))


def _iter_nodes(config: dict):
    workflow = config.get("workflow", {})
    for node in workflow.get("nodes", []):
        yield "workflow.nodes", node
    for profile_name, profile in (workflow.get("profiles") or {}).items():
        for node in profile.get("nodes", []) if isinstance(profile, dict) else []:
            yield f"workflow.profiles.{profile_name}.nodes", node
    for stage in workflow.get("dynamicStages", []) or []:
        for node in stage.get("nodes", []) if isinstance(stage, dict) else []:
            yield f"workflow.dynamicStages[{stage.get('id', '?')}].nodes", node


def _iter_artifacts(config: dict):
    for context, node in _iter_nodes(config):
        if not isinstance(node, dict):
            continue
        artifacts = node.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        for direction in ("inputs", "outputs"):
            for artifact in artifacts.get(direction, []) or []:
                if isinstance(artifact, dict):
                    yield f"{context}[{node.get('id', '?')}].{direction}", artifact


class BoardConfigInvariantsTest(unittest.TestCase):
    def test_every_input_declares_extract_with_degrade(self) -> None:
        missing: list[str] = []
        for context, artifact in _iter_artifacts(_board_config()):
            if ".inputs" not in context:
                continue
            extract = artifact.get("extract")
            path = artifact.get("path", "?")
            if not isinstance(extract, dict) or not str(extract.get("degrade", "")).strip():
                missing.append(f"{context}: {path}")
        self.assertEqual(
            missing,
            [],
            "every input must declare extract with a non-empty degrade "
            "(missing-handling completeness): " + ", ".join(missing),
        )

    def test_no_artifact_declares_external_flag(self) -> None:
        offenders = [
            f"{context}: {artifact.get('path', '?')}"
            for context, artifact in _iter_artifacts(_board_config())
            if "external" in artifact
        ]
        self.assertEqual(
            offenders,
            [],
            "the external flag was removed (drop semantics); offending artifacts: "
            + ", ".join(offenders),
        )

    def test_workflow_skills_index_shared_completion_guide(self) -> None:
        index_line = "${pluginPath}/skills/references/ui-continuation-guide.md"
        legacy_phrases = (
            "请回到特性面板新开新对话",
            "提醒用户回到特性面板新开对话",
            "如果用户仍在当前对话输入“继续”",
            "若用户随后在当前对话输入“继续”",
        )
        skills = {
            node.get("skill")
            for _, node in _iter_nodes(_board_config())
            if isinstance(node, dict)
            and isinstance(node.get("skill"), str)
            and node["skill"].startswith(("autobiz-", "autodev-", "autoops-"))
        }
        offenders: list[str] = []
        for skill in sorted(skills):
            group = skill.split("-", 1)[0]
            relative_path = Path("skills") / group / skill / "SKILL.md"
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            if content.count(index_line) != 1:
                offenders.append(f"{relative_path}: index_count={content.count(index_line)}")
            for phrase in legacy_phrases:
                if phrase in content:
                    offenders.append(f"{relative_path}: legacy={phrase}")
        self.assertEqual(
            offenders,
            [],
            "workflow skills must keep one shared completion-guide index: "
            + ", ".join(offenders),
        )

        guide = (ROOT / "skills/references/ui-continuation-guide.md").read_text(
            encoding="utf-8"
        )
        for phrase in (
            "## 完成时",
            "## 完成后的续办意图",
            "当前技能仍在执行",
            "不得在当前对话中直接调用下一技能",
            'resolve_next_skill.py" --json',
        ):
            self.assertIn(phrase, guide)

    def test_machine_stages_do_not_require_markdown_views_when_json_exists(self) -> None:
        self._assert_markdown_views_are_optional(
            {
                "PLAN.md": "plan.json",
                "UNIT_TEST_REPORT.md": "UNIT_TEST_RESULT.json",
                "E2E_REPORT.md": "E2E_RESULT.json",
                "VERIFY_REPORT.md": "VERIFY_DECISION.json",
            }
        )

    def test_machine_stages_do_not_use_markdown_report_validators(self) -> None:
        legacy_markdown_validators = {
            "unit_test_report_contract",
            "e2e_report_contract",
            "verify_report_contract",
            "plan_initial_tasks",
        }
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict):
                continue
            validators = node.get("validators", [])
            if not isinstance(validators, list):
                continue
            for validator in validators:
                if validator in legacy_markdown_validators:
                    offenders.append(f"{context}[{node.get('id', '?')}]: {validator}")
        self.assertEqual(
            offenders,
            [],
            "machine workflow must validate JSON facts, not Markdown reports: " + ", ".join(offenders),
        )

    def test_plan_stage_keeps_json_initial_status_gate(self) -> None:
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict):
                continue
            if node.get("id") != "dev.plan":
                continue
            validators = node.get("validators", [])
            if not isinstance(validators, list) or "plan_json_initial_tasks" not in validators:
                offenders.append(f"{context}[dev.plan]")
        self.assertEqual(offenders, [], "dev.plan must keep plan_json_initial_tasks gate")

    def test_design_and_plan_are_separate_workflow_nodes(self) -> None:
        nodes = {
            node.get("id"): node
            for _, node in _iter_nodes(_board_config())
            if isinstance(node, dict)
        }
        design = nodes["dev.design"]
        plan = nodes["dev.plan"]

        self.assertEqual(design.get("skill"), "autodev-design")
        self.assertEqual(design.get("checkpoints"), ["design_in_progress", "design_done"])
        self.assertEqual(
            [item.get("path") for item in design["artifacts"]["outputs"]],
            ["design.md", ".design-contract.lock.json"],
        )
        self.assertIn("design_contract", design.get("validators", []))
        self.assertIn("design_contract_lock", design.get("validators", []))

        self.assertEqual(plan.get("skill"), "autodev-plan")
        self.assertEqual(plan.get("checkpoints"), ["plan_in_progress", "plan_done"])
        self.assertEqual(
            [item.get("path") for item in plan["artifacts"]["outputs"]],
            ["PLAN.md", "plan.json"],
        )
        design_input = next(item for item in plan["artifacts"]["inputs"] if item.get("path") == "design.md")
        self.assertTrue(design_input.get("required"))
        lock_input = next(
            item for item in plan["artifacts"]["inputs"]
            if item.get("path") == ".design-contract.lock.json"
        )
        self.assertTrue(lock_input.get("required"))
        self.assertNotIn("design_contract", plan.get("validators", []))
        self.assertNotIn("plan_ref_resolution", plan.get("validators", []))
        self.assertIn("plan_json_contract", plan.get("validators", []))

    def test_batch_pipeline_stages_are_not_duplicated_as_board_nodes(self) -> None:
        config = _board_config()
        node_ids = {node.get("id") for _, node in _iter_nodes(config)}
        self.assertNotIn("dev.review", node_ids)
        self.assertNotIn("dev.utest", node_ids)
        self.assertNotIn("dev.e2e", node_ids)
        self.assertNotIn("dev.verify", node_ids)
        transitions = config["workflow"]["transitions"]
        transition_ids = {transition["id"] for transition in transitions}
        self.assertEqual(
            {"prd-to-specs", "specs-to-design", "design-to-plan", "plan-to-code", "code-to-cicd", "cicd-to-archive"},
            transition_ids,
        )


    def test_standard_workflow_does_not_depend_on_advisory_smoke_artifacts(self) -> None:
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict):
                continue
            artifacts = node.get("artifacts", {})
            if isinstance(artifacts, dict):
                for direction in ("inputs", "outputs"):
                    for artifact in artifacts.get(direction, []):
                        if isinstance(artifact, dict) and artifact.get("path") in {
                            "SMOKE_TEST_PLAN.json",
                            "SMOKE_RESULT.json",
                        }:
                            offenders.append(
                                f"{context}[{node.get('id', '?')}]: {direction}:{artifact['path']}"
                            )
            validators = node.get("validators", [])
            if isinstance(validators, list):
                for validator in {"smoke_test_plan_json", "smoke_result_json"} & set(validators):
                    offenders.append(f"{context}[{node.get('id', '?')}]: validator:{validator}")
        self.assertEqual(offenders, [], "standard workflow must not depend on advisory smoke: " + ", ".join(offenders))

    def test_code_stage_rejects_legacy_plan_task_schema(self) -> None:
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict) or node.get("id") != "dev.code":
                continue
            validators = node.get("validators", [])
            if (
                not isinstance(validators, list)
                or "plan_task_detail_schema" not in validators
                or "plan_ref_resolution" not in validators
            ):
                offenders.append(f"{context}[dev.code]")
        self.assertEqual(offenders, [], "dev.code must keep plan_task_detail_schema and plan_ref_resolution gates")

    def test_plan_stage_requires_human_plan_view_output(self) -> None:
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict) or node.get("id") != "dev.plan":
                continue
            outputs = (node.get("artifacts") or {}).get("outputs", [])
            plan_output = next(
                (
                    artifact
                    for artifact in outputs
                    if isinstance(artifact, dict) and artifact.get("path") == "PLAN.md"
                ),
                None,
            )
            if plan_output is None or plan_output.get("required") is not True:
                offenders.append(f"{context}[dev.plan]")
        self.assertEqual(offenders, [], "dev.plan must generate PLAN.md as a required human view")

    def test_session_context_inject_passes_target_platform(self) -> None:
        config = _board_config()
        offenders: list[str] = []
        for platform, commands in (config.get("inspectCommands") or {}).items():
            if not isinstance(commands, dict):
                continue
            command = str(commands.get("session_context_inject", ""))
            if f"--platform {platform}" not in command:
                offenders.append(str(platform))
        self.assertEqual(
            offenders,
            [],
            "session_context_inject must pass target platform for path rendering: "
            + ", ".join(offenders),
        )

    def test_plugin_script_cwd_rule_is_consistent_and_precedes_utest_commands(self) -> None:
        execution_rule = "execute/shell 工具请求省略 `cwd` 字段"
        missing_platforms: list[str] = []
        for platform, commands in (_board_config().get("inspectCommands") or {}).items():
            prompt = str(commands.get("system_prompt_inject", ""))
            if execution_rule not in prompt:
                missing_platforms.append(str(platform))
        self.assertEqual(
            missing_platforms,
            [],
            "every platform system prompt must keep the plugin-script cwd rule: "
            + ", ".join(missing_platforms),
        )


    def test_skill_output_sections_never_declare_global_state_files(self) -> None:
        state_files = (".autobizdevops/state.json", ".autobizdevops/STATE.md")
        offenders: list[str] = []
        for skill_path in sorted((ROOT / "skills").rglob("SKILL.md")):
            in_output = False
            output_heading_level = 0
            for line_number, line in enumerate(
                skill_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                heading_marks = len(stripped) - len(stripped.lstrip("#"))
                is_heading = heading_marks > 0 and stripped[heading_marks:].startswith(" ")
                if is_heading:
                    title = stripped[heading_marks:].strip()
                    if "输出" in title:
                        in_output = True
                        output_heading_level = heading_marks
                    elif in_output and heading_marks <= output_heading_level:
                        in_output = False
                    continue
                if stripped in {"输出:", "输出："}:
                    in_output = True
                    output_heading_level = 7
                    continue
                if in_output and any(state_file in stripped for state_file in state_files):
                    offenders.append(
                        f"{skill_path.relative_to(ROOT)}:{line_number}: {stripped}"
                    )
        self.assertEqual(
            offenders,
            [],
            "state.json and STATE.md are runtime state sources, not Skill outputs: "
            + ", ".join(offenders),
        )


    def test_biz_validate_invocation_paths_are_plugin_relative(self) -> None:
        stale_patterns = {
            "python autobiz/hooks/biz_validate.py",
            "${pluginPath}/autobiz/hooks/biz_validate.py",
            "python skills/autobiz/hooks/biz_validate.py",
        }
        required_path = 'python -X utf8 "${pluginPath}/skills/autobiz/hooks/biz_validate.py"'
        files = [
            "skills/autobiz/SKILL.md",
            "skills/autobiz/autobiz-requirement-discuss/SKILL.md",
            "skills/autobiz/hooks/biz_validate.py",
        ]
        offenders: list[str] = []
        for relative_path in files:
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            for pattern in stale_patterns:
                if pattern in content:
                    offenders.append(f"{relative_path}: {pattern}")
        self.assertEqual(offenders, [], "Biz validation commands must use the plugin-relative script path")

        command_docs = [
            "skills/autobiz/SKILL.md",
            "skills/autobiz/autobiz-requirement-discuss/SKILL.md",
        ]
        missing = [
            relative_path
            for relative_path in command_docs
            if required_path not in (ROOT / relative_path).read_text(encoding="utf-8")
        ]
        self.assertEqual(missing, [], "Biz skill docs must show the unified biz_validate.py command path")


    def test_plan_templates_expose_only_current_model_inputs(self) -> None:
        template_dir = ROOT / "skills/autodev/autodev-plan/templates"
        self.assertFalse((template_dir / "plan.json").exists())
        self.assertFalse((template_dir / "batch-plan.json").exists())
        self.assertFalse((template_dir / "task-input.json").exists())
        self.assertFalse((template_dir / "task-detail-input.json").exists())
        grouping = json.loads((template_dir / "plan-input.json").read_text(encoding="utf-8"))
        self.assertIn("featureId", grouping)
        self.assertEqual(grouping["schemaVersion"], "autodev.plan")
        self.assertEqual(len(grouping["tasks"]), 1)
        self.assertIn("workspace", grouping["tasks"][0])
        self.assertIn("verification", grouping["tasks"][0])
        self.assertNotIn("writeSet", grouping["tasks"][0])

    def test_plan_skill_defines_deterministic_task_writer_protocol(self) -> None:
        content = _plan_skill_docs()
        required = [
            "templates/plan-input.json",
            "autodev.plan",
            "prepare-plan",
            "publish-plan",
            "--code-workspace",
            "Critic",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must define the deterministic task writer protocol: " + ", ".join(missing),
        )

    def test_plan_skill_removes_targeted_draft_repair_loop(self) -> None:
        content = _plan_skill_docs()
        self.assertNotIn("set-draft-task-detail", content)
        self.assertNotIn("lint-draft-task-details", content)

    def test_plan_skill_keeps_ui_task_generation_guidance(self) -> None:
        content = _plan_skill_docs()
        required = [
            "UI_CONTEXT.json",
            "UI Task",
            "ui",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must keep first-pass JSON generation guidance: " + ", ".join(missing),
        )








    def test_default_code_material_is_small_and_conditional_references_resolve(self):
        root = ROOT / "skills/autodev/autodev-code"
        self.assertLess((root / "SKILL.md").stat().st_size, 10000)
        for filename in ("task-execution.md", "frontend-route.md", "batch-workflow-guide.md"):
            self.assertTrue((root / "references" / filename).is_file())
        code = next(node for _, node in _iter_nodes(_board_config()) if node.get("id") == "dev.code")
        self.assertEqual(code["runtimePolicy"]["agentMode"], "workflow")

    def test_code_exploration_cache_is_not_a_code_output(self) -> None:
        code = next(
            item
            for context, item in _iter_nodes(_board_config())
            if context == "workflow.nodes" and item.get("id") == "dev.code"
        )
        outputs = code["artifacts"]["outputs"]
        self.assertNotIn("cache/code-exploration/**/*.json", [item.get("path") for item in outputs])


    def _assert_markdown_views_are_optional(self, pairs: dict[str, str]) -> None:
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict):
                continue
            artifacts = node.get("artifacts")
            if not isinstance(artifacts, dict):
                continue
            input_by_path = {
                artifact.get("path"): artifact
                for artifact in artifacts.get("inputs", []) or []
                if isinstance(artifact, dict)
            }
            for markdown_path, json_path in pairs.items():
                markdown = input_by_path.get(markdown_path)
                if markdown is None:
                    continue
                json_input = input_by_path.get(json_path)
                if json_input is None:
                    offenders.append(f"{context}[{node.get('id', '?')}]: {markdown_path} without {json_path}")
                    continue
                if markdown.get("required") is True or json_input.get("required") is not True:
                    offenders.append(
                        f"{context}[{node.get('id', '?')}]: {markdown_path} required={markdown.get('required')} "
                        f"{json_path} required={json_input.get('required')}"
                    )
        self.assertEqual(
            offenders,
            [],
            "Markdown views with JSON counterparts must be optional inputs, "
            "and the JSON counterpart must be required: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
