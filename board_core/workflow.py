"""Workflow node resolution — checkpoint→node mapping, status derivation."""

from __future__ import annotations

from board_core.contracts import BoardConfigError, artifact_dicts


NEXT_ACTION_FIELDS = ("slashSkill", "userMessage", "dialogTips")
NODE_STATUSES = {
    "not_started",
    "in_progress",
    "done",
    "blocked",
    "skipped",
    "archived",
    "unknown",
}
DEFAULT_NODE_STATUS_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "done": "已完成",
    "blocked": "已阻塞",
    "skipped": "已跳过",
    "archived": "已归档",
    "unknown": "未知",
}
ARTIFACT_TYPES = {
    "file",
    "directory",
    "markdown",
    "text",
    "log",
    "yaml",
    "json",
    "report",
    "external",
    "virtual",
    "unknown",
}
ARTIFACT_STATUSES = {
    "generated",
    "missing",
    "partial",
    "invalid",
    "unknown",
}
DEFAULT_ARTIFACT_STATUS_LABELS = {
    "generated": "已生成",
    "missing": "未生成",
    "partial": "部分生成",
    "invalid": "不可用",
    "unknown": "未知",
}


def extract_checkpoint_suffix(checkpoint: str) -> str | None:
    """Return the state suffix portion of a checkpoint.

    e.g. 'discuss_in_progress' → 'in_progress'
         'verify_done'         → 'done'
         'needs_fix'           → 'needs_fix'
         'archived'            → 'archived'
         'bogus'               → None
    """
    for suffix in ("in_progress", "done"):
        suffix_ = "_" + suffix
        if checkpoint.endswith(suffix_) and len(checkpoint) > len(suffix_):
            return suffix

    if checkpoint == "needs_fix":
        return "needs_fix"
    if checkpoint == "archived":
        return "archived"
    return None


def find_current_node(
    nodes: list[dict], checkpoint: str,
) -> tuple[int, str | None]:
    """Return (index_into_nodes, node_id) for the checkpoint.

    Returns (-1, None) when no match is found.
    """
    for idx, node in enumerate(nodes):
        if checkpoint in node.get("checkpoints", []):
            return idx, node["id"]
    return -1, None


def _normalize_node_status(value: object) -> str:
    if isinstance(value, str) and value in NODE_STATUSES:
        return value
    return "unknown"


def _normalize_artifact_type(value: object) -> str:
    if isinstance(value, str) and value in ARTIFACT_TYPES:
        return value
    return "unknown"


def _normalize_artifact_status(value: object) -> str:
    if isinstance(value, str) and value in ARTIFACT_STATUSES:
        return value
    return "unknown"


def derive_node_status(
    node_idx: int,
    current_idx: int,
    current_checkpoint: str,
    node: dict,
    suffix_states: dict,
) -> str:
    """Return the board node status for a single node based on current position."""
    if current_checkpoint == "archived":
        return "archived" if node["id"] == "ops.archive" else "done"

    if current_checkpoint == "needs_fix":
        return "unknown"

    if node_idx < current_idx:
        return "done"
    if node_idx > current_idx:
        return "not_started"

    suffix = extract_checkpoint_suffix(current_checkpoint)
    state = suffix_states.get(suffix, {"nodeStatus": "unknown"})
    return _normalize_node_status(state.get("nodeStatus"))


def derive_current_node_status(
    checkpoint: str, suffix_states: dict, current_idx: int,
) -> str:
    """Return the current run node status for project summaries."""
    if checkpoint == "needs_fix":
        return "blocked"
    if checkpoint == "archived":
        return "archived"
    if current_idx < 0:
        return "unknown"
    suffix = extract_checkpoint_suffix(checkpoint)
    if suffix is None:
        return "unknown"
    state = suffix_states.get(suffix, {"nodeStatus": "unknown"})
    return _normalize_node_status(state.get("nodeStatus"))


def build_workflow_fallback_states(config: dict) -> list[dict]:
    """Build workflow-level fallback states for statuses not tied to a node."""
    by_status: dict[str, dict] = {
        "unknown": {
            "nodeStatus": "unknown",
            "label": DEFAULT_NODE_STATUS_LABELS["unknown"],
        },
    }
    for state in config.get("checkpointSuffixState", {}).values():
        node_status = _normalize_node_status(state.get("nodeStatus"))
        by_status[node_status] = {
            "nodeStatus": node_status,
            "label": state.get("label", DEFAULT_NODE_STATUS_LABELS[node_status]),
        }
    return list(by_status.values())


