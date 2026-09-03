"""Spring 单测参考按域渲染契约。"""

from __future__ import annotations

import contextlib
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.render_spring_test_reference import (  # noqa: E402
    ALL_DOMAINS,
    DOMAINS,
    REFERENCE_PATH,
    SpringTestReferenceError,
    main,
    parse_sections,
    render,
)

UTEST_SKILL = ROOT / "skills" / "autodev" / "autodev-utest" / "SKILL.md"
QUALITY_REFERENCE = ROOT / "skills" / "references" / "test-quality.md"
BUNDLED_LICENSE = (
    ROOT
    / "skills"
    / "autodev"
    / "autodev-utest"
    / "reference"
    / "LICENSE.spring-testing-skills-Apache-2.0.txt"
)
SOURCE_LICENSE_SHA256 = "ba6fad3f5af3a381bedbe0095a8b5b2a6ee2f60fd77fa257b8146490ed3ef50d"


FIXTURE = """编辑说明不渲染。
<!-- section: 通用 | domain: * -->
COMMON
<!-- section: MVC | domain: mvc -->
MVC_ONLY
<!-- section: 共享 | domain: mvc,security -->
MVC_SECURITY
<!-- section: Security | domain: security -->
SECURITY_ONLY
"""


class SpringTestReferenceRendererTest(unittest.TestCase):
    def _source(self, text):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "reference.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_single_domain_contains_common_and_matching_sections(self):
        output = render("mvc", source=self._source(FIXTURE))

        self.assertIn("COMMON", output)
        self.assertIn("MVC_ONLY", output)
        self.assertIn("MVC_SECURITY", output)
        self.assertNotIn("SECURITY_ONLY", output)

    def test_multiple_domains_are_a_source_ordered_union(self):
        output = render("security,mvc", source=self._source(FIXTURE))

        self.assertEqual(1, output.count("COMMON"))
        self.assertEqual(1, output.count("MVC_SECURITY"))
        self.assertLess(output.index("MVC_ONLY"), output.index("SECURITY_ONLY"))

    def test_duplicate_requested_domain_is_ignored(self):
        output = render("mvc,mvc", source=self._source(FIXTURE))

        self.assertEqual(1, output.count("MVC_ONLY"))
        self.assertIn("Spring 单测参考 · mvc", output)

    def test_unknown_requested_domain_lists_legal_values_and_fix(self):
        with self.assertRaises(SpringTestReferenceError) as caught:
            render("nope", source=self._source(FIXTURE))

        message = str(caught.exception)
        self.assertIn("修复：", message)
        for domain in DOMAINS:
            self.assertIn(domain, message)

    def test_empty_requested_domain_explains_the_fix(self):
        with self.assertRaises(SpringTestReferenceError) as caught:
            render(" , ", source=self._source(FIXTURE))

        self.assertIn("修复：", str(caught.exception))

    def test_missing_marker_explains_the_fix(self):
        with self.assertRaises(SpringTestReferenceError) as caught:
            render("mvc", source=self._source("plain text"))

        self.assertIn("修复：", str(caught.exception))

    def test_unreadable_encoding_explains_the_fix(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "reference.md"
        source.write_bytes(b"\xff")

        with self.assertRaises(SpringTestReferenceError) as caught:
            render("mvc", source=source)

        self.assertIn("UTF-8", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_empty_section_domain_explains_the_fix(self):
        source = self._source("<!-- section: empty | domain: -->\nbody\n")

        with self.assertRaises(SpringTestReferenceError) as caught:
            render("mvc", source=source)

        self.assertIn("domain 为空", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_unknown_section_domain_explains_the_fix(self):
        source = self._source("<!-- section: bad | domain: nope -->\nbody\n")

        with self.assertRaises(SpringTestReferenceError) as caught:
            render("mvc", source=source)

        self.assertIn("未知域", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_legal_domain_without_specific_section_is_rejected(self):
        source = self._source("<!-- section: common | domain: * -->\nbody\n")

        with self.assertRaises(SpringTestReferenceError) as caught:
            render("mvc", source=source)

        self.assertIn("没有匹配到专属小节", str(caught.exception))
        self.assertIn("修复：", str(caught.exception))

    def test_cli_error_goes_to_stderr(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(["--domain", "nope", "--source", str(self._source(FIXTURE))])

        self.assertEqual(1, result)
        self.assertIn("render_spring_test_reference_failed", stderr.getvalue())
        self.assertIn("修复：", stderr.getvalue())

    def test_cli_argument_error_explains_the_fix(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(["--domain"])

        self.assertEqual(1, result)
        self.assertIn("命令参数无效", stderr.getvalue())
        self.assertIn("修复：", stderr.getvalue())

    def test_every_real_domain_output_is_bounded(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                self.assertLessEqual(len(render(domain).splitlines()), 400)

    def test_real_common_sections_are_bounded(self):
        sections = parse_sections(REFERENCE_PATH.read_text(encoding="utf-8"))
        common_lines = sum(
            len(section["lines"])
            for section in sections
            if ALL_DOMAINS in section["domains"]
        )

        self.assertLessEqual(common_lines, 60)


class SpringTestReferenceIntegrationContractTest(unittest.TestCase):
    def test_reference_excludes_out_of_scope_symbols(self):
        content = REFERENCE_PATH.read_text(encoding="utf-8")
        forbidden = (
            "@MockitoBean",
            "@MockitoSpyBean",
            "MockMvcTester",
            "RestTestClient",
            "@DataJpaTest",
            "TestEntityManager",
            "JpaRepository",
            "@Modifying",
            "javax.persistence",
            "jakarta.persistence",
            "Hibernate",
            "@MybatisTest",
            "SqlSession",
            "RANDOM_PORT",
            "WebSocketStompClient",
            "StompSession",
        )

        for symbol in forbidden:
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, content)

    def test_reference_pins_boot_2_and_3_versions(self):
        content = REFERENCE_PATH.read_text(encoding="utf-8")
        required = (
            "org.springframework.boot.test.mock.mockito.MockBean",
            "org.springframework.boot.test.mock.mockito.SpyBean",
            "javax.validation.Valid",
            "jakarta.validation.Valid",
            "Security 5.2+",
            "Security 5.3+",
            "ApplicationContextInitializer",
            "TestPropertyValues",
            "Framework 5.2.5+",
            "@ServiceConnection",
            "Boot 3.1+",
        )

        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_utest_skill_has_one_runtime_entrypoint(self):
        content = UTEST_SKILL.read_text(encoding="utf-8")

        self.assertEqual(1, content.count("render_spring_test_reference.py"))
        self.assertIn(
            'python "${pluginPath}/hooks/render_spring_test_reference.py" --domain <domain>',
            content,
        )
        self.assertIn("version: v1.2.08311", content)
        self.assertNotIn("绝不 mock 自己的类或内部协作者", content)
        self.assertNotIn("别走侧信道（如直接查库断言）", content)

    def test_shared_quality_rules_cover_slice_and_persistence_exceptions(self):
        content = QUALITY_REFERENCE.read_text(encoding="utf-8")
        required = (
            "验证读取：用独立持久化通道准备数据",
            "验证写入：经被测接口写入",
            "框架切片测试允许替换切片边界外的协作者",
            "不得替换被测对象本身",
        )

        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)
        self.assertNotIn("**只 mock**", content)
        self.assertNotIn("**绝不 mock**", content)

    def test_bundled_license_matches_reviewed_source_hash(self):
        digest = hashlib.sha256(BUNDLED_LICENSE.read_bytes()).hexdigest()

        self.assertEqual(SOURCE_LICENSE_SHA256, digest)


if __name__ == "__main__":
    unittest.main()
