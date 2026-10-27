#!/usr/bin/env python3
"""Bounded UTest preparation, test-only POM writes and durable execution plans.

Tests are launched by the generated Bash script, never by this Python process.
The runtime supervises the Bash process group and records its real output via
run_utest_command.py. All state is bound to a live Batch lease and Plan task.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import sys
import tempfile
import time
import zipfile
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.inspect_test_environment import inspect_environment, _detected_framework
from hooks.runtime_guidance import RuntimeArgumentParser
from hooks.utest_policy import stage_budget, deadline_reason
from hooks.json_writer_common import atomic_write_json, resolve_feature, resolve_workspace
from hooks.parallel_runtime import check_lease, load_manifest, run_dir, run_lock
from hooks.utest_plan_contract import command_executes_tests, load_utest_plan
from hooks.utest_presets import POLICY, preset_recipe, recommend_preset
from hooks.utest_test_environment import validate_worktree_pom
from hooks.utest_workspace_binding import resolve_task_workspace, select_task_execution_target
from hooks.utest_workspace_binding import UTestWorkspaceBindingError
from hooks.validate_utest_source_bug import SourceBugValidationError
from hooks.json_writer_common import WriterError


def fingerprint(root):
    """Hash only bounded local build inputs, including local parent POMs."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    names = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
             "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "gradle.properties")
    for directory in (root, *root.parents):
        for name in names if directory == root else ("pom.xml",):
            path = directory / name
            if path.is_file():
                digest.update(str(path).encode())
                digest.update(path.read_bytes())
        if (directory / ".git").exists():
            break
    for name in ("JAVA_HOME", "MAVEN_OPTS", "JAVA_TOOL_OPTIONS"):
        digest.update((name + "=" + os.environ.get(name, "")).encode())
    return digest.hexdigest()


def _read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def recipe_reference(environment, recipe):
    if recipe["reference"].startswith("reference/"):
        return (ROOT / "skills" / "autodev" / "autodev-utest" / recipe["reference"]).read_text(encoding="utf-8")
    if environment.get("runner") in {"maven", "gradle"}:
        from hooks.render_spring_test_reference import render
        return render(recipe["domain"])
    if environment.get("framework") in {"vue", "vue3", "react"}:
        from hooks.render_frontend_test_reference import render
        return render("vue" if environment["framework"] in {"vue", "vue3"} else "react", recipe["domain"])
    return "Reuse the closest test and configured runner; no speculative initialization."


def shell_path(value):
    # Native Windows paths must be executable/readable from Git Bash. Apply
    # the same normalization to the frozen argv and its recording invocation.
    return value.replace("\\", "/") if re.match(r"^[A-Za-z]:[\\/]", value) else value


def shell_quote(value):
    return shlex.quote(shell_path(value))


def validate_maven_module_argv(argv, root):
    for index, token in enumerate(argv):
        candidate = None
        if token in {"-f", "--file"} and index + 1 < len(argv):
            candidate = argv[index + 1]
        elif token.startswith("--file="):
            candidate = token.split("=", 1)[1]
        elif token.startswith("-f") and token != "-f":
            candidate = token[2:].lstrip("=")
        if candidate:
            path = Path(candidate)
            path = path if path.is_absolute() else Path(root) / path
            if path.resolve() != (Path(root) / "pom.xml").resolve():
                raise ValueError("utest_build_file_outside_selected_module")


def validate_exact_test_argv(argv, test_files, root):
    from hooks.run_utest_command import is_maven_command
    executable = Path(argv[0].replace("\\", "/")).name.lower()
    names = {Path(path).stem for path in test_files}
    if is_maven_command(argv):
        validate_maven_module_argv(argv, root)
        selectors = [token.split("=", 1)[1] for token in argv if token.startswith(("-Dtest=", "-Dit.test="))]
        selected = {name.split("#", 1)[0].rsplit(".", 1)[-1] for selector in selectors for name in selector.split(",")}
        if not selected or selected != names:
            raise ValueError("utest_maven_exact_test_selector_required")
    if executable in {"gradle", "gradlew", "gradle.bat", "gradlew.bat"}:
        selected = {argv[index + 1].rsplit(".", 1)[-1] for index, token in enumerate(argv[:-1]) if token == "--tests"}
        selected.update(token.split("=", 1)[1].rsplit(".", 1)[-1] for token in argv if token.startswith("--tests="))
        if selected != names:
            raise ValueError("utest_gradle_exact_test_selector_required")


