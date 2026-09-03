from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from board_core.artifact_paths import artifact_exists_exact, resolve_artifact_files_exact


class ResolveArtifactFilesExactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, relative: str, body: str = "content") -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def test_glob_returns_sorted_absolute_nonempty_files(self) -> None:
        zeta = self.write("specs/zeta/z.md")
        alpha = self.write("specs/alpha/a.md")
        self.write("specs/alpha/empty.md", body="")

        resolved = resolve_artifact_files_exact(self.root, "specs/**/*.md")

        self.assertEqual(resolved, (alpha.resolve(), zeta.resolve()))
        self.assertTrue(artifact_exists_exact(self.root, "specs/**/*.md"))

    def test_exact_file_and_directory_share_resolution_rules(self) -> None:
        first = self.write("evidence/a.jsonl")
        second = self.write("evidence/nested/b.jsonl")

        self.assertEqual(
            resolve_artifact_files_exact(self.root, "evidence"),
            (first.resolve(), second.resolve()),
        )
        self.assertEqual(
            resolve_artifact_files_exact(self.root, "evidence/a.jsonl"),
            (first.resolve(),),
        )

    def test_case_mismatch_empty_file_and_parent_escape_do_not_resolve(self) -> None:
        self.write("Proposal.md")
        self.write("empty.md", body="")

        self.assertEqual(resolve_artifact_files_exact(self.root, "proposal.md"), ())
        self.assertEqual(resolve_artifact_files_exact(self.root, "empty.md"), ())
        self.assertEqual(resolve_artifact_files_exact(self.root, "../outside.md"), ())

    def test_symlink_outside_feature_dir_does_not_resolve(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.md"
        outside.write_text("outside", encoding="utf-8")
        self.addCleanup(outside.unlink)
        (self.root / "linked.md").symlink_to(outside)

        self.assertEqual(resolve_artifact_files_exact(self.root, "linked.md"), ())
        self.assertFalse(artifact_exists_exact(self.root, "linked.md"))


if __name__ == "__main__":
    unittest.main()
