"""Count sealed source additions and retain per-task attribution failures."""
import posixpath
import re
import subprocess
from pathlib import PurePosixPath

from .changes import SHA, git, source_file
from .sources import metric, rows


def normalize_file(raw, task, bindings):
    if not isinstance(raw, str):
        return []
    raw = raw.replace('\\', '/')
    refs = {r['ref'] for r in bindings}
    selected = task.get('repository')
    refs = {selected} if selected in refs else refs
    context = task.get('pathContext', {})
    roots = [(r['ref'], r['root'], '') for r in bindings]
    roots.extend((r.get('id'), r.get('path'), '') for r in rows(context.get('repositories')))
    roots.extend((r.get('repository'), r.get('requestedPath'), r.get('workspacePrefix', ''))
                 for r in rows(context.get('scopeWorkspaces')))
    if raw.startswith('/') or re.match(r'^[A-Za-z]:/', raw):
        candidates = []
        for ref, root, prefix in roots:
            if ref not in refs or not isinstance(root, str):
                continue
            root = root.replace('\\', '/').rstrip('/')
            if raw.startswith(root + '/'):
                candidates.append((ref, posixpath.join(prefix, raw[len(root) + 1:])))
        return sorted(set(candidates))
    ref, separator, path = raw.partition(':')
    if separator and ref in refs:
        return [(ref, posixpath.normpath(path.removeprefix('./')))]
    path = posixpath.normpath(raw)
    if path.startswith('../') or PurePosixPath(path).is_absolute() or len(refs) != 1:
        return []
    ref = next(iter(refs))
    candidates = {(ref, path)}
    for scope in rows(context.get('scopeWorkspaces')):
        if scope.get('repository') == ref and scope.get('workspacePrefix'):
            candidates.add((ref, posixpath.normpath(scope['workspacePrefix'] + '/' + path)))
    return sorted(candidates)


def code_lines(tasks, changes, repositories, batches=()):
    files = {(r['repository'], f['path']): f for r in changes for f in r['files']}
    matched, refs, details = set(), set(), []
    eligible = [t for t in tasks if t['delivered']]
    complete = bool(eligible) and len(eligible) == len(tasks)
    confirmed = False
    for task in eligible:
        bindings = [r for r in repositories if r['runId'] == task['deliveryRunId']]
        if task.get('repository') in {r['ref'] for r in bindings}:
            bindings = [r for r in bindings if r['ref'] == task['repository']]
        refs.update(task['sourceRefs'])
        detail = {'task': task['id'], 'batch': task['batch'], 'files': [], 'unresolved': [], 'status': 'measured'}
        details.append(detail)
        if task.get('noCodeChange') or task.get('completionMode') in {'verified_existing', 'verify', 'no_change'}:
            confirmed = True
            detail['status'] = 'verified_zero'
            continue
        paths = task.get('files') or []
        paths = list(paths) if isinstance(paths, list) else []
        for change in rows(task.get('fileChanges')):
            paths.extend(change[k] for k in ('path', 'fromPath') if isinstance(change.get(k), str))
        if not paths and len(bindings) == 1:
            batch = next((b for b in batches if b['id'] == task['batch'] and b.get('runId') == task['deliveryRunId']), {})
            base, head = batch.get('baseSha'), batch.get('commitSha')
            if all(SHA.fullmatch(str(sha)) for sha in (base, head)):
                try:
                    paths = git(bindings[0]['root'], 'diff', '--name-only', '-z', base, head, '--').decode().split('\0')
                    paths = [p for p in paths if p]
                    detail['basis'] = 'batch_delivery_diff'
                    refs.update(batch['sourceRefs'])
                    if not paths:
                        confirmed = True
                        detail['status'] = 'verified_zero'
                except (OSError, subprocess.SubprocessError):
                    pass
        if not paths and detail['status'] != 'verified_zero':
            detail['unresolved'].append({'originalPath': None, 'reason': '缺少本次实现的文件清单或可恢复的 Batch 区间'})
        invalid = [p for p in paths if not isinstance(p, str)]
        if invalid:
            detail['unresolved'].append({'originalPath': None, 'reason': '实现文件清单含无效记录'})
        for raw in sorted({p for p in paths if isinstance(p, str)}):
            candidates = normalize_file(raw, task, bindings)
            known = [key for key in candidates if key in files]
            if len(known) == 1:
                key = known[0]
                if source_file(key[1]) and not files[key]['binary']:
                    matched.add(key)
                    detail['files'].append(key[1])
                confirmed = True
            elif len(candidates) == 1 and not source_file(candidates[0][1]):
                confirmed = True
            else:
                # A task change may have been reverted before final delivery.
                unchanged = []
                for ref, path in candidates:
                    repo = next((r for r in bindings if r['ref'] == ref), None)
                    if repo is None or not SHA.fullmatch(str(repo.get('headSha'))):
                        continue
                    try:
                        result = subprocess.run(['git', '-C', str(repo['root']), 'cat-file', '-e', f'{repo["headSha"]}:{path}'],
                                                capture_output=True, timeout=25)
                    except (OSError, subprocess.SubprocessError):
                        continue
                    if result.returncode == 0:
                        unchanged.append((ref, path))
                if len(unchanged) == 1:
                    confirmed = True
                else:
                    detail['unresolved'].append({'originalPath': raw, 'candidates': [list(p) for p in candidates],
                                                 'reason': '仓库或路径无法唯一匹配封存交付版本'})
        if detail['unresolved']:
            complete = False
            detail['status'] = 'partial'
    for repository in changes:
        refs.update(repository['sourceRefs'])
    value = sum(files[key]['added'] for key in matched)
    result = metric(value if confirmed else None, refs,
                    None if complete else '部分 AI 任务的文件归属待核对，已保留可核验数量')
    result.update(basis='delivered_task_diff', details=details,
                  description='已交付 AI 任务源码相对基线的新增行，按最终交付范围去重，排除运行和生成材料。')
    return result