def context(args):
    workspace = resolve_workspace(args.workspace)
    feature = resolve_feature(args.feature)
    manifest = load_manifest(workspace, feature, args.run_id)
    batch = manifest.get("batches", {}).get(args.batch_id, {})
    worktree = Path(args.batch_worktree).resolve()
    if Path(batch.get("worktreePath") or "/").resolve() != worktree:
        raise ValueError("utest_runtime_worktree_mismatch")
    stages = batch.get("stageStates", {})
    if stages.get("test", {}).get("status") != "running" or stages.get("review", {}).get("status") != "passed":
        raise ValueError("utest_runtime_requires_review_passed_and_test_running")
    if stages["test"].get("stopRequested"):
        raise ValueError("utest_stop_requested: retain output and seal/record/release only")
    if not check_lease(workspace, feature, args.run_id, args.batch_id, args.owner_token):
        raise ValueError("utest_runtime_lease_invalid")
    binding = resolve_task_workspace(workspace, feature, args.task_id,
                                     args.environment_target_id, code_workspace=worktree)
    if binding["batchId"] != args.batch_id:
        raise ValueError("utest_runtime_task_batch_mismatch")
    target = select_task_execution_target(binding, getattr(args, "test_file", None))
    root = Path(target["executionRoot"]).resolve()
    base = run_dir(workspace, feature, args.run_id) / "utest" / args.batch_id
    execution_path = base / "UTEST_EXECUTION.json"
    state = _read(execution_path)
    if state is None:
        from datetime import datetime
        started = stages["test"].get("startedAt")
        epoch = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp() if started else time.time()
        state = {"schemaVersion": "autodev.utest.execution.v1", "worktree": str(worktree),
                 "startedEpoch": epoch,
                 "firstCommandDeadlineEpoch": epoch + POLICY["firstExecutionSeconds"],
                 "stageDeadlineEpoch": epoch + min(args.stage_timeout, POLICY["stageSeconds"]),
                 "tasks": {}, "diagnostics": {}}
    durable = stage_budget(manifest, batch, state)
    state.update({**durable, "stageDeadlineEpoch": min(state["stageDeadlineEpoch"], durable["stageDeadlineEpoch"]),
                  "firstCommandDeadlineEpoch": min(state["firstCommandDeadlineEpoch"], durable["firstCommandDeadlineEpoch"])})
    if state["worktree"] != str(worktree):
        raise ValueError("utest_runtime_state_binding_mismatch")
    # Recovery or a new lease must never extend a previously saved deadline.
    state["stageDeadlineEpoch"] = min(state["stageDeadlineEpoch"], state["startedEpoch"] + args.stage_timeout)
    if time.time() >= state["stageDeadlineEpoch"]:
        raise ValueError("stage_deadline_exceeded")
    if not state.get("firstCommandAt") and time.time() >= state["firstCommandDeadlineEpoch"]:
        raise ValueError("first_execution_deadline_exceeded")
    plan = load_utest_plan(workspace / ".autobizdevops" / "features" / feature)
    task = next(task for item in plan["batches"] for task in item["tasks"] if task["id"] == args.task_id)
    entry = state["tasks"].get(args.task_id)
    if entry and entry["taskDigest"] != binding["taskDigest"]:
        raise ValueError("utest_runtime_task_digest_changed: reroute through controlled recovery")
    return workspace, feature, worktree, root, target, task, base, execution_path, state


