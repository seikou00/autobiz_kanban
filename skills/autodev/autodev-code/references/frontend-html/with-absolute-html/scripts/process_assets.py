#!/usr/bin/env python3
"""Inventory and preserve referenced visual assets; replacements are advisory."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import quote, unquote, urlsplit

URL_RE = re.compile(r'url\(\s*[\"\']?([^\"\')]+)[\"\']?\s*\)', re.I)
IMPORT_RE = re.compile(r'@import\s+[\"\']([^\"\']+)[\"\']', re.I)


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references: list[dict[str, Any]] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        keys = ['src', 'poster'] if tag in {'img', 'source', 'video', 'audio', 'script', 'iframe'} else []
        if tag in {'image', 'use'}:
            keys += ['href', 'xlink:href']
        if tag == 'link' and set(attrs.get('rel', '').lower().split()) & {'stylesheet', 'icon', 'preload'}:
            keys += ['href']
        for key in keys:
            if attrs.get(key):
                self.references.append({'path': attrs[key], 'type': tag, 'altText': attrs.get('alt', ''), 'context': tag})
        # Data URLs can contain commas; preserve the whole attribute for those.
        srcset = attrs.get('srcset', '')
        if srcset and not srcset.startswith('data:'):
            for item in srcset.split(','):
                if item.strip():
                    self.references.append({'path': item.strip().split()[0], 'type': 'srcset', 'altText': '', 'context': tag})


def extract_asset_references(html_content: str) -> list[dict[str, Any]]:
    parser = AssetParser()
    parser.feed(html_content)
    parser.references.extend({'path': m.group(1).strip(), 'type': 'css', 'altText': '', 'context': ''}
                             for regex in (URL_RE, IMPORT_RE) for m in regex.finditer(html_content))
    return parser.references


def resolve_asset_reference(reference: str, base: Path) -> tuple[Path, str]:
    """Prefer an existing local filename, then interpret query/fragment suffixes."""
    literal = (base / unquote(reference).lstrip('/')).resolve()
    if literal.is_file():
        return literal, ''
    url = urlsplit(reference)
    source = (base / unquote(url.path).lstrip('/')).resolve()
    suffix = ('?' + url.query if url.query else '') + ('#' + url.fragment if url.fragment else '')
    return source, suffix


def classify_asset(asset_ref: dict[str, Any], project_root: Path, *, source: Path | None = None) -> dict[str, Any]:
    """Never substitute visual content based on a filename or a retention quota."""
    path = asset_ref['path']
    name = (source or Path(unquote(urlsplit(path).path))).name
    suffix = Path(name).suffix.lower()
    kind = 'chart' if re.search(r'(sparkline|chart|trend|funnel)', name, re.I) else 'image' if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif'} else 'unknown'
    return {'originalFile': path, 'semantic': asset_ref.get('altText') or name,
            'decision': 'copyToProject', 'kind': kind, 'candidateReplacement': None,
            'reason': '保留源资源；替换需真实视觉和项目组件证据', 'fileExtension': suffix}


def generate_mapping_table(task_stem: str, html_files: list[Path], project_root: Path,
                           public_root: Path | None = None) -> dict[str, Any]:
    mappings: list[dict[str, Any]] = []
    seen: set[Path] = set()
    queue = list(html_files)
    scanned: set[Path] = set()
    roots = [p.parent.resolve() for p in html_files]
    if public_root:
        roots.append(public_root.resolve())
    while queue:
        document = queue.pop(0).resolve()
        if document in scanned:
            continue
        scanned.add(document)
        for ref in extract_asset_references(read_text(document)):
            url = urlsplit(ref['path'])
            if url.scheme or url.netloc or not url.path:
                # Remote and embedded URLs stay unchanged; report them explicitly.
                if url.scheme or url.netloc:
                    mappings.append({**ref, 'originalFile': ref['path'], 'sourceDocument': str(document),
                                     'decision': 'unchanged', 'copyStatus': 'external' if url.scheme != 'data' else 'embedded'})
                continue
            raw = Path(unquote(url.path))
            base = public_root if raw.is_absolute() and public_root is not None else document.parent
            source, suffix = resolve_asset_reference(ref['path'], base)
            item = {**classify_asset(ref, project_root, source=source), 'sourceDocument': str(document),
                    'urlSuffix': suffix}
            if raw.is_absolute() and public_root is None:
                item.update(copyStatus='failed', error='root_relative_asset_requires_public_root')
                mappings.append(item)
                continue
            if not any(source.is_relative_to(root) for root in roots):
                # ../assets is allowed beside an HTML bundle, within its declared parent.
                if not any(source.is_relative_to(root.parent / 'assets') for root in roots):
                    item.update(copyStatus='failed', error='asset_outside_bundle_roots', resolvedSource=str(source))
                    mappings.append(item)
                    continue
            if source in seen:
                # Preserve every reference, but only copy/scan each source once.
                first = next(m for m in mappings if m.get('resolvedSource') == str(source))
                first.setdefault('references', []).append({'sourceDocument': str(document), 'url': ref['path'],
                                                          'urlSuffix': suffix})
                continue
            seen.add(source)
            item['resolvedSource'] = str(source)
            if not source.is_file():
                item.update(copyStatus='failed', error='source_asset_missing')
            else:
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                # Source path distinguishes same-name files with different relative dependencies.
                identity = hashlib.sha256((str(source) + digest).encode()).hexdigest()[:12]
                target = f'src/assets/frontend/{identity}/{source.name}'
                item.update(sourceDigest=digest, targetPath=target, copyStatus='pending',
                            referenceHint='使用项目真实 alias、相对 import 或 public URL；勿照抄 @/src')
                if source.suffix.lower() in {'.css', '.svg'}:
                    queue.append(source)
            mappings.append(item)
    copied = sum(m.get('decision') == 'copyToProject' for m in mappings)
    return {'schemaVersion': 2, 'taskStem': task_stem, 'publicRoot': str(public_root.resolve()) if public_root else None, 'totalAssets': len(mappings),
            'referencedAssets': len(mappings), 'decisions': {'copyToProject': copied, 'useAntdIcon': 0, 'useCss': 0, 'useProjectIcon': 0},
            'mappings': mappings, 'htmlSources': [str(p) for p in html_files]}


def copy_assets_to_project(mappings: list[dict[str, Any]], project_root: Path, html_dir: Path | None = None) -> dict[str, Any]:
    copied, failed = [], []
    for item in mappings:
        if item.get('copyStatus') == 'failed':
            failed.append(item)
            continue
        if item.get('decision') != 'copyToProject':
            continue
        source = Path(item['resolvedSource'])
        target = project_root / item['targetPath']
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != item['sourceDigest']:
                shutil.copy2(source, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != item['sourceDigest']:
                raise OSError('copied_asset_digest_mismatch')
            item['copyStatus'] = 'verified'
            copied.append({'originalFile': item['originalFile'], 'targetPath': item['targetPath']})
        except OSError as exc:
            item.update(copyStatus='failed', error=str(exc))
            failed.append(item)
    # Rewrite copied stylesheet URLs to the verified targets of its dependencies.
    by_reference = {}
    for mapped in mappings:
        if mapped.get('copyStatus') != 'verified':
            continue
        by_reference[(mapped['sourceDocument'], mapped['originalFile'])] = (mapped, mapped.get('urlSuffix', ''))
        for ref in mapped.get('references', []):
            # Older mappings stored only the URL for additional references.
            url = urlsplit(ref['url'])
            suffix = ref.get('urlSuffix', ('?' + url.query if url.query else '') + ('#' + url.fragment if url.fragment else ''))
            by_reference[(ref['sourceDocument'], ref['url'])] = (mapped, suffix)
    for item in mappings:
        if item.get('copyStatus') != 'verified' or Path(item['resolvedSource']).suffix.lower() not in {'.css', '.svg'}:
            continue
        source = Path(item['resolvedSource'])
        target = project_root / item['targetPath']
        def replace(match):
            raw = match.group(1).strip()
            reference = by_reference.get((str(source), raw))
            if not reference:
                return match.group(0)
            dependency, suffix = reference
            relative = os.path.relpath(project_root / dependency['targetPath'], target.parent).replace(os.sep, '/')
            return match.group(0).replace(raw, quote(relative, safe='/') + suffix)
        original = read_text(source)
        content = original
        for regex in (URL_RE, IMPORT_RE):
            content = regex.sub(replace, content)
        if source.suffix.lower() == '.svg':
            content = re.sub(r'''(?:xlink:href|href)=["']([^"']+)["']''', replace, content)
        if content != original:
            if '\ufffd' in original:
                item.update(copyStatus='failed', error='asset_encoding_requires_project_processing')
                failed.append(item)
                continue
            target.write_text(content, encoding='utf-8')
        item['targetDigest'] = hashlib.sha256(target.read_bytes()).hexdigest()
    return {'copiedCount': len(copied), 'failedCount': len(failed), 'copiedFiles': copied, 'failedFiles': failed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root', required=True)
    ap.add_argument('--html-file', action='append', required=True)
    ap.add_argument('--task-stem', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--public-root', help='Explicit URL root for /assets/... references')
    args = ap.parse_args()
    project = Path(args.project_root).resolve()
    if not project.is_dir():
        ap.error('project-root must exist')
    mapping = generate_mapping_table(args.task_stem, [Path(f).resolve() for f in args.html_file], project,
                                     Path(args.public_root).resolve() if args.public_root else None)
    result = copy_assets_to_project(mapping['mappings'], project)
    mapping['assetsCopyResult'] = result
    mapping['assetsReady'] = not result['failedCount']
    output = Path(args.output_dir) / f'{args.task_stem}-assets-mapping.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'mappingPath': str(output), 'assetsReady': mapping['assetsReady'], **result}, ensure_ascii=False))
    return 0 if mapping['assetsReady'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
