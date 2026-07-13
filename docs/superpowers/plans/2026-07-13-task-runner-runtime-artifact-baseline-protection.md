# Task Runner Runtime Artifact And Baseline Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent CMB DevClaw runtime files from contaminating task snapshots, preserve the original task baseline across operator mistakes, and provide a safe recovery path for accidentally aborted runs.

**Architecture:** Extend `hooks/repository_snapshot.py` with a narrow Git-ignore policy check, while retaining repository-wide content snapshots. Keep lifecycle rules in `hooks/task_runner.py`: structured errors explain resolved Git roots, abort refuses to discard a changed baseline, resume reuses an aborted run's original snapshot, and verified-existing completion checks prior aborted baselines.

**Tech Stack:** Python 3 standard library, Git CLI, argparse, JSON, unittest, Markdown workflow documentation.

---

## Execution Note

The current checkout contains relevant uncommitted extraction work in `hooks/repository_snapshot.py`, `hooks/task_runner.py`, and their tests. Execute in place and preserve those edits. Do not create intermediate implementation commits that would capture pre-existing changes in the same files; use focused passing test commands as checkpoints and leave the final commit decision to the workspace owner.

### Task 1: Enforce The Runtime Artifact Ignore Contract

**Files:**
- Modify: `hooks/repository_snapshot.py`
- Modify: `tests/test_code_exploration.py`
- Modify: `tests/test_task_runner.py`
- Modify: `tests/test_batched_plan.py`

- [ ] **Step 1: Add failing repository policy tests**

Add tests that call `unignored_runtime_artifact_paths` before and after writing the narrow rule to `.git/info/exclude`:

```python
def test_runtime_artifact_path_must_be_git_ignored(self) -> None:
    from hooks.repository_snapshot import unignored_runtime_artifact_paths

    with tempfile.TemporaryDirectory() as tmp:
        repo = self._repo(Path(tmp))
        self.assertEqual(
            unignored_runtime_artifact_paths(repo),
            [".cmbdevclaw/large_tool_results/"],
        )
        (repo / ".git" / "info" / "exclude").write_text(
            ".cmbdevclaw/large_tool_results/\n",
            encoding="utf-8",
        )
        self.assertEqual(unignored_runtime_artifact_paths(repo), [])
```

- [ ] **Step 2: Run the unit test and observe the missing API**

Run:

```bash
python -m unittest tests.test_code_exploration.RepositorySnapshotTest.test_runtime_artifact_path_must_be_git_ignored
```

Expected: `ImportError` for `unignored_runtime_artifact_paths`.

- [ ] **Step 3: Implement the narrow ignore-policy helper**

Add to `hooks/repository_snapshot.py`:

```python
REQUIRED_IGNORED_RUNTIME_PATHS = (".cmbdevclaw/large_tool_results/",)


def unignored_runtime_artifact_paths(
    repo: Path,
    paths: tuple[str, ...] = REQUIRED_IGNORED_RUNTIME_PATHS,
) -> list[str]:
    unignored: list[str] = []
    for raw in paths:
        relative = raw.rstrip("/")
        probe = f"{relative}/.task-runner-ignore-probe"
        completed = _run_text(repo, "check-ignore", "--quiet", "--no-index", "--", probe)
        if completed.returncode == 1:
            unignored.append(raw)
        elif completed.returncode != 0:
            raise RepositorySnapshotError("git_ignore_check_failed")
    return unignored
```

- [ ] **Step 4: Give all existing task-runner fixtures the required local ignore rule**

Immediately after each temporary Git repository is initialized, write:

```python
(code / ".git" / "info" / "exclude").write_text(
    ".cmbdevclaw/large_tool_results/\n",
    encoding="utf-8",
)
```

Apply the equivalent statement for secondary repositories and both standalone repositories in `tests/test_batched_plan.py`.

- [ ] **Step 5: Verify repository snapshot tests**

Run:

```bash
python -m unittest tests.test_code_exploration.RepositorySnapshotTest
```

Expected: all `RepositorySnapshotTest` tests pass.

### Task 2: Add Structured Start Diagnostics And Preflight

**Files:**
- Modify: `hooks/task_runner.py`
- Modify: `tests/test_task_runner.py`

- [ ] **Step 1: Add failing start preflight and metadata tests**

Cover both an unignored repository and a requested module subdirectory:

