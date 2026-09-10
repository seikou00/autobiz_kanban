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


    def test_plan_template_has_one_task_input_example(self) -> None:
        template_dir = ROOT / "skills/autodev/autodev-plan/templates"
        template = json.loads((template_dir / "task-input.json").read_text(encoding="utf-8"))
        self.assertFalse((template_dir / "plan.json").exists())
        self.assertFalse((template_dir / "batch-plan.json").exists())
        for field in (
            "id",
            "title",
            "goal",
            "deps",
            "uiRequired",
            "workspaceRef",
            "scope",
            "implementationPoints",
            "acceptanceCriteria",
            "validationBoundary",
            "nonGoals",
            "specRefs",
            "designRefs",
            "apiIds",
            "dataIds",
            "decisionIds",
            "validationCommands",
            "expectedFiles",
            "blockers",
        ):
            self.assertIn(field, template)
        for writer_owned_field in (
            "status",
            "evidenceIds",
            "completionEvidenceIds",
            "latestPassEvidenceId",
            "completionPolicy",
        ):
            self.assertNotIn(writer_owned_field, template)
        self.assertFalse(template["uiRequired"])
        self.assertNotIn("uiRefs", template)
        self.assertTrue(template["nonGoals"])
        self.assertIsInstance(template["validationBoundary"], str)
        self.assertGreaterEqual(len(template["validationBoundary"].strip()), 10)
        self.assertEqual(template["apiIds"], [])
        self.assertEqual(template["dataIds"], [])
        self.assertEqual(template["mergedScenarioRefs"], [])
        detail = json.loads((template_dir / "task-detail-input.json").read_text(encoding="utf-8"))
        self.assertNotIn("id", detail)
        self.assertNotIn("specRefs", detail)
        self.assertNotIn("pages", detail["scope"])
        self.assertNotIn("workspaceRoots", detail["scope"])
        self.assertTrue(detail["nonGoals"])
        self.assertNotIn("id", detail["acceptanceCriteria"][0])
        self.assertNotIn("id", detail["validationCommands"][0])
        grouping = json.loads((template_dir / "task-groups.json").read_text(encoding="utf-8"))
        self.assertIn("featureId", grouping)
        self.assertEqual(len(grouping["groups"]), 1)
        self.assertIn("validationBoundary", grouping["groups"][0])
        self.assertIn("workspaceRef", grouping["groups"][0])
        group_ui_example = grouping["uiRequiredExample"]
        self.assertTrue(group_ui_example["uiRequired"])
        self.assertEqual(
            list(group_ui_example["uiRefs"]),
            ["pageRefs", "interactionRefs", "visualSourceRefs", "frontendRoute"],
        )
        group_exception = grouping["matrixExceptionExample"]
        self.assertEqual(group_exception["mergedScenarioRefs"], group_exception["specRefs"][1:])
        self.assertIn("splitRationale", group_exception)
        self.assertIn("validationBoundary", group_exception)

    def test_plan_skill_defines_deterministic_task_writer_protocol(self) -> None:
        content = _plan_skill_docs()
        required = [
            "templates/task-detail-input.json",
            "templates/task-groups.json",
            "[任务拆分与计划语义](references/task-planning.md)",
            "add-task-contract",
            ".tmp/plan_writer/draft/plan.json",
            ".tmp/plan_writer/task-groups.json",
            "preflight-task-groups",
            "prepare-task-draft",
            "set-draft-task-detail",
            "preflight-task-draft",
            "finalize-task-draft",
            "rebuild-task-draft",
            "workspaceRef",
            "不写任何正式产物",
            "不得直接编辑根 / Batch JSON",
            "不得读取 writer 源码来发现参数或枚举值",
            "详情不得改写 group-owned 字段",
            "scope.pages",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must define the deterministic task writer protocol: " + ", ".join(missing),
        )

    def test_plan_skill_requires_targeted_draft_repair_loop(self) -> None:
        content = _plan_skill_docs()
        required = [
            "validation.issues",
            "validation.invalidTaskIds",
            "repairTarget=task_detail",
            "repairTarget=task_group",
            "repair-draft-task",
            "repair-draft-tasks",
            "删除 `.tmp/plan_writer`",
            "不得为了规避错误删除",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must keep targeted Draft repair semantics: " + ", ".join(missing),
        )

    def test_plan_skill_keeps_ui_task_generation_guidance(self) -> None:
        content = _plan_skill_docs()
        required = [
            "templates/task-detail-input.json",
            "UI_CONTEXT.json",
            "不得按出现顺序新编 PAGE/UIX",
            "不得为了预检虚构 UI task",
            "visualSourceRefs",
            "frontendRoute",
            "空数组 `[]`",
            "uiRequired",
            "uiRefs",
            "uiRequired:false",
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
            ("要求同时产出两份", ("生成 `plan.json`、所有 `plans/Bxxx/plan.json` 和 `PLAN.md`",)),
            ("PLAN.md 是必须产物", ("所有 Batch 计划与 `PLAN.md`",)),
            (
                "PLAN.md 由 plan.json 投影而来",
                ("`PLAN.md` 必须从 `plan.json` 投影", "`PLAN.md` 必须由 `plan_writer.py"),
            ),
            ("PLAN.md 要落盘", ("一次性写入根 `plan.json`、所有 Batch 计划与 `PLAN.md`",)),
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

    def test_plan_skill_requires_prewrite_task_splitting_algorithm(self) -> None:
        content = _plan_skill_docs()
        required = [
            "一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令",
            "vertical slice",
            "implementationScope",
            "SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam",
            "候选任务分组表",
            "不要一边补 task detail 一边重新拆分",
            "连续 `T001`、`T002`、`T003`",
            "完整 specRefs 清单",
            "不同 spec 文件里的同号 `SCN-001` 是不同场景",
            "拆分结论",
            "需拆分",
            "可合并(附 splitRationale)",
            "splitRationale 草稿",
            "SCN `<=5`",
            "SCN `12`",
            "mergedScenarioRefs",
            "用户动作 + 公开 seam + 自动化验证边界",
            "只允许一次拆分",
            "基础能力可以单独成 task",
            "公开 seam",
            "共享同一验证闭环",
            "oversized_plan_task_must_split",
            "missing_plan_task_split_rationale",
            "invalid_plan_task_split_rationale",
            "DAG 拓扑序",
            "回覆盖矩阵定位遗漏并重新分组",
            "rebuild-task-draft",
            "reopen-finalized-draft",
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
            "skip-batch-compile",
            "workflow_batch_compile_disabled",
            "绝不执行 Maven、Gradle、npm build/typecheck、`batch-compile` 或 `revalidate-batch-compile`",
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
            "按 `waves` 展示",
            "实际后续 Wave 只会在上游合并成功后释放",
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
        plan_required = [
            "`scope.workspaceRoots` 由 writer 根据 `prepare-task-draft --code-workspace` 派生",
            "`scope.paths` 只写相对该 workspace 的提示性路径",
            "`validationCommands[].cwd` 保持 Git 根相对路径",
            "`repoId:relative/path`",
        ]
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
        self.assertIn("未知写集保守串行", content)
        self.assertIn("依赖不得前向或跨批成环", content)
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
