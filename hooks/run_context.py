#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve, persist, validate, and inject the Feature RunContext."""

from __future__ import print_function

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import atomic_write_json  # noqa: E402
from hooks.paths import (  # noqa: E402
    get_plugin_output_workspace,
    resolve_env_feature,
)


SCHEMA_VERSION = "autodev.run-context.v1"
FILE_NAME = "RUN_CONTEXT.json"


def feature_dir(workspace, feature):
    return Path(workspace) / ".autobizdevops" / "features" / feature


def context_path(workspace, feature):
    return feature_dir(workspace, feature) / ".runtime" / FILE_NAME


def _git_root(path):
    try:
        process = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if process.returncode != 0:
        return None
    value = (process.stdout or "").strip()
    return Path(value).resolve() if value else None


def _digest_payload(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve(selected, feature):
    repositories = []
    modules = []
    errors = []
    repository_ids = {}
    seen_modules = set()
    for item in selected:
        module_id = str(item.get("deployUnitId", "") or "").strip()
        raw_path = str(item.get("localRepoPath", "") or "").strip()
        if not module_id or not raw_path:
            errors.append({"code": "SCOPE_UNRESOLVED", "moduleId": module_id or None, "detail": "deployUnitId/localRepoPath 缺失"})
            continue
        module_root = Path(raw_path).expanduser().resolve(strict=False)
        if not module_root.is_dir():
            errors.append({"code": "SCOPE_UNRESOLVED", "moduleId": module_id, "detail": "module root 不存在: {}".format(module_root)})
            continue
        repository_root = _git_root(module_root)
        if repository_root is None:
            errors.append({"code": "SCOPE_UNRESOLVED", "moduleId": module_id, "detail": "无法解析 Git root: {}".format(module_root)})
            continue
        try:
            relative_root = module_root.relative_to(repository_root).as_posix()
        except ValueError:
            errors.append({"code": "SCOPE_UNRESOLVED", "moduleId": module_id, "detail": "module root 不属于 Git root: {}".format(module_root)})
            continue
        repository_key = str(repository_root)
        repository_id = repository_ids.get(repository_key)
        if repository_id is None:
            repository_id = "repo-{:02d}".format(len(repositories) + 1)
            repository_ids[repository_key] = repository_id
            repositories.append({"repositoryId": repository_id, "root": repository_key})
        if module_id in seen_modules:
            errors.append({"code": "SCOPE_UNRESOLVED", "moduleId": module_id, "detail": "deployUnitId 重复"})
            continue
        seen_modules.add(module_id)
        modules.append({
            "moduleId": module_id,
            "repositoryId": repository_id,
            "root": str(module_root),
            "relativeRoot": relative_root or ".",
        })
    stable = {
        "schemaVersion": SCHEMA_VERSION,
        "featureId": feature,
        "status": "SCOPE_UNRESOLVED" if errors or not modules else "ready",
        "repositories": repositories,
        "modules": modules,
        "errors": errors,
    }
    stable["contextDigest"] = _digest_payload(stable)
    return stable


def persist(workspace, feature, selected):
    data = resolve(selected, feature)
    target = context_path(workspace, feature)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, data)
    return data


def load(workspace, feature):
    target = context_path(workspace, feature)
    if not target.is_file():
        raise ValueError("SCOPE_UNRESOLVED: {} 不存在；修复：重新启动 Feature 解析代码库范围。".format(target))
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("SCOPE_UNRESOLVED: RunContext 非法；修复：重新启动 Feature。{}".format(exc))
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("SCOPE_UNRESOLVED: RunContext schema 不匹配；修复：重新启动 Feature。")
    supplied_digest = data.get("contextDigest")
    stable = dict(data)
    stable.pop("contextDigest", None)
    if supplied_digest != _digest_payload(stable):
        raise ValueError("SCOPE_UNRESOLVED: RunContext digest 不匹配；修复：重新启动 Feature。")
    if data.get("status") != "ready":
        raise ValueError("SCOPE_UNRESOLVED: {}".format(json.dumps(data.get("errors", []), ensure_ascii=False)))
    for item in data.get("repositories", []) + data.get("modules", []):
        root = item.get("root") if isinstance(item, dict) else None
        if not isinstance(root, str) or not Path(root).is_dir():
            raise ValueError("SCOPE_UNRESOLVED: RunContext root 已失效: {}；修复：重新启动 Feature。".format(root))
    return data


