"""Machine-readable CLI contracts generated from the actual argparse parsers.

No help-text cache and no duplicated CLI parameter schema. Catalog entries
describe inputs, not authorization; execution rechecks the current binding.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class RuntimeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(message, file=sys.stderr)
        print(json.dumps({"ok": False, "status": "blocked", "reasonCode": "invalid_arguments",
                          "detail": message, "nextAction": "supply_missing_or_correct_arguments",
                          "argumentSchema": parser_schema(self)}, ensure_ascii=False))
        raise SystemExit(2)


def parser_schema(parser: argparse.ArgumentParser) -> dict:
    arguments, commands = [], {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands = {name: parser_schema(child) for name, child in action.choices.items()}
        elif action.dest != "help":
            arguments.append({"name": action.dest, "flags": action.option_strings,
                              "required": action.required, "choices": list(action.choices) if action.choices is not None else None,
                              "repeatable": isinstance(action, argparse._AppendAction),
                              "nargs": action.nargs})
    return {"arguments": arguments, "commands": commands}


def command_catalog() -> dict:
    # Imports are deferred: these modules also use guidance for their results.
    from hooks import task_runner, batch_lease_manager, worktree_manager, parallel_batch_stage, utest_runtime
    from hooks import engineering_context, parallel_dispatch
    modules = (task_runner, batch_lease_manager, worktree_manager, parallel_batch_stage, utest_runtime, engineering_context, parallel_dispatch)
    return {"schemaVersion": "autodev.runtime.commands.v1", "commands": {
        Path(module.__file__).stem: {"argvPrefix": [sys.executable, "-X", "utf8", str(Path(module.__file__).resolve())],
                                     **parser_schema(module.build_parser() if hasattr(module, "build_parser") else module.parser())}
        for module in modules}}


def bound_operations(ctx: dict, token: str | None) -> list[dict]:
    """Concrete current coordinates plus explicit missing business inputs.

    Token-bearing commands are returned to the caller, never saved in packs.
    Each owner still validates authority when executing the command.
    """
    root = Path(__file__).resolve().parent
    common = ["--workspace", str(ctx["workspace"]), "--feature", ctx["feature"],
              "--run-id", ctx["runId"], "--batch-id", ctx["batchId"]]
    def argv(script, action, args):
        return [sys.executable, "-X", "utf8", str(root / script), action, *args]
    result = []
    if ctx["stage"] in {"code", "rework"}:
        for task in ctx["tasks"]:
            args = ["--workspace", str(ctx["workspace"]), "--feature", ctx["feature"],
                    "--task-id", task["id"], "--code-workspace", str(ctx["root"]),
                    "--parallel-run-id", ctx["runId"], "--workspace-ref", task.get("workspaceRef", "default")]
            if token:
                args += ["--lease-token", token]
            result.append({"action": "inspect_task", "taskId": task["id"], "argv": argv("task_runner.py", "inspect", args)})
            if token and task.get("status") == "todo":
                result.append({"action": "start_task", "taskId": task["id"], "argv": argv("task_runner.py", "start", args)})
            if token and ctx["stage"] == "rework" and task.get("status") in {"implemented", "done"} and task.get("latestImplementationEvidenceId"):
                result.append({"action": "start_task_repair", "taskId": task["id"], "argv": argv("task_runner.py", "start-task-repair", args + ["--prior-evidence-id", task["latestImplementationEvidenceId"]])})
            if token:
                from hooks.engineering_context import read_json
                task_runs = ctx["workspace"] / ".autobizdevops" / "features" / ctx["feature"] / ".task-runs" / task["id"]
                active = [state for path in task_runs.glob("*.json") if
                          (state := read_json(path)).get("parallelRunId") == ctx["runId"] and
                          state.get("status") in {"started", "in_progress", "implementation_recording"}]
                if len(active) == 1:
                    state = active[0]
                    bound = args + ["--run-id", state["runId"]]
                    result.append({"action": "prepare_compile", "taskId": task["id"],
                                   "argv": argv("task_runner.py", "prepare-compile", bound),
                                   "missingArguments": ["--execution-cwd", "-- <production-only compile argv>"],
                                   "requires": "backend_production_sources_ready"})
                    attempt = next((a for a in state.get("compileAttempts", []) if a.get("attemptId") == state.get("latestCompileAttemptId")), None)
                    if attempt and attempt.get("status") != "recorded":
                        result.append({"action": "record_compile", "taskId": task["id"],
                                       "argv": argv("task_runner.py", "record-compile", bound + ["--attempt-id", attempt["attemptId"]]),
                                       "missingArguments": ["--exit-code", "--log-exit-code", "--output-file"],
                                       "requires": "original_Bash_execution_completed; never_repeat_to_repair_registration"})
                    finish_args = bound + (["--repair-mode"] if state.get("repairContext", {}).get("taskRepair") else [])
                    result.append({"action": "finish_implementation", "taskId": task["id"],
                                   "argv": argv("task_runner.py", "finish-implementation", finish_args),
                                   "requires": "current_sources_implemented_and_required_compile_passed"})
    elif ctx["stage"] in {"utest", "fix"} and token:
        for task in ctx["tasks"]:
            test_args = common + ["--batch-worktree", str(ctx["root"]), "--task-id", task["id"], "--owner-token", token]
            result.append({"action": "prepare_test", "taskId": task["id"], "argv": argv("utest_runtime.py", "prepare", test_args)})
            result.append({"action": "preflight_test", "taskId": task["id"], "argv": argv("utest_runtime.py", "preflight", test_args),
                           "missingArguments": ["--test-file", "-- <exact test argv>"],
                           "requires": "assigned_test_assets_ready; reuse_original_target_on_retry"})
            result.append({"action": "preflight_setup", "taskId": task["id"], "argv": argv("utest_runtime.py", "preflight-setup", test_args),
                           "missingArguments": ["-- <exact setup argv>"], "requires": "selected_module_missing_capability"})
    if token:
        result.append({"action": "check_lease", "argv": argv("batch_lease_manager.py", "check", common + ["--owner-token", token, "--require-lease-guard"])})
        result.append({"action": "seal", "argv": argv("worktree_manager.py", "seal", [
            "--artifact-workspace", str(ctx["workspace"]), *common[2:], "--repo", str(ctx["root"]),
            "--owner-token", token, "--purpose", "implementation" if ctx["stage"] in {"utest", "fix"} else "review",
            "--commit-stage", "utest" if ctx["stage"] in {"utest", "fix"} else "rework" if ctx["stage"] == "rework" else "code"],
            ), "missingArguments": ["--commit-summary"], "requires": "all_current_stage_tasks_finalized"})
        result.append({"action": "release_lease", "argv": argv("batch_lease_manager.py", "release", common + ["--owner-token", token]),
                       "missingArguments": ["--final-status"]})
    engineering = common + ["--batch-worktree", str(ctx["root"]), "--stage", ctx["stage"]]
    for task in ctx["tasks"]:
        engineering += ["--task-id", task["id"]]
    result.append({"action": "lookup_fact", "argv": argv("engineering_context.py", "lookup", engineering),
                   "missingArguments": ["--symbol"], "requires": "before_repeated_source_search; read_only_before_TASK_start"})
    if token and ctx["stage"] != "review":
        for action in ("resolve", "query", "lookup-class", "inspect-class"):
            result.append({"action": action, "argv": argv("engineering_context.py", action, engineering + ["--owner-token", token]),
                           "missingArguments": ["--classpath-file", "--class-name", "--module"] if action.endswith("class") else ["--symbol", "--source"],
                           "requires": "exact_missing_sources_only; publish_claimed_semantics_immediately"})
    return result


def task_guidance(args, workspace: Path, feature: str, root: Path, state: dict) -> dict:
    if not getattr(args, "parallel_run_id", None) or not state.get("batchId"):
        return {"nextAction": "follow_task_state", "nextActionArgv": None}
    prefix = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("engineering_context.py")), "prepare",
              "--workspace", str(workspace), "--feature", feature, "--run-id", args.parallel_run_id,
              "--batch-id", state["batchId"], "--batch-worktree", str(root), "--task-id", args.task_id,
              "--stage", "rework" if state.get("repairContext", {}).get("taskRepair") else "code"]
    if args.lease_token:
        prefix += ["--owner-token", args.lease_token]
    return {"nextAction": "read_bound_engineering_context", "nextActionArgv": prefix}
