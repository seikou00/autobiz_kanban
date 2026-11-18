"""Prevent runtime subprocess readers from falling back to the host code page."""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SubprocessEncodingTest(unittest.TestCase):
    def test_text_subprocess_readers_declare_encoding(self):
        paths = [path for folder in ('hooks', 'skills', 'scripts', 'board_core')
                 for path in (ROOT / folder).rglob('*.py')]
        paths.extend(ROOT.glob('*.py'))
        missing = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == 'subprocess'):
                    continue
                kwargs = {item.arg: item.value for item in node.keywords}
                reads_text = any(isinstance(kwargs.get(name), ast.Constant) and kwargs[name].value is True
                                 for name in ('text', 'universal_newlines'))
                if reads_text and 'encoding' not in kwargs:
                    missing.append(f'{path.relative_to(ROOT)}:{node.lineno}')
        self.assertEqual(missing, [], f'Locale-dependent subprocess readers: {missing}')
