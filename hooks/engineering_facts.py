"""Content-bound engineering facts shared by the worktrees of one Feature.

Facts are hints with provenance, never execution authority or passing Evidence.
Locks cover only short metadata transactions; parsing and model work happen
outside the lock. A generation token fences expired producers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from hooks.evidence_kernel import FileLock
from hooks.json_writer_common import atomic_write_json, feature_dir

SCHEMA = "autodev.engineering.fact.v1"
KINDS = ("api_signature", "usage_example", "runtime_precondition", "test_recipe", "capability_result")
BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
               "gradle.properties", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock")
MAX_SOURCE_BYTES = 1024 * 1024
MAX_FACTS = 2048


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def git(root: Path, *argv: str) -> str:
    try:
        result = subprocess.run(["git", *argv], cwd=root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("engineering_git_timeout") from exc
    if result.returncode:
        raise ValueError("engineering_git_failed:" + " ".join(argv[:2]))
    return result.stdout if "-z" in argv else result.stdout.strip()


def repository_id(root: Path) -> str:
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    return digest(str((root / common).resolve() if not common.is_absolute() else common.resolve()))


def source_path(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or not relative or ".git" in raw.parts:
        raise ValueError("engineering_source_outside_worktree")
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("engineering_source_outside_worktree")
    # Nested Git roots and external symlinks are not part of this binding.
    for parent in candidate.parents:
        if parent == root.resolve():
            break
        if (parent / ".git").exists():
            raise ValueError("engineering_source_nested_repository")
    return candidate


def environment_scope() -> str:
    return digest({name: os.environ.get(name, "") for name in
                   ("JAVA_HOME", "JAVA_TOOL_OPTIONS", "MAVEN_OPTS", "PATH")})


def shared_build_fingerprint(worktree: Path, module: Path) -> str:
    observed = {}
    for directory in (module, *module.parents):
        if not directory.is_relative_to(worktree):
            break
        for name in BUILD_FILES:
            path = directory / name
            observed[path.relative_to(worktree).as_posix()] = file_digest(source_path(worktree, path.relative_to(worktree).as_posix()))
    return digest({"inputs": observed, "environment": environment_scope()})


def artifact_fingerprint(path: Path, cache_base: Path, deadline: float) -> str:
    """Hash exact dependency contents once per stat identity, no jar traversal."""
    if not path.is_file():
        return "missing"
    stat = path.stat()
    identity = [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
    cache = cache_base / "fingerprints" / (digest(identity) + ".json")
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("identity") == identity:
            return saved["sha256"]
    except (OSError, ValueError, KeyError):
        pass
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("engineering_dependency_fingerprint_timeout")
            part = handle.read(1024 * 1024)
            if not part:
                break
            sha.update(part)
    if identity != [str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.stat().st_ino]:
        raise ValueError("engineering_dependency_changed_during_read")
    atomic_write_json(cache, {"identity": identity, "sha256": sha.hexdigest()})
    return sha.hexdigest()


def file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("engineering_source_not_file")
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(part)
    return sha.hexdigest()


def provenance(root: Path, sources: list[str]) -> dict[str, str | None]:
    if not sources or len(sources) > 32:
        raise ValueError("engineering_requires_one_to_32_sources")
    observed: dict[str, str | None] = {}
    for relative in sorted(set(sources)):
        path = source_path(root, relative)
        if not path.is_file():
            raise ValueError("engineering_source_missing:" + relative)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("engineering_source_too_large_or_not_file")
        observed[path.relative_to(root.resolve()).as_posix()] = file_digest(path)
        # Include missing build inputs as well: adding a POM invalidates a fact.
        for directory in (path.parent, *path.parent.parents):
            if not directory.is_relative_to(root.resolve()):
                break
            for name in BUILD_FILES:
                build = directory / name
                observed[build.relative_to(root.resolve()).as_posix()] = file_digest(source_path(root, build.relative_to(root.resolve()).as_posix()))
    return observed


class FactStore:
    def __init__(self, workspace: Path, feature: str, root: Path):
        self.workspace, self.feature = workspace, feature
        self.root = root.resolve()
        self.repository = repository_id(self.root)
        self.base = feature_dir(workspace, feature) / ".engineering" / "facts" / self.repository

    def note(self, event: str, *, owner: str = "", fact_id: str = "", reason: str = "", query_key: str = "") -> None:
        from hooks.engineering_observability import observe
        parts = owner.split(":", 2)
        observe(self.workspace, self.feature, self.repository, event, factId=fact_id,
                runId=parts[0] if parts else "", batchId=parts[1] if len(parts) > 1 else "",
                reasonCode=reason, queryKey=query_key)

    def request(self, *args, **kwargs) -> dict:
        result = self._request(*args, **kwargs)
        event = {"hit": "fact_hit", "cached_failure": "fact_cached_failure", "in_progress": "fact_wait",
                 "claimed": "fact_takeover" if result.get("takenOver") else "fact_claimed"}[result["status"]]
        symbol = args[0] if args else kwargs.get("symbol")
        kind = args[1] if len(args) > 1 else kwargs.get("kind")
        sources = args[2] if len(args) > 2 else kwargs.get("sources")
        self.note(event, owner=kwargs.get("owner", ""), fact_id=result["factId"],
                  query_key=digest({"symbol": symbol, "kind": kind, "paths": sorted(sources or [])}))
        return result

    def publish(self, fact_id: str, claim: str, **kwargs) -> dict:
        try:
            result = self._publish(fact_id, claim, **kwargs)
        except ValueError as exc:
            self.note("fact_publish_rejected", owner=kwargs.get("owner", ""), fact_id=fact_id, reason=str(exc))
            raise
        self.note("fact_failed" if kwargs.get("failure") else "fact_published", owner=kwargs.get("owner", ""), fact_id=fact_id)
        return result

    def _read(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and value.get("schemaVersion") == SCHEMA else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    def valid(self, fact: dict) -> bool:
        if fact.get("schemaVersion") != SCHEMA or fact.get("repositoryId") != self.repository:
            return False
        if fact.get("status") not in {"ready", "failed"} or not isinstance(fact.get("sources"), dict):
            return False
        if not isinstance(fact.get("content"), dict):
            return False
        expiry = fact.get("expiresEpoch")
        if expiry is not None and (not isinstance(expiry, (int, float)) or time.time() >= expiry):
            return False
        if fact.get("kind") in {"test_recipe", "capability_result"} and fact.get("scope") != environment_scope():
            return False
        try:
            source_valid = bool(fact["sources"]) and all(file_digest(source_path(self.root, path)) == sha
                                                         for path, sha in fact["sources"].items())
            deadline = time.monotonic() + 30
            return source_valid and all(artifact_fingerprint(Path(item["path"]), self.base.parent / "dependency-fingerprints", deadline) == item["sha256"]
                                        for item in fact.get("dependencies", []))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _request(self, symbol: str, kind: str, sources: list[str], *, owner: str,
                scope: str = "", ttl: int = 120, classpath_file: str | None = None,
                source_only: bool = False) -> dict:
        if kind not in KINDS or not symbol.strip() or len(symbol) > 512:
            raise ValueError("engineering_invalid_query")
        dependencies = []
        if classpath_file:
            snapshot = source_path(self.root, classpath_file)
            data = snapshot.read_text(encoding="utf-8-sig").strip()
            separator = ";" if ";" in data or re.match(r"^[A-Za-z]:[\\/]", data) else os.pathsep
            jars = [Path(p) for p in data.split(separator) if p]
            if len(jars) > 512 or any(not p.is_absolute() or p.suffix.lower() not in {".jar", ".zip"} for p in jars):
                raise ValueError("engineering_classpath_requires_bounded_absolute_jars")
            sources = [*sources, classpath_file]
            deadline = time.monotonic() + 30
            dependencies = [{"path": str(p), "sha256": artifact_fingerprint(p, self.base.parent / "dependency-fingerprints", deadline)} for p in jars]
        if source_only:
            if kind != "api_signature" or classpath_file:
                raise ValueError("engineering_source_only_requires_declarations")
            observed = {p: file_digest(source_path(self.root, p)) for p in sources}
        else:
            observed = provenance(self.root, sources)
        key = digest({"repositoryId": self.repository, "symbol": symbol, "kind": kind,
                      "scope": scope, "sources": observed, "dependencies": dependencies,
                      "sourceOnly": source_only})
        path = self.base / (key + ".json")
        with FileLock(self.base / (key + ".lock")):
            old = self._read(path)
            if self.valid(old):
                return {"status": "hit" if old["status"] == "ready" else "cached_failure",
                        "factId": key, "fact": old, "factPath": str(path)}
            now = time.time()
            if old.get("status") == "pending" and isinstance(old.get("claimExpiresEpoch"), (int, float)) and now < old["claimExpiresEpoch"] <= now + 300:
                return {"status": "in_progress", "factId": key, "factPath": str(path),
                        "retryAfterSeconds": min(5, max(1, int(old["claimExpiresEpoch"] - now))),
                        "claimExpiresEpoch": old["claimExpiresEpoch"]}
            claim = uuid.uuid4().hex
            value = {"schemaVersion": SCHEMA, "factId": key, "repositoryId": self.repository,
                     "symbol": symbol, "kind": kind, "scope": scope, "sources": observed,
                     "querySources": sorted(set(sources)),
                     "sourceOnly": source_only,
                     "dependencies": dependencies,
                     "status": "pending", "owner": owner, "claimToken": claim,
                     "claimExpiresEpoch": now + max(1, min(ttl, 300)), "createdEpoch": now}
            atomic_write_json(path, value)
            return {"status": "claimed", "factId": key, "claimToken": claim,
                    "takenOver": old.get("status") == "pending",
                    "factPath": str(path), "sources": observed}

    def lookup(self, symbol: str, kind: str = "api_signature", sources: list[str] | None = None) -> dict:
        """A genuinely read-only lookup, including before TASK start/Review."""
        matches = []
        for index, path in enumerate(sorted(self.base.glob("*.json"))):
            if index >= MAX_FACTS:
                break
            fact = self._read(path)
            if (fact.get("symbol") == symbol and fact.get("kind") == kind
                    and (not sources or set(sources) <= set(fact.get("querySources", [])))
                    and self.valid(fact)):
                matches.append({**{k: v for k, v in fact.items() if k not in {"claimToken", "owner"}},
                                "factPath": str(path)})
        status = "hit" if any(fact["status"] == "ready" for fact in matches) else "cached_failure" if matches else "missing"
        return {"status": status, "facts": matches[:12],
                "readOnly": True, "nextAction": "reuse_fact_with_provenance" if matches else "resolve_exact_missing_sources"}

    def close_claims(self, owner: str) -> list[str]:
        """Do not silently abandon a claimed semantic exploration at finish."""
        closed = []
        for index, path in enumerate(sorted(self.base.glob("*.json"))):
            if index >= MAX_FACTS:
                break
            if self._read(path).get("owner") != owner:
                continue
            with FileLock(path.with_suffix(".lock")):
                fact = self._read(path)
                if fact.get("owner") != owner or fact.get("status") != "pending":
                    continue
                fact.update(status="failed", content={"reasonCode": "exploration_unpublished_at_closure"},
                            verification="source_observed", publishedEpoch=time.time(), expiresEpoch=time.time() + 60)
                fact.pop("claimToken", None)
                atomic_write_json(path, fact)
                closed.append(fact["factId"])
            self.note("fact_unpublished", owner=owner, fact_id=fact["factId"], reason="exploration_unpublished_at_closure")
        return closed

    def record_declarations(self, snapshots: dict, owner: str) -> dict:
        """Publish bounded source excerpts only while completion hashes match."""
        facts, gaps = [], []
        suffixes = {".java", ".py", ".js", ".jsx", ".ts", ".tsx", ".kt", ".go", ".rs", ".cs", ".cpp", ".h", ".vue"}
        for relative, sha in list(snapshots.items())[:64]:
            try:
                path = source_path(self.root, relative)
                if path.suffix not in suffixes:
                    gaps.append({"source": relative, "reasonCode": "declaration_format_unsupported"})
                    continue
                if not sha or file_digest(path) != sha:
                    gaps.append({"source": relative, "reasonCode": "completion_snapshot_no_longer_current"})
                    continue
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    raise ValueError("engineering_source_too_large_or_not_file")
                text = path.read_text(encoding="utf-8")
                package = re.search(r"(?m)^\s*package\s+([\w.]+)", text)
                names = re.findall(r"\b(?:class|interface|enum|record)\s+([\w$]+)", text)[:8] or [path.stem]
                for name in names:
                    symbol = ((package.group(1) + ".") if package else "") + name
                    claim = self.request(symbol, "api_signature", [relative], owner=owner,
                                         scope="source_declarations_v1", source_only=True)
                    if claim["status"] == "claimed":
                        claim = self.publish(claim["factId"], claim["claimToken"], owner=owner,
                                             content=signature_excerpt(self.root, [relative], symbol))
                    if claim["status"] in {"ready", "hit"}:
                        facts.append(claim["factId"])
                    else:
                        gaps.append({"source": relative, "reasonCode": "declaration_producer_in_progress"})
            except (OSError, ValueError, UnicodeError) as exc:
                gaps.append({"source": relative, "reasonCode": str(exc)})
        return {"factIds": sorted(set(facts)), "gaps": gaps,
                "unpublishedClaimIds": self.close_claims(owner)}

    def _publish(self, fact_id: str, claim: str, *, owner: str, content: dict,
                failure: bool = False, transient: bool = False) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", fact_id):
            raise ValueError("engineering_invalid_fact_id")
        if not isinstance(content, dict) or not content or len(json.dumps(content, ensure_ascii=False)) > 32768:
            raise ValueError("engineering_invalid_fact_content")
        path = self.base / (fact_id + ".json")
        with FileLock(self.base / (fact_id + ".lock")):
            fact = self._read(path)
            if (fact.get("status") != "pending" or fact.get("claimToken") != claim
                    or fact.get("owner") != owner or fact.get("claimExpiresEpoch", 0) <= time.time()):
                raise ValueError("engineering_claim_expired_or_replaced")
            if not all(file_digest(source_path(self.root, p)) == sha for p, sha in fact["sources"].items()):
                raise ValueError("engineering_sources_changed_during_query")
            deadline = time.monotonic() + 30
            if any(artifact_fingerprint(Path(item["path"]), self.base.parent / "dependency-fingerprints", deadline) != item["sha256"] for item in fact.get("dependencies", [])):
                raise ValueError("engineering_dependencies_changed_during_query")
            fact.update(status="failed" if failure else "ready", content=content,
                        verification="source_observed", publishedEpoch=time.time(),
                        expiresEpoch=time.time() + 60 if transient else None)
            fact.pop("claimToken", None)
            atomic_write_json(path, fact)
            return {"status": fact["status"], "factId": fact_id, "factPath": str(path), "fact": fact}

    def related(self, paths: list[str], symbols: list[str] | None = None, limit: int = 12) -> list[dict]:
        """Read only this repository's bounded fact directory, no business scan."""
        result = []
        for index, path in enumerate(sorted(self.base.glob("*.json"))):
            if index >= MAX_FACTS:
                break
            fact = self._read(path)
            if self.valid(fact) and (set(paths) & set(fact.get("querySources", [])) or
                                    any(fact.get("symbol", "").split("#", 1)[0] == s for s in (symbols or []))):
                public = {key: value for key, value in fact.items() if key not in {"claimToken", "owner"}}
                public["factPath"] = str(path)
                result.append(public)
                if len(result) >= limit:
                    break
        return result

    def attach_test_proof(self, fact_id: str, expected_sources: dict, response: dict) -> None:
        """Only called by the runner after real, bound test registration.

        This proves execution of the recipe's referenced test file, not branch
        coverage or general correctness of a model's prose.
        """
        if not re.fullmatch(r"[a-f0-9]{64}", fact_id):
            raise ValueError("engineering_invalid_fact_id")
        path = self.base / (fact_id + ".json")
        with FileLock(self.base / (fact_id + ".lock")):
            fact = self._read(path)
            if (not self.valid(fact) or fact.get("status") != "ready" or fact.get("sources") != expected_sources
                    or fact.get("kind") not in {"test_recipe", "capability_result"}):
                return
            if (response.get("result") != "PASS" or response.get("exitCode") != 0
                    or response.get("logExitCode") != 0 or not response.get("evidenceId")
                    or not response.get("testsExecuted")):
                return
            fact.update(verification="referenced_test_executed", testProof={k: response.get(k) for k in
                        ("evidenceId", "commandId", "targetId", "testsExecuted", "cwd")})
            fact["testProof"]["dependencyScope"] = "resolved_classpath" if fact.get("dependencies") else "build_inputs_only"
            fact["testProof"].update({k: response.get(k) for k in ("runId", "batchId", "taskId")})
            proofs = fact.setdefault("testProofs", {})
            proofs[digest({k: response.get(k) for k in ("runId", "batchId", "taskId", "evidenceId", "commandId", "targetId")})] = fact["testProof"]
            atomic_write_json(path, fact)


