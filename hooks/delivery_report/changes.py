"""Read immutable Git objects for the delivered ranges. No checkout or diff driver."""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .sources import metric, canonical, digest
from .source_index import SourceIndex

SHA = re.compile(r'(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\Z')
EXCLUDED = {"dist", "build", "target", "node_modules", "vendor", ".autobizdevops", "generated",
            ".frontend", ".engineering", ".parallel-runs", ".task-runs", ".delivery-report",
            ".compile-logs", "html-analysis", "coverage"}
LOCKFILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "poetry.lock", "go.sum"}
METHODS = {"get", "put", "patch", "post", "delete", "options", "head", "trace"}
TYPES = {".java", ".kt", ".ts", ".tsx", ".jsx", ".js", ".vue", ".py", ".go", ".rs", ".c", ".cc",
         ".cpp", ".h", ".cs", ".html", ".css", ".less", ".scss", ".sql", ".xml", ".json", ".yaml", ".yml", ".sh", ".md"}


def source_file(path):
    p = Path(path.replace('\\', '/'))
    return (p.suffix.lower() in TYPES - {'.md'} and not EXCLUDED.intersection(p.parts)
            and p.name not in LOCKFILES and not p.name.endswith(('.min.js', '.min.css')))


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=25).stdout


def source_tree(root, sha):
    entries = git(root, 'ls-tree', '-r', '-z', sha).split(b'\0')
    objects = []
    for entry in entries:
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        path = os.fsdecode(raw_path)
        if (kind != b'blob' or mode == b'120000' or EXCLUDED.intersection(Path(path).parts)
                or Path(path).suffix.lower() not in {'.java','.xml','.json','.js','.jsx','.ts','.tsx','.vue','.css','.scss','.less'}):
            continue
        objects.append((path, oid))
    if not objects:
        return {}
    response = subprocess.run(['git','-C',str(root),'cat-file','--batch'],
                              input=b''.join(oid + b'\n' for _,oid in objects),
                              check=True,capture_output=True,timeout=60).stdout
    sources, offset = {}, 0
    for path, _ in objects:
        newline = response.index(b'\n',offset)
        header = response[offset:newline].split()
        size = int(header[2])
        content = response[newline + 1:newline + 1 + size]
        offset = newline + size + 2
        if b'\0' not in content:
            sources[path] = content.decode('utf-8','replace')
    return sources


def openapi_contract(document, operation):
    references = {}
    pending = [operation]
    while pending:
        value = pending.pop()
        if isinstance(value,list):
            pending.extend(value)
        elif isinstance(value,dict):
            pending.extend(value.values())
            ref = value.get('$ref')
            if isinstance(ref,str) and ref.startswith('#/') and ref not in references:
                target = document
                for part in ref[2:].split('/'):
                    target = target.get(part.replace('~1','/').replace('~0','~'),{}) if isinstance(target,dict) else {}
                references[ref] = target
                pending.append(target)
    return json.dumps({'operation':operation,'references':references},sort_keys=True)


def definitions(sources, warnings):
    index = SourceIndex(sources)
    result = index.definitions()
    warnings.extend(index.warnings)
    for path, content in sources.items():
        if Path(path).suffix != '.json' or not any(n in path.lower() for n in ('openapi','swagger')):
            continue
        data = json.loads(content)
        if not isinstance(data,dict) or not isinstance(data.get('paths',{}),dict):
            raise ValueError('OpenAPI paths 格式无效')
        for route, operations in data.get('paths',{}).items():
            if not isinstance(operations,dict):
                continue
            for method, spec in operations.items():
                if method not in METHODS:
                    continue
                # Referenced schemas can change response fields without changing the operation.
                result['api',method.upper() + ' ' + route] = {
                    'path':path,'files':[path],
                    'fingerprint':json.dumps(spec,sort_keys=True),
                    'contractFiles':[path],
                    'contractFingerprint':openapi_contract(data,{'spec':spec,'parameters':operations.get('parameters',[])})}
    return result


def implementation_evidence(old, new, changed_paths):
    sides = [record['_graph'].evidence(record, changed_paths) if record and record.get('_graph') else {} for record in (old, new)]
    result = []
    for key in sorted(sides[0].keys() | sides[1].keys()):
        before, after = sides[0].get(key), sides[1].get(key)
        if before and after and before['fingerprint'] == after['fingerprint']:
            continue
        item = after or before
        result.append({**{k: v for k, v in item.items() if k != 'fingerprint'},
                       'version': 'head' if after else 'base'})
    return result