```python
def test_start_rejects_unignored_runtime_artifact_path(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace, feature_dir, code = _workspace(Path(tmp))
        (code / ".git" / "info" / "exclude").write_text("", encoding="utf-8")

        started = _run(
            "start", "--workspace", str(workspace), "--feature", "alpha",
            "--task-id", "T001", "--code-workspace", str(code),
        )

        payload = json.loads(started.stdout)
        self.assertNotEqual(started.returncode, 0)
        self.assertEqual(payload["requiredAction"], "configure_git_ignore_and_retry")
        self.assertEqual(payload["resolvedGitRoots"], [str(code.resolve())])
        self.assertFalse((feature_dir / ".task-runs").exists())
        self.assertEqual(_read_batch(feature_dir)["tasks"][0]["status"], "todo")


def test_start_reports_requested_workspace_and_resolved_git_root(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace, _, code = _workspace(Path(tmp))
        module = code / "bccompliancemng"
        module.mkdir()

        started = _run(
            "start", "--workspace", str(workspace), "--feature", "alpha",
            "--task-id", "T001", "--code-workspace", str(module),
        )

        payload = json.loads(started.stdout)
        self.assertEqual(payload["requestedCodeWorkspaces"], [str(module.resolve())])
        self.assertEqual(payload["repositories"][0]["path"], str(code.resolve()))
        self.assertEqual(payload["snapshotMode"], "git_visible_file_content_sha256")
        self.assertFalse(payload["stagingAffectsSnapshot"])
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
python -m unittest \
  tests.test_task_runner.TaskRunnerTest.test_start_rejects_unignored_runtime_artifact_path \
  tests.test_task_runner.TaskRunnerTest.test_start_reports_requested_workspace_and_resolved_git_root
```

Expected: failures because start has no preflight or metadata fields.

- [ ] **Step 3: Add structured task-runner errors**

Replace the empty error type and add a shared CLI emitter:

```python
class TaskRunnerError(ValueError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


def _emit_error(exc: ValueError) -> int:
    details = exc.details if isinstance(exc, TaskRunnerError) else {}
    return _emit(False, error=str(exc), **details)
```

All command handlers must call `_emit_error(exc)` in their exception branches.

- [ ] **Step 4: Add runtime ignore preflight before run creation**

Import `unignored_runtime_artifact_paths` and add:

```python
def _assert_runtime_artifacts_ignored(repositories: RepositoryMap) -> None:
    for repository_id, repo in repositories.items():
        unignored = unignored_runtime_artifact_paths(repo)
        if unignored:
            path = unignored[0]
            raise TaskRunnerError(
                f"runtime_artifact_path_not_ignored:{repository_id}:{path}",
                requiredAction="configure_git_ignore_and_retry",
                resolvedGitRoots=[str(item) for item in repositories.values()],
                runtimeArtifactPaths=unignored,
            )
```

Call it immediately after `_resolve_repositories` in `_start_task_unlocked`, before `_repository_state` or any plan mutation.

- [ ] **Step 5: Persist explicit snapshot metadata**

Add these run-state fields without changing `repositories[].path`:

```python
"requestedCodeWorkspaces": [str(item) for item in code_workspaces],
"snapshotMode": "git_visible_file_content_sha256",
"stagingAffectsSnapshot": False,
```

- [ ] **Step 6: Verify focused and existing start tests**

Run:

```bash
python -m unittest \
  tests.test_task_runner.TaskRunnerTest.test_start_rejects_unignored_runtime_artifact_path \
  tests.test_task_runner.TaskRunnerTest.test_start_reports_requested_workspace_and_resolved_git_root \
  tests.test_task_runner.TaskRunnerTest.test_start_rejects_unfinished_dependency
```

Expected: all tests pass.

### Task 3: Protect Abort And Resume The Original Baseline

**Files:**
- Modify: `hooks/task_runner.py`
- Modify: `tests/test_task_runner.py`

- [ ] **Step 1: Add failing abort-protection tests**

Add tests that modify `implemented.txt` after start and assert normal abort fails without state mutation, while forced abort requires a reason and records audit data:

```python
def test_abort_rejects_unrecorded_changes_without_mutating_run(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace, feature_dir, code = _workspace(Path(tmp))
        started = _start(workspace, code)
        (code / "implemented.txt").write_text("implemented\n", encoding="utf-8")

        aborted = _run(
            "abort", "--workspace", str(workspace), "--feature", "alpha",
            "--task-id", "T001", "--code-workspace", str(code),
            "--run-id", started["runId"],
        )

        payload = json.loads(aborted.stdout)
        self.assertNotEqual(aborted.returncode, 0)
        self.assertEqual(payload["requiredAction"], "fix_workspace_and_retry_complete_or_force_abort")
        run = json.loads(
            (feature_dir / ".task-runs" / "T001" / f"{started['runId']}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run["status"], "started")
        self.assertEqual(_read_batch(feature_dir)["tasks"][0]["status"], "in_progress")
```

