from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-reviewer"
    / "scripts"
    / "capture_review_baseline.py"
)


def run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


class CaptureReviewBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run(["git", "init", "-q"], cwd=self.repo)
        run(["git", "config", "user.email", "review@example.test"], cwd=self.repo)
        run(["git", "config", "user.name", "Review Test"], cwd=self.repo)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
        run(["git", "add", "src/app.py"], cwd=self.repo)
        run(["git", "commit", "-qm", "initial"], cwd=self.repo)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_captures_and_deduplicates_git_roots_from_module_manifest(self) -> None:
        manifest = self.root / "modules_compile.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "modules": [
                        {"module": "root", "path": str(self.repo), "compile_command": "true"},
                        {
                            "module": "nested",
                            "path": str(self.repo / "src"),
                            "compile_command": "true",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "feature" / "review-baseline.json"

        result = run(
            [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(output),
                "--module-manifest",
                str(manifest),
            ],
            cwd=self.repo,
        )

        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn("REVIEW_BASELINE_CAPTURED", result.stdout)
        self.assertEqual(payload["schema_version"], "autobizdevops.review-baseline.v1")
        self.assertEqual(len(payload["repositories"]), 1)
        repository = payload["repositories"][0]
        self.assertEqual(repository["path"], str(self.repo.resolve()))
        self.assertEqual(repository["base_sha"], run(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip())
        self.assertEqual(repository["scope_confidence"], "full")
        self.assertEqual(repository["initial_dirty_paths"], [])

    def test_records_preexisting_dirty_and_untracked_paths_as_partial_scope(self) -> None:
        (self.repo / "src" / "app.py").write_text("print('dirty')\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("preexisting\n", encoding="utf-8")
        output = self.root / "review-baseline.json"

        run(
            [sys.executable, str(SCRIPT), "--output", str(output), "--repo", f"backend={self.repo}"],
            cwd=self.root,
        )

        repository = json.loads(output.read_text(encoding="utf-8"))["repositories"][0]
        self.assertEqual(repository["id"], "backend")
        self.assertEqual(repository["scope_confidence"], "partial")
        self.assertEqual(repository["initial_dirty_paths"], ["notes.txt", "src/app.py"])
        self.assertEqual(repository["initial_untracked_paths"], ["notes.txt"])

    def test_rejects_non_git_repository_without_writing_output(self) -> None:
        not_repo = self.root / "not-repo"
        not_repo.mkdir()
        output = self.root / "review-baseline.json"

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(output), "--repo", f"bad={not_repo}"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("REVIEW_BASELINE_ERROR", result.stderr)
        self.assertFalse(output.exists())

    def test_refuses_to_overwrite_existing_baseline(self) -> None:
        output = self.root / "review-baseline.json"
        output.write_text('{"existing": true}\n', encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(output), "--repo", f"backend={self.repo}"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), '{"existing": true}\n')


if __name__ == "__main__":
    unittest.main()
