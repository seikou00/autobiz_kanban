#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.render_frontend_test_reference import (  # noqa: E402
    DOMAINS,
    FRAMEWORKS,
    MAX_RENDERED_LINES,
    REFERENCE_PATH,
    FrontendTestReferenceError,
    render,
)


LICENSE_PATH = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-utest"
    / "reference"
    / "LICENSE.vuejs-ai-MIT.txt"
)

FIXTURE = """editor note
<!-- section: common | framework: * | domain: * -->
COMMON
<!-- section: fundamentals | framework: * | domain: fundamentals -->
FUNDAMENTALS
<!-- section: vue-component | framework: vue | domain: component -->
VUE_COMPONENT
<!-- section: react-component | framework: react | domain: component -->
REACT_COMPONENT
<!-- section: vue-shared | framework: vue | domain: component,logic -->
VUE_SHARED
<!-- section: vue-logic | framework: vue | domain: logic -->
VUE_LOGIC
"""


class RenderFrontendTestReferenceTest(unittest.TestCase):
    def _source(self, text):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "frontend.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_framework_isolation(self):
        vue = render("vue", "component", source=self._source(FIXTURE))
        react = render("react", "component", source=self._source(FIXTURE))

        self.assertIn("VUE_COMPONENT", vue)
        self.assertNotIn("REACT_COMPONENT", vue)
        self.assertIn("REACT_COMPONENT", react)
        self.assertNotIn("VUE_COMPONENT", react)

    def test_multiple_domains_are_source_ordered_and_deduplicated(self):
        output = render("vue", "logic,component,logic", source=self._source(FIXTURE))

        self.assertEqual(1, output.count("COMMON"))
        self.assertEqual(1, output.count("VUE_SHARED"))
        self.assertLess(output.index("VUE_COMPONENT"), output.index("VUE_LOGIC"))

    def test_unknown_values_include_repair_and_legal_values(self):
        with self.assertRaises(FrontendTestReferenceError) as framework_error:
            render("svelte", "component", source=self._source(FIXTURE))
        with self.assertRaises(FrontendTestReferenceError) as domain_error:
            render("vue", "browser", source=self._source(FIXTURE))

        self.assertIn("修复：", str(framework_error.exception))
        self.assertIn("修复：", str(domain_error.exception))
        for value in FRAMEWORKS:
            self.assertIn(value, str(framework_error.exception))
        for value in DOMAINS:
            self.assertIn(value, str(domain_error.exception))

    def test_oversized_render_is_rejected(self):
        source = self._source(
            "<!-- section: common | framework: * | domain: * -->\n"
            + "\n".join("line" for _ in range(MAX_RENDERED_LINES))
            + "\n<!-- section: vue-component | framework: vue | domain: component -->\nbody\n"
        )

        with self.assertRaises(FrontendTestReferenceError) as caught:
            render("vue", "component", source=source)

        self.assertIn("行上限", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_all_real_framework_domain_outputs_are_bounded_and_isolated(self):
        for framework in FRAMEWORKS:
            for domain in DOMAINS:
                with self.subTest(framework=framework, domain=domain):
                    output = render(framework, domain)
                    self.assertLessEqual(len(output.splitlines()), MAX_RENDERED_LINES)
                    if framework == "vue":
                        self.assertNotIn("React component", output)
                    else:
                        self.assertNotIn("Vue component", output)

    def test_reference_and_license_keep_reviewed_attribution(self):
        reference = REFERENCE_PATH.read_text(encoding="utf-8")
        license_text = LICENSE_PATH.read_text(encoding="utf-8")

        self.assertIn("vuejs-ai", reference)
        self.assertIn("vuejs.org/guide/scaling-up/testing.html", reference)
        self.assertIn("testing-library.com", reference)
        self.assertIn("Copyright (c) 2025 hyf0, SerKo", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)


if __name__ == "__main__":
    unittest.main()
