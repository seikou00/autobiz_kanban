#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover Runtime validation capabilities from canonical module roots."""

from __future__ import print_function

import hashlib
import argparse
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.json_writer_common import atomic_write_json  # noqa: E402


SCHEMA_VERSION = "autodev.validation-capabilities.v1"
FILE_NAME = "VALIDATION_CAPABILITIES.json"
SCRIPT_MARKERS = ("build", "compile", "typecheck", "type-check")
MANIFEST_NAMES = {
    "build.gradle",
    "build.gradle.kts",
    "package.json",
    "pom.xml",
}
IGNORED_DISCOVERY_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".next",
    ".nuxt",
    ".turbo",
    ".vite",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
}
MAX_MANIFEST_DEPTH = 5


def catalog_path(feature_dir):
    return Path(feature_dir) / ".runtime" / FILE_NAME


def _capability_id(module_id, argv, cwd):
    raw = json.dumps([module_id, argv, cwd], ensure_ascii=False, separators=(",", ":"))
    return "CAP-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()


def _append(result, seen, module, argv, cwd, kind, source):
    key = (module["moduleId"], tuple(argv), cwd)
    if key in seen:
        return
    seen.add(key)
    result.append({
        "capabilityId": _capability_id(module["moduleId"], argv, cwd),
        "moduleId": module["moduleId"],
        "repositoryId": module["repositoryId"],
        "argv": argv,
        "cwd": cwd,
        "kind": kind,
        "required": True,
        "source": source,
    })


def _append_unavailable(result, seen, module, cwd, source, executable, candidate_argv=None):
    key = (module["moduleId"], cwd, source, executable)
    if key in seen:
        return
    seen.add(key)
    item = {
        "moduleId": module["moduleId"],
        "repositoryId": module["repositoryId"],
        "cwd": cwd,
        "source": source,
        "requiredExecutable": executable,
        "reason": "executable_not_found",
    }
    if candidate_argv:
        item["candidateArgv"] = candidate_argv
    result.append(item)


def _is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _manifest_roots(module, modules):
    module_root = Path(str(module.get("root", ""))).resolve(strict=False)
    nested_module_roots = {
        Path(str(item.get("root", ""))).resolve(strict=False)
        for item in modules
        if isinstance(item, dict)
        and item is not module
        and str(item.get("root", "")).strip()
    }
    nested_module_roots = {
        root for root in nested_module_roots
        if root != module_root and _is_within(root, module_root)
    }
    result = []
    for current_raw, dirs, files in os.walk(str(module_root)):
        current = Path(current_raw).resolve(strict=False)
        relative = current.relative_to(module_root)
        if len(relative.parts) > MAX_MANIFEST_DEPTH:
            dirs[:] = []
            continue
        if current != module_root and any(
            current == nested or _is_within(current, nested)
            for nested in nested_module_roots
        ):
            dirs[:] = []
            continue
        kept = []
        for name in sorted(dirs):
            child = (current / name).resolve(strict=False)
            if name in IGNORED_DISCOVERY_DIRS:
                continue
            if any(child == nested for nested in nested_module_roots):
                continue
            kept.append(name)
        dirs[:] = kept
        if MANIFEST_NAMES.intersection(files):
            result.append(current)
    return result