def prepare(args, ctx):
    prepared_started = time.monotonic()
    _, _, _, root, target, task, base, path, state = ctx
    env_path = base / "UTEST_ENVIRONMENT.json"
    environments = _read(env_path, {"schemaVersion": "autodev.utest.environment.v1", "modules": {}})
    key, signature = str(root), fingerprint(root)
    env = environments["modules"].get(key)
    reused = bool(env and env["fingerprint"] == signature)
    if not reused:
        framework = _detected_framework(root)
        from hooks.engineering_environment import environment_identity
        from hooks.engineering_facts import digest
        from hooks.engineering_cache import resolve
        shared_identity = environment_identity(root)
        shared_path = ctx[0] / ".autobizdevops" / "features" / ctx[1] / ".engineering" / "environments" / shared_identity["repositoryId"] / (digest(shared_identity) + ".json")
        def produce_environment():
            observed = inspect_environment(root, framework) if framework else {"status": "unsupported", "capabilities": {}}
            return {"status": observed["status"], "environment": observed, "sourceRoot": str(root)}
        from hooks.engineering_observability import observe
        shared = resolve(shared_path, produce_environment, validate=lambda: environment_identity(root) == shared_identity,
                         notify=lambda event: observe(ctx[0], ctx[1], shared_identity["repositoryId"],
                             "environment_" + event.removeprefix("dependency_"), runId=args.run_id, batchId=args.batch_id))
        if shared["status"] == "in_progress":
            return shared
        # Only content-validated declaration data is transplanted, never PASS.
        def rebind(value):
            if isinstance(value, dict):
                return {k: rebind(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rebind(v) for v in value]
            if isinstance(value, str) and (value == shared["sourceRoot"] or value.startswith(shared["sourceRoot"] + os.sep)):
                return str(root) + value[len(shared["sourceRoot"]):]
            return value
        env = rebind(shared["environment"])
        env["sharedCacheStatus"] = shared["cacheStatus"]
        env.update(fingerprint=signature, projectRoot=key)
        environments["modules"][key] = env
        atomic_write_json(env_path, environments)
    frontend = (root / "package.json").is_file()
    preset = recommend_preset(task["testPoints"], frontend=frontend)
    entry = state["tasks"].setdefault(args.task_id, {"taskDigest": task["taskDigest"],
                                                   "presetId": preset, "strategyChanges": 0,
                                                   "commands": {}, "environmentTargetId": target["environmentTargetId"]})
    entry["recipe"] = preset_recipe(entry["presetId"], env.get("capabilities", {}))
    atomic_write_json(path, state)
    from hooks.engineering_observability import observe
    from hooks.engineering_facts import repository_id
    observe(ctx[0], ctx[1], repository_id(ctx[2]), "utest_prepared", runId=args.run_id, batchId=args.batch_id,
            stage="test", durationMs=int((time.monotonic() - prepared_started) * 1000))
    from types import SimpleNamespace
    from hooks.engineering_context import context_reference, execute as engineering_execute
    engineering = engineering_execute(SimpleNamespace(
        action="prepare", workspace=str(ctx[0]), feature=ctx[1], run_id=args.run_id,
        batch_id=args.batch_id, batch_worktree=str(ctx[2]), task_id=[args.task_id],
        stage="utest", owner_token=args.owner_token))
    return {"status": env["status"], "environmentReused": reused,
            "sharedEnvironmentReused": env.get("sharedCacheStatus") == "hit", "environment": env,
            "recipe": entry["recipe"], "referenceContent": recipe_reference(env, entry["recipe"]),
            "testPoints": task["testPoints"], "policy": state["utestPolicy"],
            "environmentPath": str(env_path), "executionPath": str(path),
            "engineeringContext": context_reference(engineering),
            "remainingSeconds": max(0, int(state["stageDeadlineEpoch"] - time.time()))}


def choose(args, ctx):
    result = prepare(args, ctx)
    state, path = ctx[-1], ctx[-2]
    entry = state["tasks"][args.task_id]
    if any(cmd.get("attempts", 0) for cmd in entry["commands"].values()):
        raise ValueError("utest_strategy_frozen_after_execution")
    if entry["presetId"] != args.preset:
        if not args.reason or entry["strategyChanges"] >= POLICY["strategyChanges"]:
            raise ValueError("utest_strategy_change_budget_exhausted_or_reason_missing")
        entry.update(presetId=args.preset, strategyChanges=entry["strategyChanges"] + 1, strategyReason=args.reason)
    entry["recipe"] = preset_recipe(entry["presetId"], result["environment"].get("capabilities", {}))
    atomic_write_json(path, state)
    return dict(result, recipe=entry["recipe"], referenceContent=recipe_reference(result["environment"], entry["recipe"]))


def apply_pom(args, ctx):
    _, _, worktree, root, _, _, _, _, _ = ctx
    candidate = Path(args.content_file).resolve()
    if not candidate.is_relative_to(Path(tempfile.gettempdir()).resolve()) and not candidate.is_relative_to(worktree):
        raise ValueError("utest_pom_content_outside_worktree_or_temp")
    after = candidate.read_text(encoding="utf-8-sig")
    path = root / "pom.xml"
    validation = validate_worktree_pom(worktree, path.relative_to(worktree), after=after)
    if not validation["allowed"]:
        raise ValueError(validation["reason"] + ":" + validation.get("detail", ""))
    # Same-directory atomic replace; never open a production task to authorize
    # a test dependency. The sealer repeats semantic validation against HEAD.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=root, delete=False) as handle:
        handle.write(after)
        temp = Path(handle.name)
    temp.replace(path)
    return dict(prepare(args, ctx), pomChange=validation)


from hooks.engineering_dependencies import lookup, inspect_class


def preflight(args, ctx, kind="test"):
    workspace, feature, worktree, root, target, task, base, path, state = ctx
    prepare(args, ctx)
    now = time.time()
    if now >= state["stageDeadlineEpoch"]:
        raise ValueError("stage_deadline_exceeded")
    if not state.get("firstCommandAt") and now >= state["firstCommandDeadlineEpoch"]:
        raise ValueError("first_execution_deadline_exceeded: record a blocked target and seal")
    files = []
    for name in args.test_file or []:
        relative = Path(name)
        test = (worktree / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not test.is_relative_to(root) or not test.is_file():
            raise ValueError("utest_preflight_test_file_invalid:" + name)
        files.append(name)
    if kind == "setup" and files:
        raise ValueError("utest_setup_does_not_accept_test_files")
    argv = [shell_path(token) for token in (args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv)]
    if not argv:
        raise ValueError("utest_preflight_requires_argv")
    if kind == "test" and (not files or not command_executes_tests({"argv": argv})):
        raise ValueError("utest_preflight_requires_test_files_and_behavior_command")
    if any(re.search(r"(?:skiptests|maven\.test\.skip|testfailureignore|failifnotests=false|failifnospecifiedtests=false)", item, re.I) for item in argv):
        raise ValueError("utest_preflight_test_masking_forbidden")
    cache_path = None
    if kind == "test":
        validate_exact_test_argv(argv, files, root)
    else:
        from hooks.run_utest_command import is_maven_command, maven_setup_executes_build, diagnostic_cache_path, cached_diagnostic
        if not is_maven_command(argv):
            raise ValueError("utest_setup_requires_maven_command")
        validate_maven_module_argv(argv, root)
        if maven_setup_executes_build(argv):
            raise ValueError("utest_setup_cannot_run_tests_or_compile: use behavior preflight or Bash compile")
        cache_path = diagnostic_cache_path(workspace / ".autobizdevops" / "features" / feature, root, argv)
    identity = [target["environmentTargetId"], files, argv]
    if kind == "setup":
        identity.extend(["bash-setup-v1", fingerprint(root), os.environ.get("PATH", "")])
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
    entry = state["tasks"][args.task_id]
    commands = entry.setdefault("setupCommands", {}) if kind == "setup" else entry["commands"]
    if kind == "test" and commands and key not in commands:
        raise ValueError("utest_exact_command_frozen: retain the original argv and test target")
    command = commands.setdefault(key, {"argv": argv, "testFiles": files if kind == "test" else None, "attempts": 0, "kind": kind})
    if command.get("status") in {"running", "awaiting_record"}:
        return {"status": "awaiting_record", "executionId": key, "outputFile": command["outputFile"],
                "requiredAction": "record_existing_output_only"}
    if command.get("status") == "prepared":
        from hooks.engineering_environment import diagnostic_claim_valid
        if not cache_path or diagnostic_claim_valid(cache_path, command.get("diagnosticClaimToken", "")):
            return {"status": "prepared", "executionId": key, "outputFile": command["outputFile"],
                    "scriptPath": command["scriptPath"], "bashCommand": ["bash", command["scriptPath"]]}
    cache_stamp = cache_path.stat().st_mtime_ns if cache_path and cache_path.is_file() else None
    if kind == "setup":
        if not getattr(args, "refresh_diagnostics", False):
            cached = cached_diagnostic(cache_path, root, argv)
            if cached:
                return dict(cached, status="cached")
            if command.get("status") == "recorded" and not cache_path:
                return dict(command["response"], status="cached", diagnosticReused=True)
    if kind == "test" and (command["attempts"] >= 3 or command.get("passed")):
        raise ValueError("utest_execution_budget_exhausted_or_already_passed")
    if kind == "test" and command["attempts"] and args.retry_kind not in {"test_bug", "source_repair"}:
        raise ValueError("utest_retry_requires_classified_failure")
    if kind == "test" and args.retry_kind == "source_repair" and not command.get("sourceRepairEligible"):
        raise ValueError("utest_source_repair_requires_failing_evidence")
    if kind == "test" and args.retry_kind == "source_repair":
        import subprocess
        current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, timeout=10).stdout.strip()
        if current == command.get("sourceCommit"):
            raise ValueError("utest_source_repair_requires_new_reviewed_production_commit")
        response = command["response"]
        if response["failureClassification"] != "source_compile":
            from hooks.validate_utest_source_bug import validate_claim
            validate_claim(workspace, feature, args.task_id, response["commandId"], response["targetId"],
                           response["taskDigest"], response["evidenceId"])
    if kind == "test" and args.retry_kind:
        counter = args.retry_kind + "Count"
        if command.get(counter, 0) >= 1:
            raise ValueError("utest_repair_budget_exhausted:" + args.retry_kind)
        command[counter] = command.get(counter, 0) + 1
    timeout = min(args.command_timeout, state["utestPolicy"]["commandSeconds"], int(state["stageDeadlineEpoch"] - now))
    if kind == "setup" and not state.get("firstCommandAt"):
        timeout = min(timeout, int(state["firstCommandDeadlineEpoch"] - now))
    if timeout <= 0:
        raise ValueError("stage_or_first_execution_deadline_exceeded")
    if cache_path:
        from hooks.engineering_environment import reserve_diagnostic
        owner = ":".join([args.run_id, args.batch_id, args.task_id, key])
        claim = reserve_diagnostic(cache_path, owner, timeout + 30, cache_stamp)
        if claim["status"] == "in_progress":
            return claim
        command["diagnosticClaimToken"] = claim["token"]
    command.update(pendingAttempt=command["attempts"] + 1, status="prepared", environmentFingerprint=fingerprint(root),
                   executionRoot=str(root), diagnosticCachePath=str(cache_path) if cache_path else None)
    command.setdefault("preparedEpoch", now)
    command.pop("pendingEvidenceId", None)
    command.pop("pendingOutputSha256", None)
    output = tempfile.NamedTemporaryFile(prefix="utest-output-", delete=False)
    output.close()
    command["outputFile"] = output.name
    script = base / ("execute-" + key + ".sh")
    record = [sys.executable, str(ROOT / "hooks" / "run_utest_command.py"), "--kind", kind, "--record-only",
              "--workspace", str(workspace), "--feature", feature, "--task-id", args.task_id,
              "--batch-worktree", str(worktree), "--output-file", output.name,
              "--run-id", args.run_id, "--batch-id", args.batch_id, "--execution-id", key]
    if args.environment_target_id:
        record.extend(["--environment-target-id", args.environment_target_id])
    for file in files:
        record.extend(["--test-file", file])
    watchdog = [sys.executable, str(Path(__file__).resolve()), "watch", "--pid", "$test_native_pid", "--seconds", str(timeout), "--deadline", str(state["stageDeadlineEpoch"]), "--output-file", output.name]
    begin_argv = [sys.executable, str(Path(__file__).resolve()), "begin", "--workspace", str(workspace),
                  "--feature", feature, "--run-id", args.run_id, "--batch-id", args.batch_id,
                  "--batch-worktree", str(worktree), "--task-id", args.task_id, "--owner-token", args.owner_token,
                  "--execution-id", key, "--attempt-number", str(command["pendingAttempt"]), "--kind", kind,
                  "--stage-timeout", str(args.stage_timeout)]
    if args.environment_target_id:
        begin_argv.extend(["--environment-target-id", args.environment_target_id])
    for file in files:
        begin_argv.extend(["--test-file", file])
    # Bash launches and streams the test itself. The Python watchdog only
    # supervises the process group, including tee and compiler children.
    content = "#!/usr/bin/env bash\nset -o pipefail\nset -m\ncd " + shell_quote(str(root)) + " || exit 2\n"
    content += """RAW_OUTPUT=""" + shell_quote(output.name) + "\n"
    content += '''watchdog=""
test_pid=""
cleanup() {
  if [ -n "$watchdog" ]; then kill "$watchdog" 2>/dev/null || true; fi
  if [ -n "$test_pid" ]; then kill -KILL -- "-$test_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
'''
    content += " ".join(shell_quote(v) for v in begin_argv) + " || exit 2\n"
    content += '(\n' + " ".join(shell_quote(v) for v in argv) + ' 2>&1 | tee "$RAW_OUTPUT"\nstatuses=("${PIPESTATUS[@]}")\nprintf "%s %s\\n" "${statuses[0]}" "${statuses[1]}" > "$RAW_OUTPUT.status"\n) &\ntest_pid=$!\n'
    content += 'test_native_pid=$test_pid\ncase "$OSTYPE" in msys*|cygwin*) test_native_pid=$(ps -p "$test_pid" | awk \'NR==2 {print $4}\') ;; esac\n'
    content += " ".join(shell_quote(v) if v != "$test_native_pid" else '"$test_native_pid"' for v in watchdog) + " &\nwatchdog=$!\n"
    content += 'wait "$test_pid"\ntest_wait=$?\n'
    content += 'kill "$watchdog" 2>/dev/null || true\nwait "$watchdog" 2>/dev/null || true\n'
    content += 'test_exit=$test_wait\nlog_exit=0\nif [ -f "$RAW_OUTPUT.status" ]; then read -r test_exit log_exit < "$RAW_OUTPUT.status"; fi\n'
    content += 'if [ -f "$RAW_OUTPUT.timeout" ]; then test_exit=124; fi\n'
    content += " ".join(shell_quote(v) for v in record) + ' --exit-code "$test_exit" --log-exit-code "$log_exit" -- ' + " ".join(shell_quote(v) for v in argv) + "\n"
    content += 'record_exit=$?\nif [ "$record_exit" -ne 0 ]; then exit "$record_exit"; fi\nrm -f "$RAW_OUTPUT" "$RAW_OUTPUT.timeout" "$RAW_OUTPUT.status"\nexit 0\n'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(content, encoding="utf-8", newline="\n")
    command["scriptPath"] = str(script)
    command["scriptSha256"] = hashlib.sha256(content.encode()).hexdigest()
    atomic_write_json(path, state)
    return {"status": "prepared", "executionId": key, "outputFile": output.name,
            "scriptPath": str(script), "executionCwd": str(root), "timeoutSeconds": timeout,
            "bashCommand": ["bash", script.as_posix()]}


def begin(args, ctx):
    state, path = ctx[-1], ctx[-2]
    now = time.time()
    kind = getattr(args, "kind", "test")
    command = state["tasks"][args.task_id]["setupCommands" if kind == "setup" else "commands"][args.execution_id]
    if command["status"] != "prepared" or command["pendingAttempt"] != args.attempt_number:
        raise ValueError("utest_execution_already_started_or_stale_script")
    if now >= state["stageDeadlineEpoch"] or (not state.get("firstCommandAt") and now >= state["firstCommandDeadlineEpoch"]):
        raise ValueError("stage_or_first_execution_deadline_exceeded")
    if command["environmentFingerprint"] != fingerprint(ctx[3]):
        raise ValueError("utest_execution_environment_changed: prepare again after controlled recovery")
    if kind == "setup" and command.get("diagnosticCachePath"):
        from hooks.engineering_environment import diagnostic_claim_valid
        if not diagnostic_claim_valid(Path(command["diagnosticCachePath"]), command.get("diagnosticClaimToken", "")):
            raise ValueError("utest_diagnostic_claim_expired_or_replaced: preflight again; do not replay stale script")
    command.update(status="running", attempts=args.attempt_number, startedEpoch=now)
    import subprocess
    command["sourceCommit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ctx[2], text=True, capture_output=True, timeout=10).stdout.strip()
    if kind == "test":
        from hooks.engineering_context import context_base, read_json
        from hooks.engineering_facts import FactStore
        used = read_json(context_base(ctx[0], ctx[1], args.run_id, args.batch_id) / "usage" / (args.task_id + ".json"))
        facts = FactStore(ctx[0], ctx[1], ctx[2]).related(command["testFiles"], used.get("symbols", []))
        command["engineeringFacts"] = [{"factId": fact["factId"], "sources": fact["sources"]} for fact in facts
                                        if set(fact.get("querySources", [])) & set(command["testFiles"])]
        state.setdefault("firstCommandAt", now)
    atomic_write_json(path, state)
    return {"status": "started", "executionId": args.execution_id}


def validate_runtime_execution(workspace, feature, run_id, batch_id, execution_id, task_id, argv, test_files, output_file, worktree, kind="test", execution_root=None):
    path = run_dir(Path(workspace), feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json"
    state = _read(path)
    cmd = state["tasks"][task_id]["setupCommands" if kind == "setup" else "commands"][execution_id]
    if (state["worktree"] != str(Path(worktree).resolve()) or cmd["argv"] != argv
            or cmd["testFiles"] != test_files or Path(cmd["outputFile"]).resolve() != Path(output_file).resolve()
            or cmd["status"] not in {"running", "awaiting_record", "recorded"}):
        raise ValueError("utest_execution_record_binding_mismatch")
    if execution_root is not None and cmd.get("executionRoot") and Path(execution_root).resolve() != Path(cmd["executionRoot"]).resolve():
        raise ValueError("utest_execution_record_module_mismatch")
    return cmd


def record_runtime_execution(workspace, feature, run_id, batch_id, execution_id, task_id, argv, test_files, output_file, response, kind="test"):
    """Commit an attempt after setup logging or test Evidence registration."""
    path = run_dir(Path(workspace), feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json"
    with run_lock(Path(workspace), feature, run_id):
        state = _read(path)
        cmd = state["tasks"][task_id]["setupCommands" if kind == "setup" else "commands"][execution_id]
        if cmd["argv"] != argv or cmd["testFiles"] != test_files or Path(cmd["outputFile"]).resolve() != Path(output_file).resolve():
            raise ValueError("utest_execution_record_binding_mismatch")
        if cmd.get("executionRoot") and Path(response["cwd"]).resolve() != Path(cmd["executionRoot"]).resolve():
            raise ValueError("utest_execution_record_module_mismatch")
        cmd.update(status="recorded", passed=response["result"] == "PASS", result=response["result"],
                   evidenceId=response["evidenceId"], sourceRepairEligible=response["exitCode"] != 0 and response["result"] == "FAIL" and response["failureClassification"] in {"source_compile", "test_failure"},
                   response=response)
        cmd.update(completedEpoch=time.time(), durationMs=max(0, int((time.time() - cmd.get("startedEpoch", time.time())) * 1000)))
        atomic_write_json(path, state)
        if kind == "test" and response.get("result") != "PASS":
            from hooks.engineering_context import record_repair
            manifest = load_manifest(Path(workspace), feature, run_id)
            batch = manifest["batches"][batch_id]
            failure = {"type": response.get("failureClassification"),
                       "message": " ".join(str(response.get(k) or "") for k in ("targetId", "commandId", "evidenceId")),
                       "nextStage": "test"}
            record_repair(Path(workspace), feature, run_id, batch_id, batch, "test", failure)
        if kind == "test" and cmd.get("engineeringFacts"):
            from hooks.engineering_facts import FactStore
            store = FactStore(Path(workspace), feature, Path(state["worktree"]))
            for fact in cmd["engineeringFacts"]:
                store.attach_test_proof(fact["factId"], fact["sources"], {**response, "runId": run_id, "batchId": batch_id, "taskId": task_id})


def remember_pending_evidence(workspace, feature, run_id, batch_id, execution_id, task_id, evidence_id, output):
    path = run_dir(Path(workspace), feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json"
    with run_lock(Path(workspace), feature, run_id):
        state = _read(path)
        cmd = state["tasks"][task_id]["commands"][execution_id]
        cmd.update(pendingEvidenceId=evidence_id, pendingOutputSha256=hashlib.sha256(output.encode()).hexdigest())
        atomic_write_json(path, state)


def watch(pid, seconds, output_file, deadline):
    # Bash's job control creates a dedicated group for the test subshell. Kill
    # that group, not the parent shell that must still register the evidence.
    time.sleep(max(0, min(seconds, deadline - time.time())))
    import subprocess
    Path(str(output_file) + ".timeout").write_text("command_timeout\n", encoding="utf-8")
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def build_parser():
    parser = RuntimeArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    watcher = sub.add_parser("watch")
    watcher.add_argument("--pid", type=int, required=True)
    watcher.add_argument("--seconds", type=int, required=True)
    watcher.add_argument("--output-file", required=True)
    watcher.add_argument("--deadline", type=float, required=True)
    for action in ("prepare", "select", "apply-pom", "lookup-class", "inspect-class", "preflight", "preflight-setup", "begin"):
        item = sub.add_parser(action)
        for name in ("workspace", "feature", "run-id", "batch-id", "batch-worktree", "task-id", "owner-token"):
            item.add_argument("--" + name, required=True)
        item.add_argument("--environment-target-id")
        item.add_argument("--stage-timeout", type=int, default=POLICY["stageSeconds"])
        if action == "select":
            item.add_argument("--preset", required=True)
            item.add_argument("--reason", required=True)
        elif action == "apply-pom":
            item.add_argument("--content-file", required=True)
        elif action in {"lookup-class", "inspect-class"}:
            item.add_argument("--classpath-file", required=True)
            item.add_argument("--class-name", required=True)
        elif action in {"preflight", "preflight-setup"}:
            item.add_argument("--test-file", action="append", required=action == "preflight")
            item.add_argument("--retry-kind", choices=("test_bug", "source_repair"))
            item.add_argument("--refresh-diagnostics", action="store_true")
            item.add_argument("--command-timeout", type=int, default=POLICY["commandSeconds"])
            item.add_argument("argv", nargs=argparse.REMAINDER)
        elif action == "begin":
            item.add_argument("--kind", choices=("test", "setup"), default="test")
            item.add_argument("--execution-id", required=True)
            item.add_argument("--attempt-number", type=int, required=True)
            item.add_argument("--test-file", action="append")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.action == "watch":
        watch(args.pid, args.seconds, args.output_file, args.deadline)
        return 0
    try:
        ctx = context(args)
        handlers = {"prepare": prepare, "select": choose, "apply-pom": apply_pom,
                    "lookup-class": lookup, "inspect-class": inspect_class, "preflight": preflight,
                    "preflight-setup": lambda args, ctx: preflight(args, ctx, kind="setup"), "begin": begin}
        if args.action in {"lookup-class", "inspect-class"}:
            result = handlers[args.action](args, ctx)
            context(args)  # Recheck stage, lease and deadline after inspection.
        else:
            with run_lock(ctx[0], ctx[1], args.run_id):
                ctx = context(args)
                result = handlers[args.action](args, ctx)
    except (ValueError, OSError, KeyError, WriterError, UTestWorkspaceBindingError, SourceBugValidationError) as exc:
        print(json.dumps({"status": "blocked", "reasonCode": str(exc),
                          "requiredAction": "retain_output_and_seal_or_record_blocked; do not repeat unchanged probes"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
