from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/autodev/autodev-code/references/frontend-html/with-absolute-html/scripts/process_assets.py'
spec = importlib.util.spec_from_file_location('frontend_assets_test', SCRIPT)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class FrontendAssetsTest(unittest.TestCase):
    def test_literal_and_encoded_hash_filenames_pass_copy_and_inventory_rechecks(self):
        from hooks import frontend_verification as fv
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project = root / 'project'
            project.mkdir()
            names = ['4_数据展示_04.表格_分隔线#有间距.svg', '图标#选中.svg', '图片#副本.png']
            originals = {}
            references = []
            for name in names:
                content = b'<svg/>' if name.endswith('.svg') else b'png'
                (root / name).write_bytes(content)
                originals[name] = content
                references.extend([f'<img src="{name}">', f'<img src="{quote(name)}">'])
            html = root / 'page.html'
            html.write_text(''.join(references), encoding='utf-8')
            output = project / '.frontend/html-analysis'
            proc = subprocess.run([sys.executable, '-B', str(SCRIPT), '--project-root', str(project),
                                   '--html-file', str(html), '--task-stem', 'page', '--output-dir', str(output)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            mapping = json.loads((output / 'page-assets-mapping.json').read_text(encoding='utf-8'))
            self.assertTrue(mapping['assetsReady'])
            self.assertEqual(len(mapping['mappings']), len(names))
            for item in mapping['mappings']:
                name = Path(item['resolvedSource']).name
                self.assertEqual(item['fileExtension'], Path(name).suffix)
                self.assertEqual(item['urlSuffix'], '')
                self.assertEqual(item['sourceDigest'], hashlib.sha256(originals[name]).hexdigest())
                self.assertEqual((project / item['targetPath']).read_bytes(), originals[name])
            route = {'route': 'absolute-html', 'htmlSourcePaths': [str(html)]}
            for _ in range(3):
                self.assertEqual(fv.asset_status(project, route)['status'], 'passed')
            target = project / mapping['mappings'][0]['targetPath']
            target.write_bytes(b'changed')
            self.assertEqual(fv.asset_status(project, route)['status'], 'failed')
            (root / names[0]).unlink()
            self.assertTrue(any(error['reason'] == 'source_asset_missing'
                                for error in fv.asset_status(project, route)['errors']))

    def test_hash_filenames_and_url_suffixes_survive_css_and_svg_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            name = '图标#有间距.svg'
            (root / name).write_text('<svg/>')
            (root / 'icons.svg').write_text('<svg/>')
            css_source = root / 'style#主.css'
            css_source.write_text(f'a{{background:url("{name}")}}'
                                  f'b{{background:url("{quote(name)}?v=2#two")}}'
                                  'c{background:url(icons.svg?v=1#one)}'
                                  'd{background:url(icons.svg?v=2#two)}'
                                  'e{mask:url(#local)}', encoding='utf-8')
            svg_source = root / 'wrapper.svg'
            svg_source.write_text(f'<svg><image href="{name}"/><use xlink:href="icons.svg#two"/>'
                                  '<use href="#local"/></svg>', encoding='utf-8')
            html = root / 'page.html'
            html.write_text('<link rel="stylesheet" href="style#主.css"><img src="wrapper.svg">', encoding='utf-8')
            original_css, original_svg = css_source.read_bytes(), svg_source.read_bytes()
            mapping = assets.generate_mapping_table('page', [html], project)
            result = assets.copy_assets_to_project(mapping['mappings'], project)
            self.assertEqual(result['failedCount'], 0, result)
            by_source = {Path(item['resolvedSource']).name: item for item in mapping['mappings']}
            for source_name in (css_source.name, svg_source.name):
                target = project / by_source[source_name]['targetPath']
                content = target.read_text(encoding='utf-8')
                self.assertIn('%23', content)
                refs = assets.extract_asset_references(content)
                local = [urlsplit(ref['path']) for ref in refs if urlsplit(ref['path']).path]
                for url in local:
                    self.assertTrue((target.parent / unquote(url.path)).is_file(), url)
                self.assertIn('#local', content)
                self.assertIn('#two', content)
            css = (project / by_source[css_source.name]['targetPath']).read_text(encoding='utf-8')
            self.assertIn('?v=1#one', css)
            self.assertEqual(css.count('?v=2#two'), 2)
            self.assertEqual(css_source.read_bytes(), original_css)
            self.assertEqual(svg_source.read_bytes(), original_svg)

    def test_exact_local_filename_takes_precedence_and_root_relative_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            public = root / 'public'
            public.mkdir()
            (public / 'icons.svg').write_text('<svg>base</svg>')
            literal = public / 'icons.svg#alternate.svg'
            literal.write_text('<svg>literal</svg>')
            html = root / 'page.html'
            html.write_text('<img src="/icons.svg#alternate.svg">')
            item = assets.generate_mapping_table('page', [html], project, public_root=public)['mappings'][0]
            self.assertEqual(Path(item['resolvedSource']), literal.resolve())
            self.assertEqual(item['urlSuffix'], '')
            denied = assets.generate_mapping_table('page', [html], project)['mappings'][0]
            self.assertEqual(denied['error'], 'root_relative_asset_requires_public_root')

    def test_real_assets_never_become_filename_placeholders(self):
        for name in ('a1b2.svg', 'sparkline.svg', 'user-avatar.png', 'line-chart.svg', 'brand-logo.svg'):
            with self.subTest(name=name):
                item = assets.classify_asset({'path': './assets/' + name, 'altText': '', 'context': ''}, ROOT)
                self.assertEqual(item['decision'], 'copyToProject')
                self.assertIsNone(item['candidateReplacement'])
        self.assertEqual(assets.classify_asset({'path': 'sparkline.svg'}, ROOT)['kind'], 'chart')

    def test_multiple_documents_keep_distinct_sources_and_existing_project_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'; project.mkdir()
            sources = []
            for index in range(2):
                bundle = root / str(index); (bundle / 'assets').mkdir(parents=True)
                (bundle / 'assets/logo.svg').write_text(f'<svg>{index}</svg>')
                html = bundle / 'index.html'; html.write_text('<img src="assets/logo.svg">')
                sources.append(html)
            mapping = assets.generate_mapping_table('page', sources, project)
            result = assets.copy_assets_to_project(mapping['mappings'], project)
            self.assertEqual(result['failedCount'], 0)
            targets = [item['targetPath'] for item in mapping['mappings']]
            self.assertEqual(len(set(targets)), 2)
            self.assertEqual({(project / p).read_text() for p in targets}, {'<svg>0</svg>', '<svg>1</svg>'})

    def test_css_dependencies_are_rebased_and_suffixes_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            (root / 'assets').mkdir()
            (root / 'assets/logo.svg').write_text('<svg/>')
            (root / 'assets/style.css').write_text('a{background:url(logo.svg?v=1#icon)}')
            html = root / 'page.html'; html.write_text('<link rel="stylesheet" href="./assets/style.css">')
            mapping = assets.generate_mapping_table('page', [html], project)
            result = assets.copy_assets_to_project(mapping['mappings'], project)
            self.assertEqual(result['failedCount'], 0)
            css = next(m for m in mapping['mappings'] if m['originalFile'].endswith('.css'))
            content = (project / css['targetPath']).read_text()
            self.assertIn('?v=1#icon', content)
            self.assertNotEqual(content, (root / 'assets/style.css').read_text())
            self.assertTrue(css['targetDigest'])

    def test_opaque_svg_bytes_are_preserved_when_no_reference_needs_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            original = '<?xml version="1.0" encoding="UTF-16"?><svg>品牌</svg>'.encode('utf-16')
            (root / 'brand.svg').write_bytes(original)
            html = root / 'page.html'; html.write_text('<img src="brand.svg">')
            mapping = assets.generate_mapping_table('page', [html], project)
            result = assets.copy_assets_to_project(mapping['mappings'], project)
            self.assertEqual(result['failedCount'], 0)
            self.assertEqual((project / mapping['mappings'][0]['targetPath']).read_bytes(), original)

    def test_root_relative_css_and_nested_svg_dependencies_are_rebased(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            public = root / 'public'; (public / 'assets').mkdir(parents=True)
            (public / 'assets/logo.png').write_bytes(b'png')
            (public / 'assets/icon.svg').write_text('<svg><image href="logo.png" /></svg>')
            (public / 'assets/style.css').write_text('a{background:url(/assets/icon.svg#icon)}')
            html = root / 'page.html'; html.write_text('<link rel="stylesheet" href="/assets/style.css">')
            mapping = assets.generate_mapping_table('page', [html], project, public_root=public)
            result = assets.copy_assets_to_project(mapping['mappings'], project)
            self.assertEqual(result['failedCount'], 0)
            self.assertEqual(len(mapping['mappings']), 3)
            css = next(m for m in mapping['mappings'] if m['originalFile'].endswith('.css'))
            svg = next(m for m in mapping['mappings'] if m['originalFile'].endswith('.svg#icon'))
            self.assertNotIn('/assets/icon.svg', (project / css['targetPath']).read_text())
            self.assertIn('#icon', (project / css['targetPath']).read_text())
            self.assertNotIn('href="logo.png"', (project / svg['targetPath']).read_text())
            self.assertTrue(svg['targetDigest'])

    def test_prepare_and_reference_renderer_share_explicit_public_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            source_dir = root / 'pages'; source_dir.mkdir()
            public = root / 'public'; (public / 'assets').mkdir(parents=True)
            (public / 'assets/logo.svg').write_text('<svg/>')
            html = source_dir / 'page.html'; html.write_text('<html><body><img src="/assets/logo.svg"></body></html>')
            prepare = SCRIPT.with_name('prepare_html_analysis.py')
            result = subprocess.run([sys.executable, '-B', str(prepare), '--project-root', str(project),
                '--html-file', str(html), '--task-stem', 'page', '--public-root', str(public)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            mapping = json.loads((project / '.frontend/html-analysis/page-assets-mapping.json').read_text())
            self.assertTrue(mapping['assetsReady'])
            self.assertEqual(mapping['publicRoot'], str(public.resolve()))
            # Test URL mapping without launching a browser or requiring Playwright.
            javascript = """const assert = require('node:assert/strict');
const path = require('node:path');
const { referenceFile } = require('./hooks/capture_frontend.cjs');
const [base, root] = process.argv.slice(1);
assert.equal(referenceFile(base, root, '/assets/logo.svg'), path.join(root, 'assets/logo.svg'));
assert.equal(referenceFile(base, root, '/__reference__/page.html'), path.join(base, 'page.html'));
assert.throws(() => referenceFile(base, root, '/__reference__/../../outside.txt'));
"""
            checked = subprocess.run(['node', '-e', javascript, str(source_dir.resolve()), str(public.resolve())], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_public_root_must_be_explicit_and_missing_asset_fails_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / 'project'; project.mkdir()
            html = root / 'page.html'; html.write_text('<img src="/assets/missing.png">')
            proc = subprocess.run([sys.executable, '-B', str(SCRIPT), '--project-root', str(project),
                '--html-file', str(html), '--task-stem', 'page', '--output-dir', str(project / '.frontend')], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            mapping = json.loads((project / '.frontend/page-assets-mapping.json').read_text())
            self.assertFalse(mapping['assetsReady'])
            self.assertEqual(mapping['mappings'][0]['error'], 'root_relative_asset_requires_public_root')

    def test_srcset_parent_assets_and_data_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'pages').mkdir(); (root / 'assets').mkdir()
            (root / 'assets/a.png').write_bytes(b'png')
            html = root / 'pages/page.html'; html.write_text('<img srcset="../assets/a.png 1x, ../assets/a.png 2x"><img src="data:image/svg+xml,abc">')
            mapping = assets.generate_mapping_table('page', [html], root)
            local = [item for item in mapping['mappings'] if item['decision'] == 'copyToProject']
            self.assertEqual(len(local), 1)
            self.assertEqual(local[0]['copyStatus'], 'pending')
            self.assertTrue(any(item.get('copyStatus') == 'embedded' for item in mapping['mappings']))
