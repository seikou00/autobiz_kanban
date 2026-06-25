#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 agents 知识库仓库（UI 触发），产出 service units 供宿主合并进 board.json。

board_config.json 注册::

    "sync_agents": "python3 ${pluginPath}/hooks/sync_agents.py"

仓库地址来自 board_config.json 顶层 ``agentsRepo``（可被 --repo-url/--ref 覆盖）::

    "agentsRepo": { "url": "https://git.example.com/agents-kb.git", "ref": "main" }

行为：克隆/更新到 ``<pluginPath>/sys/``（已 .gitignore）；仓库内含
``agents.manifest.json`` 与 ``<systemId>/AGENTS.md``。随后把清单整形为 stdout JSON。

输出（stdout，宿主据此把 supported_service_units 合并进 board.json）::

    { "ok": true, "schemaVersion": "...", "message": "...",
      "repo": {"url","ref","commit"},
      "supported_service_units": [...],
      "systems": [ {"systemId","systemName","agentsReady","agentsPath","serviceUnits":[...]} ] }

UI 直调约定：逻辑失败也输出 ok:false 的 JSON 并 exit 0，绝不让 UI 收到非 JSON。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import (  # noqa: E402
    SYNC_SCHEMA_VERSION,
    AgentsManifestError,
    build_sync_payload,
    get_agents_root,
)
from board_core.contracts import BoardConfigError, load_board_config  # noqa: E402

BOARD_CONFIG_PATH = ROOT / "board_core" / "board_config.json"
DEFAULT_REF = "main"


def _fail(message: str, errors: Optional[List[str]] = None) -> dict:
    return {
        "ok": False,
        "schemaVersion": SYNC_SCHEMA_VERSION,
        "message": message,
        "errors": errors or [message],
    }


def _resolve_repo(repo_url: Optional[str], ref: Optional[str]) -> Tuple[str, str]:
    """合并 CLI 覆盖与 board_config.json 的 agentsRepo；返回 (url, ref)。"""
    url = (repo_url or "").strip()
    resolved_ref = (ref or "").strip()
    if not url or not resolved_ref:
        config = load_board_config(BOARD_CONFIG_PATH)
        agents_repo = config.get("agentsRepo") if isinstance(config, dict) else None
        if isinstance(agents_repo, dict):
            url = url or str(agents_repo.get("url", "") or "").strip()
            resolved_ref = resolved_ref or str(agents_repo.get("ref", "") or "").strip()
    return url, (resolved_ref or DEFAULT_REF)


def _run_git(args: List[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_error(proc: subprocess.CompletedProcess, action: str) -> str:
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = detail[-1] if detail else f"git 返回码 {proc.returncode}"
    return f"{action} 失败: {tail}"


def sync_repo(url: str, ref: str, dest: Path) -> dict:
    """克隆或更新仓库到 dest；返回 {commit} 或抛 RuntimeError(消息)。"""
    git_dir = dest / ".git"
    if git_dir.is_dir():
        fetch = _run_git(["fetch", "--depth", "1", "origin", ref], cwd=dest)
        if fetch.returncode != 0:
            raise RuntimeError(_git_error(fetch, f"更新 fetch origin/{ref}"))
        reset = _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=dest)
        if reset.returncode != 0:
            raise RuntimeError(_git_error(reset, "更新 reset --hard"))
        # 清掉残留的旧系统目录/未跟踪文件，保持缓存与远端一致。
        _run_git(["clean", "-fd"], cwd=dest)
    else:
        if dest.exists() and any(dest.iterdir()):
            raise RuntimeError(f"缓存目录已存在且非 git 仓库，请手动清理后重试: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        clone = _run_git(["clone", "--depth", "1", "--branch", ref, url, str(dest)])
        if clone.returncode != 0:
            # ref 可能是 commit/tag，--branch 不适用：退回普通 clone 再切换。
            fallback = _run_git(["clone", "--depth", "1", url, str(dest)])
            if fallback.returncode != 0:
                raise RuntimeError(_git_error(fallback, "克隆"))
            checkout = _run_git(["checkout", ref], cwd=dest)
            if checkout.returncode != 0:
                raise RuntimeError(_git_error(checkout, f"切换到 {ref}"))

    rev = _run_git(["rev-parse", "HEAD"], cwd=dest)
    commit = rev.stdout.strip() if rev.returncode == 0 else ""
    return {"commit": commit}


def run(repo_url: Optional[str], ref: Optional[str]) -> dict:
    try:
        url, resolved_ref = _resolve_repo(repo_url, ref)
    except BoardConfigError as exc:
        return _fail(f"读取 board_config.json 失败: {exc}")

    if not url:
        return _fail(
            "未配置 agents 仓库地址：请在 board_core/board_config.json 顶层补充 "
            'agentsRepo.url（或用 --repo-url 传入）'
        )

    dest = get_agents_root()
    try:
        repo_info = sync_repo(url, resolved_ref, dest)
    except FileNotFoundError:
        return _fail("未找到 git 可执行文件，请确认运行环境已安装 git")
    except RuntimeError as exc:
        return _fail(str(exc))

    repo_info = {"url": url, "ref": resolved_ref, **repo_info}
    try:
        payload = build_sync_payload(repo_info=repo_info)
    except AgentsManifestError as exc:
        result = _fail(f"仓库已拉取但清单不可用: {exc}")
        result["repo"] = repo_info
        return result
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="拉取 agents 知识库仓库并产出 service units（UI 触发）",
        allow_abbrev=False,
    )
    parser.add_argument("--repo-url", dest="repo_url", default=None, help="覆盖 board_config.json 的 agentsRepo.url")
    parser.add_argument("--ref", dest="ref", default=None, help="覆盖 agentsRepo.ref（分支/标签/commit，默认 main）")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    result = run(args.repo_url, args.ref)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
