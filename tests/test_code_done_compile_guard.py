from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "hooks" / "code_done_compile_guard.py"
HOOKS_DIR = ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from code_done_compile_guard import validate_modules_compile  # noqa: E402


def plugin_env(workspace: Path, *, feature: str = "alpha") -> dict[str, str]:
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(ROOT)
    env["PLUGIN_WORKSPACE"] = str(workspace.parent)
    env["PROJECT_CODE"] = workspace.name
    env["FEATURE_ID"] = feature
    env.pop("PLUGIN_OUTPUT_DIR", None)
    return env


def env_without(workspace: Path, *keys: str) -> dict[str, str]:
    env = plugin_env(workspace)
    for key in keys:
        env.pop(key, None)
    return env


def run_guard(payload: dict | str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    raw_input = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=raw_input,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def execute_payload(command: str, cwd: Path) -> dict:
    return {
        "tool_name": "execute",
        "cwd": str(cwd),
        "tool_input": {"command": command},
    }


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    autobiz_dir = workspace / ".autobizdevops"
    autobiz_dir.mkdir(parents=True)
    (autobiz_dir / "state.json").write_text(json.dumps({"features": {}}, ensure_ascii=False), encoding="utf-8")
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

    def test_update_checkpoint_non_code_done_passes_without_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            command = "python hooks/update_checkpoint.py --checkpoint code_in_progress"

            result = run_guard(execute_payload(command, Path(tmp)))

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_state_script_workspace_argument_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            commands = [
                f"python hooks/update_checkpoint.py --workspace {workspace} --checkpoint code_in_progress",
                f"python read_state_json.py --workspace {workspace} --feature alpha",
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = run_guard(execute_payload(command, Path(tmp)), env=plugin_env(workspace))

                    self.assertEqual(result.returncode, 2)
                    self.assertIn("不接受 --workspace/-w", result.stderr)
                    self.assertEqual(json.loads(result.stdout)["decision"], "block")

    def test_modules_compile_missing_manifest_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))

            _, errors = validate_modules_compile(workspace, emit_success=False)

            self.assertTrue(errors)
            self.assertIn("缺少模块编译清单", "\n".join(errors))

    def test_code_done_hook_missing_plugin_workspace_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            command = "python hooks/update_checkpoint.py --checkpoint code_done"

            result = run_guard(execute_payload(command, Path(tmp)), env=env_without(workspace, "PLUGIN_WORKSPACE"))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout, "")

    def test_code_done_hook_missing_project_code_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            command = "python hooks/update_checkpoint.py --checkpoint code_done"

            result = run_guard(execute_payload(command, Path(tmp)), env=env_without(workspace, "PROJECT_CODE"))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout, "")

    def test_invalid_modules_compile_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            (workspace / ".autobizdevops" / "modules_compile.json").write_text(
                json.dumps({"version": 1, "modules": [{"module": "root", "path": str(workspace)}]}),
                encoding="utf-8",
            )
            _, errors = validate_modules_compile(workspace, emit_success=False)

            self.assertTrue(errors)
            self.assertIn("compile_command 缺失", "\n".join(errors))

    def test_missing_module_path_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_workspace(Path(tmp))
            missing = Path(tmp) / "missing"
            write_modules(
                workspace,
                [{"module": "missing", "path": str(missing), "compile_command": "echo ok"}],
            )
            _, errors = validate_modules_compile(workspace, emit_success=False)

            self.assertTrue(errors)
            self.assertIn("不存在或不是目录", "\n".join(errors))

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
            module_count, errors = validate_modules_compile(workspace, emit_success=False)

            self.assertEqual(errors, [])
            self.assertEqual(module_count, 2)

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
            _, errors = validate_modules_compile(workspace, emit_success=False)

            joined_errors = "\n".join(errors)
            self.assertIn("service", joined_errors)
            self.assertIn(command_text, joined_errors)
            self.assertIn("boom output", joined_errors)


if __name__ == "__main__":
    unittest.main()
