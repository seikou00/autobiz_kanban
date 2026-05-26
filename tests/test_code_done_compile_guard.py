from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "hooks" / "code_done_compile_guard.py"


def run_guard(payload: dict | str) -> subprocess.CompletedProcess:
    raw_input = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=raw_input,
        text=True,
        capture_output=True,
        check=False,
    )


def execute_payload(command: str, cwd: Path) -> dict:
    return {
        "tool_name": "execute",
        "cwd": str(cwd),
        "tool_input": {"command": command},
    }


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / ".autobizdevops").mkdir(parents=True)
    return workspace


def write_modules(workspace: Path, modules: list[dict]) -> None:
    (workspace / ".autobizdevops" / "modules_compile.json").write_text(
        json.dumps({"version": 1, "modules": modules}, ensure_ascii=False),
        encoding="utf-8",
    )


def py_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


class CodeDoneCompileGuardTest(unittest.TestCase):
    def test_non_update_checkpoint_command_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_guard(execute_payload("echo hello", Path(tmp)))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_update_checkpoint_non_code_done_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            command = (
                f"python hooks/update_checkpoint.py --workspace {workspace} "
                "--feature alpha --checkpoint code_in_progress"
            )

            result = run_guard(execute_payload(command, Path(tmp)))

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_code_done_missing_modules_compile_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            command = (
                f"python hooks/update_checkpoint.py --workspace {workspace} "
                "--feature alpha --checkpoint code_done"
            )

            result = run_guard(execute_payload(command, Path(tmp)))

            self.assertEqual(result.returncode, 2)
            self.assertIn("缺少模块编译清单", result.stderr)
            self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_invalid_modules_compile_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "modules_compile.json").write_text(
                json.dumps({"version": 1, "modules": [{"module": "root", "path": str(workspace)}]}),
                encoding="utf-8",
            )
            command = (
                f"python hooks/update_checkpoint.py --workspace={workspace} "
                "--feature=alpha --checkpoint=code_done"
            )

            result = run_guard(execute_payload(command, Path(tmp)))

            self.assertEqual(result.returncode, 2)
            self.assertIn("compile_command 缺失", result.stderr)

    def test_missing_module_path_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            missing = Path(tmp) / "missing"
            write_modules(
                workspace,
                [{"module": "missing", "path": str(missing), "compile_command": "echo ok"}],
            )
            command = (
                f"python hooks/update_checkpoint.py --workspace {workspace} "
                "--feature alpha --checkpoint code_done"
            )

            result = run_guard(execute_payload(command, Path(tmp)))

            self.assertEqual(result.returncode, 2)
            self.assertIn("不存在或不是目录", result.stderr)

    def test_multiple_modules_compile_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = make_workspace(root)
            service = root / "service"
            web = root / "web"
            service.mkdir()
            web.mkdir()
            write_modules(
                workspace,
                [
                    {
                        "module": "service",
                        "path": str(service),
                        "compile_command": py_command("print('service ok')"),
                    },
                    {
                        "module": "web",
                        "path": str(web),
                        "compile_command": py_command("import sys; print('web warning', file=sys.stderr)"),
                    },
                ],
            )
            command = (
                f"/bin/zsh -lc 'python hooks/update_checkpoint.py --workspace {workspace} "
                "--feature alpha --checkpoint code_done'"
            )

            result = run_guard(execute_payload(command, root))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("service", result.stdout)
            self.assertIn("web warning", result.stdout)
            self.assertIn("code_done 模块编译校验通过: 2 个模块", result.stdout)

    def test_compile_failure_blocks_with_module_command_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = make_workspace(root)
            service = root / "service"
            service.mkdir()
            command_text = py_command("import sys; print('boom output'); sys.exit(7)")
            write_modules(
                workspace,
                [{"module": "service", "path": str(service), "compile_command": command_text}],
            )
            command = (
                f"python hooks/update_checkpoint.py --workspace {workspace} "
                "--feature alpha --checkpoint code_done"
            )

            result = run_guard(execute_payload(command, root))

            self.assertEqual(result.returncode, 2)
            self.assertIn("service", result.stderr)
            self.assertIn(command_text, result.stderr)
            self.assertIn("boom output", result.stderr)
            self.assertEqual(json.loads(result.stdout)["decision"], "block")


if __name__ == "__main__":
    unittest.main()