def _normalize_next_action(value: object, *, context: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BoardConfigError(f"{context}.nextAction must be an object")

    next_action: dict[str, str] = {}
    for field in NEXT_ACTION_FIELDS:
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise BoardConfigError(f"{context}.nextAction.{field} must be a non-empty string")
        next_action[field] = item
    return next_action


def _normalize_node_states(node: dict) -> list[dict]:
    node_id = node.get("id", "<unknown>")
    states = node.get("states", [])
    if not isinstance(states, list):
        raise BoardConfigError(f"{node_id}.states must be a list")

    clean_states: list[dict] = []
    for index, state in enumerate(states):
        context = f"{node_id}.states[{index}]"
        if not isinstance(state, dict):
            raise BoardConfigError(f"{context} must be an object")
        node_status = _normalize_node_status(state.get("nodeStatus"))
        if node_status == "unknown" and state.get("nodeStatus") != "unknown":
            raise BoardConfigError(f"{context}.nodeStatus must be a supported node status")
        clean_states.append({
            "nodeStatus": node_status,
            "label": state.get("label", DEFAULT_NODE_STATUS_LABELS[node_status]),
            "nextAction": _normalize_next_action(state.get("nextAction"), context=context),
        })
    return clean_states


def _default_artifact_statuses() -> list[dict]:
    return [
        {"artifactStatus": "generated", "label": DEFAULT_ARTIFACT_STATUS_LABELS["generated"]},
        {"artifactStatus": "missing", "label": DEFAULT_ARTIFACT_STATUS_LABELS["missing"]},
        {"artifactStatus": "partial", "label": DEFAULT_ARTIFACT_STATUS_LABELS["partial"]},
        {"artifactStatus": "invalid", "label": DEFAULT_ARTIFACT_STATUS_LABELS["invalid"]},
        {"artifactStatus": "unknown", "label": DEFAULT_ARTIFACT_STATUS_LABELS["unknown"]},
    ]


def _normalize_artifact_statuses(artifact: dict, *, context: str) -> list[dict]:
    statuses = artifact.get("artifactStatuses", _default_artifact_statuses())
    if not isinstance(statuses, list):
        raise BoardConfigError(f"{context}.artifactStatuses must be a list")

    clean_statuses: list[dict] = []
    for index, status in enumerate(statuses):
        status_context = f"{context}.artifactStatuses[{index}]"
        if not isinstance(status, dict):
            raise BoardConfigError(f"{status_context} must be an object")
        artifact_status = _normalize_artifact_status(status.get("artifactStatus"))
        if artifact_status == "unknown" and status.get("artifactStatus") != "unknown":
            raise BoardConfigError(f"{status_context}.artifactStatus must be a supported artifact status")
        clean_statuses.append({
            "artifactStatus": artifact_status,
            "label": status.get("label", DEFAULT_ARTIFACT_STATUS_LABELS[artifact_status]),
        })
    return clean_statuses or _default_artifact_statuses()


def _normalize_artifact_definitions(node: dict) -> list[dict]:
    node_id = node.get("id", "<unknown>")
    clean_artifacts: list[dict] = []
    for index, artifact in enumerate(artifact_dicts(node, "outputs")):
        context = f"{node_id}.artifactDefinitions[{index}]"
        artifact_type = _normalize_artifact_type(artifact.get("artifactType"))
        if artifact_type == "unknown" and artifact.get("artifactType") != "unknown":
            raise BoardConfigError(f"{context}.artifactType must be a supported artifact type")
        clean_artifacts.append({
            "id": artifact.get("id"),
            "label": artifact.get("label", artifact.get("id")),
            "artifactType": artifact_type,
            "required": artifact.get("required", False),
            "artifactStatuses": _normalize_artifact_statuses(artifact, context=context),
        })
    return clean_artifacts


def build_workflow_shell(config: dict) -> dict:
    """Build workflow shell from config — strips internal mapping fields.

    Removes from output:
    - top-level ``id``, ``version``, and ``kind``
    - workflow-level ``checkpoints`` (contract-only checkpoint matrix)
    - workflow-level ``transitions`` (the board currently treats nodes as a linear sequence)
    - node-level ``checkpoints`` (internal checkpoint→node mapping)
    - ``order``, ``skill``, ``artifacts``, and ``validators`` from nodes
    - ``path`` from each output artifact definition
    """
    workflow = {
        k: v
        for k, v in config["workflow"].items()
        if k not in {"id", "version", "kind", "checkpoints", "transitions"}
    }
    clean_nodes: list[dict] = []
    for node in workflow["nodes"]:
        clean = {
            k: v
            for k, v in node.items()
            if k not in {"checkpoints", "order", "skill", "artifacts", "validators"}
        }
        clean["states"] = _normalize_node_states(node)
        clean["artifactDefinitions"] = _normalize_artifact_definitions(node)
        clean_nodes.append(clean)
    workflow["nodes"] = clean_nodes
    return workflow
