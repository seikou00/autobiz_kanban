"""Audit the actual agent, Workflow, policy and Runtime deployment together."""
from __future__ import annotations
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
         "workflows/code-batched-execution.workflow.js")


def runtime_contract(root: Path | None = None) -> dict:
    root = root or Path(__file__).resolve().parents[1]
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() if (root / name).is_file() else None
              for name in FILES}
    return {"version": "autodev.engineering.deployment.v2", "files": hashes,
            "complete": all(hashes.values()),
            "digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}
