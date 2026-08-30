"""dev.specs 的审计账本是日志，不是门。

`.runtime/REVIEW_FINDINGS.jsonl` 与 `.runtime/DECISIONS.jsonl` 由 PostToolUse
hook 自动追加，记录 critic 真实报了什么、用户真实裁定了什么。它们不参与
`specs_done` 的放行判定：账本形状不对时 hook 记一条提示就放行，不阻断工具调用，
也不因产物字节变化强制重跑 critic。dev.specs 的阻断集只有四个 validator。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUTODEV_HOOKS = ROOT / "skills" / "autodev" / "hooks"
if str(AUTODEV_HOOKS) not in sys.path:
    sys.path.insert(0, str(AUTODEV_HOOKS))

from hooks.augment_critic_task_prompt import build_updated_input  # noqa: E402
from hooks.critic_findings_writer import append_run, parse_response  # noqa: E402
from hooks.decision_ledger_writer import append_records, records_from_payload  # noqa: E402
from hooks.init_workspace import create_feature, init_workspace  # noqa: E402
from hooks.runtime_artifact_guard import protected_path  # noqa: E402
from hooks.stage_gate import validate_stage  # noqa: E402


RUN_ID = "RV-20260828T120000Z-deadbeef"

PROPOSAL = """# Proposal: 导出

## Why

需要导出。

## What Changes

- 新增导出。

## Capabilities

### New Capabilities

- `order-export`: 导出
  - **Existing:** none

### Modified Capabilities

无

### Removed Capabilities

无

## Impact

- export

## Out of Scope

- 无

## Decision Log

无

## Open Questions

无
"""

SPEC = """# Order Export

## Source References / 外部资料引用

无

## ADDED Requirements

### Requirement [REQ-001]: 创建导出

The system SHALL 创建导出任务。

#### Scenario [SCN-001]: 创建成功

- **WHEN** 用户提交请求
- **THEN** 返回任务号

## MODIFIED Requirements

无

## REMOVED Requirements

无
"""

REVIEW = """# Specs Review

## Verdict

PASS

## Findings

无

## Unresolved

无
"""


class SpecsRuntimeLedgerTest(unittest.TestCase):
    def _feature(self, tmp: str) -> tuple[Path, Path]:
        workspace = Path(tmp) / "demo"
        workspace.mkdir()
        init_workspace(workspace)
        create_feature(workspace, "alpha")
        feature_dir = workspace / ".autobizdevops" / "features" / "alpha"
        (feature_dir / "proposal.md").write_text(PROPOSAL, encoding="utf-8")
        spec = feature_dir / "specs" / "order-export" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(SPEC, encoding="utf-8")
        return workspace, feature_dir

    def _set_checkpoint(self, workspace: Path, checkpoint: str) -> None:
        state = workspace / ".autobizdevops" / "state.json"
        data = json.loads(state.read_text(encoding="utf-8"))
        data["features"]["alpha"]["checkpoint"] = checkpoint
        state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _run_hook(self, script: str, workspace: Path, payload: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(
            {
                "PLUGIN_WORKSPACE": str(workspace.parent),
                "PROJECT_DIR": workspace.name,
                "FEATURE_ID": "alpha",
            }
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )

    def test_structure_gate_does_not_require_review_but_final_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = self._feature(tmp)
            structure = validate_stage(
                workspace=workspace, feature="alpha", stage="dev.specs", phase="structure"
            )
            final = validate_stage(
                workspace=workspace, feature="alpha", stage="dev.specs", phase="final"
            )
            self.assertTrue(structure.ok, structure.errors)
            self.assertFalse(final.ok)
            self.assertIn(
                "missing_required_artifacts", [item["reason"] for item in final.errors or []]
            )

    def test_final_gate_needs_no_ledger_and_no_digest(self) -> None:
        """没有 critic 账本、没有摘要状态，产物齐全就该放行。"""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = self._feature(tmp)
            (feature_dir / "SPECS_REVIEW.md").write_text(REVIEW, encoding="utf-8")
            final = validate_stage(
                workspace=workspace, feature="alpha", stage="dev.specs", phase="final"
            )
            self.assertTrue(final.ok, final.errors)

    def test_editing_artifacts_after_review_does_not_reopen_the_gate(self) -> None:
        """critic 提出的问题由主模型修完收口，任意字节变化不再强制重跑 critic。"""
        with tempfile.TemporaryDirectory() as tmp:
            workspace, feature_dir = self._feature(tmp)
            append_run(feature_dir, RUN_ID, [])
            (feature_dir / "SPECS_REVIEW.md").write_text(REVIEW, encoding="utf-8")
            spec_path = feature_dir / "specs" / "order-export" / "spec.md"
            spec_path.write_text(
                SPEC.replace("创建导出任务", "创建异步导出任务"), encoding="utf-8"
            )
            final = validate_stage(
                workspace=workspace, feature="alpha", stage="dev.specs", phase="final"
            )
            self.assertTrue(final.ok, final.errors)

    def test_critic_prompt_and_writer_preserve_a_stable_finding_id(self) -> None:
        payload = {
            "tool_name": "task",
            "tool_input": {"subagent_type": "critic-autodev", "description": "review"},
        }
        updated = build_updated_input(payload, run_id=RUN_ID)
        self.assertIn(RUN_ID, updated["updatedInput"]["description"])

        response = dict(payload)
        response["tool_response"] = {
            "output": """done
