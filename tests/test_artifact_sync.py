"""Artifact synchronization and catalog contract tests."""

from __future__ import annotations

import json
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
    "source-context.json": ("requirement_source", "final"),
    "proposal.md": ("behavior_proposal", "final"),
    "specs/**/*.md": ("behavior_spec", "final"),
    "SPECS_REVIEW.md": ("review_report", "evidence"),
    "design.md": ("technical_design", "process"),
    "PLAN.md": ("implementation_plan", "process"),
    "plan.json": ("implementation_plan", "final"),
    "DETAIL_DESIGN.md": ("technical_detail", "process"),
    "evidence/EVIDENCE.jsonl": ("evidence_stream", "evidence"),
    "REQUIREMENTS_EVAL.md": ("review_report", "evidence"),
    "UNIT_TEST_REPORT.md": ("unit_test_report", "evidence"),
    "UNIT_TEST_RESULT.json": ("unit_test_result", "evidence"),
    "test-output.log": ("log", "log"),
    "E2E_TEST_CASES.yaml": ("e2e_cases", "evidence"),
    "E2E_REPORT.md": ("e2e_report", "evidence"),
    "E2E_RESULT.json": ("e2e_result", "evidence"),
    "E2E_QUALITY_SCAN.json": ("e2e_quality_scan", "evidence"),
    "e2e-diagnostics/**/*": ("e2e_diagnostic", "evidence"),
    "FIX_REQUEST.json": ("fix_request", "process"),
    "e2e-run.log": ("log", "log"),
    "VERIFY_REPORT.md": ("verify_report", "final"),
    "VERIFY_DECISION.json": ("verify_decision", "final"),
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
            stage="dev.verify",
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

        source_snapshot = artifact_sync.catalog_entry(
            path="sources/SRC-001/payment.docx",
            stage="biz.prd",
            upload_status="uploaded",
            size=12,
            sha256="def",
        )
        self.assertEqual(source_snapshot["source"], "extra")
        self.assertEqual(source_snapshot["category"], "requirement_source_snapshot")
        self.assertEqual(source_snapshot["lifecycle"], "reference")

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
            stage="dev.e2e",
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
                ["PLAN.md", "design.md", "plan.json", artifact_sync.CATALOG_FILE_NAME],
            )
            for item in event["artifacts"]:
                self.assertTrue(item["upload_path"].startswith("P001/DEV/Features/alpha"))

            catalog = json.loads((feature_dir / artifact_sync.CATALOG_FILE_NAME).read_text(encoding="utf-8"))
            catalog_entries = {item["path"]: item for item in catalog["artifacts"]}
            self.assertEqual(catalog_entries["design.md"]["category"], "technical_design")
            self.assertEqual(catalog_entries["PLAN.md"]["category"], "implementation_plan")
            self.assertEqual(catalog_entries["plan.json"]["category"], "implementation_plan")

    def test_prd_done_uploads_prd_and_original_materials_from_biz_prd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
            original_dir = feature_dir / "prd_original"
            original_dir.mkdir(parents=True)
            source_dir = feature_dir / "sources" / "SRC-001"
            source_dir.mkdir(parents=True)
            (feature_dir / "PRD.md").write_text("# 需求正式稿\n", encoding="utf-8")
            (feature_dir / "source-context.json").write_text('{"version": 1}\n', encoding="utf-8")
            (original_dir / "source.docx").write_bytes(b"source")
            (source_dir / "payment.docx").write_bytes(b"payment")
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
                [
                    "PRD.md",
                    "prd_original/source.docx",
                    "source-context.json",
                    "sources/SRC-001/payment.docx",
                    artifact_sync.CATALOG_FILE_NAME,
                ],
            )

            catalog = json.loads((feature_dir / artifact_sync.CATALOG_FILE_NAME).read_text(encoding="utf-8"))
            by_path = {item["path"]: item for item in catalog["artifacts"]}
            self.assertEqual(by_path["PRD.md"]["stage"], "biz.prd")
            self.assertEqual(by_path["source-context.json"]["stage"], "biz.prd")
            self.assertEqual(by_path["prd_original/source.docx"]["stage"], "biz.prd")
            self.assertEqual(by_path["sources/SRC-001/payment.docx"]["stage"], "biz.prd")

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
            "python3 hooks/update_checkpoint.py -c verify_done",
            "/bin/zsh -lc 'python hooks/update_checkpoint.py --skip-node dev.e2e'",
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
                "command": "python hooks/update_checkpoint.py --checkpoint verify_done",
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
                "command": "python hooks/update_checkpoint.py --checkpoint verify_done",
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

    def test_project_hook_config_registers_post_execute_without_dropping_pre_hooks(self) -> None:
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(config["PreToolUse"]), 4)
        registrations = [
            hook
            for registration in config["PostToolUse"]
            if registration.get("matcher") == "execute"
            for hook in registration.get("hooks", [])
        ]
        self.assertTrue(
            any(hook.get("command") == "python hooks/artifact_sync_execute_hook.py" for hook in registrations)
        )


if __name__ == "__main__":
    unittest.main()
