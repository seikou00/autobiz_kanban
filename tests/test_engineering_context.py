"""Real Git worktrees exercise shared facts, recovery and stage handoffs."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from hooks.engineering_facts import FactStore, environment_scope, git, locate
from hooks.engineering_context import bound_context, build_stage_context, context_base, execute, read_json
from hooks.json_writer_common import atomic_write_json
from hooks.parallel_batch_stage import complete_stage, fail_stage, finalize_implementation, start_stage
from hooks.parallel_runtime import load_manifest, run_dir
from hooks.runtime_artifact_guard import guard as artifact_guard
from hooks.runtime_guidance import command_catalog
from tests import test_code_stage_authority as authority_fixture
from tests import test_utest_optimization as utest_fixture


class SharedFactsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="engineering-facts-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        def run(*argv):
            subprocess.run(["git", *argv], cwd=self.root, check=True, capture_output=True)
        run("init", "-q")
        run("config", "user.email", "test@example.invalid")
        run("config", "user.name", "Test")
        (self.root / "Api.java").write_text("public class Api {\n public static String user() { return null; }\n}\n")
        (self.root / "pom.xml").write_text("<project/>\n")
        run("add", ".")
        run("commit", "-qm", "baseline")
        self.other = self.base / "second worktree"
        run("worktree", "add", "-qb", "second", str(self.other))
        self.store = FactStore(self.base / "artifacts", "alpha", self.root)
        self.second = FactStore(self.base / "artifacts", "alpha", self.other)

    def query(self, store=None, **kwargs):
        return (store or self.store).request("Api#user", "api_signature", ["Api.java"], owner="B001", **kwargs)

    def publish(self, claim, store=None, **kwargs):
        return (store or self.store).publish(claim["factId"], claim["claimToken"], owner="B001",
                                           content={"signature": "String user()"}, **kwargs)

    def test_simultaneous_cold_queries_have_one_producer_and_share_result(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.query(), range(8)))
        self.assertEqual(1, sum(r["status"] == "claimed" for r in results))
        claim = next(r for r in results if r["status"] == "claimed")
        self.publish(claim)
        hit = self.query(self.second)
        self.assertEqual("hit", hit["status"])
        self.assertEqual("source_observed", hit["fact"]["verification"])
        self.assertNotIn("claimToken", hit["fact"])

    def test_dirty_branch_and_build_changes_do_not_reuse_other_branch_fact(self):
        claim = self.query()
        self.publish(claim)
        (self.other / "Api.java").write_text("public class Api { public static int user() { return 1; } }")
        self.assertEqual("claimed", self.query(self.second)["status"])
        self.assertEqual("hit", self.query()["status"])
        (self.root / "pom.xml").write_text("<project><version>2</version></project>")
        self.assertEqual("claimed", self.query()["status"])

    def test_expired_producer_is_fenced_after_takeover(self):
        first = self.query(ttl=1)
        with patch("hooks.engineering_facts.time.time", return_value=time.time() + 2):
            second = self.query()
            self.assertEqual("claimed", second["status"])
            with self.assertRaisesRegex(ValueError, "expired_or_replaced"):
                self.publish(first)
            self.publish(second)

    def test_mutation_during_query_rejects_publication(self):
        claim = self.query()
        (self.root / "Api.java").write_text("changed")
        with self.assertRaisesRegex(ValueError, "sources_changed"):
            self.publish(claim)

    def test_corrupt_entry_rebuilt_once_and_negative_result_reused(self):
        first = self.query()
        Path(first["factPath"]).write_text("broken")
        rebuilt = self.query()
        self.assertEqual("claimed", rebuilt["status"])
        self.publish(rebuilt, failure=True, transient=True)
        self.assertEqual("cached_failure", self.query()["status"])
        with patch("hooks.engineering_facts.time.time", return_value=time.time() + 65):
            self.assertEqual("claimed", self.query()["status"])

    def test_source_symlink_and_parent_escape_rejected(self):
        (self.root / "escape.java").symlink_to(self.base / "outside")
        for source in ("../outside", "escape.java", ".git/config"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.store.request("Api", "api_signature", [source], owner="B001")

    def test_environment_recipe_invalidates_and_unrelated_facts_are_not_injected(self):
        claim = self.store.request("Api", "test_recipe", ["Api.java"], owner="B001", scope=environment_scope())
        self.publish(claim)
        self.assertEqual(1, len(self.store.related([], ["Api"])))
        self.assertEqual([], self.store.related(["Other.java"]))
        with patch.dict(os.environ, {"JAVA_HOME": "different-jdk"}):
            self.assertEqual([], self.store.related([], ["Api"]))

    def test_locator_only_returns_exact_tracked_filenames(self):
        (self.root / "Api2.java").write_text("untracked")
        self.assertEqual(["Api.java"], locate(self.root, "Api")["candidates"])
        self.assertEqual("missing", locate(self.root, "Api2")["status"])

    def test_resolved_dependency_content_change_invalidates_recipe(self):
        jar = self.base / "dependency.jar"
        jar.write_bytes(b"first dependency content")
        (self.root / "classpath.txt").write_text(str(jar))
        claim = self.store.request("Api", "test_recipe", ["Api.java"], owner="B001",
                                   scope=environment_scope(), classpath_file="classpath.txt")
        self.publish(claim)
        self.assertEqual(1, len(self.store.related([], ["Api"])))
        jar.write_bytes(b"other dependency content")
        self.assertEqual([], self.store.related([], ["Api"]))

    def test_replaced_build_file_symlink_is_not_read_outside_binding(self):
        claim = self.query()
        self.publish(claim)
        (self.root / "pom.xml").unlink()
        outside = self.base / "outside-pom.xml"
        outside.write_text("<project/>")
        (self.root / "pom.xml").symlink_to(outside)
        self.assertFalse(self.store.valid(read_json(Path(claim["factPath"]))))

    def test_dependency_lookup_shares_identical_builds_across_worktrees(self):
        jar = self.base / "shared.jar"
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("example/LoginContext.class", b"fixture class")
        args = argparse.Namespace(class_name="example.LoginContext")
        for root in (self.root, self.other):
            (root / "classpath.txt").write_text(str(jar))
        def lookup(root):
            args.classpath_file = str(root / "classpath.txt")
            ctx = (self.base / "artifacts", "alpha", root, root, {}, {}, self.base / "unused", None, {})
            return utest_fixture.lookup(args, ctx)
        self.assertFalse(lookup(self.root)["reused"])
        with patch("hooks.utest_runtime.zipfile.ZipFile", side_effect=AssertionError("must share dependency lookup")):
            self.assertTrue(lookup(self.other)["reused"])


class EngineeringStageTest(unittest.TestCase):
    def setUp(self):
        self.a = authority_fixture.CodeStageAuthorityTest()
        self.a.setUp()
        self.addCleanup(self.a.doCleanups)
        self.args = argparse.Namespace(action="prepare", workspace=str(self.a.workspace), feature="alpha",
            run_id=self.a.parallel_id, batch_id="B001", batch_worktree=str(self.a.worktree),
            task_id=["T001"], stage="code", owner_token=self.a.token)

    def test_prepare_is_idempotent_without_exploration_and_catalog_uses_real_parsers(self):
        with patch("hooks.engineering_context.locate", side_effect=AssertionError("no startup search")):
            first, second = execute(self.args), execute(self.args)
        self.assertEqual(first["contextDigest"], second["contextDigest"])
        self.assertFalse(first["discoveryRequired"])
        self.assertNotIn(self.a.token, Path(first["contextPath"]).read_text())
        catalog = read_json(Path(first["commandCatalogPath"]))["commands"]
        self.assertIn("prepare-compile", catalog["task_runner"]["commands"])
        self.assertIn("preflight", catalog["utest_runtime"]["commands"])

    def test_source_query_records_usage_and_finish_automatically_hands_it_off(self):
        task = self.a._task()
        (self.a.worktree / "production.txt").write_text("public class Api {}\n")
        self.args.action, self.args.symbol = "resolve", "Api"
        self.args.kind, self.args.source = "api_signature", ["production.txt"]
        result = execute(self.args)
        self.assertEqual("ready", result["status"])
        finished = self.a._finish(task)
        handoff = read_json(Path(finished["engineeringHandoff"]["path"]))
        self.assertIn(result["factId"], handoff["factIds"])
        self.assertEqual(finished["implementationEvidenceId"], handoff["implementationEvidenceId"])
        self.assertTrue(handoff["compileEvidenceIds"])

    def test_wrong_task_worktree_and_missing_lease_are_rejected(self):
        self.a._task()
        self.args.action, self.args.symbol = "query", "Api"
        self.args.kind, self.args.source = "api_signature", ["production.txt"]
        self.args.owner_token = "wrong"
        with self.assertRaisesRegex(ValueError, "lease_invalid"):
            execute(self.args)
        self.args.owner_token = self.a.token
        self.args.batch_worktree = str(self.a.repo)
        with self.assertRaisesRegex(ValueError, "worktree_mismatch"):
            execute(self.args)

    def test_review_failure_survives_reset_and_selects_affected_task(self):
        self.a._initial_delivery()
        start_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review")
        result = fail_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review",
                            failure_type="implementation", message="production.txt:1 expected authorized, actual allowed")
        self.assertEqual(["T001"], result["affectedTaskIds"])
        self.args.stage = "rework"
        pack = execute(self.args)
        self.assertEqual(["T001"], pack["repairTaskIds"])
        self.assertIn("actual allowed", pack["context"]["repair"]["failure"]["message"])
        self.assertEqual("pending", load_manifest(self.a.workspace, "alpha", self.a.parallel_id)["batches"]["B001"]["stageStates"]["review"]["status"])

    def test_runtime_artifacts_are_readable_but_not_directly_writable(self):
        result = execute(self.args)
        with patch.dict(os.environ, self.a.environment):
            self.assertIsNone(artifact_guard({"tool_name": "read_file", "tool_input": {"file_path": result["contextPath"]}}))
            self.assertIn("RUNTIME_ARTIFACT_OWNED", artifact_guard({"tool_name": "write_file", "tool_input": {"file_path": result["contextPath"]}}))
            self.assertIn("RUNTIME_ARTIFACT_OWNED", artifact_guard({"tool_name": "execute", "tool_input": {"command": "rm " + str(Path(result["commandCatalogPath"]))}}))

    def test_misbound_handoff_is_rejected_before_injection(self):
        finished, _ = self.a._initial_delivery()
        path = Path(finished["engineeringHandoff"]["path"])
        handoff = read_json(path)
        handoff["batchId"] = "B999"
        atomic_write_json(path, handoff)
        with self.assertRaisesRegex(ValueError, "handoff_identity_mismatch"):
            execute(self.args)

    def test_compile_operations_bind_real_task_run_and_real_parser(self):
        state = self.a._task()
        result = execute(self.args)
        operation = next(op for op in result["operations"] if op["action"] == "prepare_compile")
        from hooks.task_runner import build_parser
        parsed = build_parser().parse_args(operation["argv"][4:] + ["--execution-cwd", str(self.a.worktree), "--", "mvn", "compile"])
        self.assertEqual(state["runId"], parsed.run_id)
        self.assertEqual(self.a.token, parsed.lease_token)
        self.assertEqual(["--", "mvn", "compile"], parsed.argv)

    def test_real_cli_prepare_returns_exact_paths_without_help(self):
        result = subprocess.run([sys.executable, "hooks/engineering_context.py", "prepare",
            "--workspace", self.args.workspace, "--feature", "alpha", "--run-id", self.args.run_id,
            "--batch-id", "B001", "--batch-worktree", self.args.batch_worktree,
            "--stage", "code", "--owner-token", self.a.token], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertTrue(Path(json.loads(result.stdout)["contextPath"]).is_file())

    def test_invalid_cli_arguments_return_actual_schema(self):
        result = subprocess.run([sys.executable, "hooks/engineering_context.py", "query"], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        error = json.loads(result.stdout)
        self.assertEqual("invalid_arguments", error["reasonCode"])
        self.assertTrue(any(a["name"] == "source" and a["required"] for a in error["argumentSchema"]["arguments"]))

    def test_unresolved_repair_never_offers_all_task_repair_operations(self):
        self.a._initial_delivery()
        start_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review")
        failure = fail_stage(self.a.workspace, "alpha", self.a.parallel_id, "B001", "review",
                             failure_type="implementation", message="authorization mismatch without a location")
        original = Path(failure["repairContextPath"]).read_bytes()
        self.args.stage = "rework"
        pack = execute(self.args)
        self.assertEqual([], pack["repairTaskIds"])
        self.assertFalse(any(op["action"] == "start_task_repair" for op in pack["operations"]))
        content = self.a.worktree / "scope-input.json"
        content.write_text(json.dumps({"affectedTaskIds": ["T001"], "reason": "bounded finding attribution"}))
        self.args.action, self.args.content_file = "repair-scope", str(content)
        self.assertEqual(["T001"], execute(self.args)["repairTaskIds"])
        self.assertEqual(original, Path(failure["repairContextPath"]).read_bytes())


class EngineeringUtestTest(unittest.TestCase):
    def setUp(self):
        self.u = utest_fixture.UTestOptimizationTest()
        self.u.setUp()
        self.addCleanup(self.u.doCleanups)
        self.a = self.u.a
        self.args = argparse.Namespace(action="prepare", workspace=str(self.a.workspace), feature="alpha",
            run_id=self.a.parallel_id, batch_id="B001", batch_worktree=str(self.a.worktree),
            task_id=["T001"], stage="utest", owner_token=self.a.token)

    def run_test(self, body=None):
        self.u.write_test(body)
        prepared = utest_fixture.preflight(self.u.args, self.u.ctx())
        result = subprocess.run(prepared["bashCommand"], text=True, capture_output=True, timeout=20)
        return prepared, result

    def test_utest_loads_handoff_and_failure_pack_preserves_exact_command_and_budget(self):
        prepared, result = self.run_test('import unittest\nclass SampleTest(unittest.TestCase):\n def test_value(self):\n  self.assertEqual(1, 2)\n')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)  # Registration succeeded.
        self.args.stage = "fix"
        first = execute(self.args)
        response = first["context"]["resumeCursor"][0]["response"]
        self.assertEqual("FAIL", response["result"])
        self.assertEqual(1, response["exitCode"])
        self.assertEqual(self.u.args.argv, first["context"]["repair"]["failedCommands"][0]["argv"])
        self.assertEqual(["T001"], first["repairTaskIds"])
        self.assertTrue(first["context"]["repair"]["logs"])
        self.assertEqual("recorded", first["context"]["resumeCursor"][0]["status"])
        second = execute(self.args)
        self.assertEqual(first["context"]["deadlines"], second["context"]["deadlines"])
        self.assertEqual(first["contextDigest"], second["contextDigest"])
        self.assertEqual(self.a.worktree, Path(first["context"]["repair"]["failedCommands"][0]["cwd"]))

    def test_real_test_proof_attaches_only_after_execution(self):
        self.u.write_test()
        self.args.action, self.args.symbol = "query", "example.LoginContext"
        self.args.kind, self.args.source = "test_recipe", ["production.txt", "tests/test_sample.py"]
        claim = execute(self.args)
        content = self.a.worktree / "recipe-input.json"
        content.write_text(json.dumps({"fixture": "tests/test_sample.py", "verification": "model cannot grant pass"}))
        self.args.action, self.args.fact_id, self.args.claim_token, self.args.content_file = "publish", claim["factId"], claim["claimToken"], str(content)
        published = execute(self.args)
        self.assertEqual("source_observed", published["fact"]["verification"])
        prepared = utest_fixture.preflight(self.u.args, self.u.ctx())
        result = subprocess.run(prepared["bashCommand"], text=True, capture_output=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        fact = read_json(Path(published["factPath"]))
        self.assertEqual("referenced_test_executed", fact["verification"])
        self.assertTrue(fact["testProof"]["evidenceId"])

    def test_expired_budget_blocks_fact_queries_and_prepare_does_not_extend_it(self):
        utest_fixture.prepare(self.u.args, self.u.ctx())
        path = self.u.ctx()[-2]
        state = read_json(path)
        state["firstCommandDeadlineEpoch"] = time.time() - 1
        atomic_write_json(path, state)
        self.args.action, self.args.symbol = "query", "Api"
        self.args.kind, self.args.source = "api_signature", ["production.txt"]
        with self.assertRaisesRegex(ValueError, "budget_exhausted"):
            execute(self.args)
        self.args.action = "prepare"
        pack = execute(self.args)
        self.assertEqual(state["firstCommandDeadlineEpoch"], pack["context"]["deadlines"]["firstCommandDeadlineEpoch"])


if __name__ == "__main__":
    unittest.main()
