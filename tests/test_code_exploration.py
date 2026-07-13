from __future__ import annotations

import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class RepositorySnapshotTest(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "code"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")
        (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "initial")
        return repo

    def test_snapshot_captures_head_index_and_worktree_files(self) -> None:
        from hooks.repository_snapshot import capture_repository_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (repo / "new.txt").write_text("new\n", encoding="utf-8")

            snapshot = capture_repository_snapshot(repo)

            self.assertEqual(snapshot["headCommit"], _git(repo, "rev-parse", "HEAD"))
            self.assertEqual(snapshot["indexTree"], _git(repo, "write-tree"))
            self.assertEqual(set(snapshot["files"]), {"tracked.txt", "new.txt"})
            self.assertRegex(snapshot["files"]["tracked.txt"], r"^[0-9a-f]{64}$")

    def test_snapshot_diff_preserves_task_runner_change_details(self) -> None:
        from hooks.repository_snapshot import snapshot_changes

        before = {"a.py": "one", "delete.py": "gone", "old.py": "same"}
        after = {"a.py": "two", "new.py": "new", "renamed.py": "same"}

        changes = snapshot_changes(before, after)

        self.assertEqual(
            {(item["operation"], item["path"]) for item in changes},
            {
                ("modified", "a.py"),
                ("deleted", "delete.py"),
                ("created", "new.py"),
                ("renamed", "renamed.py"),
            },
        )
        renamed = next(item for item in changes if item["operation"] == "renamed")
        self.assertEqual(renamed["fromPath"], "old.py")
        self.assertEqual(renamed["kind"], "source")


class CacheClassificationTest(unittest.TestCase):
    def _snapshot(
        self,
        *,
        head: str = "head-1",
        files: dict[str, str | None] | None = None,
    ) -> dict:
        return {
            "headCommit": head,
            "indexTree": "tree-1",
            "files": files if files is not None else {"src/a.py": "old"},
        }

    def _cache(self, *, snapshot: dict | None = None) -> dict:
        return {
            "schemaVersion": "autodev.code-exploration.v1",
            "featureId": "alpha",
            "repository": {"id": "code", "root": "/tmp/code"},
            "executionLane": "backend",
            "capturedAt": "2026-07-13T00:00:00Z",
            "capturedBatchId": "B001",
            "capturedTaskId": "T001",
            "gitSnapshot": snapshot or self._snapshot(),
            "findings": {
                "moduleMap": [],
                "conventions": [],
                "integrationPoints": [],
                "testEntrypoints": [],
                "validationPatterns": [],
            },
            "exploredPaths": ["src/a.py"],
            "sharedPaths": [],
            "evidenceCoverage": {
                "explainedTaskIds": [],
                "completionEvidenceIds": [],
                "lastExplainedBatchId": None,
                "lastExplainedAt": None,
            },
        }

    def test_missing_cache_requires_record(self) -> None:
        from hooks.code_exploration import TrustedEvolution, classify_cache

        result = classify_cache(None, self._snapshot(), TrustedEvolution.empty())

        self.assertEqual(result["status"], "missing")
        self.assertTrue(result["policy"]["requiresRecord"])

    def test_equal_snapshot_is_fresh(self) -> None:
        from hooks.code_exploration import TrustedEvolution, classify_cache

        result = classify_cache(self._cache(), self._snapshot(), TrustedEvolution.empty())

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["changedPaths"], [])

    def test_head_change_is_stale(self) -> None:
        from hooks.code_exploration import TrustedEvolution, classify_cache

        result = classify_cache(
            self._cache(),
            self._snapshot(head="head-2"),
            TrustedEvolution.empty(),
        )

        self.assertEqual(result["status"], "stale")
        self.assertIn("head_commit_changed", result["staleReasons"])

    def test_critical_path_change_is_stale_even_when_evidence_explains_it(self) -> None:
        from hooks.code_exploration import TrustedEvolution, classify_cache

        cache = self._cache(snapshot=self._snapshot(files={"package.json": "old"}))
        current = self._snapshot(files={"package.json": "new"})
        trusted = TrustedEvolution(
            changed_paths=frozenset({"package.json"}),
            latest_files=current["files"],
            task_ids=("T001",),
            evidence_ids=("ev_0001",),
            untrusted_reasons=(),
        )

        result = classify_cache(cache, current, trusted)

        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["criticalHits"], ["package.json"])

    def test_explained_change_with_matching_final_snapshot_is_reusable(self) -> None:
        from hooks.code_exploration import TrustedEvolution, classify_cache

        current = self._snapshot(files={"src/a.py": "new"})
        trusted = TrustedEvolution(
            changed_paths=frozenset({"src/a.py"}),
            latest_files=current["files"],
            task_ids=("T001",),
            evidence_ids=("ev_0001",),
            untrusted_reasons=(),
        )

        result = classify_cache(self._cache(), current, trusted)

        self.assertEqual(result["status"], "reusable_with_changes")
        self.assertTrue(result["policy"]["requiresPatch"])
        self.assertEqual(result["matchedTaskIds"], ["T001"])

    def test_same_path_modified_after_task_completion_is_stale(self) -> None:
        from hooks.code_exploration import TrustedEvolution, classify_cache

        current = self._snapshot(files={"src/a.py": "manual-edit"})
        trusted = TrustedEvolution(
            changed_paths=frozenset({"src/a.py"}),
            latest_files={"src/a.py": "task-final"},
            task_ids=("T001",),
            evidence_ids=("ev_0001",),
            untrusted_reasons=(),
        )

        result = classify_cache(self._cache(), current, trusted)

        self.assertEqual(result["status"], "stale")
        self.assertIn("current_snapshot_not_latest_task_snapshot", result["staleReasons"])