def locate(root: Path, symbol: str, module: str = ".") -> dict:
    """Name lookup in Git's tracked index; never read every source file."""
    scope = source_path(root, module)
    if not scope.is_dir():
        raise ValueError("engineering_module_missing")
    name = symbol.rsplit(".", 1)[-1].split("$", 1)[0]
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        raise ValueError("engineering_invalid_symbol")
    output = git(root, "ls-files", "-z", "--", module)
    candidates = [p for p in output.split("\0") if Path(p).stem == name][:16]
    return {"status": "found" if len(candidates) == 1 else "ambiguous" if candidates else "missing",
            "candidates": candidates, "symbol": symbol, "method": "tracked_filename_only"}


def signature_excerpt(root: Path, sources: list[str], symbol: str) -> dict:
    """Conservative excerpts, not an AST-derived semantic claim."""
    excerpts = []
    remaining = 20000
    omitted = []
    member = symbol.rsplit("#", 1)[-1] if "#" in symbol else None
    for relative in sources:
        if remaining <= 0:
            omitted.append(relative)
            continue
        path = source_path(root, relative)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("engineering_source_too_large_or_not_file")
        lines = path.read_text(encoding="utf-8").splitlines()
        matches = [i for i, line in enumerate(lines) if
                   (member and re.search(r"\b" + re.escape(member) + r"\s*\(", line)) or
                   (not member and re.search(r"\b(public|protected|export|class|interface|enum|def|function)\b", line))]
        declarations = []
        for i in matches[:12]:
            if remaining <= 0:
                break
            text = "\n".join(lines[max(0, i - 1):i + 5])[:min(1600, remaining)]
            remaining -= len(text)
            declarations.append({"line": i + 1, "text": text})
        excerpts.append({"path": relative, "declarations": declarations})
    return {"symbol": symbol, "excerpts": excerpts, "omittedSources": omitted,
            "truncated": remaining <= 0, "interpretation": "source excerpts; confirm overloads and semantics"}
