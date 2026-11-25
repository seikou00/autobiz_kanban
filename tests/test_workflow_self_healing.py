#!/usr/bin/env python3
"""End-to-end regression tests for Phase 1 & Phase 2 Workflow Self-Healing (v4)."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hooks import parallel_batch_stage as stage_mod
from hooks import parallel_merge_train as train_mod
from hooks import workflow_remediation as rem_mod
from hooks.batch_lease_manager import acquire_lease
from hooks.code_execution_guard import guard as exec_guard
from hooks.parallel_batch_scheduler import (
    create_run,
    ensure_run,
    manual_resume_run,
    mark_batch,
    prepare_state_path,
    reconcile_prepare_ledger,
    resume_run,
    schedule,
)
from hooks.parallel_batch_stage import (
    complete_stage,
    fail_stage,
    finish_review,
    gate_batch,
    open_second_review_repair,
    prepare_implementation_repair,
    prepare_review,
    record_single_repair_resolution,
    start_stage,
)
from hooks.parallel_delivery import prepare_finalization, settle_delivery
from hooks.parallel_dispatch import claim, defer
from hooks.parallel_evidence_aggregate import aggregate_evidence
from hooks.parallel_failure import HOOK_RECORD_FIELDS, classify_error_signal, find_verified_hook_block
from hooks.parallel_integration import git, integration_repo
from hooks.parallel_runtime import lease_path, load_manifest, release_lease, run_dir, save_manifest
from hooks.parallel_workspace_sync import sync_workspace
from hooks.runtime_artifact_guard import guard as artifact_guard
from hooks.worktree_manager import provision_parallel_worktree, seal_parallel_batch
from hooks.workflow_launcher import analyze_batches
from tests.test_task_runner import _refresh_parallel_pipeline, _workspace


class WorkflowSelfHealingTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="self-healing-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.workspace, self.feature_path, self.repo = _workspace(self.root)

    def _setup_batches(
        self,
        count: int = 1,
        *,
        deps_map: dict[str, list[str]] | None = None,
        enable_remediation: bool = True,
    ) -> str:
        deps_map = deps_map or {}
        root_path = self.feature_path / "plan.json"
        root = json.loads(root_path.read_text(encoding="utf-8"))
        root["codeWorkspaces"] = {"default": str(self.repo)}
        base_entry = copy.deepcopy(root["batches"][0])
        base_entry.update(executionStage="parallel", workspaceRef="default", deps=deps_map.get("B001", []))
        root["batches"][0] = base_entry
        root["batches"] = root["batches"][:1]

        base_plan = json.loads((self.feature_path / base_entry["path"]).read_text(encoding="utf-8"))
        base_plan.update(executionStage="parallel")
        base_plan["taskIds"] = ["T001"]
        base_plan["tasks"][0]["status"] = "implemented"
        (self.feature_path / base_entry["path"]).write_text(json.dumps(base_plan), encoding="utf-8")

        for idx in range(2, count + 1):
            bid, tid = f"B{idx:03d}", f"T{idx:03d}"
            entry = copy.deepcopy(base_entry)
            plan = copy.deepcopy(base_plan)
            entry.update(id=bid, path=f"plans/{bid}/plan.json", taskIds=[tid], deps=deps_map.get(bid, []))
            plan.update(batchId=bid, taskIds=[tid])
            task = plan["tasks"][0]
            task["id"] = tid
            for ac in task["acceptanceCriteria"]:
                ac["id"] = ac["id"].replace("T001", tid)
            target = self.feature_path / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(plan), encoding="utf-8")
            root["batches"].append(entry)

        root_path.write_text(json.dumps(root), encoding="utf-8")
        if not enable_remediation:
            cfg_dir = self.workspace / ".autobiz"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "runtime_config.json").write_text(
                json.dumps({"enableUnattributedRemediation": False}), encoding="utf-8"
            )
        _refresh_parallel_pipeline(self.feature_path)
        created = create_run(
            self.workspace,
            "alpha",
            max_parallel=max(2, count),
            timeout_seconds=300,
            code_workspaces=[f"default={self.repo}"],
            task_card_id="TEST-001",
        )
        return str(created["runId"])

    def _deliver_batch(self, run_id: str, batch_id: str, filename: str | None = None, content: str | None = None):
        manifest = load_manifest(self.workspace, "alpha", run_id)
        gen = int(manifest["batches"][batch_id].get("dispatchGeneration", 1))
        claimed = claim(self.workspace, "alpha", run_id, batch_id, gen, "initial", f"req-{batch_id}-{gen}")
        self.assertEqual(claimed["status"], "claimed", claimed)
        prov = provision_parallel_worktree(self.workspace, "alpha", run_id, batch_id)
        wt = Path(prov["worktreePath"])
        fname = filename or f"{batch_id}.txt"
        (wt / fname).write_text(content if content is not None else f"payload-{batch_id}\n", encoding="utf-8")
        git(wt, "add", fname)
        git(wt, "commit", "-m", f"feat: {batch_id}")
        sha = git(wt, "rev-parse", "HEAD")
        mark_batch(
            self.workspace, "alpha", run_id, batch_id, "sealed",
            worktreePath=str(wt), branchName=prov["branchName"], commitSha=sha,
        )
        for st in ("prepare", "implement", "review"):
            start_stage(self.workspace, "alpha", run_id, batch_id, st)
            if st == "review":
                finish_review(self.workspace, "alpha", run_id, batch_id, decision="passed")
            else:
                complete_stage(self.workspace, "alpha", run_id, batch_id, st, metadata={"batchCommit": sha})
        return gen, claimed["ownerToken"], wt, sha

    def test_case1_classification_precedence_and_idempotent_charging(self) -> None:
        run_id = self._setup_batches(3, enable_remediation=False)

        # 1. Transient empty_agent_output ignores identical fingerprint and runs 1 + 2 = 3 attempts.
        for expected_gen in (1, 2):
            c = claim(self.workspace, "alpha", run_id, "B001", expected_gen, "initial", f"g-{expected_gen}")
            res = defer(self.workspace, "alpha", run_id, "B001", expected_gen, c["ownerToken"], "empty_agent_output")
            self.assertEqual(res["status"], "retry_pending")
            # Duplicate defer on same generation must not double-charge.
            b = load_manifest(self.workspace, "alpha", run_id)["batches"]["B001"]
            self.assertEqual(b["recovery"]["transientRetryAttempts"], expected_gen)
            resumed = resume_run(self.workspace, "alpha", run_id)
            self.assertIn("B001", resumed["rescheduledRetryBatches"])

        c3 = claim(self.workspace, "alpha", run_id, "B001", 3, "initial", "g-3")
        res3 = defer(self.workspace, "alpha", run_id, "B001", 3, c3["ownerToken"], "empty_agent_output")
        self.assertEqual(res3["status"], "blocked")
        b1 = load_manifest(self.workspace, "alpha", run_id)["batches"]["B001"]
        self.assertEqual(b1["recovery"]["status"], "escalated_to_operator")
        self.assertEqual(b1["recovery"]["transientRetryAttempts"], 3)

        # 2. Deterministic prestart_unattributed_changes_detected escalates on attempt 1 (0 spin).
        c_det = claim(self.workspace, "alpha", run_id, "B002", 1, "initial", "det-1")
        res_det = defer(
            self.workspace, "alpha", run_id, "B002", 1, c_det["ownerToken"],
            "prestart_unattributed_changes_detected:foo.py",
        )
        self.assertEqual(res_det["status"], "blocked")
        b2 = load_manifest(self.workspace, "alpha", run_id)["batches"]["B002"]
        self.assertEqual(b2["recovery"]["status"], "escalated_to_operator")
        self.assertEqual(b2["recovery"]["retryAttempts"], 1)

        # 3. Unclassified error retries once, then escalates immediately on identical fingerprint.
        c_u1 = claim(self.workspace, "alpha", run_id, "B003", 1, "initial", "u-1")
        self.assertEqual(
            defer(self.workspace, "alpha", run_id, "B003", 1, c_u1["ownerToken"], "mysterious_ glitch_42")["status"],
            "retry_pending",
        )
        resume_run(self.workspace, "alpha", run_id)
        c_u2 = claim(self.workspace, "alpha", run_id, "B003", 2, "initial", "u-2")
        self.assertEqual(
            defer(self.workspace, "alpha", run_id, "B003", 2, c_u2["ownerToken"], "mysterious_ glitch_42")["status"],
            "blocked",
        )
        self.assertEqual(
            load_manifest(self.workspace, "alpha", run_id)["batches"]["B003"]["recovery"]["reasonCode"],
            "repeated_failure_fingerprint",
        )

    def test_case2_pre_dispatch_crash_split_prepare_and_finalization(self) -> None:
        # Part A: Prepare Crash Point 1 (CLI never started -> charges transient up to 1+2, then blocks) vs Crash Point 2 (run created -> 0 charge).
        for idx in range(1, 4):
            st = reconcile_prepare_ledger(
                self.workspace, "alpha",
                plan_digest_value="p-digest", contract_digest_value="c-digest",
                invocation_id=f"{idx:032x}", begin_attempt=True,
            )
            self.assertFalse(st["escalated"])
            self.assertEqual(st["transientFailures"], idx - 1)
        # Fourth reconcile after 3 crashed attempts before ensure -> escalated!
        st4 = reconcile_prepare_ledger(
            self.workspace, "alpha",
            plan_digest_value="p-digest", contract_digest_value="c-digest",
            invocation_id=f"{4:032x}", begin_attempt=True,
        )
        self.assertTrue(st4["escalated"])
        # Explicit --reopen-prepare clears escalation.
        reopened = reconcile_prepare_ledger(
            self.workspace, "alpha",
            plan_digest_value="p-digest", contract_digest_value="c-digest",
            invocation_id=f"{5:032x}", begin_attempt=True, reopen=True,
        )
        self.assertFalse(reopened["escalated"])
        self.assertEqual(reopened["transientFailures"], 0)
        prepare_state_path(self.workspace, "alpha").unlink(missing_ok=True)

        # Prepare Crash Point 2: inFlightAttempt recorded, then ensure_run succeeds on disk before response lost.
        run_id = self._setup_batches(1)
        manifest = load_manifest(self.workspace, "alpha", run_id)
        p_digest = manifest["planDigest"]
        reconcile_prepare_ledger(
            self.workspace, "alpha",
            plan_digest_value=p_digest, contract_digest_value="c-digest",
            invocation_id="a" * 32, begin_attempt=True,
        )
        # Force an in-flight entry while active run with matching planDigest exists on disk.
        raw_state = json.loads(prepare_state_path(self.workspace, "alpha").read_text(encoding="utf-8"))
        self.assertIsNotNone(raw_state["inFlightAttempt"])
        adopted = reconcile_prepare_ledger(
            self.workspace, "alpha",
            plan_digest_value=p_digest, contract_digest_value="c-digest",
            invocation_id="b" * 32, begin_attempt=False,
        )
        self.assertEqual(adopted["transientFailures"], 0)
        self.assertEqual(adopted["reconciledRunId"], run_id)

        # Part B: Finalization Crash Point 1 vs Crash Point 2 (fresh workspace without codeWorkspaces extra key).
        tmp2 = tempfile.TemporaryDirectory(prefix="self-healing-fin-")
        self.addCleanup(tmp2.cleanup)
        ws2, feat2, repo2 = _workspace(Path(tmp2.name))
        rp2 = feat2 / "plan.json"
        r_data = json.loads(rp2.read_text(encoding="utf-8"))
        r_data["batches"][0].update(executionStage="parallel", workspaceRef="default")
        bp2 = feat2 / r_data["batches"][0]["path"]
        b_data = json.loads(bp2.read_text(encoding="utf-8"))
        b_data.update(executionStage="parallel", taskIds=["T001"])
        b_data["tasks"][0]["status"] = "implemented"
        bp2.write_text(json.dumps(b_data), encoding="utf-8")
        rp2.write_text(json.dumps(r_data), encoding="utf-8")
        _refresh_parallel_pipeline(feat2)
        run_fin = str(create_run(
            ws2, "alpha", max_parallel=1, timeout_seconds=300,
            code_workspaces=[str(repo2)], task_card_id="TEST-001",
        )["runId"])
        c_fin = claim(ws2, "alpha", run_fin, "B001", 1, "initial", "fin-b1")
        prov_fin = provision_parallel_worktree(ws2, "alpha", run_fin, "B001")
        wt_fin = Path(prov_fin["worktreePath"])
        (wt_fin / "B001.txt").write_text("fin-payload\n", encoding="utf-8")
        git(wt_fin, "add", "B001.txt")
        git(wt_fin, "commit", "-m", "feat: B001")
        sha_fin = git(wt_fin, "rev-parse", "HEAD")
        mark_batch(ws2, "alpha", run_fin, "B001", "sealed", worktreePath=str(wt_fin), branchName=prov_fin["branchName"], commitSha=sha_fin)
        for st in ("prepare", "implement", "review"):
            start_stage(ws2, "alpha", run_fin, "B001", st)
            if st == "review":
                finish_review(ws2, "alpha", run_fin, "B001", decision="passed")
            else:
                complete_stage(ws2, "alpha", run_fin, "B001", st, metadata={"batchCommit": sha_fin})
        settled = settle_delivery(ws2, "alpha", run_fin, "B001", 1, c_fin["ownerToken"])
        self.assertEqual(settled["status"], "merged")

        # Crash Point 1: 3 prepare_finalization calls where aggregate CLI never ran -> 4th call escalates!
        for idx in range(3):
            m = load_manifest(ws2, "alpha", run_fin)
            m["activeInvocationId"] = f"inv-{idx}"
            save_manifest(ws2, "alpha", run_fin, m)
            r = prepare_finalization(ws2, "alpha", run_fin)
            self.assertEqual(r["status"], "ready")
            self.assertEqual(r["finalization"]["transientFailures"], idx)
        r4 = prepare_finalization(ws2, "alpha", run_fin)
        self.assertEqual(r4["status"], "escalated_to_operator")

        # Reopen finalization, then simulate Crash Point 2: aggregate_evidence writes final-evidence-aggregate.json, response lost.
        r_reopen = prepare_finalization(ws2, "alpha", run_fin, reopen=True)
        self.assertEqual(r_reopen["status"], "ready")
        self.assertEqual(r_reopen["finalization"]["inFlightAttempt"]["step"], "aggregate")
        aggregate_evidence(ws2, "alpha", run_fin, finish_workflow=True)
        resume_run(ws2, "alpha", run_fin)

        # Next prepare_finalization adopts durable aggregateReceipt with 0 failure charged and advances step to code_done!
        r_adopted = prepare_finalization(ws2, "alpha", run_fin)
        self.assertEqual(r_adopted["status"], "ready")
        self.assertEqual(r_adopted["finalization"]["transientFailures"], 0)
        self.assertTrue(r_adopted["aggregateReceipt"]["reconciled"])
        self.assertEqual(r_adopted["finalization"]["inFlightAttempt"]["step"], "code_done")
        request_id = r_adopted["finalization"]["inFlightAttempt"]["attemptId"]
        repeated = prepare_finalization(ws2, "alpha", run_fin, request_id=request_id, requested_step="code_done")
        self.assertEqual(repeated["finalization"]["inFlightAttempt"]["attemptId"], request_id)
        self.assertEqual(repeated["finalization"]["transientFailures"], 0)
        blocked = prepare_finalization(ws2, "alpha", run_fin, begin_attempt=False, blocked_checkpoint=request_id)
        self.assertEqual(blocked["status"], "escalated_to_operator")
        self.assertEqual(blocked["finalization"]["escalationCategory"], "checkpoint_blocking_error")

    def test_resume_launcher_reads_prepare_escalation_without_charging_inflight(self) -> None:
        self._setup_batches()
        args = analyze_batches("alpha", workspace=self.workspace, task_card_id="TEST-001")["workflowArgs"]
        binding = {"plan_digest_value": args["planDigest"], "contract_digest_value": args["runtimeContractDigest"]}
        path = prepare_state_path(self.workspace, "alpha")
        for idx in range(4):
            ledger = reconcile_prepare_ledger(
                self.workspace, "alpha", **binding, invocation_id=f"{idx + 1:032x}",
                begin_attempt=True, attempt_number=1, resume=True,
            )
            contents = path.read_bytes()
            launch = analyze_batches("alpha", workspace=self.workspace, task_card_id="TEST-001")
            self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(launch["canStartWorkflow"], not ledger["escalated"])
        self.assertEqual(launch["requiredAction"], "resolve_prepare_blocker_then_reopen")
        reopened = analyze_batches("alpha", workspace=self.workspace, task_card_id="TEST-001", reopen_prepare=True)
        self.assertTrue(reopened["canStartWorkflow"])

    def test_case3_delayed_gen1_hook_blocks_never_poison_gen2_and_strict_whitelist(self) -> None:
        run_id = self._setup_batches(1)
        c1 = claim(self.workspace, "alpha", run_id, "B001", 1, "initial", "gen1")
        prov = provision_parallel_worktree(self.workspace, "alpha", run_id, "B001")
        wt = Path(prov["worktreePath"])
        lease1 = acquire_lease(
            self.workspace, "alpha", run_id, "B001", ttl_seconds=60,
            dispatch_auth={"generation": 1, "token": c1["ownerToken"]},
        )
        # Gen 1 times out -> Gen 2 starts.
        defer(self.workspace, "alpha", run_id, "B001", 1, c1["ownerToken"], "timeout")
        resume_run(self.workspace, "alpha", run_id)
        c2 = claim(self.workspace, "alpha", run_id, "B001", 2, "initial", "gen2")
        self.assertEqual(c2["status"], "claimed")
        lease2 = acquire_lease(
            self.workspace, "alpha", run_id, "B001", ttl_seconds=60,
            dispatch_auth={"generation": 2, "token": c2["ownerToken"]},
        )
        self.assertNotEqual(lease1["ownerToken"], lease2["ownerToken"])

        env = {
            "PLUGIN_WORKSPACE": str(self.workspace.parent),
            "PROJECT_DIR": self.workspace.name,
            "FEATURE_ID": "alpha",
        }
        secret = "SUPER_SECRET_TOKEN_XYZ_999"
        with patch.dict(os.environ, env, clear=False):
            # 1) Delayed Gen 1 runs uncredentialed mvn test in shared worktree.
            reason1 = exec_guard({
                "tool_name": "execute",
                "tool_input": {"command": f"mvn test -Dsecret={secret}", "cwd": str(wt)},
            })
            self.assertIsNotNone(reason1)
            # 2) Delayed Gen 1 runs command carrying stale Gen-1 dispatch credentials.
            reason2 = exec_guard({
                "tool_name": "execute",
                "tool_input": {
                    "command": f"mvn test --run-id {run_id} --batch-id B001 --dispatch-generation 1 --dispatch-token {c1['ownerToken']} -Dsecret={secret}",
                    "cwd": str(wt),
                },
            })
            self.assertIsNotNone(reason2)
            # 3) Delayed Gen 1 runs command carrying revoked Gen-1 lease token.
            reason3 = exec_guard({
                "tool_name": "execute",
                "tool_input": {
                    "command": f"mvn test --run-id {run_id} --batch-id B001 --lease-token {lease1['ownerToken']} -Dsecret={secret}",
                    "cwd": str(wt),
                },
            })
            self.assertIsNotNone(reason3)
            # 4) Runtime artifact guard block with secret in command.
            reason4 = artifact_guard({
                "tool_name": "execute",
                "tool_input": {"command": f"python -c 'open(\"plan.json\",\"w\")' --secret {secret}"},
            })
            self.assertIsNotNone(reason4)

        # None of Gen 1's blocks may poison Gen 2!
        self.assertIsNone(find_verified_hook_block(self.workspace, "alpha", run_id, "B001", 2))

        # Verify strict closed whitelist & zero secret/reason leakage on disk.
        diag_path = run_dir(self.workspace, "alpha", run_id) / "diagnostics" / "hook-blocks.jsonl"
        raw_log = diag_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw_log)
        self.assertNotIn("GUARD_DIAGNOSTICS", raw_log)
        for line in raw_log.splitlines():
            rec = json.loads(line)
            self.assertEqual(set(rec.keys()), set(HOOK_RECORD_FIELDS))
            self.assertEqual(rec["attributionConfidence"], "unverified_diagnostic_only")
            self.assertIsNone(rec["dispatchGeneration"])

        # Positive check: a call carrying Gen 2's active dispatch token IS verified_generation!
        with patch.dict(os.environ, env, clear=False):
            exec_guard({
                "tool_name": "execute",
                "tool_input": {
                    "command": f"mvn test --run-id {run_id} --batch-id B001 --dispatch-generation 2 --dispatch-token {c2['ownerToken']}",
                    "cwd": str(wt),
                },
            })
        verified = find_verified_hook_block(self.workspace, "alpha", run_id, "B001", 2)
        self.assertIsNotNone(verified)
        self.assertEqual(verified["dispatchGeneration"], 2)
        self.assertEqual(verified["attributionConfidence"], "verified_generation")

    def test_case4_terminal_escalation_fenced_everywhere_and_selective_reopen(self) -> None:
        run_id = self._setup_batches(3, enable_remediation=False)
        # B001 merges; B002 (with provisioned dirty worktree) and B003 both escalate to operator.
        g1, t1, _, _ = self._deliver_batch(run_id, "B001")
        self.assertEqual(settle_delivery(self.workspace, "alpha", run_id, "B001", g1, t1)["status"], "merged")

        c2 = claim(self.workspace, "alpha", run_id, "B002", 1, "initial", "b2")
        prov2 = provision_parallel_worktree(self.workspace, "alpha", run_id, "B002")
        wt2 = Path(prov2["worktreePath"])
        (wt2 / "partial_b2.py").write_text("x = 1\n", encoding="utf-8")
        defer(self.workspace, "alpha", run_id, "B002", 1, c2["ownerToken"], "engineering_handoff_identity_mismatch")

        c3 = claim(self.workspace, "alpha", run_id, "B003", 1, "initial", "b3")
        defer(self.workspace, "alpha", run_id, "B003", 1, c3["ownerToken"], "engineering_handoff_identity_mismatch")

        m = load_manifest(self.workspace, "alpha", run_id)
        self.assertEqual(m["batches"]["B002"]["recovery"]["status"], "escalated_to_operator")
        self.assertEqual(m["batches"]["B003"]["recovery"]["status"], "escalated_to_operator")

        # 1) mark_batch cannot flip escalated_to_operator back to retry_pending.
        with self.assertRaisesRegex(ValueError, "parallel_batch_escalated_terminal:B002"):
            mark_batch(self.workspace, "alpha", run_id, "B002", "retry_pending")

        # 2) defer cannot flip escalated_to_operator back to retry_pending.
        self.assertEqual(
            defer(self.workspace, "alpha", run_id, "B002", 1, c2["ownerToken"], "empty_agent_output")["status"],
            "blocked",
        )

        # 3) Automatic resume_run and un-parameterized manual_resume_run keep both frozen.
        self.assertEqual(resume_run(self.workspace, "alpha", run_id)["rescheduledRetryBatches"], [])
        self.assertEqual(manual_resume_run(self.workspace, "alpha", run_id)["rescheduledRetryBatches"], [])

        # 4) Un-parameterized workflow_launcher blocks without spinning.
        launch_blocked = analyze_batches("alpha", workspace=self.workspace, task_card_id="TEST-001")
        self.assertFalse(launch_blocked["useWorkflow"])
        self.assertTrue(launch_blocked["reason"].startswith("workflow_escalated_to_operator:"))
        self.assertEqual(launch_blocked["escalatedBatchIds"], ["B002", "B003"])

        # 5) Explicit --reopen-batch B002 reopens ONLY B002 (preserving its dirty worktree), leaving B003 escalated!
        launch_reopen = analyze_batches(
            "alpha", workspace=self.workspace, task_card_id="TEST-001", reopen_batches=["B002"]
        )
        self.assertTrue(launch_reopen["useWorkflow"])
        self.assertEqual(launch_reopen["workflowArgs"]["reopenBatches"], ["B002"])

        reopened = manual_resume_run(self.workspace, "alpha", run_id, reopen_batches=["B002"])
        self.assertEqual([item["batchId"] for item in reopened["implementationRecoveryBatches"]], ["B002"])
        self.assertTrue((wt2 / "partial_b2.py").is_file())
        m_after = load_manifest(self.workspace, "alpha", run_id)
        self.assertEqual(m_after["batches"]["B003"]["status"], "blocked")
        self.assertEqual(m_after["batches"]["B003"]["recovery"]["status"], "escalated_to_operator")

    def test_case5_candidate_conflict_reopen_resume_and_rebuild(self) -> None:
        # Mode A: --reopen-candidate default:1:resume
        run_id = self._setup_batches(2)
        g1, t1, _, _ = self._deliver_batch(run_id, "B001", filename="shared.txt", content="from-B001\n")
        g2, t2, _, _ = self._deliver_batch(run_id, "B002", filename="shared.txt", content="from-B002\n")
        self.assertEqual(settle_delivery(self.workspace, "alpha", run_id, "B001", g1, t1)["status"], "merged")
        conflicted = settle_delivery(self.workspace, "alpha", run_id, "B002", g2, t2)
        self.assertEqual(conflicted["status"], "candidate_conflicted")
        wave = conflicted["wave"]

        # Exhaust candidate conflict resolution budget so train enters needs_resolution and B002 escalates.
        for idx in range(3):
            train_mod.prepare_resolution(self.workspace, "alpha", run_id, wave, "default", attempt_id=f"att-{idx}")
        res_ex = train_mod.prepare_resolution(
            self.workspace, "alpha", run_id, wave, "default", attempt_id="att-final", workflow_retry=True
        )
        # Consume the +1 workflow_retry slot, then next call hits max_resolution_attempts_exceeded and escalates B002!
        if res_ex.get("status") == "resolution_required":
            res_ex = train_mod.prepare_resolution(
                self.workspace, "alpha", run_id, wave, "default", attempt_id="att-over", workflow_retry=True
            )
        self.assertEqual(res_ex["error"], "max_resolution_attempts_exceeded")
        m = load_manifest(self.workspace, "alpha", run_id)
        self.assertEqual(m["batches"]["B002"]["recovery"]["status"], "escalated_to_operator")

        # Guardrail: --reopen-batch B002 alone MUST fail with candidate_reopen_required!
        with self.assertRaisesRegex(ValueError, "candidate_reopen_required:default:wave-002"):
            manual_resume_run(self.workspace, "alpha", run_id, reopen_batches=["B002"])
        launcher_refused = analyze_batches(
            "alpha", workspace=self.workspace, task_card_id="TEST-001", reopen_batches=["B002"]
        )
        self.assertFalse(launcher_refused["useWorkflow"])
        self.assertIn("candidate_reopen_required:", launcher_refused["reason"])

        # Operator resolves conflict in candidate worktree and commits merge -> reopen with :resume!
        cand_wt = Path(m["mergeTrains"][f"default:wave-{wave:03d}"]["worktreePath"])
        (cand_wt / "shared.txt").write_text("merged-B001-and-B002\n", encoding="utf-8")
        git(cand_wt, "add", "shared.txt")
        git(cand_wt, "commit", "-m", "resolve conflict")
        reopened = manual_resume_run(
            self.workspace, "alpha", run_id, reopen_candidates=[f"default:{wave}:resume"]
        )
        self.assertIn("B002", reopened["mergeableBatches"])
        new_gen = reopened["dispatchGrants"]["B002"]
        c_new = claim(self.workspace, "alpha", run_id, "B002", new_gen, "merge_candidate", "reopen-merge")
        # Pass reviewed_candidate since shared.txt changed vs B002's seal.
        cand_sha = git(cand_wt, "rev-parse", "HEAD")
        merged = settle_delivery(
            self.workspace, "alpha", run_id, "B002", new_gen, c_new["ownerToken"], reviewed_candidate=cand_sha
        )
        self.assertEqual(merged["status"], "merged")

        # Mode B: --reopen-candidate default:<wave>:rebuild discards conflicted candidate and re-queues B002!
        tmp_rb = tempfile.TemporaryDirectory(prefix="self-healing-rb-")
        self.addCleanup(tmp_rb.cleanup)
        self.workspace, self.feature_path, self.repo = _workspace(Path(tmp_rb.name))
        run2 = self._setup_batches(2)
        g1b, t1b, _, _ = self._deliver_batch(run2, "B001", filename="shared2.txt", content="v1\n")
        g2b, t2b, _, _ = self._deliver_batch(run2, "B002", filename="shared2.txt", content="v2\n")
        self.assertEqual(settle_delivery(self.workspace, "alpha", run2, "B001", g1b, t1b)["status"], "merged")
        conf2 = settle_delivery(self.workspace, "alpha", run2, "B002", g2b, t2b)
        self.assertEqual(conf2["status"], "candidate_conflicted")
        w2 = conf2["wave"]
        for idx in range(4):
            train_mod.prepare_resolution(
                self.workspace, "alpha", run2, w2, "default", attempt_id=f"rb-{idx}", workflow_retry=(idx >= 2)
            )
        rebuilt = manual_resume_run(
            self.workspace, "alpha", run2, reopen_candidates=[f"default:{w2}:rebuild"]
        )
        self.assertIn("B002", rebuilt["mergeableBatches"])
        m2 = load_manifest(self.workspace, "alpha", run2)
        self.assertEqual(m2["mergeTrains"][f"default:wave-{w2:03d}"]["status"], "discarded")

    def test_case6_partial_drain_preserves_independent_merges_and_blocks_code_done(self) -> None:
        run_id = self._setup_batches(4, deps_map={"B004": ["B002"]}, enable_remediation=False)
        # B002 escalates (blocking downstream B004), while independent B001 & B003 merge and sync!
        c2 = claim(self.workspace, "alpha", run_id, "B002", 1, "initial", "b2-fail")
        defer(self.workspace, "alpha", run_id, "B002", 1, c2["ownerToken"], "prestart_unattributed_changes_detected:x.py")

        for bid in ("B001", "B003"):
            g, tok, _, _ = self._deliver_batch(run_id, bid)
            self.assertEqual(settle_delivery(self.workspace, "alpha", run_id, bid, g, tok)["status"], "merged")

        sync_res = sync_workspace(self.workspace, "alpha", run_id, "default", "inv-drain")
        self.assertEqual(sync_res["status"], "synced")
        self.assertTrue((self.repo / "B001.txt").is_file())
        self.assertTrue((self.repo / "B003.txt").is_file())

        # B004 is never scheduled because B002 is escalated_to_operator; prepare_finalization refuses ready!
        snap = schedule(self.workspace, "alpha", run_id, workflow_view=True)
        self.assertNotIn("B004", snap["readyBatches"])
        fin = prepare_finalization(self.workspace, "alpha", run_id)
        self.assertEqual(fin["status"], "repair_required")
        self.assertEqual(sorted(fin["batchIds"]), ["B002", "B004"])
        relaunch = analyze_batches("alpha", workspace=self.workspace, task_card_id="TEST-001")
        self.assertFalse(relaunch["canStartWorkflow"])
        self.assertEqual(relaunch["requiredAction"], "resolve_escalated_batches_then_reopen")

        state_json = json.loads((self.workspace / ".autobizdevops" / "state.json").read_text(encoding="utf-8"))
        self.assertNotEqual(state_json.get("features", {}).get("alpha", {}).get("checkpoint"), "code_done")

    def test_phase2_wal_s1_crash_recovery_and_stage_progress_validator(self) -> None:
        run_id = self._setup_batches(2, enable_remediation=True)
        c1 = claim(self.workspace, "alpha", run_id, "B001", 1, "initial", "b1-gen1")
        prov = provision_parallel_worktree(self.workspace, "alpha", run_id, "B001")
        wt = Path(prov["worktreePath"])

        # Write a dirty unattributed file that is BOTH staged AND modified differently in worktree,
        # so preIndexBlob != baseBlob AND preWorktreeSha256 != baseWorktreeSha256!
        (wt / "stray.txt").write_text("staged-version\n", encoding="utf-8")
        git(wt, "add", "stray.txt")
        (wt / "stray.txt").write_text("worktree-modified-version\n", encoding="utf-8")
        (wt / "untracked.tmp").write_text("temp-noise\n", encoding="utf-8")

        # Deferring with prestart_unattributed_changes_detected routes B001 to remediation_pending!
        res = defer(
            self.workspace, "alpha", run_id, "B001", 1, c1["ownerToken"],
            "prestart_unattributed_changes_detected:stray.txt,untracked.tmp",
        )
        self.assertEqual(res["status"], "remediation_pending")
        snap = schedule(self.workspace, "alpha", run_id, workflow_view=True)
        self.assertEqual([item["batchId"] for item in snap["remediationBatches"]], ["B001"])

        rem_gen = snap["dispatchGrants"]["B001"]
        self.assertGreater(rem_gen, 1, "live Workflow must be able to dispatch remediation without restarting")
        c_rem = claim(self.workspace, "alpha", run_id, "B001", rem_gen, "remediation", "rem-1")
        self.assertEqual(c_rem["status"], "claimed")

        inspected = rem_mod.inspect_unattributed(
            self.workspace, "alpha", run_id, "B001", generation=rem_gen, token=c_rem["ownerToken"]
        )
        self.assertEqual(inspected["unattributedFiles"], ["stray.txt", "untracked.tmp"])

        # Inject crash right after stray.txt's index is restored, BEFORE its worktree file is restored (State S1!).
        def crash_after_index(rel_path: str) -> None:
            if rel_path == "stray.txt":
                raise RuntimeError("injected_crash_at_S1")

        rem_mod._AFTER_INDEX_RESTORE_HOOK = crash_after_index
        try:
            with self.assertRaisesRegex(RuntimeError, "injected_crash_at_S1"):
                rem_mod.archive_and_restore_unattributed(
                    self.workspace, "alpha", run_id, "B001",
                    generation=rem_gen, token=c_rem["ownerToken"],
                    expected_snapshot_digest=inspected["snapshotDigest"],
                    paths=["stray.txt", "untracked.tmp"],
                )
        finally:
            rem_mod._AFTER_INDEX_RESTORE_HOOK = None

        # At this exact point: stray.txt's index is ABSENT (baseBlob), while stray.txt still exists in worktree!
        self.assertEqual(rem_mod._index_blob(wt, "stray.txt"), rem_mod.ABSENT)
        self.assertTrue((wt / "stray.txt").is_file())

        # Replay on recovery via verify_remediation: recognizes S1_index_restored_worktree_pending and completes S2!
        interrupted = defer(self.workspace, "alpha", run_id, "B001", rem_gen, c_rem["ownerToken"], "transport timeout")
        self.assertEqual(interrupted["status"], "remediation_pending")
        next_gen = schedule(self.workspace, "alpha", run_id)["dispatchGrants"]["B001"]
        self.assertGreater(next_gen, rem_gen)
        next_rem = claim(self.workspace, "alpha", run_id, "B001", next_gen, "remediation", "rem-2")
        with self.assertRaisesRegex(ValueError, "parallel_dispatch_stale_remediation_owner"):
            rem_mod.verify_remediation(self.workspace, "alpha", run_id, "B001", generation=rem_gen, token=c_rem["ownerToken"])
        self.assertTrue((wt / "stray.txt").is_file())
        verified = rem_mod.verify_remediation(
            self.workspace, "alpha", run_id, "B001", generation=next_gen, token=next_rem["ownerToken"]
        )
        self.assertEqual(verified["outcome"], "resolved")
        self.assertEqual(verified["remainingUnattributedFiles"], [])
        self.assertFalse((wt / "stray.txt").exists())
        self.assertFalse((wt / "untracked.tmp").exists())

        journal_files = list((run_dir(self.workspace, "alpha", run_id) / "recovery-archives" / "B001").glob("*.journal.json"))
        self.assertEqual(len(journal_files), 1)
        journal_data = json.loads(journal_files[0].read_text(encoding="utf-8"))
        self.assertEqual(journal_data["status"], "completed")
        self.assertIn(
            {"path": "stray.txt", "fromState": "S1_index_restored_worktree_pending"},
            journal_data["transitions"],
        )

        # Also verify 0-progress on B002 escalates immediately without opening a 2nd round!
        c2 = claim(self.workspace, "alpha", run_id, "B002", 1, "initial", "b2-gen1")
        prov2 = provision_parallel_worktree(self.workspace, "alpha", run_id, "B002")
        (Path(prov2["worktreePath"]) / "unfixed.txt").write_text("still-dirty\n", encoding="utf-8")
        defer(
            self.workspace, "alpha", run_id, "B002", 1, c2["ownerToken"],
            "prestart_unattributed_changes_detected:unfixed.txt",
        )
        snap2 = schedule(self.workspace, "alpha", run_id, workflow_view=True)
        g2 = snap2["dispatchGrants"]["B002"]
        c2_rem = claim(self.workspace, "alpha", run_id, "B002", g2, "remediation", "b2-rem")
        rem_mod.inspect_unattributed(self.workspace, "alpha", run_id, "B002", generation=g2, token=c2_rem["ownerToken"])
        no_prog = rem_mod.verify_remediation(self.workspace, "alpha", run_id, "B002", generation=g2, token=c2_rem["ownerToken"])
        self.assertEqual(no_prog["outcome"], "escalated")
        self.assertEqual(no_prog["reasonCode"], "remediation_no_progress")
        self.assertEqual(
            load_manifest(self.workspace, "alpha", run_id)["batches"]["B002"]["recovery"]["status"],
            "escalated_to_operator",
        )

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_remediation_archives_symlink_and_companion_as_distinct_entries(self) -> None:
        run_id = self._setup_batches()
        initial = claim(self.workspace, "alpha", run_id, "B001", 1, "initial", "archive-fixture")
        wt = Path(provision_parallel_worktree(self.workspace, "alpha", run_id, "B001")["worktreePath"])
        (wt / "draft").symlink_to("original-target")
        (wt / "draft.symlink").write_bytes(b"companion")
        (wt / "draft [final].txt").write_bytes(b"literal path")
        defer(self.workspace, "alpha", run_id, "B001", 1, initial["ownerToken"], "prestart_unattributed_changes_detected")
        gen = schedule(self.workspace, "alpha", run_id)["dispatchGrants"]["B001"]
        grant = claim(self.workspace, "alpha", run_id, "B001", gen, "remediation", "archive")
        auth = {"generation": gen, "token": grant["ownerToken"]}
        inspected = rem_mod.inspect_unattributed(self.workspace, "alpha", run_id, "B001", **auth)
        result = rem_mod.archive_and_restore_unattributed(
            self.workspace, "alpha", run_id, "B001", **auth,
            expected_snapshot_digest=inspected["snapshotDigest"], paths=inspected["unattributedFiles"],
        )
        journal = json.loads(Path(result["journalPath"]).read_text(encoding="utf-8"))
        self.assertEqual(
            {entry["path"]: (Path(result["archiveDir"]) / "worktree" / entry["archiveFile"]).read_bytes()
             for entry in journal["entries"]},
            {"draft": b"original-target", "draft.symlink": b"companion", "draft [final].txt": b"literal path"},
        )
        manifest = load_manifest(self.workspace, "alpha", run_id)
        manifest["batches"]["B001"]["recovery"]["remediationClaimedBaseline"] = {"drifted.txt": {}}
        save_manifest(self.workspace, "alpha", run_id, manifest)
        verified = rem_mod.verify_remediation(self.workspace, "alpha", run_id, "B001", **auth)
        self.assertEqual(verified["reasonCode"], "remediation_claimed_files_modified")
        self.assertEqual(verified["schedulerSnapshot"]["manifest"]["batches"]["B001"]["recovery"]["status"],
                         "escalated_to_operator")

    def test_remediation_ignores_closed_history_but_escalates_prepared_compile(self) -> None:
        run_id = self._setup_batches()
        g = claim(self.workspace, "alpha", run_id, "B001", 1, "initial", "history-fixture")
        wt = Path(provision_parallel_worktree(self.workspace, "alpha", run_id, "B001")["worktreePath"])
        (wt / "local.txt").write_text("unattributed", encoding="utf-8")
        defer(self.workspace, "alpha", run_id, "B001", 1, g["ownerToken"], "prestart_unattributed_changes_detected")
        gen = schedule(self.workspace, "alpha", run_id)["dispatchGrants"]["B001"]
        grant = claim(self.workspace, "alpha", run_id, "B001", gen, "remediation", "history")
        auth = {"generation": gen, "token": grant["ownerToken"]}
        records = self.feature_path / ".task-runs" / "T001"
        records.mkdir(parents=True, exist_ok=True)
        identity = {"taskId": "T001", "batchId": "B001", "parallelRunId": run_id}
        aborted = {**identity, "runId": "aborted", "status": "aborted", "planStatusReset": True}
        (records / "aborted.json").write_text(json.dumps(aborted), encoding="utf-8")
        (records / "failed.json").write_text(json.dumps({
            **identity, "runId": "failed", "status": "failed", "updatedAt": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        (records / "done.json").write_text(json.dumps({
            **identity, "runId": "done", "status": "implemented", "updatedAt": "2026-01-02T00:00:00Z",
            "implementationEvidenceId": "fixture-evidence",
        }), encoding="utf-8")
        self.assertEqual(rem_mod.inspect_unattributed(self.workspace, "alpha", run_id, "B001", **auth)["unattributedFiles"],
                         ["local.txt"])
        (records / "aborted.json").write_text(json.dumps({
            **aborted, "compileAttempts": [{"status": "prepared"}],
        }), encoding="utf-8")
        result = rem_mod.inspect_unattributed(self.workspace, "alpha", run_id, "B001", **auth)
        self.assertEqual(result["reasonCode"], "remediation_compile_completion_unconfirmed")
        self.assertEqual(result["schedulerSnapshot"]["manifest"]["batches"]["B001"]["status"], "blocked")
        self.assertTrue((wt / "local.txt").is_file())

    def test_second_repair_reclaims_only_quiescent_same_dispatch_lease(self) -> None:
        from tests.test_code_stage_authority import CodeStageAuthorityTest

        fixture = CodeStageAuthorityTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        _, initial = fixture._initial_delivery()
        ws, feat, rid, bid = fixture.workspace, "alpha", fixture.parallel_id, "B001"
        prepare_review(ws, feat, rid, bid)
        finish_review(ws, feat, rid, bid, decision="failed", message="production.txt:1 defect")
        with self.assertRaisesRegex(ValueError, "review_repair_lease_unconfirmed") as failure:
            open_second_review_repair(ws, feat, rid, bid)
        self.assertEqual(classify_error_signal(str(failure.exception))[0], "transient")
        self.assertTrue(lease_path(ws, feat, rid, bid).is_file())

        release_lease(ws, feat, rid, bid, fixture.token)
        mark_batch(ws, feat, rid, bid, "sealed", commitSha=initial["commitSha"])
        grant = claim(ws, feat, rid, bid, 1, "stage_recovery", "repair")
        self.assertEqual(grant["status"], "claimed")
        auth = {"generation": 1, "token": grant["ownerToken"]}
        acquire_lease(ws, feat, rid, bid, dispatch_auth=auth)
        output = io.StringIO()
        with redirect_stdout(output):
            rc = stage_mod.main([
                "reopen-second-repair", "--workspace", str(ws), "--feature", feat,
                "--run-id", rid, "--batch-id", bid, "--dispatch-generation", "1",
                "--dispatch-token", grant["ownerToken"],
            ])
        result = json.loads(output.getvalue())
        self.assertEqual(rc, 0, result)
        self.assertTrue(result["repairPolicy"]["requireActualReview"])
        self.assertFalse(lease_path(ws, feat, rid, bid).exists())

    def test_phase2_second_review_repair_enforces_positive_provenance_and_blocks_bypasses(self) -> None:
        # Use CodeStageAuthorityTest fixture so real task evidence exists for finalize_implementation.
        from tests import test_code_stage_authority as auth_mod

        fixture = auth_mod.CodeStageAuthorityTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._initial_delivery()
        ws, feat, rid, bid = fixture.workspace, "alpha", fixture.parallel_id, "B001"
        wt = fixture.worktree

        # Round-0 Review fails with an implementation finding -> writes REPAIR_CONTEXT.json.
        prepare_review(ws, feat, rid, bid)
        failed1 = finish_review(ws, feat, rid, bid, decision="failed", message="production.txt:1 defect round 1")
        self.assertEqual(failed1["status"], "failed")

        # Round-1 repair seals new commit and uses single_repair_accepted.
        lease1 = fixture.token
        (wt / "production.txt").write_text("round-1-fix\n", encoding="utf-8")
        sealed1 = seal_parallel_batch(ws, feat, rid, bid, wt, lease1, purpose="review", commit_stage="rework")
        self.assertTrue(sealed1["success"], sealed1)
        record_single_repair_resolution(
            ws, feat, rid, bid, failed_stage="review",
            metadata={"batchCommit": sealed1["commitSha"], "worktreePath": str(wt), "branchName": fixture.branch},
        )

        # Simulate follow-up Review failure & open Round 2 durable policy!
        m = load_manifest(ws, feat, rid)
        m["batches"][bid]["stageStates"]["review"]["status"] = "running"
        save_manifest(ws, feat, rid, m)
        fail_stage(ws, feat, rid, bid, "review", failure_type="implementation", message="production.txt:1 defect round 2")
        release_lease(ws, feat, rid, bid, lease1, final_status="pending")

        opened = open_second_review_repair(ws, feat, rid, bid)
        self.assertEqual(opened["repairPolicy"]["round"], 2)
        self.assertTrue(opened["repairPolicy"]["requireActualReview"])

        # Bypass 1 blocked: generic CLI `complete --stage review` is rejected!
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = stage_mod.main([
                "complete", "--workspace", str(ws), "--feature", feat,
                "--run-id", rid, "--batch-id", bid, "--stage", "review",
            ])
        self.assertEqual(rc, 1)
        self.assertIn("parallel_review_requires_finish_review_entrypoint", buf.getvalue())

        # Bypass 2 blocked: record_single_repair_resolution is rejected on Round 2!
        with self.assertRaisesRegex(ValueError, "parallel_repair_requires_actual_review"):
            record_single_repair_resolution(
                ws, feat, rid, bid, failed_stage="review",
                metadata={"batchCommit": sealed1["commitSha"], "worktreePath": str(wt), "branchName": fixture.branch},
            )

        # Seal Round-2 rework commit (`commit_r2 != commit_r1`).
        lease2 = fixture._acquire()
        (wt / "production.txt").write_text("round-2-fix\n", encoding="utf-8")
        sealed2 = seal_parallel_batch(ws, feat, rid, bid, wt, lease2, purpose="review", commit_stage="rework")
        self.assertTrue(sealed2["success"], sealed2)
        self.assertNotEqual(sealed2["commitSha"], sealed1["commitSha"])

        # Crash simulation after Round-2 seal: prepare_implementation_repair MUST return review_required, NOT reused!
        prep_r2 = prepare_implementation_repair(ws, feat, rid, bid, "review")
        self.assertEqual(prep_r2["status"], "review_required")
        self.assertEqual(gate_batch(ws, feat, rid, bid)["error"], "parallel_batch_stage_gate_incomplete")

        # Bypass 3 blocked: direct Python complete_stage(..., "review") without finish_review authority is rejected!
        start_stage(ws, feat, rid, bid, "review")
        with self.assertRaisesRegex(ValueError, "parallel_review_requires_finish_review_entrypoint"):
            complete_stage(ws, feat, rid, bid, "review", metadata={"batchCommit": sealed2["commitSha"]})

        # Controlled finish_review stamps positive provenance and satisfies all 5 assertions in gate_batch!
        passed2 = finish_review(ws, feat, rid, bid, decision="passed")
        self.assertEqual(passed2["status"], "passed")
        gated = gate_batch(ws, feat, rid, bid)
        self.assertTrue(gated["success"], gated)
        self.assertEqual(gated["status"], "ready_to_candidate")

        # Tampering metadata provenance invalidates digest / provenance check in gate_batch!
        ev_path = Path(load_manifest(ws, feat, rid)["batches"][bid]["stageStates"]["review"]["evidencePath"])
        tampered = json.loads(ev_path.read_text(encoding="utf-8"))
        tampered["metadata"]["repairRound"] = 1
        tampered["digest"] = stage_mod._digest({k: v for k, v in tampered.items() if k != "digest"})
        ev_path.write_text(json.dumps(tampered), encoding="utf-8")
        gated_bad = gate_batch(ws, feat, rid, bid)
        self.assertFalse(gated_bad["success"])
        self.assertIn("parallel_batch_actual_review_proof_invalid:repair_round_mismatch", gated_bad["error"])


if __name__ == "__main__":
    unittest.main()
