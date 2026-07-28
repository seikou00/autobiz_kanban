#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan compatible task-validation commands into shared physical executions."""

from __future__ import annotations

import copy
import json
from typing import Any

from hooks.validation_policy import (
    frontend_compile_command_matches_kind,
    maven_test_selectors,
    normalized_argv,
)


def _maven_selector_property(argv: list[str]) -> tuple[str, int] | None:
    matches: list[tuple[str, int]] = []
    for index, token in enumerate(argv):
        lowered = token.lower()
        if lowered.startswith("-dtest="):
            matches.append(("test", index))
        elif lowered.startswith("-dit.test="):
            matches.append(("it.test", index))
    return matches[0] if len(matches) == 1 else None


def _maven_group_key(command: dict[str, Any]) -> tuple[Any, ...] | None:
    argv = normalized_argv(command)
    selectors = maven_test_selectors(command)
    selector_property = _maven_selector_property(argv or [])
    if not argv or not selectors or selector_property is None:
        return None
    property_name, property_index = selector_property
    normalized = list(argv)
    normalized[property_index] = f"-D{property_name}=<selectors>"
    return (
        "maven_test_aggregate",
        command.get("repo"),
        command.get("cwd"),
        command.get("kind"),
        command.get("required"),
        command.get("timeoutSeconds"),
        tuple(normalized),
    )


def _exact_frontend_group_key(command: dict[str, Any]) -> tuple[Any, ...] | None:
    argv = normalized_argv(command)
    if not argv or not frontend_compile_command_matches_kind(command):
        return None
    return (
        "exact_frontend_compile",
        command.get("repo"),
        command.get("cwd"),
        command.get("kind"),
        command.get("required"),
        command.get("timeoutSeconds"),
        tuple(argv),
    )


def _physical_maven_command(
    logical_commands: list[dict[str, Any]],
    group_id: str,
) -> dict[str, Any]:
    command = copy.deepcopy(logical_commands[0]["command"])
    argv = list(normalized_argv(command) or [])
    selector_property = _maven_selector_property(argv)
    if selector_property is None:
        raise ValueError("maven_validation_group_selector_property_missing")
    property_name, property_index = selector_property
    selectors: list[str] = []
    for logical in logical_commands:
        for selector in maven_test_selectors(logical["command"]):
            if selector not in selectors:
                selectors.append(selector)
    argv[property_index] = f"-D{property_name}={','.join(selectors)}"
    command["id"] = group_id
    command["argv"] = argv
    command["covers"] = []
    return command


def plan_validation_groups(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stable physical groups while preserving every logical command."""

    pending: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for task in batch.get("tasks", []):
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            continue
        for command in task.get("validationCommands", []):
            if not isinstance(command, dict) or not isinstance(command.get("id"), str):
                continue
            logical = {
                "taskId": str(task["id"]),
                "commandId": str(command["id"]),
                "command": copy.deepcopy(command),
                "selectors": maven_test_selectors(command),
            }
            key = _maven_group_key(command) or _exact_frontend_group_key(command)
            if key is None:
                key = ("single", str(task["id"]), str(command["id"]))
            pending.append((key, logical))

    grouped: dict[str, list[dict[str, Any]]] = {}
    keys: dict[str, tuple[Any, ...]] = {}
    key_ids: dict[str, str] = {}
    for key, logical in pending:
        serialized = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
        group_id = key_ids.get(serialized)
        if group_id is None:
            group_id = f"TVG-{len(grouped) + 1:03d}"
            key_ids[serialized] = group_id
            keys[group_id] = key
            grouped[group_id] = []
        grouped[group_id].append(logical)

    result: list[dict[str, Any]] = []
    for group_id, logical_commands in grouped.items():
        strategy = str(keys[group_id][0])
        physical_command = (
            _physical_maven_command(logical_commands, group_id)
            if strategy == "maven_test_aggregate"
            else {
                **copy.deepcopy(logical_commands[0]["command"]),
                "id": group_id,
                "covers": [],
            }
        )
        result.append({
            "id": group_id,
            "strategy": strategy,
            "status": "pending",
            "taskIds": list(dict.fromkeys(item["taskId"] for item in logical_commands)),
            "logicalCommands": logical_commands,
            "physicalCommand": physical_command,
            "attempts": [],
        })
    return result


def validation_groups_sha256_payload(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the immutable execution-plan projection used by run integrity."""

    return [
        {
            "id": group.get("id"),
            "strategy": group.get("strategy"),
            "taskIds": group.get("taskIds"),
            "logicalCommands": group.get("logicalCommands"),
            "physicalCommand": group.get("physicalCommand"),
        }
        for group in groups
        if isinstance(group, dict)
    ]
