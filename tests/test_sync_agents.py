"""Tests for hooks/sync_agents.py: repo resolution, failure path, end-to-end git sync."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks import sync_agents  # noqa: E402
from hooks.agents_repo import build_sync_payload  # noqa: E402

GIT = shutil.which("git")


@contextlib.contextmanager
def _temp_board_config(text: str):
    """临时把 sync_agents.BOARD_CONFIG_PATH 指到内容受控的配置——测回退逻辑本身，
    不依赖随包 board_config.json 当前的 agentsRepo.url（避免被真实值改动影响）。"""
    orig = sync_agents.BOARD_CONFIG_PATH
    cfg = Path(tempfile.mkdtemp()) / "board_config.json"
    cfg.write_text(text, encoding="utf-8")
    sync_agents.BOARD_CONFIG_PATH = cfg
    try:
        yield cfg
    finally:
        sync_agents.BOARD_CONFIG_PATH = orig


class ResolveRepoTest(unittest.TestCase):
    def test_cli_override_wins_without_reading_config(self):
        url, ref = sync_agents._resolve_repo("https://example.com/a.git", "dev")
        self.assertEqual((url, ref), ("https://example.com/a.git", "dev"))

    def test_falls_back_to_empty_board_config(self):
        with _temp_board_config('{"agentsRepo": {"url": "", "ref": "main"}}'):
            url, ref = sync_agents._resolve_repo(None, None)
        self.assertEqual(url, "")
        self.assertEqual(ref, "main")

    def test_falls_back_to_configured_url(self):
        with _temp_board_config('{"agentsRepo": {"url": "https://git/x.git", "ref": "dev"}}'):
            url, ref = sync_agents._resolve_repo(None, None)
        self.assertEqual((url, ref), ("https://git/x.git", "dev"))


class RunFailurePathTest(unittest.TestCase):
    def test_missing_url_returns_ok_false_without_touching_git(self):
        with _temp_board_config('{"agentsRepo": {"url": "", "ref": "main"}}'):
            result = sync_agents.run(None, None)
        self.assertFalse(result["ok"])
        self.assertIn("未配置 agents 仓库地址", result["message"])


def _git(args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _make_source_repo() -> Path:
    src = Path(tempfile.mkdtemp()) / "agents-src"
    src.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(src)], capture_output=True, text=True, check=True)
    manifest = {
        "schemaVersion": "autobizdevops.agents.manifest.v1",
        "systems": [
            {"systemId": "LF39", "systemName": "外联", "serviceUnits": [
                {"serviceUnitId": "LF39.18_Outservice", "name": "出站"}]},
        ],
    }
    (src / "agents.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (src / "LF39").mkdir()
    (src / "LF39" / "AGENTS.md").write_text("# LF39\n- 走统一网关\n", encoding="utf-8")
    _git(["add", "-A"], src)
    _git(["commit", "-m", "init"], src)
    return src


@unittest.skipUnless(GIT, "git not available")
class SyncRepoEndToEndTest(unittest.TestCase):
    def test_clone_then_update(self):
        src = _make_source_repo()
        plugin_root = Path(tempfile.mkdtemp())
        dest = plugin_root / "sys"

        # 首次：克隆
        info = sync_agents.sync_repo(str(src), "main", dest)
        self.assertTrue(info["commit"])
        self.assertTrue((dest / "agents.manifest.json").is_file())
        self.assertTrue((dest / "LF39" / "AGENTS.md").is_file())

        payload = build_sync_payload(plugin_root=plugin_root, repo_info={"url": str(src), "ref": "main", **info})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["supported_service_units"], ["LF39.18_Outservice"])
        self.assertTrue(payload["systems"][0]["agentsReady"])
        # 下载落盘路径与 repo 同级，指向克隆缓存根 <pluginPath>/sys。
        self.assertEqual(payload["downloadPath"], str(dest))

        # 远端新增系统并提交
        manifest = json.loads((src / "agents.manifest.json").read_text(encoding="utf-8"))
        manifest["systems"].append({"systemId": "LA64", "serviceUnits": [{"serviceUnitId": "LA64.05_UEXgateway"}]})
        (src / "agents.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (src / "LA64").mkdir()
        (src / "LA64" / "AGENTS.md").write_text("# LA64\n", encoding="utf-8")
        _git(["add", "-A"], src)
        _git(["commit", "-m", "add LA64"], src)

        # 第二次：删旧目录后重新克隆，拿到最新提交
        info2 = sync_agents.sync_repo(str(src), "main", dest)
        self.assertNotEqual(info2["commit"], info["commit"])
        self.assertTrue((dest / "LA64" / "AGENTS.md").is_file())
        payload2 = build_sync_payload(plugin_root=plugin_root)
        self.assertEqual(
            payload2["supported_service_units"], ["LF39.18_Outservice", "LA64.05_UEXgateway"]
        )

    def test_nonempty_nongit_dir_is_wiped_and_recloned(self):
        # 旧的非 git 残留目录不再报错：整目录删掉后重新克隆。
        src = _make_source_repo()
        plugin_root = Path(tempfile.mkdtemp())
        dest = plugin_root / "sys"
        dest.mkdir()
        (dest / "stray.txt").write_text("x", encoding="utf-8")
        info = sync_agents.sync_repo(str(src), "main", dest)
        self.assertTrue(info["commit"])
        self.assertFalse((dest / "stray.txt").exists())
        self.assertTrue((dest / "agents.manifest.json").is_file())


class WriteBoardConfigTest(unittest.TestCase):
    SAMPLE = (
        "{\n"
        '  "apiVersion": 1,\n'
        '  "agentsRepo": {\n'
        '    "url": "",\n'
        '    "ref": "main"\n'
        "  },\n"
        '  "supported_service_units": ["OLD1", "OLD2"],\n'
        '  "inspectCommands": { "darwin": { "x": "y" } }\n'
        "}\n"
    )

    def _write(self, text: str) -> Path:
        cfg = Path(tempfile.mkdtemp()) / "board_config.json"
        cfg.write_text(text, encoding="utf-8")
        return cfg

    def test_replaces_only_the_array_and_preserves_rest(self):
        cfg = self._write(self.SAMPLE)
        sync_agents.merge_supported_units_into_board_config(["A", "B", "C"], cfg)
        out = cfg.read_text(encoding="utf-8")
        self.assertEqual(json.loads(out)["supported_service_units"], ["A", "B", "C"])
        self.assertIn('  "apiVersion": 1,\n', out)
        self.assertIn('  "inspectCommands": { "darwin": { "x": "y" } }\n', out)
        self.assertIn('"supported_service_units": ["A", "B", "C"],', out)
        # 仅定点替换、不重排：总行数不变
        self.assertEqual(len(out.splitlines()), len(self.SAMPLE.splitlines()))

    def test_inserts_after_agentsRepo_when_key_absent(self):
        text = (
            "{\n"
            '  "agentsRepo": {\n'
            '    "url": "",\n'
            '    "ref": "main"\n'
            "  },\n"
            '  "inspectCommands": {}\n'
            "}\n"
        )
        cfg = self._write(text)
        sync_agents.merge_supported_units_into_board_config(["A"], cfg)
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["supported_service_units"], ["A"])
        self.assertEqual(data["inspectCommands"], {})

    def test_empty_list_writes_empty_array(self):
        cfg = self._write(self.SAMPLE)
        sync_agents.merge_supported_units_into_board_config([], cfg)
        out = cfg.read_text(encoding="utf-8")
        self.assertEqual(json.loads(out)["supported_service_units"], [])
        self.assertIn('"supported_service_units": [],', out)

    def test_missing_agentsRepo_anchor_raises_and_does_not_write(self):
        text = '{\n  "inspectCommands": {}\n}\n'
        cfg = self._write(text)
        with self.assertRaises(RuntimeError):
            sync_agents.merge_supported_units_into_board_config(["A"], cfg)
        self.assertEqual(cfg.read_text(encoding="utf-8"), text)  # 未落盘

    def test_against_real_board_config_copy_changes_exactly_one_line(self):
        real = (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        cfg = self._write(real)
        sync_agents.merge_supported_units_into_board_config(["X1", "X2"], cfg)
        after = cfg.read_text(encoding="utf-8").splitlines()
        before = real.splitlines()
        self.assertEqual(json.loads("\n".join(after))["supported_service_units"], ["X1", "X2"])
        self.assertEqual(len(before), len(after))
        diffs = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(diffs), 1)
        self.assertIn("supported_service_units", before[diffs[0]])


class WriteBoardConfigWiringTest(unittest.TestCase):
    """main() 里 --write-board-config 的触发条件（monkeypatch，不碰真实文件）。"""

    def _run_main(self, argv, run_payload):
        calls = []
        orig_run = sync_agents.run
        orig_merge = sync_agents.merge_supported_units_into_board_config
        sync_agents.run = lambda *a, **k: dict(run_payload)
        sync_agents.merge_supported_units_into_board_config = (
            lambda units, *a, **k: calls.append(list(units))
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                sync_agents.main(argv)
        finally:
            sync_agents.run = orig_run
            sync_agents.merge_supported_units_into_board_config = orig_merge
        return calls

    def test_flag_triggers_write_with_synced_units(self):
        calls = self._run_main(
            ["--write-board-config"],
            {"ok": True, "supported_service_units": ["U1", "U2"], "message": "x"},
        )
        self.assertEqual(calls, [["U1", "U2"]])

    def test_no_flag_does_not_write(self):
        calls = self._run_main(
            [], {"ok": True, "supported_service_units": ["U1"], "message": "x"}
        )
        self.assertEqual(calls, [])

    def test_flag_skips_write_when_sync_failed(self):
        calls = self._run_main(["--write-board-config"], {"ok": False, "message": "fail"})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
