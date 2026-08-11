#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hooks.e2e_quality_check import QualityCheckError, resolve, scan, scan_source


class E2EQualityPatternTest(unittest.TestCase):
    def _rules(self, source: str) -> set[str]:
        return {item["rule"] for item in scan_source("e2e/example.spec.ts", source)}

    def test_eight_false_green_patterns_are_detected(self) -> None:
        examples = {
            "locator-truthy": "test('x', async ({ page }) => { expect(page.getByRole('button')).toBeTruthy(); });",
            "no-await": "test('x', async ({ page }) => { expect(page.getByRole('button')).toBeVisible(); });",
            "discarded-state-read": "test('x', async ({ page }) => { await page.getByRole('button').isVisible(); expect(true).toBe(true); });",
            "conditional-assertion": "test('x', async ({ page }) => { if (ready) { await expect(page).toHaveURL('/ok'); } });",
            "only-leak": "test.only('x', async ({ page }) => { await expect(page).toHaveURL('/ok'); });",
            "swallowed-exception": "test('x', async () => { try { await work(); } catch (error) {} expect(true).toBe(true); });",
            "zero-assertion": "test('x', async ({ page }) => { await page.goto('/'); });",
            "expected-failure": "test('x', async ({ page }) => { test.fail(); await expect(page).toHaveURL('/'); });",
        }
        for expected, source in examples.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, self._rules(source))

    def test_five_valid_patterns_are_not_flagged(self) -> None:
        examples = [
            "test('x', async ({ page }) => { await expect(page.getByRole('button')).toBeVisible(); });",
            "test('x', async ({ page }) => { await page.getByRole('button').click(); await expect(page).toHaveURL('/ok'); });",
            "test('x', async ({ page }) => { const visible = await page.getByRole('button').isVisible(); expect(visible).toBe(true); });",
            "test('x', async () => { try { await work(); } catch (error) { console.error(error); throw error; } expect(true).toBe(true); });",
            "test('x', async ({ page }) => { await expect(page.getByText('done')).not.toBeVisible(); });",
        ]
        forbidden = {
            "locator-truthy",
            "no-await",
            "discarded-state-read",
            "conditional-assertion",
            "only-leak",
            "swallowed-exception",
            "zero-assertion",
            "expected-failure",
        }
        for source in examples:
            with self.subTest(source=source):
                self.assertFalse(self._rules(source) & forbidden)

    def test_later_same_line_call_and_nested_if_condition_are_scanned(self) -> None:
        same_line = (
            "test('x', async ({ page }) => { await page.click('a'); "
            "page.click('b'); await expect(page).toHaveURL('/'); });"
        )
        nested_if = (
            "test('x', async ({ page }) => { if (await page.isVisible()) { "
            "await expect(page).toHaveURL('/'); } });"
        )
        self.assertIn("no-await", self._rules(same_line))
        self.assertIn("conditional-assertion", self._rules(nested_if))
        self.assertIn("zero-assertion", self._rules("test('x', () => helper());"))


