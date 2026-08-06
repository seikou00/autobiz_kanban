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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks import sync_agents  # noqa: E402
from hooks.agents_repo import build_sync_payload  # noqa: E402

GIT = shutil.which("git")


def _git_result(returncode=0, *, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


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
        with mock.patch.object(
            sync_agents,
            "load_board_config",
            side_effect=AssertionError("config should not be read"),
        ):
            url, ref, ssh_url = sync_agents._resolve_repo(
                "https://example.com/a.git", "dev"
            )
        self.assertEqual((url, ref, ssh_url), ("https://example.com/a.git", "dev", ""))

    def test_falls_back_to_empty_board_config(self):
        with _temp_board_config('{"agentsRepo": {"url": "", "ref": "main"}}'):
            url, ref, ssh_url = sync_agents._resolve_repo(None, None)
        self.assertEqual(url, "")
        self.assertEqual(ref, "main")
        self.assertEqual(ssh_url, "")

    def test_falls_back_to_configured_url(self):
        config = (
            '{"agentsRepo": {"url": "https://git/x.git", '
            '"sshUrl": "git@git:x.git", "ref": "dev"}}'
        )
        with _temp_board_config(config):
            url, ref, ssh_url = sync_agents._resolve_repo(None, None)
        self.assertEqual((url, ref, ssh_url), ("https://git/x.git", "dev", "git@git:x.git"))

    def test_explicit_repo_url_does_not_inherit_configured_ssh_url(self):
        config = (
            '{"agentsRepo": {"url": "https://git/config.git", '
            '"sshUrl": "git@git:config.git", "ref": "dev"}}'
        )
        with _temp_board_config(config):
            url, ref, ssh_url = sync_agents._resolve_repo(
                "https://git/override.git", None
            )
        self.assertEqual(url, "https://git/override.git")
        self.assertEqual(ref, "dev")
        self.assertEqual(ssh_url, "")

    def test_explicit_empty_repo_url_still_disables_configured_ssh_url(self):
        config = (
            '{"agentsRepo": {"url": "https://git/config.git", '
            '"sshUrl": "git@git:config.git", "ref": "dev"}}'
        )
        with _temp_board_config(config):
            url, ref, ssh_url = sync_agents._resolve_repo("", None)
        self.assertEqual((url, ref, ssh_url), ("https://git/config.git", "dev", ""))

    def test_explicit_ssh_url_overrides_config_and_is_trimmed(self):
        config = (
            '{"agentsRepo": {"url": "https://git/config.git", '
            '"sshUrl": "git@git:config.git", "ref": "dev"}}'
        )
        with _temp_board_config(config):
            url, ref, ssh_url = sync_agents._resolve_repo(
                None, None, "  git@git:override.git  "
            )
        self.assertEqual((url, ref, ssh_url), ("https://git/config.git", "dev", "git@git:override.git"))

    def test_explicit_empty_ssh_url_disables_configured_fallback(self):
        config = (
            '{"agentsRepo": {"url": "https://git/config.git", '
            '"sshUrl": "git@git:config.git", "ref": "dev"}}'
        )
        with _temp_board_config(config):
            url, ref, ssh_url = sync_agents._resolve_repo(None, None, "")
        self.assertEqual((url, ref, ssh_url), ("https://git/config.git", "dev", ""))


class RunFailurePathTest(unittest.TestCase):
    def test_missing_url_returns_ok_false_without_touching_git(self):
        with _temp_board_config('{"agentsRepo": {"url": "", "ref": "main"}}'):
            result = sync_agents.run(None, None)
        self.assertFalse(result["ok"])
        self.assertIn("未配置 agents 仓库地址", result["message"])

    def test_https_and_ssh_failure_still_outputs_json_and_exit_zero(self):
        dest = Path(tempfile.mkdtemp()) / "sys"
        failures = [
            _git_result(1, stderr="https branch unavailable"),
            _git_result(1, stderr="https clone unavailable"),
            _git_result(1, stderr="ssh branch unavailable"),
            _git_result(1, stderr="ssh clone unavailable"),
        ]
        stdout = io.StringIO()
        with mock.patch.object(sync_agents, "get_agents_root", return_value=dest), mock.patch.object(
            sync_agents, "_run_git", side_effect=failures
        ), contextlib.redirect_stdout(stdout):
            code = sync_agents.main(
                [
                    "--repo-url",
                    "https://git/x.git",
                    "--ssh-url",
                    "git@git:x.git",
                    "--ref",
                    "main",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertIn("HTTPS 克隆失败", payload["message"])
        self.assertIn("https clone unavailable", payload["message"])
        self.assertIn("SSH 兜底失败", payload["message"])
        self.assertIn("ssh clone unavailable", payload["message"])


class RunSuccessPathTest(unittest.TestCase):
    def test_payload_records_canonical_urls_and_actual_transport(self):
        config = (
            '{"agentsRepo": {"url": "https://git/x.git", '
            '"sshUrl": "git@git:x.git", "ref": "dev"}}'
        )
        with _temp_board_config(config), mock.patch.object(
            sync_agents,
            "sync_repo",
            return_value={"commit": "abc123", "transport": "ssh"},
        ) as sync_repo_mock, mock.patch.object(
            sync_agents,
            "build_sync_payload",
            side_effect=lambda *, repo_info: {"ok": True, "repo": repo_info},
        ):
            result = sync_agents.run(None, None)

        sync_repo_mock.assert_called_once_with(
            "https://git/x.git",
            "dev",
            mock.ANY,
            "git@git:x.git",
        )
        self.assertEqual(
            result["repo"],
            {
                "url": "https://git/x.git",
                "sshUrl": "git@git:x.git",
                "ref": "dev",
                "commit": "abc123",
                "transport": "ssh",
            },
        )


class KnowledgeIndexWiringTest(unittest.TestCase):
    """克隆后生成知识库索引这一步：warnings 透传、失败仍出合法 JSON。"""

    CONFIG = '{"agentsRepo": {"url": "https://git/x.git", "ref": "main"}}'

    def _run_with(self, build_side_effect):
        with _temp_board_config(self.CONFIG), mock.patch.object(
            sync_agents, "sync_repo", return_value={"commit": "abc", "transport": "https"}
        ), mock.patch.object(
            sync_agents.knowledge_index, "build", side_effect=build_side_effect
        ), mock.patch.object(
            sync_agents,
            "build_sync_payload",
            side_effect=lambda *, repo_info: {"ok": True, "repo": repo_info},
        ):
            return sync_agents.run(None, None)

    def test_warnings_flow_into_payload(self):
        result = self._run_with(
            lambda dest: {"generated": True, "warnings": ["a.md: 缺必填字段 sub_product"]}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["warnings"], ["a.md: 缺必填字段 sub_product"])

    def test_payload_has_warnings_key_even_when_clean(self):
        result = self._run_with(lambda dest: {"generated": True, "warnings": []})
        self.assertEqual(result["warnings"], [])

    def test_index_build_failure_returns_ok_false_with_repo_context(self):
        def _boom(dest):
            raise sync_agents.FrontmatterError("索引写入失败。修复：检查 sys/ 目录是否可写")

        result = self._run_with(_boom)
        self.assertFalse(result["ok"])
        self.assertIn("知识库索引生成失败", result["message"])
        self.assertIn("修复", result["message"])
        self.assertEqual(result["repo"]["commit"], "abc")
        self.assertIn("knowledge_path", result)

    def test_index_build_oserror_returns_ok_false(self):
        def _boom(dest):
            raise OSError("Read-only file system")

        result = self._run_with(_boom)
        self.assertFalse(result["ok"])
        self.assertIn("索引落盘失败", result["message"])
        self.assertIn("可写", result["message"])

    def test_index_failure_still_emits_json_and_exit_zero(self):
        """AC6：UI 直调约定——逻辑失败也必须是合法 JSON + exit 0。"""
        stdout = io.StringIO()
        with _temp_board_config(self.CONFIG), mock.patch.object(
            sync_agents, "sync_repo", return_value={"commit": "abc", "transport": "https"}
        ), mock.patch.object(
            sync_agents.knowledge_index,
            "build",
            side_effect=sync_agents.FrontmatterError("坏了。修复：看这里"),
        ), contextlib.redirect_stdout(stdout):
            code = sync_agents.main([])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["ok"])

    def test_manifest_unavailable_path_keeps_warnings(self):
        """索引生成成功但清单仍不可用时，warnings 是诊断线索，不能丢。"""
        with _temp_board_config(self.CONFIG), mock.patch.object(
            sync_agents, "sync_repo", return_value={"commit": "abc", "transport": "https"}
        ), mock.patch.object(
            sync_agents.knowledge_index,
            "build",
            return_value={"generated": False, "warnings": ["全库无 frontmatter，回落 agents.manifest.json"]},
        ), mock.patch.object(
            sync_agents,
            "build_sync_payload",
            side_effect=sync_agents.AgentsManifestError("未找到清单文件"),
        ):
            result = sync_agents.run(None, None)

        self.assertFalse(result["ok"])
        self.assertIn("清单不可用", result["message"])
        self.assertEqual(len(result["warnings"]), 1)


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
            {"systemId": "LF39", "systemName": "外联", "deployUnits": [
                {"deployUnitId": "LF39.18_Outservice", "name": "出站"}]},
        ],
    }
    (src / "agents.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (src / "LF39").mkdir()
    (src / "LF39" / "AGENTS.md").write_text("# LF39\n- 走统一网关\n", encoding="utf-8")
    _git(["add", "-A"], src)
    _git(["commit", "-m", "init"], src)
    return src


class SyncRepoFallbackTest(unittest.TestCase):
    HTTPS = "https://git.example.com/agents.git"
    SSH = "git@git.example.com:agents.git"

    def _dest(self):
        return Path(tempfile.mkdtemp()) / "sys"

    def test_https_branch_clone_success_does_not_use_ssh(self):
        dest = self._dest()
        results = [
            _git_result(),
            _git_result(stdout="https-commit\n"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            info = sync_agents.sync_repo(self.HTTPS, "main", dest, self.SSH)

        self.assertEqual(info, {"commit": "https-commit", "transport": "https"})
        self.assertEqual(run_git.call_count, 2)
        self.assertTrue(
            all(self.SSH not in args for (args,), _ in run_git.call_args_list)
        )

    def test_https_plain_clone_and_checkout_success_does_not_use_ssh(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="branch form failed"),
            _git_result(),
            _git_result(),
            _git_result(stdout="checkout-commit\n"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            info = sync_agents.sync_repo(self.HTTPS, "commit-ish", dest, self.SSH)

        self.assertEqual(info, {"commit": "checkout-commit", "transport": "https"})
        self.assertEqual(run_git.call_count, 4)
        self.assertTrue(
            all(self.SSH not in args for (args,), _ in run_git.call_args_list)
        )

    def test_https_clone_failures_are_cleaned_before_ssh_success(self):
        dest = self._dest()
        marker = dest / "partial"
        calls = []

        def fake_run_git(args, *, cwd=None, timeout=None):
            calls.append((args, cwd, timeout))
            if len(calls) == 1:
                dest.mkdir(parents=True)
                marker.write_text("https branch partial", encoding="utf-8")
                return _git_result(1, stderr="https branch unavailable")
            if len(calls) == 2:
                self.assertFalse(marker.exists())
                dest.mkdir(parents=True)
                marker.write_text("https plain partial", encoding="utf-8")
                return _git_result(1, stderr="https clone unavailable")
            if len(calls) == 3:
                self.assertFalse(marker.exists())
                return _git_result()
            if len(calls) == 4:
                return _git_result(stdout="ssh-commit\n")
            raise AssertionError(f"unexpected git call: {args}")

        with mock.patch.object(sync_agents, "_run_git", side_effect=fake_run_git):
            info = sync_agents.sync_repo(self.HTTPS, "main", dest, self.SSH)

        self.assertEqual(info, {"commit": "ssh-commit", "transport": "ssh"})
        clone_urls = [args[-2] for args, _, _ in calls if args[0] == "clone"]
        self.assertEqual(clone_urls, [self.HTTPS, self.HTTPS, self.SSH])

    def test_https_clone_timeout_stops_https_and_uses_ssh(self):
        dest = self._dest()
        marker = dest / "partial"

        def fake_run_git(args, *, cwd=None, timeout=None):
            if args[0] == "clone" and self.HTTPS in args:
                self.assertEqual(timeout, sync_agents.HTTPS_CLONE_TIMEOUT_SECONDS)
                dest.mkdir(parents=True, exist_ok=True)
                marker.write_text("https partial", encoding="utf-8")
                raise subprocess.TimeoutExpired(["git", *args], timeout)
            if args[0] == "clone" and self.SSH in args:
                self.assertIsNone(timeout)
                self.assertFalse(marker.exists())
                return _git_result()
            if args[0] == "rev-parse":
                return _git_result(stdout="ssh-commit\n")
            raise AssertionError(f"unexpected git call: {args}")

        with mock.patch.object(sync_agents, "_run_git", side_effect=fake_run_git) as run_git:
            info = sync_agents.sync_repo(self.HTTPS, "main", dest, self.SSH)

        self.assertEqual(info, {"commit": "ssh-commit", "transport": "ssh"})
        self.assertEqual(run_git.call_count, 3)

    def test_https_plain_clone_also_has_timeout(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="branch form failed"),
            subprocess.TimeoutExpired(["git", "clone"], sync_agents.HTTPS_CLONE_TIMEOUT_SECONDS),
            _git_result(),
            _git_result(stdout="ssh-commit\n"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            info = sync_agents.sync_repo(self.HTTPS, "main", dest, self.SSH)

        self.assertEqual(info, {"commit": "ssh-commit", "transport": "ssh"})
        self.assertEqual(
            [kwargs.get("timeout") for _, kwargs in run_git.call_args_list],
            [20, 20, None, None],
        )

    def test_ssh_uses_same_plain_clone_and_checkout_strategy(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="https branch unavailable"),
            _git_result(1, stderr="https clone unavailable"),
            _git_result(1, stderr="ssh branch form failed"),
            _git_result(),
            _git_result(),
            _git_result(stdout="ssh-checkout-commit\n"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            info = sync_agents.sync_repo(self.HTTPS, "commit-ish", dest, self.SSH)

        self.assertEqual(info, {"commit": "ssh-checkout-commit", "transport": "ssh"})
        clone_urls = [
            args[-2]
            for (args,), _ in run_git.call_args_list
            if args[0] == "clone"
        ]
        self.assertEqual(clone_urls, [self.HTTPS, self.HTTPS, self.SSH, self.SSH])
        checkout_calls = [
            args
            for (args,), _ in run_git.call_args_list
            if args[0] == "checkout"
        ]
        self.assertEqual(len(checkout_calls), 1)
        self.assertEqual(checkout_calls[0], ["checkout", "commit-ish"])

    def test_checkout_failure_does_not_trigger_ssh(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="branch form failed"),
            _git_result(),
            _git_result(1, stderr="unknown ref"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            with self.assertRaises(RuntimeError) as ctx:
                sync_agents.sync_repo(self.HTTPS, "missing-ref", dest, self.SSH)

        self.assertIn("切换到 missing-ref 失败", str(ctx.exception))
        self.assertEqual(run_git.call_count, 3)
        self.assertTrue(
            all(self.SSH not in args for (args,), _ in run_git.call_args_list)
        )

    def test_missing_ssh_url_keeps_existing_failure_behavior(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="branch unavailable"),
            _git_result(1, stderr="https unavailable"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            with self.assertRaises(RuntimeError) as ctx:
                sync_agents.sync_repo(self.HTTPS, "main", dest)

        self.assertIn("https unavailable", str(ctx.exception))
        self.assertNotIn("SSH 兜底失败", str(ctx.exception))
        self.assertEqual(run_git.call_count, 2)

    def test_non_https_primary_never_uses_ssh_fallback(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="branch unavailable"),
            _git_result(1, stderr="local clone unavailable"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results) as run_git:
            with self.assertRaises(RuntimeError):
                sync_agents.sync_repo("/local/repo", "main", dest, self.SSH)

        self.assertEqual(run_git.call_count, 2)
        self.assertTrue(
            all(self.SSH not in args for (args,), _ in run_git.call_args_list)
        )

    def test_both_transports_fail_with_combined_diagnostics(self):
        dest = self._dest()
        results = [
            _git_result(1, stderr="https branch unavailable"),
            _git_result(1, stderr="https clone unavailable"),
            _git_result(1, stderr="ssh branch unavailable"),
            _git_result(1, stderr="ssh clone unavailable"),
        ]
        with mock.patch.object(sync_agents, "_run_git", side_effect=results):
            with self.assertRaises(RuntimeError) as ctx:
                sync_agents.sync_repo(self.HTTPS, "main", dest, self.SSH)

        message = str(ctx.exception)
        self.assertIn("HTTPS 克隆失败", message)
        self.assertIn("https clone unavailable", message)
        self.assertIn("SSH 兜底失败", message)
        self.assertIn("ssh clone unavailable", message)


def _make_frontmatter_source_repo() -> Path:
    """带 frontmatter、不带 agents.manifest.json 的知识库仓（已迁移形态）。"""
    src = Path(tempfile.mkdtemp()) / "kb-src"
    src.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(src)], capture_output=True, text=True, check=True)

    def _md(rel, **fields):
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        head = "\n".join("{}: {}".format(k, v) for k, v in fields.items())
        path.write_text("---\n{}\n---\n\n# 正文\n".format(head), encoding="utf-8")

    _md("LF3918/AGENTS.md", type="product knowledge", title="中台导航",
        description="系统总览", sub_product="LF39.18")
    _md("LF3918/03_Backend_Services/services/focusone/glossary.md",
        type="service knowledge", title="领域术语表", description="业务术语",
        sub_product="LF39.18", deploy_unit="LF39.18_focusone")
    _md("LF3918/04_Frontend_Apps/services/wgWebFlow/ui.md",
        type="component reference", title="UI 体系", description="组件约定",
        sub_product="LF39.18", deploy_unit="LF39.18_wgflowweb")
    _md("BAD.md", type="product knowledge", title="缺字段")   # 触发 warning

    _git(["add", "-A"], src)
    _git(["commit", "-m", "init"], src)
    return src


@unittest.skipUnless(GIT, "git not available")
class FrontmatterRepoEndToEndTest(unittest.TestCase):
    """真实克隆一个带 frontmatter 的知识库，跑完整 run()：
    frontmatter → 生成索引 → supported_deploy_units。"""

    @contextlib.contextmanager
    def _isolated(self, dest: Path):
        """把克隆落点与索引读取点都挪到临时目录，绝不碰随包的 sys/。"""
        from hooks import agents_repo

        with mock.patch.object(sync_agents, "get_agents_root", return_value=dest), \
                mock.patch.object(agents_repo, "get_agents_root", return_value=dest):
            yield

    def test_supported_units_derived_from_frontmatter(self):
        src = _make_frontmatter_source_repo()
        dest = Path(tempfile.mkdtemp()) / "sys"
        with self._isolated(dest), _temp_board_config(
            json.dumps({"agentsRepo": {"url": str(src), "ref": "main"}})
        ):
            result = sync_agents.run(None, None)

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(
            result["supported_deploy_units"], ["LF39.18_focusone", "LF39.18_wgflowweb"]
        )
        system = result["systems"][0]
        self.assertEqual(system["systemId"], "LF39.18")
        self.assertEqual(system["systemName"], "中台导航")
        self.assertTrue(system["agentsReady"])
        self.assertFalse((dest / "agents.manifest.json").exists())
        self.assertTrue((dest / "knowledge.index.json").is_file())

    def test_bad_document_surfaces_as_warning_without_failing_sync(self):
        src = _make_frontmatter_source_repo()
        dest = Path(tempfile.mkdtemp()) / "sys"
        with self._isolated(dest), _temp_board_config(
            json.dumps({"agentsRepo": {"url": str(src), "ref": "main"}})
        ):
            result = sync_agents.run(None, None)

        self.assertTrue(result["ok"])
        bad = [w for w in result["warnings"] if w.startswith("BAD.md")]
        self.assertEqual(len(bad), 1)
        self.assertIn("description/sub_product", bad[0])
        self.assertIn("修复", bad[0])

    def test_legacy_repo_without_frontmatter_still_works(self):
        """未迁移的知识库：不生成索引，回落手写清单，行为与改造前一致。"""
        src = _make_source_repo()   # 有 agents.manifest.json、md 全裸
        dest = Path(tempfile.mkdtemp()) / "sys"
        with self._isolated(dest), _temp_board_config(
            json.dumps({"agentsRepo": {"url": str(src), "ref": "main"}})
        ):
            result = sync_agents.run(None, None)

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["supported_deploy_units"], ["LF39.18_Outservice"])
        self.assertFalse((dest / "knowledge.index.json").exists())
        self.assertTrue(any("回落 agents.manifest.json" in w for w in result["warnings"]))


@unittest.skipUnless(GIT, "git not available")
class SyncRepoEndToEndTest(unittest.TestCase):
    def test_clone_then_update(self):
        src = _make_source_repo()
        plugin_root = Path(tempfile.mkdtemp())
        dest = plugin_root / "sys"

        # 首次：克隆
        info = sync_agents.sync_repo(str(src), "main", dest)
        self.assertTrue(info["commit"])
        self.assertEqual(info["transport"], "other")
        self.assertTrue((dest / "agents.manifest.json").is_file())
        self.assertTrue((dest / "LF39" / "AGENTS.md").is_file())

        payload = build_sync_payload(plugin_root=plugin_root, repo_info={"url": str(src), "ref": "main", **info})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["supported_deploy_units"], ["LF39.18_Outservice"])
        self.assertTrue(payload["systems"][0]["agentsReady"])
        # 知识库落盘路径与 repo 同级，指向克隆缓存根 <pluginPath>/sys。
        self.assertEqual(payload["knowledge_path"], str(dest))

        # 远端新增系统并提交
        manifest = json.loads((src / "agents.manifest.json").read_text(encoding="utf-8"))
        manifest["systems"].append({"systemId": "LA64", "deployUnits": [{"deployUnitId": "LA64.05_UEXgateway"}]})
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
            payload2["supported_deploy_units"], ["LF39.18_Outservice", "LA64.05_UEXgateway"]
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

    def test_readonly_leftover_is_wiped_and_recloned(self):
        # Windows 上 git 给 .git/objects/pack 等文件加只读位，第二次同步删旧目录时
        # 曾直接 PermissionError。模拟只读残留文件，确认清理会先去只读位再删。
        import stat

        src = _make_source_repo()
        plugin_root = Path(tempfile.mkdtemp())
        dest = plugin_root / "sys"
        packs = dest / ".git" / "objects" / "pack"
        packs.mkdir(parents=True)
        readonly = packs / "pack-x.idx"
        readonly.write_text("x", encoding="utf-8")
        readonly.chmod(stat.S_IREAD)
        info = sync_agents.sync_repo(str(src), "main", dest)
        self.assertTrue(info["commit"])
        self.assertFalse(readonly.exists())
        self.assertTrue((dest / "agents.manifest.json").is_file())


class WriteBoardConfigTest(unittest.TestCase):
    SAMPLE = (
        "{\n"
        '  "apiVersion": 1,\n'
        '  "agentsRepo": {\n'
        '    "url": "",\n'
        '    "sshUrl": "git@git.example.com:agents.git",\n'
        '    "ref": "main"\n'
        "  },\n"
        '  "supported_deploy_units": ["OLD1", "OLD2"],\n'
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
        self.assertEqual(json.loads(out)["supported_deploy_units"], ["A", "B", "C"])
        self.assertIn('  "apiVersion": 1,\n', out)
        self.assertIn('  "inspectCommands": { "darwin": { "x": "y" } }\n', out)
        self.assertIn('"supported_deploy_units": ["A", "B", "C"],', out)
        self.assertEqual(
            json.loads(out)["agentsRepo"]["sshUrl"],
            "git@git.example.com:agents.git",
        )
        # 仅定点替换、不重排：总行数不变
        self.assertEqual(len(out.splitlines()), len(self.SAMPLE.splitlines()))

    def test_inserts_after_agentsRepo_when_key_absent(self):
        text = (
            "{\n"
            '  "agentsRepo": {\n'
            '    "url": "",\n'
            '    "sshUrl": "git@git.example.com:agents.git",\n'
            '    "ref": "main"\n'
            "  },\n"
            '  "inspectCommands": {}\n'
            "}\n"
        )
        cfg = self._write(text)
        sync_agents.merge_supported_units_into_board_config(["A"], cfg)
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["supported_deploy_units"], ["A"])
        self.assertEqual(data["agentsRepo"]["sshUrl"], "git@git.example.com:agents.git")
        self.assertEqual(data["inspectCommands"], {})

    def test_empty_list_writes_empty_array(self):
        cfg = self._write(self.SAMPLE)
        sync_agents.merge_supported_units_into_board_config([], cfg)
        out = cfg.read_text(encoding="utf-8")
        self.assertEqual(json.loads(out)["supported_deploy_units"], [])
        self.assertIn('"supported_deploy_units": [],', out)

    def test_missing_agentsRepo_anchor_raises_and_does_not_write(self):
        text = '{\n  "inspectCommands": {}\n}\n'
        cfg = self._write(text)
        with self.assertRaises(RuntimeError):
            sync_agents.merge_supported_units_into_board_config(["A"], cfg)
        self.assertEqual(cfg.read_text(encoding="utf-8"), text)  # 未落盘

    KP_SAMPLE = (
        "{\n"
        '  "agentsRepo": { "url": "", "ref": "main" },\n'
        '  "inspectCommands": {\n'
        '    "darwin": {\n'
        '      "knowledge_path": "${pluginPath}/sys",\n'
        '      "project_status": "python3 a"\n'
        "    },\n"
        '    "linux": {\n'
        '      "knowledge_path": "${pluginPath}/sys",\n'
        '      "project_status": "python3 b"\n'
        "    },\n"
        '    "win32": {\n'
        '      "knowledge_path": "${pluginPath}\\\\sys",\n'
        '      "project_status": "python c"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )

    def test_knowledge_path_replaces_only_current_platform_block(self):
        cfg = self._write(self.KP_SAMPLE)
        sync_agents.merge_knowledge_path_into_board_config(
            "/abs/plugin/sys", cfg, platform="darwin"
        )
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["inspectCommands"]["darwin"]["knowledge_path"], "/abs/plugin/sys")
        # 其余平台的模板不动
        self.assertEqual(data["inspectCommands"]["linux"]["knowledge_path"], "${pluginPath}/sys")
        self.assertEqual(data["inspectCommands"]["win32"]["knowledge_path"], "${pluginPath}\\sys")
        # 定点替换、不重排：行数不变
        out = cfg.read_text(encoding="utf-8")
        self.assertEqual(len(out.splitlines()), len(self.KP_SAMPLE.splitlines()))

    def test_knowledge_path_targets_win32_block(self):
        cfg = self._write(self.KP_SAMPLE)
        sync_agents.merge_knowledge_path_into_board_config(
            "C:\\plugin\\sys", cfg, platform="win32"
        )
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["inspectCommands"]["win32"]["knowledge_path"], "C:\\plugin\\sys")
        self.assertEqual(data["inspectCommands"]["darwin"]["knowledge_path"], "${pluginPath}/sys")

    def test_knowledge_path_missing_key_raises_and_does_not_write(self):
        text = '{\n  "inspectCommands": { "darwin": { "project_status": "x" } }\n}\n'
        cfg = self._write(text)
        with self.assertRaises(RuntimeError):
            sync_agents.merge_knowledge_path_into_board_config(
                "/abs/sys", cfg, platform="darwin"
            )
        self.assertEqual(cfg.read_text(encoding="utf-8"), text)  # 未落盘

    def test_platform_key_normalizes(self):
        self.assertEqual(sync_agents._platform_key("darwin"), "darwin")
        self.assertEqual(sync_agents._platform_key("linux"), "linux")
        self.assertEqual(sync_agents._platform_key("win32"), "win32")
        self.assertEqual(sync_agents._platform_key("windows"), "win32")
        self.assertEqual(sync_agents._platform_key(" win32 "), "win32")

    def test_against_real_board_config_copy_changes_exactly_one_line(self):
        real = (ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8")
        cfg = self._write(real)
        sync_agents.merge_supported_units_into_board_config(["X1", "X2"], cfg)
        after = cfg.read_text(encoding="utf-8").splitlines()
        before = real.splitlines()
        self.assertEqual(json.loads("\n".join(after))["supported_deploy_units"], ["X1", "X2"])
        self.assertEqual(len(before), len(after))
        diffs = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(diffs), 1)
        self.assertIn("supported_deploy_units", before[diffs[0]])


class WriteBoardConfigWiringTest(unittest.TestCase):
    """main() 里 --write-board-config 的触发条件（monkeypatch，不碰真实文件）。"""

    def _run_main(self, argv, run_payload):
        unit_calls = []
        kp_calls = []
        orig_run = sync_agents.run
        orig_units = sync_agents.merge_supported_units_into_board_config
        orig_kp = sync_agents.merge_knowledge_path_into_board_config
        sync_agents.run = lambda *a, **k: dict(run_payload)
        sync_agents.merge_supported_units_into_board_config = (
            lambda units, *a, **k: unit_calls.append(list(units))
        )
        sync_agents.merge_knowledge_path_into_board_config = (
            lambda path, *a, **k: kp_calls.append(path)
        )
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                sync_agents.main(argv)
        finally:
            sync_agents.run = orig_run
            sync_agents.merge_supported_units_into_board_config = orig_units
            sync_agents.merge_knowledge_path_into_board_config = orig_kp
        return unit_calls, kp_calls

    def test_flag_triggers_write_with_synced_units(self):
        unit_calls, _ = self._run_main(
            ["--write-board-config"],
            {"ok": True, "supported_deploy_units": ["U1", "U2"], "message": "x"},
        )
        self.assertEqual(unit_calls, [["U1", "U2"]])

    def test_flag_triggers_knowledge_path_write(self):
        unit_calls, kp_calls = self._run_main(
            ["--write-board-config"],
            {
                "ok": True,
                "supported_deploy_units": ["U1"],
                "knowledge_path": "/abs/plugin/sys",
                "message": "x",
            },
        )
        self.assertEqual(unit_calls, [["U1"]])
        self.assertEqual(kp_calls, ["/abs/plugin/sys"])

    def test_no_flag_does_not_write(self):
        unit_calls, kp_calls = self._run_main(
            [], {"ok": True, "supported_deploy_units": ["U1"], "knowledge_path": "/s", "message": "x"}
        )
        self.assertEqual(unit_calls, [])
        self.assertEqual(kp_calls, [])

    def test_flag_skips_write_when_sync_failed(self):
        unit_calls, kp_calls = self._run_main(["--write-board-config"], {"ok": False, "message": "fail"})
        self.assertEqual(unit_calls, [])
        self.assertEqual(kp_calls, [])


if __name__ == "__main__":
    unittest.main()
