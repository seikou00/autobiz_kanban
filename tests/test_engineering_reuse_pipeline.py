"""Exercise the real cross-Batch entry points and safe deadline closure."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from hooks.engineering_context import execute, context_reference, record_implementation, read_json
from hooks.engineering_facts import FactStore, file_digest
from hooks.engineering_environment import capture_diagnostic
from hooks.json_writer_common import atomic_write_json
from hooks.parallel_batch_stage import (start_stage, complete_stage, prepare_test, record_test_failure)
from hooks.parallel_runtime import load_manifest, save_manifest, run_dir, check_lease
from hooks.run_utest_command import diagnostic_cache_path, cached_diagnostic
from hooks.utest_control import request_stop_locked
from hooks.utest_deadline_guard import guard
from hooks.utest_policy import POLICY, policy_snapshot, stage_budget, deadline_reason
from hooks.worktree_manager import seal_parallel_batch, provision_parallel_worktree
from tests import test_code_stage_authority as authority
from tests import test_engineering_context as engineering
from tests.test_task_runner import _add_second_compile_only_batch


class DurablePolicyTest(unittest.TestCase):
    def test_deployment_contract_verifies_actual_policy_and_agent_files(self):
        from hooks.engineering_contract import runtime_contract
        contract = runtime_contract()
        self.assertTrue(contract["complete"])
        self.assertIn("agents/test-engineer.md", contract["files"])
        self.assertIn("hooks/utest_policy.py", contract["files"])
        args = argparse.Namespace(action="contract", expected_digest=contract["digest"])
        self.assertEqual("verified", execute(args)["status"])
        args.expected_digest = "0" * 64
        self.assertEqual("blocked", execute(args)["status"])

    def test_policy_is_shared_and_preserves_earlier_execution_limits(self):
        from hooks.parallel_batch_stage import UTEST_STAGE_TIMEOUT_SECONDS
        self.assertEqual(2700, UTEST_STAGE_TIMEOUT_SECONDS)
        self.assertEqual(POLICY["stageSeconds"], UTEST_STAGE_TIMEOUT_SECONDS)
        batch = {"stageStates": {"test": {"startedAt": "2026-10-16T00:00:00Z", "utestPolicy": policy_snapshot()}}}
        budget = stage_budget({}, batch)
        start = budget["startedEpoch"]
        self.assertIsNone(deadline_reason(budget, {"firstCommandAt": start + 1}, start + 900))
        self.assertIsNone(deadline_reason(budget, {}, start + 600))
        self.assertIsNone(deadline_reason(budget, {}, start + 1799))
        self.assertEqual(start + 1800, budget["firstCommandDeadlineEpoch"])
        self.assertEqual("first_execution_deadline_exceeded", deadline_reason(budget, {}, start + 1800))
        execution = {"firstCommandAt": start + 1799}
        self.assertIsNone(deadline_reason(budget, execution, start + 1800))
        self.assertIsNone(deadline_reason(budget, execution, start + 2699))
        self.assertEqual(start + 2700, budget["stageDeadlineEpoch"])
        self.assertEqual("stage_deadline_exceeded", deadline_reason(budget, execution, start + 2700))
        limited = stage_budget({}, batch, {"stageDeadlineEpoch": start + 800})
        self.assertEqual(start + 800, limited["stageDeadlineEpoch"])

    def test_legacy_running_stage_does_not_gain_another_fifteen_minutes(self):
        batch = {"stageStates": {"test": {"startedAt": "2026-10-16T00:00:00Z"}}}
        budget = stage_budget({"timeoutPerBatch": 14400}, batch)
        self.assertEqual(900, budget["stageDeadlineEpoch"] - budget["startedEpoch"])
        self.assertEqual("legacy_preserved", budget["utestPolicy"]["version"])
        self.assertEqual(600, budget["firstCommandDeadlineEpoch"] - budget["startedEpoch"])
        self.assertEqual("first_execution_deadline_exceeded", deadline_reason(budget, {}, budget["startedEpoch"] + 600))

    def test_saved_first_execution_deadline_is_not_extended_by_new_defaults(self):
        saved = {**policy_snapshot(), "firstExecutionSeconds": 600, "stageSeconds": 1800}
        batch = {"stageStates": {"test": {"startedAt": "2026-10-16T00:00:00Z", "utestPolicy": saved}}}
        budget = stage_budget({}, batch)
        self.assertEqual(600, budget["firstCommandDeadlineEpoch"] - budget["startedEpoch"])
        self.assertEqual(1800, budget["stageDeadlineEpoch"] - budget["startedEpoch"])


class SharedContentTest(unittest.TestCase):
    def setUp(self):
        self.s = engineering.SharedFactsTest()
        self.s.setUp()
        self.addCleanup(self.s.doCleanups)

    def test_automatic_declarations_survive_unrelated_test_pom_and_invalidate_on_source(self):
        store = self.s.store
        result = store.record_declarations({"Api.java": file_digest(self.s.root / "Api.java")}, "run:B001:T001")
        self.assertTrue(result["factIds"])
        before = {p: p.read_bytes() for p in store.base.glob("*.json")}
        self.assertEqual("hit", self.s.second.lookup("Api")["status"])
        self.assertEqual(before, {p: p.read_bytes() for p in store.base.glob("*.json")})
        (self.s.other / "pom.xml").write_text("<project><dependencies>test-only-change</dependencies></project>")
        self.assertEqual("hit", self.s.second.lookup("Api")["status"])
        (self.s.other / "Api.java").write_text("public class Api { public int changed() { return 1; } }")
        self.assertEqual("missing", self.s.second.lookup("Api")["status"])

    def test_unpublished_claim_is_explicitly_closed_and_not_reported_ready(self):
        claim = self.s.store.request("Api", "usage_example", ["Api.java"], owner="run:B001:T001")
        self.assertEqual([claim["factId"]], self.s.store.close_claims("run:B001:T001"))
        fact = read_json(Path(claim["factPath"]))
        self.assertEqual("failed", fact["status"])
        self.assertEqual("exploration_unpublished_at_closure", fact["content"]["reasonCode"])
        self.assertNotIn("claimToken", fact)

    def test_concurrent_diagnostic_has_one_producer_and_old_producer_cannot_publish(self):
        from hooks.engineering_environment import reserve_diagnostic, publish_diagnostic
        from concurrent.futures import ThreadPoolExecutor
        path = self.s.base / "diagnostic.json"
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda n: reserve_diagnostic(path, f"B{n}", 1), range(4)))
        claimed = [r for r in results if r["status"] == "claimed"]
        self.assertEqual(1, len(claimed))
        with patch("hooks.engineering_environment.time.time", return_value=time.time() + 2):
            next_owner = reserve_diagnostic(path, "takeover", 10)
            self.assertFalse(publish_diagnostic(path, claimed[0]["token"], {"obsolete": True}))
            self.assertTrue(publish_diagnostic(path, next_owner["token"], {"current": True}))
        self.assertEqual({"current": True}, read_json(path))

    def test_diagnostics_share_across_worktrees_and_materialize_current_classpath(self):
        root, other = self.s.root, self.s.other
        jar = self.s.base / "dependency.jar"
        jar.write_bytes(b"test dependency")
        original = ["mvn", "-o", "dependency:build-classpath", "-DoutputFile=first.txt"]
        following = ["mvn", "-o", "dependency:build-classpath", "-DoutputFile=next.txt"]
        feature = self.s.base / "artifacts" / ".autobizdevops" / "features" / "alpha"
        first = diagnostic_cache_path(feature, root, original)
        second = diagnostic_cache_path(feature, other, following)
        self.assertEqual(first, second)
        (root / "first.txt").write_text(str(jar))
        atomic_write_json(first, capture_diagnostic({"ok": True, "exitCode": 0, "result": "PASS"}, root, original))
        reused = cached_diagnostic(second, other, following)
        self.assertTrue(reused["diagnosticReused"])
        self.assertEqual(str(jar), (other / "next.txt").read_text())
        self.assertEqual(str(other), reused["cwd"])
        (other / "pom.xml").write_text("<project><version>different</version></project>")
        self.assertNotEqual(first, diagnostic_cache_path(feature, other, following))

    def test_transient_failure_expires_and_restore_never_overwrites_tracked_source(self):
        argv = ["mvn", "dependency:tree", "-DoutputFile=Api.java"]
        feature = self.s.base / "artifacts" / ".autobizdevops" / "features" / "alpha"
        path = diagnostic_cache_path(feature, self.s.root, argv)
        value = capture_diagnostic({"ok": False, "exitCode": 1}, self.s.root, argv)
        atomic_write_json(path, value)
        with patch("hooks.run_utest_command.time.time", return_value=time.time() + 61):
            self.assertIsNone(cached_diagnostic(path, self.s.root, argv))
        value.update(ok=True, exitCode=0, cacheExpiresEpoch=None)
        atomic_write_json(path, value)
        before = (self.s.root / "Api.java").read_bytes()
        self.assertIsNone(cached_diagnostic(path, self.s.root, argv))
        self.assertEqual(before, (self.s.root / "Api.java").read_bytes())


class BoundReusePipelineTest(unittest.TestCase):
    def setUp(self):
        self.a = authority.CodeStageAuthorityTest()
        original = authority._workspace
        def with_shared_source(root):
            workspace, feature, repo = original(root)
            (repo / "SharedApi.java").write_text("package example;\npublic class SharedApi {\n public static int value() { return 1; }\n}\n")
            subprocess.run(["git", "add", "SharedApi.java"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "shared API"], cwd=repo, check=True, capture_output=True)
            _add_second_compile_only_batch(feature)
            return workspace, feature, repo
        with patch.object(authority, "_workspace", side_effect=with_shared_source):
            self.a.setUp()
        self.addCleanup(self.a.doCleanups)
        self.args = argparse.Namespace(action="resolve", workspace=str(self.a.workspace), feature="alpha",
            run_id=self.a.parallel_id, batch_id="B001", batch_worktree=str(self.a.worktree),
            task_id=["T001"], stage="code", owner_token=self.a.token,
            symbol="example.SharedApi", kind="api_signature", source=["SharedApi.java"], classpath_file=None)

    def test_another_batch_code_reads_published_fact_before_task_start_without_lease(self):
        self.assertEqual("pending", self.a._batch()["stageStates"]["implement"]["status"])
        published = execute(self.args)
        self.assertEqual("ready", published["status"])
        second = provision_parallel_worktree(self.a.workspace, "alpha", self.a.parallel_id, "B002")
        self.args.action, self.args.batch_id, self.args.task_id = "lookup", "B002", ["T002"]
        self.args.batch_worktree, self.args.owner_token = second["worktreePath"], None
        found = execute(self.args)
        self.assertEqual("hit", found["status"])
        self.assertEqual(published["factId"], found["facts"][0]["factId"])
        self.assertEqual("pending", load_manifest(self.a.workspace, "alpha", self.a.parallel_id)["batches"]["B002"]["stageStates"]["implement"]["status"])
        self.args.stage = "review"
        self.assertEqual("hit", execute(self.args)["status"])

    def test_code_uses_shared_dependency_inspection_before_utest_exists(self):
        jar = self.a.workspace.parent / "common-1.jar"
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("example/CookieUtil.class", b"fixture")
        with zipfile.ZipFile(jar.with_name("common-1-sources.jar"), "w") as archive:
            archive.writestr("example/CookieUtil.java", "package example; public class CookieUtil {}")
        (self.a.worktree / "classpath.txt").write_text(str(jar))
        self.args.action, self.args.class_name, self.args.module = "inspect-class", "example.CookieUtil", "."
        self.args.classpath_file = "classpath.txt"
        result = execute(self.args)
        self.assertEqual("source", result["kind"])
        self.assertIn("CookieUtil", result["content"])
        self.assertTrue(execute(self.args)["reused"])

    def test_finish_automatically_publishes_current_declarations_and_utest_receives_them(self):
        task = self.a._task()
        path = self.a.worktree / "SharedApi.java"
        path.write_text(path.read_text().replace("return 1", "return 2"))
        finished = self.a._finish(task)
        handoff = read_json(Path(finished["engineeringHandoff"]["path"]))
        self.assertTrue(handoff["factIds"])
        self.a._seal()
        from hooks.parallel_batch_stage import finalize_implementation
        finalize_implementation(self.a.workspace, "alpha", self.a.parallel_id, "B001")
        start_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review")
        complete_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review")
        preparation = prepare_test(self.a.workspace, "alpha", self.a.parallel_id, "B001")
        self.assertTrue(preparation["engineeringContext"]["brief"]["facts"])
        self.assertEqual("example.SharedApi", preparation["engineeringContext"]["brief"]["facts"][0]["symbol"])
        path.write_text("public class SharedApi { /* later dirty content */ }")
        from hooks.plan_json import find_task, load_plan_bundle
        _, plan_task = find_task(load_plan_bundle(self.a.feature_dir), "T001")
        recovered = record_implementation(self.a.workspace, "alpha", self.a.parallel_id, "B001", self.a.worktree, plan_task, finished)
        data = read_json(Path(recovered["path"]))
        self.assertEqual(handoff["files"], data["files"])
        self.assertIn("completion_snapshot_no_longer_current", [g["reasonCode"] for g in data["cacheWriteSummary"]["gaps"]])

    def test_stop_retains_lease_blocks_tools_and_requires_seal_before_defer(self):
        self.a._initial_delivery()
        start_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review")
        complete_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review")
        start_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "test")
        manifest = load_manifest(self.a.workspace, "alpha", self.a.parallel_id)
        batch = manifest["batches"]["B001"]
        tests = self.a.worktree / "tests"
        tests.mkdir()
        (tests / "test_preserved.py").write_text("def test_preserved():\n assert True\n")
        with patch("hooks.utest_control.time.time", return_value=batch["stageStates"]["test"]["stageDeadlineEpoch"] + 1):
            with self.assertRaisesRegex(ValueError, "cannot_pass"):
                complete_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "test")
        manifest = load_manifest(self.a.workspace, "alpha", self.a.parallel_id)
        batch = manifest["batches"]["B001"]
        result = request_stop_locked(self.a.workspace, "alpha", self.a.parallel_id, "B001", manifest, batch)
        self.assertEqual("stop_requested", result["status"])
        self.assertTrue(check_lease(self.a.workspace, "alpha", self.a.parallel_id, "B001", self.a.token))
        with patch.dict(os.environ, self.a.environment):
            for name in ("read_file", "write_file", "execute"):
                data = {"file_path": str(tests / "test_preserved.py"), "cwd": str(self.a.worktree), "command": "javap example.Class"}
                self.assertIn("UTEST_STOP_REQUESTED", guard({"tool_name": name, "tool_input": data}))
            closure = f'{os.sys.executable} -X utf8 "{Path("hooks/worktree_manager.py").resolve()}" --json seal --run-id {self.a.parallel_id} --batch-id B001'
            self.assertIsNone(guard({"tool_name": "execute", "tool_input": {"cwd": str(self.a.worktree), "command": closure}}))
            checked = subprocess.run([os.sys.executable, "hooks/utest_deadline_guard.py"],
                input=json.dumps({"tool_name": "read_file", "tool_input": {"file_path": str(tests / "test_preserved.py")}}),
                text=True, capture_output=True, timeout=10)
            self.assertEqual(2, checked.returncode, checked.stderr)
            self.assertEqual("block", json.loads(checked.stdout)["decision"])
        with self.assertRaisesRegex(ValueError, "cannot_pass"):
            complete_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "test")
        with self.assertRaisesRegex(ValueError, "requires_asset_seal"):
            record_test_failure(self.a.workspace, "alpha", self.a.parallel_id, "B001", failure_type="environment", message="timeout")
        sealed = seal_parallel_batch(self.a.workspace, "alpha", self.a.parallel_id, "B001", self.a.worktree, self.a.token)
        self.assertTrue(sealed["success"], sealed)
        record_test_failure(self.a.workspace, "alpha", self.a.parallel_id, "B001", failure_type="environment", message="timeout")
        self.assertFalse(self.a._batch().get("utestClosurePending"))
        self.assertEqual("deferred", self.a._batch()["stageStates"]["test"]["status"])
        self.assertTrue((tests / "test_preserved.py").is_file())


if __name__ == "__main__":
    unittest.main()
