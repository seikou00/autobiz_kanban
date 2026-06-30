#!/usr/bin/env python3
"""Regression tests for the autodev-code frontend route gate."""

from __future__ import annotations

import contextlib
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
    PARSERS,
    ROUTE_ABSOLUTE,
    ROUTE_SKILLS,
    ROUTE_STANDARD,
    evidence_path,
    mark_evidence,
    main as route_main,
    read_json,
    resolve_frontend_route,
    route_todo_ids,
    route_todo_output,
    write_json,
)


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
        "routeTodoProtocolVersion": 1,
        "requiredRouteTodoIds": route_todo_ids(route),
        "routeTodoIdsCreated": route_todo_ids(route),
        "routeTodoIdsCompleted": route_todo_ids(route),
        "routeTodosReadyForParser": True,
        "parserRead": True,
        "reviewStatus": "passed",
    }


class FrontendRouteResolverTests(unittest.TestCase):
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

    def test_route_todo_template_uses_fixed_ids(self) -> None:
        payload = {"route": ROUTE_ABSOLUTE}
        todos = route_todo_output(payload)["todos"]

        self.assertEqual(
            [todo["id"] for todo in todos],
            [
                "ABS-01-html-source",
                "ABS-02-project-context",
                "ABS-03-page-modules",
                "ABS-04-analysis-script",
                "ABS-05-context-handoff",
                "ABS-06-parser-handoff",
                "ABS-07-return-to-code",
            ],
        )

    def test_route_todos_created_requires_all_fixed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(evidence_path(workspace, "alpha"), complete_evidence(ROUTE_STANDARD))
            evidence = read_json(evidence_path(workspace, "alpha"))
            evidence["routeTodoIdsCreated"] = []
            evidence["routeTodoIdsCompleted"] = []
            write_json(evidence_path(workspace, "alpha"), evidence)

            with self.assertRaisesRegex(ValueError, "missing required todo id"):
                mark_evidence(
                    workspace,
                    "alpha",
                    mark="route-todos-created",
                    todo_ids=route_todo_ids(ROUTE_STANDARD)[:-1],
                )

    def test_emit_route_todos_cli_outputs_fixed_json_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_feature_file(workspace, "PLAN.md", "需要根据 HTML 实现前端页面。\n")
            write_feature_file(workspace, "frontend-html/page.html", "<form><button>OK</button></form>\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = route_main(
                    [
                        "--workspace",
                        str(workspace),
                        "--feature",
                        "alpha",
                        "--write-evidence",
                        "--emit-route-todos",
                        "--json",
                    ]
                )
            payload = json.loads(output.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["route"], ROUTE_STANDARD)
        self.assertEqual(payload["todos"][0]["id"], "STD-01-route-confirm")
        self.assertEqual(payload["todos"][-1]["id"], "STD-07-return-to-code")


class FrontendRouteGateValidatorTests(unittest.TestCase):
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


class FrontendRouteReadHookTests(unittest.TestCase):
    def test_parser_read_is_blocked_until_route_todos_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(
                evidence_path(workspace, "alpha"),
                {
                    **complete_evidence(ROUTE_ABSOLUTE),
                    "routeTodoIdsCreated": [],
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

    def test_parser_read_is_blocked_until_parser_handoff_todo_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            evidence = complete_evidence(ROUTE_ABSOLUTE)
            evidence["routeTodoIdsCompleted"] = [
                todo_id for todo_id in route_todo_ids(ROUTE_ABSOLUTE) if todo_id != "ABS-06-parser-handoff"
            ]
            evidence["routeTodosReadyForParser"] = False
            evidence["parserRead"] = False
            write_json(evidence_path(workspace, "alpha"), evidence)

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

        self.assertEqual(result, check_plugin_read.BLOCK_EXIT_CODE)
        self.assertIn("parser-handoff", error.getvalue())

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


class FrontendRouteWriteGuardTests(unittest.TestCase):
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

    def test_frontend_write_blocks_until_parser_handoff_todo_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            evidence = complete_evidence(ROUTE_STANDARD)
            evidence["routeTodoIdsCompleted"] = [
                todo_id for todo_id in route_todo_ids(ROUTE_STANDARD) if todo_id != "STD-06-parser-handoff"
            ]
            evidence["routeTodosReadyForParser"] = False
            write_json(evidence_path(workspace, "alpha"), evidence)

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(error):
                    result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, frontend_route_write_guard.BLOCK_EXIT_CODE)
        self.assertIn("parser-handoff", error.getvalue())

    def test_frontend_write_allows_complete_route_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            write_json(evidence_path(workspace, "alpha"), complete_evidence(ROUTE_STANDARD))

            result = frontend_route_write_guard.validate_frontend_write(workspace, "alpha")

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
