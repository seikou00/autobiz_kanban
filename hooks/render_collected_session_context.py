#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 collect-knowledge.js 恢复 session_context_inject JSON。

本脚本是 ``render_session_context.py`` 之上的兼容适配层：

1. 检查 collector 同目录的 ``node_modules/gray-matter`` 关键文件，缺失时执行 ``npm install``；
2. 调用 ``node collect-knowledge.js --listDeployUnits --knowledgePath <path>``；
3. 对选中的 deployUnit 调用 ``--deployUnit <id>``；
4. 将返回 JSON 的 ``systemPrompt`` 放入原有 ``sessionContext`` 契约；
5. 列表接口不可用时，整次调用委托给旧 ``render_session_context.render``；
6. 单元未在 collector 列表中或正文接口失败时，先按清单映射加载，再回退本地文件。

查询支持的部署单元 ID 列表见独立脚本 ``list_supported_deploy_ids.py``。

清单映射也不可用时仍可回退到 ``<localRepoPath>/AGENTS.md``。除入参 JSON
非法外，外部接口故障不会中断会话。

各阶段的开始、结束和耗时输出到 stderr，stdout 保持原有 JSON 契约。
remote 未命中时按 ``collector.env`` → ``collector.* stderr/stdout`` →
``unit.<id> result`` → ``render.summary`` 的顺序排查。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import (  # noqa: E402
    AgentsManifestError,
    DeployUnit,
    MANIFEST_NAME,
    SystemEntry,
    display_path_join,
    get_agents_root,
    index_unit_pairs,
    load_manifest,
)
from hooks.render_session_context import (  # noqa: E402
    LOCAL_AGENTS_MD,
    WORKSPACE_AGENTS_MD,
    _agent_config,
    _build_domain_context,
    _build_workspace_content,
    _compose_prompt,
    _domain_context_status,
    _fill_plugin_root,
    _heading_slug,
    _norm_path,
    _parse_selected,
    _read_nonempty,
    _ref_for,
    _resolve_one,
    _session_runtime_policy,
    _unit_heading_label,
    _workspace_binding,
    _workspace_status,
    render as render_legacy,
)

DEFAULT_KNOWLEDGE_COLLECTOR = "collect-knowledge.js"
KNOWLEDGE_COLLECT_TIMEOUT_SECONDS = 30
NPM_INSTALL_TIMEOUT_SECONDS = 300
DIAGNOSTIC_LOG_LIMIT = 2000


class KnowledgeCollectorError(RuntimeError):
    """collector 不可用或返回值不符合约定。"""


