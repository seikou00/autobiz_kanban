"""Artifact synchronization and catalog contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hooks import artifact_sync, artifact_sync_execute_hook, sync_artifacts  # noqa: E402


EXPECTED_OUTPUT_METADATA = {
    "PRD.md": ("requirement", "final"),
    "UI_CONTEXT.json": ("ui_context", "final"),
    "proposal.md": ("behavior_proposal", "final"),
    "specs/**/*.md": ("behavior_spec", "final"),
    "design.md": ("technical_design", "process"),
    ".design-contract.lock.json": ("technical_design_contract", "process"),
    "PLAN.md": ("implementation_plan", "process"),
    "plan.json": ("implementation_plan", "final"),
    "DETAIL_DESIGN.md": ("technical_detail", "process"),
    "evidence/EVIDENCE.jsonl": ("evidence_stream", "evidence"),
}


def _workflow_nodes() -> list[dict]:
    config = json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))
    workflow = config["workflow"]
    nodes = list(workflow.get("nodes", []))
    for profile in (workflow.get("profiles") or {}).values():
        if isinstance(profile, dict):
            nodes.extend(profile.get("nodes", []) or [])
    for stage in workflow.get("dynamicStages", []) or []:
        if isinstance(stage, dict):
            nodes.extend(stage.get("nodes", []) or [])
    return nodes


def _sample_path(path: str) -> str:
    if path == "specs/**/*.md":
        return "specs/example/spec.md"
    if path == "e2e-diagnostics/**/*":
        return "e2e-diagnostics/round-1/report.json"
    return path


class ArtifactCatalogContractTest(unittest.TestCase):
    def test_current_feature_record_ignores_invalid_other_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            state_dir = workspace / ".autobizdevops"
            state_dir.mkdir(parents=True)
            state_dir.joinpath("state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "alpha": {"feature": "alpha", "checkpoint": "code_in_progress"},
                            "broken-other": {
                                "feature": "broken-other",
                                "checkpoint": "missing_checkpoint",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            record = artifact_sync.current_feature_record(workspace, "alpha")

        self.assertEqual(record["checkpoint"], "code_in_progress")

    def test_current_biz_dev_outputs_have_expected_catalog_metadata(self) -> None:
        actual_paths = set()
        for node in _workflow_nodes():
            group = node.get("group") or node.get("phase")
            if group not in artifact_sync.UPLOAD_GROUPS:
                continue
            for output in (node.get("artifacts") or {}).get("outputs", []) or []:
                path = output["path"]
                actual_paths.add(path)
                metadata = artifact_sync.catalog_metadata_for_path(_sample_path(path))
                self.assertEqual(
                    (metadata["category"], metadata["lifecycle"]),
                    EXPECTED_OUTPUT_METADATA[path],
                    path,
                )

        self.assertEqual(actual_paths, set(EXPECTED_OUTPUT_METADATA))

    def test_optional_verify_and_original_requirement_artifacts_match_document(self) -> None:
        api_entry = artifact_sync.catalog_entry(
            path="FEATURE_API_DETAIL.md",
            stage="dev.code",
            upload_status="uploaded",
            size=12,
            sha256="abc",
        )
        self.assertEqual(api_entry["source"], "extra")
        self.assertEqual(api_entry["category"], "api_detail")
        self.assertEqual(api_entry["lifecycle"], "final")

        original_entry = artifact_sync.catalog_entry(
            path="prd_original/source.docx",
            stage="biz.prd",
            upload_status="skipped",
            size=artifact_sync.MAX_FILE_SIZE + 1,
            status_reason="file_size_exceeds_5mb",
        )
        self.assertEqual(original_entry["source"], "extra")
        self.assertEqual(original_entry["category"], "source_reference")
        self.assertEqual(original_entry["lifecycle"], "reference")
        self.assertEqual(original_entry["status_reason"], "file_size_exceeds_5mb")

    def test_catalog_writer_uses_required_fields_and_excludes_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp) / "alpha"
            feature_dir.mkdir()
            prd = feature_dir / "PRD.md"
            prd.write_text("# PRD\n", encoding="utf-8")
            artifact = artifact_sync.snapshot_file_artifact(
                feature_dir,
                prd,
                project_code="P001",
                feature="alpha",
                required=True,
            )

            catalog_snapshot = artifact_sync.write_artifact_catalog(
                feature_dir,
                feature="alpha",
                status=artifact_sync.default_status(),
                current_stage="biz.prd",
                current_artifacts=[artifact],
                current_missing=[],
                side_entries=[],
                project_code="P001",
            )

            payload = json.loads((feature_dir / artifact_sync.CATALOG_FILE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "autobizdevops.artifact-catalog.v1")
            self.assertEqual(payload["feature_id"], "alpha")
            self.assertTrue(payload["generated_at"])
            self.assertEqual(catalog_snapshot["path"], artifact_sync.CATALOG_FILE_NAME)
            self.assertNotIn(artifact_sync.CATALOG_FILE_NAME, {item["path"] for item in payload["artifacts"]})

            by_path = {item["path"]: item for item in payload["artifacts"]}
            self.assertEqual(by_path["PRD.md"]["upload_status"], "uploaded")
            self.assertEqual(by_path["PRD.md"]["source"], "workflow")
            self.assertNotIn("PRD_DISCUSS.md", by_path)
            for entry in payload["artifacts"]:
                self.assertTrue(
                    {
                        "path",
                        "stage",
                        "source",
                        "category",
                        "lifecycle",
                        "upload_status",
                        "description",
                    }.issubset(entry),
                    entry,
                )

    def test_catalog_upload_is_ordered_after_business_artifacts(self) -> None:
        artifacts = [
            {"path": artifact_sync.CATALOG_FILE_NAME},
            {"path": "proposal.md"},
            {"path": "PRD.md"},
        ]
        ordered = artifact_sync.order_upload_artifacts(artifacts)
        self.assertEqual(ordered[-1]["path"], artifact_sync.CATALOG_FILE_NAME)

    def test_oversized_artifact_is_skipped_with_repairable_reason(self) -> None:
        uploadable, skipped = artifact_sync.split_oversized_artifacts(
            [
                {
                    "path": "large.log",
                    "size": artifact_sync.MAX_FILE_SIZE + 1,
                    "sha256": "abc",
                }
            ],
            stage="dev.code",
        )
        self.assertEqual(uploadable, [])
        self.assertEqual(skipped[0]["upload_status"], "skipped")
        self.assertEqual(skipped[0]["status_reason"], "file_size_exceeds_5mb")

    def test_current_plan_checkpoint_produces_a_catalog_backed_sync_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            feature_dir.mkdir(parents=True)
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "alpha": {
                                "feature": "alpha",
                                "owner": "tester",
                                "checkpoint": "code_in_progress",
                                "stage": "Code",
                                "iteration": "1",
                                "updated_at": "2026-08-03 12:00:00",
                                "workflowProfile": "standard",
                                "workflowDecisions": {},
                                "workflowTemplate": "standard",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            for name in ("design.md", "PLAN.md", "plan.json"):
                (feature_dir / name).write_text("{}\n" if name.endswith(".json") else "# test\n", encoding="utf-8")

            resolved_dir, event_ids = artifact_sync.prepare_checkpoint_sync_events(
                workspace=workspace,
                feature="alpha",
                old_checkpoint="plan_done",
                new_checkpoint="code_in_progress",
                project_code="P001",
            )

            self.assertEqual(resolved_dir, feature_dir)
            self.assertEqual(len(event_ids), 1)
            status = artifact_sync.read_status(feature_dir)
            event = status["events"][event_ids[0]]
            self.assertEqual(event["source_stage"], "dev.plan")
            self.assertEqual(
                [item["path"] for item in event["artifacts"]],
                ["PLAN.md", "plan.json", artifact_sync.CATALOG_FILE_NAME],
            )
            for item in event["artifacts"]:
                self.assertTrue(item["upload_path"].startswith("P001/DEV/Features/alpha"))

            catalog = json.loads((feature_dir / artifact_sync.CATALOG_FILE_NAME).read_text(encoding="utf-8"))
            catalog_entries = {item["path"]: item for item in catalog["artifacts"]}
            self.assertEqual(catalog_entries["PLAN.md"]["category"], "implementation_plan")
            self.assertEqual(catalog_entries["plan.json"]["category"], "implementation_plan")

    def test_prd_done_uploads_prd_and_original_materials_from_biz_prd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            original_dir = feature_dir / "prd_original"
            original_dir.mkdir(parents=True)
            (feature_dir / "PRD.md").write_text("# 需求正式稿\n", encoding="utf-8")
            (original_dir / "source.docx").write_bytes(b"source")
            state_path = workspace / ".autobizdevops" / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "autobizdevops.state.v3",
                        "features": {
                            "alpha": {
                                "feature": "alpha",
                                "owner": "tester",
                                "checkpoint": "prd_done",
                                "stage": "Biz / PRD",
                                "iteration": "1",
                                "updated_at": "2026-08-13 12:00:00",
                                "workflowProfile": "standard",
                                "workflowDecisions": {},
                                "workflowTemplate": "standard",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            resolved_dir, event_ids = artifact_sync.prepare_checkpoint_sync_events(
                workspace=workspace,
                feature="alpha",
                old_checkpoint="prd_in_progress",
                new_checkpoint="prd_done",
                project_code="P001",
            )

            self.assertEqual(resolved_dir, feature_dir)
            self.assertEqual(len(event_ids), 1)
            event = artifact_sync.read_status(feature_dir)["events"][event_ids[0]]
            self.assertEqual(event["source_stage"], "biz.prd")
            self.assertEqual(event["source_skill"], "autobiz-requirement-discuss")
            self.assertEqual(
                [item["path"] for item in event["artifacts"]],
                ["PRD.md", "prd_original/source.docx", artifact_sync.CATALOG_FILE_NAME],
            )

            catalog = json.loads((feature_dir / artifact_sync.CATALOG_FILE_NAME).read_text(encoding="utf-8"))
            by_path = {item["path"]: item for item in catalog["artifacts"]}
            self.assertEqual(by_path["PRD.md"]["stage"], "biz.prd")
            self.assertEqual(by_path["prd_original/source.docx"]["stage"], "biz.prd")

    def test_read_status_migrates_retryable_discuss_events_and_retires_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp)
            (feature_dir / "PRD.md").write_text("# 需求正式稿\n", encoding="utf-8")
            (feature_dir / "UI_CONTEXT.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "featureId": "alpha",
                        "uiRequired": False,
                        "decisionStatus": "locked",
                        "decisionSource": "default_false",
                        "confirmedAtCheckpoint": "prd_done",
                        "lockedAtCheckpoint": "specs_done",
                        "notApplicableReason": "纯后端能力",
                        "pages": [],
                        "interactions": [],
                        "visualSources": [],
                        "capabilities": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status = {
                "version": 1,
                "published_artifacts": {
                    "PRD_DISCUSS.md": {"stage": "biz.discuss"},
                    "prd_original/source.docx": {"stage": "biz.discuss"},
                },
                "events": {
                    "pending-old": {
                        "status": "pending",
                        "feature": "alpha",
                        "source_stage": "biz.discuss",
                        "source_skill": "autobiz-requirement-discuss",
                        "workflow_record": {
                            "workflowTemplate": "custom",
                            "workflowNodes": ["biz.discuss", "biz.prd", "dev.code", "ops.archive"],
                            "workflowSkippedNodes": ["biz.discuss"],
                        },
                    },
                    "success-old": {
                        "status": "success",
                        "source_stage": "biz.discuss",
                    },
                },
            }
            (feature_dir / artifact_sync.STATUS_FILE_NAME).write_text(
                json.dumps(status, ensure_ascii=False),
                encoding="utf-8",
            )

            migrated = artifact_sync.read_status(feature_dir)

            self.assertNotIn("PRD_DISCUSS.md", migrated["published_artifacts"])
            self.assertEqual(
                migrated["published_artifacts"]["prd_original/source.docx"]["stage"],
                "biz.prd",
            )
            pending = migrated["events"]["pending-old"]
            self.assertEqual(pending["source_stage"], "biz.prd")
            self.assertEqual(pending["source_skill"], "autobiz-requirement-discuss")
            self.assertEqual(
                pending["workflow_record"]["workflowNodes"],
                ["biz.prd", "dev.code", "ops.archive"],
            )
            self.assertEqual(pending["workflow_record"]["workflowSkippedNodes"], [])
            self.assertEqual(migrated["events"]["success-old"]["source_stage"], "biz.discuss")

            artifacts, missing = artifact_sync.refresh_event_snapshot(
                workspace=feature_dir,
                feature_dir=feature_dir,
                project_code="P001",
                event=pending,
            )
            self.assertEqual(missing, [])
            self.assertEqual(
                [item["path"] for item in artifacts],
                ["PRD.md", "UI_CONTEXT.json", artifact_sync.CATALOG_FILE_NAME],
            )


class ArtifactUploadPreflightTest(unittest.TestCase):
    def test_preflight_detects_content_changed_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feature_dir = Path(tmp)
            path = feature_dir / "VERIFY_REPORT.md"
            path.write_text("before", encoding="utf-8")
            artifact = artifact_sync.snapshot_file_artifact(
                feature_dir,
                path,
                project_code="P001",
                feature="alpha",
            )
            path.write_text("after", encoding="utf-8")

            errors = sync_artifacts.preflight_errors([artifact])
            self.assertTrue(
                any("同步快照不一致" in error for error in errors),
                errors,
            )


class ArtifactSyncExecuteHookTest(unittest.TestCase):
    def test_checkpoint_command_detection_handles_direct_and_shell_wrapped_commands(self) -> None:
        accepted = (
            "python hooks/update_checkpoint.py --checkpoint plan_done",
            "python3 hooks/update_checkpoint.py -c code_done",
            "/bin/zsh -lc 'python hooks/update_checkpoint.py --skip-node dev.code'",
        )
        for command in accepted:
            with self.subTest(command=command):
                self.assertTrue(artifact_sync_execute_hook.is_checkpoint_update_command(command))

        rejected = (
            "python hooks/update_checkpoint.py --checkpoint plan_done --dry-run",
            "python hooks/update_checkpoint.py --feature alpha",
            "python hooks/other.py --checkpoint plan_done",
        )
        for command in rejected:
            with self.subTest(command=command):
                self.assertFalse(artifact_sync_execute_hook.is_checkpoint_update_command(command))

    def test_successful_foreground_checkpoint_update_schedules_sync(self) -> None:
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "execute",
            "tool_input": {
                "command": "python hooks/update_checkpoint.py --checkpoint code_done",
            },
            "tool_response": {"exitCode": 0},
        }
        workspace = Path("/tmp/plugin-workspace/demo")
        with patch.object(
            artifact_sync_execute_hook,
            "get_plugin_output_workspace",
            return_value=workspace,
        ), patch.object(
            artifact_sync_execute_hook,
            "resolve_env_feature",
            return_value="alpha",
        ), patch.object(
            artifact_sync_execute_hook,
            "schedule_current_checkpoint_sync_best_effort",
        ) as schedule:
            artifact_sync_execute_hook.run_hook(payload)

        schedule.assert_called_once_with(workspace=workspace, feature="alpha")

    def test_failed_or_background_checkpoint_update_does_not_schedule_sync(self) -> None:
        base = {
            "hook_event_name": "PostToolUse",
            "tool_name": "execute",
            "tool_input": {
                "command": "python hooks/update_checkpoint.py --checkpoint code_done",
            },
            "tool_response": {"exitCode": 0},
        }
        payloads = [
            {**base, "tool_response": {"exitCode": 1}},
            {**base, "tool_input": {**base["tool_input"], "run_in_background": True}},
        ]
        with patch.object(
            artifact_sync_execute_hook,
            "schedule_current_checkpoint_sync_best_effort",
        ) as schedule:
            for payload in payloads:
                artifact_sync_execute_hook.run_hook(payload)

        schedule.assert_not_called()

    def test_sync_is_not_registered_automatically_and_other_hooks_remain(self) -> None:
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(config["PreToolUse"]), 4)
        registrations = [
            hook
            for registration in config["PostToolUse"]
            if registration.get("matcher") == "execute"
            for hook in registration.get("hooks", [])
        ]
        self.assertFalse(any("artifact_sync" in hook.get("command", "") for hook in registrations))
        self.assertTrue(any("verified_digest_guard.py" in hook.get("command", "") for hook in registrations))


class ArtifactSyncSkillTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "demo"
        self.feature_dir = self.workspace / ".autobizdevops/features/alpha"
        self.feature_dir.mkdir(parents=True)
        self.state_path = self.workspace / ".autobizdevops/state.json"
        self.set_checkpoint("code_done")
        record = artifact_sync.current_feature_record(self.workspace, "alpha")
        contracts = artifact_sync.load_contracts(self.workspace, record)
        for contract in contracts.skill_contracts.values():
            if contract.group in artifact_sync.UPLOAD_GROUPS:
                for output in contract.outputs:
                    self.write_artifact(_sample_path(output.path))
        self.write_artifact("FEATURE_API_DETAIL.md")
        self.write_artifact("prd_original/source.docx")
        self.write_artifact("sources/SRC-001.md")
        environment = patch.dict(os.environ, {
            "PLUGIN_WORKSPACE": str(self.root), "PROJECT_DIR": "demo",
            "PROJECT_CODE": "P001", "FEATURE_ID": "alpha",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def set_checkpoint(self, checkpoint: str, **workflow_fields) -> None:
        self.state_path.write_text(json.dumps({
            "schemaVersion": "autobizdevops.state.v3",
            "features": {"alpha": {"feature": "alpha", "checkpoint": checkpoint, **workflow_fields}},
        }), encoding="utf-8")

    def write_artifact(self, path: str, content: str = "artifact\n") -> None:
        target = self.feature_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_first_sync_uploads_completed_stages_and_unchanged_repeat_is_noop(self) -> None:
        state_before = self.state_path.read_bytes()
        with patch.object(sync_artifacts, "upload_file", return_value=(True, "")) as upload:
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
            uploaded = {call.args[0]["path"] for call in upload.call_args_list}
            self.assertTrue({"PRD.md", "plan.json", "FEATURE_API_DETAIL.md", "sources/SRC-001.md"}.issubset(uploaded))
            status = artifact_sync.read_status(self.feature_dir)
            self.assertTrue(all(event["status"] == "success" for event in status["events"].values()))
            for event in status["events"].values():
                self.assertEqual(event["artifacts"][-1]["path"], artifact_sync.CATALOG_FILE_NAME)
            upload.reset_mock()
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
            upload.assert_not_called()
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_changed_api_document_is_republished(self) -> None:
        with patch.object(sync_artifacts, "upload_file", return_value=(True, "")) as upload:
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
            self.write_artifact("FEATURE_API_DETAIL.md", "updated interface fields\n")
            upload.reset_mock()
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
        uploaded = {call.args[0]["path"] for call in upload.call_args_list}
        self.assertIn("FEATURE_API_DETAIL.md", uploaded)
        self.assertNotIn("PRD.md", uploaded)

    def test_in_progress_stage_and_future_stages_are_not_first_published(self) -> None:
        self.set_checkpoint("plan_in_progress")
        with patch.object(sync_artifacts, "upload_file", return_value=(True, "")) as upload:
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
        uploaded = {call.args[0]["path"] for call in upload.call_args_list}
        self.assertIn("design.md", uploaded)
        self.assertTrue({"PLAN.md", "plan.json", "FEATURE_API_DETAIL.md"}.isdisjoint(uploaded))

    def test_failed_upload_can_be_retried_without_changing_checkpoint(self) -> None:
        self.set_checkpoint("prd_done")
        state_before = self.state_path.read_bytes()
        with patch.object(sync_artifacts, "upload_file", return_value=(False, "test network failure")):
            self.assertEqual(sync_artifacts.main(["--sync"]), 1)
        status = artifact_sync.read_status(self.feature_dir)
        self.assertEqual(len(status["events"]), 1)
        event_id, event = next(iter(status["events"].items()))
        self.assertEqual(event["status"], "failed")
        with patch.object(sync_artifacts, "upload_file", return_value=(True, "")):
            self.assertEqual(sync_artifacts.main(["--retry-failed"]), 0)
        retried = artifact_sync.read_status(self.feature_dir)["events"][event_id]
        self.assertEqual(retried["status"], "success")
        self.assertEqual(retried["attempts"], 2)
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_prepare_only_cli_works_outside_plugin_root_at_new_and_legacy_paths(self) -> None:
        self.set_checkpoint("prd_done")
        for script in (
            ROOT / "skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py",
            ROOT / "hooks/sync_artifacts.py",
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, "-X", "utf8", str(script), "--sync", "--prepare-only"],
                    cwd=self.root, env=dict(os.environ), capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                prepared = json.loads(result.stdout)
                self.assertFalse(prepared["uploaded"])
                self.assertTrue(prepared["event_ids"])
        status = artifact_sync.read_status(self.feature_dir)
        self.assertEqual(status["published_artifacts"], {})
        self.assertTrue(all(event["status"] == "pending" and event["attempts"] == 0 for event in status["events"].values()))

    def test_reconcile_preserves_legacy_tracked_only_scope(self) -> None:
        with patch.object(sync_artifacts, "upload_file") as upload:
            self.assertEqual(sync_artifacts.main(["--reconcile"]), 0)
        upload.assert_not_called()
        self.assertFalse((self.feature_dir / artifact_sync.STATUS_FILE_NAME).exists())

    def test_skipped_stage_is_not_first_published(self) -> None:
        self.set_checkpoint("code_done", workflowSkippedNodes=["dev.design"])
        with patch.object(sync_artifacts, "upload_file", return_value=(True, "")) as upload:
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
        uploaded = {call.args[0]["path"] for call in upload.call_args_list}
        self.assertIn("FEATURE_API_DETAIL.md", uploaded)
        self.assertNotIn("design.md", uploaded)

    def test_archived_sync_uses_the_state_selected_iteration(self) -> None:
        self.set_checkpoint("archived", iteration="2")
        archive = self.workspace / ".autobizdevops/archive"
        archive.mkdir()
        selected = archive / "alpha-iter2"
        self.feature_dir.rename(selected)
        (archive / "alpha-iter1").mkdir()
        with patch.object(sync_artifacts, "upload_file", return_value=(True, "")) as upload:
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
        self.assertTrue(upload.called)
        self.assertTrue(all(Path(call.args[0]["local_path"]).is_relative_to(selected.resolve()) for call in upload.call_args_list))
        self.assertTrue((selected / artifact_sync.STATUS_FILE_NAME).is_file())
        self.assertFalse(self.feature_dir.exists())

    def test_missing_api_document_is_reported_without_blocking_other_artifacts(self) -> None:
        (self.feature_dir / "FEATURE_API_DETAIL.md").unlink()
        catalogs = []

        def capture_upload(artifact):
            if artifact["path"] == artifact_sync.CATALOG_FILE_NAME:
                catalogs.append(json.loads(Path(artifact["local_path"]).read_text(encoding="utf-8")))
            return True, ""

        with patch.object(sync_artifacts, "upload_file", side_effect=capture_upload) as upload:
            self.assertEqual(sync_artifacts.main(["--sync"]), 0)
        code_catalogs = []
        for event in artifact_sync.read_status(self.feature_dir)["events"].values():
            if event["source_stage"] == "dev.code":
                code_catalogs.append(event)
        self.assertEqual(len(code_catalogs), 1)
        self.assertEqual(code_catalogs[0]["status"], "success")
        uploaded = {call.args[0]["path"] for call in upload.call_args_list}
        self.assertIn("evidence/EVIDENCE.jsonl", uploaded)
        self.assertNotIn("FEATURE_API_DETAIL.md", uploaded)
        self.assertTrue(any(
            entry["path"] == "FEATURE_API_DETAIL.md" and entry["upload_status"] == "missing"
            for catalog in catalogs for entry in catalog["artifacts"]
        ))


if __name__ == "__main__":
    unittest.main()