Add a forced-abort test using `--force-with-changes --abort-why "abandon implementation"` and assert `changedFilesAtAbort`, `fileChangesAtAbort`, `abortSnapshot`, and `abortWhy` are stored.

- [ ] **Step 2: Add a failing resume integration test**

Force-abort the original run, start and cleanly abort a replacement run whose baseline contains `implemented.txt`, then resume the original run and complete it. Assert completion evidence reports `implemented.txt` as created.

- [ ] **Step 3: Run abort/resume tests and verify failure**

Run:

```bash
python -m unittest \
  tests.test_task_runner.TaskRunnerTest.test_abort_rejects_unrecorded_changes_without_mutating_run \
  tests.test_task_runner.TaskRunnerTest.test_force_abort_records_changed_snapshot \
  tests.test_task_runner.TaskRunnerTest.test_resume_reuses_original_snapshot_and_completes_changes
```

Expected: failures because abort ignores repository state and `resume` is not a command.

- [ ] **Step 4: Extract reusable repository-diff collection**

Move the complete-time repository loop into:

```python
def _repository_changes(
    state: dict[str, Any],
    repositories: RepositoryMap,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    repository_states = _state_repositories(state)
    if not repository_states:
        raise TaskRunnerError("task_run_snapshot_missing")
    multiple = len(repository_states) > 1
    changes: list[dict[str, str]] = []
    final: list[dict[str, Any]] = []
    for repository_state in repository_states:
        repository_id = str(repository_state.get("id", ""))
        before = repository_state.get("snapshot")
        repo = repositories.get(repository_id)
        if not isinstance(before, dict) or repo is None:
            raise TaskRunnerError(f"task_run_repository_snapshot_missing:{repository_id}")
        after = _git_snapshot(repo)
        repo_changes = _snapshot_changes(before, after)
        if multiple:
            for change in repo_changes:
                change["path"] = f"{repository_id}:{change['path']}"
                if "fromPath" in change:
                    change["fromPath"] = f"{repository_id}:{change['fromPath']}"
                change["repository"] = repository_id
        changes.extend(repo_changes)
        final.append({"id": repository_id, "path": str(repo), "snapshot": after})
    return changes, final
```

Add `_changed_files(file_changes)` to normalize `path` and `fromPath`. Use both helpers in `complete` and `abort`.

- [ ] **Step 5: Implement guarded and forced abort**

Change `abort_task` and `_abort_task_unlocked` to accept repositories plus `force_with_changes` and `abort_why`. Reject changed snapshots unless forced; reject forced changed aborts without a reason. On forced abort, persist:

```python
state.update(
    {
        "abortSnapshot": final_repositories[0]["snapshot"],
        "abortRepositories": final_repositories,
        "fileChangesAtAbort": file_changes,
        "changedFilesAtAbort": _changed_files(file_changes),
        "abortWhy": abort_why,
    }
)
```

Add `--force-with-changes` and `--abort-why` to the abort parser.

- [ ] **Step 6: Implement resume without recapturing the baseline**

Add `_resume_task_unlocked`, `resume_task`, `_cmd_resume`, and a `resume` parser using the existing common run arguments. Validate status, evidence, task contract, repositories, runtime ignore policy, and competing active runs before plan mutation. After `set_task_execution_status(..., "in_progress")` succeeds, update only lifecycle fields:

```python
state.update(
    {
        "status": "started",
        "resumedAt": _utc_now(),
        "resumeCount": int(state.get("resumeCount", 0)) + 1,
    }
)
```

Do not replace `snapshot` or `repositories`.

- [ ] **Step 7: Make out-of-scope completion explicitly recoverable**

Raise the existing error with structured guidance:

```python
raise TaskRunnerError(
    "out_of_scope_changes_detected:" + ",".join(sorted(set(outside))),
    requiredAction="fix_workspace_and_retry_same_run",
    runId=run_id,
    changedFiles=_changed_files(file_changes),
    resolvedGitRoots=[str(item) for item in repositories.values()],
)
```

- [ ] **Step 8: Verify abort/resume and recovery regressions**

Run:

```bash
python -m unittest \
  tests.test_task_runner.TaskRunnerTest.test_abort_rejects_unrecorded_changes_without_mutating_run \
  tests.test_task_runner.TaskRunnerTest.test_force_abort_records_changed_snapshot \
  tests.test_task_runner.TaskRunnerTest.test_resume_reuses_original_snapshot_and_completes_changes \
  tests.test_task_runner.TaskRunnerTest.test_abort_can_clear_run_after_plan_contract_changes \
  tests.test_task_runner.TaskRunnerTest.test_recover_binds_evidence_written_run_without_rerunning_commands
```

