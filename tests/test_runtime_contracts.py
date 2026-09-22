#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan-owned workspace and candidate-digest contracts."""

from __future__ import print_function

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.candidate_digest import compute as compute_candidate_digest  # noqa: E402
from hooks.utest_plan_contract import load_utest_plan  # noqa: E402
from hooks.verified_digest_guard import invalidate_if_stale  # noqa: E402


class PlanOwnedRuntimeContractsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "output"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        (self.workspace / ".autobizdevops" / "state.json").write_text("{}\n", encoding="utf-8")

        self.repo = self.root / "repo"
        self.module = self.repo / "ui-admin"
        self.module.mkdir(parents=True)
        (self.module / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.repo), "-c", "user.name=Autodev",
                "-c", "user.email=autodev@example.invalid", "commit", "-q", "-m", "init",
            ],
            check=True,
        )
        self._write_plan()

    def _write_plan(self):
        plan = {
            "codeWorkspaces": {"default": str(self.repo)},
            "batches": [{"path": "plans/B001/plan.json"}],
        }
        batch = {
            "batchId": "B001",
            "executionLane": "frontend",
            "tasks": [{
                "id": "T001",
                "title": "Plan-owned workspace",
                "goal": "Validate the Plan workspace binding",
                "implementationPoints": [],
                "testPoints": [],
                "nonGoals": [],
                "validationBoundary": "Plan-bound repository",
                "verificationIntent": "Plan-bound repository",
                "workspaceRef": "default",
                "specRefs": ["specs/example/spec.md#SCN-001"],
                "acceptanceCriteria": [{"id": "AC-001", "text": "workspace resolves"}],
            }],
        }
        (self.feature_dir / "plan.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        batch_path = self.feature_dir / "plans" / "B001" / "plan.json"
        batch_path.parent.mkdir(parents=True)
        batch_path.write_text(json.dumps(batch), encoding="utf-8")

    def test_utest_ignores_obsolete_run_context_artifact(self):
        legacy_artifact = self.feature_dir / ".runtime" / "RUN_CONTEXT.json"
        legacy_artifact.parent.mkdir()
        legacy_artifact.write_text("not json", encoding="utf-8")

        plan = load_utest_plan(self.feature_dir)

        self.assertEqual("T001", plan["batches"][0]["tasks"][0]["id"])

    def test_candidate_digest_uses_plan_workspace_and_ignores_obsolete_artifact(self):
        before = compute_candidate_digest(self.workspace, "alpha")
        legacy_artifact = self.feature_dir / ".runtime" / "RUN_CONTEXT.json"
        legacy_artifact.parent.mkdir()
        legacy_artifact.write_text("not json", encoding="utf-8")

        self.assertEqual(before, compute_candidate_digest(self.workspace, "alpha"))

        (self.module / "src.js").write_text("export const changed = true;\n", encoding="utf-8")
        self.assertNotEqual(before, compute_candidate_digest(self.workspace, "alpha"))

    def test_verified_digest_change_triggers_code_stage_downgrade(self):
        original = compute_candidate_digest(self.workspace, "alpha")
        (self.feature_dir / "VERIFY_DECISION.json").write_text(
            json.dumps({"version": 1, "diffDigest": original}), encoding="utf-8"
        )
        (self.module / "src.js").write_text("export const changed = true;\n", encoding="utf-8")
        state = SimpleNamespace(
            fatal_errors=[], records={"alpha": {"checkpoint": "code_done"}}
        )
        rollback_plan = SimpleNamespace(ok=True, errors=(), new_checkpoint="code_in_progress")
        rollback_result = SimpleNamespace(ok=True, errors=())
        with mock.patch(
            "hooks.verified_digest_guard.load_state_json_records_result", return_value=state
        ), mock.patch(
            "hooks.verified_digest_guard.prepare_stage_rollback", return_value=rollback_plan
        ) as prepare, mock.patch(
            "hooks.verified_digest_guard.execute_stage_rollback", return_value=rollback_result
        ):
            result = invalidate_if_stale(self.workspace, "alpha")
        self.assertEqual("code_in_progress", result["checkpoint"])
        self.assertNotEqual(result["oldDigest"], result["newDigest"])
        self.assertEqual("dev.code", prepare.call_args.kwargs["stage"])
        self.assertEqual("keep", prepare.call_args.kwargs["code_source"])

    def test_run_context_prompt_hook_is_not_registered(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook.get("command")
            for registration in config.get("UserPromptSubmit", [])
            for hook in registration.get("hooks", [])
        ]
        self.assertNotIn("python hooks/run_context.py inject", commands)


if __name__ == "__main__":
    unittest.main()
