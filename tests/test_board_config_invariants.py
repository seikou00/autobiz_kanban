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
    """Load the Plan entrypoint with the reference it requires before planning."""
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
        index_line = (
            "技能完成后，读取并遵循 "
            "`${pluginPath}/skills/references/ui-continuation-guide.md`。"
        )
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

        skill = (ROOT / "skills" / "autodev" / "autodev-utest" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        first_command = 'python "${pluginPath}/hooks/inspect_skill_contract.py"'
        self.assertIn(execution_rule, skill)
        self.assertIn(first_command, skill)
        self.assertLess(skill.index(execution_rule), skill.index(first_command))
        self.assertIn(
            "`${pluginWorkspace}/${projectDir}` 只作为产物路径或脚本的 `--workspace` 参数",
            skill,
        )
        self.assertIn(
            "仓库根目录与执行目录只使用环境检查器返回值，不作为模型填写的脚本参数",
            skill,
        )
        self.assertIn("每次需要当前状态或 checkpoint 时重新运行该脚本", skill)
        self.assertIn(
            "不得直接读取 `.autobizdevops/state.json`、`.autobizdevops/STATE.md`、"
            "`hooks.ndjson` 或 Feature 目录内的 `.plan.lock`",
            skill,
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
        required_path = 'python "${pluginPath}/skills/autobiz/hooks/biz_validate.py"'
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
        grouping = json.loads((template_dir / "task-groups.json").read_text(encoding="utf-8"))
        self.assertIn("featureId", grouping)
        self.assertEqual(grouping["schemaVersion"], "autodev.plan.v2")
        self.assertEqual(len(grouping["tasks"]), 1)
        self.assertIn("workspace", grouping["tasks"][0])
        self.assertIn("verification", grouping["tasks"][0])
        self.assertNotIn("writeSet", grouping["tasks"][0])

    def test_plan_skill_defines_deterministic_task_writer_protocol(self) -> None:
        content = _plan_skill_docs()
        required = [
            "templates/task-groups.json",
            "autodev.plan.v2",
            "publish-plan",
            "--code-workspace",
            "一次输入即可原子生成",
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

    def test_plan_skill_requires_plan_markdown_projection(self) -> None:
        content = _plan_skill_docs()
        # 钉机制不钉字面：同一条要求给若干可接受写法，命中任一即算满足。
        # 措辞由人把关，测试只保证「PLAN.md 由 plan.json 投影产生」这条主线还在。
        required = [
            ("PLAN.md 投影", ("`PLAN.md` 是 `plan.json` 的人类视图",)),
            ("PLAN.md 落盘", ("同一次发布落盘",)),
        ]
        missing = [
            name for name, variants in required if not any(v in content for v in variants)
        ]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must require PLAN.md human-view projection: " + ", ".join(missing),
        )
        stale_phrases = [
            "optional PLAN.md",
            "可同步生成 `PLAN.md`",
            "若生成 `PLAN.md`",
            "PLAN.md 为可选",
            "PLAN.md 只作可选",
        ]
        offenders = [phrase for phrase in stale_phrases if phrase in content]
        self.assertEqual(offenders, [], "autodev-plan skill must not treat PLAN.md as optional")

    def test_plan_skill_defines_outcome_and_dependency_based_splitting(self) -> None:
        content = _plan_skill_docs()
        required = [
            "refs.requirements",
            "用户或系统可观察的交付结果",
            "真实依赖",
            "不按固定数量阈值拆分",
            "共享仓库、接口或页面不构成合并理由",
            "完整的验证叙事",
            "首次发布前不要预读 writer、门禁或校验器源码",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must keep the pre-write task splitting algorithm: " + ", ".join(missing),
        )

    def test_code_skill_requires_compile_skipped_fixed_workflow(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "workflow_launcher.py",
            "唯一的 Code 启动入口",
            "不得调用 `task_runner.py code-session`",
            "固定 Workflow",
            "batch-compile",
            "原生 Git Worktree",
            "Task Run 的 Git 快照",
            "batchExecutionPlan",
            "展示给用户",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must enforce batch-compile-skipped fixed Workflow execution: " + ", ".join(missing),
        )

    def test_code_skill_requires_same_batch_continuation(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "continue_active_batch",
            "continueCurrentBatch=true",
            "nextTaskId",
            "同批仍有可执行任务时禁止询问用户是否继续",
            "立即进入下一个 Task",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must continue runnable tasks in the active batch: " + ", ".join(missing),
        )

    def test_code_skill_protects_task_runner_snapshot_baseline(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            ".cmbdevclaw/large_tool_results/",
            "task_runner.py\" resume",
            "staging / unstaging",
            "同一个 run",
            "--no-code-change-why",
            "仓库根相对路径",
            "integritySha256",
            "task_run_integrity_mismatch",
            "禁止直接编辑 `plan.json`",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must protect task snapshot baselines: " + ", ".join(missing),
        )

    def test_code_skill_forbids_test_file_changes(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "不得创建或修改测试文件",
            "code_stage_test_changes_forbidden",
            "测试文件变更会被拒绝",
            "后续 UTest/E2E 阶段",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must define transient validation file handling: " + ", ".join(missing),
        )

    def test_code_skill_displays_batch_execution_plan_before_starting_workflow(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "batchExecutionPlan",
            "逐 Batch 列出 ID、标题、TASK 数、执行 lane、代码仓库、依赖和写集",
            "`waves` 仅是 Plan 的兼容预览/审计分组",
            "不是整波屏障",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must display the batch execution plan: " + ", ".join(missing),
        )

    def test_plan_and_code_skills_define_requested_workspace_scope_base(self) -> None:
        plan = _plan_skill_docs()
        code = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        plan_required = []
        code_required = [
            "必须与 task `scope.workspaceRoots` 声明的位置完全一致",
            "`scopePathBase=requested_code_workspace`",
            "`task_run_requested_workspace_mismatch`",
            "DTO/domain/resources/迁移/配置",
            "测试文件变更会被拒绝",
        ]
        missing = [phrase for phrase in plan_required if phrase not in plan]
        missing.extend(phrase for phrase in code_required if phrase not in code)
        self.assertEqual(
            missing,
            [],
            "Plan and Code must share the requested-workspace scope contract: "
            + ", ".join(missing),
        )

    def test_code_exploration_cache_is_not_a_code_output(self) -> None:
        code = next(
            item
            for context, item in _iter_nodes(_board_config())
            if context == "workflow.nodes" and item.get("id") == "dev.code"
        )
        outputs = code["artifacts"]["outputs"]
        self.assertNotIn("cache/code-exploration/**/*.json", [item.get("path") for item in outputs])

    def test_plan_skill_points_batch_resume_to_parallel_scheduler(self) -> None:
        content = _plan_skill_docs()
        self.assertIn("隔离工作树可乐观并行", content)
        self.assertIn("Merge Train", content)
        self.assertNotIn("task_runner.py activate-batch", content)

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
