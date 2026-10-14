from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.evidence_store import read_records, stream_path
from tests.test_task_runner import (
    _configure_frontend_without_batch_compile,
    _git,
    _read_batch,
    _run,
    _start,
    _workspace,
)

PREPARE_HTML = (
    ROOT / "skills/autodev/autodev-code/references/frontend-html/with-absolute-html"
    / "scripts/prepare_html_analysis.py"
)
ASSET_PATH = "src/assets/images/位图.png"
ORIGINAL_ASSET = b"existing-project-image"
HTML_ASSET = b"exported-html-image"


class FrontendTaskStartOrderTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, list[Path]]:
        workspace, feature_dir, code = _workspace(root)
        _configure_frontend_without_batch_compile(feature_dir)
        target = code / ASSET_PATH
        target.parent.mkdir(parents=True)
        target.write_bytes(ORIGINAL_ASSET)
        _git(code, "add", ASSET_PATH)
        _git(code, "commit", "-m", "existing shared asset")

        html_dir = root / "html-source"
        (html_dir / "assets").mkdir(parents=True)
        (html_dir / "assets/位图.png").write_bytes(HTML_ASSET)
        sources = [html_dir / "main.html", html_dir / "details.html"]
        sources[0].write_text(
            '<html><body><div style="position:absolute;left:0;top:0;width:300px;height:200px">'
            '<h1>页面详情</h1><img src="./assets/位图.png" alt="位图" /></div></body></html>',
            encoding="utf-8",
        )
        sources[1].write_text('<html><body><div>附加信息</div></body></html>', encoding="utf-8")
        return workspace, feature_dir, code, sources

    def _analyze(self, code: Path, sources: list[Path]) -> None:
        argv = [
            sys.executable, "-B", "-X", "utf8", str(PREPARE_HTML),
            "--project-root", str(code), "--task-stem", "task-t001",
        ]
        for source in sources:
            argv.extend(["--html-file", str(source)])
        result = subprocess.run(argv, cwd=code, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((code / ASSET_PATH).read_bytes(), HTML_ASSET)

    def test_analysis_before_start_is_still_rejected_without_mutating_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code, sources = self._fixture(Path(tmp))
            self._analyze(code, sources)

            result = _run(
                "start", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["error"].startswith("prestart_unattributed_changes_detected:"))
            self.assertIn(ASSET_PATH, payload["unattributedFiles"])
            self.assertIn(".frontend/html-input/task-t001-merged.html", payload["unattributedFiles"])
            self.assertFalse((feature_dir / ".task-runs/T001").exists())
            self.assertEqual(_read_batch(feature_dir)["tasks"][0]["status"], "todo")

    def test_analysis_after_start_records_reports_and_asset_changes_in_task_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir, code, sources = self._fixture(Path(tmp))
            started = _start(workspace, code)
            self.assertEqual(started["snapshot"][ASSET_PATH], hashlib.sha256(ORIGINAL_ASSET).hexdigest())
            self.assertFalse((code / ".frontend").exists())

            self._analyze(code, sources)
            (code / "src/page.tsx").write_text("export const Page = () => null;\n", encoding="utf-8")
            result = _run(
                "finish-implementation", "--workspace", str(workspace), "--feature", "alpha",
                "--task-id", "T001", "--code-workspace", str(code),
                "--run-id", started["runId"],
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finished = json.loads(result.stdout)
            evidence = next(
                record for record in read_records(stream_path(feature_dir))
                if record.get("evidenceId") == finished["implementationEvidenceId"]
            )
            self.assertEqual(evidence["taskId"], "T001")
            self.assertEqual(evidence["runId"], started["runId"])
            operations = {item["path"]: item["operation"] for item in evidence["fileChanges"]}
            self.assertEqual(operations[ASSET_PATH], "modified")
            for relative in [
                ".frontend/html-analysis/task-t001.json",
                ".frontend/html-analysis/task-t001.md",
                ".frontend/html-analysis/task-t001-checklist.md",
                ".frontend/html-analysis/task-t001-assets-mapping.json",
                ".frontend/html-input/task-t001-merged.html",
                "src/page.tsx",
            ]:
                self.assertEqual(operations[relative], "created")


if __name__ == "__main__":
    unittest.main()