```autodev-review-findings
{"reviewRunId":"RV-20260828T120000Z-deadbeef","findings":[{"id":"RV-20260828T120000Z-deadbeef-F001","severity":"Critical","claim":"分类缺证据","evidence":"proposal.md:20"}]}
```
"""
        }
        parsed_run, findings = parse_response(response)
        self.assertEqual(parsed_run, RUN_ID)
        self.assertEqual(findings[0]["findingId"], RUN_ID + "-F001")

    def test_findings_are_appended_as_a_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            finding = {
                "recordType": "finding",
                "reviewRunId": RUN_ID,
                "findingId": RUN_ID + "-F001",
                "severity": "Critical",
                "claim": "分类缺证据",
                "evidence": "proposal.md:20",
            }
            path, changed = append_run(feature_dir, RUN_ID, [finding])
            self.assertTrue(changed)
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(records[0]["recordType"], "review_run")
            self.assertEqual(records[1]["findingId"], RUN_ID + "-F001")

    def test_malformed_critic_output_does_not_block_the_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = self._feature(tmp)
            self._set_checkpoint(workspace, "specs_in_progress")
            result = self._run_hook(
                "critic_findings_writer.py",
                workspace,
                {
                    "tool_name": "task",
                    "tool_input": {"subagent_type": "critic-autodev", "description": "review"},
                    "tool_response": {"output": "没有按形状返回 block"},
                },
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("CRITIC_FINDINGS_SKIPPED", result.stdout)

    def test_decisions_are_recorded_from_a_real_tool_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            payload = {
                "tool_name": "request_user_input",
                "tool_use_id": "tool-1",
                "tool_input": {
                    "questions": [
                        {"id": "spec_001", "question": "范围？"},
                        {"id": "api_001", "question": "接口？"},
                    ]
                },
                "tool_response": {"answers": {"spec_001": "方案 A", "api_001": "方案 B"}},
            }
            records = records_from_payload(payload)
            path, changed = append_records(feature_dir, records)
            self.assertTrue(changed)
            written = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [item["decisionId"] for item in written], ["SPEC-001", "API-001"]
            )

    def test_unrecordable_decision_does_not_block_the_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _ = self._feature(tmp)
            self._set_checkpoint(workspace, "specs_in_progress")
            result = self._run_hook(
                "decision_ledger_writer.py",
                workspace,
                {
                    "tool_name": "request_user_input",
                    "tool_use_id": "tool-empty",
                    "tool_input": {"questions": [{"id": "spec_001", "question": "范围？"}]},
                    "tool_response": {},
                },
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("DECISIONS_SKIPPED", result.stdout)

    def test_runtime_guard_protects_the_two_surviving_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, feature_dir = self._feature(tmp)
            for name in ("REVIEW_FINDINGS.jsonl", "DECISIONS.jsonl"):
                self.assertTrue(protected_path(str(feature_dir / ".runtime" / name)), name)
            for name in ("SPEC_ID_ALLOCATIONS.json", "SPECS_REVIEW_STATE.json"):
                self.assertFalse(protected_path(str(feature_dir / ".runtime" / name)), name)

    def test_ledger_hooks_are_registered_but_never_declared_as_blockers(self) -> None:
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        post = [
            hook
            for registration in hooks["PostToolUse"]
            for hook in registration["hooks"]
        ]
        pre = [
            hook["command"]
            for registration in hooks["PreToolUse"]
            for hook in registration["hooks"]
        ]
        self.assertIn("python hooks/augment_critic_task_prompt.py", pre)
        for script in ("critic_findings_writer.py", "decision_ledger_writer.py"):
            entry = next(hook for hook in post if hook["command"].endswith(script))
            self.assertNotIn("onBlock", entry, script)

    def test_demoted_validators_are_off_the_dev_specs_gate(self) -> None:
        board = json.loads((ROOT / "board_core" / "board_config.json").read_text(encoding="utf-8"))
        specs = next(node for node in board["workflow"]["nodes"] if node["id"] == "dev.specs")
        for validator in (
            "specs_review_finding_ledger",
            "specs_decision_ledger",
            "specs_review_freshness",
        ):
            self.assertNotIn(validator, specs["validators"])


if __name__ == "__main__":
    unittest.main()
