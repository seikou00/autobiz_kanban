"""Effective workflow compiler for base config plus profile overlays."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Iterable


BASE_WORKFLOW_PROFILE = "standard"
LEGACY_BASE_WORKFLOW_PROFILE = "base"
DEFAULT_ENABLED_DYNAMIC_PHASES = frozenset({"Biz", "Dev"})
ALLOWED_PHASES = frozenset({"Biz", "Dev", "Ops"})
ALLOWED_GUARDS = frozenset({"code_compile"})
OVERLAY_RELATIVE_DIR = Path(".autobizdevops") / "workflow.d"
PLUGIN_OVERLAY_DIR_NAME = "workflow.d"
NEXT_ACTION_FIELDS = ("slashSkill", "userMessage", "dialogTips")


class WorkflowCompileError(Exception):
    """Raised when workflow overlays cannot be compiled safely."""


def repo_root_for_config_path(config_path: Path) -> Path:
    return config_path.resolve().parent.parent


def default_config_path() -> Path:
    return Path(__file__).resolve().with_name("board_config.json")


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowCompileError(f"invalid workflow JSON: {path}:{exc.lineno}:{exc.colno}") from exc
    if not isinstance(payload, dict):
        raise WorkflowCompileError(f"workflow JSON must be an object: {path}")
    return payload


def overlay_paths(repo_root: Path, workspace: Path | None) -> list[Path]:
    paths: list[Path] = []
    plugin_dir = repo_root / "board_core" / PLUGIN_OVERLAY_DIR_NAME
    if plugin_dir.is_dir():
        paths.extend(sorted(plugin_dir.glob("*.json")))
    if workspace is not None:
        workspace_dir = workspace.resolve() / OVERLAY_RELATIVE_DIR
        if workspace_dir.is_dir():
            paths.extend(sorted(workspace_dir.glob("*.json")))
    return paths


def _overlay_for_profile(payload: dict, path: Path, profile: str) -> dict | None:
    profiles = payload.get("profiles")
    if isinstance(profiles, dict):
        selected = profiles.get(profile)
        if selected is None:
            return None
        if not isinstance(selected, dict):
            raise WorkflowCompileError(f"workflow profile must be an object: {path}:{profile}")
        return selected

    declared_profile = payload.get("profile")
    if declared_profile is None:
        return payload if path.stem == profile else None
    if declared_profile == profile:
        return payload
    return None


def normalize_workflow_profile(profile: str | None) -> str:
    if profile is None:
        return BASE_WORKFLOW_PROFILE
    cleaned = str(profile).strip()
    if not cleaned or cleaned == LEGACY_BASE_WORKFLOW_PROFILE:
        return BASE_WORKFLOW_PROFILE
    return cleaned


def _configured_profile_overlay(base_config: dict, profile: str) -> dict | None:
    workflow = base_config.get("workflow")
    if not isinstance(workflow, dict):
        return None
    profiles = workflow.get("profiles")
    if not isinstance(profiles, dict):
        return None
    selected = profiles.get(profile)
    if selected is None:
        return None
    if not isinstance(selected, dict):
        raise WorkflowCompileError(f"workflow.profiles.{profile} must be an object")
    if profile == BASE_WORKFLOW_PROFILE and not selected.get("nodes"):
        return None
    return selected


def configured_profile_names(base_config: dict) -> tuple[str, ...]:
    workflow = base_config.get("workflow")
    if not isinstance(workflow, dict):
        return (BASE_WORKFLOW_PROFILE,)
    profiles = workflow.get("profiles")
    if not isinstance(profiles, dict):
        return (BASE_WORKFLOW_PROFILE,)
    names = [
        name
        for name in profiles
        if isinstance(name, str) and name.strip()
    ]
    if BASE_WORKFLOW_PROFILE not in names:
        names.insert(0, BASE_WORKFLOW_PROFILE)
    return tuple(dict.fromkeys(names))


def configured_profile_options(base_config: dict) -> list[dict[str, str]]:
    workflow = base_config.get("workflow")
    profiles = workflow.get("profiles", {}) if isinstance(workflow, dict) else {}
    profiles = profiles if isinstance(profiles, dict) else {}
    result: list[dict[str, str]] = []
    for profile in configured_profile_names(base_config):
        raw = profiles.get(profile, {})
        raw = raw if isinstance(raw, dict) else {}
        label = raw.get("label", profile)
        description = raw.get("description", "")
        result.append({
            "id": profile,
            "label": label if isinstance(label, str) and label.strip() else profile,
            "description": description if isinstance(description, str) else "",
        })
    return result


def load_profile_overlays(repo_root: Path, workspace: Path | None, profile: str) -> list[dict]:
    profile = normalize_workflow_profile(profile)
    if profile == BASE_WORKFLOW_PROFILE:
        return []

    selected: list[dict] = []
    for path in overlay_paths(repo_root, workspace):
        overlay = _overlay_for_profile(read_json(path), path, profile)
        if overlay is not None:
            selected.append(overlay)
    return selected


def _read_string(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowCompileError(f"{context} must be a non-empty string")
    return value.strip()


def _read_string_list(value: object, *, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise WorkflowCompileError(f"{context} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _artifact_dicts(node: dict, direction: str) -> list[dict]:
    artifacts = node.get("artifacts")
    if isinstance(artifacts, dict):
        value = artifacts.get(direction, [])
        return value if isinstance(value, list) else []
    if direction == "outputs":
        legacy = node.get("artifactDefinitions", [])
        return legacy if isinstance(legacy, list) else []
    return []


def _phase_for_node(node: dict, *, context: str) -> str:
    phase = node.get("phase", node.get("group"))
    if not isinstance(phase, str) or phase not in ALLOWED_PHASES:
        allowed = ", ".join(sorted(ALLOWED_PHASES))
        raise WorkflowCompileError(f"{context}.phase must be one of: {allowed}")
    return phase


def _find_skill_file(repo_root: Path, skill: str) -> Path | None:
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return None
    for path in skills_dir.rglob("SKILL.md"):
        if path.parent.name == skill:
            return path
    return None


def _validate_skill(repo_root: Path | None, skill: str, *, context: str) -> None:
    if repo_root is None:
        return
    if _find_skill_file(repo_root, skill) is None:
        raise WorkflowCompileError(f"{context}.skill is not installed: {skill}")


def _build_next_action(skill: str, label: str) -> dict[str, str]:
    return {
        "slashSkill": skill,
        "userMessage": f"请使用 /{skill} 继续推进当前 Feature。",
        "dialogTips": f"当前阶段：{label}。",
    }


def _read_next_action_override(value: object, *, context: str) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowCompileError(f"{context}.nextAction must be an object")

    result: dict[str, dict[str, str]] = {}
    for state_id, action in value.items():
        if state_id not in {"not_started", "in_progress", "done", "archived"}:
            raise WorkflowCompileError(f"{context}.nextAction contains unknown state: {state_id}")
        if not isinstance(action, dict):
            raise WorkflowCompileError(f"{context}.nextAction.{state_id} must be an object")
        normalized: dict[str, str] = {}
        for field in NEXT_ACTION_FIELDS:
            item = action.get(field)
            if not isinstance(item, str) or not item.strip():
                raise WorkflowCompileError(
                    f"{context}.nextAction.{state_id}.{field} must be a non-empty string"
                )
            normalized[field] = item
        result[state_id] = normalized
    return result


def _build_dynamic_node(spec: dict, *, repo_root: Path | None, context: str) -> dict:
    if not isinstance(spec, dict):
        raise WorkflowCompileError(f"{context} must be an object")

    node_id = _read_string(spec.get("id"), context=f"{context}.id")
    label = _read_string(spec.get("label"), context=f"{context}.label")
    phase = _phase_for_node(spec, context=context)
    skill = _read_string(spec.get("skill"), context=f"{context}.skill")
    checkpoint_prefix = _read_string(spec.get("checkpointPrefix"), context=f"{context}.checkpointPrefix")
    if checkpoint_prefix.endswith("_in_progress") or checkpoint_prefix.endswith("_done"):
        raise WorkflowCompileError(f"{context}.checkpointPrefix must not include a checkpoint suffix")

    _validate_skill(repo_root, skill, context=context)
    guards = tuple(_read_string_list(spec.get("guards"), context=f"{context}.guards"))
    unknown_guards = sorted(set(guards) - ALLOWED_GUARDS)
    if unknown_guards:
        raise WorkflowCompileError(f"{context}.guards contains unknown guard: {', '.join(unknown_guards)}")

    artifacts = spec.get("artifacts", {})
    if artifacts is None:
        artifacts = {}
    if not isinstance(artifacts, dict):
        raise WorkflowCompileError(f"{context}.artifacts must be an object")

    return {
        "id": node_id,
        "label": label,
        "group": phase,
        "skill": skill,
        "description": spec.get("description", label),
        "checkpoints": [f"{checkpoint_prefix}_in_progress", f"{checkpoint_prefix}_done"],
        "artifacts": {
            "inputs": copy.deepcopy(artifacts.get("inputs", [])),
            "outputs": copy.deepcopy(artifacts.get("outputs", [])),
        },
        "validators": _read_string_list(spec.get("validators"), context=f"{context}.validators"),
        "guards": list(guards),
        "_dynamic": True,
        "_nextActionOverride": _read_next_action_override(spec.get("nextAction"), context=context),
    }


def _insert_dynamic_node(nodes: list[dict], node: dict, spec: dict, *, context: str) -> None:
    insert_after = spec.get("insertAfter")
    insert_before = spec.get("insertBefore")
    if bool(insert_after) == bool(insert_before):
        raise WorkflowCompileError(f"{context} must declare exactly one of insertAfter or insertBefore")

    anchor_id = _read_string(insert_after or insert_before, context=f"{context}.insertAnchor")
    for index, existing in enumerate(nodes):
        if existing.get("id") == anchor_id:
            nodes.insert(index + 1 if insert_after else index, node)
            return
    raise WorkflowCompileError(f"{context} references unknown insert anchor: {anchor_id}")


def _state_template(node: dict, state_id: str) -> dict:
    for state in node.get("states", []):
        if isinstance(state, dict) and state.get("id") == state_id:
            return {k: copy.deepcopy(v) for k, v in state.items() if k != "nextAction"}
    defaults = {
        "not_started": {"id": "not_started", "label": "未开始", "uiKind": "pending"},
        "in_progress": {"id": "in_progress", "label": "进行中", "uiKind": "active"},
        "done": {"id": "done", "label": "已完成", "uiKind": "done"},
        "archived": {"id": "archived", "label": "已归档", "uiKind": "archived"},
    }
    return copy.deepcopy(defaults[state_id])


def _next_node_skill(nodes: list[dict], index: int) -> tuple[str, str]:
    for next_node in nodes[index + 1 :]:
        skill = next_node.get("skill")
        label = next_node.get("label")
        if isinstance(skill, str) and skill and isinstance(label, str):
            return skill, label
    skill = nodes[index].get("skill", "")
    label = nodes[index].get("label", "")
    return str(skill), str(label)


def _derive_node_states(nodes: list[dict]) -> None:
    for index, node in enumerate(nodes):
        skill = str(node.get("skill", ""))
        label = str(node.get("label", ""))
        overrides = node.pop("_nextActionOverride", {})
        checkpoints = node.get("checkpoints", [])
        if checkpoints == ["archived"]:
            archived = _state_template(node, "archived")
            archived["nextAction"] = overrides.get("archived") or {
                "slashSkill": skill,
                "userMessage": f"请使用 /{skill} 查看当前 Feature 的归档状态。",
                "dialogTips": f"当前阶段：{label}。",
            }
            node["states"] = [archived]
            continue

        next_skill, next_label = _next_node_skill(nodes, index)
        states = []
        for state_id, target_skill, target_label in (
            ("not_started", skill, label),
            ("in_progress", skill, label),
            ("done", next_skill, next_label),
        ):
            state = _state_template(node, state_id)
            state["nextAction"] = overrides.get(state_id) or _build_next_action(target_skill, target_label)
            states.append(state)
        node["states"] = states


def _start_checkpoint(node: dict) -> str | None:
    checkpoints = node.get("checkpoints", [])
    if not isinstance(checkpoints, list):
        return None
    for checkpoint in checkpoints:
        if isinstance(checkpoint, str) and checkpoint.endswith("_in_progress"):
            return checkpoint
    if checkpoints == ["archived"]:
        return "archived"
    return None


def _done_checkpoint(node: dict) -> str | None:
    checkpoints = node.get("checkpoints", [])
    if not isinstance(checkpoints, list):
        return None
    for checkpoint in checkpoints:
        if isinstance(checkpoint, str) and checkpoint.endswith("_done"):
            return checkpoint
    if checkpoints == ["archived"]:
        return "archived"
    return None


def _derive_stage_labels(nodes: list[dict], base_labels: dict[str, str]) -> dict[str, str]:
    labels = dict(base_labels)
    for node in nodes:
        node_label = str(node.get("label", ""))
        group = str(node.get("group", ""))
        start = _start_checkpoint(node)
        done = _done_checkpoint(node)
        if start and start != "archived":
            labels.setdefault(start, f"{group} / {node_label}" if group else node_label)
        if done:
            labels.setdefault(done, "已归档" if done == "archived" else f"{node_label} 完成")
    return labels


def _derive_checkpoint_transitions(nodes: list[dict], base_checkpoint_config: dict) -> dict[str, list[str]]:
    old_transitions = base_checkpoint_config.get("transitions", {})
    old_transitions = old_transitions if isinstance(old_transitions, dict) else {}
    transitions: dict[str, list[str]] = {}

    for node in nodes:
        start = _start_checkpoint(node)
        done = _done_checkpoint(node)
        if start and done and start != done:
            targets = [done]
            if "needs_fix" in old_transitions.get(start, []):
                targets.append("needs_fix")
            transitions[start] = targets
        elif start:
            transitions[start] = []

    for index, node in enumerate(nodes):
        done = _done_checkpoint(node)
        if not done or done == "archived":
            continue
        next_start = _start_checkpoint(nodes[index + 1]) if index + 1 < len(nodes) else None
        transitions[done] = [next_start] if next_start else []

    old_needs_fix_targets = old_transitions.get("needs_fix", [])
    if isinstance(old_needs_fix_targets, list):
        known_starts = {item for item in (_start_checkpoint(node) for node in nodes) if item}
        dynamic_starts = {
            start
            for node in nodes
            if node.get("_dynamic") and (start := _start_checkpoint(node)) and start != "archived"
        }
        transitions["needs_fix"] = sorted((set(old_needs_fix_targets) & known_starts) | dynamic_starts)
    if "archived" in {checkpoint for node in nodes for checkpoint in node.get("checkpoints", [])}:
        transitions.setdefault("archived", [])
    return transitions


def _derive_initial_checkpoints(nodes: list[dict], base_checkpoint_config: dict) -> list[str]:
    known = {checkpoint for node in nodes for checkpoint in node.get("checkpoints", [])}
    initial = [
        checkpoint
        for checkpoint in base_checkpoint_config.get("initial", [])
        if isinstance(checkpoint, str) and checkpoint in known
    ]
    first_start = _start_checkpoint(nodes[0]) if nodes else None
    if first_start and first_start != "archived" and first_start not in initial:
        initial.insert(0, first_start)
    return initial


def _derive_ui_transitions(nodes: list[dict]) -> list[dict]:
    transitions: list[dict] = []
    for index, node in enumerate(nodes[:-1]):
        next_node = nodes[index + 1]
        from_state = "archived" if _done_checkpoint(node) == "archived" else "done"
        to_state = "archived" if _start_checkpoint(next_node) == "archived" else "in_progress"
        transitions.append(
            {
                "id": f"{node['id']}-to-{next_node['id']}",
                "from": {"nodeId": node["id"], "state": from_state},
                "to": {"nodeId": next_node["id"], "state": to_state},
            }
        )
    return transitions


def _validate_unique_nodes_and_checkpoints(nodes: list[dict]) -> None:
    seen_nodes: set[str] = set()
    seen_checkpoints: dict[str, str] = {}
    for node in nodes:
        node_id = str(node.get("id", ""))
        if node_id in seen_nodes:
            raise WorkflowCompileError(f"duplicate workflow node id: {node_id}")
        seen_nodes.add(node_id)
        for checkpoint in node.get("checkpoints", []):
            if checkpoint in seen_checkpoints:
                raise WorkflowCompileError(
                    f"checkpoint {checkpoint} is declared by both {seen_checkpoints[checkpoint]} and {node_id}"
                )
            seen_checkpoints[checkpoint] = node_id


def _validate_artifact_dependencies(nodes: list[dict]) -> None:
    available: set[str] = set()
    output_owner: dict[str, str] = {}
    for node in nodes:
        node_id = str(node.get("id", ""))
        input_paths = set()
        for artifact in _artifact_dicts(node, "inputs"):
            if not isinstance(artifact, dict):
                continue
            path = artifact.get("path")
            if not isinstance(path, str):
                continue
            input_paths.add(path)
            if artifact.get("required", True) and not artifact.get("external", False) and path not in available:
                raise WorkflowCompileError(f"{node_id} required input is not produced upstream: {path}")

        for artifact in _artifact_dicts(node, "outputs"):
            if not isinstance(artifact, dict):
                continue
            path = artifact.get("path")
            if not isinstance(path, str):
                continue
            previous = output_owner.get(path)
            if previous is not None and path not in input_paths:
                raise WorkflowCompileError(f"{node_id} output duplicates {previous} without update input: {path}")
            output_owner[path] = node_id
            available.add(path)


def _renumber_nodes(nodes: list[dict]) -> None:
    for index, node in enumerate(nodes, start=1):
        node["order"] = index * 10


def _enabled_phases(overlays: Iterable[dict]) -> set[str]:
    enabled = set(DEFAULT_ENABLED_DYNAMIC_PHASES)
    for overlay in overlays:
        declared = overlay.get("enabledDynamicPhases")
        if declared is None:
            continue
        phases = set(_read_string_list(declared, context="enabledDynamicPhases"))
        unknown = sorted(phases - ALLOWED_PHASES)
        if unknown:
            raise WorkflowCompileError(f"enabledDynamicPhases contains unknown phase: {', '.join(unknown)}")
        enabled.update(phases)
    return enabled


def compile_board_config(
    base_config: dict,
    *,
    repo_root: Path | None = None,
    workspace: Path | None = None,
    profile: str = BASE_WORKFLOW_PROFILE,
    overlays: list[dict] | None = None,
) -> dict:
    """Compile a profile-specific effective workflow config."""
    profile = normalize_workflow_profile(profile)
    effective = copy.deepcopy(base_config)
    loaded_overlays = overlays
    if loaded_overlays is None:
        root = repo_root
        if root is None:
            root = repo_root_for_config_path(default_config_path())
        loaded_overlays = []
        configured_overlay = _configured_profile_overlay(base_config, profile)
        if configured_overlay is not None:
            loaded_overlays.append(configured_overlay)
        loaded_overlays.extend(load_profile_overlays(root, workspace, profile))

    if not loaded_overlays:
        effective["workflowProfile"] = profile
        return effective

    workflow = effective.get("workflow")
    if not isinstance(workflow, dict):
        raise WorkflowCompileError("workflow must be an object")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise WorkflowCompileError("workflow.nodes must be a list")
    nodes = copy.deepcopy(nodes)

    enabled_phases = _enabled_phases(loaded_overlays)
    for overlay_index, overlay in enumerate(loaded_overlays):
        overlay_nodes = overlay.get("nodes", [])
        if not isinstance(overlay_nodes, list):
            raise WorkflowCompileError(f"overlay[{overlay_index}].nodes must be a list")
        for node_index, spec in enumerate(overlay_nodes):
            context = f"overlay[{overlay_index}].nodes[{node_index}]"
            dynamic_node = _build_dynamic_node(spec, repo_root=repo_root, context=context)
            phase = dynamic_node["group"]
            if phase not in enabled_phases:
                raise WorkflowCompileError(
                    f"{context}.phase {phase} is supported by the compiler but disabled by phase policy"
                )
            _insert_dynamic_node(nodes, dynamic_node, spec, context=context)

    _validate_unique_nodes_and_checkpoints(nodes)
    _validate_artifact_dependencies(nodes)
    _renumber_nodes(nodes)
    _derive_node_states(nodes)

    checkpoint_config = workflow.get("checkpoints", {})
    checkpoint_config = checkpoint_config if isinstance(checkpoint_config, dict) else {}
    base_labels = checkpoint_config.get("stageLabels", {})
    base_labels = base_labels if isinstance(base_labels, dict) else {}
    workflow["nodes"] = nodes
    workflow["checkpoints"] = {
        "initial": _derive_initial_checkpoints(nodes, checkpoint_config),
        "transitions": _derive_checkpoint_transitions(nodes, checkpoint_config),
        "stageLabels": _derive_stage_labels(nodes, base_labels),
    }
    workflow["transitions"] = _derive_ui_transitions(nodes)
    effective["workflowProfile"] = profile
    return effective


def load_effective_board_config(
    config_path: Path | None = None,
    *,
    repo_root: Path | None = None,
    workspace: Path | None = None,
    profile: str = BASE_WORKFLOW_PROFILE,
    overlays: list[dict] | None = None,
) -> dict:
    path = config_path or default_config_path()
    base_config = read_json(path)
    resolved_repo_root = repo_root or repo_root_for_config_path(path)
    return compile_board_config(
        base_config,
        repo_root=resolved_repo_root,
        workspace=workspace,
        profile=profile,
        overlays=overlays,
    )
