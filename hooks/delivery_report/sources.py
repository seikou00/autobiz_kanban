"""Read-only inputs and source provenance for one delivery report."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from board_core.state_store import load_state_json_records_result
from board_core.workflow_compiler import load_record_effective_board_config


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def component(value):
    return isinstance(value, str) and value not in {"", ".", ".."} and not re.search(r'[/\\:\x00]', value)


@dataclass
class Scope:
    workspace: Path
    feature: str
    directory: Path
    iteration: str | None
    nodes: list
    historical_workflow_unknown: bool = False


def resolve_scope(workspace, feature, iteration=None):
    workspace = Path(workspace).resolve()
    if not component(feature):
        raise ValueError("无效 Feature")
    state = load_state_json_records_result(workspace)
    record = state.records.get(feature)
    if state.fatal_errors or not isinstance(record, dict):
        raise ValueError("无法确定 Feature 状态：" + feature)
    checkpoint = record.get("checkpoint")
    if iteration is None and checkpoint == "archived":
        iteration = record.get("iteration")
        if not str(iteration).isdigit():
            raise ValueError("归档记录缺少迭代号，请指定 --iteration")
    if iteration is not None:
        if not re.fullmatch(r'[1-9]\d*', str(iteration)):
            raise ValueError("无效归档迭代")
        root = workspace / ".autobizdevops/archive" / f"{feature}-iter{iteration}"
    else:
        if checkpoint not in {"code_done", "delivery_report_in_progress", "delivery_report_done",
                              "cicd_in_progress", "cicd_done"}:
            raise ValueError(f"当前阶段无法生成编码交付报告：{checkpoint}")
        root = workspace / ".autobizdevops/features" / feature
    if not root.is_dir() or root.resolve() != root:
        raise ValueError(f"未找到有效的 Feature 产物目录：{root}")
    historical = iteration is not None and (
        checkpoint != "archived" or str(record.get("iteration")) != str(iteration))
    workflow_record = record
    if historical:
        workflow_record = {"workflowDecisions": {"detail_design_before_code": "enabled"}} if (
            root / "DETAIL_DESIGN.md").is_file() else {}
    nodes = load_record_effective_board_config(workspace=workspace, record=workflow_record)[
        "workflow"]["nodes"]
    selected_iteration = iteration if iteration is not None else record.get("iteration")
    number = str(selected_iteration) if str(selected_iteration).isdigit() else None
    return Scope(workspace, feature, root, number, nodes, historical)


class Inputs:
    def __init__(self, scope):
        self.scope = scope
        self.root = scope.directory
        self.sources = {}
        self.gaps = []
        self.diagnostics = []

    def gap(self, field, reason, *, category="missing", reason_code=None, scope=None,
            metrics=(), files=(), source_refs=(), action=None):
        scope = {"featureId": self.scope.feature, "iteration": self.scope.iteration,
                 **(scope or {"subject": field})}
        code = reason_code or reason
        identity = digest(canonical([category, code, scope]))[:20]
        target = self.gaps if category == "missing" else self.diagnostics
        item = next((item for item in target if item["id"] == identity), None)
        if item is None:
            item = {"id": identity, "field": field, "reason": reason, "reasonCode": code,
                    "category": category, "scope": scope, "affectedMetrics": [],
                    "affectedFiles": [], "sourceRefs": [], "nextAction": action}
            target.append(item)
        for key, values in (("affectedMetrics", metrics), ("affectedFiles", files), ("sourceRefs", source_refs)):
            item[key] = sorted(set(item[key]) | set(values))
        return identity

    def path(self, name):
        p = self.root / name
        if not p.resolve().is_relative_to(self.root) or p.resolve() != p:
            raise ValueError(f"产物路径不属于本迭代：{name}")
        return p

    def referenced(self, name):
        """Rebase paths captured in a previous checkout or before archive."""
        if not isinstance(name, str) or not name:
            return None
        p = PurePosixPath(name.replace("\\", "/"))
        parts = p.parts
        if ".autobizdevops" in parts:
            i = parts.index(".autobizdevops")
            if parts[i + 1:i + 3] == ("features", self.scope.feature):
                p = PurePosixPath(*parts[i + 3:])
            elif parts[i + 1:i + 3] == ("archive", self.root.name):
                p = PurePosixPath(*parts[i + 3:])
            else:
                return None
        if not p.parts or p.is_absolute() or ".." in p.parts or ":" in p.parts[0]:
            return None
        return p.as_posix()

    def binary(self, name):
        try:
            raw = self.path(name).read_bytes()
            self.sources[name] = {"path": name, "sha256": digest(raw)}
            return raw
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            self.gap(name, str(exc))
            return None

    def read(self, name):
        raw = self.binary(name)
        try:
            return raw.decode("utf-8-sig") if raw is not None else None
        except UnicodeDecodeError:
            self.gap(name, "文本编码无法解析")
            return None

    def obj(self, name):
        text = self.read(name)
        if text is None:
            return {}
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("应为 JSON 对象")
            return value
        except ValueError as exc:
            self.gap(name, str(exc))
            return {}


def metric(value, refs=(), reason=None):
    return {"value": value, "displayValue": 0 if value is None else value,
            "status": "missing" if value is None else "partial" if reason else "measured",
            "sourceRefs": sorted(set(refs)), "reason": reason}


def rows(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
