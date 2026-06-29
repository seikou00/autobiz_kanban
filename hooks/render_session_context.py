#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""session_context_inject 动态提示词注入（createFeature 选中服务单元 -> sessionContext）。

board_config.json 注册（样例，附件约定）::

    "session_context_inject": "python3 ${pluginPath}/hooks/render_session_context.py --selected-serviceUnit ${selectedServiceUnits}"

入参 ``--selected-serviceUnit`` 是一个 JSON 数组字符串（serviceUnitId = 后端单元 id）::

    --selected-serviceUnit '[{"serviceUnitId":"LF39.18_Outservice","localRepoPath":"/repo/out"}]'

输出（固定形状，注入项目模式系统提示词）::

    { "ok": true, "message": "...", "sessionContext": "...",
      "agentmdLoadStatus": [ {serviceUnitId, path, loaded, source, message} ] }

``sessionContext`` 三段拼接（见 docs/agents-loading-remote-local.md），三个层次各用一对
XML 风格标签外包，使被嵌入 md 自带的 ``#``/``##`` 标题被「外包」在标签内，不再与结构标记冲突：
  ① 适用范围 ``<SCOPE>``：清单 description（引用范围）+ UI localRepoPath（代码地址）。
  ② 系统级 AGENTS.md ``<SYSTEM>``：选中单元所属系统 agentsPath 全文，按 systemId 去重。
  ③ 各单元 description.md ``<UNIT>``：选中单元 agentsPath 全文，按选择顺序；空则跳过。

加载策略按层次不同：
  · 系统级（②）只认 remote：本机不知道用户把系统级文件放在哪，**不走 local 兜底**（否则会拿
    单元级的 ``<localRepoPath>/AGENTS.md`` 冒名顶替）。找到就生成 ``<SYSTEM>`` 段，
    找不到就不生成。**系统级结果不进 agentmdLoadStatus**——状态只反映单元级（③）的加载结果。
  · 单元级（③）remote 优先 → local 兜底（``<localRepoPath>/AGENTS.md``）→ 都无则 ``loaded:false``，
    每个选中单元产出一条 agentmdLoadStatus。

系统级（②）正文里的 ``{project_root}`` 占位符在拼接前替换为该系统在 sys/ 下的目录绝对路径
（``<pluginPath>/sys/<systemId>``）；单元级（③）不做替换，占位符原样保留。

设计原则：除入参 JSON 非法外，任何情况都返回 ok:true，绝不抛异常中断会话；
缺清单 / 未匹配单元 / 缺 AGENTS.md 都降级为「少注入一点」并在 message 说明。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.agents_repo import (  # noqa: E402
    AgentsManifestError,
    Manifest,
    get_agents_root,
    index_unit_pairs,
    load_manifest,
    sys_abspath,
    sys_display,
)

PROJECT_ROOT_PLACEHOLDER = "{project_root}"  # md 正文里的占位符，替换为该系统 sys/<systemId> 绝对路径

LOCAL_AGENTS_MD = "AGENTS.md"  # local 兜底文件名（§8 #1：直接读用户仓库既有 AGENTS.md）


def _parse_selected(raw: Optional[str]) -> List[dict]:
    """解析 --selected-serviceUnit。空/缺省 -> []；非法 -> ValueError。"""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--selected-serviceUnit 不是合法 JSON: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError("--selected-serviceUnit 必须是 JSON 数组")
    selected: List[dict] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"--selected-serviceUnit[{idx}] 必须是对象")
        unit_id = item.get("serviceUnitId")
        if not isinstance(unit_id, str) or not unit_id.strip():
            raise ValueError(f"--selected-serviceUnit[{idx}].serviceUnitId 必须是非空字符串")
        local_repo = item.get("localRepoPath", "")
        selected.append(
            {
                "serviceUnitId": unit_id.strip(),
                "localRepoPath": local_repo if isinstance(local_repo, str) else "",
            }
        )
    return selected


def _read_nonempty(path: Path) -> Optional[str]:
    """读取文本；不存在或全空白时返回 None。"""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text if text.strip() else None


