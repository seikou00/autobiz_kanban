#!/usr/bin/env python3
"""Regression tests for the autodev-code frontend review runner."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SCRIPTS = ROOT / "skills" / "autodev" / "autodev-code" / "references" / "frontend-html" / "scripts"
RUNNER = REVIEW_SCRIPTS / "review_runner.py"
HTML_CHECKER = REVIEW_SCRIPTS / "html_static_checker.py"


class FrontendReviewRunnerTests(unittest.TestCase):
    def run_runner(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(RUNNER), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_html_checker(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(HTML_CHECKER), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def write(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def json_result(self, proc: subprocess.CompletedProcess[str]) -> dict:
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - clearer failure output
            self.fail(f"stdout is not JSON: {proc.stdout!r}\nstderr={proc.stderr!r}\nerror={exc}")

    def test_missing_target_returns_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "missing")
            proc = self.run_runner("--target", target, "--format", "json", "--antd-audit", "off")
            result = self.json_result(proc)

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(result["summary"]["executionErrors"], 1)

    def test_empty_directory_and_unsupported_file_return_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsupported = self.write(root, "README.md", "# not source\n")
            empty = root / "empty"
            empty.mkdir()

            for target in [empty, unsupported]:
                with self.subTest(target=target):
                    proc = self.run_runner("--target", str(target), "--format", "json", "--antd-audit", "off")
                    result = self.json_result(proc)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(result["summary"]["executionErrors"], 1)

    def test_standalone_html_checker_rejects_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "missing")
            proc = self.run_html_checker(target, "--format", "json")
            result = self.json_result(proc)

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(result["summary"]["executionErrors"], 1)

    def test_clean_tsx_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self.write(
                Path(tmp),
                "Clean.tsx",
                'export default function Clean() { return <div className="ok">Ready</div>; }\n',
            )
            proc = self.run_runner("--target", str(target), "--format", "json", "--antd-audit", "off")
            result = self.json_result(proc)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result["summary"]["mustFix"], 0)
        self.assertEqual(result["summary"]["suggestion"], 0)

    def test_static_findings_return_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self.write(
                Path(tmp),
                "Broken.tsx",
                "import Missing from './missing';\n"
                'export default function Broken() { return <div class="bad">TODO</div>; }\n',
            )
            proc = self.run_runner("--target", str(target), "--format", "json", "--antd-audit", "off")
            result = self.json_result(proc)

        self.assertEqual(proc.returncode, 1)
        self.assertGreaterEqual(result["summary"]["mustFix"], 1)
        self.assertGreaterEqual(result["summary"]["suggestion"], 1)

    def test_missing_optional_inputs_return_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self.write(root, "Clean.tsx", "export default function Clean() { return <div />; }\n")
            cases = [
                ("--source-html", root / "missing.html"),
                ("--analysis", root / "missing.json"),
                ("--plan", root / "missing.md"),
            ]
            for flag, missing in cases:
                with self.subTest(flag=flag):
                    proc = self.run_runner(
                        "--target",
                        str(target),
                        flag,
                        str(missing),
                        "--format",
                        "json",
                        "--antd-audit",
                        "off",
                    )
                    result = self.json_result(proc)
                    self.assertEqual(proc.returncode, 2)
                    self.assertEqual(result["summary"]["executionErrors"], 1)

    def test_invalid_analysis_json_returns_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self.write(root, "Clean.tsx", "export default function Clean() { return <div />; }\n")
            analysis = self.write(root, "analysis.json", "{not-json")
            proc = self.run_runner(
                "--target",
                str(target),
                "--analysis",
                str(analysis),
                "--format",
                "json",
                "--antd-audit",
                "off",
            )
            result = self.json_result(proc)

        self.assertEqual(proc.returncode, 2)
        self.assertEqual(result["summary"]["executionErrors"], 1)

    def test_antd_audit_modes_and_auto_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = self.write(root, "Clean.tsx", "export default function Clean() { return <div />; }\n")
            text_only = self.write(
                root,
                "TextOnly.tsx",
                "export default function TextOnly() { const text = \"from 'antd'\"; return <div>{text}</div>; }\n",
            )
            imported = self.write(
                root,
                "Imported.tsx",
                "import { Button } from 'antd';\n"
                "export default function Imported() { return <Button>OK</Button>; }\n",
            )
            state = self.write(root, "handoff.json", '{"auditRequired": true}')

            off = self.run_runner("--target", str(imported), "--format", "json", "--antd-audit", "off")
            off_result = self.json_result(off)
            self.assertNotIn("antd-audit", off_result["checks"])

            on = self.run_runner("--target", str(clean), "--format", "json", "--antd-audit", "on")
            on_result = self.json_result(on)
            self.assertIn("antd-audit", on_result["checks"])

            auto_text = self.run_runner("--target", str(text_only), "--format", "json")
            auto_text_result = self.json_result(auto_text)
            self.assertNotIn("antd-audit", auto_text_result["checks"])

            auto_import = self.run_runner("--target", str(imported), "--format", "json")
            auto_import_result = self.json_result(auto_import)
            self.assertIn("antd-audit", auto_import_result["checks"])

            auto_state = self.run_runner("--target", str(clean), "--analysis", str(state), "--format", "json")
            auto_state_result = self.json_result(auto_state)
            self.assertIn("antd-audit", auto_state_result["checks"])

    def test_output_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self.write(root, "Clean.tsx", "export default function Clean() { return <div />; }\n")
            markdown_report = root / "report.md"
            json_report = root / "report.json"

            markdown_proc = self.run_runner(
                "--target",
                str(target),
                "--format",
                "markdown",
                "--antd-audit",
                "off",
                "--output",
                str(markdown_report),
            )
            json_proc = self.run_runner(
                "--target",
                str(target),
                "--format",
                "json",
                "--antd-audit",
                "off",
                "--output",
                str(json_report),
            )

            self.assertEqual(markdown_proc.returncode, 0)
            self.assertEqual(json_proc.returncode, 0)
            self.assertTrue(markdown_report.read_text(encoding="utf-8").strip())
            self.assertEqual(json.loads(json_report.read_text(encoding="utf-8"))["summary"]["executionErrors"], 0)


if __name__ == "__main__":
    unittest.main()