def _state_summary(workspace, feature):
    path = Path(workspace) / ".autobizdevops" / "state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    features = data.get("features") if isinstance(data, dict) else None
    record = features.get(feature) if isinstance(features, dict) else data
    for key in ("checkpoint", "currentCheckpoint", "status"):
        value = record.get(key) if isinstance(record, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def breadcrumb(workspace, feature):
    capabilities = "unresolved"
    try:
        data = load(workspace, feature)
        repositories = ", ".join(item["root"] for item in data["repositories"])
        modules = ", ".join("{}={}".format(item["moduleId"], item["root"]) for item in data["modules"])
        status = data["status"]
        digest = data["contextDigest"]
        try:
            from hooks.validation_capabilities import load as load_capabilities

            catalog = load_capabilities(feature_dir(workspace, feature), digest)
            capability_items = [
                item for item in catalog.get("capabilities", [])
                if isinstance(item, dict)
            ]
            lane_items = {
                "backend": [
                    item for item in capability_items
                    if not str(item.get("source", "")).startswith("package.json")
                ],
                "frontend": [
                    item for item in capability_items
                    if str(item.get("source", "")).startswith("package.json")
                ],
            }
            samples = []
            for lane in ("backend", "frontend"):
                candidates = sorted(
                    lane_items[lane],
                    key=lambda item: (
                        len(str(item.get("cwd", ".")).split("/")),
                        str(item.get("cwd", ".")),
                        str(item.get("source", "")),
                    ),
                )
                if candidates:
                    item = candidates[0]
                    samples.append("{}={}@{}".format(
                        lane,
                        " ".join(item.get("argv", [])),
                        item.get("cwd", "."),
                    ))
            capabilities = "total={};backend={};frontend={};unavailable={};sample={}".format(
                len(capability_items),
                len(lane_items["backend"]),
                len(lane_items["frontend"]),
                len(catalog.get("unavailable", [])),
                ";".join(samples) or "none",
            )
        except ValueError as exc:
            capabilities = str(exc)
    except ValueError as exc:
        repositories = "unresolved"
        modules = str(exc)
        status = "SCOPE_UNRESOLVED"
        digest = "unresolved"
    return (
        "<AUTODEV_RUNTIME_STATE>\n"
        "Feature: {feature}\nStage: {stage}\nScope: {status}\n"
        "RunContext: {digest}\nRepo roots: {repositories}\nModule roots: {modules}\n"
        "Validation capabilities: {capabilities}\n"
        "Next: resolve the current stage gate; SCOPE_UNRESOLVED blocks planning and execution.\n"
        "</AUTODEV_RUNTIME_STATE>"
    ).format(
        feature=feature,
        stage=_state_summary(workspace, feature),
        status=status,
        digest=digest,
        repositories=repositories,
        modules=modules,
        capabilities=capabilities,
    )


def inject_hook():
    raw = sys.stdin.read()
    if raw.strip():
        try:
            json.loads(raw)
        except ValueError:
            return 0
    try:
        workspace = get_plugin_output_workspace()
        feature = resolve_env_feature(None, required=True)
    except ValueError as exc:
        diagnostic = (
            "<AUTODEV_RUNTIME_STATE>\n"
            "Scope: hook_context_unavailable\n"
            "Detail: {}\n"
            "Next: writer 输出 retryable=false 时立即停止；检查宿主是否注入 "
            "PLUGIN_WORKSPACE/PROJECT_DIR/FEATURE_ID。\n"
            "</AUTODEV_RUNTIME_STATE>"
        ).format(exc)
        print(json.dumps({"additionalContext": diagnostic}, ensure_ascii=False))
        return 0
    print(json.dumps({"additionalContext": breadcrumb(workspace, feature)}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Feature RunContext resolver")
    sub = parser.add_subparsers(dest="command")
    inject = sub.add_parser("inject")
    inject.set_defaults(func=lambda args: inject_hook())
    show = sub.add_parser("show")
    show.add_argument("--workspace")
    show.add_argument("--feature")
    show.set_defaults(func=lambda args: print(json.dumps(load(Path(args.workspace), args.feature), ensure_ascii=False, indent=2)) or 0)
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.error("需要 inject/show 子命令。")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
