"""Tests for hooks/sync_agents.py: repo resolution, failure path, end-to-end git sync."""

from __future__ import annotations

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


class ResolveRepoTest(unittest.TestCase):
    def test_cli_override_wins_without_reading_config(self):
        url, ref = sync_agents._resolve_repo("https://example.com/a.git", "dev")
        self.assertEqual((url, ref), ("https://example.com/a.git", "dev"))

    def test_falls_back_to_board_config(self):
        # 真实 board_config.json 的 agentsRepo.url 为空、ref 为 main
        url, ref = sync_agents._resolve_repo(None, None)
        self.assertEqual(url, "")
        self.assertEqual(ref, "main")


class RunFailurePathTest(unittest.TestCase):
    def test_missing_url_returns_ok_false_without_touching_git(self):
        result = sync_agents.run(None, None)  # 真实配置 url 为空
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

        # 远端新增系统并提交
        manifest = json.loads((src / "agents.manifest.json").read_text(encoding="utf-8"))
        manifest["systems"].append({"systemId": "LA64", "serviceUnits": [{"serviceUnitId": "LA64.05_UEXgateway"}]})
        (src / "agents.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (src / "LA64").mkdir()
        (src / "LA64" / "AGENTS.md").write_text("# LA64\n", encoding="utf-8")
        _git(["add", "-A"], src)
        _git(["commit", "-m", "add LA64"], src)

        # 第二次：走更新分支（.git 已存在）
        info2 = sync_agents.sync_repo(str(src), "main", dest)
        self.assertNotEqual(info2["commit"], info["commit"])
        self.assertTrue((dest / "LA64" / "AGENTS.md").is_file())
        payload2 = build_sync_payload(plugin_root=plugin_root)
        self.assertEqual(
            payload2["supported_service_units"], ["LF39.18_Outservice", "LA64.05_UEXgateway"]
        )

    def test_clone_into_nonempty_nongit_dir_fails(self):
        src = _make_source_repo()
        plugin_root = Path(tempfile.mkdtemp())
        dest = plugin_root / "sys"
        dest.mkdir()
        (dest / "stray.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            sync_agents.sync_repo(str(src), "main", dest)


if __name__ == "__main__":
    unittest.main()
