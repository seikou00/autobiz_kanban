#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 agents 知识库仓库（UI 触发），产出 service units 供宿主合并进 board.json。

board_config.json 注册::

    "pull_knowledge": "python3 ${pluginPath}/hooks/sync_agents.py --write-board-config"

仓库地址来自 board_config.json 顶层 ``agentsRepo``（可被 --repo-url/--ref 覆盖）::

    "agentsRepo": { "url": "https://git.example.com/agents-kb.git", "ref": "main" }

行为：每次都删掉旧 ``<pluginPath>/sys/``（已 .gitignore）再重新克隆，始终拿到远端
最新内容；仓库内含 ``agents.manifest.json`` 与 ``<systemId>/AGENTS.md``。随后把清单整形为 stdout JSON。

输出（stdout，宿主据此把 supported_deploy_units 合并进 board.json）::

    { "ok": true, "schemaVersion": "...", "message": "...",
      "repo": {"url","ref","commit"},
      "knowledge_path": "<pluginPath>/sys",  # 克隆落盘路径，与 repo 同级；写进 board.json 的
                                             # inspectCommands.<platform>.knowledge_path
      "supported_deploy_units": [...],
      "systems": [ {"systemId","systemName","agentsReady","agentsPath","deployUnits":[...]} ] }

``--write-board-config``（已写进注册的 pull_knowledge 命令，UI 每次拉取即触发）：同步成功后
把 ``supported_deploy_units`` 定点写回 board_config.json 顶层同名字段，并把克隆落盘路径写回
``inspectCommands.<当前平台>.knowledge_path``（把预置的 ``${pluginPath}/sys`` 静态模板改写成解析出的
绝对路径，只改当前 OS 那一处）。两处都正则定点替换、不重排整份文件；写前校验仍为合法 JSON，
否则放弃写入并在结果里给出 boardConfigWriteError。
只读安装环境可从命令里去掉该参数、改为打包前手动 bake 一次。

UI 直调约定：逻辑失败也输出 ok:false 的 JSON 并 exit 0，绝不让 UI 收到非 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
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


def _rmtree(path: Path) -> None:
    """删除目录树；Windows 上 git 会给 ``.git/objects/pack`` 等文件加只读位，
    直接 rmtree 抛 PermissionError——去掉只读位后重试删除。"""

    def _clear_readonly(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly)
    else:
        shutil.rmtree(path, onerror=_clear_readonly)


def sync_repo(url: str, ref: str, dest: Path) -> dict:
    """每次都删掉旧 ``sys/`` 再重新克隆到 dest；返回 {commit} 或抛 RuntimeError(消息)。

    不做增量 fetch：无论 dest 之前是 git 仓库、残留的非 git 目录、还是不存在，
    都先整目录删除再 clone。这样始终拿到远端最新内容，也避免「旧目录非 git」
    之类的边角错误（简单、可预测）。
    """
    if dest.exists():
        _rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone = _run_git(["clone", "--depth", "1", "--branch", ref, url, str(dest)])
    if clone.returncode != 0:
        # ref 可能是 commit/tag，--branch 不适用：退回普通 clone 再切换。
        if dest.exists():
            _rmtree(dest)
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
    except OSError as exc:
        # 如清理旧 sys/ 时文件仍被占用（杀毒软件/编辑器锁定）：保持 UI 只收 JSON 的契约。
        return _fail(f"同步 agents 仓库失败: {exc}")

    repo_info = {"url": url, "ref": resolved_ref, **repo_info}
    try:
        payload = build_sync_payload(repo_info=repo_info)
    except AgentsManifestError as exc:
        result = _fail(f"仓库已拉取但清单不可用: {exc}")
        result["repo"] = repo_info
        result["knowledge_path"] = str(dest)  # 已克隆，给出落盘路径（与 repo 同级）
        return result
    return payload


