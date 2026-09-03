#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 collect-knowledge.js 恢复 session_context_inject JSON。

本脚本是 ``render_session_context.py`` 之上的兼容适配层，不修改旧加载器：

1. 在 collector 所在目录执行 ``npm install``（每次都执行，不判断是否首次）；
2. 调用 ``node collect-knowledge.js --listDeployUnits --knowledgePath <path>``；
3. 对选中的 deployUnit 调用 ``--deployUnit <id>``；
4. 将返回 JSON 的 ``systemPrompt`` 放入原有 ``sessionContext`` 契约；
5. 列表接口不可用时，整次调用委托给旧 ``render_session_context.render``。

部署单元接口失败时仍可回退到 ``<localRepoPath>/AGENTS.md``。除入参 JSON
非法外，外部接口故障不会中断会话。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import display_path_join, get_agents_root  # noqa: E402
from hooks.render_session_context import (  # noqa: E402
    LOCAL_AGENTS_MD,
    WORKSPACE_AGENTS_MD,
    _agent_config,
    _build_domain_context,
    _build_workspace_content,
    _compose_prompt,
    _domain_context_status,
    _heading_slug,
    _norm_path,
    _parse_selected,
    _read_nonempty,
    _session_runtime_policy,
    _unit_heading_label,
    _workspace_binding,
    _workspace_status,
    render as render_legacy,
)
from hooks.paths import get_plugin_output_workspace_from_args  # noqa: E402
from hooks.run_context import breadcrumb as run_context_breadcrumb, persist as persist_run_context  # noqa: E402
from hooks.validation_capabilities import persist as persist_validation_capabilities  # noqa: E402

DEFAULT_KNOWLEDGE_COLLECTOR = "collect-knowledge.js"
KNOWLEDGE_COLLECT_TIMEOUT_SECONDS = 30
NPM_INSTALL_TIMEOUT_SECONDS = 300


class KnowledgeCollectorError(RuntimeError):
    """collector 不可用或返回值不符合约定。"""


