"""Regression scenarios for nested handoffs, shared publication and diagnostics."""
import argparse
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from hooks.engineering_cache import resolve
from hooks.engineering_context import execute, read_json, record_implementation, record_repair
from hooks.engineering_facts import environment_scope
from hooks.json_writer_common import atomic_write_json
from hooks.parallel_runtime import load_manifest
from hooks.plan_json import find_task, load_plan_bundle
from tests import test_code_stage_authority as authority
from tests import test_engineering_context as fact_fixture


class DependencyClaimTest(unittest.TestCase):
    def test_parser_runs_outside_lock_with_one_producer_and_no_waiter_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "class.json"
            started, release = threading.Event(), threading.Event()
            def producer():
                started.set()
                self.assertTrue(release.wait(5))
                return {"status": "found", "matches": ["a.jar"]}
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                owner = pool.submit(resolve, path, producer, validate=lambda: True)
                self.assertTrue(started.wait(5))
                waiters = [pool.submit(resolve, path, lambda: self.fail("second producer"), validate=lambda: True) for _ in range(6)]
                self.assertTrue(all(f.result(timeout=3)["status"] == "in_progress" for f in waiters))
                release.set()
                self.assertEqual("published", owner.result()["cacheStatus"])
            self.assertTrue(resolve(path, lambda: self.fail("cache must hit"), validate=lambda: True)["reused"])

    def test_expired_dependency_producer_cannot_overwrite_takeover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "class.json"
            def slow_producer():
                claim = read_json(path.with_suffix(".claim.json"))
                claim["expiresEpoch"] = time.time() - 1
                atomic_write_json(path.with_suffix(".claim.json"), claim)
                resolve(path, lambda: {"status": "found", "content": "new"}, validate=lambda: True)
                return {"status": "found", "content": "old"}
            with self.assertRaisesRegex(ValueError, "expired_or_replaced"):
                resolve(path, slow_producer, validate=lambda: True)
            self.assertEqual("new", read_json(path)["content"])

    def test_input_change_prevents_publication_and_does_not_leave_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "class.json"
            with self.assertRaisesRegex(ValueError, "inputs_changed"):
                resolve(path, lambda: {"status": "found"}, validate=lambda: False)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".claim.json").exists())


