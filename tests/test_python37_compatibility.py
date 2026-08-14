"""Regression checks for the repository's Python 3.7 runtime baseline."""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = tuple(sorted(ROOT.rglob("*.py")))
PYTHON_38_PLUS_ATTRIBUTES = {
    "is_relative_to",  # pathlib.Path, Python 3.9
    "removeprefix",  # str, Python 3.9
    "removesuffix",  # str, Python 3.9
}
PYTHON37_UNSUBSCRIPTABLE_BASES = {"AbstractContextManager"}
PYTHON39_GENERIC_BUILTINS = {"dict", "frozenset", "list", "set", "tuple", "type"}
UNAVAILABLE_STDLIB_MODULES = {
    "tomllib": "tomllib requires Python 3.11+",
}


def parse_as_python37(source, filename):
    try:
        return ast.parse(source, filename=filename, feature_version=7)
    except TypeError:
        # Python 3.7 has no feature_version argument; its own compiler is the
        # grammar check in that environment.
        return compile(source, filename, "exec", ast.PyCF_ONLY_AST)


def has_postponed_annotations(tree):
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def iter_annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


class Python37CompatibilityTest(unittest.TestCase):
    def test_all_python_files_use_python37_grammar(self):
        failures = []
        for path in PYTHON_FILES:
            source = path.read_text(encoding="utf-8-sig")
            try:
                parse_as_python37(source, str(path))
            except SyntaxError as exc:
                failures.append("{}:{}: {}".format(path.relative_to(ROOT), exc.lineno, exc.msg))
        self.assertEqual(failures, [])

    def test_all_python_files_avoid_known_newer_standard_library_apis(self):
        failures = []
        for path in PYTHON_FILES:
            source = path.read_text(encoding="utf-8-sig")
            tree = parse_as_python37(source, str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name.split(".", 1)[0]
                        if module in UNAVAILABLE_STDLIB_MODULES:
                            failures.append(
                                "{}:{}: {}".format(
                                    path.relative_to(ROOT), node.lineno, UNAVAILABLE_STDLIB_MODULES[module]
                                )
                            )
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module.split(".", 1)[0]
                    if module in UNAVAILABLE_STDLIB_MODULES:
                        failures.append(
                            "{}:{}: {}".format(
                                path.relative_to(ROOT), node.lineno, UNAVAILABLE_STDLIB_MODULES[module]
                            )
                        )
                if isinstance(node, ast.Attribute) and node.attr in PYTHON_38_PLUS_ATTRIBUTES:
                    failures.append(
                        "{}:{}: .{}() requires Python 3.9+".format(
                            path.relative_to(ROOT), node.lineno, node.attr
                        )
                    )
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        if (
                            isinstance(base, ast.Subscript)
                            and isinstance(base.value, ast.Name)
                            and base.value.id in PYTHON37_UNSUBSCRIPTABLE_BASES
                        ):
                            failures.append(
                                "{}:{}: {}[...] is not subscriptable on Python 3.7".format(
                                    path.relative_to(ROOT), node.lineno, base.value.id
                                )
                            )
            if not has_postponed_annotations(tree):
                for annotation in iter_annotations(tree):
                    for node in ast.walk(annotation):
                        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                            failures.append(
                                "{}:{}: PEP 604 union annotations require Python 3.10+ or postponed annotations".format(
                                    path.relative_to(ROOT), node.lineno
                                )
                            )
                        if (
                            isinstance(node, ast.Subscript)
                            and isinstance(node.value, ast.Name)
                            and node.value.id in PYTHON39_GENERIC_BUILTINS
                        ):
                            failures.append(
                                "{}:{}: {}[...] requires Python 3.9+ or postponed annotations".format(
                                    path.relative_to(ROOT), node.lineno, node.value.id
                                )
                            )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
