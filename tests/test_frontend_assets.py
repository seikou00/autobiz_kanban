from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/autodev/autodev-code/references/frontend-html/with-absolute-html/scripts/process_assets.py'
spec = importlib.util.spec_from_file_location('frontend_assets_test', SCRIPT)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class FrontendAssetsTest(unittest.TestCase):
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
