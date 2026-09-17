"""Regression coverage for the review-owned Code-stage contract."""

from __future__ import annotations

import json


from tests.test_task_runner import _workspace


def test_current_code_plan_has_no_batch_compile_surface(tmp_path):
    """New Code plans delegate validation to Review/UTest, not a Batch compiler."""

    _workspace(tmp_path)
    root_path = tmp_path / "artifacts" / ".autobizdevops" / "features" / "alpha" / "plan.json"
    batch_path = root_path.parent / "plans" / "B001" / "plan.json"
    root = json.loads(root_path.read_text(encoding="utf-8"))
    batch = json.loads(batch_path.read_text(encoding="utf-8"))

    assert root["taskValidationPolicy"]["codeGate"] == "review_only"
    assert "compileProfiles" not in root
    assert "compileCommand" not in batch
    assert "batchCompile" not in batch