def _short_error(text: str, limit: int = 500) -> str:
    compact = " ".join((text or "").strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _as_text(value) -> str:
    """POSIX 下 TimeoutExpired 携带的是 bytes，即使 run 传了 text=True。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _timing_log(stage, event, detail="", limit=500):
    print(
        "[session-context] {} pid={} {} {} {}".format(
            time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            os.getpid(), stage, event, _short_error(detail, limit),
        ).rstrip(),
        file=sys.stderr,
        flush=True,
    )


def _log_collector_env(collector_script, knowledge_path, node_command):
    """collector 自身不写诊断日志，路径和命令是否就绪只能在这里留痕。"""
    try:
        script = Path(collector_script)
        detail = "script={} exists={} cwd={} node={} knowledge_path={} exists={}".format(
            script.resolve(), script.is_file(), os.getcwd(),
            shutil.which(node_command) or "<PATH 中未找到 {}>".format(node_command),
            knowledge_path, Path(knowledge_path).is_dir(),
        )
    except OSError as exc:
        detail = "环境信息采集失败: {}".format(exc)
    _timing_log("collector.env", "info", detail, limit=DIAGNOSTIC_LOG_LIMIT)


def _log_collector_output(stage, stdout, stderr, *, include_stdout=False):
    """stderr 无论成败都记录；stdout 只在失败时记录，避免打印整段知识。"""
    stdout, stderr = _as_text(stdout), _as_text(stderr)
    _timing_log(stage, "output", "stdout_chars={} stderr_chars={}".format(
        len(stdout), len(stderr),
    ))
    if stderr.strip():
        _timing_log(stage, "stderr", stderr, limit=DIAGNOSTIC_LOG_LIMIT)
    if include_stdout and stdout.strip():
        _timing_log(stage, "stdout", stdout, limit=DIAGNOSTIC_LOG_LIMIT)


def _timed_call(stage, function, *args, **kwargs):
    started = time.perf_counter()
    _timing_log(stage, "start")
    try:
        result = function(*args, **kwargs)
    except Exception as exc:
        _timing_log(stage, "error", "elapsed={:.3f}s {}: {}".format(
            time.perf_counter() - started, type(exc).__name__, exc,
        ))
        raise
    detail = "elapsed={:.3f}s".format(time.perf_counter() - started)
    if hasattr(result, "returncode"):
        detail += " returncode={}".format(result.returncode)
    elif isinstance(result, str):
        detail += " chars={}".format(len(result))
    _timing_log(stage, "end", detail)
    return result


def _dependency_files_present(workdir):
    """只检查本地目录和关键文件，不启动 Node/npm。"""
    node_modules = workdir / "node_modules"
    if not node_modules.is_dir():
        return False
    gray_matter = node_modules / "gray-matter"
    return (
        (gray_matter / "package.json").is_file()
        and (gray_matter / "index.js").is_file()
    )


def _npm_install(collector_script: str, *, npm_command: str = "npm") -> str:
    """依赖关键文件缺失时，在 collector 所在目录安装依赖。

    返回空串表示成功，否则返回失败原因，由调用方决定是否继续。
    """
    workdir = Path(collector_script).resolve().parent
    if _timed_call("dependencies.files", _dependency_files_present, workdir):
        _timing_log("npm.install", "skip", "gray-matter package.json/index.js 已存在")
        return ""
    _timing_log("npm.install", "required", "gray-matter 依赖目录或关键文件缺失")
    executable = shutil.which(npm_command) or npm_command
    try:
        proc = _timed_call(
            "npm.install(timeout={}s)".format(NPM_INSTALL_TIMEOUT_SECONDS),
            subprocess.run,
            [executable, "install"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=NPM_INSTALL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        missing = exc.filename or npm_command
        return f"未找到依赖安装命令: {missing}，请安装 Node.js/npm 或用 --npm-command 指定路径"
    except subprocess.TimeoutExpired:
        return f"依赖安装超时（{NPM_INSTALL_TIMEOUT_SECONDS} 秒），请在 {workdir} 手动执行 npm install"
    except OSError as exc:
        return f"启动依赖安装失败: {_short_error(str(exc))}"

    if proc.returncode != 0:
        detail = _short_error(proc.stderr or proc.stdout) or f"返回码 {proc.returncode}"
        return f"npm install 失败: {detail}，请在 {workdir} 手动执行 npm install 并修复依赖"
    return ""


def _run_collector(
    collector_script: str,
    collector_args: List[str],
    *,
    knowledge_path: str,
    node_command: str = "node",
) -> object:
    """以 argv 调用 Node 接口，不通过 shell 解释 deployUnit 或 Windows 路径。"""
    command = [
        node_command,
        collector_script,
        *collector_args,
        "--knowledgePath",
        knowledge_path,
    ]
    stage = "collector.{}".format(" ".join(collector_args))
    _timing_log(stage, "argv", json.dumps(command, ensure_ascii=False),
                limit=DIAGNOSTIC_LOG_LIMIT)
    try:
        proc = _timed_call(
            "{}(timeout={}s)".format(stage, KNOWLEDGE_COLLECT_TIMEOUT_SECONDS),
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=KNOWLEDGE_COLLECT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        missing = exc.filename or node_command
        raise KnowledgeCollectorError(f"未找到知识恢复命令: {missing}") from exc
    except subprocess.TimeoutExpired as exc:
        _log_collector_output(stage, exc.stdout, exc.stderr, include_stdout=True)
        raise KnowledgeCollectorError(
            f"知识恢复接口超时（{KNOWLEDGE_COLLECT_TIMEOUT_SECONDS} 秒）"
        ) from exc
    except OSError as exc:
        raise KnowledgeCollectorError(f"启动知识恢复接口失败: {_short_error(str(exc))}") from exc

    failed = proc.returncode != 0
    _log_collector_output(stage, proc.stdout, proc.stderr, include_stdout=failed)
    if failed:
        detail = _short_error(proc.stderr or proc.stdout) or "无任何输出"
        if not Path(knowledge_path).is_dir():
            # collector 对不存在的 knowledgePath 静默 exit(1)
            detail += f"；knowledgePath 不存在: {knowledge_path}"
        raise KnowledgeCollectorError(
            f"知识恢复接口失败（返回码 {proc.returncode}）: {detail}"
        )
    output = (proc.stdout or "").strip().lstrip("\ufeff")
    if not output:
        raise KnowledgeCollectorError("知识恢复接口未输出 JSON")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        _timing_log(stage, "stdout", output, limit=DIAGNOSTIC_LOG_LIMIT)
        raise KnowledgeCollectorError(
            f"知识恢复接口返回非法 JSON: {_short_error(output)}"
        ) from exc


def _list_deploy_units(
    collector_script: str,
    *,
    knowledge_path: str,
    node_command: str = "node",
) -> List[str]:
    payload = _run_collector(
        collector_script,
        ["--listDeployUnits"],
        knowledge_path=knowledge_path,
        node_command=node_command,
    )
    if not isinstance(payload, list) or any(
        not isinstance(item, str) or not item.strip() for item in payload
    ):
        raise KnowledgeCollectorError("--listDeployUnits 必须返回字符串数组")
    return list(dict.fromkeys(item.strip() for item in payload))


def list_supported_deploy_units(
    collector_script: str,
    *,
    knowledge_path: str,
    node_command: str = "node",
    npm_command: str = "npm",
) -> List[str]:
    """按需安装 collector 依赖并读取其支持的部署单元。"""
    _log_collector_env(collector_script, knowledge_path, node_command)
    npm_error = _npm_install(collector_script, npm_command=npm_command)
    if npm_error:
        _timing_log("npm.install", "warning", npm_error)
    try:
        units = _list_deploy_units(
            collector_script,
            knowledge_path=knowledge_path,
            node_command=node_command,
        )
    except KnowledgeCollectorError as exc:
        reason = f"{npm_error}；{exc}" if npm_error else str(exc)
        raise KnowledgeCollectorError(reason) from exc
    _timing_log("collector.listDeployUnits", "result", "count={} ids={}".format(
        len(units), json.dumps(units, ensure_ascii=False),
    ), limit=DIAGNOSTIC_LOG_LIMIT)
    return units


def _deploy_unit_prompt(
    collector_script: str,
    deploy_unit_id: str,
    *,
    knowledge_path: str,
    node_command: str = "node",
) -> str:
    payload = _run_collector(
        collector_script,
        ["--deployUnit", deploy_unit_id],
        knowledge_path=knowledge_path,
        node_command=node_command,
    )
    if not isinstance(payload, dict):
        # collector 在多个失败分支都只输出 []，这里补上可能原因
        hint = (
            "；collector 返回空数组，通常是应用类型接口(archguardservice)调用失败，"
            "或匹配文件所在目录向上找不到 AGENTS.md"
            if payload == [] else ""
        )
        raise KnowledgeCollectorError(
            f"deployUnit {deploy_unit_id} 必须返回 JSON 对象，实际为 "
            f"{type(payload).__name__}: "
            f"{_short_error(json.dumps(payload, ensure_ascii=False), 200)}{hint}"
        )
    system_prompt = payload.get("systemPrompt")
    file_lists = payload.get("fileLists")
    file_count = len(file_lists) if isinstance(file_lists, list) else "缺失"
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise KnowledgeCollectorError(
            f"deployUnit {deploy_unit_id} 返回缺少非空 systemPrompt"
            f"（keys={sorted(payload)} fileLists={file_count}）"
        )
    _timing_log("unit.{}".format(deploy_unit_id), "remote_hit",
                "prompt_chars={} fileLists={}".format(len(system_prompt), file_count))
    return system_prompt


def _legacy_result(
    selected: List[dict],
    reason: str,
    *,
    plugin_root: Optional[Path],
    session_workspace_path: Optional[str],
    platform: Optional[str],
    node_id: Optional[str],
    plugin_workspace: Optional[str],
    project: Optional[str],
    feature: Optional[str],
    board_config_path: Optional[Path],
    knowledge_path: str,
) -> dict:
    _timing_log("legacy.render", "fallback", reason)
    result = _timed_call(
        "legacy.render", render_legacy,
        selected,
        plugin_root=plugin_root,
        session_workspace_path=session_workspace_path,
        platform=platform,
        node_id=node_id,
        plugin_workspace=plugin_workspace,
        project=project,
        feature=feature,
        board_config_path=board_config_path,
        knowledge_root=Path(knowledge_path),
    )
    old_message = str(result.get("message", "") or "")
    prefix = f"collector 不可用，已回退旧逻辑: {_short_error(reason)}"
    result["message"] = f"{prefix}；{old_message}" if old_message else prefix
    return result


def _similar_units(uid: str, candidates: Set[str]) -> List[str]:
    """忽略大小写找相近 ID，定位 deployUnitId 拼写或大小写不一致。"""
    lowered = {candidate.lower(): candidate for candidate in candidates}
    matches = difflib.get_close_matches(uid.lower(), list(lowered), n=3, cutoff=0.8)
    return [lowered[match] for match in matches]


def _resolve_unit(
    selected: dict,
    *,
    supported_units: Set[str],
    collector_script: str,
    knowledge_path: str,
    platform: Optional[str],
    node_command: str,
    manifest_pair: Optional[Tuple[SystemEntry, DeployUnit]] = None,
) -> Tuple[dict, Optional[Path], Optional[str], Optional[dict]]:
    uid = selected["deployUnitId"]
    collector_error = ""
    if uid not in supported_units:
        similar = _similar_units(uid, supported_units)
        collector_error = "collector 文档头未匹配 deployUnit: {}（列表共 {} 个{}）".format(
            uid, len(supported_units),
            "，相近: " + ", ".join(similar) if similar else "",
        )
    else:
        try:
            prompt = _deploy_unit_prompt(
                collector_script,
                uid,
                knowledge_path=knowledge_path,
                node_command=node_command,
            )
        except KnowledgeCollectorError as exc:
            collector_error = str(exc)
        else:
            return (
                {
                    "deployUnitId": uid,
                    "path": display_path_join(knowledge_path, platform=platform),
                    "loaded": True,
                    "source": "remote",
                    "message": "",
                },
                None,
                prompt,
                None,
            )

    # collector 的 header 覆盖集合并非发布单元全集；清单映射是独立的知识来源。
    system_section = None
    if manifest_pair is not None:
        system, unit = manifest_pair
        root = Path(knowledge_path)
        _timing_log("unit.{}".format(uid), "manifest_fallback", collector_error)
        system_status, system_path, system_content = _resolve_one(
            uid, system.agents_relpath(), "",
            plugin_root=None, platform=platform, allow_local_fallback=False,
            knowledge_root=root,
        )
        if system_content is not None:
            title = system.system_id
            if system.system_name:
                title += f"（{system.system_name}）"
            system_section = {
                "systemId": system.system_id,
                "title": title,
                "path": system_path,
                "content": _fill_plugin_root(
                    system_content, plugin_root=None, platform=platform, knowledge_root=root,
                ),
            }
        if unit.agents_rel:
            status, unit_path, content = _resolve_one(
                uid, unit.agents_rel, "",
                plugin_root=None, platform=platform, allow_local_fallback=False,
                knowledge_root=root,
            )
            if content is not None:
                content = _fill_plugin_root(
                    content, plugin_root=None, platform=platform, knowledge_root=root,
                )
        else:
            # 没有独立单元文档时，所属系统知识即为该单元的清单加载结果。
            status, unit_path, content = system_status, None, None
        if status["loaded"]:
            status["message"] = f"已回退 agents.manifest.json: {collector_error}"
            return status, unit_path, content, system_section
        collector_error += "；agents.manifest.json 对应知识文件未找到或为空"

    _timing_log("unit.{}".format(uid), "local_fallback", collector_error)
    local_repo = selected.get("localRepoPath", "")
    local_path = Path(local_repo) / LOCAL_AGENTS_MD if local_repo else None
    local_display = (
        display_path_join(local_repo, LOCAL_AGENTS_MD, platform=platform) if local_repo else ""
    )
    local_content = _read_nonempty(local_path) if local_path is not None else None
    _timing_log("unit.{}".format(uid), "local_agents", "path={} found={}".format(
        local_path or "<未配置 localRepoPath>", local_content is not None,
    ))
    if local_content is not None:
        return (
            {
                "deployUnitId": uid,
                "path": local_display,
                "loaded": True,
                "source": "local",
                "message": f"知识接口未命中，已回退本地 AGENTS.md: {collector_error}",
            },
            local_path,
            local_content,
            system_section,
        )
    return (
        {
            "deployUnitId": uid,
            "path": local_display or display_path_join(knowledge_path, platform=platform),
            "loaded": False,
            "source": "local",
            "message": f"{collector_error}；未找到本地 AGENTS.md",
        },
        None,
        None,
        system_section,
    )


def render(
    selected: List[dict],
    *,
    plugin_root: Optional[Path] = None,
    session_workspace_path: Optional[str] = None,
    platform: Optional[str] = None,
    node_id: Optional[str] = None,
    plugin_workspace: Optional[str] = None,
    project: Optional[str] = None,
    feature: Optional[str] = None,
    board_config_path: Optional[Path] = None,
    collector_script: str = DEFAULT_KNOWLEDGE_COLLECTOR,
    knowledge_path: Optional[str] = None,
    node_command: str = "node",
    npm_command: str = "npm",
) -> dict:
    """collector 优先；未命中的单元按清单映射加载，最后回退本地。"""
    if not selected:
        return _timed_call(
            "legacy.render(empty_selection)", render_legacy,
            selected,
            plugin_root=plugin_root,
            session_workspace_path=session_workspace_path,
            platform=platform,
            node_id=node_id,
            plugin_workspace=plugin_workspace,
            project=project,
            feature=feature,
            board_config_path=board_config_path,
        )

    resolved_knowledge_path = (knowledge_path or "").strip() or str(
        get_agents_root(plugin_root).resolve()
    )
    _timing_log("render.input", "info", "knowledge_path={}({}) units={}".format(
        resolved_knowledge_path,
        "arg" if (knowledge_path or "").strip() else "default",
        json.dumps(
            [
                {"deployUnitId": item["deployUnitId"],
                 "localRepoPath": item.get("localRepoPath", "")}
                for item in selected
            ],
            ensure_ascii=False,
        ),
    ), limit=DIAGNOSTIC_LOG_LIMIT)
    try:
        supported_units = set(
            list_supported_deploy_units(
                collector_script,
                knowledge_path=resolved_knowledge_path,
                node_command=node_command,
                npm_command=npm_command,
            )
        )
    except KnowledgeCollectorError as exc:
        return _legacy_result(
            selected,
            str(exc),
            plugin_root=plugin_root,
            session_workspace_path=session_workspace_path,
            platform=platform,
            node_id=node_id,
            plugin_workspace=plugin_workspace,
            project=project,
            feature=feature,
            board_config_path=board_config_path,
            knowledge_path=resolved_knowledge_path,
        )

    duplicate_unit_ids: set[str] = set()
    manifest_path = Path(resolved_knowledge_path) / MANIFEST_NAME
    pairs = {}
    try:
        manifest = load_manifest(
            duplicate_unit_ids=duplicate_unit_ids,
            manifest_path=manifest_path,
        )
        pairs = index_unit_pairs(manifest)
        for uid in duplicate_unit_ids:
            pairs.pop(uid, None)
    except AgentsManifestError as exc:
        duplicate_unit_ids.clear()
        _timing_log("manifest.load", "warning", str(exc))

    agent_config = _agent_config(
        _timed_call(
            "runtime.policy", _session_runtime_policy,
            node_id,
            plugin_workspace=plugin_workspace,
            project=project,
            feature=feature,
            board_config_path=board_config_path,
        ),
        plugin_root=plugin_root,
        platform=platform,
    )
    workspace_content = _timed_call(
        "workspace.agents", _build_workspace_content, session_workspace_path,
    )
    domain_context = _timed_call(
        "workspace.domain", _build_domain_context, session_workspace_path,
    )
    load_status: List[dict] = []
    system_sections: List[dict] = []
    system_loaded: Set[str] = set()
    unit_sections: List[dict] = []
    unit_has_section: Set[str] = set()
    seen_paths: Set[Path] = set()

    for item in selected:
        if item["deployUnitId"] in duplicate_unit_ids:
            status = {
                "deployUnitId": item["deployUnitId"],
                "path": display_path_join(manifest_path, platform=platform),
                "loaded": False,
                "source": "remote",
                "message": f"agents.manifest.json 中 deployUnitId 重复: {item['deployUnitId']}",
            }
            load_status.append(status)
            _timing_log("unit.{}".format(item["deployUnitId"]), "result", status["message"])
            continue
        status, content_path, content, system_section = _timed_call(
            "unit.{}.resolve".format(item["deployUnitId"]), _resolve_unit,
            item,
            supported_units=supported_units,
            collector_script=collector_script,
            knowledge_path=resolved_knowledge_path,
            platform=platform,
            node_command=node_command,
            manifest_pair=pairs.get(item["deployUnitId"]),
        )
        load_status.append(status)
        _timing_log("unit.{}".format(item["deployUnitId"]), "result",
                    "source={} loaded={} path={} {}".format(
                        status["source"], status["loaded"], status["path"], status["message"],
                    ), limit=DIAGNOSTIC_LOG_LIMIT)
        if system_section is not None:
            key = _norm_path(system_section["path"])
            if system_section["systemId"] not in system_loaded and key not in seen_paths:
                system_sections.append(system_section)
                system_loaded.add(system_section["systemId"])
                seen_paths.add(key)
        if content is None:
            continue
        if content_path is not None:
            key = _norm_path(content_path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
        uid = item["deployUnitId"]
        unit_sections.append(
            {
                "deployUnitId": uid,
                "ref": _ref_for(pairs.get(uid), item),
                "content": content,
            }
        )
        unit_has_section.add(uid)

    if workspace_content is not None:
        workspace_path = Path((session_workspace_path or "").strip()) / WORKSPACE_AGENTS_MD
        if _norm_path(workspace_path) in seen_paths:
            workspace_content = None

    bindings: List[dict] = []
    if workspace_content is not None:
        bindings.append(_workspace_binding(session_workspace_path))
    for item in selected:
        uid = item["deployUnitId"]
        pair = pairs.get(uid)
        ref = _ref_for(pair, item)
        if uid in unit_has_section:
            anchor = _heading_slug(_unit_heading_label(uid, ref))
        elif pair is not None and pair[0].system_id in system_loaded:
            anchor = f"sys-{pair[0].system_id}"
        else:
            anchor = ""
        bindings.append(
            {
                "deployUnitId": uid,
                "ref": ref,
                "localRepoPath": item.get("localRepoPath", ""),
                "anchor": anchor,
            }
        )

    prompt = _timed_call(
        "context.compose", _compose_prompt,
        bindings,
        system_sections,
        workspace_content,
        unit_sections,
        domain_context,
    )
    units_by_source = {"remote": [], "local": [], "miss": []}
    for status in load_status:
        key = status["source"] if status["loaded"] else "miss"
        units_by_source[key].append(status["deployUnitId"])
    remote_n, local_n, miss_n = (len(units_by_source[key]) for key in ("remote", "local", "miss"))
    message = f"remote {remote_n} / local {local_n} / 缺 {miss_n}"
    selected_duplicates = sorted({
        item["deployUnitId"] for item in selected
        if item["deployUnitId"] in duplicate_unit_ids
    })
    if selected_duplicates:
        message += "；清单中重复的 deployUnitId: " + ", ".join(selected_duplicates)
    _timing_log("render.summary", "done", "remote={} local={} miss={}".format(
        json.dumps(units_by_source["remote"], ensure_ascii=False),
        json.dumps(units_by_source["local"], ensure_ascii=False),
        json.dumps(units_by_source["miss"], ensure_ascii=False),
    ), limit=DIAGNOSTIC_LOG_LIMIT)

    session_entries: List[dict] = []
    if workspace_content is not None:
        session_entries.append(_workspace_status(session_workspace_path, platform=platform))
    if domain_context is not None:
        session_entries.append(_domain_context_status(session_workspace_path, platform=platform))
    return {
        "ok": True,
        "message": message,
        "sessionContext": prompt,
        "agentmdLoadStatus": [*session_entries, *load_status],
        "agentConfig": agent_config,
    }


def _main(argv: Optional[List[str]] = None) -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(
        description="session_context_inject: collect-knowledge.js -> sessionContext JSON",
        allow_abbrev=False,
    )
    parser.add_argument("--platform", default=None)
    parser.add_argument("--selected-deployUnit", dest="selected", default="")
    parser.add_argument("--node-id", dest="node_id", default="")
    parser.add_argument("--plugin-workspace", dest="plugin_workspace", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--feature", default="")
    parser.add_argument("--session-workspace-path", dest="session_workspace_path", default="")
    parser.add_argument(
        "--knowledge-path",
        "--knowledgePath",
        dest="knowledge_path",
        default="",
    )
    parser.add_argument(
        "--knowledge-collector",
        dest="collector_script",
        default=DEFAULT_KNOWLEDGE_COLLECTOR,
    )
    parser.add_argument("--node-command", dest="node_command", default="node")
    parser.add_argument("--npm-command", dest="npm_command", default="npm")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        selected = _parse_selected(args.selected)
    except ValueError as exc:
        result = {
            "ok": False,
            "message": str(exc),
            "sessionContext": "",
            "agentmdLoadStatus": [],
            "agentConfig": _agent_config(
                _session_runtime_policy(
                    args.node_id,
                    plugin_workspace=args.plugin_workspace,
                    project=args.project,
                    feature=args.feature,
                ),
                platform=args.platform,
            ),
        }
    else:
        result = _timed_call(
            "context.render(units={})".format(len(selected)), render,
            selected,
            session_workspace_path=args.session_workspace_path,
            platform=args.platform,
            node_id=args.node_id,
            plugin_workspace=args.plugin_workspace,
            project=args.project,
            feature=args.feature,
            collector_script=args.collector_script,
            knowledge_path=args.knowledge_path,
            node_command=args.node_command,
            npm_command=args.npm_command,
        )

    elapsed = time.perf_counter() - started
    message = str(result.get("message", "") or "")
    result["message"] = "[加载耗时 {:.3f}s]{}".format(elapsed, " " + message if message else "")
    _timing_log("session.result", "ready", "ok={} context_chars={} message={}".format(
        result.get("ok"), len(result.get("sessionContext", "")), result.get("message", ""),
    ), limit=DIAGNOSTIC_LOG_LIMIT)
    _timed_call("output.json", json.dump, result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return _timed_call("session.total", _main, argv)


if __name__ == "__main__":
    raise SystemExit(main())