class CodeExplorationWriterTest(unittest.TestCase):
    def _run_writer(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "hooks/code_exploration_writer.py", *args],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

    def _body(self, path: Path) -> None:
        path.write_text(
            """{
  "findings": {
    "moduleMap": [{"path": "src", "role": "application module", "dependsOn": [], "ownerLane": "backend"}],
    "conventions": [],
    "integrationPoints": [],
    "testEntrypoints": [],
    "validationPatterns": []
  },
  "exploredPaths": ["src"],
  "sharedPaths": []
}
""",
            encoding="utf-8",
        )

    def test_contract_exports_cache_policy_and_lane_rules(self) -> None:
        result = self._run_writer("contract")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schemaVersion"], "autodev.code-exploration.v1")
        self.assertEqual(payload["executionLanes"], ["backend", "frontend"])
        self.assertEqual(payload["policies"]["reusable_with_changes"]["requiresPatch"], True)
        self.assertIn("package.json", payload["criticalPathRules"]["basenames"])
        self.assertEqual(
            payload["invalidationRules"]["headCommitChanged"],
            "stale_even_when_file_hashes_match",
        )
        self.assertEqual(
            payload["runtimeIgnoreRequirements"]["paths"],
            [".autobizdevops/", ".cmbdevclaw/"],
        )
        self.assertEqual(payload["recordExample"]["findings"]["moduleMap"][0]["ownerLane"], "backend")
        self.assertEqual(payload["patchExample"]["findingUpdates"], {})
        self.assertEqual(payload["patchExample"]["reviewedPaths"], ["src/service.py"])

    def test_record_missing_cache_then_inspect_fresh_and_reject_wrong_cas(self) -> None:
        from tests.test_task_runner import _workspace

        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            body = Path(tmp) / "record.json"
            self._body(body)
            record = self._run_writer(
                "record",
                "--workspace", str(workspace),
                "--feature", "alpha",
                "--task-id", "T001",
                "--code-workspace", str(repo),
                "--expected-cache-sha256", "missing",
                "--body-file", str(body),
            )
            self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
            cache_path = feature_dir / "cache" / "code-exploration" / repo.name / "backend.json"
            self.assertTrue(cache_path.is_file())

            inspected = self._run_writer(
                "inspect",
                "--workspace", str(workspace),
                "--feature", "alpha",
                "--task-id", "T001",
                "--code-workspace", str(repo),
            )
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            self.assertEqual(json.loads(inspected.stdout)["explorationCaches"][0]["status"], "fresh")

            (repo / "existing.txt").write_text("changed\n", encoding="utf-8")
            wrong = self._run_writer(
                "record",
                "--workspace", str(workspace),
                "--feature", "alpha",
                "--task-id", "T001",
                "--code-workspace", str(repo),
                "--expected-cache-sha256", "0" * 64,
                "--body-file", str(body),
            )
            self.assertNotEqual(wrong.returncode, 0)
            self.assertIn("code_exploration_cache_sha_mismatch", wrong.stdout)

    def test_completed_task_change_is_reusable_then_patch_advances_to_fresh(self) -> None:
        from tests.test_task_runner import _read_batch, _workspace, _write_batch

        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            second = copy.deepcopy(batch["tasks"][0])
            second.update({"id": "T002", "title": "second", "deps": ["T001"]})
            second["acceptanceCriteria"][0]["id"] = "AC-T002-01"
            second["validationCommands"][0].update(
                {"id": "VAL-T002-01", "covers": ["AC-T002-01"]}
            )
            root["batches"][0]["taskIds"].append("T002")
            batch["tasks"].append(second)
            batch["taskCount"] = 2
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _write_batch(feature_dir, batch)

            body = Path(tmp) / "record.json"
            self._body(body)
            recorded = self._run_writer(
                "record", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(repo),
                "--expected-cache-sha256", "missing", "--body-file", str(body),
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

            started = subprocess.run(
                [sys.executable, "hooks/task_runner.py", "start", "--workspace", str(workspace),
                 "--feature", "alpha", "--task-id", "T001", "--code-workspace", str(repo)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            run_id = json.loads(started.stdout)["runId"]
            (repo / "existing.txt").write_text("implemented\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "hooks/task_runner.py", "complete", "--workspace", str(workspace),
                 "--feature", "alpha", "--task-id", "T001", "--run-id", run_id,
                 "--code-workspace", str(repo)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            inspected = self._run_writer(
                "inspect", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(repo),
            )
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            cache = json.loads(inspected.stdout)["explorationCaches"][0]
            self.assertEqual(cache["status"], "reusable_with_changes")
            self.assertEqual(cache["matchedTaskIds"], ["T001"])
            self.assertEqual(cache["changedPaths"], ["existing.txt"])

            patch_body = Path(tmp) / "patch.json"
            cache_path = feature_dir / "cache" / "code-exploration" / repo.name / "backend.json"
            before_rejected_patch = cache_path.read_bytes()
            patch_body.write_text(
                json.dumps(
                    {
                        "reviewedPaths": [],
                        "findingUpdates": {},
                        "exploredPathsAdd": [],
                        "sharedPathsAdd": [],
                    }
                ),
                encoding="utf-8",
            )
            rejected = self._run_writer(
                "patch", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(repo),
                "--expected-cache-sha256", cache["cacheSha256"], "--body-file", str(patch_body),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("code_exploration_reviewed_paths_incomplete:existing.txt", rejected.stdout)
            self.assertEqual(cache_path.read_bytes(), before_rejected_patch)

            patch_body.write_text(
                json.dumps(
                    {
                        "reviewedPaths": ["existing.txt"],
                        "findingUpdates": {
                            "testEntrypoints": [
                                {"cwd": ".", "scope": "missing required argv"}
                            ]
                        },
                        "exploredPathsAdd": [],
                        "sharedPathsAdd": [],
                    }
                ),
                encoding="utf-8",
            )
            invalid_findings = self._run_writer(
                "patch", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(repo),
                "--expected-cache-sha256", cache["cacheSha256"], "--body-file", str(patch_body),
            )
            self.assertNotEqual(invalid_findings.returncode, 0)
            self.assertIn("code_exploration_findings_invalid", invalid_findings.stdout)
            self.assertEqual(cache_path.read_bytes(), before_rejected_patch)

            patch_body.write_text(
                json.dumps(
                    {
                        "reviewedPaths": ["existing.txt"],
                        "findingUpdates": {},
                        "exploredPathsAdd": [],
                        "sharedPathsAdd": [],
                    }
                ),
                encoding="utf-8",
            )
            patched = self._run_writer(
                "patch", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(repo),
                "--expected-cache-sha256", cache["cacheSha256"], "--body-file", str(patch_body),
            )
            self.assertEqual(patched.returncode, 0, patched.stdout + patched.stderr)

            fresh = self._run_writer(
                "inspect", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T002", "--code-workspace", str(repo),
            )
            self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
            self.assertEqual(json.loads(fresh.stdout)["explorationCaches"][0]["status"], "fresh")

    def test_two_completed_tasks_are_absorbed_by_one_patch(self) -> None:
        from tests.test_task_runner import _read_batch, _workspace, _write_batch

        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            root_path = feature_dir / "plan.json"
            root = json.loads(root_path.read_text(encoding="utf-8"))
            batch = _read_batch(feature_dir)
            base_task = batch["tasks"][0]
            tasks = [base_task]
            for index, dependencies in ((2, []), (3, ["T001", "T002"])):
                item = copy.deepcopy(base_task)
                task_id = f"T{index:03d}"
                item.update({"id": task_id, "title": task_id, "deps": dependencies})
                item["acceptanceCriteria"][0]["id"] = f"AC-{task_id}-01"
                item["validationCommands"][0].update(
                    {"id": f"VAL-{task_id}-01", "covers": [f"AC-{task_id}-01"]}
                )
                tasks.append(item)
            root["batches"][0]["taskIds"] = [item["id"] for item in tasks]
            batch["tasks"] = tasks
            batch["taskCount"] = len(tasks)
            root_path.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _write_batch(feature_dir, batch)

            body = Path(tmp) / "record.json"
            self._body(body)
            recorded = self._run_writer(
                "record", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(repo),
                "--expected-cache-sha256", "missing", "--body-file", str(body),
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)

            # Independent tasks may complete in a different order than the plan.
            for task_id in ("T002", "T001"):
                started = subprocess.run(
                    [sys.executable, "hooks/task_runner.py", "start", "--workspace", str(workspace),
                     "--feature", "alpha", "--task-id", task_id, "--code-workspace", str(repo)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
                )
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                (repo / "existing.txt").write_text(f"{task_id}\n", encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, "hooks/task_runner.py", "complete", "--workspace", str(workspace),
                     "--feature", "alpha", "--task-id", task_id,
                     "--run-id", json.loads(started.stdout)["runId"], "--code-workspace", str(repo)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            inspected = self._run_writer(
                "inspect", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T003", "--code-workspace", str(repo),
            )
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            cache = json.loads(inspected.stdout)["explorationCaches"][0]
            self.assertEqual(cache["status"], "reusable_with_changes")
            self.assertEqual(cache["matchedTaskIds"], ["T001", "T002"])
            self.assertEqual(len(cache["matchedEvidenceIds"]), 2)

            patch_body = Path(tmp) / "patch.json"
            patch_body.write_text(
                json.dumps(
                    {
                        "reviewedPaths": cache["changedPaths"],
                        "findingUpdates": {},
                        "exploredPathsAdd": [],
                        "sharedPathsAdd": [],
                    }
                ),
                encoding="utf-8",
            )
            patched = self._run_writer(
                "patch", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T003", "--code-workspace", str(repo),
                "--expected-cache-sha256", cache["cacheSha256"], "--body-file", str(patch_body),
            )
            self.assertEqual(patched.returncode, 0, patched.stdout + patched.stderr)
            cache_path = feature_dir / "cache" / "code-exploration" / repo.name / "backend.json"
            persisted = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["evidenceCoverage"]["explainedTaskIds"], ["T001", "T002"])

            fresh = self._run_writer(
                "inspect", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T003", "--code-workspace", str(repo),
            )
            self.assertEqual(json.loads(fresh.stdout)["explorationCaches"][0]["status"], "fresh")


class CodeTaskContextCacheTest(unittest.TestCase):
    def test_context_returns_missing_cache_policy_for_business_repository(self) -> None:
        from hooks.code_task_context import build_context
        from tests.test_task_runner import _workspace

        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                "### Requirement [REQ-001]: capability\n"
                "#### Scenario [SCN-001]: observable behavior\n",
                encoding="utf-8",
            )

            result = build_context(
                workspace=workspace,
                feature="alpha",
                task_id="T001",
                code_workspaces=[repo],
            )

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.data["executionLane"], "backend")
            self.assertEqual(result.data["explorationCaches"][0]["status"], "missing")
            self.assertEqual(
                result.data["explorationPolicy"],
                {
                    "status": "missing",
                    "explorationPolicy": "full_bounded_explore",
                    "requiresRecord": True,
                    "requiresPatch": False,
                },
            )

    def test_backend_context_never_projects_frontend_shared_findings(self) -> None:
        from hooks.code_task_context import build_context
        from tests.test_task_runner import _workspace

        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, repo = _workspace(Path(tmp))
            spec_dir = feature_dir / "specs" / "cap"
            spec_dir.mkdir(parents=True)
            (spec_dir / "spec.md").write_text(
                "### Requirement [REQ-001]: capability\n"
                "#### Scenario [SCN-001]: observable behavior\n",
                encoding="utf-8",
            )
            body = Path(tmp) / "record.json"
            body.write_text(
                json.dumps(
                    {
                        "findings": {
                            "moduleMap": [{"path": "shared", "role": "backend contract"}],
                            "conventions": [], "integrationPoints": [],
                            "testEntrypoints": [], "validationPatterns": [],
                        },
                        "exploredPaths": ["shared/contract.ts"],
                        "sharedPaths": ["shared/contract.ts"],
                    }
                ),
                encoding="utf-8",
            )
            writer = CodeExplorationWriterTest()
            recorded = writer._run_writer(
                "record", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(repo),
                "--expected-cache-sha256", "missing", "--body-file", str(body),
            )
            self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
            backend_path = feature_dir / "cache" / "code-exploration" / repo.name / "backend.json"
            frontend_path = backend_path.with_name("frontend.json")
            frontend = json.loads(backend_path.read_text(encoding="utf-8"))
            frontend["executionLane"] = "frontend"
            frontend["findings"]["moduleMap"] = [{"path": "shared", "role": "frontend contract"}]
            frontend_path.write_text(json.dumps(frontend, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = build_context(
                workspace=workspace,
                feature="alpha",
                task_id="T001",
                code_workspaces=[repo],
            )

            self.assertTrue(result.ok, result.errors)
            cache = result.data["explorationCaches"][0]
            self.assertEqual(cache["executionLane"], "backend")
            self.assertEqual(cache["findings"]["moduleMap"][0]["role"], "backend contract")
            self.assertNotIn("frontend contract", json.dumps(cache, ensure_ascii=False))
            self.assertTrue(frontend_path.is_file())


if __name__ == "__main__":
    unittest.main()
