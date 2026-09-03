#!/usr/bin/env python3
"""Regression tests for conflict analysis and merge-train recovery."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hooks.conflict_resolution_agent import ConflictAnalyzer, ModelBasedResolver
from hooks.conflict_types import CandidateStatus, ConflictContext, ResolutionResult
from hooks.parallel_merge_train import discard_candidate, resolve_candidate, resume_candidate


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


class ConflictAnalyzerTest(unittest.TestCase):
    def test_only_distinct_function_additions_are_auto_mergeable(self) -> None:
        analyzer = ConflictAnalyzer()
        content = """<<<<<<< ours
def method_a():
    return \"a\"
=======
def method_b():
    return \"b\"
>>>>>>> theirs
"""
        self.assertEqual(analyzer._classify_conflict("service.py", content).value, "append_only")

    def test_duplicate_function_name_requires_manual_resolution(self) -> None:
        analyzer = ConflictAnalyzer()
        content = """<<<<<<< ours
def method_a():
    return \"a\"
=======
def method_a():
    return \"b\"
>>>>>>> theirs
"""
        self.assertNotEqual(analyzer._classify_conflict("service.py", content).value, "append_only")

    def test_empty_marker_map_is_not_auto_mergeable(self) -> None:
        context = ConflictContext(
            base_sha="base",
            batch_ids=["B001", "B002"],
            conflicted_files=["binary.dat"],
            candidate_worktree="/tmp/candidate",
            conflict_markers={},
            repository_ref="default",
            wave=1,
        )
        self.assertEqual(ConflictAnalyzer().analyze(context)["recommended_strategy"], "model_assisted")


class AutoMergeTest(unittest.TestCase):
    def test_opted_in_auto_merge_commits_a_real_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.name", "Test User")
            _git(repo, "config", "user.email", "test@example.com")
            source = repo / "service.py"
            source.write_text("class Service:\n    # extension point\n", encoding="utf-8")
            _git(repo, "add", "service.py")
            _git(repo, "commit", "-m", "base")
            _git(repo, "checkout", "-b", "delivery-a")
            source.write_text("class Service:\n    def method_a(self):\n        return 'a'\n", encoding="utf-8")
            _git(repo, "commit", "-am", "delivery a")
            _git(repo, "checkout", "main")
            source.write_text("class Service:\n    def method_b(self):\n        return 'b'\n", encoding="utf-8")
            _git(repo, "commit", "-am", "delivery b")
            merge = _git(repo, "merge", "delivery-a", check=False)
            self.assertNotEqual(merge.returncode, 0, merge.stdout + merge.stderr)
            conflict = source.read_text(encoding="utf-8")
            context = ConflictContext(
                base_sha=_git(repo, "merge-base", "main", "delivery-a").stdout.strip(),
                batch_ids=["B001", "B002"],
                conflicted_files=["service.py"],
                candidate_worktree=str(repo),
                conflict_markers={"service.py": conflict},
                repository_ref="default",
                wave=1,
                task_card_id="Z990692-294",
            )

            result = ModelBasedResolver(enable_auto_commit=True).resolve(context)

            self.assertEqual(result.status, "resolved", result.reason)
            self.assertEqual(result.strategy_used, "auto_merge_append_only")
            resolved = source.read_text(encoding="utf-8")
            self.assertIn("method_a", resolved)
            self.assertIn("method_b", resolved)
            self.assertNotIn("<<<<<<<", resolved)
            self.assertEqual(_git(repo, "status", "--porcelain").stdout, "")
            self.assertEqual(
                _git(repo, "log", "-1", "--format=%s").stdout.strip(),
                "Z990692-294 #comment 解决冲突 B001, B002",
            )


class CandidateRecoveryTest(unittest.TestCase):
    def _manifest(self, status: str = CandidateStatus.CANDIDATE_CONFLICTED.value) -> dict:
        return {
            "status": "blocked",
            "repositories": {"default": {"gitRoot": "/repo"}},
            "mergeTrains": {
                "default:wave-001": {
                    "repositoryRef": "default",
                    "wave": 1,
                    "status": status,
                    "worktreePath": "/tmp/candidate",
                    "branchName": "candidate",
                    "conflictContext": {
                        "baseSha": "base",
                        "batchIds": ["B001", "B002"],
                        "conflictedFiles": ["service.py"],
                        "candidateWorktree": "/tmp/candidate",
                        "conflictMarkers": {"service.py": "<<<<<<< ours\ndef a(): pass\n=======\ndef b(): pass\n>>>>>>> theirs"},
                        "repositoryRef": "default",
                        "wave": 1,
                        "attempts": 0,
                    },
                }
            },
            "runtimeConfig": {"conflictResolution": {"maxAttempts": 2, "enableAutoResolve": True}},
        }

    def test_resolve_candidate_records_strategy_and_unblocks_run(self) -> None:
        manifest = self._manifest()
        resolved = ResolutionResult("resolved", ["service.py"], [], "resolved-sha", strategy_used="auto_merge_append_only")
        with mock.patch("hooks.parallel_merge_train.run_lock", return_value=contextlib.nullcontext()), mock.patch(
            "hooks.parallel_merge_train.load_manifest", return_value=manifest
        ), mock.patch("hooks.parallel_merge_train.save_manifest"), mock.patch(
            "hooks.parallel_merge_train.append_event"
        ), mock.patch("hooks.parallel_merge_train._head", return_value="resolved-sha"), mock.patch(
            "hooks.parallel_merge_train.ModelBasedResolver"
        ) as resolver:
            resolver.return_value.resolve.return_value = resolved
            result = resolve_candidate(Path("/tmp"), "feature", "run", 1, "default")

        self.assertTrue(result["success"])
        record = manifest["mergeTrains"]["default:wave-001"]
        self.assertEqual(record["status"], CandidateStatus.BUILT.value)
        self.assertEqual(record["resolutionMethod"], "auto_merge_append_only")
        self.assertNotIn("conflictContext", record)
        self.assertEqual(manifest["status"], "running")

    def test_resume_candidate_marks_clean_manual_commit_built(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            manifest = self._manifest(CandidateStatus.NEEDS_RESOLUTION.value)
            manifest["mergeTrains"]["default:wave-001"]["worktreePath"] = str(worktree)
            with mock.patch("hooks.parallel_merge_train.run_lock", return_value=contextlib.nullcontext()), mock.patch(
                "hooks.parallel_merge_train.load_manifest", return_value=manifest
            ), mock.patch("hooks.parallel_merge_train.save_manifest"), mock.patch(
                "hooks.parallel_merge_train.append_event"
            ), mock.patch("hooks.parallel_merge_train.git_status_porcelain", return_value=subprocess.CompletedProcess([], 0, "", "")), mock.patch(
                "hooks.parallel_merge_train._head", return_value="manual-sha"
            ), mock.patch(
                "hooks.parallel_merge_train._git",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 1, "", ""),
                ],
            ):
                result = resume_candidate(Path("/tmp"), "feature", "run", 1, "default")

        self.assertTrue(result["success"])
        self.assertEqual(manifest["mergeTrains"]["default:wave-001"]["status"], CandidateStatus.BUILT.value)
        self.assertEqual(manifest["status"], "running")

    def test_resume_candidate_allows_a_manually_fixed_failed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            manifest = self._manifest(CandidateStatus.FAILED.value)
            manifest["mergeTrains"]["default:wave-001"]["worktreePath"] = str(worktree)
            with mock.patch("hooks.parallel_merge_train.run_lock", return_value=contextlib.nullcontext()), mock.patch(
                "hooks.parallel_merge_train.load_manifest", return_value=manifest
            ), mock.patch("hooks.parallel_merge_train.save_manifest"), mock.patch(
                "hooks.parallel_merge_train.append_event"
            ), mock.patch("hooks.parallel_merge_train.git_status_porcelain", return_value=subprocess.CompletedProcess([], 0, "", "")), mock.patch(
                "hooks.parallel_merge_train._head", return_value="manual-fixed-sha"
            ), mock.patch(
                "hooks.parallel_merge_train._git",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 1, "", ""),
                ],
            ):
                result = resume_candidate(Path("/tmp"), "feature", "run", 1, "default")

        self.assertTrue(result["success"])
        self.assertEqual(result["candidateSha"], "manual-fixed-sha")
        self.assertEqual(manifest["mergeTrains"]["default:wave-001"]["status"], CandidateStatus.BUILT.value)

    def test_resolve_candidate_rejects_malformed_conflict_context_without_raising(self) -> None:
        manifest = self._manifest()
        manifest["mergeTrains"]["default:wave-001"]["conflictContext"].pop("candidateWorktree")
        with mock.patch("hooks.parallel_merge_train.run_lock", return_value=contextlib.nullcontext()), mock.patch(
            "hooks.parallel_merge_train.load_manifest", return_value=manifest
        ):
            result = resolve_candidate(Path("/tmp"), "feature", "run", 1, "default")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_conflict_context:candidateWorktree")

    def test_discard_uses_repository_mapping(self) -> None:
        manifest = self._manifest(CandidateStatus.NEEDS_RESOLUTION.value)
        with mock.patch("hooks.parallel_merge_train.run_lock", return_value=contextlib.nullcontext()), mock.patch(
            "hooks.parallel_merge_train.load_manifest", return_value=manifest
        ), mock.patch("hooks.parallel_merge_train.save_manifest"), mock.patch(
            "hooks.parallel_merge_train.append_event"
        ), mock.patch("hooks.parallel_merge_train._remove_candidate", return_value=[]):
            result = discard_candidate(Path("/tmp"), "feature", "run", 1, "default")

        self.assertTrue(result["success"])
        self.assertEqual(manifest["mergeTrains"]["default:wave-001"]["status"], CandidateStatus.DISCARDED.value)
        self.assertEqual(manifest["status"], "running")


if __name__ == "__main__":
    unittest.main()
