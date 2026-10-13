#!/usr/bin/env python3
"""Bounded UTest preparation, test-only POM writes and durable execution plans.

Tests are launched by the generated Bash script, never by this Python process.
The runtime supervises the Bash process group and records its real output via
run_utest_command.py. All state is bound to a live Batch lease and Plan V2 task.
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


def validate_exact_test_argv(argv, test_files, root):
    executable = Path(argv[0].replace("\\", "/")).name.lower()
    names = {Path(path).stem for path in test_files}
    if executable in {"mvn", "mvnw", "mvn.cmd", "mvnw.cmd"}:
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
    _, _, _, root, target, task, base, path, state = ctx
    env_path = base / "UTEST_ENVIRONMENT.json"
    environments = _read(env_path, {"schemaVersion": "autodev.utest.environment.v1", "modules": {}})
    key, signature = str(root), fingerprint(root)
    env = environments["modules"].get(key)
    reused = bool(env and env["fingerprint"] == signature)
    if not reused:
        framework = _detected_framework(root)
        env = inspect_environment(root, framework) if framework else {"status": "unsupported", "capabilities": {}}
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
    return {"status": env["status"], "environmentReused": reused, "environment": env,
            "recipe": entry["recipe"], "referenceContent": recipe_reference(env, entry["recipe"]),
            "testPoints": task["testPoints"], "policy": POLICY,
            "environmentPath": str(env_path), "executionPath": str(path),
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


def lookup(args, ctx):
    """Lookup exact class entries only in a supplied Maven dependency snapshot."""
    _, _, worktree, root, _, _, base, _, _ = ctx
    snapshot = Path(args.classpath_file).resolve()
    if not snapshot.is_relative_to(worktree) and not snapshot.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("utest_classpath_snapshot_outside_binding")
    if not re.fullmatch(r"[\w$]+(?:\.[\w$]+)+", args.class_name):
        raise ValueError("utest_class_name_must_be_fully_qualified")
    data = snapshot.read_text(encoding="utf-8-sig").strip()
    # Windows drive colons are not classpath separators. Preserve dependency
    # ordering and duplicates; no walk/glob of the local Maven repository.
    separator = ";" if ";" in data or re.match(r"^[A-Za-z]:[\\/]", data) else os.pathsep
    jars = [Path(part) for part in data.split(separator) if part]
    signature = hashlib.sha256((fingerprint(root) + data + args.class_name).encode())
    for jar in jars:
        if jar.is_file():
            info = jar.stat()
            signature.update(f"{jar}:{info.st_size}:{info.st_mtime_ns}".encode())
    cache = base / ("classpath-" + signature.hexdigest() + ".json")
    cached = _read(cache)
    if cached is not None:
        return dict(cached, reused=True)
    started, matches = time.monotonic(), []
    entry = args.class_name.replace(".", "/") + ".class"
    for jar in jars:
        if time.monotonic() - started >= POLICY["indexSeconds"]:
            result = {"status": "blocked", "reasonCode": "classpath_lookup_timeout", "matches": matches}
            atomic_write_json(cache, result)
            return result
        if jar.is_dir():
            if (jar / entry).is_file():
                matches.append(str(jar))
        elif jar.is_file():
            try:
                with zipfile.ZipFile(jar) as archive:
                    archive.getinfo(entry)  # exact directory lookup, no extraction
                matches.append(str(jar))
            except (KeyError, zipfile.BadZipFile):
                pass
    result = {"status": "found" if matches else "missing", "className": args.class_name,
              "matches": matches, "duplicateClass": len(matches) > 1, "reused": False}
    atomic_write_json(cache, result)
    return result


def inspect_class(args, ctx):
    """Reuse exact source-jar reads or bounded javap, including failures."""
    located = lookup(args, ctx)
    if located["status"] != "found":
        return located
    jar = Path(located["matches"][0])
    source_jar = jar.with_name(jar.stem + "-sources.jar")
    signature = hashlib.sha256((fingerprint(ctx[3]) + str(jar) + args.class_name).encode())
    for path in (jar, source_jar):
        if path.is_file():
            info = path.stat()
            signature.update(f"{info.st_size}:{info.st_mtime_ns}".encode())
    cache = ctx[6] / ("class-detail-" + signature.hexdigest() + ".json")
    previous = _read(cache)
    if previous is not None:
        return dict(previous, reused=True)
    source_entry = args.class_name.split("$", 1)[0].replace(".", "/") + ".java"
    result = dict(located, reused=False, selectedJar=str(jar))
    try:
        if source_jar.is_file():
            with zipfile.ZipFile(source_jar) as archive:
                with archive.open(source_entry) as handle:
                    content = handle.read(65537)
            result.update(kind="source", content=content[:65536].decode("utf-8", errors="replace"), truncated=len(content) > 65536)
        else:
            java_home = os.environ.get("JAVA_HOME")
            binary = str(Path(java_home) / "bin" / ("javap.exe" if os.name == "nt" else "javap")) if java_home else shutil.which("javap")
            if not binary:
                raise ValueError("javap_unavailable")
            inspected = subprocess.run([binary, "-classpath", str(jar), "-p", "-c", args.class_name],
                                       capture_output=True, text=True, timeout=POLICY["indexSeconds"])
            content = inspected.stdout + inspected.stderr
            result.update(status="found" if inspected.returncode == 0 else "blocked", kind="bytecode",
                          exitCode=inspected.returncode, content=content[:65536], truncated=len(content) > 65536)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        result.update(status="blocked", reasonCode="class_inspection_unavailable", detail=str(exc))
    atomic_write_json(cache, result)
    return result


def preflight(args, ctx):
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
    argv = [shell_path(token) for token in (args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv)]
    if not files or not command_executes_tests({"argv": argv}):
        raise ValueError("utest_preflight_requires_test_files_and_behavior_command")
    if any(re.search(r"(?:skiptests|maven\.test\.skip|testfailureignore|failifnotests=false|failifnospecifiedtests=false)", item, re.I) for item in argv):
        raise ValueError("utest_preflight_test_masking_forbidden")
    validate_exact_test_argv(argv, files, root)
    key = hashlib.sha256(json.dumps([target["environmentTargetId"], files, argv]).encode()).hexdigest()[:20]
    entry = state["tasks"][args.task_id]
    if entry["commands"] and key not in entry["commands"]:
        raise ValueError("utest_exact_command_frozen: retain the original argv and test target")
    command = entry["commands"].setdefault(key, {"argv": argv, "testFiles": files, "attempts": 0})
    if command.get("status") in {"running", "awaiting_record"}:
        return {"status": "awaiting_record", "executionId": key, "outputFile": command["outputFile"],
                "requiredAction": "record_existing_output_only"}
    if command.get("status") == "prepared":
        return {"status": "prepared", "executionId": key, "outputFile": command["outputFile"],
                "scriptPath": command["scriptPath"], "bashCommand": ["bash", command["scriptPath"]]}
    if command["attempts"] >= 3 or command.get("passed"):
        raise ValueError("utest_execution_budget_exhausted_or_already_passed")
    if command["attempts"] and args.retry_kind not in {"test_bug", "source_repair"}:
        raise ValueError("utest_retry_requires_classified_failure")
    if args.retry_kind == "source_repair" and not command.get("sourceRepairEligible"):
        raise ValueError("utest_source_repair_requires_failing_evidence")
    if args.retry_kind == "source_repair":
        import subprocess
        current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, timeout=10).stdout.strip()
        if current == command.get("sourceCommit"):
            raise ValueError("utest_source_repair_requires_new_reviewed_production_commit")
        response = command["response"]
        if response["failureClassification"] != "source_compile":
            from hooks.validate_utest_source_bug import validate_claim
            validate_claim(workspace, feature, args.task_id, response["commandId"], response["targetId"],
                           response["taskDigest"], response["evidenceId"])
    if args.retry_kind:
        counter = args.retry_kind + "Count"
        if command.get(counter, 0) >= 1:
            raise ValueError("utest_repair_budget_exhausted:" + args.retry_kind)
        command[counter] = command.get(counter, 0) + 1
    timeout = min(args.command_timeout, POLICY["commandSeconds"], int(state["stageDeadlineEpoch"] - now))
    command.update(pendingAttempt=command["attempts"] + 1, status="prepared", environmentFingerprint=fingerprint(root))
    command.pop("pendingEvidenceId", None)
    command.pop("pendingOutputSha256", None)
    output = tempfile.NamedTemporaryFile(prefix="utest-output-", delete=False)
    output.close()
    command["outputFile"] = output.name
    script = base / ("execute-" + key + ".sh")
    record = [sys.executable, str(ROOT / "hooks" / "run_utest_command.py"), "--kind", "test", "--record-only",
              "--workspace", str(workspace), "--feature", feature, "--task-id", args.task_id,
              "--batch-worktree", str(worktree), "--output-file", output.name,
              "--run-id", args.run_id, "--batch-id", args.batch_id, "--execution-id", key]
    for file in files:
        record.extend(["--test-file", file])
    watchdog = [sys.executable, str(Path(__file__).resolve()), "watch", "--pid", "$test_native_pid", "--seconds", str(timeout), "--deadline", str(state["stageDeadlineEpoch"]), "--output-file", output.name]
    begin_argv = [sys.executable, str(Path(__file__).resolve()), "begin", "--workspace", str(workspace),
                  "--feature", feature, "--run-id", args.run_id, "--batch-id", args.batch_id,
                  "--batch-worktree", str(worktree), "--task-id", args.task_id, "--owner-token", args.owner_token,
                  "--execution-id", key, "--attempt-number", str(command["pendingAttempt"])]
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
    command = state["tasks"][args.task_id]["commands"][args.execution_id]
    if command["status"] != "prepared" or command["pendingAttempt"] != args.attempt_number:
        raise ValueError("utest_execution_already_started_or_stale_script")
    if now >= state["stageDeadlineEpoch"] or (not state.get("firstCommandAt") and now >= state["firstCommandDeadlineEpoch"]):
        raise ValueError("stage_or_first_execution_deadline_exceeded")
    if command["environmentFingerprint"] != fingerprint(ctx[3]):
        raise ValueError("utest_execution_environment_changed: prepare again after controlled recovery")
    command.update(status="running", attempts=args.attempt_number, startedEpoch=now)
    import subprocess
    command["sourceCommit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ctx[2], text=True, capture_output=True, timeout=10).stdout.strip()
    state.setdefault("firstCommandAt", now)
    atomic_write_json(path, state)
    return {"status": "started", "executionId": args.execution_id}


def validate_runtime_execution(workspace, feature, run_id, batch_id, execution_id, task_id, argv, test_files, output_file, worktree):
    path = run_dir(Path(workspace), feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json"
    state = _read(path)
    cmd = state["tasks"][task_id]["commands"][execution_id]
    if (state["worktree"] != str(Path(worktree).resolve()) or cmd["argv"] != argv
            or cmd["testFiles"] != test_files or Path(cmd["outputFile"]).resolve() != Path(output_file).resolve()
            or cmd["status"] not in {"running", "awaiting_record", "recorded"}):
        raise ValueError("utest_execution_record_binding_mismatch")
    return cmd


def record_runtime_execution(workspace, feature, run_id, batch_id, execution_id, task_id, argv, test_files, output_file, response):
    """Commit an attempt only after Evidence/result registration succeeds."""
    path = run_dir(Path(workspace), feature, run_id) / "utest" / batch_id / "UTEST_EXECUTION.json"
    with run_lock(Path(workspace), feature, run_id):
        state = _read(path)
        cmd = state["tasks"][task_id]["commands"][execution_id]
        if cmd["argv"] != argv or cmd["testFiles"] != test_files or Path(cmd["outputFile"]).resolve() != Path(output_file).resolve():
            raise ValueError("utest_execution_record_binding_mismatch")
        cmd.update(status="recorded", passed=response["result"] == "PASS", result=response["result"],
                   evidenceId=response["evidenceId"], sourceRepairEligible=response["exitCode"] != 0 and response["result"] == "FAIL" and response["failureClassification"] in {"source_compile", "test_failure"},
                   response=response)
        atomic_write_json(path, state)


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    watcher = sub.add_parser("watch")
    watcher.add_argument("--pid", type=int, required=True)
    watcher.add_argument("--seconds", type=int, required=True)
    watcher.add_argument("--output-file", required=True)
    watcher.add_argument("--deadline", type=float, required=True)
    for action in ("prepare", "select", "apply-pom", "lookup-class", "inspect-class", "preflight", "begin"):
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
        elif action == "preflight":
            item.add_argument("--test-file", action="append", required=True)
            item.add_argument("--retry-kind", choices=("test_bug", "source_repair"))
            item.add_argument("--command-timeout", type=int, default=POLICY["commandSeconds"])
            item.add_argument("argv", nargs=argparse.REMAINDER)
        elif action == "begin":
            item.add_argument("--execution-id", required=True)
            item.add_argument("--attempt-number", type=int, required=True)
            item.add_argument("--test-file", action="append")
    args = parser.parse_args(argv)
    if args.action == "watch":
        watch(args.pid, args.seconds, args.output_file, args.deadline)
        return 0
    try:
        ctx = context(args)
        handlers = {"prepare": prepare, "select": choose, "apply-pom": apply_pom,
                    "lookup-class": lookup, "inspect-class": inspect_class, "preflight": preflight, "begin": begin}
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
