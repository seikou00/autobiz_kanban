"""Build production Git commit messages for an AutoBiz workflow run.

The production Git hook requires a task-card prefix. A workflow chooses that
card once, persists it in its durable run manifest, and every plugin-owned
commit reads the same value from there.
"""

from __future__ import annotations

import re
from typing import Any


_TASK_CARD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COMMIT_STAGE_LABELS = {"code": "Code", "rework": "Rework", "utest": "UTest"}


class CommitMessageError(ValueError):
    """Raised when a workflow cannot construct a hook-compliant message."""


def normalize_task_card_id(value: object) -> str:
    """Return a safe task-card ID or fail before any Git side effect."""
    card_id = value.strip() if isinstance(value, str) else ""
    if not card_id:
        raise CommitMessageError("parallel_task_card_id_required")
    if not _TASK_CARD_ID_PATTERN.fullmatch(card_id):
        raise CommitMessageError("parallel_task_card_id_invalid")
    return card_id


def build_commit_message(
    task_card_id: object,
    summary: object,
    *,
    stage: str | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> str:
    """Build a marked subject, with Plan-owned task goals only for Code."""
    card_id = normalize_task_card_id(task_card_id)
    normalized_summary = " ".join(str(summary or "").split())
    if not normalized_summary:
        raise CommitMessageError("parallel_commit_summary_required")
    if "\x00" in normalized_summary:
        raise CommitMessageError("parallel_commit_summary_invalid")
    if stage is not None and stage not in _COMMIT_STAGE_LABELS:
        raise CommitMessageError(f"parallel_commit_stage_invalid:{stage}")
    if stage != "code" and tasks is not None:
        raise CommitMessageError("parallel_commit_tasks_only_allowed_for_code")
    label = f"{_COMMIT_STAGE_LABELS[stage]}：" if stage else ""
    subject = f"{card_id} #comment cmbdevcalw提交 {label}{normalized_summary}"
    if stage != "code":
        return subject
    if not tasks:
        raise CommitMessageError("parallel_commit_tasks_required")
    sections: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise CommitMessageError("parallel_commit_task_invalid")
        task_id = task.get("id")
        title = task.get("title")
        goal = task.get("goal")
        if not isinstance(task_id, str) or not re.fullmatch(r"T\d{3}", task_id) or task_id in seen:
            raise CommitMessageError("parallel_commit_task_id_invalid")
        if not isinstance(title, str) or not title.strip() or "\x00" in title:
            raise CommitMessageError(f"parallel_commit_task_title_required:{task_id}")
        if not isinstance(goal, str) or not goal.strip() or "\x00" in goal:
            raise CommitMessageError(f"parallel_commit_task_goal_required:{task_id}")
        seen.add(task_id)
        sections.append(f"TASK {task_id}：{' '.join(title.split())}\n任务目标：{goal.strip()}")
    return subject + "\n\n" + "\n\n".join(sections)
