"""Regression coverage for Code task-context execution-state validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hooks.code_task_context import build_context
from hooks.code_task_context import _extract_spec_snippet


def test_mixed_spec_heading_styles_do_not_leak_adjacent_scenarios() -> None:
    text = "\n".join([
        "### Requirement REQ-001: read capability",
        "The capability is visible.",
        "#### Scenario [SCN-001]: present",
        "Return the value.",
        "#### Scenario SCN-002: missing",
        "Return unavailable.",
        "## Source References / 外部资料引用",
        "Unrelated section.",
    ])
    assert _extract_spec_snippet(text, "REQ-001") == (
        "### Requirement REQ-001: read capability\nThe capability is visible.", 1,
    )
    assert _extract_spec_snippet(text, "SCN-001") == (
        "#### Scenario [SCN-001]: present\nReturn the value.", 3,
    )
    assert _extract_spec_snippet(text, "SCN-002") == (
        "#### Scenario SCN-002: missing\nReturn unavailable.", 5,
    )
    assert _extract_spec_snippet("#### Scenario [SCN-001: unmatched bracket", "SCN-001") is None


def test_deferred_validation_policy_uses_review_owned_state(tmp_path: Path) -> None:
    """Code context accepts the current review-owned deferred-validation Plan."""
    root_plan = {
        "taskValidationPolicy": {
            "mode": "defer_to_test_stages",
            "orchestration": "inline",
            "codeGate": "review_only",
        },
        "activeBatchId": "B001",
        "batches": [{"id": "B001", "status": "in_progress", "taskIds": ["T001"], "executionLane": "backend"}],
    }
    batch_plan = {
        "batchId": "B001",
        "title": "batch",
        "status": "in_progress",
        "taskCount": 1,
        "completedTaskCount": 0,
        "tasks": [{"id": "T001", "workspaceRef": "RouYi"}],
    }

    def validate_batch(data, **kwargs):
        assert "batchCompile" not in data
        return [] if kwargs["defer_to_test_stages"] is True else ["B001.deferred_validation_required"]

    with (
        patch("hooks.code_task_context.load_plan", side_effect=[root_plan, batch_plan]),
        patch("hooks.code_task_context.validate_plan_data", return_value=[]),
        patch("hooks.code_task_context.validate_batch_plan_data", side_effect=validate_batch),
        patch("hooks.code_task_context.resolve_task_refs", return_value=([], [], [])),
    ):
        result = build_context(workspace=tmp_path, feature="alpha", task_id="T001")

    assert result.ok is True
