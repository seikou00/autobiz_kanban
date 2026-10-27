"""Conservative E2E execution inheritance, backed by an immutable environment probe.

No policy/proof means full execution. Original runs, logs and Evidence never move.
The project explicitly opts in with a committed, unchanged read-only probe policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

POLICY_FILE = '.autodev-e2e-reuse.json'
CASE_STATE = {'executions', 'evidenceIds', 'verdict', 'verdictSource', 'reason'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, timeout=10).stdout


def _source_digest(root, excluded):
    """Use tracked blob IDs, hashing only dirty/untracked content; ignore no source changes."""
    entries = {}
    for entry in _git(root, 'ls-files', '-s', '-z').split(b'\0'):
        if not entry:
            continue
        header, raw = entry.split(b'\t', 1)
        name = os.fsdecode(raw)
        if name not in excluded:
            mode, blob, stage = header.decode().split()
            if stage != '0' or mode == '120000':
                raise ValueError('unmerged source')
            entries[name] = [mode, blob]
    dirty = set(_git(root, 'diff-files', '--name-only', '-z').split(b'\0'))
    dirty.update(_git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'))
    for raw in dirty - {b''}:
        name = os.fsdecode(raw)
        if name in excluded:
            continue
        path = root / name
        if path.is_symlink():
            raise ValueError('symlink source requires full verification')
        entries[name] = [path.stat().st_mode & 0o777, hashlib.sha256(path.read_bytes()).hexdigest()] if path.is_file() else None
    return digest(entries)


def capture_binding(feature_dir, code_root, case, *, entry_url, auth_status, cache=None):
    """Return proof only when the project supplies a real immutable snapshot identity.

    A URL, config hash, model assertion or historical PASS alone cannot establish
    environment stability. The read-only provider must describe deployment/data/auth
    and an expiry; unsupported/unknown environments deliberately return None.
    """
    from hooks.e2e_trust_common import load_json_object, scan_path, validate_scan_current
    cache = {} if cache is None else cache
    root = Path(code_root).resolve()
    try:
        policy_path = root / POLICY_FILE
        if not policy_path.is_file():
            return None
        _git(root, 'ls-files', '--error-unmatch', POLICY_FILE)
        if _git(root, 'status', '--porcelain', '--', POLICY_FILE).strip():
            return None
        policy = json.loads(policy_path.read_text())
        if not isinstance(policy, dict):
            return None
        argv = policy.get('environmentProbe')
        if (policy.get('version') != 1 or policy.get('readOnly') is not True
                or not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv)):
            return None
        executable = Path(argv[0]).name.lower().removesuffix('.exe')
        if executable in {'sh', 'bash', 'zsh', 'cmd', 'powershell', 'pwsh', 'mvn', 'mvnw', 'gradle', 'gradlew'}:
            return None
        scan, errors = validate_scan_current(Path(feature_dir))
        if errors or not scan or scan.get('passed') is not True or scan.get('codeWorkspace') != str(root):
            return None
        key = (str(root), digest(policy))
        if key not in cache:
            observed = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=5, check=True)
            if len(observed.stdout) > 32768:
                return None
            cache[key] = json.loads(observed.stdout)
        proof = cache[key]
        if not isinstance(proof, dict):
            return None
        required = ('snapshotId', 'deploymentDigest', 'dataDigest', 'authDigest', 'expiresAt')
        if (proof.get('kind') != 'immutable_snapshot' or any(not isinstance(proof.get(k), str) or not proof[k] for k in required)
                or proof.get('entryUrl') != entry_url or proof.get('authStatus') != auth_status):
            return None
        expires = datetime.fromisoformat(proof['expiresAt'].replace('Z', '+00:00')).timestamp()
        if expires <= time.time():
            return None
        entries = scan.get('scannedInputs', [])
        excluded = {x['path'] for x in entries if x.get('role') == 'spec'}
        source_key = ('sources', str(root), tuple(sorted(excluded)))
        if source_key not in cache:
            cache[source_key] = _source_digest(root, excluded)
        identity = {
            'codeWorkspace': str(root), 'sourceDigest': cache[source_key],
            'caseDigest': digest({k: v for k, v in case.items() if k not in CASE_STATE}),
            'policyDigest': digest(policy), 'engineDigest': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'environment': {k: proof[k] for k in ('snapshotId', 'deploymentDigest', 'dataDigest', 'authDigest')},
            'entryUrl': entry_url, 'authStatus': auth_status,
            'clientEnvironmentDigest': digest({k: v for k, v in os.environ.items() if k not in {'_', 'PWD', 'OLDPWD', 'SHLVL'} and not k.startswith('PLAYWRIGHT_JSON_OUTPUT_')}),
            'sharedInputs': {x['path']: x['sha256'] for x in entries if x.get('role') != 'spec'},
        }
        return {'fingerprint': digest(identity), 'expiresEpoch': expires, **identity}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def binding_matches(before, after):
    return (isinstance(before, dict) and isinstance(after, dict)
            and before.get('fingerprint') == after.get('fingerprint')
            and isinstance(before.get('expiresEpoch'), (int, float))
            and isinstance(after.get('expiresEpoch'), (int, float))
            and min(before['expiresEpoch'], after['expiresEpoch']) > time.time())


def reuse_plan(feature_dir, data):
    """Derive affected cases from prior proof; caller-supplied case IDs cannot waive tests."""
    current = data.get('currentRound') or {}
    if not isinstance(current, dict):
        return {'scope': 'full', 'affectedCaseIds': [], 'inheritedExecutions': {}}
    candidates = current.get('reuseCandidates') or {}
    if not isinstance(candidates, dict):
        candidates = {}
    inherited, affected, cache = {}, [], {}
    executed = []
    round_index = current.get('index')
    pending = (list((Path(feature_dir) / 'e2e-diagnostics' / f'round-{round_index}').glob('*.pending.json'))
               if isinstance(round_index, int) and round_index > 0 else [None])
    cases = data.get('cases')
    for case in cases if isinstance(cases, list) else []:
        if not isinstance(case, dict):
            continue
        case_id = case.get('caseId')
        executions = case.get('executions') if isinstance(case.get('executions'), list) else []
        current_executions = [x for x in executions if isinstance(x, dict) and x.get('roundIndex') == round_index]
        if current_executions:
            latest = current_executions[-1]
            if latest.get('result') == 'PASS' and latest.get('gateExitCode') == 0:
                executed.append(case_id)
            else:
                affected.append(case_id)
            continue
        if pending:
            affected.append(case_id)
            continue
        run_id = candidates.get(case_id)
        execution = next((x for x in executions if isinstance(x, dict) and x.get('runId') == run_id), None)
        if execution and execution.get('result') == 'PASS' and execution.get('gateExitCode') == 0:
            before = execution.get('reuseBinding')
            after = capture_binding(feature_dir, execution.get('codeWorkspace', ''), case,
                entry_url=execution.get('entryUrl'), auth_status=execution.get('authStatus'), cache=cache)
            from hooks.e2e_trust_common import sha256_path
            root = Path(execution.get('codeWorkspace', '')).resolve()
            hashes = execution.get('specHashes')
            specs_match = isinstance(hashes, dict) and bool(hashes) and all(
                isinstance(name, str) and (root / name).resolve().is_relative_to(root)
                and (root / name).is_file() and sha256_path(root / name) == expected
                for name, expected in hashes.items())
            if (binding_matches(before, after) and specs_match and isinstance(execution.get('roundIndex'), int)
                    and isinstance(round_index, int) and execution['roundIndex'] < round_index):
                inherited[case_id] = {'runId': run_id, 'fromRound': execution['roundIndex'],
                                      'fingerprint': before['fingerprint']}
                continue
        if case.get('verdict') != 'SKIP':
            affected.append(case_id)
    return {'scope': 'affected' if inherited else 'full', 'affectedCaseIds': affected,
            'inheritedExecutions': inherited, 'executedCaseIds': executed}


def valid_inherited(feature_dir, data):
    current = data.get('currentRound') or {}
    if not isinstance(current, dict) or current.get('kind') != 'repair':
        return {}
    actual = reuse_plan(feature_dir, data)['inheritedExecutions']
    recorded = current.get('inheritedExecutions')
    if not isinstance(recorded, dict):
        return {}
    return {case_id: row for case_id, row in actual.items() if recorded.get(case_id) == row}
