"""Read-only proof for avoiding an additional production compile in UTest.

This never relaxes Code's per-TASK compile requirement or proves test execution.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


def production_fingerprint(repositories, argv):
    from hooks.task_runner import _git_snapshot, _is_transient_validation_path, _is_code_stage_test_asset_path
    from hooks.utest_test_environment import parse_pom, _projection
    sources = {}
    for name, root in repositories.items():
        entries = {}
        for relative, value in _git_snapshot(root).items():
            if _is_transient_validation_path(relative) or _is_code_stage_test_asset_path(relative):
                continue
            path = root / relative
            if path.name == 'pom.xml' and path.is_file():
                try:
                    value = _projection(parse_pom(path.read_text(encoding='utf-8-sig')))
                except (ValueError, OSError, SyntaxError):
                    pass  # Conservative raw content fingerprint for an invalid POM.
            entries[relative] = value
        sources[name] = entries
    executable = shutil.which(argv[0]) if argv else None
    binary = hashlib.sha256(Path(executable).read_bytes()).hexdigest() if executable and Path(executable).is_file() else None
    payload = {'sources': sources, 'argv': argv, 'executable': [executable, binary],
               'environment': {key: os.environ.get(key) for key in
                               ('JAVA_HOME', 'JAVA_TOOL_OPTIONS', 'MAVEN_OPTS', 'GRADLE_OPTS', 'PATH')}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compile_coverage(feature_dir, parallel_run_id, batch_id, task, worktree):
    from hooks.evidence_store import read_records, stream_path, EvidenceStoreError
    from hooks.task_run_integrity import task_run_integrity_error
    result = {'productionCompileCovered': False, 'regression': 'impact_only', 'additionalCompile': 'assess_if_needed'}
    states = []
    for path in (Path(feature_dir) / '.task-runs' / task['id']).glob('*.json'):
        try:
            state = json.loads(path.read_text())
            if state.get('parallelRunId') == parallel_run_id and state.get('batchId') == batch_id:
                states.append(state)
        except (OSError, ValueError):
            continue
    states.sort(key=lambda state: state.get('startedAt', ''), reverse=True)
    if not states:
        return result
    state = states[0]
    if state.get('status') not in {'implemented', 'done'} or task_run_integrity_error(state):
        return result
    attempt = next((x for x in state.get('compileAttempts', []) if x.get('attemptId') == state.get('latestCompileAttemptId')), {})
    if not attempt.get('productionFingerprint') or attempt.get('exitCode') != 0 or attempt.get('logExitCode') != 0:
        return result
    try:
        repositories = {row['id']: Path(row['path']) for row in state.get('repositories', [])}
        if len(repositories) != 1 or next(iter(repositories.values())).resolve() != Path(worktree).resolve():
            return result
        if attempt['productionFingerprint'] != production_fingerprint(repositories, attempt['argv']):
            return result
        evidence = next((row for row in read_records(stream_path(Path(feature_dir)))
                         if row.get('evidenceId') == attempt.get('evidenceId')
                         and row.get('taskId') == task['id'] and row.get('runId') == state.get('runId')
                         and row.get('action') == 'production_compile'), None)
        value = evidence.get('compile', {}) if evidence else {}
        if (value.get('productionFingerprint') != attempt['productionFingerprint']
                or value.get('exitCode') != 0 or value.get('logExitCode') != 0):
            return result
        log = Path(feature_dir) / value['logPath']
        if not log.resolve().is_relative_to(Path(feature_dir).resolve()) or hashlib.sha256(log.read_bytes()).hexdigest() != value.get('outputSha256'):
            return result
    except (OSError, ValueError, KeyError, EvidenceStoreError):
        return result
    return {**result, 'productionCompileCovered': True, 'additionalCompile': 'not_required',
            'compileEvidenceId': attempt['evidenceId']}
