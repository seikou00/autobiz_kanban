"""Workflow node resolution — checkpoint→node mapping, status derivation."""

from __future__ import annotations

from board_core.contracts import artifact_dicts


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


def derive_node_state_id(
    node_idx: int,
    current_idx: int,
    current_checkpoint: str,
    node: dict,
    suffix_states: dict,
) -> str:
    """Return the workflow state id for a single node based on current position."""
    if current_checkpoint == "archived":
        return "archived" if node["id"] == "ops.archive" else "done"

    if current_checkpoint == "needs_fix":
        return "unknown"

    if node_idx < current_idx:
        return "done"
    if node_idx > current_idx:
        return "not_started"

    suffix = extract_checkpoint_suffix(current_checkpoint)
    state = suffix_states.get(suffix, {"id": "unknown"})
    return state.get("id") or "unknown"


def build_overall_status(
    checkpoint: str, suffix_states: dict, current_idx: int,
) -> dict:
    """Derive a high-level overallStatus for the run."""
    if checkpoint == "needs_fix":
        return {"id": "blocked", "label": "已阻断", "uiKind": "blocked"}
    if checkpoint == "archived":
        return {"id": "archived", "label": "已归档", "uiKind": "archived"}
    if current_idx < 0:
        return {"id": "unknown", "label": "未知", "uiKind": "unknown"}
    suffix = extract_checkpoint_suffix(checkpoint)
    if suffix is None:
        return {"id": "unknown", "label": "未知", "uiKind": "unknown"}
    state = suffix_states.get(suffix, {"id": "unknown", "label": "未知", "uiKind": "unknown"})
    return {"id": state["id"], "label": state["label"], "uiKind": state["uiKind"]}


def build_workflow_shell(config: dict) -> dict:
    """Build workflow shell from config — strips internal mapping fields.

    Removes from output:
    - top-level ``id``, ``version``, and ``kind``
    - workflow-level ``checkpoints`` (contract-only checkpoint matrix)
    - node-level ``checkpoints`` (internal checkpoint→node mapping)
    - ``order``, ``skill``, ``artifacts``, and ``validators`` from nodes
    - ``path`` from each output artifact definition
    """
    workflow = {
        k: v
        for k, v in config["workflow"].items()
        if k not in {"id", "version", "kind", "checkpoints"}
    }
    clean_nodes: list[dict] = []
    for node in workflow["nodes"]:
        clean = {
            k: v
            for k, v in node.items()
            if k not in {"checkpoints", "order", "skill", "artifacts", "validators"}
        }
        clean["artifactDefinitions"] = [
            {k: v for k, v in art.items() if k != "path"}
            for art in artifact_dicts(node, "outputs")
        ]
        clean_nodes.append(clean)
    workflow["nodes"] = clean_nodes
    return workflow