def scan_range(root, base, head):
    raw = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--numstat', '-z', base, head, '--')
    files = []
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        added, deleted, name = entry.split(b'\t', 2)
        path = os.fsdecode(name)
        p = Path(path)
        if EXCLUDED.intersection(p.parts) or p.name in LOCKFILES or p.name.endswith(('.min.js','.min.css')):
            continue
        binary = added == b'-'
        files.append({'path':path,'added':None if binary else int(added),
                      'deleted':None if binary else int(deleted),'binary':binary})
    warnings = []
    before = definitions(source_tree(root,base), warnings)
    after = definitions(source_tree(root,head), warnings)
    changed_paths = {f['path'] for f in files}
    changes = {}
    for key in before.keys() | after.keys():
        old, new = before.get(key), after.get(key)
        operation = None
        fields = ['files']
        if old is None or new is None:
            operation = 'added' if old is None else 'deleted'
        elif old['fingerprint'] != new['fingerprint']:
            operation = 'modified'
        elif key[0] == 'api' and old.get('contractFingerprint') != new.get('contractFingerprint'):
            operation, fields = 'contract_affected', ['contractFiles']
        elif key[0] == 'api' and old.get('impactFingerprint') != new.get('impactFingerprint'):
            operation, fields = 'potential_affected', ['impactFiles']
        if operation:
            record = new or old
            related = {path for side in (old,new) if side for field in fields for path in side.get(field,[])}
            changes[key] = {'operation':operation,'path':record['path'], 'service':record.get('service'),
                            'affectedFiles':sorted(changed_paths & related),
                            'basis':'page_file' if key[1].startswith('文件 ') else 'source_dependency',
                            'changeType': 'contract' if operation == 'contract_affected' else 'potential' if operation == 'potential_affected' else 'implementation',
                            'implementationEvidence': implementation_evidence(old, new, changed_paths) if key[0] == 'api' and operation in {'modified','added','deleted'} else []}
    supported = {kind for kind,_ in before.keys() | after.keys()}
    gaps = [warning for warning in warnings if changed_paths.intersection(warning['files'])]
    for kind, extensions in [('api',('.java','.kt','.go','.py','.cs','.xml')),('page',('.jsx','.tsx','.vue'))]:
        connected = {path for key,record in list(before.items())+list(after.items()) if key[0]==kind
                     for field in ('files','contractFiles','impactFiles') for path in record.get(field,[])}
        unresolved = [f['path'] for f in files if f['path'].endswith(extensions) and f['path'] not in connected
                      and (not f['path'].endswith('.xml') or 'mapper' in f['path'].lower())]
        if unresolved:
            gaps.append({'kind':kind,'reason':'内部变更尚未关联到独立入口','files':unresolved,
                         'affectsMetric':False})
    return files, changes, supported, gaps


def combine_ranges(root, ranges):
    """Combine overlapping intervals on one history, leaving unverifiable segments explicit."""
    @lru_cache(maxsize=None)
    def ancestor(older, newer):
        if older == newer:
            return True
        return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer],
                              capture_output=True, timeout=25).returncode == 0

    segments = sorted(ranges)
    if any(not ancestor(base, head) for base, head in segments):
        raise ValueError("交付基线与终点不属于同一提交链")
    while True:
        for i, (base, head) in enumerate(segments):
            for j, (other, end) in enumerate(segments):
                if i == j or not ancestor(base, other) or not ancestor(other, head):
                    continue
                if ancestor(end, head):
                    merged = (base, head)
                elif ancestor(head, end):
                    merged = (base, end)
                else:
                    continue
                segments = [pair for index, pair in enumerate(segments) if index not in (i, j)] + [merged]
                break
            else:
                continue
            break
        else:
            return sorted(segments)


