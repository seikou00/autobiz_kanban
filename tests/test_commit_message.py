from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from hooks.commit_message import CommitMessageError, build_commit_message
from hooks.parallel_batch_scheduler import create_run, mark_batch
from hooks.parallel_batch_stage import complete_stage, fail_stage, start_stage
from hooks.parallel_runtime import acquire_lease, load_manifest, save_manifest
from hooks.worktree_manager import main, provision_parallel_worktree, seal_parallel_batch
from tests.test_task_runner import _read_batch, _workspace, _write_batch


class CommitMessageTest(unittest.TestCase):
    def test_code_keeps_only_task_titles_and_multiline_goals(self) -> None:
        tasks = [
            {"id": "T001", "title": "订单查询", "goal": "支持分页查询。\n保留状态筛选。",
             "implementationPoints": ["不能出现在正文"], "specRefs": ["REQ-001"]},
            {"id": "T002", "title": "空列表", "goal": "没有订单时展示空列表。"},
        ]
        message = build_commit_message("Z990692-294", " 实现订单查询\n与空列表 ", stage="code", tasks=tasks)
        self.assertEqual(message, "Z990692-294 #comment cmbdevcalw提交 Code：实现订单查询 与空列表\n\n"
                         "TASK T001：订单查询\n任务目标：支持分页查询。\n保留状态筛选。\n\n"
                         "TASK T002：空列表\n任务目标：没有订单时展示空列表。")

    def test_other_operations_and_stages_have_no_task_body(self) -> None:
        for stage, summary, label in ((None, "初始化工作流", ""), ("rework", "修复状态筛选", "Rework："),
                                      ("utest", "补充分页测试", "UTest：")):
            with self.subTest(stage=stage):
                self.assertEqual(build_commit_message("Z990692-294", summary, stage=stage),
                                 f"Z990692-294 #comment cmbdevcalw提交 {label}{summary}")

    def test_invalid_messages_fail_before_git(self) -> None:
        cases = [
            ({"stage": "code"}, "tasks_required"),
            ({"stage": "review"}, "stage_invalid"),
            ({"stage": "utest", "tasks": []}, "tasks_only_allowed_for_code"),
            ({"stage": "rework", "tasks": []}, "tasks_only_allowed_for_code"),
            ({"stage": "code", "tasks": [{"id": "T001", "title": "task", "goal": ""}]}, "goal_required"),
        ]
        for options, error in cases:
            with self.subTest(options=options), self.assertRaisesRegex(CommitMessageError, error):
                build_commit_message("Z990692-294", "说明", **options)
        for card, summary in (("", "说明"), ("CARD invalid", "说明"), ("CARD", " "), ("CARD", "bad\x00summary")):
            with self.subTest(card=card, summary=summary), self.assertRaises(CommitMessageError):
                build_commit_message(card, summary)


class BatchCommitMessageTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="commit-message-")
        self.addCleanup(temporary.cleanup)
        self.workspace, self.feature_dir, self.repo = _workspace(Path(temporary.name), deps=["T000"])
        self.run_id = create_run(self.workspace, "alpha", max_parallel=1, timeout_seconds=60,
                                 code_workspaces=[str(self.repo)], task_card_id="Z990692-294")["runId"]
        provisioned = provision_parallel_worktree(self.workspace, "alpha", self.run_id, "B001")
        self.worktree = Path(provisioned["worktreePath"])
        self.token = acquire_lease(self.workspace, "alpha", self.run_id, "B001")["ownerToken"]
        mark_batch(self.workspace, "alpha", self.run_id, "B001", "running",
                   worktreePath=str(self.worktree), branchName=provisioned["branchName"])
        (self.worktree / "delivery.txt").write_text("production\n", encoding="utf-8")

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.worktree, capture_output=True, text=True,
                              check=True).stdout.strip()

    def _seal(self, **options: object) -> dict:
        return seal_parallel_batch(self.workspace, "alpha", self.run_id, "B001", self.worktree,
                                   self.token, **options)

    def _initial_delivery(self) -> dict:
        draft = self._seal(purpose="review", commit_stage="code", commit_summary="实现订单查询")
        self.assertTrue(draft["success"], draft)
        for stage in ("prepare", "implement"):
            start_stage(self.workspace, "alpha", self.run_id, "B001", stage)
            complete_stage(self.workspace, "alpha", self.run_id, "B001", stage)
        return draft

    def _start_test(self) -> dict:
        draft = self._initial_delivery()
        start_stage(self.workspace, "alpha", self.run_id, "B001", "review")
        complete_stage(self.workspace, "alpha", self.run_id, "B001", "review")
        start_stage(self.workspace, "alpha", self.run_id, "B001", "test")
        tests = self.worktree / "tests"
        tests.mkdir()
        (tests / "test_delivery.py").write_text("def test_delivery():\n    assert True\n", encoding="utf-8")
        return draft

    def test_review_draft_has_all_bound_task_goals_in_real_git(self) -> None:
        result = self._seal(purpose="review", commit_stage="code", commit_summary="实现订单查询")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["purpose"], "review")
        self.assertEqual(result["commitStage"], "code")
        message = self._git("log", "-1", "--format=%B")
        self.assertEqual(message, "Z990692-294 #comment cmbdevcalw提交 Code：实现订单查询\n\n"
                         "TASK T000：dependency\n任务目标：deliver observable behavior\n\n"
                         "TASK T001：deliver behavior\n任务目标：deliver observable behavior")
        self.assertNotIn("update behavior", message)
        self.assertNotIn("REQ-001", message)

    def test_missing_goal_is_rejected_without_staging(self) -> None:
        batch = _read_batch(self.feature_dir)
        del batch["tasks"][0]["goal"]
        _write_batch(self.feature_dir, batch)
        result = self._seal(purpose="review", commit_stage="code", commit_summary="实现订单查询")
        self.assertFalse(result["success"], result)
        self.assertIn("goal_missing", result["error"])
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")

    def test_changed_goal_is_rejected_without_staging(self) -> None:
        batch = _read_batch(self.feature_dir)
        batch["tasks"][0]["goal"] = "changed contract"
        _write_batch(self.feature_dir, batch)
        result = self._seal(purpose="review", commit_stage="code")
        self.assertFalse(result["success"], result)
        self.assertEqual(result["error"], "parallel_commit_plan_changed")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")

    def test_wrong_task_binding_is_rejected_without_staging(self) -> None:
        manifest = load_manifest(self.workspace, "alpha", self.run_id)
        manifest["batches"]["B001"]["taskIds"] = ["T999"]
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        result = self._seal(purpose="review", commit_stage="code")
        self.assertEqual(result["error"], "parallel_commit_task_binding_mismatch:B001")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")

    def test_empty_summary_is_rejected_without_staging(self) -> None:
        result = self._seal(purpose="review", commit_stage="code", commit_summary=" ")
        self.assertEqual(result["error"], "parallel_commit_summary_required")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")

    def test_review_and_test_source_repairs_have_no_task_body(self) -> None:
        self._initial_delivery()
        for failed_stage in ("review", "test"):
            with self.subTest(failed_stage=failed_stage):
                if failed_stage == "test":
                    for stage in ("prepare", "implement", "review"):
                        start_stage(self.workspace, "alpha", self.run_id, "B001", stage)
                        complete_stage(self.workspace, "alpha", self.run_id, "B001", stage)
                start_stage(self.workspace, "alpha", self.run_id, "B001", failed_stage)
                fail_stage(self.workspace, "alpha", self.run_id, "B001", failed_stage,
                           failure_type="implementation", message="状态筛选失效")
                for stage in ("prepare", "implement"):
                    start_stage(self.workspace, "alpha", self.run_id, "B001", stage)
                    if stage == "prepare":
                        complete_stage(self.workspace, "alpha", self.run_id, "B001", stage)
                (self.worktree / "delivery.txt").write_text(f"repaired {failed_stage}\n", encoding="utf-8")
                # Review repair uses the new explicit API; a legacy test repair
                # call must infer Rework from the durable source-failure record.
                stage_options = {"commit_stage": "rework"} if failed_stage == "review" else {}
                result = self._seal(purpose="review", commit_summary="修复状态筛选失效", **stage_options)
                self.assertTrue(result["success"], result)
                self.assertEqual(self._git("log", "-1", "--format=%B"),
                                 "Z990692-294 #comment cmbdevcalw提交 Rework：修复状态筛选失效")

    def test_utest_assets_have_no_task_body_before_stage_result(self) -> None:
        self._start_test()
        result = self._seal(commit_stage="utest", commit_summary="补充分页测试")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["purpose"], "utest")
        self.assertEqual(self._git("log", "-1", "--format=%B"),
                         "Z990692-294 #comment cmbdevcalw提交 UTest：补充分页测试")
        # Failure paths seal while test is still running, before recording the result.
        batch = load_manifest(self.workspace, "alpha", self.run_id)["batches"]["B001"]
        self.assertEqual(batch["stageStates"]["test"]["status"], "running")
        (self.worktree / "tests" / "test_delivery.py").write_text("def test_delivery():\n    assert 1 == 1\n", encoding="utf-8")
        legacy = self._seal()
        self.assertTrue(legacy["success"], legacy)
        self.assertEqual(legacy["commitStage"], "utest")
        self.assertNotIn("TASK", self._git("log", "-1", "--format=%B"))

    def test_utest_cannot_commit_production_changes_or_use_code_label(self) -> None:
        self._start_test()
        wrong_stage = self._seal(commit_stage="code", commit_summary="实现查询")
        self.assertIn("stage_mismatch:expected=utest:actual=code", wrong_stage["error"])
        (self.worktree / "delivery.txt").write_text("production change\n", encoding="utf-8")
        result = self._seal(commit_stage="utest", commit_summary="补充测试")
        self.assertEqual(result["error"], "parallel_utest_production_change_forbidden")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")

    def test_review_cannot_create_a_commit(self) -> None:
        draft = self._initial_delivery()
        start_stage(self.workspace, "alpha", self.run_id, "B001", "review")
        (self.worktree / "delivery.txt").write_text("review change\n", encoding="utf-8")
        result = self._seal(purpose="review", commit_stage="code", commit_summary="实现查询")
        self.assertEqual(result["error"], "parallel_commit_review_is_read_only")
        self.assertEqual(self._git("rev-parse", "HEAD"), draft["commitSha"])

    def test_no_change_seal_does_not_create_another_commit(self) -> None:
        first = self._seal(purpose="review", commit_stage="code", commit_summary="实现查询")
        self.assertTrue(first["success"], first)
        second = self._seal(purpose="review", commit_stage="code", commit_summary="继续实现查询")
        self.assertTrue(second["success"], second)
        self.assertEqual(first["commitSha"], second["commitSha"])
        self.assertEqual(self._git("rev-list", "--count", "HEAD"), "2")

    def test_ordinary_retry_stays_code_and_old_calls_infer_the_stage(self) -> None:
        manifest = load_manifest(self.workspace, "alpha", self.run_id)
        manifest["batches"]["B001"]["stageStates"]["implement"]["attempt"] = 3
        save_manifest(self.workspace, "alpha", self.run_id, manifest)
        result = self._seal(purpose="review")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["commitStage"], "code")
        self.assertIn("TASK T001", self._git("log", "-1", "--format=%B"))

    def test_cli_passes_stage_and_summary_to_real_git(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["--json", "seal", "--artifact-workspace", str(self.workspace),
                              "--feature", "alpha", "--run-id", self.run_id, "--batch-id", "B001",
                              "--repo", str(self.worktree), "--owner-token", self.token, "--purpose", "review",
                              "--commit-stage", "code", "--commit-summary", "实现中文查询"])
        self.assertEqual(exit_code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["commitStage"], "code")
        self.assertEqual(self._git("log", "-1", "--format=%s"),
                         "Z990692-294 #comment cmbdevcalw提交 Code：实现中文查询")


if __name__ == "__main__":
    unittest.main()