def _package_manager(module_root, package):
    declared = package.get("packageManager") if isinstance(package, dict) else None
    if isinstance(declared, str) and declared.strip():
        name = declared.strip().split("@", 1)[0]
        if name in {"bun", "npm", "pnpm", "yarn"}:
            return name
    if (module_root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (module_root / "yarn.lock").is_file():
        return "yarn"
    if (module_root / "bun.lockb").is_file() or (module_root / "bun.lock").is_file():
        return "bun"
    return "npm"


def discover(run_context):
    capabilities = []
    seen = set()
    unavailable = []
    unavailable_seen = set()
    modules = [
        item for item in run_context.get("modules", []) if isinstance(item, dict)
    ]
    repository_roots = {
        str(item.get("repositoryId")): Path(str(item.get("root", ""))).resolve(strict=False)
        for item in run_context.get("repositories", [])
        if isinstance(item, dict) and str(item.get("root", "")).strip()
    }
    for module in modules:
        if not isinstance(module, dict):
            continue
        repository_root = repository_roots.get(str(module.get("repositoryId")))
        for manifest_root in _manifest_roots(module, modules):
            if repository_root is not None and _is_within(manifest_root, repository_root):
                relative = manifest_root.relative_to(repository_root)
                cwd = "." if relative == Path(".") else relative.as_posix()
            else:
                cwd = str(module.get("relativeRoot", ".") or ".")
            package_json = manifest_root / "package.json"
            if package_json.is_file():
                try:
                    package = json.loads(package_json.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    package = {}
                scripts = package.get("scripts") if isinstance(package, dict) else None
                if isinstance(scripts, dict):
                    manager = _package_manager(manifest_root, package)
                    candidate_names = [
                        str(name) for name in sorted(scripts)
                        if any(marker in str(name).lower() for marker in SCRIPT_MARKERS)
                    ]
                    if candidate_names and shutil.which(manager) is None:
                        _append_unavailable(
                            unavailable,
                            unavailable_seen,
                            module,
                            cwd,
                            "package.json",
                            manager,
                            [manager, "run", candidate_names[0]],
                        )
                    else:
                        for name in candidate_names:
                            lowered = name.lower()
                            argv = [manager, str(name)] if manager == "yarn" else [manager, "run", str(name)]
                            kind = "typecheck" if "type" in lowered else "build"
                            _append(capabilities, seen, module, argv, cwd, kind, "package.json#scripts.{}".format(name))
            if (manifest_root / "pom.xml").is_file():
                if (manifest_root / "mvnw").is_file():
                    maven = "./mvnw"
                elif shutil.which("mvn") is not None:
                    maven = "mvn"
                else:
                    maven = None
                if maven is not None:
                    _append(capabilities, seen, module, [maven, "compile"], cwd, "compile", "pom.xml")
                else:
                    _append_unavailable(
                        unavailable,
                        unavailable_seen,
                        module,
                        cwd,
                        "pom.xml",
                        "mvn",
                        ["mvn", "compile"],
                    )
            if (manifest_root / "build.gradle").is_file() or (manifest_root / "build.gradle.kts").is_file():
                if (manifest_root / "gradlew").is_file():
                    executable = "./gradlew"
                elif shutil.which("gradle") is not None:
                    executable = "gradle"
                else:
                    executable = None
                if executable is not None:
                    _append(capabilities, seen, module, [executable, "classes"], cwd, "compile", "build.gradle")
                else:
                    _append_unavailable(
                        unavailable,
                        unavailable_seen,
                        module,
                        cwd,
                        "build.gradle",
                        "gradle",
                        ["gradle", "classes"],
                    )
    stable = {
        "schemaVersion": SCHEMA_VERSION,
        "contextDigest": run_context.get("contextDigest"),
        "capabilities": capabilities,
        "unavailable": unavailable,
    }
    stable["catalogDigest"] = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return stable


def persist(feature_dir, run_context):
    data = discover(run_context)
    path = catalog_path(feature_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)
    return data


def load(feature_dir, context_digest=None):
    path = catalog_path(feature_dir)
    if not path.is_file():
        raise ValueError("VALIDATION_CAPABILITY_UNRESOLVED: capability catalog 不存在；修复：重新启动 Feature。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("VALIDATION_CAPABILITY_UNRESOLVED: catalog 非法；修复：重新启动 Feature。{}".format(exc))
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("VALIDATION_CAPABILITY_UNRESOLVED: catalog schema 不匹配；修复：重新启动 Feature。")
    supplied_digest = data.get("catalogDigest")
    stable = dict(data)
    stable.pop("catalogDigest", None)
    actual_digest = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if supplied_digest != actual_digest:
        raise ValueError("VALIDATION_CAPABILITY_UNRESOLVED: catalog digest 不匹配；修复：重新启动 Feature。")
    if context_digest is not None and data.get("contextDigest") != context_digest:
        raise ValueError("VALIDATION_CAPABILITY_UNRESOLVED: catalog 与 RunContext 不一致；修复：重新启动 Feature。")
    return data


def command_errors(catalog, command, context):
    if not isinstance(command, dict):
        return ["{}.validation_capability_missing".format(context)]
    argv = command.get("argv")
    cwd = command.get("cwd")
    kind = command.get("kind")
    capability_id = command.get("capabilityId")
    if not isinstance(argv, list) or not isinstance(cwd, str):
        return ["{}.validation_capability_missing".format(context)]
    matches = [
        item for item in catalog.get("capabilities", [])
        if isinstance(item, dict)
        and item.get("argv") == argv
        and item.get("cwd") == cwd
        and (capability_id is None or item.get("capabilityId") == capability_id)
        and (kind == item.get("kind") or (kind == "compile" and item.get("kind") in {"build", "compile", "typecheck"}))
    ]
    if not matches:
        return ["{}.validation_capability_unrecognized".format(context)]
    return []


def _matches_lane(item, lane):
    is_frontend = str(item.get("source", "")).startswith("package.json")
    return lane is None or (lane == "frontend" and is_frontend) or (
        lane == "backend" and not is_frontend
    )


def refresh(feature_dir, lane=None):
    feature_path = Path(feature_dir).expanduser().resolve(strict=False)
    try:
        from hooks.run_context import load as load_run_context

        run_context = load_run_context(feature_path.parents[2], feature_path.name)
    except (IndexError, ValueError) as exc:
        print(json.dumps({
            "ok": False,
            "reason": "SCOPE_UNRESOLVED",
            "detail": str(exc),
            "repairSuggestion": "确认 Feature 目录及 RUN_CONTEXT.json 有效后重新执行 refresh。",
        }, ensure_ascii=False, indent=2))
        return 2
    data = persist(feature_path, run_context)
    all_unavailable = data.get("unavailable", [])
    unavailable = [
        item for item in all_unavailable
        if isinstance(item, dict) and _matches_lane(item, lane)
    ]
    executables = sorted({
        str(item.get("requiredExecutable"))
        for item in unavailable
        if isinstance(item, dict) and item.get("requiredExecutable")
    })
    ok = not unavailable
    result = {
        "ok": ok,
        "status": "ready" if ok else "toolchain_unavailable",
        "path": str(catalog_path(feature_path)),
        "catalogDigest": data.get("catalogDigest"),
        "capabilityCount": len(data.get("capabilities", [])),
        "unavailableCount": len(unavailable),
        "catalogUnavailableCount": len(all_unavailable),
        "missingExecutables": executables,
    }
    if unavailable:
        result["repairSuggestion"] = (
            "安装缺失工具 {}，然后重新执行本 refresh 命令。"
        ).format(", ".join(executables))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Runtime validation capability catalog")
    sub = parser.add_subparsers(dest="command")
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--feature-dir", required=True)
    refresh_parser.add_argument("--lane", choices=("backend", "frontend"))
    args = parser.parse_args(argv)
    if args.command == "refresh":
        return refresh(args.feature_dir, lane=args.lane)
    parser.error("需要 refresh 子命令。")


if __name__ == "__main__":
    raise SystemExit(main())
