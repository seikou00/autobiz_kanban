"""Shared, bounded dependency inspection for Code, UTest and repairs.

Callers validate their own stage/lease; this module grants no business writes.
"""
from __future__ import annotations
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from hooks.utest_policy import POLICY

def _dependency_inputs(args, ctx):
    from hooks.engineering_facts import artifact_fingerprint, repository_id, shared_build_fingerprint
    from hooks.json_writer_common import feature_dir
    worktree, root = ctx[2], ctx[3]
    snapshot = Path(args.classpath_file)
    snapshot = (snapshot if snapshot.is_absolute() else worktree / snapshot).resolve()
    if not snapshot.is_relative_to(worktree) and not snapshot.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("utest_classpath_snapshot_outside_binding")
    if not re.fullmatch(r"[\w$]+(?:\.[\w$]+)+", args.class_name):
        raise ValueError("utest_class_name_must_be_fully_qualified")
    data = snapshot.read_text(encoding="utf-8-sig").strip()
    separator = ";" if ";" in data or re.match(r"^[A-Za-z]:[\\/]", data) else os.pathsep
    jars = [Path(part) for part in data.split(separator) if part]
    if len(jars) > 512 or any(not jar.is_absolute() for jar in jars):
        raise ValueError("utest_classpath_too_large")
    repo = repository_id(worktree)
    shared = feature_dir(ctx[0], ctx[1]) / ".engineering" / "dependencies" / repo
    deadline = time.monotonic() + POLICY["indexSeconds"]
    entry = args.class_name.replace(".", "/") + ".class"
    def identity():
        from hooks.engineering_environment import environment_identity
        from hooks.engineering_facts import digest
        signature = hashlib.sha256((digest(environment_identity(root)) + snapshot.read_text(encoding="utf-8-sig").strip() + args.class_name).encode())
        for jar in jars:
            signature.update(artifact_fingerprint(jar / entry if jar.is_dir() else jar, shared, deadline).encode())
        return signature.hexdigest()
    def notify(event, cache_key="", operation="lookup"):
        from hooks.engineering_observability import observe
        observe(ctx[0], ctx[1], repo, event, runId=getattr(args, "run_id", ""), batchId=getattr(args, "batch_id", ""),
                cacheKey=cache_key, queryKey=hashlib.sha256((operation + args.class_name + str(root.relative_to(worktree))).encode()).hexdigest())
    return jars, shared, deadline, entry, identity, notify


def lookup(args, ctx):
    """One producer per exact classpath key; parsing never holds the run lock."""
    from hooks.engineering_cache import resolve
    jars, shared, deadline, entry, identity, notify = _dependency_inputs(args, ctx)
    key = identity()
    def produce():
        matches = []
        for jar in jars:
            if time.monotonic() >= deadline:
                return {"status": "blocked", "reasonCode": "classpath_lookup_timeout", "matches": matches}
            if jar.is_dir():
                if (jar / entry).is_file():
                    matches.append(str(jar))
            elif jar.is_file():
                try:
                    with zipfile.ZipFile(jar) as archive:
                        archive.getinfo(entry)
                    matches.append(str(jar))
                except (KeyError, zipfile.BadZipFile):
                    pass
        return {"status": "found" if matches else "missing", "className": args.class_name,
                "matches": matches, "duplicateClass": len(matches) > 1}
    return resolve(shared / ("classpath-" + key + ".json"), produce,
                   validate=lambda: identity() == key, notify=lambda event: notify(event, key))


def inspect_class(args, ctx):
    """Sources/javap inspection shares per-key claims, including failures."""
    from hooks.engineering_cache import resolve
    from hooks.engineering_facts import artifact_fingerprint
    located = lookup(args, ctx)
    if located["status"] != "found":
        return located
    jars, shared, deadline, entry, cp_identity, notify = _dependency_inputs(args, ctx)
    jar = Path(located["matches"][0])
    source_jar = jar.with_name(jar.stem + "-sources.jar")
    def identity():
        return hashlib.sha256((cp_identity() + str(jar) + artifact_fingerprint(source_jar, shared, deadline)).encode()).hexdigest()
    key = identity()
    def produce():
        result = dict(located, reused=False, selectedJar=str(jar))
        source_entry = args.class_name.split("$", 1)[0].replace(".", "/") + ".java"
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
                                           capture_output=True, text=True, timeout=max(0.1, deadline - time.monotonic()))
                content = inspected.stdout + inspected.stderr
                result.update(status="found" if inspected.returncode == 0 else "blocked", kind="bytecode",
                              exitCode=inspected.returncode, content=content[:65536], truncated=len(content) > 65536)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
            result.update(status="blocked", reasonCode="class_inspection_unavailable", detail=str(exc))
        return result
    return resolve(shared / ("class-detail-" + key + ".json"), produce,
                   validate=lambda: identity() == key, notify=lambda event: notify(event, key, "inspect"))
