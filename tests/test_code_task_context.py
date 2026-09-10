"""Regression coverage for Code task-context execution-state validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hooks.code_task_context import build_context


def test_deferred_validation_policy_allows_batch_compile_state(tmp_path: Path) -> None:
    """A sealed Batch must remain repairable after batch-compile writes its state."""
    root_plan = {
        "taskValidationPolicy": {
            "mode": "defer_to_test_stages",
            "orchestration": "inline",
            "codeGate": "batch_compile_only",
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
        "batchCompile": {"status": "passed"},
        "tasks": [{"id": "T001", "workspaceRef": "RouYi"}],
    }

    def validate_batch(data, **kwargs):
        assert data["batchCompile"]["status"] == "passed"
        return [] if kwargs["defer_to_test_stages"] is True else ["B001.batchCompile_unexpected"]

    with (
        patch("hooks.code_task_context.load_plan", side_effect=[root_plan, batch_plan]),
        patch("hooks.code_task_context.validate_plan_data", return_value=[]),
        patch("hooks.code_task_context.validate_batch_plan_data", side_effect=validate_batch),
        patch("hooks.code_task_context.resolve_task_refs", return_value=([], [], [])),
    ):
        result = build_context(workspace=tmp_path, feature="alpha", task_id="T001")

    assert result.ok is True
