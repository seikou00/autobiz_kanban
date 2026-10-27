"""Code Batch Review and optional Design review retain resolvable read-only roles."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_SKILL = ROOT / "skills/autodev/autodev-code/SKILL.md"
VERIFICATION_AGENT = ROOT / "agents/verification.md"
REFERENCES = ROOT / "skills/references"


def _read(path):
    return path.read_text(encoding="utf-8")


def _node_subagents(node_id):
    board = json.loads(_read(ROOT / "board_core/board_config.json"))
    return next(node for node in board["workflow"]["nodes"] if node["id"] == node_id)["runtimePolicy"]["subagentConfig"]


def _dev_code_subagents():
    return _node_subagents("dev.code")


class CodeReviewUsesBatchOnlyTest(unittest.TestCase):
    def test_legacy_code_review_is_removed(self) -> None:
        self.assertFalse((REFERENCES / "review-protocol-code.md").exists())
        self.assertFalse((ROOT / "agents" / "code-reviewer.md").exists())
        self.assertFalse((ROOT / "agents" / "code-simplifier.md").exists())
        self.assertIn("每个 Batch `review` 子阶段", _read(CODE_SKILL))

        subagents = _dev_code_subagents()
        self.assertNotIn("agents/code-reviewer.md", subagents["customSubagentFiles"])
        self.assertNotIn("agents/code-simplifier.md", subagents["customSubagentFiles"])
        self.assertNotIn("agents/explore.md", subagents["customSubagentFiles"])


class VerificationRoleIsResolvableTest(unittest.TestCase):
    """Code 阶段的 verification 角色必须可被宿主解析。"""

    def test_agent_name_matches_runner_directive(self) -> None:
        self.assertIn("name: verification-autodev", _read(VERIFICATION_AGENT))

    def test_dev_code_injects_verification_agent(self) -> None:
        subagents = _dev_code_subagents()

        self.assertIn("agents/verification.md", subagents["customSubagentFiles"])
        self.assertIn("verification", subagents["disabledBuiltinSubagents"])


class OptionalDesignReviewTest(unittest.TestCase):
    def test_design_critic_is_available_and_read_only(self):
        files = _node_subagents("dev.design")["customSubagentFiles"]
        agent = ROOT / "agents/critic_autodev_design_zh.md"
        self.assertIn(str(agent.relative_to(ROOT)), files)
        metadata = _read(agent).split("---")[1]
        self.assertIn("name: critic-autodev-design-zh", metadata)
        for tool in ("write_file", "edit_file", "write_todos"):
            self.assertIn(tool, metadata)
