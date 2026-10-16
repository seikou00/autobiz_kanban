"""Content identities and structured outputs for shared read-only diagnostics."""
from __future__ import annotations
import os
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from hooks.engineering_facts import (digest, git, repository_id, shared_build_fingerprint, file_digest,
                                     source_path)
from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json


def environment_identity(module: Path) -> dict:
    module = Path(module).resolve()
    root = Path(git(module, "rev-parse", "--show-toplevel")).resolve()
    settings = Path(os.environ.get("MAVEN_CONFIG", str(Path.home() / ".m2"))) / "settings.xml"
    toolchain = {name: shutil.which(name) for name in ("java", "javap", "mvn")}
    if os.environ.get("JAVA_HOME"):
        for name in ("java", "javap"):
            candidate = Path(os.environ["JAVA_HOME"]) / "bin" / (name + (".exe" if os.name == "nt" else ""))
            toolchain[name] = str(candidate) if candidate.is_file() else None
    signatures = {name: [path, file_digest(Path(path))] if path and Path(path).is_file() else path
                  for name, path in toolchain.items()}
    return {"repositoryId": repository_id(root), "module": module.relative_to(root).as_posix(),
            "build": shared_build_fingerprint(root, module), "tools": signatures,
            "settings": file_digest(settings),
            "mavenConfig": {name: file_digest(source_path(root, name)) for name in
                            (".mvn/maven.config", ".mvn/jvm.config", ".mvn/extensions.xml")},
            "environment": {name: os.environ.get(name, "") for name in
                            ("M2_HOME", "MAVEN_HOME", "MAVEN_CONFIG", "AUTODEV_DEPENDENCY_REVISION")}}


def diagnostic_identity(module: Path, argv: list[str]) -> dict:
    canonical = []
    for token in argv:
        if token.startswith(("-DoutputFile=", "-Dmdep.outputFile=")):
            canonical.append(token.split("=", 1)[0] + "=<runtime-output>")
        else:
            canonical.append(token)
    executable = Path(argv[0]) if Path(argv[0]).is_absolute() else Path(shutil.which(argv[0]) or module / argv[0])
    settings = {}
    for index, token in enumerate(argv[:-1]):
        if token in {"-s", "--settings", "-gs", "--global-settings"}:
            path = Path(argv[index + 1])
            path = path if path.is_absolute() else module / path
            settings[token] = file_digest(path)
    return {"version": "shared-bash-diagnostic-v2", **environment_identity(module), "argv": canonical,
            "invokedExecutable": file_digest(executable), "explicitSettings": settings}


def capture_diagnostic(response: dict, cwd: Path, argv: list[str]) -> dict:
    """Output files are data, never another worktree's temporary file path."""
    result = dict(response, cacheVersion=2, cacheExpiresEpoch=time.time() + 60 if not response.get("ok") else None)
    outputs = []
    for token in argv:
        if not token.startswith(("-DoutputFile=", "-Dmdep.outputFile=")):
            continue
        name = token.split("=", 1)[1]
        path = Path(name) if Path(name).is_absolute() else cwd / name
        if path.is_file() and path.stat().st_size <= 1024 * 1024:
            value = path.read_text(encoding="utf-8-sig")
            # Local build outputs cannot be transplanted between worktrees.
            if str(cwd).replace("\\", "/").casefold() not in value.replace("\\", "/").casefold():
                outputs.append({"option": token.split("=", 1)[0], "content": value})
    result["structuredOutputs"] = outputs
    return result


def restore_diagnostic(cached: dict, cwd: Path, argv: list[str]) -> bool:
    for token in argv:
        if not token.startswith(("-DoutputFile=", "-Dmdep.outputFile=")):
            continue
        name = token.split("=", 1)[1]
        path = (Path(name) if Path(name).is_absolute() else cwd / name).resolve()
        temp = Path(tempfile.gettempdir()).resolve()
        safe_temp = path.is_relative_to(temp) and path.name.startswith("utest-classpath-")
        if not path.is_relative_to(cwd.resolve()) and not safe_temp:
            # Never restore an arbitrary old/temporary path supplied by a model.
            return False
        if path.is_relative_to(cwd.resolve()):
            relative = path.relative_to(cwd.resolve())
            if any(p in relative.parts for p in (".git", ".autobizdevops", ".cmbdevclaw")) or relative.as_posix().startswith("src/"):
                return False
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", str(relative)],
                                     cwd=cwd, capture_output=True, timeout=10)
            if tracked.returncode == 0:
                return False
        outputs = cached.get("structuredOutputs", [])
        if not isinstance(outputs, list):
            return False
        item = next((x for x in outputs if isinstance(x, dict) and x.get("option") == token.split("=", 1)[0]), None)
        if item is None or not isinstance(item.get("content"), str):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")
    return True


def _claim(path: Path) -> dict:
    try:
        value = json.loads(path.with_suffix(".claim.json").read_text())
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def reserve_diagnostic(path: Path, owner: str, ttl: float, expected_stamp: int | None = None) -> dict:
    with FileLock(path.with_suffix(".lock")):
        stamp = path.stat().st_mtime_ns if path.is_file() else None
        if stamp != expected_stamp:
            return {"status": "in_progress", "retryAfterSeconds": 1, "reasonCode": "shared_diagnostic_updated_retry_lookup"}
        old = _claim(path)
        if old.get("expiresEpoch", 0) > time.time():
            if old.get("owner") == owner:
                return {"status": "claimed", "token": old["token"]}
            return {"status": "in_progress", "retryAfterSeconds": 2, "reasonCode": "shared_diagnostic_producer_active"}
        token = uuid.uuid4().hex
        atomic_write_json(path.with_suffix(".claim.json"), {"owner": owner, "token": token, "expiresEpoch": time.time() + ttl})
        return {"status": "claimed", "token": token}


def diagnostic_claim_valid(path: Path, token: str) -> bool:
    claim = _claim(path)
    return claim.get("token") == token and claim.get("expiresEpoch", 0) > time.time()


def publish_diagnostic(path: Path, token: str | None, value: dict) -> bool:
    with FileLock(path.with_suffix(".lock")):
        if token and not diagnostic_claim_valid(path, token):
            return False
        if not token and _claim(path).get("expiresEpoch", 0) > time.time():
            return False
        atomic_write_json(path, value)
        if token:
            path.with_suffix(".claim.json").unlink(missing_ok=True)
        return True
