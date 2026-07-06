"""Guard the input missing-handling invariant.

Every input declared anywhere in board_config.json must declare an extract with
a non-empty degrade, so contract consumers always know how to handle the input
when it is missing. The external flag was removed in favor of drop semantics;
no artifact may declare it again.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _board_config() -> dict:
    return json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))


def _iter_nodes(config: dict):
    workflow = config.get("workflow", {})
    for node in workflow.get("nodes", []):
        yield "workflow.nodes", node
    for profile_name, profile in (workflow.get("profiles") or {}).items():
        for node in profile.get("nodes", []) if isinstance(profile, dict) else []:
            yield f"workflow.profiles.{profile_name}.nodes", node
    for stage in workflow.get("dynamicStages", []) or []:
        for node in stage.get("nodes", []) if isinstance(stage, dict) else []:
            yield f"workflow.dynamicStages[{stage.get('id', '?')}].nodes", node


def _iter_artifacts(config: dict):
    for context, node in _iter_nodes(config):
        if not isinstance(node, dict):
            continue
        artifacts = node.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        for direction in ("inputs", "outputs"):
            for artifact in artifacts.get(direction, []) or []:
                if isinstance(artifact, dict):
                    yield f"{context}[{node.get('id', '?')}].{direction}", artifact


class BoardConfigInvariantsTest(unittest.TestCase):
    def test_every_input_declares_extract_with_degrade(self) -> None:
        missing: list[str] = []
        for context, artifact in _iter_artifacts(_board_config()):
            if ".inputs" not in context:
                continue
            extract = artifact.get("extract")
            path = artifact.get("path", "?")
            if not isinstance(extract, dict) or not str(extract.get("degrade", "")).strip():
                missing.append(f"{context}: {path}")
        self.assertEqual(
            missing,
            [],
            "every input must declare extract with a non-empty degrade "
            "(missing-handling completeness): " + ", ".join(missing),
        )

    def test_no_artifact_declares_external_flag(self) -> None:
        offenders = [
            f"{context}: {artifact.get('path', '?')}"
            for context, artifact in _iter_artifacts(_board_config())
            if "external" in artifact
        ]
        self.assertEqual(
            offenders,
            [],
            "the external flag was removed (drop semantics); offending artifacts: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
