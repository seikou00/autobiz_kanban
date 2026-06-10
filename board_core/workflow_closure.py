"""Dependency closure solver for custom workflow node selections."""

from __future__ import annotations

from dataclasses import dataclass

from board_core.workflow_compiler import WorkflowCompileError, _artifact_dicts


@dataclass(frozen=True)
class ClosureResult:
    """Closure outcome over the base node array.

    nodes: closed selection in base array order.
    added: producer nodes pulled in to satisfy required inputs.
    entry_nodes: chain head plus nodes left with externalized required inputs.
    externalized: node id -> required input paths to mark external.
    """

    nodes: tuple[str, ...]
    added: tuple[str, ...]
    entry_nodes: tuple[str, ...]
    externalized: dict[str, tuple[str, ...]]


def _base_nodes(base_config: dict) -> list[dict]:
    workflow = base_config.get("workflow")
    if not isinstance(workflow, dict):
        raise WorkflowCompileError("workflow must be an object")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise WorkflowCompileError("workflow.nodes must be a list")
    return [node for node in nodes if isinstance(node, dict) and isinstance(node.get("id"), str)]


def _producer_index(nodes: list[dict]) -> dict[str, str]:
    producers: dict[str, str] = {}
    for node in nodes:
        for artifact in _artifact_dicts(node, "outputs"):
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                producers.setdefault(artifact["path"], str(node["id"]))
    return producers


def _required_input_paths(node: dict) -> list[str]:
    paths: list[str] = []
    for artifact in _artifact_dicts(node, "inputs"):
        if not isinstance(artifact, dict):
            continue
        path = artifact.get("path")
        if not isinstance(path, str):
            continue
        if artifact.get("required", True) and not artifact.get("external", False):
            paths.append(path)
    return paths


def solve_node_closure(
    base_config: dict,
    selected_node_ids: object,
    *,
    auto_include_producers: bool = True,
) -> ClosureResult:
    """Close a custom node selection over its required-input dependencies.

    With auto_include_producers, picking a node pulls in the producers of its
    required inputs transitively (选了 PRD 就必须选 prd_discuss). Without it, or
    when no base node produces an input, the input is externalized and its node
    becomes an entry point.
    """
    if not isinstance(selected_node_ids, (list, tuple)):
        raise WorkflowCompileError("selected nodes must be a list of node ids")
    requested = [str(node_id).strip() for node_id in selected_node_ids if str(node_id).strip()]
    if not requested:
        raise WorkflowCompileError("node selection must contain at least one node id")

    nodes = _base_nodes(base_config)
    node_order = [str(node["id"]) for node in nodes]
    node_by_id = {str(node["id"]): node for node in nodes}

    unknown = sorted(set(requested) - set(node_order))
    if unknown:
        raise WorkflowCompileError(f"node selection references unknown nodes: {', '.join(unknown)}")

    selected = set(requested)
    added: set[str] = set()
    if auto_include_producers:
        producers = _producer_index(nodes)
        changed = True
        while changed:
            changed = False
            for node_id in sorted(selected):
                for path in _required_input_paths(node_by_id[node_id]):
                    producer = producers.get(path)
                    if producer and producer != node_id and producer not in selected:
                        selected.add(producer)
                        added.add(producer)
                        changed = True

    ordered = [node_id for node_id in node_order if node_id in selected]

    # Anything still unsatisfied after closure gets externalized; its node is an entry.
    available: set[str] = set()
    externalized: dict[str, tuple[str, ...]] = {}
    entry_nodes: list[str] = [ordered[0]] if ordered else []
    for node_id in ordered:
        node = node_by_id[node_id]
        broken = tuple(path for path in _required_input_paths(node) if path not in available)
        if broken:
            externalized[node_id] = broken
            if node_id not in entry_nodes:
                entry_nodes.append(node_id)
        for artifact in _artifact_dicts(node, "outputs"):
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                available.add(artifact["path"])

    return ClosureResult(
        nodes=tuple(ordered),
        added=tuple(node_id for node_id in node_order if node_id in added),
        entry_nodes=tuple(entry_nodes),
        externalized=externalized,
    )