def _fill_project_root(
    content: str, system_id: str, *, plugin_root: Optional[Path]
) -> str:
    """把系统级正文里的 ``{project_root}`` 占位符替换为该系统在 sys/ 下的目录绝对路径
    （``<pluginPath>/sys/<systemId>``，即 sys 绝对路径 + systemId）。仅系统级（②）调用，
    单元级不替换。

    无占位符 / 无 systemId 时原样返回。
    """
    if not system_id or PROJECT_ROOT_PLACEHOLDER not in content:
        return content
    root_dir = get_agents_root(plugin_root) / system_id
    return content.replace(PROJECT_ROOT_PLACEHOLDER, str(root_dir))


def _resolve_one(
    owner_uid: str,
    rel_in_manifest: str,
    local_repo: str,
    *,
    plugin_root: Optional[Path],
    allow_local_fallback: bool = True,
) -> Tuple[dict, Optional[Path], Optional[str]]:
    """一个 md 文件：remote 优先 →（可选）local 兜底 → 都无则 loaded:false。

    ``allow_local_fallback=False`` 时只认 remote：系统级 AGENTS.md 用此模式——本机不知道用户
    把系统级文件放在哪，不能拿 ``<localRepoPath>/AGENTS.md``（那是单元级兜底）冒名顶替，
    remote 缺失就直接报 remote 未命中，不走 local。

    返回 (status, abs_path, content)；content 非 None 表示成功加载、可进正文。
    """
    # ① remote：清单里有该路径 且 sys/ 下文件存在 → 用 remote，忽略 local。
    if rel_in_manifest:
        try:
            abs_path = sys_abspath(rel_in_manifest, plugin_root)
        except AgentsManifestError:
            abs_path = None  # 路径非法（穿越/绝对）：当作 remote 取不到，转 local 兜底。
        if abs_path is not None:
            content = _read_nonempty(abs_path)
            if content is not None:
                status = {
                    "serviceUnitId": owner_uid,
                    "path": sys_display(rel_in_manifest, plugin_root),
                    "loaded": True,
                    "source": "remote",
                    "message": "",
                }
                return status, abs_path, content

    # 不允许 local 兜底（系统级）：remote 缺失即报 remote 未命中，不去碰 <localRepoPath>/AGENTS.md。
    if not allow_local_fallback:
        status = {
            "serviceUnitId": owner_uid,
            "path": sys_display(rel_in_manifest, plugin_root) if rel_in_manifest else "",
            "loaded": False,
            "source": "remote",
            "message": "file not exist",
        }
        return status, None, None

    # ② local 兜底：未命中清单 或 remote 文件缺失 → 读 <localRepoPath>/AGENTS.md。
    local_abs = (Path(local_repo) / LOCAL_AGENTS_MD) if local_repo else None
    if local_abs is not None:
        content = _read_nonempty(local_abs)
        if content is not None:
            status = {
                "serviceUnitId": owner_uid,
                "path": str(local_abs),
                "loaded": True,
                "source": "local",
                "message": "",
            }
            return status, local_abs, content

    # ③ remote 与 local 都没有。
    status = {
        "serviceUnitId": owner_uid,
        "path": str(local_abs) if local_abs is not None else "",
        "loaded": False,
        "source": "local",
        "message": "file not exist",
    }
    return status, None, None


def _md_cell(text: str) -> str:
    """转义表格单元里的 `|` 与换行，避免用户值（description/localRepoPath）撑破表格。"""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


# 三个层次各用一对 XML 风格标签外包：被嵌入 md 自带的 `#`/`##` 标题被包在标签内，不再与
# 结构标记同级交错（取代旧的 Markdown 二级标题 + ``<a id>`` 锚点；锚点改放标签 id 属性）。
SCOPE_TAG = "SCOPE"   # ① 适用范围
SYSTEM_TAG = "SYSTEM"     # ② 系统级 AGENTS.md
UNIT_TAG = "UNIT"    # ③ 各单元 description.md