Expected: all tests pass.

### Task 4: Reject False Verified-Existing Claims After A Lost Baseline

**Files:**
- Modify: `hooks/task_runner.py`
- Modify: `tests/test_task_runner.py`

- [ ] **Step 1: Add a failing historical-baseline test**

Start a run, create an in-scope file, force-abort it, start a replacement run with that file already present, then attempt verified-existing completion. Assert:

```python
self.assertIn(
    f"verified_existing_conflicts_with_prior_run_changes:{original['runId']}:implemented.txt",
    completed.stdout,
)
```

Also keep the existing genuine verified-existing test as the non-conflicting control.

- [ ] **Step 2: Run the focused test and verify the false claim currently passes**

Run:

```bash
python -m unittest \
  tests.test_task_runner.TaskRunnerTest.test_verified_existing_rejects_changes_from_prior_aborted_run \
  tests.test_task_runner.TaskRunnerTest.test_complete_supports_verified_existing_with_no_file_changes
```

Expected: the new rejection test fails.

- [ ] **Step 3: Implement prior aborted-run conflict detection**

Add a helper that reads older aborted run JSON files, uses `changedFilesAtAbort` when present, otherwise compares the prior original snapshot with current repository snapshots, and filters changes through current `scope.paths`:

```python
def _prior_aborted_run_conflict(
    feature_dir: Path,
    task: dict[str, Any],
    current_run_id: str,
    repositories: RepositoryMap,
) -> tuple[str, list[str]] | None:
    scope = task.get("scope")
    scope_paths = scope.get("paths") if isinstance(scope, dict) else []
    for path in sorted(_runs_dir(feature_dir, str(task.get("id"))).glob("*.json")):
        prior = json.loads(path.read_text(encoding="utf-8"))
        if prior.get("runId") == current_run_id or prior.get("status") != "aborted":
            continue
        changed = prior.get("changedFilesAtAbort")
        if not isinstance(changed, list):
            try:
                changed = _changed_files(_repository_changes(prior, repositories)[0])
            except TaskRunnerError:
                continue
        relevant = [
            item for item in changed
            if isinstance(item, str) and (not scope_paths or _path_in_scope(item, scope_paths))
        ]
        if relevant:
            return str(prior.get("runId")), sorted(set(relevant))
    return None
```

Before selecting `verified_existing`, reject a conflict with the specified error and structured `requiredAction="resume_original_run_or_rebuild_baseline"`.

- [ ] **Step 4: Verify both guarded and genuine no-change flows**

Run:

```bash
python -m unittest \
  tests.test_task_runner.TaskRunnerTest.test_verified_existing_rejects_changes_from_prior_aborted_run \
  tests.test_task_runner.TaskRunnerTest.test_complete_supports_verified_existing_with_no_file_changes \
  tests.test_task_runner.TaskRunnerTest.test_no_change_completion_requires_reason_and_supporting_file
```

Expected: all tests pass.

### Task 5: Document The Recovery Workflow And Run Regression Tests

**Files:**
- Modify: `docs/evidence-task-runner.md`
- Modify: `skills/autodev/autodev-code/SKILL.md`
- Modify: `tests/test_board_config_invariants.py`

- [ ] **Step 1: Update operator documentation**

Document the required ignore rule and the exact behavior:

```text
.cmbdevclaw/large_tool_results/
```

State that `--code-workspace` resolves to the Git root, snapshots compare file content rather than staging state, out-of-scope failures must retry the same run, `resume` restores an accidentally aborted original baseline, and `--no-code-change-why` is not a baseline-recovery mechanism.

- [ ] **Step 2: Update the Code skill contract**

Add the pre-start check, same-run retry rule, protected abort command, and resume command to `skills/autodev/autodev-code/SKILL.md`. Require repository-root-relative supporting-file paths.

- [ ] **Step 3: Add invariant assertions for the workflow text**

Assert the skill contains:

```python
self.assertIn(".cmbdevclaw/large_tool_results/", content)
self.assertIn("task_runner.py resume", content)
self.assertIn("staging", content)
self.assertIn("同一个 run", content)
self.assertIn("--no-code-change-why", content)
```

- [ ] **Step 4: Run focused suites**

Run:

```bash
python -m unittest \
  tests.test_code_exploration.RepositorySnapshotTest \
  tests.test_task_runner.TaskRunnerTest \
  tests.test_batched_plan \
  tests.test_board_config_invariants
```

Expected: all tests pass.

- [ ] **Step 5: Run the full Python suite**

Run:

```bash
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 6: Inspect final scope and whitespace**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; status contains the pre-existing exploration changes plus the files intentionally modified by this plan.
