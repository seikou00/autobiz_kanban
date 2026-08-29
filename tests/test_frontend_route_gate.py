#!/usr/bin/env python3
"""Regression tests for the autodev-code frontend route gate."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "hooks", ROOT / "skills" / "autodev" / "hooks"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import check_plugin_read  # noqa: E402
import frontend_route_write_guard  # noqa: E402
from artifact_check import validate_frontend_route_gate  # noqa: E402
from board_core.state_store import state_json_content_from_records  # noqa: E402
from common import HookContext  # noqa: E402
from hooks.resolve_frontend_html_route import (  # noqa: E402
    FrontendRouteError,
    PARSERS,
    ROUTE_ABSOLUTE,
    ROUTE_MISSING,
    ROUTE_NONE,
    ROUTE_SKILLS,
    ROUTE_SPEC_DRIVEN,
    ROUTE_STANDARD,
    evidence_path,
    mark_evidence,
    read_json,
    resolve_frontend_route,
    write_json,
)
from hooks.task_run_integrity import task_run_integrity_sha256  # noqa: E402


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    feature_dir.mkdir(parents=True)
    state = {
        "alpha": {
            "feature": "alpha",
            "owner": "owner",
            "checkpoint": "code_in_progress",
            "stage": "",
            "iteration": "1",
            "updated_at": "2026-06-29 12:00:00",
        }
    }
    (workspace / ".autobizdevops" / "state.json").write_text(
        state_json_content_from_records(state, workspace=workspace),
        encoding="utf-8",
    )
    return workspace


def write_feature_file(workspace: Path, name: str, content: str) -> Path:
    path = workspace / ".autobizdevops" / "features" / "alpha" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_ui_context(workspace: Path, payload: dict) -> None:
    write_json(workspace / ".autobizdevops" / "features" / "alpha" / "UI_CONTEXT.json", payload)


def write_plan_route(
    workspace: Path,
    route: str,
    *,
    visual_source_refs: list[str] | None = None,
) -> None:
    feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
    write_json(
        feature_dir / "plan.json",
        {
            "featureId": "alpha",
            "status": "todo",
            "taskSetStatus": "finalized",
            "activeBatchId": "B001",
            "nextBatchId": None,
            "batchPolicy": {"maxTasks": 5, "strategy": "spec_capability_execution_lane_topological"},
            "batches": [
                {
                    "id": "B001",
                    "path": "plans/B001/plan.json",
                    "title": "ui",
                    "specRoots": ["specs/cap/spec.md"],
                    "executionLane": "frontend",
                    "deps": [],
                    "taskIds": ["T001"],
                    "status": "todo",
                }
            ],
            "projectValidationCommands": [],
            "projectCheckEvidenceIds": [],
            "latestProjectCheckEvidenceId": None,
        },
    )
    write_json(
        feature_dir / "plans" / "B001" / "plan.json",
        {
            "featureId": "alpha",
            "batchId": "B001",
            "title": "ui",
            "executionLane": "frontend",
            "status": "todo",
            "taskCount": 1,
            "completedTaskCount": 0,
            "completionEvidenceIds": [],
            "startedAt": None,
            "completedAt": None,
            "tasks": [
                {
                    "id": "T001",
                    "title": "ui",
                    "status": "todo",
                    "deps": [],
                    "uiRequired": True,
                    "uiRefs": {
                        "pageRefs": ["PAGE-001"],
                        "interactionRefs": ["UIX-001"],
                        "visualSourceRefs": (
                            visual_source_refs
                            if visual_source_refs is not None
                            else ([] if route in {ROUTE_NONE, ROUTE_SPEC_DRIVEN} else ["VIS-001"])
                        ),
                        "frontendRoute": route,
                    },
                    "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
                    "designRefs": ["design.md#D-001"],
                    "apiIds": [],
                    "dataIds": [],
                    "decisionIds": ["D-001"],
                    "validationCommands": [{"command": "echo ok"}],
                    "expectedFiles": [],
                    "evidenceIds": [],
                    "blockers": [],
                }
            ],
        },
    )


def base_ui_context(*, ui_required: bool = True) -> dict:
    return {
        "version": 1,
        "featureId": "alpha",
        "uiRequired": ui_required,
        "decisionStatus": "locked",
        "decisionSource": "user_confirmed" if ui_required else "default_false",
        "confirmedAtCheckpoint": "prd_done",
        "lockedAtCheckpoint": "specs_done",
        "notApplicableReason": "" if ui_required else "纯后端能力",
        "pages": [
            {"pageId": "PAGE-001", "name": "页面", "goal": "展示能力", "states": ["success"]}
        ] if ui_required else [],
        "interactions": [
            {"interactionId": "UIX-001", "pageId": "PAGE-001", "summary": "点击提交"}
        ] if ui_required else [],
        "visualSources": [],
        "capabilities": [
            {
                "capabilityId": "alpha-ui",
                "uiRequired": True,
                "pageRefs": ["PAGE-001"],
                "interactionRefs": ["UIX-001"],
                "visualSourceRefs": [],
                "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
            }
        ] if ui_required else [],
    }


def gate_context(workspace: Path) -> HookContext:
    return HookContext(skill="autodev-code", slug="alpha", root=workspace)


def complete_evidence(route: str) -> dict:
    return {
        "version": 1,
        "feature": "alpha",
        "triggered": True,
        "route": route,
        "routeSkillPath": str(ROUTE_SKILLS[route]),
        "parserPath": str(PARSERS[route]),
        "routeSkillRead": True,
        "routeSkillReadComplete": True,
        "routeTodosCreated": True,
        "routeTodosCompleted": True,
        "parserRead": True,
        "reviewStatus": "passed",
    }


def sealed_task_run(*, execution_mode: str = "code") -> dict:
    run = {
        "version": 2,
        "runId": "run-1",
        "featureId": "alpha",
        "batchId": "B001",
        "taskId": "T001",
        "taskContractSha256": "contract",
        "executionMode": execution_mode,
        "status": "started",
        "codeWorkspace": "/tmp/code",
        "requestedCodeWorkspaces": ["/tmp/code"],
        "resolvedGitRoots": ["/tmp/code"],
        "workspacePrefixes": [""],
        "scopeWorkspaces": [{"repository": "code", "resolvedGitRoot": "/tmp/code"}],
        "scopePathBase": "requested_code_workspace",
        "declaredScopePaths": [],
        "resolvedScopePaths": [],
        "repositories": [{"id": "code", "path": "/tmp/code", "snapshot": {}}],
        "snapshotMode": "git_visible_file_content_sha256",
        "stagingAffectsSnapshot": False,
        "startedAt": "2026-07-30T00:00:00Z",
        "snapshot": {},
    }
    run["integritySha256"] = task_run_integrity_sha256(run)
    return run


def write_task_run(workspace: Path, run: dict) -> Path:
    path = (
        workspace
        / ".autobizdevops"
        / "features"
        / "alpha"
        / ".task-runs"
        / "T001"
        / "run-1.json"
    )
    write_json(path, run)
    return path


class FrontendRouteResolverTests(unittest.TestCase):
    def test_ui_context_false_resolves_none_even_if_markdown_mentions_frontend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_ui_context(workspace, base_ui_context(ui_required=False))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["source"], "UI_CONTEXT.json")
        self.assertEqual(payload["route"], ROUTE_NONE)
        self.assertFalse(payload["uiRequired"])

    def test_code_task_run_write_blocks_without_active_task_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            output = io.StringIO()
            error = io.StringIO()

            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_code_task_run_write(
                        workspace,
                        "alpha",
                    )

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("exactly one active task run", error.getvalue())
        self.assertIn("block", output.getvalue())

    def test_code_task_run_write_allows_sealed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_task_run(workspace, sealed_task_run())

            result = frontend_route_write_guard.validate_code_task_run_write(
                workspace,
                "alpha",
            )

        self.assertEqual(result, 0)

    def test_code_task_run_write_rejects_legacy_run_with_repair_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            run = sealed_task_run()
            run["version"] = 1
            run["repairContext"] = {}
            run["integritySha256"] = task_run_integrity_sha256(run)
            write_task_run(workspace, run)
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                result = frontend_route_write_guard.validate_code_task_run_write(
                    workspace,
                    "alpha",
                )

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("task_run_version_invalid", error.getvalue())

    def test_code_task_run_write_rejects_missing_task_id_with_repair_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            run = sealed_task_run()
            run["taskId"] = None
            run["repairContext"] = {}
            run["integritySha256"] = task_run_integrity_sha256(run)
            write_task_run(workspace, run)
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                result = frontend_route_write_guard.validate_code_task_run_write(
                    workspace,
                    "alpha",
                )

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("task_run_taskId_invalid", error.getvalue())

    def test_non_code_execution_mode_reports_source_writes_are_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_task_run(workspace, sealed_task_run(execution_mode="verified_existing"))
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                result = frontend_route_write_guard.validate_code_task_run_write(
                    workspace,
                    "alpha",
                )

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("executionMode=verified_existing", error.getvalue())

    def test_managed_task_run_paths_are_runner_owned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            run_path = (
                workspace
                / ".autobizdevops"
                / "features"
                / "alpha"
                / ".task-runs"
                / "T001"
                / "forged.json"
            )

            self.assertTrue(
                frontend_route_write_guard.is_managed_task_run_path(
                    run_path,
                    workspace,
                    "alpha",
                )
            )

    def test_backend_source_paths_use_business_code_write_gate(self) -> None:
        self.assertTrue(frontend_route_write_guard.is_business_code_path(Path("Service.java")))
        self.assertTrue(frontend_route_write_guard.is_business_code_path(Path("mapper.xml")))
        self.assertFalse(frontend_route_write_guard.is_frontend_code_path(Path("Service.java")))

    def test_ui_context_required_without_html_resolves_spec_driven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_ui_context(workspace, base_ui_context(ui_required=True))

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["source"], "UI_CONTEXT.json")
        self.assertEqual(payload["route"], ROUTE_SPEC_DRIVEN)
        self.assertTrue(payload["uiRequired"])

    def test_invalid_ui_context_does_not_fallback_to_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "UI_CONTEXT.json", "{")
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")

            with self.assertRaises(FrontendRouteError):
                resolve_frontend_route(workspace, "alpha", write_evidence=True)

    def test_ui_context_high_fidelity_html_resolves_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            html_path = write_feature_file(
                workspace,
                "frontend-html/page.html",
                """
                <div style="position:absolute; left:10px; top:10px; width:120px; height:40px"></div>
                <div style="position:absolute; left:20px; top:60px; width:120px; height:40px"></div>
                <div style="position:absolute; left:30px; top:110px; width:120px; height:40px"></div>
                """,
            )
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "high_fidelity_html",
                    "path": str(html_path),
                    "route": ROUTE_ABSOLUTE,
                    "required": True,
                }
            ]
            context["capabilities"][0]["visualSourceRefs"] = ["VIS-001"]
            write_ui_context(workspace, context)
            write_plan_route(workspace, ROUTE_ABSOLUTE)

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["route"], ROUTE_ABSOLUTE)
        self.assertEqual(payload["visualSourceIds"], ["VIS-001"])
        self.assertEqual(payload["taskSourceBindings"]["T001"]["visualSourceIds"], ["VIS-001"])
        self.assertEqual(payload["taskSourceBindings"]["T001"]["htmlSourcePaths"], [str(html_path.resolve())])

    def test_ui_context_visual_route_overrides_plan_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            html_path = write_feature_file(
                workspace,
                "frontend-html/page.html",
                """
                <div style="position:absolute; left:10px; top:10px; width:120px; height:40px"></div>
                <div style="position:absolute; left:20px; top:60px; width:120px; height:40px"></div>
                <div style="position:absolute; left:30px; top:110px; width:120px; height:40px"></div>
                """,
            )
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "high_fidelity_html",
                    "path": str(html_path),
                    "route": ROUTE_ABSOLUTE,
                    "required": True,
                }
            ]
            write_ui_context(workspace, context)
            write_plan_route(workspace, ROUTE_STANDARD)

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["route"], ROUTE_ABSOLUTE)
        self.assertIn("plan.json route overridden by HTML/UI_CONTEXT evidence", payload["reasons"])

    def test_absolute_html_classification_overrides_plan_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            html_path = write_feature_file(
                workspace,
                "frontend-html/page.html",
                """
                <div style="position:absolute; left:10px; top:10px; width:120px; height:40px"></div>
                <div style="position:absolute; left:20px; top:60px; width:120px; height:40px"></div>
                <div style="position:absolute; left:30px; top:110px; width:120px; height:40px"></div>
                <div style="position:absolute; left:40px; top:160px; width:120px; height:40px"></div>
                """,
            )
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "other",
                    "path": str(html_path),
                    "required": True,
                }
            ]
            write_ui_context(workspace, context)
            write_plan_route(workspace, ROUTE_STANDARD)

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["route"], ROUTE_ABSOLUTE)
        self.assertIn("position:absolute count=4", payload["reasons"])
        self.assertIn("plan.json route overridden by HTML/UI_CONTEXT evidence", payload["reasons"])

    def test_required_plan_html_route_without_readable_html_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [{
                "sourceId": "VIS-001",
                "type": "high_fidelity_html",
                "path": "frontend-html/missing.html",
                "route": ROUTE_ABSOLUTE,
                "required": True,
            }]
            write_ui_context(workspace, context)
            write_plan_route(workspace, ROUTE_ABSOLUTE)

            with self.assertRaisesRegex(FrontendRouteError, "required_visual_source_missing:VIS-001"):
                resolve_frontend_route(workspace, "alpha", write_evidence=True)

    def test_explicit_required_html_route_without_readable_html_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "high_fidelity_html",
                    "path": "frontend-html/missing.html",
                    "route": ROUTE_ABSOLUTE,
                    "required": True,
                }
            ]
            write_ui_context(workspace, context)

            with self.assertRaisesRegex(FrontendRouteError, "required_visual_source_missing:VIS-001"):
                resolve_frontend_route(workspace, "alpha", write_evidence=True)

    def test_optional_missing_high_fidelity_fallback_allows_code_done_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "high_fidelity_html",
                    "path": "frontend-html/missing.html",
                    "route": ROUTE_ABSOLUTE,
                    "required": False,
                }
            ]
            write_ui_context(workspace, context)
            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)
            payload["reviewStatus"] = "passed"
            write_json(evidence_path(workspace, "alpha"), payload)

            failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertEqual(failures, 0)

    def test_direct_html_file_does_not_replace_missing_required_bound_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            html_path = Path(tmp) / "provided.html"
            html_path.write_text("<div style='position:absolute; left:1px; top:2px'>OK</div>", encoding="utf-8")
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "high_fidelity_html",
                    "path": "frontend-html/missing.html",
                    "route": ROUTE_ABSOLUTE,
                    "required": True,
                }
            ]
            write_ui_context(workspace, context)

            with self.assertRaisesRegex(FrontendRouteError, "required_visual_source_missing:VIS-001"):
                resolve_frontend_route(workspace, "alpha", html_files=[str(html_path)], write_evidence=True)

    def test_spec_driven_task_ignores_unrelated_required_high_fidelity_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [{
                "sourceId": "VIS-001",
                "type": "high_fidelity_html",
                "path": "frontend-html/missing.html",
                "route": ROUTE_ABSOLUTE,
                "required": True,
            }]
            write_ui_context(workspace, context)
            write_plan_route(workspace, ROUTE_SPEC_DRIVEN, visual_source_refs=[])

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["route"], ROUTE_SPEC_DRIVEN)
        self.assertEqual(payload["visualSourceIds"], [])
        self.assertEqual(payload["taskSourceBindings"]["T001"]["htmlSourcePaths"], [])

    def test_required_archived_html_digest_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            html_path = write_feature_file(workspace, "frontend-html/VIS-001/index.html", "<main>before</main>")
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [{
                "sourceId": "VIS-001",
                "type": "high_fidelity_html",
                "path": "frontend-html/VIS-001/index.html",
                "route": ROUTE_ABSOLUTE,
                "required": True,
                "contentSha256": hashlib.sha256(b"<main>before</main>").hexdigest(),
            }]
            write_ui_context(workspace, context)
            write_plan_route(workspace, ROUTE_ABSOLUTE)
            html_path.write_text("<main>after</main>", encoding="utf-8")

            with self.assertRaisesRegex(FrontendRouteError, "required_visual_source_digest_mismatch:VIS-001"):
                resolve_frontend_route(workspace, "alpha", write_evidence=True)

    def test_legacy_missing_html_from_ui_context_allows_code_done_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_MISSING,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": ["VIS-001"],
                    "htmlSourcePaths": [],
                    "htmlSourceMissing": True,
                    "missingHtmlSourcePaths": [],
                    "reasons": [],
                    "docPaths": [],
                    "reviewStatus": "passed",
                },
            )

            failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertEqual(failures, 0)

    def test_absolute_html_is_classified_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(
                workspace,
                "frontend-html/page.html",
                """
                <div style="position:absolute; left:10px; top:10px; width:120px; height:40px"></div>
                <div style="position:absolute; left:20px; top:60px; width:120px; height:40px"></div>
                <div style="position:absolute; left:30px; top:110px; width:120px; height:40px"></div>
                <div style="position:absolute; left:40px; top:160px; width:120px; height:40px"></div>
                """,
            )

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)
            stored = read_json(evidence_path(workspace, "alpha"))

        self.assertEqual(payload["route"], ROUTE_ABSOLUTE)
        self.assertEqual(stored["route"], ROUTE_ABSOLUTE)
        self.assertTrue(stored["routeSkillPath"].endswith("with-absolute-html\\SKILL.md") or stored["routeSkillPath"].endswith("with-absolute-html/SKILL.md"))

    def test_standard_html_is_classified_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(
                workspace,
                "frontend-html/page.html",
                """
                <form class="search-panel" style="display:flex">
                  <label>名称<input name="name" /></label>
                  <button type="submit">查询</button>
                </form>
                <table class="result-table"><tr><td>数据</td></tr></table>
                """,
            )

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["route"], ROUTE_STANDARD)


    def test_write_evidence_preserves_route_run_flags_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "need HTML frontend\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")
            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)
            payload["routeSkillRead"] = True
            payload["routeSkillReadComplete"] = True
            payload["routeTodosCreated"] = True
            payload["routeTodosCompleted"] = True
            payload["parserRead"] = True
            payload["reviewStatus"] = "passed"
            payload["reviewRouteRunId"] = payload["routeRunId"]
            write_json(evidence_path(workspace, "alpha"), payload)

            recovered = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(recovered["routeRunId"], payload["routeRunId"])
        self.assertTrue(recovered["routeSkillReadComplete"])
        self.assertTrue(recovered["parserRead"])
        self.assertEqual(recovered["reviewStatus"], "passed")

    def test_start_route_run_resets_flags_and_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "need HTML frontend\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")
            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)
            payload["routeSkillRead"] = True
            payload["routeSkillReadComplete"] = True
            payload["routeTodosCreated"] = True
            payload["routeTodosCompleted"] = True
            payload["parserRead"] = True
            payload["reviewStatus"] = "passed"
            payload["reviewRouteRunId"] = payload["routeRunId"]
            write_json(evidence_path(workspace, "alpha"), payload)

            fresh = resolve_frontend_route(workspace, "alpha", write_evidence=True, start_route_run=True)

        self.assertNotEqual(fresh["routeRunId"], payload["routeRunId"])
        self.assertFalse(fresh["routeSkillRead"])
        self.assertFalse(fresh["routeSkillReadComplete"])
        self.assertFalse(fresh["routeTodosCreated"])
        self.assertFalse(fresh["routeTodosCompleted"])
        self.assertFalse(fresh["parserRead"])
        self.assertNotIn("reviewStatus", fresh)
        self.assertNotIn("reviewRouteRunId", fresh)

    def test_route_change_resets_route_run_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            html_path = write_feature_file(
                workspace,
                "frontend-html/page.html",
                """
                <div style="position:absolute; left:10px; top:10px; width:120px; height:40px"></div>
                <div style="position:absolute; left:20px; top:60px; width:120px; height:40px"></div>
                <div style="position:absolute; left:30px; top:110px; width:120px; height:40px"></div>
                """,
            )
            context = base_ui_context(ui_required=True)
            context["visualSources"] = [
                {
                    "sourceId": "VIS-001",
                    "type": "high_fidelity_html",
                    "path": str(html_path),
                    "route": ROUTE_ABSOLUTE,
                    "required": True,
                }
            ]
            write_ui_context(workspace, context)
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_STANDARD),
                    "routeRunId": "rr_old",
                    "reviewRouteRunId": "rr_old",
                },
            )

            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)

        self.assertEqual(payload["route"], ROUTE_ABSOLUTE)
        self.assertNotEqual(payload["routeRunId"], "rr_old")
        self.assertFalse(payload["routeSkillReadComplete"])
        self.assertFalse(payload["parserRead"])
        self.assertNotIn("reviewStatus", payload)

    def test_parser_read_mark_requires_skill_and_todos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "need HTML frontend\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")
            resolve_frontend_route(workspace, "alpha", write_evidence=True, start_route_run=True)

            with self.assertRaisesRegex(ValueError, "routeSkillReadComplete"):
                mark_evidence(workspace, "alpha", mark="parser-read")
            mark_evidence(workspace, "alpha", mark="route-skill-read-complete")
            with self.assertRaisesRegex(ValueError, "routeTodosCreated"):
                mark_evidence(workspace, "alpha", mark="parser-read")
            mark_evidence(workspace, "alpha", mark="route-todos-created")
            payload = mark_evidence(workspace, "alpha", mark="parser-read")

        self.assertTrue(payload["parserRead"])


class FrontendRouteGateValidatorTests(unittest.TestCase):
    def test_spec_driven_ui_allows_code_done_without_html_protocol_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_SPEC_DRIVEN,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": [],
                    "htmlSourcePaths": [],
                    "reasons": ["UI_CONTEXT uiRequired without HTML visual source"],
                    "docPaths": [],
                    "reviewStatus": "passed",
                },
            )

            failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertEqual(failures, 0)

    def test_non_ui_context_ignores_stale_frontend_route_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_ui_context(workspace, base_ui_context(ui_required=False))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_SPEC_DRIVEN,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": [],
                    "htmlSourcePaths": [],
                    "reasons": ["stale evidence from an earlier UI run"],
                    "docPaths": [],
                },
            )

            failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertEqual(failures, 0)

    def test_spec_driven_ui_requires_frontend_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_SPEC_DRIVEN,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": [],
                    "htmlSourcePaths": [],
                    "reasons": ["UI_CONTEXT uiRequired without HTML visual source"],
                    "docPaths": [],
                },
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertGreater(failures, 0)
        self.assertIn("frontend_review_not_passed_or_skipped", output.getvalue())

    def test_missing_evidence_blocks_frontend_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertEqual(failures, 1)
        self.assertIn("missing_frontend_route_evidence", output.getvalue())

    def test_incomplete_route_todos_block_code_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")
            payload = resolve_frontend_route(workspace, "alpha", write_evidence=True)
            payload["routeSkillRead"] = True
            payload["routeSkillReadComplete"] = True
            payload["parserRead"] = True
            payload["reviewStatus"] = "passed"
            write_json(evidence_path(workspace, "alpha"), payload)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertGreaterEqual(failures, 2)
        self.assertIn("frontend_route_routeTodosCreated_missing", output.getvalue())
        self.assertIn("frontend_route_routeTodosCompleted_missing", output.getvalue())

    def test_complete_evidence_allows_code_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_json(evidence_path(workspace, "alpha"), complete_evidence(ROUTE_STANDARD))

            failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertEqual(failures, 0)


    def test_review_status_must_match_current_route_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_STANDARD),
                    "routeRunId": "rr_new",
                    "reviewStatus": "passed",
                    "reviewRouteRunId": "rr_old",
                },
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                failures = validate_frontend_route_gate(gate_context(workspace))

        self.assertGreater(failures, 0)
        self.assertIn("frontend_review_route_run_mismatch", output.getvalue())


class FrontendRouteReadHookTests(unittest.TestCase):
    def test_parser_read_is_blocked_until_route_todos_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_ABSOLUTE),
                    "routeTodosCreated": False,
                    "parserRead": False,
                },
            )

            output = io.StringIO()
            error = io.StringIO()
            with mock.patch.dict(os.environ, {"FEATURE_ID": "alpha"}):
                with contextlib.redirect_stdout(output):
                    with contextlib.redirect_stderr(error):
                        result = check_plugin_read.enforce_frontend_route_reads(
                            {"tool_input": {}},
                            [PARSERS[ROUTE_ABSOLUTE]],
                            workspace,
                        )
            stored = read_json(evidence_path(workspace, "alpha"))

        self.assertEqual(result, check_plugin_read.BLOCK_EXIT_CODE)
        self.assertFalse(stored["parserRead"])
        self.assertIn("block", output.getvalue())

    def test_parser_read_marks_evidence_after_route_todos_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_ABSOLUTE),
                    "parserRead": False,
                },
            )

            with mock.patch.dict(os.environ, {"FEATURE_ID": "alpha"}):
                result = check_plugin_read.enforce_frontend_route_reads(
                    {"tool_input": {}},
                    [PARSERS[ROUTE_ABSOLUTE]],
                    workspace,
                )
            stored = read_json(evidence_path(workspace, "alpha"))

        self.assertEqual(result, 0)
        self.assertTrue(stored["parserRead"])

    def test_route_skill_read_requires_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_STANDARD),
                    "routeSkillRead": False,
                    "routeSkillReadComplete": False,
                },
            )

            with mock.patch.dict(os.environ, {"FEATURE_ID": "alpha"}):
                first = check_plugin_read.enforce_frontend_route_reads(
                    {"tool_input": {"offset": 0, "limit": 10}},
                    [ROUTE_SKILLS[ROUTE_STANDARD]],
                    workspace,
                )
                partial = read_json(evidence_path(workspace, "alpha"))
                second = check_plugin_read.enforce_frontend_route_reads(
                    {"tool_input": {}},
                    [ROUTE_SKILLS[ROUTE_STANDARD]],
                    workspace,
                )
                complete = read_json(evidence_path(workspace, "alpha"))

        self.assertEqual(first, 0)
        self.assertTrue(partial["routeSkillRead"])
        self.assertFalse(partial["routeSkillReadComplete"])
        self.assertEqual(second, 0)
        self.assertTrue(complete["routeSkillReadComplete"])

    def test_html_read_blocks_when_ui_context_false_even_with_stale_html_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_ui_context(workspace, base_ui_context(ui_required=False))
            write_json(evidence_path(workspace, "alpha"), complete_evidence(ROUTE_ABSOLUTE))

            output = io.StringIO()
            error = io.StringIO()
            with mock.patch.dict(os.environ, {"FEATURE_ID": "alpha"}):
                with contextlib.redirect_stdout(output):
                    with contextlib.redirect_stderr(error):
                        result = check_plugin_read.enforce_html_read(workspace, "alpha")

        self.assertEqual(result, check_plugin_read.BLOCK_EXIT_CODE)
        self.assertIn("uiRequired=false", error.getvalue())
        self.assertIn("block", output.getvalue())


class FrontendRouteWriteGuardTests(unittest.TestCase):
    def test_main_blocks_direct_task_run_json_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            forged_path = (
                workspace
                / ".autobizdevops"
                / "features"
                / "alpha"
                / ".task-runs"
                / "T001"
                / "forged.json"
            )
            payload = json.dumps({"tool_input": {"file_path": str(forged_path)}})
            error = io.StringIO()

            with mock.patch.dict(os.environ, {"FEATURE_ID": "alpha"}):
                with mock.patch.object(frontend_route_write_guard, "read_stdin_text", return_value=payload):
                    with mock.patch.object(
                        frontend_route_write_guard,
                        "workspace_from_payload",
                        return_value=workspace,
                    ):
                        with contextlib.redirect_stderr(error):
                            result = frontend_route_write_guard.main()

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("runner-owned", error.getvalue())

    def test_main_blocks_task_run_write_without_feature_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            forged_path = (
                workspace
                / ".autobizdevops"
                / "features"
                / "alpha"
                / ".task-runs"
                / "T001"
                / "forged.json"
            )
            payload = json.dumps({"tool_input": {"file_path": str(forged_path)}})

            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(frontend_route_write_guard, "read_stdin_text", return_value=payload):
                    result = frontend_route_write_guard.main()

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)

    def test_main_applies_task_run_gate_to_backend_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            payload = json.dumps({"tool_input": {"file_path": "/tmp/Service.java"}})
            error = io.StringIO()

            with mock.patch.dict(os.environ, {"FEATURE_ID": "alpha"}):
                with mock.patch.object(frontend_route_write_guard, "read_stdin_text", return_value=payload):
                    with mock.patch.object(
                        frontend_route_write_guard,
                        "workspace_from_payload",
                        return_value=workspace,
                    ):
                        with contextlib.redirect_stderr(error):
                            result = frontend_route_write_guard.main()

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("business code write requires exactly one active", error.getvalue())

    def test_frontend_write_allows_spec_driven_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_SPEC_DRIVEN,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": [],
                    "htmlSourcePaths": [],
                    "reasons": [],
                    "docPaths": [],
                },
            )

            result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, 0)

    def test_frontend_write_blocks_when_ui_context_false_even_with_stale_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_ui_context(workspace, base_ui_context(ui_required=False))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_SPEC_DRIVEN,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": [],
                    "htmlSourcePaths": [],
                    "reasons": ["stale evidence from an earlier UI run"],
                    "docPaths": [],
                },
            )

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("uiRequired=false", error.getvalue())

    def test_frontend_write_allows_missing_high_fidelity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_SPEC_DRIVEN,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": ["VIS-001"],
                    "htmlSourcePaths": [],
                    "htmlSourceMissing": True,
                    "missingHtmlSourcePaths": [
                        str(
                            workspace
                            / ".autobizdevops"
                            / "features"
                            / "alpha"
                            / "frontend-html"
                            / "missing.html"
                        )
                    ],
                    "htmlRequestMessage": "请先引导用户提供 HTML 文件；如果用户不提供，本轮按 spec-driven-ui 继续。",
                    "htmlFallbackRoute": ROUTE_SPEC_DRIVEN,
                    "reasons": [],
                    "docPaths": [],
                },
            )

            result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, 0)

    def test_frontend_write_allows_legacy_missing_html_from_ui_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    "version": 1,
                    "feature": "alpha",
                    "uiRequired": True,
                    "triggered": True,
                    "route": ROUTE_MISSING,
                    "source": "UI_CONTEXT.json",
                    "visualSourceIds": ["VIS-001"],
                    "htmlSourcePaths": [],
                    "htmlSourceMissing": True,
                    "missingHtmlSourcePaths": [],
                    "reasons": [],
                    "docPaths": [],
                },
            )

            result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, 0)

    def test_frontend_write_blocks_when_ui_context_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_ui_context(workspace, base_ui_context(ui_required=False))

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("uiRequired=false", error.getvalue())

    def test_frontend_write_blocks_invalid_ui_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "UI_CONTEXT.json", "{")
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("UI_CONTEXT.json 非法", error.getvalue())

    def test_frontend_write_blocks_when_route_evidence_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("FRONTEND_ROUTE.json", error.getvalue())
        self.assertIn("block", output.getvalue())

    def test_frontend_write_blocks_until_parser_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_STANDARD),
                    "parserRead": False,
                },
            )

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("parser", error.getvalue())

    def test_frontend_write_allows_complete_route_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(evidence_path(workspace, "alpha"), complete_evidence(ROUTE_STANDARD))

            result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, 0)

    def test_frontend_write_allows_after_explicit_route_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "need HTML frontend\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")
            resolve_frontend_route(workspace, "alpha", write_evidence=True, start_route_run=True)
            mark_evidence(workspace, "alpha", mark="route-skill-read-complete")
            mark_evidence(workspace, "alpha", mark="route-todos-created")
            mark_evidence(workspace, "alpha", mark="parser-read")

            result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
