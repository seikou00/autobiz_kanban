"""Command-scoped Git compatibility without changing the user's Git config."""

from __future__ import annotations

import os
import sys


def git_command(*args: str) -> list[str]:
    """Build plugin Git argv with LFS conversion bypassed by default.

    Set AUTOBIZ_GIT_LFS_BYPASS=0 to restore normal LFS processing for runs
    that need LFS objects. Only command-scoped settings are overridden.
    """
    command = ["git"]
    if sys.platform == "win32":
        command.extend(["-c", "core.longpaths=true"])
    if os.environ.get("AUTOBIZ_GIT_LFS_BYPASS", "1").strip().lower() not in {"0", "false", "no", "off"}:
        for setting in (
            "filter.lfs.process=",
            "filter.lfs.smudge=",
            "filter.lfs.clean=",
            "filter.lfs.required=false",
        ):
            command.extend(["-c", setting])
    return [*command, *args]
