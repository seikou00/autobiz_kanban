import argparse
import json
import sys
from pathlib import Path

from hooks.paths import get_plugin_output_workspace, resolve_env_feature
from hooks.json_writer_common import write_text
from .sources import resolve_scope
from .collect import collect
from .publish import publish, validate, runtime, HTML, JSON


def main(action):
    p = argparse.ArgumentParser(description="生成或复核当前 Feature 的离线交付报告")
    p.add_argument("--workspace", type=Path)
    p.add_argument("--feature")
    p.add_argument("--iteration", type=int)
    if action == "collect":
        p.add_argument("--output", action="store_true", help="完整采集快照写入本迭代 .delivery-report/collection.json，标准输出仅返回摘要")
    if action == "render":
        p.add_argument("--narrative", type=Path, help="基于采集快照生成的摘要 JSON")
    args = p.parse_args()
    try:
        scope = resolve_scope(args.workspace or get_plugin_output_workspace(),
                              resolve_env_feature(args.feature, required=not args.feature), args.iteration)
        if action == "collect":
            result = collect(scope)
            if args.output:
                path = runtime(scope.directory) / "collection.json"
                if path.is_symlink():
                    raise ValueError("采集快照路径为符号链接")
                write_text(path, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
                result = {"ok": True, "featureId": scope.feature,
                          "inputDigest": result["snapshot"]["inputDigest"],
                          "status": result["generationStatus"], "snapshotPath": str(path)}
        else:
            note = json.loads(args.narrative.read_text(encoding="utf-8")
                              ) if getattr(args, "narrative", None) else None
            result = publish(scope, note) if action == "render" else validate(scope)
            result = {"ok": True, "reportId": result["reportId"], "status": result["generationStatus"],
                      "html": str(scope.directory / HTML), "json": str(scope.directory / JSON)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
