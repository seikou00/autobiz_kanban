# Artifact JSON Glob Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `inspect_state` to scan the declared code-exploration JSON cache without weakening recursive artifact path validation.

**Architecture:** Replace the specs-only glob validator with an exact artifact contract table keyed by artifact ID. Each contract fixes the allowed path and suffix; the existing glob response shape and missing fallback remain unchanged.

**Tech Stack:** Python 3 standard library, `pathlib`, `unittest`, shell ZIP tooling.

---

### Task 1: Reproduce JSON artifact scanning at the scanner boundary

**Files:**
- Modify: `tests/test_state_json_source.py`

- [x] **Step 1: Add a focused failing test**

Add a test to `ArtifactScanTests` that creates nested cache JSON files and a non-JSON neighbor, invokes the real scanner, and expects only JSON paths:

```python
def test_scan_code_exploration_cache_glob_returns_json_paths(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = make_workspace(Path(tmp))
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        cache_dir = feature_dir / "cache" / "code-exploration" / "repo"
        cache_dir.mkdir(parents=True)
        (cache_dir / "backend.json").write_text("{}", encoding="utf-8")
        (cache_dir / "notes.md").write_text("skip", encoding="utf-8")

        artifacts = scan_artifacts(
            feature_dir,
            workspace,
            [{
                "id": "code_exploration_cache",
                "label": "代码探索缓存",
                "path": "cache/code-exploration/**/*.json",
            }],
        )

        self.assertEqual(
            artifacts[0]["paths"],
            [".autobizdevops/features/alpha/cache/code-exploration/repo/backend.json"],
        )
        self.assertEqual(artifacts[0]["artifactStatus"], "generated")
```

- [x] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_state_json_source.ArtifactScanTests.test_scan_code_exploration_cache_glob_returns_json_paths
```

Expected: error containing `only specs artifacts may use glob paths`.

### Task 2: Implement the explicit glob contract

**Files:**
- Modify: `board_core/artifacts.py`

- [x] **Step 1: Replace the specs-only constant with exact contracts**

Define:

```python
GLOB_ARTIFACT_CONTRACTS = {
    "specs": {"path": "specs/**/*.md", "suffix": ".md"},
    "code_exploration_cache": {
        "path": "cache/code-exploration/**/*.json",
        "suffix": ".json",
    },
}
```

- [x] **Step 2: Return the required suffix from validation**

Validate the artifact ID and exact path against the table. Raise `ValueError` for unknown IDs or mismatched paths; otherwise return the configured suffix.

- [x] **Step 3: Scan using the contract suffix**

Use the returned suffix instead of the hard-coded `.md` comparison while preserving sorted workspace-relative paths and the existing missing fallback.

- [x] **Step 4: Verify GREEN and existing rejection behavior**

Run:

```bash
python3 -m unittest tests.test_state_json_source.ArtifactScanTests
```

Expected: all artifact scanner tests pass, including rejection of `logs/**/*.md` and malformed specs paths.

### Task 3: Verify the installed workflow path and rebuild the archive

**Files:**
- Verify: `tests/test_dynamic_workflow.py`
- Package: `dist/autobiz_kanban_workspace.zip`

- [x] **Step 1: Run the original failing integration test**

Run:

```bash
python3 -m unittest tests.test_dynamic_workflow.DynamicWorkflowRuntimeTests.test_inspect_hides_frontend_node_for_standard_profile
```

Expected: `OK` with no artifact glob traceback.

- [x] **Step 2: Run related regressions and syntax checks**

Run:

```bash
python3 -m unittest tests.test_state_json_source tests.test_dynamic_workflow tests.test_board_config_invariants
python3 -m py_compile board_core/artifacts.py inspect_state.py
git diff --check
```

Expected: all selected tests pass; syntax and whitespace checks return zero.

- [x] **Step 3: Rebuild and clean the plugin archive**

Run:

```bash
./package_workspace.sh dist/autobiz_kanban_workspace.zip
zip -d dist/autobiz_kanban_workspace.zip '*/__pycache__/*' '*.pyc' '*/.DS_Store'
unzip -t dist/autobiz_kanban_workspace.zip
```

Expected: ZIP integrity succeeds and its file list contains no runtime caches.

- [x] **Step 4: Commit the bug fix independently**

Stage only:

```bash
git add board_core/artifacts.py tests/test_state_json_source.py docs/superpowers/plans/2026-07-14-artifact-json-glob-scanning.md
git commit -m "fix: scan code exploration cache artifacts"
```

Expected: the commit contains no Task Runner baseline or active-batch continuation changes.
