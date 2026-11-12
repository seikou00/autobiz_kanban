#!/usr/bin/env python3
"""Cached, task-bound frontend review. No dependency installs or unconditional builds."""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.json_writer_common import atomic_write_json
from hooks.repository_snapshot import capture_file_snapshot
from hooks.resolve_frontend_html_route import FRONTEND_ROOT, feature_dir, resolve_frontend_route

SUFFIXES = {'.tsx', '.jsx', '.vue', '.ts', '.js', '.css', '.less', '.scss', '.sass', '.html'}
IGNORED = {'.frontend', '.git', 'node_modules', 'dist', 'build', 'coverage', '.cmbdevclaw', '__pycache__'}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'expected_json_object:{path}')
    return value


def report_path(fd: Path, task_id: str, run_id: str) -> Path:
    return fd / 'frontend-verification' / task_id / f'{run_id}.json'


def source_snapshot(snapshot: dict, root: Path | None = None, targets: list[str] | None = None) -> dict:
    if root is not None:
        root = root.resolve()
    files = {p: sha for p, sha in snapshot.items() if not set(Path(p).parts) & IGNORED}
    if root is None or not targets:
        return files
    # Follow local imports once; shared CSS/config always invalidate. Unknown aliases
    # fall back to the module snapshot rather than silently missing dependencies.
    for name in ('tsconfig.json', 'jsconfig.json'):
        config = root / name
        if config.is_file() and '"paths"' in config.read_text(encoding='utf-8', errors='replace'):
            return files
    selected = {p for p in files if any(p == t or p.startswith(t.rstrip('/') + '/') for t in targets)}
    selected.update(p for p in files if Path(p).suffix in {'.css', '.less', '.scss', '.sass'}
                    or p.startswith(('public/', 'static/')) or '/' not in p
                    or re.search(r'(?:^|/)(?:routes?|router|layout|main|app|providers?|theme)[/.]', p, re.I))
    queue = list(selected)
    seen = set()
    while queue:
        name = queue.pop()
        if name in seen or Path(name).suffix not in SUFFIXES:
            continue
        seen.add(name)
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        if re.search(r'''(?:import|require)\s*\(\s*[^'"\s]''', text):
            return files
        imports = re.findall(r"(?:from\s*|import\s*\(?|require\s*\()\s*['\"]([^'\"]+)['\"]", text)
        imports += re.findall(r"url\(\s*['\"]?([^'\")\s]+)", text)
        imports += re.findall(r"\bsrc=['\"](\.[^'\"]+)['\"]", text)
        for ref in imports:
            if ref.startswith('.'):
                base = path.parent / ref
            elif ref.startswith('@/'):
                base = root / 'src' / ref[2:]
            elif ref.startswith(('~', '#', '/')):
                return files
            else:
                continue
            base = Path(str(base).split('?', 1)[0].split('#', 1)[0]).resolve()
            candidates = [base, *[Path(str(base) + ext) for ext in SUFFIXES], *[base / ('index' + ext) for ext in SUFFIXES]]
            for candidate in candidates:
                if candidate.is_relative_to(root):
                    dep = candidate.relative_to(root).as_posix()
                    if dep in files and dep not in selected:
                        selected.add(dep)
                        queue.append(dep)
    return {p: files[p] for p in sorted(selected)}


@lru_cache(maxsize=1)
def asset_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_frontend_assets', FRONTEND_ROOT / 'with-absolute-html/scripts/process_assets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def asset_inventory(root: Path, route: dict):
    sources = [Path(p) for p in route.get('htmlSourcePaths', [])]
    mappings = list(route.get('_assetMappings', []))
    roots = {route['_publicRoot']} if route.get('_publicRoot') else set()
    source_names = {str(p) for p in sources}
    for path in (root / '.frontend/html-analysis').glob('*-assets-mapping.json'):
        mapping = read(path)
        if mapping.get('schemaVersion') == 2 and source_names.intersection(mapping.get('htmlSources', [])):
            mappings.extend(mapping.get('mappings', []))
            if mapping.get('publicRoot'):
                roots.add(mapping['publicRoot'])
    if len(roots) > 1:
        raise ValueError('ambiguous_asset_public_roots')
    public_root = Path(next(iter(roots))) if roots else None
    inventory = asset_module().generate_mapping_table('verify', sources, root, public_root)['mappings']
    expected = {(m.get('resolvedSource'), m.get('sourceDigest')) for m in inventory if m.get('sourceDigest')}
    mappings = list({(m.get('resolvedSource'), m.get('sourceDigest'), m.get('targetPath')): m
                     for m in mappings if (m.get('resolvedSource'), m.get('sourceDigest')) in expected}.values())
    return inventory, mappings, str(public_root) if public_root else None


