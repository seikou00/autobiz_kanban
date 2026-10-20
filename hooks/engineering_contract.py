"""Audit the actual agent, Workflow, policy and Runtime deployment together."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path

FILES = ("agents/test-engineer.md", "hooks/augment_test_engineer_task_prompt.py", "hooks/hooks.json",
         "hooks/utest_policy.py", "hooks/utest_control.py", "hooks/utest_deadline_guard.py",
         "hooks/engineering_context.py", "hooks/engineering_facts.py", "hooks/engineering_cache.py",
         "hooks/engineering_dependencies.py", "hooks/engineering_environment.py", "hooks/runtime_guidance.py",
         "hooks/engineering_contract.py", "hooks/code_execution_guard.py", "hooks/frontend_route_write_guard.py",
         "hooks/utest_test_environment.py", "hooks/utest_workspace_binding.py",
         "hooks/parallel_batch_stage.py", "hooks/parallel_batch_scheduler.py", "hooks/run_utest_command.py",
         "hooks/utest_runtime.py", "hooks/utest_presets.py", "hooks/task_runner.py", "hooks/worktree_manager.py",
         "skills/autodev/autodev-code/SKILL.md", "skills/autodev/autodev-utest/SKILL.md",
         "skills/autodev/autodev-utest/reference/test-engineer-task-protocol.md",
         "agents/reviewer.md", "board_core/board_config.json", "hooks/workflow_launcher.py",
         "hooks/parallel_dispatch.py", "hooks/parallel_runtime.py", "hooks/parallel_integration.py",
         "hooks/parallel_merge_train.py", "hooks/parallel_batch_lifecycle.py", "hooks/parallel_final_verify.py",
         "hooks/parallel_evidence_aggregate.py", "hooks/batch_lease_manager.py", "hooks/query_json_fields.py", "hooks/code_task_context.py",
         "hooks/resolve_frontend_html_route.py", "hooks/utest_assignment_router.py",
         "hooks/inspect_test_environment.py", "hooks/validate_utest_source_bug.py",
         "skills/autodev/autodev-code/references/batch-workflow-guide.md",
         "workflows/code-batched-execution.workflow.js")


def _local_imports(source: bytes, source_path: str) -> set[str]:
    dependencies = set()
    for node in ast.walk(ast.parse(source)):
        modules = []
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package = source_path.removesuffix(".py").split("/")[:-1]
                module = ".".join([*package[:len(package) - node.level + 1], *filter(None, module.split("."))])
            modules = ([module + "." + alias.name for alias in node.names if alias.name != "*"]
                       if module in {"hooks", "board_core"} else [module])
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        dependencies.update(module.replace(".", "/") + ".py" for module in modules
                            if module.startswith(("hooks.", "board_core.")))
    return dependencies


def runtime_contract(root: Path | None = None) -> dict:
    root = root or Path(__file__).resolve().parents[1]
    hashes = {}
    pending = set(FILES)
    valid_python = True
    while pending:
        name = pending.pop()
        if name in hashes:
            continue
        path = root / name
        if not path.is_file():
            hashes[name] = None
            continue
        source = path.read_bytes()
        hashes[name] = hashlib.sha256(source).hexdigest()
        if name.endswith(".py"):
            try:
                pending.update(_local_imports(source, name) - hashes.keys())
            except SyntaxError:
                valid_python = False
    hashes = dict(sorted(hashes.items()))
    return {"version": "autodev.engineering.deployment.v2", "files": hashes,
            "complete": all(hashes.values()) and valid_python,
            "digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}
