"""Configuration-driven artifact sync, publication and retry behavior."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks import artifact_sync as sync, sync_artifacts as cli

CATALOG = sync.CATALOG_FILE_NAME
DEFAULT_CONFIG = json.loads(sync.CONFIG_PATH.read_text(encoding="utf-8"))
SAMPLES = {
    "specs/**/*.md": "specs/example/spec.md",
    "prd_original/**/*": "prd_original/source.docx",
    "sources/**/*": "sources/SRC-001.md",
}


class ArtifactSyncTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "demo"
        self.feature_dir = self.workspace / ".autobizdevops/features/alpha"
        self.feature_dir.mkdir(parents=True)
        self.state_path = self.workspace / ".autobizdevops/state.json"
        self.set_checkpoint("code_done")
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config_path = self.root / "artifact-sync.json"
        self.save_config()
        config_patch = patch.object(sync, "CONFIG_PATH", self.config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        environment = patch.dict(os.environ, {
            "PLUGIN_WORKSPACE": str(self.root), "PROJECT_DIR": "demo",
            "PROJECT_CODE": "P001", "FEATURE_ID": "alpha",
        })
        environment.start()
        self.addCleanup(environment.stop)
        for paths in sync.configured_nodes(self.config).values():
            for pattern in paths:
                self.write_artifact(SAMPLES.get(pattern, pattern))
        self.uploaded = []
        self.remote_catalogs = []
        self.failures = set()
        upload_patch = patch.object(cli, "upload_file", side_effect=self.upload)
        upload_patch.start()
        self.addCleanup(upload_patch.stop)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def set_checkpoint(self, checkpoint, **fields):
        self.state_path.write_text(json.dumps({"schemaVersion": "autobizdevops.state.v3", "features": {
            "alpha": {"feature": "alpha", "checkpoint": checkpoint, **fields}, "broken-other": 42,
        }}), encoding="utf-8")

    def write_artifact(self, name, content="artifact\n"):
        path = self.feature_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def oversized(self, name):
        with (self.feature_dir / name).open("wb") as handle:
            handle.truncate(sync.MAX_FILE_SIZE + 1)

    def upload(self, artifact):
        path = artifact["path"]
        self.uploaded.append(path)
        if path in self.failures:
            return False, f"test upload failure: {path}"
        if path == CATALOG:
            self.remote_catalogs.append(json.loads(Path(artifact["local_path"]).read_text(encoding="utf-8")))
        return True, ""

    def run_cli(self, *args, expected=0):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            code = cli.main(list(args))
        self.assertEqual(code, expected, self.stderr.getvalue())

    def status(self):
        return sync.read_status(self.feature_dir)

    def catalog(self):
        return json.loads((self.feature_dir / CATALOG).read_text(encoding="utf-8"))

    def entries(self):
        return {row["path"]: row for row in self.catalog()["artifacts"]}

    def reset_uploads(self):
        self.uploaded.clear()
        self.remote_catalogs.clear()

    def files(self):
        return {str(path): path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}

    def test_first_sync_and_repeat_are_file_incremental(self):
        before = self.state_path.read_bytes()
        self.run_cli("--sync")
        expected = {SAMPLES.get(p, p) for paths in sync.configured_nodes(self.config).values() for p in paths}
        self.assertEqual(set(self.uploaded), expected | {CATALOG})
        self.assertEqual(len(self.uploaded), len(expected) + 1)
        self.assertEqual(self.uploaded[-1], CATALOG)
        self.assertEqual(set(self.entries()), expected)
        self.assertEqual(self.entries()["UI_CONTEXT.json"]["stage"], "dev.specs")
        self.assertTrue(all(row["upload_status"] == "uploaded" for row in self.entries().values()))
        self.assertTrue(all(event["status"] == "success" for event in self.status()["events"].values()))
        catalog_before = (self.feature_dir / CATALOG).read_bytes()
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])
        self.assertEqual((self.feature_dir / CATALOG).read_bytes(), catalog_before)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_one_file_change_does_not_reupload_its_siblings(self):
        self.run_cli("--sync")
        self.write_artifact("proposal.md", "changed!\n")  # Same size, different hash.
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, ["proposal.md", CATALOG])

    def test_only_description_change_uploads_only_catalog(self):
        self.run_cli("--sync")
        self.config["stages"]["dev"]["dev.specs"]["proposal.md"] = "新的规格总览说明。"
        self.save_config()
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [CATALOG])
        self.assertEqual(self.remote_catalogs[0]["artifacts"], self.catalog()["artifacts"])
        self.assertEqual(self.entries()["proposal.md"]["description"], "新的规格总览说明。")
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])

    def test_allowed_checkpoint_change_does_not_upload(self):
        self.run_cli("--sync")
        self.config["allowedCheckpoints"].append("custom_done")
        self.save_config()
        self.set_checkpoint("custom_done")
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])

    def test_node_ownership_change_only_updates_catalog(self):
        self.run_cli("--sync")
        self.config["stages"]["dev"]["dev.custom"] = self.config["stages"]["dev"].pop("dev.specs")
        self.save_config()
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [CATALOG])
        self.assertEqual(self.entries()["proposal.md"]["stage"], "dev.custom")

    def test_config_alone_selects_custom_files_and_descriptions(self):
        self.config["stages"] = {"biz": {}, "dev": {"dev.custom": {"custom/*.txt": "自定义产物。"}}}
        self.save_config()
        self.write_artifact("custom/new.txt")
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, ["custom/new.txt", CATALOG])
        self.assertEqual(set(self.entries()), {"custom/new.txt"})
        self.assertEqual(self.entries()["custom/new.txt"]["description"], "自定义产物。")
        self.assertEqual(self.entries()["custom/new.txt"]["category"], "artifact")

    def test_glob_description_and_metadata_are_preserved(self):
        self.write_artifact("specs/root.md")
        self.run_cli("--sync")
        entries = self.entries()
        for path in ("specs/root.md", "specs/example/spec.md"):
            self.assertEqual(entries[path]["description"], self.config["stages"]["dev"]["dev.specs"]["specs/**/*.md"])
            self.assertEqual((entries[path]["category"], entries[path]["lifecycle"]), ("behavior_spec", "final"))
        self.assertEqual(entries["proposal.md"]["description"], "行为规格总览。")
        self.assertEqual(entries["prd_original/source.docx"]["category"], "source_reference")
        self.assertEqual(entries["sources/SRC-001.md"]["category"], "requirement_source_snapshot")
        self.assertEqual(entries["FEATURE_API_DETAIL.md"]["source"], "extra")
        self.assertEqual(entries[".design-contract.lock.json"]["category"], "technical_design_contract")

    def test_missing_and_oversized_files_do_not_enter_catalog(self):
        (self.feature_dir / "FEATURE_API_DETAIL.md").unlink()
        (self.feature_dir / "specs/example/spec.md").unlink()
        self.oversized("PRD.md")
        self.oversized("prd_original/source.docx")
        self.run_cli("--sync")
        for path in ("FEATURE_API_DETAIL.md", "specs/example/spec.md", "PRD.md", "prd_original/source.docx"):
            self.assertNotIn(path, self.uploaded)
            self.assertNotIn(path, self.entries())
        self.assertIn("file_size_exceeds_5mb", self.stderr.getvalue())
        self.assertIn("file_not_found", self.stderr.getvalue())
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])

    def test_removed_missing_and_oversized_published_files_leave_catalog_but_keep_history(self):
        self.run_cli("--sync")
        prior = self.status()["published_artifacts"]
        del self.config["stages"]["dev"]["dev.specs"]["proposal.md"]
        self.save_config()
        (self.feature_dir / "FEATURE_API_DETAIL.md").unlink()
        self.oversized("PRD.md")
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [CATALOG])
        for path in ("proposal.md", "FEATURE_API_DETAIL.md", "PRD.md"):
            self.assertNotIn(path, self.entries())
            self.assertEqual(self.status()["published_artifacts"][path], prior[path])

    def test_only_missing_inputs_produce_empty_catalog_once(self):
        self.config["stages"] = {"biz": {}, "dev": {"dev.none": {"absent.md": "未生成文件。"}}}
        self.save_config()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [CATALOG])
        self.assertEqual(self.entries(), {})
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])

    def test_temporary_glob_files_excluded_and_explicit_hidden_file_included(self):
        ignored = ["sources/.hidden.md", "sources/.cache/a.md", "sources/~$draft.docx", "sources/a.tmp", "sources/Thumbs.db"]
        for path in ignored:
            self.write_artifact(path)
        self.write_artifact("sources/nested/actual.pdf")
        self.run_cli("--sync")
        self.assertTrue(set(ignored).isdisjoint(self.uploaded))
        self.assertIn("sources/nested/actual.pdf", self.uploaded)
        self.assertIn(".design-contract.lock.json", self.uploaded)

    def test_failed_new_file_is_absent_and_successful_sibling_is_durable(self):
        self.failures.add("plan.json")
        self.run_cli("--sync", expected=1)
        self.assertNotIn("plan.json", self.entries())
        self.assertIn("PLAN.md", self.status()["published_artifacts"])
        failed = [e for e in self.status()["events"].values() if e["status"] == "failed"]
        self.assertEqual([e["source_stage"] for e in failed], ["dev.plan"])
        self.failures.clear()
        self.reset_uploads()
        self.run_cli("--retry-failed")
        self.assertEqual(self.uploaded, ["plan.json", CATALOG])
        self.assertIn("plan.json", self.entries())
        self.assertEqual(self.status()["events"][failed[0]["event_id"]]["attempts"], 2)

    def test_failed_update_keeps_previously_uploaded_version_in_catalog(self):
        self.run_cli("--sync")
        previous = self.entries()["proposal.md"].copy()
        self.write_artifact("proposal.md", "updated version\n")
        self.config["stages"]["dev"]["dev.specs"]["proposal.md"] = "更新后的简述。"
        self.save_config()
        self.failures.add("proposal.md")
        self.reset_uploads()
        self.run_cli("--sync", expected=1)
        self.assertEqual(self.uploaded, ["proposal.md", CATALOG])
        entry = self.entries()["proposal.md"]
        self.assertEqual(entry["sha256"], previous["sha256"])
        self.assertEqual(entry["size"], previous["size"])
        self.assertEqual(entry["description"], "更新后的简述。")

    def test_catalog_failure_retries_only_catalog(self):
        self.failures.add(CATALOG)
        self.run_cli("--sync", expected=1)
        status = self.status()
        self.assertNotIn("catalog", status)
        self.assertIn("proposal.md", status["published_artifacts"])
        failed_ids = sync.pending_event_ids(self.feature_dir)
        self.assertEqual(len(failed_ids), 1)
        self.assertEqual(status["events"][failed_ids[0]]["kind"], "catalog")
        self.failures.clear()
        self.reset_uploads()
        self.run_cli("--retry-failed")
        self.assertEqual(self.uploaded, [CATALOG])
        self.assertEqual(self.status()["events"][failed_ids[0]]["attempts"], 2)

    def test_publication_is_saved_before_next_upload(self):
        original = self.upload
        seen = []
        def observe(artifact):
            for prior in seen:
                self.assertIn(prior, self.status()["published_artifacts"])
            result = original(artifact)
            seen.append(artifact["path"])
            return result
        with patch.object(cli, "upload_file", side_effect=observe):
            self.run_cli("--sync")
        self.assertGreater(len(seen), 2)

    def test_retry_uses_current_config_not_saved_file_list_or_workflow(self):
        self.run_cli("--sync", "--prepare-only")
        del self.config["stages"]["dev"]["dev.specs"]["proposal.md"]
        self.config["stages"]["dev"]["dev.specs"]["new.md"] = "新文件。"
        self.save_config()
        self.write_artifact("new.md")
        self.run_cli("--retry-failed")
        self.assertNotIn("proposal.md", self.uploaded)
        self.assertIn("new.md", self.uploaded)
        self.assertNotIn("proposal.md", self.entries())

    def test_removed_node_retry_is_skipped(self):
        self.run_cli("--sync", "--prepare-only")
        event_id = next(k for k, e in self.status()["events"].items() if e["source_stage"] == "dev.plan")
        del self.config["stages"]["dev"]["dev.plan"]
        self.save_config()
        self.run_cli("--retry-failed")
        self.assertNotIn("plan.json", self.uploaded)
        self.assertNotIn("PLAN.md", self.entries())
        self.assertEqual(self.status()["events"][event_id]["status"], "skipped")

    def test_prepare_only_does_not_claim_new_files_are_uploaded(self):
        self.run_cli("--sync", "--prepare-only")
        self.assertEqual(self.uploaded, [])
        self.assertEqual(self.entries(), {})
        status = self.status()
        self.assertEqual(status["published_artifacts"], {})
        self.assertTrue(all(e["status"] == "pending" and e["attempts"] == 0 for e in status["events"].values()))
        self.assertTrue(any(e["artifacts"] for e in status["events"].values()))
        self.write_artifact("proposal.md", "changed after prepare")
        self.run_cli("--retry-failed")
        self.assertEqual(self.entries()["proposal.md"]["sha256"], sync.sha256_file(self.feature_dir / "proposal.md"))

    def test_prepare_only_after_file_change_keeps_published_hash(self):
        self.run_cli("--sync")
        old_hash = self.entries()["proposal.md"]["sha256"]
        self.write_artifact("proposal.md", "changed")
        self.reset_uploads()
        self.run_cli("--sync", "--prepare-only")
        self.assertEqual(self.uploaded, [])
        self.assertEqual(self.entries()["proposal.md"]["sha256"], old_hash)

    def test_single_event_does_not_first_publish_other_nodes(self):
        self.run_cli("--sync", "--prepare-only")
        event_id = next(k for k, e in self.status()["events"].items() if e["source_stage"] == "dev.plan")
        self.run_cli("--event-id", event_id)
        self.assertEqual(self.uploaded, ["PLAN.md", "plan.json", CATALOG])
        self.assertEqual(set(self.entries()), {"PLAN.md", "plan.json"})

    def test_new_events_have_configuration_snapshot_and_no_workflow_snapshot(self):
        self.run_cli("--sync", "--prepare-only")
        events = self.status()["events"]
        before = {key: e["fingerprint"] for key, e in events.items()}
        for event in events.values():
            self.assertEqual(event["config_snapshot"], self.config)
            self.assertEqual(event["config_digest"], sync.config_digest(self.config))
            self.assertNotIn("workflow_record", event)
        self.config["stages"]["dev"]["dev.specs"]["proposal.md"] = "修改简述。"
        self.save_config()
        self.run_cli("--sync", "--prepare-only")
        self.assertEqual(set(self.status()["events"]), set(events))
        self.assertTrue(all(e["fingerprint"] != before[k] for k, e in self.status()["events"].items()))

    def test_read_only_check_does_not_migrate_or_write_sync_state(self):
        legacy = self.feature_dir / "artifact-sync"
        legacy.mkdir()
        (legacy / sync.STATUS_FILE_NAME).write_text(json.dumps(sync.default_status()), encoding="utf-8")
        before = self.files()
        self.run_cli("--check")
        self.assertEqual(self.files(), before)
        self.assertEqual(self.uploaded, [])
        result = json.loads(self.stdout.getvalue())
        self.assertEqual(result["checkpoint"], "code_done")
        self.assertEqual(Path(result["feature_dir"]), self.feature_dir.resolve())

    def test_all_entrypoints_reject_disallowed_checkpoint_without_writing(self):
        actions = [("--check",), ("--sync",), ("--sync", "--prepare-only"), ("--reconcile",),
                   ("--retry-failed",), ("--drain-outbox",), ("--event-id", "old")]
        for checkpoint in ("prd_done", "plan_done", "code_in_progress", "needs_fix"):
            self.set_checkpoint(checkpoint)
            before = self.files()
            for args in actions:
                with self.subTest(checkpoint=checkpoint, args=args):
                    self.run_cli(*args, expected=1)
                    self.assertEqual(self.files(), before)
        self.assertEqual(self.uploaded, [])

    def test_prepared_events_cannot_bypass_gate_after_rollback(self):
        self.run_cli("--sync", "--prepare-only")
        event_id = next(iter(self.status()["events"]))
        self.set_checkpoint("code_in_progress")
        before = self.files()
        for args in [("--retry-failed",), ("--event-id", event_id), ("--reconcile",)]:
            self.run_cli(*args, expected=1)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.uploaded, [])

    def test_direct_preparation_also_checks_admission(self):
        self.set_checkpoint("code_in_progress")
        with self.assertRaisesRegex(ValueError, "code_done"):
            sync.prepare_reconcile_events(workspace=self.workspace, feature="alpha", project_code="P001")
        self.assertFalse((self.feature_dir / sync.STATUS_FILE_NAME).exists())

    def test_workflow_and_checkpoint_changes_do_not_change_scope(self):
        self.set_checkpoint("code_done", workflowSkippedNodes=["dev.code", "biz.prd", "dev.design"],
                            workflowTemplate="invalid", workflowNodes=["ops.cicd"])
        self.run_cli("--sync")
        self.assertIn("PRD.md", self.uploaded)
        self.assertIn("design.md", self.uploaded)
        self.assertIn("FEATURE_API_DETAIL.md", self.uploaded)
        for cp in ("cicd_in_progress", "cicd_done"):
            self.reset_uploads()
            self.set_checkpoint(cp)
            self.run_cli("--sync")
            self.assertEqual(self.uploaded, [])

    def test_legacy_state_formats_are_supported(self):
        for record in ("code_done", {"checkpoint": "code_done"}):
            self.state_path.write_text(json.dumps({"alpha": record, "bad": []}), encoding="utf-8")
            self.run_cli("--check")
        self.assertEqual(self.uploaded, [])

    def test_invalid_current_state_is_rejected_without_writes(self):
        for payload in ({"features": []}, {"features": {"alpha": []}}, {"alpha": {"feature": "other", "checkpoint": "code_done"}}, []):
            self.state_path.write_text(json.dumps(payload), encoding="utf-8")
            before = self.files()
            self.run_cli("--check", expected=1)
            self.assertEqual(self.files(), before)

    def test_archive_uses_selected_iteration(self):
        self.set_checkpoint("archived", iteration="2")
        archive = self.workspace / ".autobizdevops/archive"
        archive.mkdir()
        (archive / "alpha-iter1").mkdir()
        self.feature_dir.rename(archive / "alpha-iter2")
        self.feature_dir = archive / "alpha-iter2"
        self.run_cli("--sync")
        self.assertIn("proposal.md", self.entries())
        self.assertFalse((archive / "alpha-iter1" / CATALOG).exists())

    def test_missing_selected_archive_does_not_use_wrong_iteration(self):
        self.set_checkpoint("archived", iteration="2")
        archive = self.workspace / ".autobizdevops/archive"
        archive.mkdir()
        self.feature_dir.rename(archive / "alpha-iter1")
        self.run_cli("--check", expected=1)

    def test_legacy_reconcile_and_drain_aliases(self):
        self.run_cli("--reconcile", "--prepare-only")
        self.run_cli("--drain-outbox")
        self.assertIn("PRD.md", self.uploaded)
        self.reset_uploads()
        self.run_cli("--reconcile")
        self.assertEqual(self.uploaded, [])

    def test_legacy_published_records_do_not_force_file_reupload(self):
        self.run_cli("--sync")
        status = self.status()
        status.pop("catalog")
        for event in status["events"].values():
            event.pop("config_digest", None)
            event.pop("config_snapshot", None)
        sync.write_status(self.feature_dir, status)
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [CATALOG])

    def test_upload_target_change_republishes_files(self):
        self.run_cli("--sync")
        before = set(self.uploaded)
        self.reset_uploads()
        with patch.dict(os.environ, {"PROJECT_CODE": "P002"}):
            self.run_cli("--sync")
        self.assertEqual(set(self.uploaded), before)
        self.assertTrue(all(p["object_key"].startswith("P002/") for p in self.status()["published_artifacts"].values()))

    def test_config_errors_and_escaping_paths_do_not_write(self):
        invalid_configs = [[], {}, {"allowedCheckpoints": [], "stages": {"biz": {}, "dev": {}}}]
        for pattern in ("../outside.md", "/tmp/outside.md", "x/../../out.md", "C:\\out.md", CATALOG):
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["stages"]["dev"]["dev.code"][pattern] = "非法路径。"
            invalid_configs.append(cfg)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["stages"]["dev"]["dev.code"]["FEATURE_API_DETAIL.md"] = " "
        invalid_configs.append(cfg)
        before = self.files()
        for cfg in invalid_configs:
            self.config_path.write_text(json.dumps(cfg), encoding="utf-8")
            self.run_cli("--sync", expected=1)
            self.assertEqual(self.files(), before)
        self.config_path.write_text("{broken", encoding="utf-8")
        self.run_cli("--check", expected=1)
        self.config_path.unlink()
        self.run_cli("--check", expected=1)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.uploaded, [])

    def test_escaping_symlink_files_and_glob_prefixes_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("outside", encoding="utf-8")
        for name, target, pattern in (("link.md", outside / "secret.md", "link.md"),
                                      ("linked", outside, "linked/**/*.md")):
            (self.feature_dir / name).symlink_to(target)
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            self.config["stages"]["dev"]["dev.code"][pattern] = "文件。"
            self.save_config()
            before = self.files()
            self.run_cli("--sync", expected=1)
            self.assertEqual(self.files(), before)
            (self.feature_dir / name).unlink()
        self.assertEqual(self.uploaded, [])

    def test_preflight_detects_changed_content(self):
        path = self.feature_dir / "proposal.md"
        artifact = sync.snapshot_file_artifact(self.feature_dir, path, project_code="P001", feature="alpha")
        self.write_artifact("proposal.md", "new bytes")
        self.assertTrue(any("同步快照不一致" in e for e in cli.preflight_errors([artifact])))

    def test_legacy_directory_status_migration(self):
        legacy = self.feature_dir / "artifact-sync"
        legacy.mkdir()
        status = sync.default_status()
        status["events"]["old"] = {
            "event_id": "old", "status": "pending", "feature": "alpha", "source_stage": "biz.discuss",
            "workflow_record": {"workflowNodes": ["biz.discuss", "biz.prd"], "workflowSkippedNodes": ["biz.discuss"]},
            "artifacts": [{"path": "outside-config.md"}],
        }
        (legacy / sync.STATUS_FILE_NAME).write_text(json.dumps(status), encoding="utf-8")
        (legacy / "outbox.ndjson").write_text("{}\n", encoding="utf-8")
        self.write_artifact("outside-config.md")
        self.run_cli("--retry-failed")
        self.assertEqual(self.uploaded, ["PRD.md", "prd_original/source.docx", "sources/SRC-001.md", CATALOG])
        self.assertEqual(self.status()["events"]["old"]["source_stage"], "biz.prd")
        self.assertFalse(legacy.exists())

    def test_new_and_legacy_entrypoints_work_without_board_package_from_other_cwd(self):
        plugin = self.root / "isolated-plugin"
        files = ["hooks/artifact_sync.py", "hooks/sync_artifacts.py", "hooks/paths.py",
                 "skills/autobizdevops-artifact-sync/scripts/artifact_sync.py",
                 "skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py",
                 "skills/autobizdevops-artifact-sync/config/artifact-sync.json"]
        for name in files:
            target = plugin / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        for entry in ("hooks/sync_artifacts.py", "skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py"):
            for args in (["--check"], ["--sync", "--prepare-only"]):
                result = subprocess.run([sys.executable, "-B", str(plugin / entry), *args], cwd=self.root,
                                        env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertEqual(self.uploaded, [])

    def test_hook_is_not_registered_and_other_hooks_remain(self):
        config = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
        commands = [h["command"] for registration in config["PostToolUse"]
                    if registration.get("matcher") == "execute" for h in registration.get("hooks", [])]
        self.assertFalse(any("artifact_sync" in c for c in commands))
        self.assertTrue(any("verified_digest_guard.py" in c for c in commands))

    def test_config_can_include_previously_retired_path_without_losing_history(self):
        self.config["stages"] = {"biz": {"biz.discuss": {"PRD_DISCUSS.md": "配置选择的文档。"}}, "dev": {}}
        self.save_config()
        self.write_artifact("PRD_DISCUSS.md")
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, ["PRD_DISCUSS.md", CATALOG])
        self.assertEqual(self.entries()["PRD_DISCUSS.md"]["stage"], "biz.discuss")
        self.assertIn("PRD_DISCUSS.md", self.status()["published_artifacts"])
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])

    def test_exactly_five_mib_is_uploadable(self):
        self.config["stages"] = {"biz": {}, "dev": {"dev.large": {"large.bin": "大小边界文件。"}}}
        self.save_config()
        with (self.feature_dir / "large.bin").open("wb") as handle:
            handle.truncate(sync.MAX_FILE_SIZE)
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, ["large.bin", CATALOG])
        self.assertEqual(self.entries()["large.bin"]["size"], sync.MAX_FILE_SIZE)

    def test_symlink_matched_by_glob_cannot_escape_feature(self):
        outside = self.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        (self.feature_dir / "sources/escape.md").symlink_to(outside)
        before = self.files()
        self.run_cli("--check", expected=1)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.uploaded, [])

    def test_runtime_outputs_are_not_rescanned_by_broad_glob(self):
        self.config["stages"] = {"biz": {}, "dev": {"dev.all": {"**/*": "配置范围内文件。"}}}
        self.save_config()
        self.run_cli("--sync")
        self.assertNotIn(sync.STATUS_FILE_NAME, self.entries())
        self.assertNotIn(CATALOG, self.entries())
        self.reset_uploads()
        self.run_cli("--sync")
        self.assertEqual(self.uploaded, [])

    def test_catalog_failure_after_description_edit_retries_only_catalog(self):
        self.run_cli("--sync")
        self.config["stages"]["dev"]["dev.specs"]["proposal.md"] = "重试后发布的新简述。"
        self.save_config()
        self.failures.add(CATALOG)
        self.reset_uploads()
        self.run_cli("--sync", expected=1)
        self.assertEqual(self.uploaded, [CATALOG])
        self.failures.clear()
        self.reset_uploads()
        self.run_cli("--retry-failed")
        self.assertEqual(self.uploaded, [CATALOG])
        self.assertEqual(self.entries()["proposal.md"]["description"], "重试后发布的新简述。")


if __name__ == "__main__":
    unittest.main()
