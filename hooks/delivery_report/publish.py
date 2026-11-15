"""Validate, render and recover an offline report pair under a Feature lock."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import write_text
from .collect import collect
from .sources import canonical, digest

ASSETS = Path(__file__).resolve().parents[2] / "skills/autodev/autodev-delivery-report/assets"
HTML = "DELIVERY_REPORT.html"
JSON = "DELIVERY_REPORT.json"


def templates():
    names = ("report.html", "report.css", "vendor/marked.umd.js", "vendor/purify.min.js",
             "report-markdown.js", "report-charts.js", "report.js",
             "vendor/marked-LICENSE.md", "vendor/DOMPurify-LICENSE.txt")
    return {name: (ASSETS / name).read_text(encoding="utf-8") for name in names}


def default_narrative(data):
    m = data["metrics"]
    if m["tasks"]["value"] is None:
        summary = "当前迭代缺少可核验的任务计划。"
    else:
        summary = (
            f'本轮计划 {m["tasks"]["displayValue"]} 项任务，'
            f'已实现 {m["implemented"]["displayValue"]} 项，已交付 {m["delivered"]["displayValue"]} 项。'
        )
    refs = m["delivered"]["sourceRefs"]
    actions = [r["action"] for r in data["risks"] if isinstance(
        r.get("action"), str) and r.get("status") == "open"]
    return {"summary": summary, "nextActions": actions, "sourceRefs": refs}


def narrative(data, supplied):
    if supplied is None:
        return default_narrative(data)
    if not isinstance(supplied, dict) or supplied.get("inputDigest") != data["snapshot"]["inputDigest"]:
        raise ValueError("摘要对应的采集快照已变化")
    if set(supplied) - {"inputDigest", "summary", "nextActions", "sourceRefs"}:
        raise ValueError("摘要只能提供说明、建议和来源")
    refs = supplied.get("sourceRefs")
    actions = supplied.get("nextActions")
    if not (isinstance(supplied.get("summary"), str) and supplied["summary"].strip() and isinstance(actions, list)
            and all(isinstance(a, str) for a in actions) and isinstance(refs, list) and refs
            and all(isinstance(ref, str) and ref in {s["path"] for s in data["sources"]} for ref in refs)):
        raise ValueError("摘要格式或来源引用无效")
    return {k: supplied[k] for k in ("summary", "nextActions", "sourceRefs")}


def html_template(assets):
    scripts = "\n".join(assets[name] for name in (
        "vendor/marked.umd.js", "vendor/purify.min.js", "report-markdown.js",
        "report-charts.js", "report.js"))
    licenses = "\n".join("/*\n" + assets[name].replace("*/", "* /") + "\n*/" for name in (
        "vendor/marked-LICENSE.md", "vendor/DOMPurify-LICENSE.txt"))
    scripts = licenses + "\n" + scripts
    return (assets["report.html"].replace("{{STYLE}}", assets["report.css"])
            .replace("{{SCRIPT}}", scripts))


def render(data, assets):
    content = canonical(data).decode().replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    # Insert data last, so template tokens appearing in a user's text stay literal.
    return html_template(assets).replace("{{REPORT}}", content)


def verify_pair(directory, assets, feature=None):
    for name in (JSON, HTML):
        if (directory / name).is_symlink():
            raise ValueError("报告路径为符号链接")
    data = json.loads((directory / JSON).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schemaVersion") != "autodev.delivery-report.v1" or feature and data.get("featureId") != feature:
        raise ValueError("报告类型或 Feature 不匹配")
    if data.get("reportId") != digest(canonical({k: v for k, v in data.items() if k != "reportId"})):
        raise ValueError("报告摘要不匹配")
    if data.get("templateDigest") != digest(html_template(assets).encode("utf-8")) or (directory / HTML).read_text(encoding="utf-8") != render(data, assets):
        raise ValueError("HTML、JSON 或模板版本不匹配")
    return data


def runtime(directory):
    home = directory / ".delivery-report"
    if home.is_symlink():
        raise ValueError("无效的交付报告运行目录")
    return home


def finish_pending(root, home):
    pending = home / "pending"
    if not pending.exists():
        return
    # Both files stay in the journal until both destinations have been replaced.
    manifest = json.loads((pending / "hashes.json").read_text(encoding="utf-8"))
    for name in (JSON, HTML):
        if digest((pending / name).read_bytes()) != manifest.get(name):
            raise ValueError("报告发布暂存文件损坏")
    for name in (JSON, HTML):
        if (root / name).is_symlink():
            raise ValueError("无效的报告目标路径")
        write_text(root / name, (pending / name).read_text(encoding="utf-8"))
    if any(digest((root / name).read_bytes()) != manifest[name] for name in (JSON, HTML)):
        raise ValueError("报告写入后复核失败，请重试发布")
    retired = home / "finished"
    if retired.exists():
        shutil.rmtree(retired)
    os.replace(pending, retired)
    shutil.rmtree(retired)


def validate(scope):
    home = runtime(scope.directory)
    with FileLock(home / "write.lock"):
        finish_pending(scope.directory, home)
        data = verify_pair(scope.directory, templates(), scope.feature)
        current = collect(scope)
        for key, val in current.items():
            if data.get(key) != val:
                raise ValueError(f"报告与当前交付产物不一致：{key}")
        return data


def publish(scope, supplied=None):
    home = runtime(scope.directory)
    with FileLock(home / "write.lock"):
        finish_pending(scope.directory, home)
        facts = collect(scope)
        assets = templates()
        template_id = digest(html_template(assets).encode("utf-8"))
        old = None
        try:
            old = verify_pair(scope.directory, assets, scope.feature)
        except (ValueError, OSError):
            pass
        unchanged = old is not None and all(old.get(k) == v for k, v in facts.items())
        note = old["narrative"] if unchanged and supplied is None else narrative(facts, supplied)
        if unchanged and note == old["narrative"]:
            return old
        data = {**facts, "narrative": note, "templateDigest": template_id,
                "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        data["reportId"] = digest(canonical(data))
        draft = Path(tempfile.mkdtemp(prefix="staging-", dir=home))
        try:
            (draft / JSON).write_text(json.dumps(data, ensure_ascii=False,
                                                 indent=2, allow_nan=False) + "\n", encoding="utf-8")
            (draft / HTML).write_text(render(data, assets), encoding="utf-8")
            verify_pair(draft, assets, scope.feature)
            if collect(scope)["snapshot"]["inputDigest"] != facts["snapshot"]["inputDigest"]:
                raise ValueError("采集期间输入发生变化，请重新生成")
            hashes = {n: digest((draft / n).read_bytes()) for n in (JSON, HTML)}
            (draft / "hashes.json").write_bytes(canonical(hashes))
            os.replace(draft, home / "pending")
            finish_pending(scope.directory, home)
            return verify_pair(scope.directory, assets, scope.feature)
        finally:
            if draft.exists():
                shutil.rmtree(draft)
