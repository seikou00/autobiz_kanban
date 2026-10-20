"""Check real Runtime output against the schemas used across the agent boundary."""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hooks.engineering_contract import runtime_contract
from hooks.parallel_batch_scheduler import _workflow_status_view, load_manifest, resume_run, main as scheduler_main
from hooks.parallel_batch_stage import main as stage_main
from hooks.parallel_dispatch import claim, defer, main as dispatch_main
from hooks.worktree_manager import provision_parallel_worktree
from tests.test_parallel_batch_runtime import create_run
from tests.test_task_runner import _workspace, _configure_defer_to_test_stages


ROOT = Path(__file__).resolve().parents[1]


def schema_errors(schema, value, path="$", errors=None):
    errors = [] if errors is None else errors
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    expected = schema.get("type")
    if expected:
        names = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(value, kinds[name]) and not (name in {"integer", "number"} and isinstance(value, bool)) for name in names):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum mismatch")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing required field")
        for key, child in value.items():
            rule = properties.get(key, schema.get("additionalProperties", True))
            if rule is False:
                errors.append(f"{path}.{key}: undeclared field")
            elif isinstance(rule, dict):
                schema_errors(rule, child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            schema_errors(schema.get("items", {}), child, f"{path}[{index}]", errors)
    return errors


class RuntimeSchemaConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = r'''
const fs = require("fs"), vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf("function normalizeStructuredOutput(")).replace("export const meta =", "const meta =") +
  "\nglobalThis.schemas = { SCHEDULER_RESULT_SCHEMA, DISPATCH_RESULT_SCHEMA, WORKTREE_SCHEMA, SINGLE_REPAIR_RESULT_SCHEMA, UTEST_PREPARATION_SCHEMA, REVIEW_VALIDATION_SCHEMA };", context);
process.stdout.write(JSON.stringify(context.schemas));
'''
        result = subprocess.run(["node", "-e", script, str(ROOT / "workflows/code-batched-execution.workflow.js")], capture_output=True, text=True, check=True)
        cls.schemas = json.loads(result.stdout)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace, feature, repo = _workspace(Path(temporary.name))
        _configure_defer_to_test_stages(feature)
        self.created = create_run(self.workspace, "alpha", code_workspaces=[str(repo)], max_parallel=2, timeout_seconds=60)
        self.run = self.created["runId"]

    def assert_schema(self, name, value):
        self.assertEqual([], schema_errors(self.schemas[name], value))

    def test_dispatch_replay_and_defer_outputs_fit_the_actual_schema(self):
        args = (self.workspace, "alpha", self.run, "B001", 1, "initial", "worker")
        owner = claim(*args, request_id="same-request")
        replay = claim(*args, request_id="same-request")
        self.assertTrue(replay["reused"])
        for result in (owner, replay):
            self.assert_schema("DISPATCH_RESULT_SCHEMA", result)
        for _ in range(2):
            self.assert_schema("DISPATCH_RESULT_SCHEMA", defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "worker_interrupted"))

    def test_dispatch_cli_errors_preserve_identity_without_schema_repair(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = dispatch_main(["claim", "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", self.run,
                                  "--batch-id", "B001", "--generation", "1", "--source", "obsolete-source", "--worker", "worker"])
        result = json.loads(output.getvalue())
        self.assertEqual(1, code)
        self.assertEqual("parallel_dispatch_source_invalid", result["reasonCode"])
        self.assert_schema("DISPATCH_RESULT_SCHEMA", result)

    def test_scheduler_outputs_match_the_schema_before_and_after_retry(self):
        self.assert_schema("SCHEDULER_RESULT_SCHEMA", self.created)
        owner = claim(self.workspace, "alpha", self.run, "B001", 1, "initial", "worker")
        defer(self.workspace, "alpha", self.run, "B001", 1, owner["ownerToken"], "worker_interrupted")
        resumed = resume_run(self.workspace, "alpha", self.run)
        self.assert_schema("SCHEDULER_RESULT_SCHEMA", resumed)
        self.assert_schema("SCHEDULER_RESULT_SCHEMA", _workflow_status_view(resumed, load_manifest(self.workspace, "alpha", self.run)))

    def test_worktree_success_and_failure_are_both_valid_transport_results(self):
        self.assert_schema("WORKTREE_SCHEMA", provision_parallel_worktree(self.workspace, "alpha", self.run, "B001"))
        failed = provision_parallel_worktree(self.workspace, "alpha", self.run, "B404")
        self.assertFalse(failed["success"])
        self.assert_schema("WORKTREE_SCHEMA", failed)

    def test_control_command_errors_fit_their_schemas_without_invented_success_fields(self):
        commands = [
            (scheduler_main, "SCHEDULER_RESULT_SCHEMA", ["status", "--workflow-view"]),
            (stage_main, "SINGLE_REPAIR_RESULT_SCHEMA", ["record-single-repair", "--batch-id", "B404", "--failed-stage", "review", "--metadata-json", "{}"]),
            (stage_main, "UTEST_PREPARATION_SCHEMA", ["prepare-test", "--batch-id", "B404"]),
            (stage_main, "REVIEW_VALIDATION_SCHEMA", ["validate-review-result", "--batch-id", "B404"]),
        ]
        for main, name, command in commands:
            with self.subTest(schema=name):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main([*command, "--workspace", str(self.workspace), "--feature", "alpha", "--run-id", "missing-run"])
                value = json.loads(output.getvalue())
                self.assertEqual(1, code)
                self.assertFalse(value["ok"])
                self.assert_schema(name, value)


class DeploymentConsistencyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in runtime_contract()["files"]:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def test_dispatch_recovery_and_transitive_dependency_updates_change_the_fingerprint(self):
        before = runtime_contract(self.root)
        self.assertTrue(before["complete"])
        for name in ("hooks/parallel_dispatch.py", "hooks/parallel_failure.py", "hooks/parallel_runtime.py", "hooks/commit_message.py"):
            with self.subTest(file=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n# deployment changed\n")
                self.assertNotEqual(before["digest"], runtime_contract(self.root)["digest"])
                path.write_bytes(original)

    def test_new_local_imports_are_covered_and_missing_dependencies_fail_closed(self):
        path = self.root / "hooks/parallel_dispatch.py"
        path.write_bytes(path.read_bytes() + b"\nfrom hooks import newly_added_runtime\n")
        missing = runtime_contract(self.root)
        self.assertFalse(missing["complete"])
        self.assertIsNone(missing["files"]["hooks/newly_added_runtime.py"])
        (self.root / "hooks/newly_added_runtime.py").write_text("from . import another_runtime\n", encoding="utf-8")
        self.assertFalse(runtime_contract(self.root)["complete"])
        (self.root / "hooks/another_runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.assertTrue(runtime_contract(self.root)["complete"])

    def test_every_fixed_workflow_script_is_in_the_deployment_contract(self):
        source = (self.root / "workflows/code-batched-execution.workflow.js").read_text(encoding="utf-8")
        entrypoints = {"hooks/" + name for name in re.findall(r"\b([a-z_]+\.py)\b", source)
                       if (ROOT / "hooks" / name).is_file()}
        self.assertEqual(set(), entrypoints - runtime_contract(self.root)["files"].keys())

    def test_invalid_python_dependency_fails_the_contract(self):
        (self.root / "hooks/parallel_failure.py").write_text("def broken(:\n", encoding="utf-8")
        self.assertFalse(runtime_contract(self.root)["complete"])


class SkillReferenceConsistencyTest(unittest.TestCase):
    def test_explicit_plugin_script_and_reference_paths_exist(self):
        missing = []
        for base in (ROOT / "skills", ROOT / "agents"):
            for path in base.rglob("*.md"):
                text = path.read_text(encoding="utf-8")
                for match in re.finditer(r"\$\{pluginPath\}/([A-Za-z0-9_./-]+\.(?:py|md|json))\b", text):
                    if not (ROOT / match.group(1)).exists():
                        missing.append(f"{path.relative_to(ROOT)}: {match.group(1)}")
        self.assertEqual([], missing)