class E2EQualityArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        self.repo = root / "repo"
        (self.repo / "e2e").mkdir(parents=True)

    def test_candidate_blocks_until_attributed_dismissal(self) -> None:
        spec = self.repo / "e2e" / "alpha.spec.ts"
        spec.write_text(
            "test('x', async ({ page }) => { expect(page.getByRole('button')).toBeVisible(); });\n",
            encoding="utf-8",
        )
        payload = scan(
            self.workspace,
            "alpha",
            self.repo,
            ["e2e/alpha.spec.ts"],
            [],
            [],
        )
        self.assertFalse(payload["passed"])
        finding = next(item for item in payload["findings"] if item["rule"] == "no-await")

        resolved = resolve(
            self.workspace,
            "alpha",
            finding["findingId"],
            "dismissed",
            "reviewer",
            "fixture returns an already awaited matcher",
            [],
            None,
            "blocker",
            None,
            None,
            None,
        )

        self.assertTrue(resolved["passed"])
        self.assertEqual("dismissed", resolved["findings"][0]["status"])
        self.assertIsNotNone(resolved["findings"][0]["reviewedAt"])

    def test_unresolved_alias_blocks_and_input_dir_is_conservative_escape(self) -> None:
        spec = self.repo / "e2e" / "alpha.spec.ts"
        spec.write_text(
            "import { helper } from '@missing/helper';\n"
            "test('x', async () => { expect(helper()).toBe(true); });\n",
            encoding="utf-8",
        )
        (self.repo / "tsconfig.json").write_text(
            '{"compilerOptions":{"baseUrl":".","paths":{"@missing/*":["support/*"]}}}\n',
            encoding="utf-8",
        )
        blocked = scan(
            self.workspace,
            "alpha",
            self.repo,
            ["e2e/alpha.spec.ts"],
            [],
            [],
        )
        self.assertFalse(blocked["passed"])
        self.assertEqual("alias_unresolved", blocked["unresolvedImports"][0]["reason"])

        (self.repo / "support").mkdir()
        (self.repo / "support" / "placeholder.ts").write_text("export const value = true;\n", encoding="utf-8")
        (self.repo / "support" / "storage-state.json").write_text("{}\n", encoding="utf-8")
        conservative = scan(
            self.workspace,
            "alpha",
            self.repo,
            ["e2e/alpha.spec.ts"],
            [],
            ["support"],
        )
        self.assertTrue(conservative["passed"])
        self.assertEqual([], conservative["unresolvedImports"])
        self.assertIn(
            "support/storage-state.json",
            {item["path"] for item in conservative["scannedInputs"]},
        )

    def test_explicit_input_resolves_matching_blind_path_and_config_imports_are_followed(self) -> None:
        spec = self.repo / "e2e" / "alpha.spec.ts"
        spec.write_text(
            "import state from '@generated/state';\n"
            "const lazy = import('../support/dynamic');\n"
            "test('x', async () => { expect(state).toBeTruthy(); });\n",
            encoding="utf-8",
        )
        support = self.repo / "support"
        support.mkdir()
        (support / "state.json").write_text("{}\n", encoding="utf-8")
        (support / "config-helper.ts").write_text("export const retries = 0;\n", encoding="utf-8")
        (support / "dynamic.ts").write_text("export const value = true;\n", encoding="utf-8")
        (self.repo / "playwright.config.ts").write_text(
            "import { retries } from './support/config-helper';\nexport default { retries };\n",
            encoding="utf-8",
        )
        (self.repo / "tsconfig.json").write_text(
            '{"compilerOptions":{"baseUrl":".","paths":{"@generated/*":["support/*"]}}}\n',
            encoding="utf-8",
        )

        payload = scan(
            self.workspace,
            "alpha",
            self.repo,
            ["e2e/alpha.spec.ts"],
            ["support/state.json"],
            [],
        )

        self.assertTrue(payload["passed"], payload)
        paths = {item["path"] for item in payload["scannedInputs"]}
        self.assertIn("support/state.json", paths)
        self.assertIn("support/config-helper.ts", paths)
        self.assertIn("support/dynamic.ts", paths)

    def test_resolve_input_cannot_refresh_a_changed_scanned_file(self) -> None:
        spec = self.repo / "e2e" / "alpha.spec.ts"
        spec.write_text(
            "test('x', async ({ page }) => { expect(page).toHaveURL('/'); });\n",
            encoding="utf-8",
        )
        payload = scan(self.workspace, "alpha", self.repo, ["e2e/alpha.spec.ts"], [], [])
        finding = next(item for item in payload["findings"] if item["rule"] == "no-await")
        spec.write_text("// changed\n" + spec.read_text(encoding="utf-8"), encoding="utf-8")

        with self.assertRaises(QualityCheckError) as caught:
            resolve(
                self.workspace,
                "alpha",
                finding["findingId"],
                "dismissed",
                "reviewer",
                "known wrapper",
                ["e2e/alpha.spec.ts"],
                None,
                "blocker",
                None,
                None,
                None,
            )
        self.assertIn("重新运行 scan", str(caught.exception))

    def test_changed_input_resets_preserved_resolution(self) -> None:
        spec = self.repo / "e2e" / "alpha.spec.ts"
        spec.write_text(
            "test('x', async ({ page }) => { expect(page).toHaveURL('/'); });\n",
            encoding="utf-8",
        )
        first = scan(self.workspace, "alpha", self.repo, ["e2e/alpha.spec.ts"], [], [])
        finding = next(item for item in first["findings"] if item["rule"] == "no-await")
        resolve(
            self.workspace,
            "alpha",
            finding["findingId"],
            "dismissed",
            "reviewer",
            "known wrapper",
            [],
            None,
            "blocker",
            None,
            None,
            None,
        )
        spec.write_text(
            "// changed\ntest('x', async ({ page }) => { expect(page).toHaveURL('/'); });\n",
            encoding="utf-8",
        )
        rescanned = scan(self.workspace, "alpha", self.repo, ["e2e/alpha.spec.ts"], [], [])
        finding = next(item for item in rescanned["findings"] if item["rule"] == "no-await")
        self.assertEqual("candidate", finding["status"])
        self.assertFalse(rescanned["passed"])

    def test_semantic_review_input_survives_rescan_and_change_resets_verdict(self) -> None:
        spec = self.repo / "e2e" / "alpha.spec.ts"
        spec.write_text(
            "test('x', async () => { expect(true).toBe(true); });\n",
            encoding="utf-8",
        )
        source = self.repo / "support" / "checkout.ts"
        source.parent.mkdir()
        source.write_text("export const checkout = true;\n", encoding="utf-8")
        scan(self.workspace, "alpha", self.repo, ["e2e/alpha.spec.ts"], [], [])
        resolved = resolve(
            self.workspace,
            "alpha",
            None,
            "confirmed",
            "reviewer",
            "checkout state transition is not asserted",
            [],
            "semantic:missing-state-assertion",
            "blocker",
            "support/checkout.ts",
            1,
            "checkout state transition",
        )
        semantic = next(
            item for item in resolved["findings"] if item["rule"].startswith("semantic:")
        )
        self.assertEqual("confirmed", semantic["status"])

        unchanged = scan(self.workspace, "alpha", self.repo, ["e2e/alpha.spec.ts"], [], [])
        semantic = next(
            item for item in unchanged["findings"] if item["rule"].startswith("semantic:")
        )
        self.assertEqual("confirmed", semantic["status"])
        self.assertIn(
            "support/checkout.ts",
            {entry["path"] for entry in unchanged["scannedInputs"]},
        )

        source.write_text("export const checkout = false;\n", encoding="utf-8")
        changed = scan(self.workspace, "alpha", self.repo, ["e2e/alpha.spec.ts"], [], [])
        semantic = next(
            item for item in changed["findings"] if item["rule"].startswith("semantic:")
        )
        self.assertEqual("candidate", semantic["status"])
        self.assertIsNone(semantic["reviewer"])
        self.assertFalse(changed["passed"])


if __name__ == "__main__":
    unittest.main()
