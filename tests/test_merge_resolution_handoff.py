"""Exercise the model-to-plugin handoff with real native Git worktrees."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "hooks"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import frontend_route_write_guard as write_guard
from hooks.parallel_merge_train import _candidate_paths, complete_resolution, prepare_resolution
from hooks.parallel_runtime import load_manifest, save_manifest


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


@contextlib.contextmanager
def conflicted_candidate(filename: str = "src/service.py", maximum: int = 2):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = root / "business"
        repo.mkdir()
        workspace = root / "artifacts"
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Merge Test")
        git(repo, "config", "user.email", "merge@example.com")
        source = repo / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("value = 'base'\n", encoding="utf-8")
        (repo / "clean.txt").write_text("base\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "base")
        git(repo, "checkout", "-b", "delivery")
        source.write_text("value = 'delivery'\n", encoding="utf-8")
        (repo / "clean.txt").write_text("auto-merged delivery\n", encoding="utf-8")
        git(repo, "commit", "-am", "delivery")
        delivery = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "checkout", "main")
        source.write_text("value = 'main'\n", encoding="utf-8")
        git(repo, "commit", "-am", "main update")
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        candidate, branch = _candidate_paths(workspace, "feature", "run", "default", 1)
        candidate.parent.mkdir(parents=True)
        git(repo, "worktree", "add", "-b", branch, str(candidate), base)
        merge = git(candidate, "merge", "--no-ff", "-m", "merge delivery", "delivery", check=False)
        assert merge.returncode != 0 and "CONFLICT" in merge.stdout
        record = {
            "repositoryRef": "default", "wave": 1, "batchIds": ["B001"],
            "baseSha": base, "deliveryCommits": {"B001": delivery},
            "worktreePath": str(candidate), "branchName": branch, "status": "candidate_conflicted",
            "conflictContext": {
                "baseSha": base, "batchIds": ["B001"], "conflictedFiles": [filename],
                "candidateWorktree": str(candidate), "repositoryRef": "default", "wave": 1,
                "attempts": 0, "taskCardId": "Z990692-294",
                "conflictMarkers": {filename: (candidate / filename).read_text(encoding="utf-8")},
            },
        }
        manifest = {
            "runId": "run", "featureId": "feature", "status": "blocked", "taskCardId": "Z990692-294",
            "repositories": {"default": {"gitRoot": str(repo)}},
            "mergeTrains": {"default:wave-001": record},
            "runtimeConfig": {"conflictResolution": {"maxAttempts": maximum, "enableAutoResolve": False}},
        }
        save_manifest(workspace, "feature", "run", manifest)
        (workspace / ".autobizdevops/state.json").write_text("{}", encoding="utf-8")
        yield workspace, repo, candidate, filename, base, delivery


def prepare(workspace: Path):
    return prepare_resolution(workspace, "feature", "run", 1, "default")


def complete(workspace: Path):
    return complete_resolution(workspace, "feature", "run", 1, "default")


class MergeResolutionHandoffTest(unittest.TestCase):
    def test_plugin_commits_unstaged_model_edits_and_preserves_auto_merged_files(self):
        with conflicted_candidate(maximum=1) as (workspace, repo, candidate, filename, base, delivery):
            self.assertEqual(prepare(workspace)["status"], "resolution_required")
            # Simulate the model: only edit the file, without staging or committing.
            (candidate / filename).write_text("values = ['main', 'delivery']\n", encoding="utf-8")
            result = complete(workspace)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["resolutionAttempts"], 1)
            self.assertEqual(git(candidate, "show", "-s", "--format=%P", "HEAD").stdout.split(), [base, delivery])
            self.assertEqual(git(candidate, "show", "-s", "--format=%s", "HEAD").stdout.strip(),
                             "Z990692-294 #comment cmbdevcalw提交 解决冲突 B001")
            self.assertEqual((candidate / "clean.txt").read_text(), "auto-merged delivery\n")
            self.assertEqual(git(candidate, "status", "--porcelain").stdout, "")
            self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), base)
            self.assertNotEqual(result["candidateSha"], base)
            self.assertEqual(complete(workspace)["candidateSha"], result["candidateSha"])
            self.assertTrue(prepare(workspace)["reused"])

    def test_remaining_markers_prevent_commit(self):
        with conflicted_candidate() as (workspace, _, candidate, _, base, _):
            prepare(workspace)
            result = complete(workspace)
            self.assertFalse(result["success"])
            self.assertIn("candidate_conflict_markers_remain", result["error"])
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)

    def test_platform_journals_are_preserved_and_never_committed(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            journal = candidate / ".cmbdevclaw/workflows/workflow/state.json"
            journal.parent.mkdir(parents=True)
            journal.write_text("{}")
            result = complete(workspace)
            self.assertTrue(result["success"], result)
            self.assertTrue(journal.is_file())
            self.assertEqual(git(candidate, "ls-files", ".cmbdevclaw/").stdout, "")
            self.assertTrue(prepare(workspace)["reused"])

    def test_plugin_stages_deletions_and_literal_paths(self):
        with conflicted_candidate("src/service [new].py") as (workspace, _, candidate, filename, base, delivery):
            self.assertTrue(prepare(workspace)["success"])
            (candidate / filename).unlink()
            result = complete(workspace)
            self.assertTrue(result["success"], result)
            self.assertFalse((candidate / filename).exists())
            self.assertEqual(git(candidate, "show", "-s", "--format=%P", "HEAD").stdout.split(), [base, delivery])

    def test_changes_outside_conflict_files_prevent_commit(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, base, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            (candidate / "clean.txt").write_text("unrelated edit\n")
            result = complete(workspace)
            self.assertEqual(result["error"], "candidate_resolution_out_of_scope_changes")
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)

    def test_unrelated_staged_changes_cannot_be_hidden_by_restoring_the_working_copy(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, base, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            clean = candidate / "clean.txt"
            clean.write_text("unrelated staged edit\n")
            git(candidate, "add", "clean.txt")
            clean.write_text("auto-merged delivery\n")
            self.assertEqual(complete(workspace)["error"], "candidate_resolution_out_of_scope_changes")
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)

    def test_candidate_writes_have_their_own_authorization_including_frontend_and_tests(self):
        for filename in ("src/service.py", "src/app.tsx", "src/test/test_service.py", "pom.xml"):
            with self.subTest(filename=filename), conflicted_candidate(filename) as (workspace, _, candidate, _, _, _):
                prepare(workspace)
                payload = json.dumps({"cwd": str(workspace), "tool_input": {"file_path": str(candidate / filename)}})
                with mock.patch.object(write_guard, "read_stdin_text", return_value=payload), mock.patch.object(
                    write_guard, "workspace_from_payload", return_value=workspace
                ), mock.patch.object(write_guard, "current_feature", return_value="feature"), mock.patch.object(
                    write_guard, "current_checkpoint", return_value="code_in_progress"
                ):
                    # No active production TASK or frontend route run is needed.
                    self.assertEqual(write_guard.main(), 0)
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / "other.py"), 2)
                (candidate / filename).write_text("resolved\n")
                self.assertTrue(complete(workspace)["success"])
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / filename), 2)

    def test_git_identity_changes_revoke_write_permission(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            git(candidate, "merge", "--abort")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / filename), 2)
            self.assertEqual(complete(workspace)["error"], "candidate_resolution_commit_mismatch")

    def test_needs_resolution_can_be_prepared_again_with_a_bounded_budget(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            manifest = load_manifest(workspace, "feature", "run")
            manifest["mergeTrains"]["default:wave-001"]["status"] = "needs_resolution"
            save_manifest(workspace, "feature", "run", manifest)
            self.assertEqual(prepare(workspace)["attempts"], 2)
            self.assertEqual(prepare(workspace)["error"], "max_resolution_attempts_exceeded")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(write_guard.validate_merge_resolution_write(workspace, "feature", candidate / filename), 2)

    def test_repeated_preparation_of_the_same_workflow_step_does_not_consume_attempts(self):
        with conflicted_candidate(maximum=1) as (workspace, _, _, _, _, _):
            first = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="immediate")
            repeated = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="immediate")
            self.assertEqual(first["attempts"], 1)
            self.assertEqual(repeated["attempts"], 1)
            self.assertTrue(repeated["reused"])
            final = prepare_resolution(workspace, "feature", "run", 1, "default", attempt_id="final")
            self.assertEqual(final["error"], "max_resolution_attempts_exceeded")

    def test_commit_hooks_remain_enabled_and_commit_retry_does_not_need_model_edits(self):
        with conflicted_candidate() as (workspace, repo, candidate, filename, base, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            hook = repo / ".git/hooks/pre-commit"
            hook.write_text("#!/bin/sh\necho 'test rejection' >&2\nexit 1\n")
            hook.chmod(0o755)
            result = complete(workspace)
            self.assertIn("candidate_resolution_commit_failed", result["error"])
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), base)
            self.assertEqual(prepare(workspace)["status"], "ready_to_complete")
            hook.unlink()
            self.assertTrue(complete(workspace)["success"])

    def test_commit_success_before_manifest_failure_is_recovered_without_another_commit(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            prepare(workspace)
            (candidate / filename).write_text("resolved\n")
            real_save = save_manifest

            def interrupted_save(workspace, feature, run_id, manifest):
                if manifest["mergeTrains"]["default:wave-001"]["status"] == "built":
                    raise OSError("simulated registration interruption")
                real_save(workspace, feature, run_id, manifest)

            with mock.patch("hooks.parallel_merge_train.save_manifest", side_effect=interrupted_save):
                self.assertFalse(complete(workspace)["success"])
            sha = git(candidate, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(prepare(workspace)["status"], "ready_to_complete")
            self.assertEqual(complete(workspace)["candidateSha"], sha)
            self.assertEqual(git(candidate, "rev-parse", "HEAD").stdout.strip(), sha)

    def test_cli_handoff_uses_plugin_commands_for_both_operations(self):
        with conflicted_candidate() as (workspace, _, candidate, filename, _, _):
            arguments = ["--workspace", str(workspace), "--feature", "feature", "--run-id", "run", "--repository-ref", "default", "--wave", "1"]
            script = str(ROOT / "hooks/parallel_merge_train.py")
            prepared = subprocess.run([sys.executable, "-X", "utf8", script, "prepare-resolution", *arguments], capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            self.assertEqual(json.loads(prepared.stdout)["status"], "resolution_required")
            (candidate / filename).write_text("resolved\n")
            committed = subprocess.run([sys.executable, "-X", "utf8", script, "complete-resolution", *arguments], capture_output=True, text=True)
            self.assertEqual(committed.returncode, 0, committed.stdout + committed.stderr)
            self.assertEqual(json.loads(committed.stdout)["status"], "built")


if __name__ == "__main__":
    unittest.main()
