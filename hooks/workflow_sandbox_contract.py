"""Conservative publication checks for the fixed Workflow sandbox.

This catches unsupported ambient clock/random dependencies before publication;
it does not replace the platform's own parser or execution checks.
"""
from __future__ import annotations

import re
from pathlib import Path

RULES = (
    ("ambient_clock", re.compile(r"\b(?:Date|performance)\b")),
    ("ambient_random", re.compile(r"\bMath\s*(?:\.\s*random\b|\[\s*['\"]random['\"]\s*\])|\bcrypto\b")),
)


def validate_workflow_source(source: str, path: Path) -> None:
    # Deliberately conservative for this repository-owned fixed template:
    # even references in comments/strings must not introduce these globals.
    for reason, pattern in RULES:
        match = pattern.search(source)
        if match:
            line = source.count("\n", 0, match.start()) + 1
            raise ValueError(f"fixed_workflow_sandbox_forbidden:{reason}:{path}:{line}")
