"""Reject known unsupported Workflow globals before publishing either copy."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from hooks.workflow_launcher import materialize_workflow_script
from hooks.workflow_sandbox_contract import validate_workflow_source

ROOT = Path(__file__).resolve().parents[1]


class WorkflowSandboxContractTest(unittest.TestCase):
    def test_fixed_source_has_no_ambient_clock_or_random(self):
        source = ROOT / "workflows" / "code-batched-execution.workflow.js"
        validate_workflow_source(source.read_text(encoding="utf-8"), source)

    def test_invalid_template_is_rejected_before_publishing(self):
        for expression in ("Date.now()", "Date . now()", "new Date()", "const clock = Date; clock.now()",
                           "Math.random()", "Math [ 'random' ]()", "performance.now()", "crypto.randomUUID()",
                           "`request-${Date.now()}`"):
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "source.js"
                source.write_text("export const meta = {};\n" + expression)
                workspace = root / "runtime"
                workspace.mkdir()
                with self.assertRaisesRegex(ValueError, "fixed_workflow_sandbox_forbidden"):
                    materialize_workflow_script(source, workspace, "alpha")
                self.assertFalse((workspace / ".cmbdevclaw").exists())

    def test_relaunch_replaces_bad_copy_with_validated_source_and_matching_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = ROOT / "workflows" / "code-batched-execution.workflow.js"
            for name in ("artifact", "mounted"):
                workspace = root / name
                target = workspace / ".cmbdevclaw" / "workflows" / "alpha" / source.name
                target.parent.mkdir(parents=True)
                target.write_text("Date.now(); Math.random();")
                result = materialize_workflow_script(source, workspace, "alpha")
                self.assertEqual(source.read_bytes(), target.read_bytes())
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), result["workflowScriptSha256"])
                validate_workflow_source(target.read_text(), target)

    def test_rejected_source_does_not_overwrite_previously_published_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixed.js"
            source.write_text("export const meta = {}; Math.max(1, 2);")
            result = materialize_workflow_script(source, root, "alpha")
            target = Path(result["workflowScript"])
            before = target.read_bytes()
            source.write_text("export const meta = {}; Date.now();")
            with self.assertRaisesRegex(ValueError, "sandbox_forbidden"):
                materialize_workflow_script(source, root, "alpha")
            self.assertEqual(before, target.read_bytes())