def summarize(repositories, inputs):
    groups = defaultdict(list)
    refs = []
    for repo in repositories:
        groups[repo["root"] or repo["ref"]].append(repo)
        refs.extend(repo["sourceRefs"])
    totals = defaultdict(int)
    supported = set()
    details, partial = [], False
    coverage_gaps = {}
    for root, group in groups.items():
        try:
            if not Path(root).is_absolute() or not Path(root).is_dir():
                raise ValueError("交付仓库不可用")
            ranges = {(r["baseSha"], r["headSha"]) for r in group}
            if not all(SHA.fullmatch(str(sha)) for pair in ranges for sha in pair):
                raise ValueError("缺少交付基线或提交")
            segments = combine_ranges(root, ranges)
            if len(segments) > 1:
                partial = True
                inputs.gap(group[0]["ref"], "交付区间无法核对为同一完整范围，分段展示")
            for base, head in segments:
                files, entities, known, warnings = scan_range(root, base, head)
                supported.update(known)
                for warning in warnings:
                    kind = warning['kind']
                    inputs.gap('接口识别' if kind=='api' else '页面识别', warning['reason'], category='coverage',
                               reason_code=kind+'_'+warning['reason'], scope={'repository':group[0]['ref'],
                               'baseSha':base,'headSha':head}, metrics=warning.get('metrics',[kind,kind+'Modified']) if warning.get('affectsMetric') else [kind],
                               files=warning['files'], source_refs=sorted(set(refs)),
                               action='核对所列文件的调用或页面引用关系')
                    if warning.get('affectsMetric'):
                        for name in warning.get('metrics',[kind,kind+'Modified']):
                            coverage_gaps[name] = warning['reason']
                row = {"repository": group[0]["ref"], "baseSha": base, "headSha": head, "files": files,
                       "changes": [{"kind": k, "name": n, **change} for (k, n), change in sorted(entities.items())],
                       "sourceRefs": sorted({ref for r in group for ref in r["sourceRefs"]})}
                git_ref = f'git:{group[0]["ref"]}:{base}..{head}'
                inputs.sources[git_ref] = {'path':git_ref,'kind':'git_diff','repository':group[0]['ref'],
                                           'baseSha':base,'headSha':head,'sha256':digest(canonical(row))}
                row['sourceRefs'].append(git_ref)
                refs.append(git_ref)
                for f in files:
                    if not f["binary"] and source_file(f["path"]):
                        totals["added"] += f["added"]
                        totals["deleted"] += f["deleted"]
                for (kind, _), change in entities.items():
                    operation = change['operation']
                    if operation in {'added','modified','deleted'}:
                        totals[kind + operation.title()] += 1
                        totals[kind] += 1
                        if kind == 'api' and operation == 'modified':
                            totals['apiImplementationModified'] += 1
                    elif operation == 'contract_affected':
                        totals['apiContractAffected'] += 1
                        totals['apiContractModified'] += 1
                        totals['apiModified'] += 1
                        totals['api'] += 1
                    else:
                        totals['apiPotentialAffected'] += 1
                modules = {}
                for f in files:
                    name = f["path"].split("/")[0] if "/" in f["path"] else "."
                    mod = modules.setdefault(
                        name, {"name": name, "files": [], "added": 0, "deleted": 0, "api": 0, "page": 0, "changes": []})
                    mod["files"].append(f)
                    if source_file(f["path"]):
                        mod["added"] += f["added"] or 0
                        mod["deleted"] += f["deleted"] or 0
                for change in row["changes"]:
                    path = change["path"]
                    name = path.split("/")[0] if "/" in path else "."
                    mod = modules.setdefault(name, {"name":name,"files":[],"added":0,"deleted":0,"api":0,"page":0,"changes":[]})
                    if change["operation"] in {"added","modified","deleted","contract_affected"}:
                        mod[change["kind"]] += 1
                    mod["changes"].append(change)
                row["modules"] = list(modules.values())
                details.append(row)
        except (ValueError, OSError, subprocess.SubprocessError, AttributeError) as exc:
            partial = True
            inputs.gap(group[0]["ref"], str(exc))
    totals["net"] = totals["added"] - totals["deleted"]
    result = {}
    for key in ('added', 'deleted', 'net', 'api', 'apiAdded', 'apiModified', 'apiDeleted', 'page', 'pageAdded', 'pageModified', 'pageDeleted', 'apiContractAffected', 'apiPotentialAffected', 'apiImplementationModified', 'apiContractModified'):
        val = totals[key] if details else None
        reason = None
        if not details:
            reason = "缺少可核对的代码区间"
        elif partial:
            val, reason = None, "部分仓库范围缺失，明细仅列已核验的仓库"
        elif key in coverage_gaps:
            reason = coverage_gaps[key]
        elif key.startswith(('api', 'page')) and not any(key.startswith(kind) for kind in supported):
            val, reason = None, "受支持的接口或页面定义中未取得变更证据"
        result[key] = metric(val, refs, reason)
    for key in ('api','page'):
        if details and key not in supported and not any(f['path'].endswith(('.java','.jsx','.tsx','.vue')) for r in details for f in r['files']):
            for name in (key,key+'Added',key+'Modified',key+'Deleted'):
                result[name] = metric(0,refs)
                result[name]['status'] = 'not_applicable'
    result['api']['description'] = '按仓库、HTTP 方法与路径去重，合计新增、实现或调用链修改、已确认契约修改及删除；待核验依赖影响单列。'
    result['apiModified']['description'] = '既有接口的 Handler、可定位的 Service/辅助方法/Mapper SQL、引用字段常量或输入输出契约变化；Controller 文件无需发生修改。'
    result['apiContractAffected']['description'] = '已确认的契约修改，已包含在接口修改与接口变更总数中。'
    return result, details
