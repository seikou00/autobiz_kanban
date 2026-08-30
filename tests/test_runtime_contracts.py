#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks.candidate_digest import compute as compute_candidate_digest  # noqa: E402
from hooks.run_context import breadcrumb, inject_hook, load, persist  # noqa: E402
from hooks.runtime_artifact_guard import guard  # noqa: E402
from hooks.plan_writer import (  # noqa: E402
    _apply_runtime_validation_profiles,
    _runtime_task_group_lane_errors,
)
from hooks.validation_capabilities import (  # noqa: E402
    command_errors,
    persist as persist_capabilities,
    refresh as refresh_capabilities,
)
from hooks.verified_digest_guard import invalidate_if_stale  # noqa: E402
from hooks.utest_workspace_binding import (  # noqa: E402
    UTestWorkspaceBindingError,
    _run_context_execution_roots,
    _validate_expected_file_evidence,
)


class RuntimeContractsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "output"
        self.feature_dir = self.workspace / ".autobizdevops" / "features" / "alpha"
        self.feature_dir.mkdir(parents=True)
        state_dir = self.workspace / ".autobizdevops"
        (state_dir / "state.json").write_text("{}\n", encoding="utf-8")
        self.repo = self.root / "repo"
        self.module = self.repo / "ui-admin"
        self.module.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.module / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build", "test": "vitest"}}),
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(self.repo), "-c", "user.name=Autodev",
                "-c", "user.email=autodev@example.invalid", "commit", "-q", "-m", "init",
            ],
            check=True,
        )
        self.context = persist(
            self.workspace,
            "alpha",
            [{"deployUnitId": "ui", "localRepoPath": str(self.module)}],
        )

    def test_run_context_resolves_canonical_roots_and_broadcasts(self):
        loaded = load(self.workspace, "alpha")
        self.assertEqual("ready", loaded["status"])
        self.assertEqual(str(self.repo.resolve()), loaded["repositories"][0]["root"])
        self.assertEqual("ui-admin", loaded["modules"][0]["relativeRoot"])
        broadcast = breadcrumb(self.workspace, "alpha")
        self.assertIn(loaded["contextDigest"], broadcast)
        self.assertIn(str(self.module.resolve()), broadcast)
        self.assertLess(len(broadcast), 1200)

    def test_runtime_guards_are_registered_at_prompt_and_tool_boundaries(self):
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        prompt_commands = [
            hook.get("command")
            for registration in config.get("UserPromptSubmit", [])
            for hook in registration.get("hooks", [])
        ]
        self.assertIn("python hooks/run_context.py inject", prompt_commands)
        pre_commands = [
            hook.get("command")
            for registration in config.get("PreToolUse", [])
            for hook in registration.get("hooks", [])
        ]
        post_commands = [
            hook.get("command")
            for registration in config.get("PostToolUse", [])
            for hook in registration.get("hooks", [])
        ]
        self.assertIn("python hooks/runtime_artifact_guard.py", pre_commands)
        self.assertIn("python hooks/verified_digest_guard.py", post_commands)
        commands = prompt_commands + pre_commands + post_commands
        self.assertTrue(all(command.startswith("python ") for command in commands))

    def test_capability_catalog_is_manifest_derived_and_fail_closed(self):
        with mock.patch("hooks.validation_capabilities.shutil.which", return_value="/usr/bin/npm"):
            catalog = persist_capabilities(self.feature_dir, self.context)
        build = catalog["capabilities"][0]
        self.assertEqual(["npm", "run", "build"], build["argv"])
        self.assertEqual("ui-admin", build["cwd"])
        self.assertEqual([], command_errors(catalog, {"argv": build["argv"], "cwd": build["cwd"], "kind": "compile"}, "batch"))
        self.assertEqual(
            ["batch.validation_capability_unrecognized"],
            command_errors(catalog, {"argv": ["ls", "ui-admin"], "cwd": ".", "kind": "compile"}, "batch"),
        )

        plan = {
            "tasks": [{
                "id": "T001",
                "uiRequired": True,
                "workspaceRef": "default",
                "scope": {"workspaceRoots": {"default": "ui-admin"}},
            }]
        }
        self.assertEqual([], _apply_runtime_validation_profiles(self.feature_dir, plan))
        generated = plan["batchValidationProfiles"]["frontend"]["commands"]
        self.assertEqual(1, len(generated))
        self.assertEqual(["npm", "run", "build"], generated[0]["argv"])
        self.assertEqual(build["capabilityId"], generated[0]["capabilityId"])

    def test_capability_catalog_discovers_nested_frontend_manifest_without_leaking_to_parent_module(self):
        mono = self.root / "mono"
        frontend_parent = mono / "yudao-ui"
        frontend_app = frontend_parent / "yudao-ui-admin-vue3"
        frontend_app.mkdir(parents=True)
        (mono / "pom.xml").write_text("<project />\n", encoding="utf-8")
        (frontend_app / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build", "typecheck": "vue-tsc --noEmit"}}),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(mono)], check=True)
        subprocess.run(["git", "-C", str(mono), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(mono), "-c", "user.name=Autodev",
                "-c", "user.email=autodev@example.invalid", "commit", "-q", "-m", "init",
            ],
            check=True,
        )
        context = persist(
            self.workspace,
            "nested",
            [
                {"deployUnitId": "backend", "localRepoPath": str(mono)},
                {"deployUnitId": "frontend", "localRepoPath": str(frontend_parent)},
            ],
        )
        nested_feature = self.workspace / ".autobizdevops" / "features" / "nested"
        with mock.patch("hooks.validation_capabilities.shutil.which", side_effect=lambda name: "/usr/bin/" + name):
            catalog = persist_capabilities(nested_feature, context)

        backend = [item for item in catalog["capabilities"] if item["moduleId"] == "backend"]
        frontend = [item for item in catalog["capabilities"] if item["moduleId"] == "frontend"]
        self.assertEqual(["pom.xml"], [item["source"] for item in backend])
        self.assertEqual(
            {"package.json#scripts.build", "package.json#scripts.typecheck"},
            {item["source"] for item in frontend},
        )
        self.assertEqual(
            {"yudao-ui/yudao-ui-admin-vue3"},
            {item["cwd"] for item in frontend},
        )

        plan = {
            "tasks": [
                {"id": "T001", "uiRequired": False, "scope": {"workspaceRoots": {"default": "."}}},
                {"id": "T002", "uiRequired": True, "scope": {"workspaceRoots": {"default": "."}}},
            ]
        }
        self.assertEqual([], _apply_runtime_validation_profiles(nested_feature, plan))
        self.assertEqual(["mvn", "compile"], plan["batchValidationProfiles"]["backend"]["commands"][0]["argv"])
        self.assertEqual(
            ["npm", "run", "build"],
            plan["batchValidationProfiles"]["frontend"]["commands"][0]["argv"],
        )

    def test_single_root_deploy_unit_selects_exact_nested_frontend_workspace(self):
        mono = self.root / "single-root"
        vue2 = mono / "yudao-ui" / "yudao-ui-admin-vue2"
        vue3 = mono / "yudao-ui" / "yudao-ui-admin-vue3"
        vue2.mkdir(parents=True)
        vue3.mkdir(parents=True)
        (mono / "pom.xml").write_text("<project />\n", encoding="utf-8")
        for app in (vue2, vue3):
            (app / "package.json").write_text(
                json.dumps({"scripts": {"build": "vite build"}}),
                encoding="utf-8",
            )
        subprocess.run(["git", "init", "-q", str(mono)], check=True)
        subprocess.run(["git", "-C", str(mono), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(mono), "-c", "user.name=Autodev",
            "-c", "user.email=autodev@example.invalid", "commit", "-q", "-m", "init",
        ], check=True)
        context = persist(
            self.workspace,
            "single-root",
            [{"deployUnitId": "application", "localRepoPath": str(mono)}],
        )
        feature_dir = self.workspace / ".autobizdevops" / "features" / "single-root"
        with mock.patch("hooks.validation_capabilities.shutil.which", side_effect=lambda name: "/usr/bin/" + name):
            persist_capabilities(feature_dir, context)

        exact_plan = {"tasks": [
            {"id": "T001", "uiRequired": False, "scope": {"workspaceRoots": {"default": "."}}},
            {
                "id": "T002",
                "uiRequired": True,
                "scope": {"workspaceRoots": {"default": "yudao-ui/yudao-ui-admin-vue3"}},
            },
        ]}
        self.assertEqual([], _apply_runtime_validation_profiles(feature_dir, exact_plan))
        frontend_commands = exact_plan["batchValidationProfiles"]["frontend"]["commands"]
        self.assertEqual(1, len(frontend_commands))
        self.assertEqual("yudao-ui/yudao-ui-admin-vue3", frontend_commands[0]["cwd"])

        broad_plan = {"tasks": [{
            "id": "T001",
            "uiRequired": True,
            "scope": {"workspaceRoots": {"default": "yudao-ui"}},
        }]}
        self.assertEqual([], _apply_runtime_validation_profiles(feature_dir, broad_plan))
        self.assertEqual(
            {
                "yudao-ui/yudao-ui-admin-vue2",
                "yudao-ui/yudao-ui-admin-vue3",
            },
            {
                item["cwd"]
                for item in broad_plan["batchValidationProfiles"]["frontend"]["commands"]
            },
        )

    def test_multi_repository_root_capabilities_are_not_deduplicated(self):
        selected = []
        repository_names = []
        for module_id in ("order", "user"):
            repository = self.root / "svc-{}".format(module_id)
            repository.mkdir()
            (repository / "pom.xml").write_text("<project />\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run([
                "git", "-C", str(repository), "-c", "user.name=Autodev",
                "-c", "user.email=autodev@example.invalid", "commit", "-q", "-m", "init",
            ], check=True)
            selected.append({"deployUnitId": module_id, "localRepoPath": str(repository)})
            repository_names.append(repository.name)
        context = persist(self.workspace, "multi-repo", selected)
        feature_dir = self.workspace / ".autobizdevops" / "features" / "multi-repo"
        with mock.patch("hooks.validation_capabilities.shutil.which", return_value="/usr/bin/mvn"):
            catalog = persist_capabilities(feature_dir, context)
        self.assertEqual({"order", "user"}, {item["moduleId"] for item in catalog["capabilities"]})

        plan = {"tasks": [
            {
                "id": "T001",
                "uiRequired": False,
                "scope": {"workspaceRoots": {repository_names[0]: "."}},
            },
            {
                "id": "T002",
                "uiRequired": False,
                "scope": {"workspaceRoots": {repository_names[1]: "."}},
            },
        ]}
        self.assertEqual([], _apply_runtime_validation_profiles(feature_dir, plan))
        commands = plan["batchValidationProfiles"]["backend"]["commands"]
        self.assertEqual(2, len(commands))
        self.assertEqual(set(repository_names), {item["repo"] for item in commands})
        self.assertEqual(2, len({item["capabilityId"] for item in commands}))

    def test_missing_lane_toolchain_is_actionable_before_task_details(self):
        with mock.patch("hooks.validation_capabilities.shutil.which", return_value=None):
            persist_capabilities(self.feature_dir, self.context)
        plan = {
            "tasks": [{
                "id": "T001",
                "uiRequired": True,
                "workspaceRef": "default",
                "scope": {"workspaceRoots": {"default": "ui-admin"}},
            }]
        }

        errors = _apply_runtime_validation_profiles(self.feature_dir, plan)

        self.assertEqual("validation_toolchain_unavailable", errors[0]["reason"])
        self.assertIs(errors[0]["retryable"], True)
        self.assertEqual(
            "install_missing_tool_and_refresh_validation_capabilities",
            errors[0]["requiredAction"],
        )
        self.assertIn("missingExecutables=npm", errors[0]["detail"])
        self.assertIn("validation_capabilities.py refresh", errors[0]["repairSuggestion"])

        group_errors = _runtime_task_group_lane_errors(
            self.feature_dir,
            {"groups": [{"id": "T001", "uiRequired": True}]},
        )
        self.assertEqual("validation_toolchain_unavailable", group_errors[0]["reason"])

    def test_capability_refresh_reports_missing_executable(self):
        with mock.patch("hooks.validation_capabilities.shutil.which", return_value=None), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            exit_code = refresh_capabilities(self.feature_dir)
        result = json.loads(output.getvalue())
        self.assertEqual(2, exit_code)
        self.assertEqual(["npm"], result["missingExecutables"])
        self.assertIn("重新执行本 refresh 命令", result["repairSuggestion"])

    def test_legacy_feature_without_run_context_keeps_manual_profile_and_guidance(self):
        feature_dir = self.workspace / ".autobizdevops" / "features" / "legacy"
        feature_dir.mkdir(parents=True)
        plan = {
            "tasks": [{"id": "T001", "uiRequired": False}],
            "batchValidationProfiles": {
                "backend": {
                    "mode": "commands",
                    "commands": [{
                        "argv": ["mvn", "compile"],
                        "cwd": ".",
                        "kind": "compile",
                        "required": True,
                    }],
                }
            },
        }
        expected = json.loads(json.dumps(plan["batchValidationProfiles"]))
        self.assertEqual([], _apply_runtime_validation_profiles(feature_dir, plan))
        self.assertEqual(expected, plan["batchValidationProfiles"])
        skill = (ROOT / "skills" / "autodev" / "autodev-plan" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("没有 `.runtime/RUN_CONTEXT.json` 时使用存量流程", skill)
        self.assertIn("add-batch-validation-command", skill)

    def test_runtime_owned_artifacts_block_direct_writes_and_evidence_append(self):
        env = {
            "PLUGIN_WORKSPACE": str(self.root),
            "PROJECT_DIR": "output",
            "FEATURE_ID": "alpha",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            reason = guard({
                "tool_name": "write_file",
                "tool_input": {"filePath": str(self.feature_dir / "UNIT_TEST_RESULT.json")},
            })
            self.assertIn("RUNTIME_ARTIFACT_OWNED", reason)
            reason = guard({
                "tool_name": "execute",
                "tool_input": {"command": "python hooks/evidence_store.py append --feature alpha"},
            })
            self.assertIn("禁止直接 append Evidence", reason)
            reason = guard({
                "tool_name": "execute",
                "tool_input": {"command": "printf '{}' | tee {}".format("{}", self.feature_dir / "FIX_REQUEST.json")},
            })
            self.assertIn("RUNTIME_ARTIFACT_OWNED", reason)
            self.assertIsNone(guard({
                "tool_name": "execute",
                "tool_input": {"command": "python hooks/unit_test_result_writer.py init --feature alpha --from-plan"},
            }))

        with mock.patch.dict(os.environ, {}, clear=True):
            reason = guard({
                "tool_name": "edit_file",
                "tool_input": {"file_path": str(self.feature_dir / ".runtime" / "VALIDATION_CAPABILITIES.json")},
            })
            self.assertIn("RUNTIME_ARTIFACT_GUARD_CONTEXT_MISSING", reason)
            reason = guard({
                "tool_name": "execute",
                "tool_input": {"command": "rm -rf .tmp/plan_writer/draft"},
            })
            self.assertIn("RUNTIME_ARTIFACT_GUARD_CONTEXT_MISSING", reason)

    def test_prompt_hook_is_observable_when_host_context_is_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "sys.stdin", io.StringIO("{}")
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(0, inject_hook())
        payload = json.loads(output.getvalue())
        self.assertIn("hook_context_unavailable", payload["additionalContext"])
        self.assertIn("retryable=false", payload["additionalContext"])

    def test_utest_consumes_expected_files_from_latest_implementation_evidence(self):
        expected = self.module / "src" / "page.vue"
        expected.parent.mkdir()
        expected.write_text("<template />\n", encoding="utf-8")
        evidence_dir = self.feature_dir / "evidence"
        evidence_dir.mkdir()
        (evidence_dir / "EVIDENCE.jsonl").write_text(
            json.dumps({
                "evidenceId": "ev_0001",
                "taskId": "T001",
                "changedFiles": ["ui-admin/src/page.vue"],
            }) + "\n",
            encoding="utf-8",
        )
        task = {
            "id": "T001",
            "workspaceRef": "default",
            "rawTask": {
                "expectedFiles": ["ui-admin/src/not-created.vue", "ui-admin/src/page.vue"],
                "implementationEvidenceIds": ["ev_0001"],
                "latestImplementationEvidenceId": "ev_0001",
                "scope": {
                    "modules": ["活动管理（业务语义）"],
                    "workspaceRoots": {"default": "ui-admin"},
                },
            },
        }
        _validate_expected_file_evidence(self.feature_dir, task, self.repo)
        roots = _run_context_execution_roots(
            self.workspace,
            "alpha",
            task,
            self.repo,
            [{"repo": "default", "cwd": ".", "root": self.repo}],
        )
        self.assertEqual([("ui", self.module.resolve())], roots)
        expected.unlink()
        with self.assertRaises(UTestWorkspaceBindingError) as caught:
            _validate_expected_file_evidence(self.feature_dir, task, self.repo)
        self.assertEqual("EVIDENCE_ROOT_MISMATCH", caught.exception.code)

    def test_verified_digest_change_triggers_code_stage_downgrade(self):
        original = compute_candidate_digest(self.workspace, "alpha")
        (self.feature_dir / "VERIFY_DECISION.json").write_text(
            json.dumps({"version": 1, "diffDigest": original}), encoding="utf-8"
        )
        (self.module / "src.js").write_text("export const changed = true;\n", encoding="utf-8")
        state = SimpleNamespace(
            fatal_errors=[], records={"alpha": {"checkpoint": "verify_done"}}
        )
        rollback_plan = SimpleNamespace(ok=True, errors=(), new_checkpoint="code_in_progress")
        rollback_result = SimpleNamespace(ok=True, errors=())
        with mock.patch("hooks.verified_digest_guard.load_state_json_records_result", return_value=state), mock.patch(
            "hooks.verified_digest_guard.prepare_stage_rollback", return_value=rollback_plan
        ) as prepare, mock.patch(
            "hooks.verified_digest_guard.execute_stage_rollback", return_value=rollback_result
        ):
            result = invalidate_if_stale(self.workspace, "alpha")
        self.assertEqual("code_in_progress", result["checkpoint"])
        self.assertNotEqual(result["oldDigest"], result["newDigest"])
        self.assertEqual("dev.code", prepare.call_args.kwargs["stage"])
        self.assertEqual("keep", prepare.call_args.kwargs["code_source"])


if __name__ == "__main__":
    unittest.main()