class HandoffRegressionTest(unittest.TestCase):
    def setUp(self):
        self.a = authority.CodeStageAuthorityTest()
        self.a.setUp()
        self.addCleanup(self.a.doCleanups)

    def test_module_workspace_uses_manifest_root_and_frozen_git_relative_snapshots(self):
        finished, _ = self.a._initial_delivery()
        nested = self.a.worktree / "后台 服务" / "零售客户经营" / "模块"
        nested.mkdir(parents=True)
        _, task = find_task(load_plan_bundle(self.a.feature_dir), "T001")
        initial = read_json(Path(finished["engineeringHandoff"]["path"]))
        (self.a.worktree / "production.txt").write_text("later dirty content")
        result = record_implementation(self.a.workspace, "alpha", self.a.parallel_id, "B001", nested, task, finished)
        handoff = read_json(Path(result["path"]))
        self.assertEqual(str(self.a.worktree), handoff["worktreePath"])
        self.assertEqual(str(nested), handoff["taskWorkspace"])
        self.assertEqual("batch_git_root", handoff["pathBase"])
        self.assertEqual(initial["files"], handoff["files"])
        self.assertTrue(handoff["files"]["production.txt"])
        snapshots = list((Path(result["path"]).parent / "snapshots").glob("*.json"))
        self.assertGreaterEqual(len(snapshots), 2)

    def test_real_task_finish_from_nested_chinese_module_can_enter_review_and_utest(self):
        original = authority._workspace
        def with_module(base):
            workspace, feature, repo = original(base)
            module = repo / "后台 服务" / "零售客户经营"
            module.mkdir(parents=True)
            (module / "pom.xml").write_text("<project/>\n")
            (module / "production.txt").write_text("baseline\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "module baseline"], cwd=repo, check=True, capture_output=True)
            return workspace, feature, repo
        a = authority.CodeStageAuthorityTest()
        with patch.object(authority, "_workspace", side_effect=with_module):
            a.setUp()
        self.addCleanup(a.doCleanups)
        module = a.worktree / "后台 服务" / "零售客户经营"
        from hooks.task_runner import start_task, prepare_compile, record_compile, finish_implementation
        from hooks.parallel_batch_stage import start_stage, complete_stage, finalize_implementation
        common = {"parallel_run_id": a.parallel_id, "lease_token": a.token, "workspace_ref": "default"}
        task = start_task(a.workspace, "alpha", "T001", module, **common)
        (module / "production.txt").write_text("implementation\n")
        attempt = prepare_compile(a.workspace, "alpha", "T001", module, task["runId"], argv=["mvn", "compile"], execution_cwd=module, **common)
        log = a.workspace.parent / "compiler.log"
        log.write_text("fixture compile passed\n")
        record_compile(a.workspace, "alpha", "T001", module, task["runId"], attempt_id=attempt["attemptId"], exit_code=0, log_exit_code=0, output_file=log, **common)
        ok, finished = finish_implementation(a.workspace, "alpha", "T001", module, task["runId"], no_code_change_why=None, supporting_files=[], **common)
        self.assertTrue(ok)
        handoff = read_json(Path(finished["engineeringHandoff"]["path"]))
        self.assertEqual(str(a.worktree), handoff["worktreePath"])
        self.assertEqual(str(module), handoff["taskWorkspace"])
        self.assertTrue(handoff["files"]["后台 服务/零售客户经营/production.txt"])
        draft = a._seal()
        finalize_implementation(a.workspace, "alpha", a.parallel_id, "B001")
        start_stage(a.workspace, "alpha", a.parallel_id, "B001", "review")
        args = argparse.Namespace(action="prepare", workspace=str(a.workspace), feature="alpha", run_id=a.parallel_id,
            batch_id="B001", batch_worktree=str(a.worktree), task_id=["T001"], stage="review", owner_token=None)
        self.assertEqual("prepared", execute(args)["status"])
        complete_stage(a.workspace, "alpha", a.parallel_id, "B001", "review", metadata={"batchCommit": draft["commitSha"]})
        self.assertEqual("running", start_stage(a.workspace, "alpha", a.parallel_id, "B001", "test")["status"])

    def test_read_only_inspect_reports_paths_and_mismatch_without_writing_anything(self):
        finished, _ = self.a._initial_delivery()
        path = Path(finished["engineeringHandoff"]["path"])
        handoff = read_json(path)
        handoff["worktreePath"] = str(self.a.worktree / "后台 服务")
        atomic_write_json(path, handoff)
        def snapshot():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.a.workspace.rglob("*") if p.is_file()}
        before = snapshot()
        args = [sys.executable, "hooks/engineering_context.py", "inspect", "--workspace", str(self.a.workspace),
                "--feature", "alpha", "--run-id", self.a.parallel_id, "--batch-id", "B001"]
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["readOnly"])
        self.assertIn("engineering_handoff_identity_mismatch", report["batches"]["B001"]["reasonCodes"])
        self.assertIn(".engineering/facts", report["paths"]["factsRoot"])
        self.assertEqual(before, snapshot())

    def test_repair_recording_and_attempt_append_do_not_lose_history(self):
        self.a._initial_delivery()
        batch = load_manifest(self.a.workspace, "alpha", self.a.parallel_id)["batches"]["B001"]
        record_repair(self.a.workspace, "alpha", self.a.parallel_id, "B001", batch, "review", {"message": "first finding"})
        # Exercise the actual shared repair lock and runtime API; whichever
        # writer wins, the attempt must survive in current or previous history.
        from hooks.evidence_kernel import FileLock
        from hooks.engineering_context import context_base
        base = context_base(self.a.workspace, "alpha", self.a.parallel_id, "B001")
        def append_attempt():
            with FileLock(base / ".repair.lock"):
                path = base / "REPAIR_CONTEXT.json"
                data = read_json(path)
                data["attempts"].append({"hypothesis": "H1", "result": "ruled_out"})
                atomic_write_json(path, data)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(append_attempt), pool.submit(record_repair, self.a.workspace, "alpha", self.a.parallel_id,
                "B001", batch, "review", {"message": "second finding"})]
            for f in futures:
                f.result()
        repair = read_json(base / "REPAIR_CONTEXT.json")
        self.assertEqual(1, len([a for a in repair["attempts"] + repair["previousAttempts"] if a["hypothesis"] == "H1"]))


class ProofMergeTest(unittest.TestCase):
    def setUp(self):
        fixture = fact_fixture.SharedFactsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store = fixture.store

    def test_multiple_batches_append_test_proof_without_overwriting(self):
        claim = self.store.request("Api", "test_recipe", ["Api.java"], owner="B001", scope=environment_scope())
        published = self.store.publish(claim["factId"], claim["claimToken"], owner="B001", content={"test": "recipe"})
        def attach(i):
            self.store.attach_test_proof(claim["factId"], published["fact"]["sources"],
                {"runId": "r", "batchId": f"B{i:03}", "evidenceId": f"ev{i}", "commandId": "c", "targetId": "t",
                 "result": "PASS", "exitCode": 0, "logExitCode": 0, "testsExecuted": 1})
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(attach, range(6)))
        self.assertEqual(6, len(read_json(Path(published["factPath"]))["testProofs"]))

    def test_observability_counts_hit_wait_and_content_invalidation(self):
        first = self.store.request("Api", "api_signature", ["Api.java"], owner="r:B001:T001")
        self.store.request("Api", "api_signature", ["Api.java"], owner="r:B002:T002")
        self.store.publish(first["factId"], first["claimToken"], owner="r:B001:T001", content={"signature": "v1"})
        self.store.request("Api", "api_signature", ["Api.java"], owner="r:B002:T002")
        (self.store.root / "Api.java").write_text("class Api { int v2; }")
        self.store.request("Api", "api_signature", ["Api.java"], owner="r:B002:T002")
        path = self.store.base.parent.parent / "observations" / (self.store.repository + ".json")
        metrics = read_json(path)
        self.assertEqual(1, metrics["counts"]["fact_wait"])
        self.assertEqual(1, metrics["counts"]["fact_hit"])
        self.assertEqual(1, metrics["counts"]["fact_invalidated"])
        self.assertNotIn(first["claimToken"], path.read_text())