def _short_error(text: str, limit: int = 500) -> str:
    compact = " ".join((text or "").strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _npm_install(collector_script: str, *, npm_command: str = "npm") -> str:
    """在 collector 所在目录安装依赖；每次都执行，不判断是否首次。

    返回空串表示成功，否则返回失败原因，由调用方决定是否继续。
    """
    workdir = Path(collector_script).resolve().parent
    executable = shutil.which(npm_command) or npm_command
    try:
        proc = subprocess.run(
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
    try:
        proc = subprocess.run(
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
        raise KnowledgeCollectorError(
            f"知识恢复接口超时（{KNOWLEDGE_COLLECT_TIMEOUT_SECONDS} 秒）"
        ) from exc
    except OSError as exc:
        raise KnowledgeCollectorError(f"启动知识恢复接口失败: {_short_error(str(exc))}") from exc

    if proc.returncode != 0:
        detail = _short_error(proc.stderr or proc.stdout) or f"返回码 {proc.returncode}"
        raise KnowledgeCollectorError(f"知识恢复接口失败: {detail}")
    output = (proc.stdout or "").strip().lstrip("\ufeff")
    if not output:
        raise KnowledgeCollectorError("知识恢复接口未输出 JSON")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
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
    """安装 collector 依赖并读取其支持的部署单元。"""
    npm_error = _npm_install(collector_script, npm_command=npm_command)
    try:
        return _list_deploy_units(
            collector_script,
            knowledge_path=knowledge_path,
            node_command=node_command,
        )
    except KnowledgeCollectorError as exc:
        reason = f"{npm_error}；{exc}" if npm_error else str(exc)
        raise KnowledgeCollectorError(reason) from exc


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
        raise KnowledgeCollectorError(f"deployUnit {deploy_unit_id} 必须返回 JSON 对象")
    system_prompt = payload.get("systemPrompt")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise KnowledgeCollectorError(
            f"deployUnit {deploy_unit_id} 返回缺少非空 systemPrompt"
        )
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
) -> dict:
    result = render_legacy(
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
    old_message = str(result.get("message", "") or "")
    prefix = f"collector 不可用，已回退旧逻辑: {_short_error(reason)}"
    result["message"] = f"{prefix}；{old_message}" if old_message else prefix
    return result


def _resolve_unit(
    selected: dict,
    *,
    supported_units: Set[str],
    collector_script: str,
    knowledge_path: str,
    platform: Optional[str],
    node_command: str,
) -> Tuple[dict, Optional[Path], Optional[str]]:
    uid = selected["deployUnitId"]
    collector_error = ""
    if uid not in supported_units:
        collector_error = f"deployUnit 不在知识库列表中: {uid}"
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
            )

    local_repo = selected.get("localRepoPath", "")
    local_path = Path(local_repo) / LOCAL_AGENTS_MD if local_repo else None
    local_display = (
        display_path_join(local_repo, LOCAL_AGENTS_MD, platform=platform) if local_repo else ""
    )
    local_content = _read_nonempty(local_path) if local_path is not None else None
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
    """使用新接口渲染；接口不可用时委托旧 renderer。"""
    if not selected:
        return render_legacy(
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
        )

    agent_config = _agent_config(
        _session_runtime_policy(
            node_id,
            plugin_workspace=plugin_workspace,
            project=project,
            feature=feature,
            board_config_path=board_config_path,
        ),
        plugin_root=plugin_root,
        platform=platform,
    )
    workspace_content = _build_workspace_content(session_workspace_path)
    domain_context = _build_domain_context(session_workspace_path)
    load_status: List[dict] = []
    unit_sections: List[dict] = []
    unit_has_section: Set[str] = set()
    seen_local_paths: Set[Path] = set()

    for item in selected:
        status, local_path, content = _resolve_unit(
            item,
            supported_units=supported_units,
            collector_script=collector_script,
            knowledge_path=resolved_knowledge_path,
            platform=platform,
            node_command=node_command,
        )
        load_status.append(status)
        if content is None:
            continue
        if local_path is not None:
            key = _norm_path(local_path)
            if key in seen_local_paths:
                continue
            seen_local_paths.add(key)
        uid = item["deployUnitId"]
        unit_sections.append(
            {
                "deployUnitId": uid,
                "ref": item.get("description", ""),
                "content": content,
            }
        )
        unit_has_section.add(uid)

    if workspace_content is not None:
        workspace_path = Path((session_workspace_path or "").strip()) / WORKSPACE_AGENTS_MD
        if _norm_path(workspace_path) in seen_local_paths:
            workspace_content = None

    bindings: List[dict] = []
    if workspace_content is not None:
        bindings.append(_workspace_binding(session_workspace_path))
    for item in selected:
        uid = item["deployUnitId"]
        ref = item.get("description", "")
        bindings.append(
            {
                "deployUnitId": uid,
                "ref": ref,
                "localRepoPath": item.get("localRepoPath", ""),
                "anchor": (
                    _heading_slug(_unit_heading_label(uid, ref))
                    if uid in unit_has_section
                    else ""
                ),
            }
        )

    prompt = _compose_prompt(
        bindings,
        [],
        workspace_content,
        unit_sections,
        domain_context,
    )
    remote_n = sum(
        1 for status in load_status if status["loaded"] and status["source"] == "remote"
    )
    local_n = sum(
        1 for status in load_status if status["loaded"] and status["source"] == "local"
    )
    miss_n = sum(1 for status in load_status if not status["loaded"])

    session_entries: List[dict] = []
    if workspace_content is not None:
        session_entries.append(_workspace_status(session_workspace_path, platform=platform))
    if domain_context is not None:
        session_entries.append(_domain_context_status(session_workspace_path, platform=platform))
    return {
        "ok": True,
        "message": f"remote {remote_n} / local {local_n} / 缺 {miss_n}",
        "sessionContext": prompt,
        "agentmdLoadStatus": [*session_entries, *load_status],
        "agentConfig": agent_config,
    }


def main(argv: Optional[List[str]] = None) -> int:
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
        result = render(
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

        if args.plugin_workspace and args.project and args.feature and selected:
            try:
                runtime_workspace = get_plugin_output_workspace_from_args(
                    args.plugin_workspace, args.project
                )
                runtime_context = persist_run_context(
                    runtime_workspace, args.feature, selected
                )
                runtime_capabilities = persist_validation_capabilities(
                    runtime_workspace / ".autobizdevops" / "features" / args.feature,
                    runtime_context,
                )
                runtime_broadcast = run_context_breadcrumb(
                    runtime_workspace, args.feature
                )
                current_context = str(result.get("sessionContext", "") or "")
                result["sessionContext"] = (
                    runtime_broadcast + ("\n\n" + current_context if current_context else "")
                )
                result["runContext"] = runtime_context
                result["validationCapabilities"] = {
                    "catalogDigest": runtime_capabilities.get("catalogDigest"),
                    "count": len(runtime_capabilities.get("capabilities", [])),
                    "capabilities": runtime_capabilities.get("capabilities", []),
                }
                if runtime_context.get("status") != "ready":
                    result["ok"] = False
                    result["message"] = "SCOPE_UNRESOLVED: " + json.dumps(
                        runtime_context.get("errors", []), ensure_ascii=False
                    )
            except (OSError, ValueError) as exc:
                result["ok"] = False
                result["message"] = "SCOPE_UNRESOLVED: {}".format(exc)
                result["runContext"] = {
                    "schemaVersion": "autodev.run-context.v1",
                    "status": "SCOPE_UNRESOLVED",
                    "errors": [{"code": "SCOPE_UNRESOLVED", "detail": str(exc)}],
                }

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
