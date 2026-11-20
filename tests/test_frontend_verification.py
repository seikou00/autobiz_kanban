from __future__ import annotations
from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from hooks import frontend_verification as fv
from hooks.resolve_frontend_html_route import classify_html
from tests.test_task_runner import _workspace, _configure_frontend_without_batch_compile, _start, _run, _verify_frontend


class FrontendVerificationTest(unittest.TestCase):
    def setup_task(self, root):
        workspace, fd, code = _workspace(root)
        _configure_frontend_without_batch_compile(fd)
        state = _start(workspace, code)
        page = code / 'src/Page.tsx'; page.parent.mkdir(exist_ok=True)
        page.write_text('export default function Page() { return <div>Ready</div>; }')
        return workspace, fd, code, state

    def test_default_targets_use_changed_files_in_react_and_vue_projects(self):
        from tests.test_task_runner import _git
        for page_name, content in (
            ('Page.tsx', 'export default () => <p>Ready</p>'),
            ('Page.vue', '<template><p class="field">Ready</p></template>'),
        ):
            with self.subTest(page=page_name), tempfile.TemporaryDirectory() as tmp:
                workspace, fd, code = _workspace(Path(tmp))
                _configure_frontend_without_batch_compile(fd)
                (code / 'app').mkdir()
                (code / 'app/Unrelated.tsx').write_text('import "./missing";')
                _git(code, 'add', 'app/Unrelated.tsx')
                _git(code, 'commit', '-m', 'existing page')
                state = _start(workspace, code)
                (code / 'app' / page_name).write_text(content)
                (code / 'app/theme.sass').write_text('.field\n  display: block\n')
                (code / 'index.html').write_text('<div id="app"></div>')
                checked = _verify_frontend(workspace, code, state['runId'], None)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertEqual(json.loads(checked.stdout)['binding']['targets'],
                                 [f'app/{page_name}', 'app/theme.sass', 'index.html'])
                finished = _run('finish-implementation', '--workspace', str(workspace), '--feature', 'alpha',
                                '--task-id', 'T001', '--code-workspace', str(code), '--run-id', state['runId'])
                self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)

    def test_standard_html_route_accepts_both_frameworks_without_an_asset_mapping(self):
        from tests.test_frontend_route_gate import base_ui_context, gate_context, validate_frontend_route_gate, write_ui_context
        from tests.test_task_runner import _read_batch, _refresh_parallel_pipeline, _write_batch
        from hooks.resolve_frontend_html_route import mark_evidence

        for extension, page in (
            ('tsx', 'import icon from "./icon.svg";\nexport default () => <img src={icon} alt="Logo" />;'),
            ('vue', '<script setup>\nimport icon from "./icon.svg";\n</script>\n<template><img :src="icon" alt="Logo" /></template>'),
        ):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as tmp:
                workspace, fd, code = _workspace(Path(tmp))
                _configure_frontend_without_batch_compile(fd)
                original = fd / 'frontend-html' / 'design.html'
                original.parent.mkdir()
                original.write_text('<main><img src="./icon.svg" alt="Logo"></main>')
                original.with_name('icon.svg').write_text('<svg/>')
                ui_context = base_ui_context()
                ui_context['visualSources'] = [{'sourceId': 'VIS-001', 'type': 'other', 'path': str(original),
                                                'route': 'standard-html', 'required': True}]
                ui_context['capabilities'][0]['visualSourceRefs'] = ['VIS-001']
                write_ui_context(workspace, ui_context)
                batch = _read_batch(fd)
                batch['tasks'][0]['uiRefs'].update(frontendRoute='standard-html', visualSourceRefs=['VIS-001'])
                _write_batch(fd, batch)
                _refresh_parallel_pipeline(fd)
                fv.resolve_frontend_route(workspace, 'alpha', task_id='T001', start_route_run=True, write_evidence=True)
                state = _start(workspace, code)
                for mark in ('route-skill-read-complete', 'route-todos-created', 'parser-read', 'route-todos-completed'):
                    mark_evidence(workspace, 'alpha', task_id='T001', mark=mark)
                (code / 'src').mkdir()
                (code / 'src' / f'Page.{extension}').write_text(page)
                (code / 'src/icon.svg').write_text('<svg/>')
                proc = _verify_frontend(workspace, code, state['runId'], None)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn('frontend_assets_unverified', {item['type'] for item in json.loads(proc.stdout)['advisories']})
                finished = _run('finish-implementation', '--workspace', str(workspace), '--feature', 'alpha',
                                '--task-id', 'T001', '--code-workspace', str(code), '--run-id', state['runId'])
                self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
                self.assertEqual(validate_frontend_route_gate(gate_context(workspace)), 0)

    def test_performance_hook_allows_vue_lifecycle_and_warns_about_scroll_handlers(self):
        from tests.test_task_runner import ROOT, _git

        with tempfile.TemporaryDirectory() as tmp:
            _, _, code = _workspace(Path(tmp))
            page = code / 'Page.vue'
            page.write_text('<template><p>Ready</p></template>')
            _git(code, 'add', 'Page.vue')
            _git(code, 'commit', '-m', 'existing page')
            page.write_text(
                '<script setup>\nimport { onMounted, ref, watchEffect } from "vue";\n'
                'const title = ref("");\n'
                'onMounted(() => { title.value = "Ready"; });\n'
                'watchEffect(() => { document.title = title.value; });\n'
                'function onScroll() {}\n</script>\n<template><p @scroll="onScroll">{{ title }}</p></template>'
            )
            payload = {'tool_input': {'command': 'python hooks/update_checkpoint.py --checkpoint code_done'}}
            hook = subprocess.run([sys.executable, '-B', str(ROOT / 'hooks/performance_checker.py')],
                                  input=json.dumps(payload), capture_output=True, text=True,
                                  env={**os.environ, 'WORKSPACE_PATH': str(code)})
            result = json.loads(hook.stdout)
            self.assertNotEqual(result.get('decision'), 'block')
            self.assertIn('FRONT-RULE004', result['additionalContext'])
            self.assertNotIn('FRONT-RULE005', result['additionalContext'])

    def test_missing_report_blocks_finish_and_same_revision_reuses_static(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            result = _run('finish-implementation', '--workspace', str(workspace), '--feature', 'alpha', '--task-id', 'T001',
                          '--code-workspace', str(code), '--run-id', state['runId'])
            self.assertIn('frontend_verification_missing_or_failed', result.stdout)
            first = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertTrue(json.loads(second.stdout)['cacheHit'])
            # Real static runner is not called again for an unchanged binding.
            args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'], code_workspace=code,
                             parallel_run_id=None, lease_token=None, workspace_ref=None, target=['src/Page.tsx'],
                             candidate_wave=None, check_script=None, capture=False, actual_screenshot=None,
                             visual_report=None, timeout=60)
            with patch.object(fv.subprocess, 'run', wraps=subprocess.run) as run:
                fv.verify(args)
                self.assertFalse(any('review_runner.py' in str(c.args) for c in run.call_args_list))
            result = _run('finish-implementation', '--workspace', str(workspace), '--feature', 'alpha', '--task-id', 'T001',
                          '--code-workspace', str(code), '--run-id', state['runId'])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = fv.read(fv.report_path(fd, 'T001', state['runId']))
            self.assertEqual(report['fidelityStatus'], 'not-applicable')

    def test_related_edits_invalidate_but_unrelated_page_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            (code / 'src/Part.tsx').write_text('export default function Part(){return <span/>}')
            (code / 'src/Page.tsx').write_text("import Part from './Part'; export default function Page(){return <Part/>}")
            proc = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(proc.returncode, 0, proc.stdout)
            finished = _run('finish-implementation', '--workspace', str(workspace), '--feature', 'alpha', '--task-id', 'T001',
                            '--code-workspace', str(code), '--run-id', state['runId'])
            self.assertEqual(finished.returncode, 0, finished.stdout)
            from hooks import task_runner as tr
            _, state = tr._load_run(fd, 'T001', state['runId'])
            (code / 'src/Other.tsx').write_text('export const Other = 1')
            _, _, task = __import__('hooks.task_runner', fromlist=[''])._load_plan_and_task(fd, 'T001', require_active_batch=False)
            fv.assert_current(fd, task, state, code)
            (code / 'src/Part.tsx').write_text('export default function Part(){return <p>changed</p>}')
            with self.assertRaisesRegex(ValueError, 'stale'):
                fv.assert_current(fd, task, state, code)
            _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            (code / 'src/global.css').write_text('*{color:red}')
            with self.assertRaisesRegex(ValueError, 'stale'):
                fv.assert_current(fd, task, state, code)
            _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            (code / 'public').mkdir()
            (code / 'public/logo.svg').write_text('<svg/>')
            with self.assertRaisesRegex(ValueError, 'stale'):
                fv.assert_current(fd, task, state, code)

    def test_replaying_finished_task_reuses_recorded_evidence_after_checkout_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            checked = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(checked.returncode, 0, checked.stdout)
            args = ('finish-implementation', '--workspace', str(workspace), '--feature', 'alpha',
                    '--task-id', 'T001', '--code-workspace', str(code), '--run-id', state['runId'])
            self.assertEqual(_run(*args).returncode, 0)
            path = fv.report_path(fd, 'T001', state['runId'])
            original = path.read_bytes()
            page = code / 'src/Page.tsx'
            page.write_bytes(page.read_bytes() + b'\r\n')
            replay = _run(*args)
            self.assertEqual(replay.returncode, 0, replay.stdout)
            self.assertEqual(path.read_bytes(), original)

    def test_static_review_and_its_python_children_inherit_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            checkers = Path(tmp) / 'checkers'
            (checkers / 'scripts').mkdir(parents=True)
            (checkers / 'scripts/review_runner.py').write_text(
                "import json, os, subprocess, sys\n"
                "child = subprocess.check_output([sys.executable, '-c', 'import sys; print(sys.flags.utf8_mode)'], text=True).strip()\n"
                "os.write(2, b'native diagnostic: \\xb4\\xed\\xce\\xf3')\n"
                "print(json.dumps({'findings': [], 'label': '中文', 'utf8': sys.flags.utf8_mode, 'childUtf8': child}, ensure_ascii=False))\n",
                encoding='utf-8')
            args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'], code_workspace=code,
                parallel_run_id=None, lease_token=None, workspace_ref=None, candidate_wave=None, target=['src/Page.tsx'],
                check_script=None, capture=False, actual_screenshot=None, reference_screenshot=None,
                visual_report=None, timeout=60)
            with patch.object(fv, 'FRONTEND_ROOT', checkers), patch.object(fv, 'checker_digest', return_value='checker'), \
                    patch.dict(os.environ, {'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'ascii'}):
                result = fv.verify(args)
            self.assertIn(result['status'], {'passed', 'has-suggestions'})
            report = result['static']['reports'][0]
            self.assertEqual(report['label'], '中文')
            self.assertEqual(report['utf8'], 1)
            self.assertEqual(report['childUtf8'], '1')

    def test_static_process_errors_and_invalid_json_remain_blocked(self):
        cases = [
            (2, b'', 'frontend_static_execution_failed:native diagnostic:'),
            (0, b'{"findings": [], "label": "\xff"}', 'frontend_static_invalid_json:'),
            (0, b'not JSON', 'frontend_static_invalid_json:'),
        ]
        for exit_code, stdout, reason in cases:
            with self.subTest(exit_code=exit_code, stdout=stdout), tempfile.TemporaryDirectory() as tmp:
                workspace, fd, code, state = self.setup_task(Path(tmp))
                checkers = Path(tmp) / 'checkers'
                (checkers / 'scripts').mkdir(parents=True)
                (checkers / 'scripts/review_runner.py').write_text(
                    f"import os, sys\nos.write(1, {stdout!r})\n"
                    "os.write(2, b'native diagnostic: \\xb4\\xed\\xce\\xf3')\n"
                    f"sys.exit({exit_code})\n", encoding='utf-8')
                args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'],
                    code_workspace=code, parallel_run_id=None, lease_token=None, workspace_ref=None,
                    candidate_wave=None, target=['src/Page.tsx'], check_script=None, capture=False,
                    actual_screenshot=None, reference_screenshot=None, visual_report=None, timeout=60)
                with patch.object(fv, 'FRONTEND_ROOT', checkers), patch.object(fv, 'checker_digest', return_value='checker'):
                    with self.assertRaisesRegex(ValueError, reason):
                        fv.verify(args)
                report = fv.read(fv.report_path(fd, 'T001', state['runId']))
                self.assertEqual(report['status'], 'blocked')
                self.assertEqual(report['static']['status'], 'blocked')
                self.assertTrue(report['lastError'].startswith(reason))

    def test_compiler_output_preserves_utf8_and_tolerates_native_diagnostics(self):
        real_run = subprocess.run
        cases = [
            (0, '编译通过'.encode('utf-8'), b'', 'passed', '编译通过'),
            (2, b'', b'typecheck failed: \xb1\xe0\xd2\xeb', 'failed', 'typecheck failed:'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'package.json').write_text(json.dumps({'scripts': {'typecheck': 'tsc --noEmit'}}), encoding='utf-8')
            executable = root / 'node_modules/.bin' / ('tsc.cmd' if os.name == 'nt' else 'tsc')
            executable.parent.mkdir(parents=True)
            executable.touch()
            for exit_code, stdout, stderr, status, message in cases:
                with self.subTest(exit_code=exit_code):
                    command = [sys.executable, '-c',
                        f'import os, sys; os.write(1, {stdout!r}); os.write(2, {stderr!r}); sys.exit({exit_code})']
                    def compiler(argv, **kwargs):
                        return real_run(command, **kwargs)
                    with patch.object(fv.subprocess, 'run', side_effect=compiler):
                        result = fv.check_script(root, 'typecheck', 10)
                    self.assertEqual(result['status'], status)
                    self.assertEqual(result['exitCode'], exit_code)
                    self.assertEqual(result['argv'], ['tsc', '--noEmit'])
                    self.assertIn(message, result['output'])

    def test_capture_separates_utf8_json_from_native_diagnostics(self):
        real_popen = subprocess.Popen
        payload = {'screenshots': [], 'errors': [], 'label': '中文截图'}
        stdout = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        cases = [
            (0, stdout, None),
            (1, b'', 'frontend_capture_failed:native diagnostic:'),
            (0, b'{"label": "\xff"}', 'frontend_capture_invalid_json:'),
            (0, b'not JSON', 'frontend_capture_invalid_json:'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = Namespace(url='http://127.0.0.1:12345', preview_script=None, timeout=10, width=1440, height=900)
            for exit_code, stdout, reason in cases:
                with self.subTest(exit_code=exit_code, stdout=stdout):
                    command = [sys.executable, '-c',
                        f'import os, sys; os.write(1, {stdout!r}); '
                        "os.write(2, b'native diagnostic: \\xb4\\xed\\xce\\xf3'); "
                        f'sys.exit({exit_code})']
                    def renderer(argv, **kwargs):
                        return real_popen(command, **kwargs)
                    with patch.object(fv.subprocess, 'Popen', side_effect=renderer), patch('urllib.request.urlopen'):
                        if reason:
                            with self.assertRaisesRegex(ValueError, reason):
                                fv.capture(root, root, args, [])
                        else:
                            self.assertEqual(fv.capture(root, root, args, []), payload)

    def test_task_without_html_does_not_borrow_sibling_visual_sources(self):
        import copy
        from hooks.resolve_frontend_html_route import _plan_ui_scope
        from tests.test_task_runner import _read_batch
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code = _workspace(Path(tmp))
            _configure_frontend_without_batch_compile(fd)
            batch = _read_batch(fd)
            sibling = copy.deepcopy(batch['tasks'][0]); sibling['id'] = 'T002'
            sibling['uiRefs'].update(frontendRoute='absolute-html', visualSourceRefs=['VIS-001'])
            batch['tasks'].append(sibling)
            (fd / 'plans/B001/plan.json').write_text(json.dumps(batch))
            scope = _plan_ui_scope(fd, task_id='T001')
            self.assertEqual([t['taskId'] for t in scope['tasks']], ['T001'])
            self.assertEqual(scope['tasks'][0]['visualSourceRefs'], [])
            archive = fd / 'frontend-html'; archive.mkdir()
            (archive / 'sibling.html').write_text('<div>Other page design</div>')
            from hooks.resolve_frontend_html_route import route_payload
            route = route_payload(workspace, 'alpha', task_id='T001')
            self.assertEqual(route['route'], 'spec-driven-ui')
            self.assertEqual(route['htmlSourcePaths'], [])

    def test_static_must_fix_cannot_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            (code / 'src/Page.tsx').write_text('import "./missing";\nexport default function Page(){return <div>Ready</div>}')
            proc = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertEqual(json.loads(proc.stdout)['status'], 'failed')

    def test_tailwind_main_layout_routes_absolute_but_one_decoration_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / 'page.html'
            html.write_text(''.join('<div class="absolute left-[10px] top-[20px]">x</div>' for _ in range(5)))
            self.assertEqual(classify_html([html])[0], 'absolute-html')
            html.write_text('<main class="flex"><span class="absolute left-0 top-0">x</span></main>')
            self.assertEqual(classify_html([html])[0], 'standard-html')

    def test_readable_html_allows_unverified_visuals_and_still_checks_claimed_visual_proof(self):
        import base64
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            source = Path(tmp) / 'original.html'; source.write_text('<html><body>Ready</body></html>')
            args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'], code_workspace=code,
                parallel_run_id=None, lease_token=None, workspace_ref=None, candidate_wave=None, target=['src/Page.tsx'],
                check_script=None, capture=False, actual_screenshot=None, reference_screenshot=None,
                visual_report=None, timeout=60)
            with patch.object(fv, 'resolve_frontend_route', return_value={'route': 'absolute-html', 'htmlSourcePaths': [str(source)]}):
                result = fv.verify(args)
                self.assertEqual(result['status'], 'has-suggestions')
                self.assertEqual(result['fidelityStatus'], 'unverified')
                self.assertFalse(result['advisories'][0]['blocksWorkflow'])
                fv.assert_current(fd, {'id': 'T001'}, state, code)
                image = Path(tmp) / 'screen.png'
                image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
                visual = Path(tmp) / 'visual.json'
                visual.write_text(json.dumps({'bindingDigest': result['bindingDigest'], 'decision': 'passed', 'findings': []}))
                args.actual_screenshot = image; args.reference_screenshot = [image]; args.visual_report = visual
                result = fv.verify(args)
                self.assertEqual(result['fidelityStatus'], 'passed')
                self.assertEqual(result['advisories'], [])
                fv.assert_current(fd, {'id': 'T001'}, state, code)
                stored_screenshot = Path(result['capture']['screenshots'][0]['path'])
                screenshot_bytes = stored_screenshot.read_bytes()
                stored_screenshot.unlink()
                with self.assertRaisesRegex(ValueError, 'screenshot_missing_or_changed'):
                    fv.assert_current(fd, {'id': 'T001'}, state, code)
                stored_screenshot.write_bytes(screenshot_bytes)
                self.assertIn('function Page', (code / 'src/Page.tsx').read_text())
                source.write_text('<html><body>Changed</body></html>')
                with self.assertRaisesRegex(ValueError, 'stale'):
                    fv.assert_current(fd, {'id': 'T001'}, state, code)
            # No HTML is an ordinary specification-driven page, with no image requirement.
            with patch.object(fv, 'resolve_frontend_route', return_value={'route': 'spec-driven-ui', 'htmlSourcePaths': [], 'requiredVisualSourceMissing': [{'path': 'missing.html'}]}):
                args.actual_screenshot = None; args.visual_report = None
                result = fv.verify(args)
                self.assertIn(result['status'], {'passed', 'has-suggestions'})
                self.assertEqual(result['fidelityStatus'], 'not-applicable')

    def test_missing_visual_environment_allows_real_task_finish_but_keeps_failures_blocking(self):
        from hooks import task_runner as tr
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            source = Path(tmp) / 'original.html'
            source.write_text('<html><body>Ready</body></html>')
            args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'], code_workspace=code,
                parallel_run_id=None, lease_token=None, workspace_ref=None, candidate_wave=None, target=['src/Page.tsx'],
                check_script=None, capture=True, actual_screenshot=None, reference_screenshot=None,
                visual_report=None, timeout=60, url=None, preview_script='dev', page_path='/', width=1440, height=900)
            route = {'route': 'absolute-html', 'htmlSourcePaths': [str(source)]}
            with patch.object(fv, 'resolve_frontend_route', return_value=route):
                for error in (FileNotFoundError('vite executable missing'),
                              ValueError('frontend_capture_failed:Playwright unavailable'),
                              subprocess.TimeoutExpired('browser', 1)):
                    with self.subTest(error=str(error)), patch.object(fv, 'capture', side_effect=error):
                        result = fv.verify(args)
                        self.assertEqual(result['status'], 'has-suggestions')
                        self.assertEqual(result['fidelityStatus'], 'unverified')
                        self.assertIn(str(error), result['advisories'][0]['message'])
                        fv.assert_current(fd, {'id': 'T001'}, state, code)
                with patch.object(fv, 'capture', return_value={'errors': ['404 /broken-asset.png'], 'screenshots': []}):
                    self.assertEqual(fv.verify(args)['status'], 'failed')
                with patch.object(fv, 'capture', side_effect=FileNotFoundError('browser unavailable')):
                    self.assertEqual(fv.verify(args)['status'], 'failed')
                args.capture = False
                # Refresh after a source change so an old capture cannot claim this revision.
                (code / 'src/Page.tsx').write_text('export default function Page(){return <div>Updated</div>;}')
                self.assertEqual(fv.verify(args)['status'], 'has-suggestions')
                stdout = io.StringIO()
                argv = ['frontend_verification.py', '--workspace', str(workspace), '--feature', 'alpha',
                        '--task-id', 'T001', '--run-id', state['runId'], '--code-workspace', str(code), '--target', 'src/Page.tsx']
                with patch.object(sys, 'argv', argv), redirect_stdout(stdout):
                    self.assertEqual(fv.main(), 0)
                summary = json.loads(stdout.getvalue())
                self.assertEqual(summary['fidelityStatus'], 'unverified')
                self.assertEqual(summary['advisories'][0]['type'], 'frontend_visual_unverified')
                self.assertTrue(tr.finish_implementation(workspace, 'alpha', 'T001', code, state['runId'],
                                                        no_code_change_why=None, supporting_files=[])[0])

    def test_a_known_visual_defect_survives_an_unavailable_capture_retry(self):
        import base64
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            source = Path(tmp) / 'original.html'
            source.write_text('<html><body>Ready</body></html>')
            args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'], code_workspace=code,
                parallel_run_id=None, lease_token=None, workspace_ref=None, candidate_wave=None, target=['src/Page.tsx'],
                check_script=None, capture=False, actual_screenshot=None, reference_screenshot=None,
                visual_report=None, timeout=60, url=None, preview_script='dev', page_path='/', width=1440, height=900)
            with patch.object(fv, 'resolve_frontend_route', return_value={'route': 'absolute-html', 'htmlSourcePaths': [str(source)]}):
                result = fv.verify(args)
                image = Path(tmp) / 'screen.png'
                image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
                visual = Path(tmp) / 'visual.json'
                visual.write_text(json.dumps({'bindingDigest': result['bindingDigest'], 'decision': 'failed',
                                              'findings': [{'severity': 'must-fix', 'message': 'Page layout is broken'}]}))
                args.actual_screenshot = image
                args.reference_screenshot = [image]
                args.visual_report = visual
                self.assertEqual(fv.verify(args)['status'], 'failed')
                args.actual_screenshot = args.visual_report = None
                args.capture = True
                with patch.object(fv, 'capture', side_effect=FileNotFoundError('browser unavailable')):
                    result = fv.verify(args)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['visual']['status'], 'failed')
                with self.assertRaisesRegex(ValueError, 'missing_or_failed'):
                    fv.assert_current(fd, {'id': 'T001'}, state, code)

    def test_visual_advisory_is_reported_once_and_cleared_after_validation(self):
        from tests.test_parallel_delivery import DeliveryTest
        from hooks.parallel_evidence_aggregate import aggregate_evidence
        from hooks.parallel_delivery import settle_delivery
        delivery = DeliveryTest()
        delivery.setUp()
        self.addCleanup(delivery.doCleanups)
        delivery.create()
        args = Namespace(workspace=delivery.workspace, feature='alpha', parallel_run_id=delivery.run,
                         task_id='T001', _frontend_batch_id='B001')
        result = {'binding': {'route': 'absolute-html'}, 'status': 'has-suggestions',
                  'advisories': [{'type': 'frontend_visual_unverified', 'message': 'Browser unavailable'}]}
        for _ in range(2):
            fv.publish_route_summary(args, result)
        self.assertEqual(len(delivery.manifest()['deferredIssues']), 1)
        settle_delivery(*delivery.delivery()[0])
        report = aggregate_evidence(delivery.workspace, 'alpha', delivery.run)
        self.assertTrue(report['passed'])
        self.assertFalse(report['hasBlockingDeferredIssues'])
        self.assertEqual(report['deferredIssues'][0]['kind'], 'frontend_visual_unverified')
        result.update(status='passed', advisories=[])
        fv.publish_route_summary(args, result)
        self.assertEqual(delivery.manifest()['deferredIssues'], [])

    def test_verification_reports_are_runtime_owned(self):
        from hooks.runtime_artifact_guard import guard
        path = '/tmp/artifacts/.autobizdevops/features/alpha/frontend-verification/T001/run.json'
        self.assertIn('RUNTIME_ARTIFACT_OWNED', guard({'tool_name': 'write_file', 'tool_input': {'path': path}}))
        self.assertIsNone(guard({'tool_name': 'read_file', 'tool_input': {'path': path}}))

    def test_failed_optional_check_revokes_old_pass_and_retries_same_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            args = Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'], code_workspace=code,
                parallel_run_id=None, lease_token=None, workspace_ref=None, candidate_wave=None, target=['src/Page.tsx'],
                check_script=None, capture=False, actual_screenshot=None, visual_report=None, timeout=60)
            self.assertIn(fv.verify(args)['status'], {'passed', 'has-suggestions'})
            args.check_script = 'typecheck'
            with patch.object(fv, 'check_script', side_effect=subprocess.TimeoutExpired('tsc', 1)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    fv.verify(args)
            self.assertEqual(fv.read(fv.report_path(fd, 'T001', state['runId']))['status'], 'blocked')
            with self.assertRaisesRegex(ValueError, 'missing_or_failed'):
                fv.assert_current(fd, {'id': 'T001'}, state, code)
            with patch.object(fv, 'check_script', return_value={'status': 'failed', 'exitCode': 1}) as check:
                self.assertEqual(fv.verify(args)['status'], 'failed')
                check.assert_called_once()
            with patch.object(fv, 'check_script', return_value={'status': 'passed', 'exitCode': 0}) as check:
                self.assertIn(fv.verify(args)['status'], {'passed', 'has-suggestions'})
                check.assert_called_once()

    def test_an_unrelated_target_does_not_skip_changed_task_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            (code / 'src/Missed.tsx').write_text('import "./missing";\nexport default function Missed(){return <div>Ready</div>}')
            result = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(result.returncode, 1, result.stdout)
            report = fv.read(fv.report_path(fd, 'T001', state['runId']))
            self.assertIn('src/Missed.tsx', report['binding']['targets'])

    def test_real_review_updates_the_legacy_code_done_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            from hooks.resolve_frontend_html_route import resolve_frontend_route, evidence_path
            resolve_frontend_route(workspace, 'alpha', task_id='T001', write_evidence=True)
            checked = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertIn(fv.read(evidence_path(workspace, 'alpha'))['reviewStatus'], {'passed', 'has-suggestions'})
            (code / 'src/Page.tsx').write_text('import "./missing";\nexport const Page = () => <div>Ready</div>')
            checked = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertEqual(fv.read(evidence_path(workspace, 'alpha'))['reviewStatus'], 'failed')

    def test_mapped_asset_changes_invalidate_even_without_js_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); code = root / 'project'; (code / 'src').mkdir(parents=True)
            from tests.test_task_runner import _git
            _git(code, 'init', '-b', 'main')
            (code / 'src/Page.tsx').write_text('export const Page = () => <img src="/logo.svg" />')
            source = root / 'page.html'; source.write_text('<img src="logo.svg">')
            (root / 'logo.svg').write_text('<svg>original</svg>')
            module = fv.asset_module()
            mapping = module.generate_mapping_table('page', [source], code)
            module.copy_assets_to_project(mapping['mappings'], code)
            folder = code / '.frontend/html-analysis'; folder.mkdir(parents=True)
            (folder / 'page-assets-mapping.json').write_text(json.dumps(mapping))
            route = {'route': 'absolute-html', 'htmlSourcePaths': [str(source)]}
            first = fv.binding(code, route, ['src/Page.tsx'])
            (code / mapping['mappings'][0]['targetPath']).write_text('<svg>changed</svg>')
            second = fv.binding(code, route, ['src/Page.tsx'])
            self.assertEqual(first['codeDigest'], second['codeDigest'])
            self.assertNotEqual(first['assetDigest'], second['assetDigest'])

    def test_retired_candidate_command_leaves_implementation_report_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace, fd, code, state = self.setup_task(Path(tmp))
            checked = _verify_frontend(workspace, code, state['runId'], 'src/Page.tsx')
            self.assertEqual(checked.returncode, 0, checked.stdout)
            path = fv.report_path(fd, 'T001', state['runId'])
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'candidate_recheck_retired'):
                fv.verify(Namespace(workspace=workspace, feature='alpha', task_id='T001', run_id=state['runId'],
                                    code_workspace=Path(tmp) / 'candidate', candidate_wave=1))
            self.assertEqual(path.read_bytes(), original)

    def test_managed_preview_is_stopped_when_capture_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'package.json').write_text(json.dumps({'scripts': {'dev': 'vite'}}))
            args = Namespace(url=None, preview_script='dev', page_path='/', timeout=1, width=1440, height=900)
            server = Mock(pid=12345)
            renderer = Mock(pid=12346)
            renderer.poll.return_value = None
            renderer.communicate.side_effect = subprocess.TimeoutExpired('node', 1)
            socket = Mock()
            socket.__enter__ = Mock(return_value=socket)
            socket.__exit__ = Mock(return_value=False)
            socket.getsockname.return_value = ('127.0.0.1', 12345)
            with patch('socket.socket', return_value=socket), patch.object(fv.subprocess, 'Popen', side_effect=[server, renderer]), patch.object(fv.subprocess, 'run', side_effect=subprocess.TimeoutExpired('node', 1)), patch('urllib.request.urlopen'), patch.object(fv.os, 'killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired):
                    fv.capture(root, root, args, [])
                if fv.os.name != 'nt':
                    self.assertEqual(kill.call_count, 2)
                server.wait.assert_called()
