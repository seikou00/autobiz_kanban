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

from hooks.ui_context import DECISION_SOURCES, DECISION_STATUSES, VISUAL_SOURCE_ROUTES, VISUAL_SOURCE_TYPES  # noqa: E402


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

    def test_machine_stages_do_not_require_markdown_views_when_json_exists(self) -> None:
        self._assert_markdown_views_are_optional(
            {
                "PLAN.md": "plan.json",
                "REQUIREMENTS_EVAL.md": "REVIEW_FINDINGS.json",
                "UNIT_TEST_REPORT.md": "UNIT_TEST_RESULT.json",
                "E2E_REPORT.md": "E2E_RESULT.json",
                "VERIFY_REPORT.md": "VERIFY_DECISION.json",
            }
        )

    def test_machine_stages_do_not_use_markdown_report_validators(self) -> None:
        legacy_markdown_validators = {
            "requirements_eval_verdict",
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

    def test_ui_context_flows_through_downstream_dev_stages(self) -> None:
        required_nodes = {"dev.review", "dev.utest", "dev.e2e", "dev.verify"}
        missing: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict) or node.get("id") not in required_nodes:
                continue
            inputs = (node.get("artifacts") or {}).get("inputs", [])
            ui_input = next(
                (
                    artifact
                    for artifact in inputs
                    if isinstance(artifact, dict) and artifact.get("path") == "UI_CONTEXT.json"
                ),
                None,
            )
            if ui_input is None or ui_input.get("required") is not True:
                missing.append(f"{context}[{node.get('id', '?')}]")
        self.assertEqual(missing, [], "downstream dev stages must read UI_CONTEXT.json: " + ", ".join(missing))

    def test_ui_context_validator_on_downstream_dev_stages(self) -> None:
        required_nodes = {"dev.review", "dev.utest", "dev.e2e", "dev.verify"}
        missing: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict) or node.get("id") not in required_nodes:
                continue
            validators = node.get("validators", [])
            if not isinstance(validators, list) or "ui_context_json" not in validators:
                missing.append(f"{context}[{node.get('id', '?')}]")
        self.assertEqual(missing, [], "downstream dev stages must validate UI_CONTEXT.json: " + ", ".join(missing))

    def test_biz_skills_keep_ui_context_convergence_guidance(self) -> None:
        self.assertTrue(
            (ROOT / "skills/autobiz/references/ui-context.md").is_file(),
            "UI_CONTEXT reference template must be tracked with Biz skills",
        )
        required_phrases = {
            "skills/autobiz/autobiz-requirement-discuss/SKILL.md": [
                "UI_CONTEXT.json",
                "skills/autobiz/references/ui-context.md",
                "uiRequired",
                "页面数",
                "核心交互",
                "空态",
                "错误态",
                "高保真",
                "visualSources[]",
                "capabilities[].specRefs",
                "格式符合 `ui-context.md`",
            ],
            "skills/autobiz/autobiz-prd-generate/SKILL.md": [
                "skills/autobiz/references/ui-context.md",
                "`UI_CONTEXT.json` 是 UI 范围机器事实源",
                "不要从 PRD 正文重新推导",
                "页面数",
                "核心交互",
                "空态",
                "错误态",
                "高保真",
                "visualSources[]",
                "capabilities[].specRefs",
            ],
        }
        missing: list[str] = []
        for relative_path, phrases in required_phrases.items():
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            for phrase in phrases:
                if phrase not in content:
                    missing.append(f"{relative_path}: {phrase}")
        self.assertEqual(
            missing,
            [],
            "Biz discuss/prd skills must keep UI_CONTEXT convergence guidance: " + ", ".join(missing),
        )

    def test_ui_context_reference_tracks_validator_enums(self) -> None:
        content = (ROOT / "skills/autobiz/references/ui-context.md").read_text(encoding="utf-8")
        missing: list[str] = []
        for group_name, values in {
            "decisionStatus": DECISION_STATUSES,
            "decisionSource": DECISION_SOURCES,
            "visualSources.type": VISUAL_SOURCE_TYPES,
            "visualSources.route": VISUAL_SOURCE_ROUTES,
        }.items():
            for value in sorted(values):
                if value not in content:
                    missing.append(f"{group_name}:{value}")
        self.assertEqual(missing, [], "ui-context.md must document validator enum values: " + ", ".join(missing))

    def test_specs_skill_keeps_ui_context_lock_guidance(self) -> None:
        content = (ROOT / "skills/autodev/autodev-specs/SKILL.md").read_text(encoding="utf-8")
        required = [
            "UI_CONTEXT.json",
            "decisionStatus` 固化为 `locked`",
            "必须至少有一个 UI capability",
            "REQ-xxx",
            "SCN-xxx",
            "specRefs",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(missing, [], "autodev-specs must keep UI_CONTEXT lock guidance: " + ", ".join(missing))

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
            "skills/autobiz/autobiz-prd-generate/SKILL.md",
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
            "skills/autobiz/autobiz-prd-generate/SKILL.md",
        ]
        missing = [
            relative_path
            for relative_path in command_docs
            if required_path not in (ROOT / relative_path).read_text(encoding="utf-8")
        ]
        self.assertEqual(missing, [], "Biz skill docs must show the unified biz_validate.py command path")

    def test_autodev_code_frontend_routes_use_references_directory(self) -> None:
        references_root = ROOT / "skills/autodev/autodev-code/references/frontend-html"
        legacy_root = ROOT / "skills/autodev/autodev-code/deps"
        self.assertTrue(references_root.is_dir(), "autodev-code frontend route assets must live under references/")
        self.assertFalse(legacy_root.exists(), "autodev-code must not keep the legacy deps/ route directory")

        banned_patterns = {
            "autodev-code/deps",
            "deps/frontend-html",
            "with-absolute-html/deps",
            "with-standard-html/deps",
            "deps/html-parser",
            "deps/standard-html-parser",
            "route SKILL/deps",
            "当前路线依赖",
        }
        scan_roots = [
            ROOT / "hooks",
            ROOT / "skills/autodev",
            ROOT / "tests",
            ROOT / "docs/ui-json-convergence-adaptation.md",
        ]
        offenders: list[str] = []
        self_path = Path(__file__).resolve()
        for scan_root in scan_roots:
            paths = [scan_root] if scan_root.is_file() else scan_root.rglob("*")
            for path in paths:
                if path.resolve() == self_path:
                    continue
                if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".txt"}:
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
                for pattern in banned_patterns:
                    if pattern in content:
                        offenders.append(f"{path.relative_to(ROOT)}: {pattern}")
        self.assertEqual(offenders, [], "autodev-code route docs/tools must use references/ paths")

    def test_plan_template_keeps_ui_projection_examples(self) -> None:
        template = json.loads(
            (ROOT / "skills/autodev/autodev-plan/templates/plan.json").read_text(encoding="utf-8")
        )
        tasks = template.get("tasks")
        self.assertIsInstance(tasks, list)
        self.assertTrue(tasks, "plan template must include task examples")

        missing_base_fields: list[str] = []
        for task in tasks:
            task_id = task.get("id", "?") if isinstance(task, dict) else "?"
            if not isinstance(task, dict):
                missing_base_fields.append(f"{task_id}: not_object")
                continue
            for field in ("uiRequired", "apiIds", "dataIds"):
                if field not in task:
                    missing_base_fields.append(f"{task_id}: {field}")
        self.assertEqual(
            missing_base_fields,
            [],
            "plan template tasks must show UI and API/DATA fields so first generation matches validators: "
            + ", ".join(missing_base_fields),
        )
        empty_api_data_examples = [
            str(task.get("id", "?"))
            for task in tasks
            if isinstance(task, dict) and task.get("apiIds") == [] and task.get("dataIds") == []
        ]
        self.assertTrue(
            empty_api_data_examples,
            "plan template must include a task example with apiIds/dataIds as empty arrays "
            "for work that does not touch API or data",
        )

        ui_tasks = [task for task in tasks if isinstance(task, dict) and task.get("uiRequired") is True]
        self.assertTrue(ui_tasks, "plan template must include a uiRequired=true task example")
        non_ui_tasks = [
            task
            for task in tasks
            if isinstance(task, dict) and "UI capability" not in str(task.get("title", ""))
        ]
        non_ui_offenders = [
            str(task.get("id", "?"))
            for task in non_ui_tasks
            if task.get("uiRequired") is not False
        ]
        self.assertEqual(
            non_ui_offenders,
            [],
            "non-UI task examples must explicitly set uiRequired=false: " + ", ".join(non_ui_offenders),
        )
        for task in ui_tasks:
            ui_refs = task.get("uiRefs")
            self.assertIsInstance(ui_refs, dict)
            self.assertNotIn("uiRequired", ui_refs, "uiRequired is a task-level field, not nested in uiRefs")
            for field in ("pageRefs", "interactionRefs", "visualSourceRefs"):
                self.assertIsInstance(ui_refs.get(field), list)
            self.assertIn(
                ui_refs.get("frontendRoute"),
                {"none", "spec-driven-ui", "absolute-html", "standard-html", "missing-html"},
            )

    def test_plan_skill_keeps_ui_projection_generation_guidance(self) -> None:
        content = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        required = [
            "templates/plan.json",
            "不得先自由生成再依赖 validator 反复修字段",
            "模板同时包含非 UI task 与 UI task 示例",
            "UI_CONTEXT.uiRequired=false",
            "删除 UI 示例任务",
            "UI_CONTEXT.uiRequired=true",
            "至少一个 `uiRequired:true`",
            "模板中的 API/Data/Decision ID 都是占位示例",
            "不要为了过校验强行编造",
            "空数组 `[]`",
            "x-auto-no-http-api: true",
            "x-auto-no-sql: true",
            "`uiRequired` 是 task 顶层字段",
            "不在 `uiRefs` 内部",
            "禁止原样复制占位 ID",
            "必须显式写 `uiRequired:false`",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must keep first-pass JSON generation guidance: " + ", ".join(missing),
        )

    def test_plan_skill_requires_plan_markdown_projection(self) -> None:
        content = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        required = [
            "plan.json + PLAN.md",
            "本阶段必须生成",
            "PLAN.md` 是从 `plan.json` 投影的人类视图",
            "`PLAN.md` 必须从 `plan.json` 投影",
            "PLAN.md` 文件已写入磁盘",
        ]
        missing = [phrase for phrase in required if phrase not in content]
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
