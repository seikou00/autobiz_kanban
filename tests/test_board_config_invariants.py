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

    def test_plan_stage_keeps_granularity_and_json_validators(self) -> None:
        required_validators = {
            "ui_context_json",
            "plan_json_contract",
            "plan_json_initial_tasks",
            "plan_ref_resolution",
            "plan_task_granularity",
            "plan_scenario_coverage",
            "smoke_test_plan_json",
        }
        offenders: list[str] = []
        for context, node in _iter_nodes(_board_config()):
            if not isinstance(node, dict) or node.get("id") != "dev.plan":
                continue
            validators = node.get("validators", [])
            if not isinstance(validators, list):
                offenders.append(f"{context}[dev.plan]: validators_not_list")
                continue
            missing = sorted(required_validators - set(validators))
            if missing:
                offenders.append(f"{context}[dev.plan]: {','.join(missing)}")
        self.assertEqual(offenders, [], "dev.plan validators drifted: " + ", ".join(offenders))

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
        content = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        required = [
            "templates/task-detail-input.json",
            "templates/task-groups.json",
            "add-task-contract",
            "每次 Plan 会话准备 Draft 前只执行一次",
            ".tmp/plan_writer/draft/plan.json",
            ".tmp/plan_writer/task-groups.json",
            "preflight-task-groups",
            "prepare-task-draft",
            "set-draft-task-detail",
            "preflight-task-draft",
            "finalize-task-draft",
            "rebuild-task-draft",
            "task_group_changed_after_draft_created",
            "失败时不写任何正式产物",
            "禁止使用 `python -c`",
            "不得通过 validator 失败来探索 schema",
            "不得读取 writer 源码来发现参数或枚举值",
            "required AC 覆盖校验",
            "`scope.pages` 由 writer 从分组 UI refs 投影",
            "不得把缺失 Scenario 添加到标题相近",
            "taskSetDigest",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must define the deterministic task writer protocol: " + ", ".join(missing),
        )

    def test_plan_skill_keeps_ui_projection_generation_guidance(self) -> None:
        content = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        required = [
            "templates/task-detail-input.json",
            "不得先自由生成再依赖 validator 反复修字段",
            "UI_CONTEXT.uiRequired=false",
            "UI_CONTEXT.uiRequired=true",
            "UI_CONTEXT.uiRequired=true` 时，只为 UI capability 生成 `uiRequired=true`",
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

    def test_plan_skill_requires_prewrite_task_splitting_algorithm(self) -> None:
        content = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        required = [
            "Plan Task 拆分算法（生成 plan.json 前必走）",
            "一个 task = 一个公开入口 + 一个用户可观察结果 + 一个可运行验证命令",
            "默认先按 vertical slice 拆开",
            "implementationScope",
            "建立 Scenario 覆盖矩阵",
            "SCN / REQ / 用户动作或系统触发 / 可观察结果 / API / Data / Page / UIX / 验证命令或公开 seam",
            "候选任务分组表",
            "不得边补 task detail 边重新拆分",
            "最终候选任务分组表",
            "连续 `T001`、`T002`、`T003`",
            "禁止 `T003a`",
            "完整 specRefs 清单",
            "不同 spec 文件里的同号 `SCN-001` 必须按不同场景分别计数",
            "不得用 `SCN-007~SCN-016`",
            "跨 spec 同号场景必须点名完整路径",
            "拆分结论",
            "需拆分",
            "可合并(附 splitRationale)",
            "splitRationale 草稿",
            "必须先在候选分组表证明共享验证闭环",
            "任务超过软阈值时默认必须继续拆分",
            "SCN `<=5`",
            "SCN `<=12`",
            "SCN 数 `>12`",
            "mergedScenarioRefs",
            "taskGroupMatrixExceptionExample",
            "uiRequiredExample",
            "禁止看到 6-12 个 SCN 就为所有 group 自动补",
            "禁止按连续 SCN 编号机械切块",
            "用户动作 + 公开 seam + 自动化验证边界",
            "只允许一次拆分",
            "不得输出 `v2`、`v3`",
            "最终候选任务分组表不得包含 `拆分结论=需拆分` 的行",
            "基础能力可以单独成 task",
            "validationCommands` 必须验证下游公开 seam",
            "只有共享同一验证闭环时才允许合并",
            "oversized_plan_task_must_split",
            "missing_plan_task_split_rationale",
            "invalid_plan_task_split_rationale",
            "不得通过完整 task 的内容校验失败来探索如何拆分",
            "必须按 DAG 拓扑序编号",
            "`preflight-task-groups` 成功后只运行一次 `prepare-task-draft`",
            "不得创建独立 `Txxx.json`",
            "回 Scenario 覆盖矩阵定位遗漏并重新分组",
            "运行一次 `preflight-task-draft` 和一次 `finalize-task-draft`",
            "分组 digest 变化时运行 `rebuild-task-draft`",
            "对 finalized 计划不原地解封",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-plan skill must keep the pre-write task splitting algorithm: " + ", ".join(missing),
        )

    def test_code_skill_requires_batch_session_entry_and_handoff_stop(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "task_runner.py\" code-session",
            "每次进入 Code 阶段或在新对话恢复 Code 时",
            "execute_active_batch",
            "run_project_check",
            "code_done_ready",
            "stop_and_open_new_conversation",
            "原样输出 `userMessage`",
            "立即结束当前回复",
            "不得在同一对话再次调用 `code-session`",
            "requiresNewConversation",
            "协议层约束",
            "宿主未提供 conversation ID",
            "explorationCaches",
            "full_bounded_explore",
            "task_scope_only",
            "targeted_reread",
            "requiresRecord",
            "requiresPatch",
            "changedPaths + 1-hop 依赖 + 当前 Task scope",
            "fresh/reusable_with_changes 时禁止无边界全仓探索",
            "code_exploration_writer.py",
            "explorationPolicy.status=unavailable",
            "必须停止并补传 `--code-workspace`",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must enforce Code session entry and batch handoff stop: " + ", ".join(missing),
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

    def test_code_skill_defines_transient_validation_files(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "transientValidationFiles",
            "本轮新建",
            "未跟踪且未暂存",
            "`src/test`、`test`、`tests`",
            "不得 `git add`",
            "已跟踪、已暂存或 start 前已存在",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must define transient validation file handling: " + ", ".join(missing),
        )

    def test_code_skill_defines_batch_level_exploration_cache_policy(self) -> None:
        content = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        required = [
            "fresh_with_trusted_changes",
            "同一 active batch",
            "批次边界",
            "shared/integration",
            "deferredCacheUpdate",
            "transientValidationFiles",
        ]
        missing = [phrase for phrase in required if phrase not in content]
        self.assertEqual(
            missing,
            [],
            "autodev-code must define batch-level exploration cache policy: " + ", ".join(missing),
        )

    def test_plan_and_code_skills_define_requested_workspace_scope_base(self) -> None:
        plan = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        code = (ROOT / "skills/autodev/autodev-code/SKILL.md").read_text(encoding="utf-8")
        plan_required = [
            "`scope.workspaceRoots` 由 writer 根据 `prepare-task-draft --code-workspace` 派生",
            "`scope.paths` 只写相对该 workspace 的路径",
            "`validationCommands[].cwd` 保持 Git 根相对路径",
            "domain、test、resources",
            "`repoId:relative/path`",
        ]
        code_required = [
            "必须与 task `scope.workspaceRoots` 声明的位置完全一致",
            "`scopePathBase=requested_code_workspace`",
            "`task_run_requested_workspace_mismatch`",
            "`correct_plan_scope_and_rebuild_task_baseline`",
        ]
        missing = [phrase for phrase in plan_required if phrase not in plan]
        missing.extend(phrase for phrase in code_required if phrase not in code)
        self.assertEqual(
            missing,
            [],
            "Plan and Code must share the requested-workspace scope contract: "
            + ", ".join(missing),
        )

    def test_code_exploration_cache_is_an_optional_code_output(self) -> None:
        code = next(
            item
            for context, item in _iter_nodes(_board_config())
            if context == "workflow.nodes" and item.get("id") == "dev.code"
        )
        outputs = code["artifacts"]["outputs"]
        matches = [item for item in outputs if item.get("path") == "cache/code-exploration/**/*.json"]
        self.assertEqual(len(matches), 1)
        self.assertFalse(matches[0]["required"])

    def test_plan_skill_points_batch_resume_to_code_session(self) -> None:
        content = (ROOT / "skills/autodev/autodev-plan/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("task_runner.py code-session", content)
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