def merge_supported_units_into_board_config(
    units: List[str], config_path: Path = BOARD_CONFIG_PATH
) -> None:
    """把同步得到的 supported_deploy_units 定点写回 board_config.json（打包前 bake）。

    只替换顶层 ``"supported_deploy_units": [...]`` 这一处的数组值（正则定点替换、
    不重排整份文件，保留其余手写格式）；该键不存在时插到 ``agentsRepo`` 块之后。
    写盘前用 ``json.loads`` 校验结果仍是合法 JSON 且该字段已等于 units，否则抛
    RuntimeError 不落盘——确保任何情况下都不会写坏 board_config.json。
    """
    text = config_path.read_text(encoding="utf-8")
    serialized = json.dumps(units, ensure_ascii=False)  # 形如 ["a", "b"]，与手写风格一致
    array_pat = re.compile(r'("supported_deploy_units"\s*:\s*)\[[^\]]*\]')
    if array_pat.search(text):
        new_text = array_pat.sub(lambda m: m.group(1) + serialized, text, count=1)
    else:
        # 键缺失：插到 agentsRepo 对象（含其后逗号）之后，沿用顶层两空格缩进。
        anchor = re.search(r'"agentsRepo"\s*:\s*\{[^}]*\}\s*,', text)
        if not anchor:
            raise RuntimeError("board_config.json 缺少 agentsRepo 锚点，无法插入 supported_deploy_units")
        insertion = anchor.group(0) + f'\n  "supported_deploy_units": {serialized},'
        new_text = text[: anchor.start()] + insertion + text[anchor.end() :]

    try:
        parsed = json.loads(new_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"写回后 board_config.json 非合法 JSON，已放弃写入: {exc}") from exc
    if parsed.get("supported_deploy_units") != units:
        raise RuntimeError("写回校验失败：supported_deploy_units 未按预期更新，已放弃写入")
    config_path.write_text(new_text, encoding="utf-8")


def _platform_key(platform: Optional[str] = None) -> str:
    """把 ``sys.platform`` 归一到 board_config.json inspectCommands 的三平台键。"""
    p = (platform or sys.platform).lower()
    if p.startswith("win"):
        return "win32"
    if p == "darwin":
        return "darwin"
    return "linux"


def merge_knowledge_path_into_board_config(
    knowledge_path: str,
    config_path: Path = BOARD_CONFIG_PATH,
    platform: Optional[str] = None,
) -> None:
    """把克隆落盘路径定点写回 board_config.json 的
    ``inspectCommands.<platform>.knowledge_path``（仅当前运行平台那一处）。

    knowledge_path 在三平台块里都以 ``${pluginPath}/sys`` 静态模板预置；本机拉取后把它
    改写成解析出的绝对路径（machine-specific，只对当前 OS 有效，故只改当前平台块、不动另外
    两个平台的模板）。``knowledge_path`` 是各平台块的首个键，正则以「平台名 + 首键」定位，
    只替换那一处字符串值、不重排整份文件。写盘前用 ``json.loads`` 校验结果仍合法且该字段已
    等于 knowledge_path，否则抛 RuntimeError 不落盘。
    """
    key = _platform_key(platform)
    text = config_path.read_text(encoding="utf-8")
    serialized = json.dumps(knowledge_path, ensure_ascii=False)  # JSON 字符串字面量（转义反斜杠等）
    # 以「"<platform>": { "knowledge_path":」为锚（knowledge_path 是平台块首键），只命中本平台那处。
    value_pat = re.compile(
        r'("' + re.escape(key) + r'"\s*:\s*\{\s*"knowledge_path"\s*:\s*)"(?:[^"\\]|\\.)*"'
    )
    if not value_pat.search(text):
        raise RuntimeError(
            f"board_config.json 的 inspectCommands.{key} 缺少 knowledge_path 首键，无法写回"
        )
    new_text = value_pat.sub(lambda m: m.group(1) + serialized, text, count=1)

    try:
        parsed = json.loads(new_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"写回后 board_config.json 非合法 JSON，已放弃写入: {exc}") from exc
    commands = parsed.get("inspectCommands")
    block = commands.get(key) if isinstance(commands, dict) else None
    if not isinstance(block, dict) or block.get("knowledge_path") != knowledge_path:
        raise RuntimeError("写回校验失败：knowledge_path 未按预期更新，已放弃写入")
    config_path.write_text(new_text, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="拉取 agents 知识库仓库并产出 service units（UI 触发）",
        allow_abbrev=False,
    )
    parser.add_argument("--repo-url", dest="repo_url", default=None, help="覆盖 board_config.json 的 agentsRepo.url")
    parser.add_argument("--ref", dest="ref", default=None, help="覆盖 agentsRepo.ref（分支/标签/commit，默认 main）")
    parser.add_argument(
        "--write-board-config",
        dest="write_board_config",
        action="store_true",
        help="同步成功后把 supported_deploy_units 定点写回 board_config.json（打包前 bake 用；UI 常规拉取不必带）",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    result = run(args.repo_url, args.ref)
    if args.write_board_config and result.get("ok") and isinstance(result.get("supported_deploy_units"), list):
        try:
            merge_supported_units_into_board_config(result["supported_deploy_units"])
            # 知识库落盘路径写回 inspectCommands.<当前平台>.knowledge_path（把静态模板改写成绝对路径）。
            if isinstance(result.get("knowledge_path"), str) and result["knowledge_path"]:
                merge_knowledge_path_into_board_config(result["knowledge_path"])
            result["boardConfigWritten"] = True
        except (OSError, RuntimeError) as exc:
            result["boardConfigWritten"] = False
            result["boardConfigWriteError"] = str(exc)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