def _attr(text: str) -> str:
    """转义 XML 属性值里的 & < > "，避免 description 等自由文本撑破标签。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _compose_prompt(
    bindings: List[dict],
    system_sections: List[dict],
    unit_sections: List[dict],
) -> str:
    """① 适用范围（绑定表，serviceUnitId 为锚点链接）→ ② 系统级 AGENTS.md → ③ 各单元 description.md。

    三个层次各用一对 XML 风格标签外包（``<SCOPE>`` / ``<SYSTEM>`` /
    ``<UNIT>``）；被嵌入 md 自带的 ``#``/``##`` 标题被包在标签内，不再与结构
    标记同级交错。锚点放在 ②③ 标签的 ``id`` 属性上，适用范围表里的 serviceUnitId 仍以
    ``[id](#anchor)`` 指向下方对应段（有单元段指向单元段，否则指向所属系统段；无内容则不加链接）。

    每个标签行用 inline code（反引号）包裹并前后留空行：
      · 反引号让标签成为「行内代码」→ 被包进独立的 ``<p>`` 块、字符原样保留（literal），
        各渲染器行为一致；否则裸标签会被当成原始 HTML——相邻的 ``</SCOPE>``/``<UNIT…>``
        会被折叠到同一行、或连同紧随的 ``## 标题`` 整块不解析（标签文字外泄、标题不渲染）。
      · 前后空行使每个标签各自成段，块间留出换行。
    """
    lines: List[str] = []
    lines.append("# 统一 Agent 指令")
    lines.append("")
    # ① 适用范围：绑定表（serviceUnitId 锚点链接指向下方 ②/③ 段的 id 属性）。
    lines.append(f"`<{SCOPE_TAG}>`")
    lines.append("")
    lines.append("## 适用范围")
    lines.append(
        "本文件是 产品的前端、后端工程工作的统一入口，执行任务前必须先确认下面的工程映射，避免把前端、后端或文档索引路径识别错。"
        "开发、排查与代码修改时优先在这些路径内进行（serviceUnitId 链接指向下方对应知识库段）："
    )
    lines.append("")
    lines.append("| 引用范围 | serviceUnitId | 代码地址（localRepoPath） |")
    lines.append("| --- | --- | --- |")
    for binding in bindings:
        ref = _md_cell(binding.get("ref") or "(未匹配知识库)")
        repo = _md_cell(binding.get("localRepoPath") or "(未提供)")
        uid_text = _md_cell(binding["serviceUnitId"])
        cell = f"[{uid_text}](#{binding['anchor']})" if binding.get("anchor") else uid_text
        lines.append(f"| {ref} | {cell} | {repo} |")
    lines.append("")
    lines.append(f"`</{SCOPE_TAG}>`")

    # ② 系统级 AGENTS.md：每段外包 <SYSTEM>，id 供 ① 表锚点定位。
    for section in system_sections:
        lines.append("")
        lines.append(
            f'`<{SYSTEM_TAG} id="sys-{section["systemId"]}" system="{_attr(section["title"])}">`'
        )
        lines.append("")
        lines.append(section["content"].strip())
        lines.append("")
        lines.append(f"`</{SYSTEM_TAG}>`")

    # ③ 各单元 description.md：每段外包 <UNIT>，id 供 ① 表锚点定位。
    for section in unit_sections:
        title = section["serviceUnitId"]
        if section.get("ref"):
            title += f"（{section['ref']}）"
        lines.append("")
        lines.append(
            f'`<{UNIT_TAG} id="unit-{section["serviceUnitId"]}" unit="{_attr(title)}">`'
        )
        lines.append("")
        lines.append(section["content"].strip())
        lines.append("")
        lines.append(f"`</{UNIT_TAG}>`")

    return "\n".join(lines)


def render(selected: List[dict], *, plugin_root: Optional[Path] = None) -> dict:
    """核心逻辑（无 I/O 边界外副作用），便于单测。"""
    if not selected:
        return {
            "ok": True,
            "message": "未选择服务单元，无需注入",
            "sessionContext": "",
            "agentmdLoadStatus": [],
        }

    # 清单不可用（缺失/非法）时降级：所有单元当作未命中，直接走 local 兜底。
    manifest: Optional[Manifest]
    try:
        manifest = load_manifest(plugin_root)
    except AgentsManifestError:
        manifest = None
    pairs = index_unit_pairs(manifest) if manifest is not None else {}

    load_status: List[dict] = []
    system_sections: List[dict] = []  # ② 按 systemId 去重，首次出现顺序
    unit_sections: List[dict] = []    # ③ 按选择顺序
    seen_systems: set[str] = set()
    seen_paths: set[Path] = set()     # 正文按解析后绝对路径去重
    system_loaded: set[str] = set()   # 实际产出系统段的 systemId（供锚点回退）
    unit_has_section: set[str] = set()  # 实际产出单元段的 serviceUnitId（供锚点指向）

    def _append_body(abs_path: Optional[Path], bucket: List[dict], section: dict) -> bool:
        if abs_path is not None and abs_path not in seen_paths:
            seen_paths.add(abs_path)
            bucket.append(section)
            return True
        return False

    for sel in selected:
        uid = sel["serviceUnitId"]
        local = sel["localRepoPath"]
        pair = pairs.get(uid)
        if pair is not None:
            system, unit = pair
            # ② 系统级 AGENTS.md —— 每个系统一次（归属首个被选中单元）。
            if system.system_id not in seen_systems:
                seen_systems.add(system.system_id)
                # 系统级只查 remote；不进 agentmdLoadStatus（状态只反映单元级结果）。
                # 找到就生成 <SYSTEM> 段，找不到就不生成（status 丢弃，仅取 content）。
                _status, abs_path, content = _resolve_one(
                    uid, system.agents_relpath(), local,
                    plugin_root=plugin_root, allow_local_fallback=False,
                )
                if content is not None:
                    content = _fill_project_root(
                        content, system.system_id, plugin_root=plugin_root
                    )
                    title = system.system_id
                    if system.system_name:
                        title += f"（{system.system_name}）"
                    if _append_body(
                        abs_path, system_sections,
                        {"systemId": system.system_id, "title": title, "content": content},
                    ):
                        system_loaded.add(system.system_id)
            # ③ 单元级 description.md —— 仅当单元自带 agentsPath。
            if unit.agents_rel:
                status, abs_path, content = _resolve_one(
                    uid, unit.agents_rel, local, plugin_root=plugin_root
                )
                load_status.append(status)
                # 单元级不做 {project_root} 替换（占位符原样保留）。
                if content is not None and _append_body(
                    abs_path, unit_sections,
                    {"serviceUnitId": uid, "ref": unit.name, "content": content},
                ):
                    unit_has_section.add(uid)
        else:
            # 未命中清单：只走 local 兜底（rel 为空）。
            status, abs_path, content = _resolve_one(uid, "", local, plugin_root=plugin_root)
            load_status.append(status)
            if content is not None and _append_body(
                abs_path, unit_sections,
                {"serviceUnitId": uid, "ref": "", "content": content},
            ):
                unit_has_section.add(uid)

    # 适用范围：每个选中单元一行（引用范围 = 清单 description；代码地址 = localRepoPath）；
    # serviceUnitId 锚点：有单元段→指向单元段，否则→指向所属系统段，再否则→无链接。
    bindings: List[dict] = []
    for sel in selected:
        uid = sel["serviceUnitId"]
        pair = pairs.get(uid)
        if uid in unit_has_section:
            anchor = f"unit-{uid}"
        elif pair is not None and pair[0].system_id in system_loaded:
            anchor = f"sys-{pair[0].system_id}"
        else:
            anchor = ""
        bindings.append(
            {
                "serviceUnitId": uid,
                "ref": pair[1].name if pair else "",
                "localRepoPath": sel["localRepoPath"],
                "anchor": anchor,
            }
        )

    prompt = _compose_prompt(bindings, system_sections, unit_sections)
    remote_n = sum(1 for s in load_status if s["loaded"] and s["source"] == "remote")
    local_n = sum(1 for s in load_status if s["loaded"] and s["source"] == "local")
    miss_n = sum(1 for s in load_status if not s["loaded"])
    message = f"remote {remote_n} / local {local_n} / 缺 {miss_n}"
    return {
        "ok": True,
        "message": message,
        "sessionContext": prompt,
        "agentmdLoadStatus": load_status,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="session_context_inject: 选中服务单元 -> 注入 sessionContext（适用范围+系统级+各单元）",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--selected-serviceUnit",
        dest="selected",
        default="",
        help="JSON 数组字符串：[{\"serviceUnitId\":\"...\",\"localRepoPath\":\"...\"}]",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        selected = _parse_selected(args.selected)
    except ValueError as exc:
        result = {
            "ok": False,
            "message": str(exc),
            "sessionContext": "",
            "agentmdLoadStatus": [],
        }
    else:
        result = render(selected)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
