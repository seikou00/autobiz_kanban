"""Project retained integration events separately from Batch code defects."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .sources import canonical, digest


def read_integration(src, run):
    manifest, ref = run['manifest'], run['ref']
    prefix = Path(ref).parent.as_posix()
    trains = manifest.get('mergeTrains') or {}
    trains = trains if isinstance(trains, dict) else {}
    repositories = manifest.get('repositories', {})
    events_ref = prefix + '/events.jsonl'
    conflicts = {}
    for line, raw in enumerate((src.read(events_ref) or '').splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError('集成事件应为对象')
        except ValueError as exc:
            src.gap(events_ref, f'集成事件 L{line} 无法解析：{exc}')
            continue
        if event.get('event') not in {'merge_train_conflicted', 'merge_train_conflict_resolved'}:
            continue
        repo, wave = event.get('repositoryRef'), event.get('wave')
        if (not isinstance(repo, str) or repo not in repositories or not isinstance(wave, int)
                or isinstance(wave, bool) or wave < 1):
            src.gap(events_ref, f'集成事件 L{line} 缺少有效仓库或候选序号')
            continue
        key = f'{repo}:wave-{wave:03d}'
        train = trains.get(key) or {}
        item = conflicts.setdefault(key, {
            'id': manifest['runId'] + ':' + key + ':conflict', 'kind': 'merge_conflict',
            'runId': manifest['runId'], 'repository': repo, 'wave': wave,
            'batches': [], 'files': [], 'status': 'blocked', 'startedAt': None,
            'resolvedAt': None, 'candidateSha': None, 'history': [], 'sourceRefs': [ref, events_ref],
        })
        item['history'].append({**event, 'line': line})
        if event['event'] == 'merge_train_conflicted':
            item.update(status='blocked', resolvedAt=None, candidateSha=None)
            item['startedAt'] = item['startedAt'] or event.get('at')
            item['files'] = sorted(set(item['files']) | {p for p in (event.get('conflictedFiles') or []) if isinstance(p, str)})
            item['batches'] = sorted(set(item['batches']) | {b for b in (event.get('batchIds') or []) if isinstance(b, str)})
        else:
            item.update(status='conflict_resolved', resolvedAt=event.get('at'), candidateSha=event.get('candidateSha'))
        item['batches'] = item['batches'] or train.get('batchIds', [])
        item['reviewedCandidateSha'] = train.get('reviewedCandidateSha')
        item['active'] = train.get('status') not in {'promoted', 'discarded', 'stale'}
    groups = {}
    for path in sorted(src.root.glob(prefix + '/candidate-reviews/*.json')):
        name = path.relative_to(src.root).as_posix()
        rec = src.obj(name)
        facts = {k: v for k, v in rec.items() if k not in {'reviewId', 'recordedAt', 'sequence', 'digest'}}
        try:
            valid = (rec.get('schemaVersion') == 'autodev.candidate-review.v1'
                 and rec.get('runId') == manifest['runId'] and rec.get('featureId') == src.scope.feature
                 and rec.get('reviewId') == path.stem == digest(canonical(facts))
                 and rec.get('digest') == 'sha256:' + digest(canonical({k: v for k, v in rec.items() if k != 'digest'}))
                 and rec.get('repositoryRef') in repositories
                 and isinstance(rec.get('wave'), int) and not isinstance(rec['wave'], bool) and rec['wave'] > 0
                 and rec.get('status') in {'passed', 'failed', 'error'}
                 and isinstance(rec.get('reviewResult'), dict) and isinstance(rec.get('deliveryCommits'), dict)
                 and isinstance(rec.get('sequence'), int) and not isinstance(rec['sequence'], bool) and rec['sequence'] > 0
                 and all(isinstance(rec.get(k), str) and re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', rec[k]) for k in ('baseSha', 'candidateSha'))
                 and isinstance(rec.get('recordedAt'), str))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            src.gap(name, '候选 Review 身份或内容摘要不匹配')
            continue
        key = (rec['repositoryRef'], rec['wave'], rec.get('baseSha'), digest(canonical(rec['deliveryCommits'])))
        groups.setdefault(key, []).append({**rec, 'sourceRefs': [name]})
    reviews = []
    recorded_candidates = set()
    for (repo, wave, base, _), history in groups.items():
        history.sort(key=lambda r: (r.get('sequence', 0), r['recordedAt'], r['reviewId']))
        latest = history[-1]
        train = trains.get(f'{repo}:wave-{wave:03d}') or {}
        current = train.get('candidateSha') == latest['candidateSha'] and train.get('baseSha') == base
        failed = [h for h in history if h['status'] in {'failed', 'error'}]
        reviews.append({
            'id': latest['reviewId'], 'kind': 'candidate_review', 'runId': manifest['runId'],
            'repository': repo, 'wave': wave, 'baseSha': base, 'candidateSha': latest['candidateSha'],
            'batches': sorted(latest['deliveryCommits']), 'files': [], 'active': current,
            'originalStatus': history[0]['status'],
            'status': 'review_passed' if latest['status'] == 'passed' else 'blocked' if current and latest['status'] == 'failed' else 'unverified',
            'reason': (failed[-1]['reviewResult'].get('error') if failed else None),
            'startedAt': history[0]['recordedAt'], 'resolvedAt': latest['recordedAt'] if latest['status'] == 'passed' else None,
            'history': history, 'sourceRefs': sorted({ref, *(p for h in history for p in h['sourceRefs'])}),
        })
        recorded_candidates.update((repo, wave, h['candidateSha']) for h in history)
    legacy = 0
    for key, train in trains.items():
        if not isinstance(train, dict) or not isinstance(train.get('reviewedCandidateSha'), str):
            continue
        repo, _, sequence = key.rpartition(':wave-')
        if sequence.isdigit() and (repo, int(sequence), train['reviewedCandidateSha']) not in recorded_candidates:
            legacy += 1
    return {'items': [*conflicts.values(), *reviews], 'legacyReviewCount': legacy}