def source_inputs(root: Path, route: dict) -> dict:
    # Hash only referenced sources, CSS and assets; never recursively scan sibling projects.
    files = {}
    if not route.get('htmlSourcePaths'):
        return files
    for raw in route.get('htmlSourcePaths', []):
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f'frontend_source_missing:{path}')
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    inventory, _, _ = asset_inventory(root, route)
    for asset in inventory:
        if asset.get('resolvedSource'):
            files[asset['resolvedSource']] = asset.get('sourceDigest', 'missing')
        elif asset.get('copyStatus') == 'failed':
            files[asset['originalFile']] = asset.get('error')
    return files


def asset_targets(root: Path, route: dict) -> dict:
    if not route.get('htmlSourcePaths'):
        return {}
    _, mappings, _ = asset_inventory(root, route)
    result = {}
    for item in mappings:
        relative = item.get('targetPath', '')
        target = (root / relative).resolve()
        result[relative] = (hashlib.sha256(target.read_bytes()).hexdigest()
                            if target.is_relative_to(root) and target.is_file() else 'missing')
    return result


def checker_digest() -> str:
    paths = [FRONTEND_ROOT / 'scripts' / n for n in ('review_runner.py', 'html_static_checker.py')]
    paths += [FRONTEND_ROOT / 'with-standard-html/scripts/audit_antd_coverage.py',
              FRONTEND_ROOT / 'references/frontend-policy.md',
              FRONTEND_ROOT / 'with-absolute-html/scripts/process_assets.py', ROOT / 'hooks/capture_frontend.cjs', Path(__file__)]
    return digest({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def owned_code_targets(root: Path, state: dict, snapshot: dict) -> set[str]:
    git_root = Path(state['codeWorkspace']).resolve()
    # Task file changes are relative to the repository, targets to the module.
    requested = Path(state.get('requestedCodeWorkspaces', [str(git_root)])[0]).resolve()
    scope = requested.relative_to(git_root).as_posix() if requested.is_relative_to(git_root) else '.'
    prefix = '' if scope == '.' else scope.rstrip('/') + '/'
    if state.get('status') == 'implemented' and isinstance(state.get('fileChanges'), list):
        changed = [item.get('path', '').split(':', 1)[-1] for item in state['fileChanges']]
        names = [p[len(prefix):] for p in changed if not prefix or p.startswith(prefix)]
    else:
        baseline = {p[len(prefix):]: sha for p, sha in state.get('snapshot', {}).items() if not prefix or p.startswith(prefix)}
        names = [p for p, sha in snapshot.items() if sha != baseline.get(p)]
    return {p for p in names if Path(p).suffix in SUFFIXES and not set(Path(p).parts) & IGNORED and (root / p).is_file()}


def binding(root: Path, route: dict, targets: list[str], snapshot: dict | None = None) -> dict:
    root = root.resolve()
    selected = source_snapshot(snapshot if snapshot is not None else capture_file_snapshot(root), root, targets)
    return {'codeDigest': digest(selected),
            'sourceDigest': digest(source_inputs(root, route)), 'checkerDigest': checker_digest(),
            'assetDigest': digest(asset_targets(root, route)),
            'targets': targets, 'route': route.get('route'), 'htmlSourcePaths': route.get('htmlSourcePaths', [])}


def validate_capture(captured: dict, binding_digest: str, source_count: int) -> None:
    screenshots = captured.get('screenshots', [])
    if captured.get('bindingDigest') != binding_digest or len(screenshots) < 1 + source_count or captured.get('errors'):
        raise ValueError('frontend_visual_capture_missing_or_failed')
    for screenshot in screenshots:
        path = Path(screenshot['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != screenshot.get('sha256'):
            raise ValueError('frontend_screenshot_missing_or_changed')


def assert_current(fd: Path, task: dict, state: dict, root: Path, snapshot: dict | None = None) -> dict:
    root = root.resolve()
    if not state.get('frontendVerificationVersion'):
        return {}  # Frozen legacy run policy; never invent passing evidence.
    result = read(report_path(fd, task['id'], state['runId']))
    if result.get('status') not in {'passed', 'has-suggestions'}:
        raise ValueError('frontend_verification_missing_or_failed:run frontend_verification.py for current TASK/run')
    route = resolve_frontend_route(fd.parents[2], fd.name, task_id=task['id'])
    route['_assetMappings'] = result.get('assets', {}).get('verifiedMappings', [])
    route['_publicRoot'] = result.get('assets', {}).get('publicRoot')
    if snapshot is not None and root != Path(state['codeWorkspace']).resolve():
        prefix = root.relative_to(Path(state['codeWorkspace']).resolve()).as_posix().rstrip('/') + '/'
        snapshot = {p[len(prefix):]: sha for p, sha in snapshot.items() if p.startswith(prefix)}
    snapshot = snapshot if snapshot is not None else capture_file_snapshot(root)
    targets = result.get('binding', {}).get('targets', [])
    covered = source_snapshot(snapshot, root, targets)
    if not owned_code_targets(root, state, snapshot).issubset(covered):
        raise ValueError('frontend_verification_stale:current TASK has unreviewed source files')
    expected = binding(root, route, targets, snapshot)
    if result.get('binding') != expected or result.get('taskContractSha256') != state.get('taskContractSha256'):
        raise ValueError('frontend_verification_stale:source/input/checker changed; rerun current TASK review')
    from hooks.plan_json import task_contract_sha256
    if task.get('goal') is not None and task_contract_sha256(task) != state.get('taskContractSha256'):
        raise ValueError('frontend_verification_task_contract_changed')
    if result.get('taskId') != task['id'] or result.get('taskRunId') != state['runId']:
        raise ValueError('frontend_verification_run_mismatch')
    if route.get('htmlSourcePaths') and result.get('visual', {}).get('status') == 'passed':
        validate_capture(result.get('capture', {}), digest(expected), len(route['htmlSourcePaths']))
    return result


def check_script(root: Path, name: str, timeout: int) -> dict:
    # Named project scripts only. Disallow arbitrary shell, lifecycle hooks and publishing.
    import shlex
    package = read(root / 'package.json')
    scripts = package.get('scripts', {})
    if name not in {'build', 'typecheck', 'check:types'} or not isinstance(scripts.get(name), str):
        raise ValueError('unsupported_frontend_check_script')
    command = scripts[name]
    if any(c in command for c in ';|&`$<>\n') or any(scripts.get(prefix + name) for prefix in ('pre', 'post')):
        raise ValueError('frontend_check_requires_simple_compiler_script')
    argv = shlex.split(command)
    if not argv or Path(argv[0]).name not in {'tsc', 'vue-tsc', 'vite', 'webpack', 'next'}:
        raise ValueError('frontend_check_not_a_compiler')
    if Path(argv[0]).name in {'vite', 'webpack', 'next'} and 'build' not in argv and Path(argv[0]).name != 'webpack':
        raise ValueError('frontend_check_not_build')
    executable = root / 'node_modules' / '.bin' / (argv[0] + ('.cmd' if os.name == 'nt' else ''))
    if not executable.is_file():
        raise ValueError(f'frontend_compiler_unavailable:{argv[0]}')
    proc = subprocess.run([str(executable), *argv[1:]], cwd=root, capture_output=True, text=True, timeout=timeout)
    return {'status': 'passed' if proc.returncode == 0 else 'failed', 'argv': argv,
            'exitCode': proc.returncode, 'output': proc.stdout + proc.stderr}


def stop_process(process) -> None:
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def capture(root: Path, output: Path, args, sources: list[str], public_root: str | None = None) -> dict:
    url = args.url
    server = None
    log = None
    try:
        if args.preview_script:
            import shlex
            script = read(root / 'package.json').get('scripts', {}).get(args.preview_script, '')
            argv = shlex.split(script)
            if any(c in script for c in ';|&`$<>\n') or not argv or argv[0] not in {'vite', 'next'}:
                raise ValueError('preview_requires_existing_simple_vite_or_next_script')
            if 'build' in argv:
                raise ValueError('preview_script_is_not_a_server')
            import socket
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            executable = root / 'node_modules/.bin' / (argv[0] + ('.cmd' if os.name == 'nt' else ''))
            extra = ['--host', '127.0.0.1', '--port', str(port), '--strictPort'] if argv[0] == 'vite' else ['--hostname', '127.0.0.1', '--port', str(port)]
            log = (output / 'preview.log').open('w', encoding='utf-8')
            server = subprocess.Popen([str(executable), *argv[1:], *extra], cwd=root, stdout=log, stderr=log,
                                      start_new_session=os.name != 'nt')
            url = f'http://127.0.0.1:{port}{args.page_path}'
        parsed = urlsplit(url or '')
        if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('preview_url_must_be_loopback')
        import urllib.request
        deadline = time.monotonic() + min(args.timeout, 30)
        while True:
            try:
                with urllib.request.urlopen(url, timeout=1):
                    break
            except Exception:
                if time.monotonic() >= deadline or (server and server.poll() is not None):
                    raise ValueError('frontend_preview_not_ready:see preview.log')
                time.sleep(.2)
        command = ['node', str(ROOT / 'hooks/capture_frontend.cjs'), str(root), url, str(output),
                   str(args.width), str(args.height), json.dumps({'sources': sources, 'publicRoot': public_root})]
        renderer = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=os.name != 'nt')
        try:
            stdout, stderr = renderer.communicate(timeout=args.timeout)
        finally:
            if renderer.poll() is None:
                stop_process(renderer)
        if renderer.returncode:
            raise ValueError('frontend_capture_failed:' + (stderr or stdout)[-2000:])
        return json.loads(stdout)
    finally:
        if server:
            stop_process(server)
        if log:
            log.close()


def asset_status(root: Path, route: dict) -> dict:
    if not route.get('htmlSourcePaths'):
        return {'status': 'not-applicable'}
    inventory, mappings, public_root = asset_inventory(root, route)
    errors = []
    for asset in inventory:
        if asset.get('decision') != 'copyToProject':
            continue
        found = next((item for item in mappings if item.get('resolvedSource') == asset.get('resolvedSource')
                      and item.get('sourceDigest') == asset.get('sourceDigest') and item.get('copyStatus') == 'verified'), None)
        if asset.get('copyStatus') == 'failed' or not found:
            errors.append({'source': asset['originalFile'], 'reason': asset.get('error', 'current_asset_mapping_missing')})
            continue
        target = (root / found['targetPath']).resolve()
        if not target.is_relative_to(root) or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != found.get('targetDigest', found.get('sourceDigest')):
            errors.append({'source': asset['originalFile'], 'reason': 'copied_asset_missing_or_changed'})
    return {'status': 'failed' if errors else 'passed', 'errors': errors,
            'verifiedMappings': mappings, 'publicRoot': public_root}


def publish_route_summary(args, result: dict) -> None:
    """Keep the existing code_done gate connected without replacing per-task proof."""
    if args.parallel_run_id:
        from hooks.parallel_runtime import load_manifest, run_lock, save_manifest
        with run_lock(args.workspace, args.feature, args.parallel_run_id):
            manifest = load_manifest(args.workspace, args.feature, args.parallel_run_id)
            issue_id = f'FRONTEND-VISUAL-{args.task_id}'
            issues = [issue for issue in manifest.get('deferredIssues', []) if issue.get('issueId') != issue_id]
            for advisory in result.get('advisories', []):
                issues.append({'issueId': issue_id, 'kind': advisory['type'], 'batchId': args._frontend_batch_id,
                               'stage': 'implement', 'message': f"{args.task_id}: {advisory['message']}",
                               'disposition': 'recorded_advisory', 'blocksWorkflow': False, 'status': 'open'})
            manifest['deferredIssues'] = issues
            save_manifest(args.workspace, args.feature, args.parallel_run_id, manifest)
    from hooks.resolve_frontend_html_route import evidence_path, mark_evidence
    route = read(evidence_path(args.workspace, args.feature))
    if not route or route.get('route') != result['binding']['route']:
        return
    owners = route.get('taskSourceBindings', {})
    if owners and args.task_id not in owners:
        return
    status = result['status'] if result['status'] in {'passed', 'has-suggestions'} else 'failed'
    mark_evidence(args.workspace, args.feature, review_status=status)


def _verify(args) -> dict:
    from hooks import task_runner as tr
    fd = feature_dir(args.workspace, args.feature)
    _, batch, task = tr._load_plan_and_task(fd, args.task_id, require_active_batch=False)
    args._frontend_batch_id = batch
    _, state = tr._load_run(fd, args.task_id, args.run_id)
    root = args.code_workspace.resolve()
    if args.candidate_wave is not None:
        raise ValueError('frontend_candidate_recheck_retired:retry delivery with the current Workflow')
    if args.parallel_run_id:
        from hooks.parallel_runtime import load_manifest
        owner = load_manifest(args.workspace, args.feature, args.parallel_run_id)['batches'][batch]
        if owner.get('activeStage') not in {None, 'none', 'implement'}:
            raise ValueError('frontend_verification_not_allowed_during_review')
    tr._assert_parallel_context(args.workspace, args.feature, args.parallel_run_id, batch, args.lease_token, root)
    if state.get('parallelRunId') != args.parallel_run_id or state.get('taskId') != args.task_id:
        raise ValueError('frontend_task_run_binding_mismatch')
    if str(root) not in state.get('requestedCodeWorkspaces', [state.get('codeWorkspace')]) or state.get('status') not in {'started', 'in_progress', 'implementation_recording', 'implemented'}:
        raise ValueError('frontend_task_not_active_or_workspace_mismatch')
    if task.get('uiRequired') is not True:
        raise ValueError('frontend_verification_requires_ui_task')
    route = resolve_frontend_route(args.workspace, args.feature, task_id=args.task_id)
    path = report_path(fd, args.task_id, args.run_id)
    previous = read(path)
    route['_assetMappings'] = previous.get('assets', {}).get('verifiedMappings', [])
    route['_publicRoot'] = previous.get('assets', {}).get('publicRoot')
    snapshot = capture_file_snapshot(root)
    targets = sorted({(root / t).resolve().relative_to(root).as_posix() for t in (args.target or ['src'])} | owned_code_targets(root, state, snapshot))
    for target in targets:
        target_path = (root / target).resolve()
        if not target_path.is_relative_to(root) or not target_path.exists():
            raise ValueError(f'frontend_target_invalid:{target}')
    current = binding(root, route, targets, snapshot)
    result = {'schemaVersion': 1, 'taskId': args.task_id, 'taskRunId': args.run_id,
              'taskContractSha256': state.get('taskContractSha256'), 'binding': current,
              'reportPath': str(path), 'bindingDigest': digest(current), 'status': 'failed', 'visual': {'status': 'not-run'},
              'fidelityStatus': 'not-run' if route.get('htmlSourcePaths') else 'not-applicable',
              'implementationMode': 'html-fidelity' if route.get('htmlSourcePaths') else 'spec-driven',
              'projectCheck': {'status': 'not-run'},
              'inputGaps': route.get('requiredVisualSourceMissing', []) or route.get('missingHtmlSourcePaths', [])}
    args._active_frontend_report = (path, result)
    result['assets'] = asset_status(root, route)
    same = previous.get('binding') == current
    if same:
        result.update({key: previous[key] for key in ('static', 'projectCheck', 'capture', 'visual') if key in previous})
    # A new operation revokes the prior verdict until it has actually completed.
    result['status'] = 'running'
    atomic_write_json(path, result)
    if result.get('static', {}).get('status') != 'passed':
        args._frontend_operation = 'static'
        reports = []
        for target in targets:
            # Each original fragment is reviewed; omit unrelated whole-feature Plan text.
            for source in route.get('htmlSourcePaths') or [None]:
                argv = [sys.executable, '-X', 'utf8', '-B', str(FRONTEND_ROOT / 'scripts/review_runner.py'), '--target', str(root / target), '--format', 'json']
                if source:
                    argv += ['--source-html', source]
                proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                                      env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}, timeout=args.timeout)
                if proc.returncode not in {0, 1}:
                    raise ValueError('frontend_static_execution_failed:' + (proc.stderr or proc.stdout)[-2000:])
                reports.append(json.loads(proc.stdout))
        findings = [f for r in reports for f in r.get('findings', [])]
        result['static'] = {'status': 'failed' if any(f.get('severity') == 'must-fix' for f in findings) else 'passed', 'reports': reports}
    if args.check_script:
        args._frontend_operation = 'projectCheck'
        if not same or result.get('projectCheck', {}).get('script') != args.check_script or result.get('projectCheck', {}).get('status') != 'passed':
            result['projectCheck'] = {**check_script(root, args.check_script, args.timeout), 'script': args.check_script}
    if args.capture:
        args._frontend_operation = 'capture'
        recipe = {'url': args.url, 'previewScript': args.preview_script, 'pagePath': args.page_path, 'width': args.width, 'height': args.height}
        if not same or 'capture' not in result or result['capture'].get('recipe') != recipe or result['capture'].get('errors') or result.get('visual', {}).get('status') == 'failed':
            output = path.parent / (args.run_id + '-capture')
            output.mkdir(parents=True, exist_ok=True)
            try:
                result['capture'] = capture(root, output, args, route.get('htmlSourcePaths', []), result['assets'].get('publicRoot'))
                result['capture']['bindingDigest'] = digest(current)
                result['capture']['recipe'] = recipe
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                if isinstance(exc, ValueError) and not str(exc).startswith(('frontend_capture_failed:', 'frontend_preview_not_ready:')):
                    raise
                result['capture'] = {'status': 'unavailable', 'error': str(exc)}
                if same and previous.get('capture', {}).get('errors'):
                    result['capture']['errors'] = previous['capture']['errors']
            result['visual'] = {'status': 'not-run'}
    if args.actual_screenshot:
        args._frontend_operation = 'capture'
        files = [args.actual_screenshot, *(args.reference_screenshot or [])]
        if len(files) < 1 + len(route.get('htmlSourcePaths', [])):
            raise ValueError('all_original_html_screenshots_required')
        saved = []
        output = path.parent / (args.run_id + '-capture')
        output.mkdir(parents=True, exist_ok=True)
        for index, screenshot in enumerate(files):
            content = screenshot.read_bytes()
            if not content.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('frontend_screenshot_requires_png')
            target = output / f'external-{index}.png'
            if screenshot.resolve() != target.resolve():
                shutil.copyfile(screenshot, target)
            saved.append({'path': str(target), 'sha256': hashlib.sha256(content).hexdigest()})
        result['capture'] = {'bindingDigest': digest(current), 'errors': [], 'method': 'external-browser', 'screenshots': saved}
        result['visual'] = {'status': 'not-run'}
    if args.visual_report:
        args._frontend_operation = 'visual'
        visual = read(args.visual_report)
        captured = result.get('capture', {})
        if not captured or visual.get('bindingDigest') != digest(current):
            raise ValueError('frontend_visual_review_binding_missing_or_stale')
        validate_capture(captured, digest(current), len(route.get('htmlSourcePaths', [])))
        if visual.get('decision') not in {'passed', 'failed'} or not isinstance(visual.get('findings'), list):
            raise ValueError('frontend_visual_review_requires_decision_and_findings')
        if captured.get('errors') or any(f.get('severity') == 'must-fix' for f in visual['findings']):
            visual['decision'] = 'failed'
        result['visual'] = {'status': visual['decision'], 'findings': visual['findings']}
    # Source/resource edits during validation invalidate the entire attempt.
    if current != binding(root, route, targets):
        raise ValueError('frontend_source_changed_during_verification')
    # A retry without a new passing review cannot erase a known visual defect.
    if same and previous.get('visual', {}).get('status') == 'failed' and result['visual']['status'] != 'passed':
        result['visual'] = previous['visual']
    must_fix = result['assets']['status'] == 'failed' or result['static']['status'] == 'failed' or result.get('projectCheck', {}).get('status') in {'failed', 'blocked'}
    needs_visual = bool(route.get('htmlSourcePaths'))
    if result.get('visual', {}).get('status') == 'failed' or result.get('capture', {}).get('errors'):
        must_fix = True
    result['advisories'] = []
    if (needs_visual and result['visual']['status'] == 'not-run') or result.get('capture', {}).get('status') == 'unavailable':
        reason = result.get('capture', {}).get('error') or '未提供实际页面与原始 HTML 的视觉核对结果'
        result['advisories'].append({'type': 'frontend_visual_unverified', 'message': reason, 'blocksWorkflow': False})
    if any(result.get(k, {}).get('status') == 'blocked' for k in ('projectCheck', 'capture', 'visual')):
        result['status'] = 'blocked'
    elif must_fix:
        result['status'] = 'failed'
    elif needs_visual and result['visual']['status'] != 'passed':
        result['status'] = 'has-suggestions'
    else:
        suggestion = bool(result['advisories']) or any(r.get('summary', {}).get('suggestion', 0) for r in result['static']['reports'])
        result['status'] = 'has-suggestions' if suggestion else 'passed'
    result['fidelityStatus'] = result['visual']['status'] if needs_visual else 'not-applicable'
    if needs_visual and result['visual']['status'] == 'not-run':
        result['fidelityStatus'] = 'unverified'
    result['cacheHit'] = same
    atomic_write_json(path, result)
    publish_route_summary(args, result)
    return result


def verify(args) -> dict:
    args._active_frontend_report = None
    args._frontend_operation = 'assets'
    try:
        return _verify(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        active = args._active_frontend_report
        if active:
            path, result = active
            operation = args._frontend_operation
            result['status'] = 'blocked'
            result['lastError'] = str(exc)
            result[operation] = {'status': 'blocked', 'error': str(exc)}
            if operation == 'capture':
                result['visual'] = {'status': 'not-run'}
            atomic_write_json(path, result)
            publish_route_summary(args, result)
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    for arg in ('workspace', 'code-workspace'):
        ap.add_argument('--' + arg, type=Path, required=True)
    for arg in ('feature', 'task-id', 'run-id'):
        ap.add_argument('--' + arg, required=True)
    for arg in ('parallel-run-id', 'lease-token', 'workspace-ref'):
        ap.add_argument('--' + arg)
    ap.add_argument('--candidate-wave', type=int, help='Retired candidate recheck option')
    ap.add_argument('--target', action='append')
    ap.add_argument('--check-script', help='Optional existing build/typecheck script; never installed automatically')
    ap.add_argument('--capture', action='store_true')
    ap.add_argument('--preview-script')
    ap.add_argument('--page-path', default='/')
    ap.add_argument('--url')
    ap.add_argument('--width', type=int, default=1440)
    ap.add_argument('--height', type=int, default=900)
    ap.add_argument('--actual-screenshot', type=Path, help='Existing browser-tool screenshot, captured for this source revision')
    ap.add_argument('--reference-screenshot', type=Path, action='append')
    ap.add_argument('--visual-report', type=Path)
    ap.add_argument('--timeout', type=int, default=60)
    args = ap.parse_args()
    def cancelled(signum, frame):
        raise InterruptedError('frontend_verification_cancelled')
    signal.signal(signal.SIGTERM, cancelled)
    if not 1 <= args.timeout <= 120 or not 100 <= args.width <= 4096 or not 100 <= args.height <= 4096:
        ap.error('timeout 1..120 seconds; viewport 100..4096')
    try:
        result = verify(args)
        # Large findings stay on disk; agent reads the report only when needed.
        summary = {k: v for k, v in result.items() if k not in {'static', 'capture', 'assets', 'projectCheck'}}
        summary['projectCheckStatus'] = result.get('projectCheck', {}).get('status', 'not-run')
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if result['status'] in {'failed', 'blocked'} else 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
