---
name: autodev-e2e
description: 对单个 feature 执行端到端测试。作为 Autodev 根流程中的正式阶段，承接 autodev-utest 的 UNIT_TEST_RESULT.json，输出 E2E_RESULT.json / E2E_TEST_CASES.yaml / e2e-run.log，并按 checkpoint 做 e2e_done / needs_fix 分支决策。默认由当前会话内联执行；可使用后台进程启动服务或运行长时间测试命令。
version: v1.1.1604
---

# autodev-e2e — E2E 阶段技能


## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-e2e --feature "${feature}" --plain
```


## State 快照读取

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入、恢复和分支决策直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 输入与行为依据

消费执行清单：按「流程契约」一节取本 Feature 的执行清单，读取 `## 输入产物` 列出的产物原件，按各自 `读取方式` 抽取重点；标『未生成』的可选 input 按其 `缺失处理`（降级）继续，清单未列出的产物不读不等。清单未列出的上游产物（其所属阶段被跳过或不在本工作流链）在可选人类报告中统一注明「不在本工作流产物清单」，而非「缺失」。

各输入的用途以其 `读取方式` 为准；行为契约（specs 的 Requirement / Scenario）是 E2E pass/fail 的主要行为依据。

读取 `plan.json.deferredValidationIssues[]` 及对应 batch plan 明细。Code 延期项不是 PASS：能映射到用户主链路的 `scope=task` 问题必须进入 P0/P1 E2E 用例；`scope=batch/project` 的集成、环境问题必须在服务启动/扩大验证时复核。用例或报告记录原 `issueId` 与 `evidenceIds`，通过新鲜 E2E evidence 证明已解决；无法在本阶段复核时保留为 manual/missing，不得静默丢弃。

禁止写入：

- 不要修改执行清单列出的任何 input（凡在清单中即只读）。
- 不要为通过 E2E 而弱化断言、删除用例、伪造报告。

每轮 E2E 必须优先以 specs 中属于用户主链路的 Requirement / Scenario 生成结构化测试用例；相关 API Decision 或 Data Decision 只作为执行和断言上下文。涉及页面、按钮、点击、弹窗、跳转、表单、前端组件、路由、用户可见流程的 P0/P1 用例必须标记 `ui_required: true`。

E2E 用例的稳定 ID 规则：

- 用例 `id` 统一使用 `E2E-{slug}-001`、`E2E-{slug}-002` ...
- `source.specs_contract` 必须优先引用稳定 ID，例如 `specs/<capability>/spec.md#REQ-001` / `#SCN-001`
- `E2E_RESULT.json` 中的失败项与回流说明必须回链到相同的 `REQ-001` / `SCN-001`

每次 E2E 命令或人工驱动执行结束后，必须把运行结果追加到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/evidence/EVIDENCE.jsonl` 末尾；这是 feature 产物目录下的证据流，不得写到业务代码仓库根目录或当前 cwd 下的临时 `.autobizdevops`。使用 `hooks/evidence_store.py append` 写入 taskId（优先来自 `plan.json`）、specRefs、designRefs、changedFiles、validation.command/exitCode/result，并把运行日志尾部作为 evidence tail 保存；append 工具会默认从 `PLUGIN_WORKSPACE/PROJECT_DIR` 定位产物根，手写命令时可显式加 `--workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"`。`ev_XXXX` 按全流顺序自动递增，不按阶段重排；`E2E_RESULT.json` 的每个用例结论都必须引用对应 `ev_XXXX`。不得插入旧记录前、重编号、截断、重写、删除 `EVIDENCE.index.json` 后重建或手动修改 `EVIDENCE.index.json`。若 append 或 checkpoint 报 `evidence_stream_rewritten_or_truncated` / `missing_evidence_index_for_nonempty_stream`，必须恢复被改写前的 `EVIDENCE.jsonl` / `EVIDENCE.index.json`，无法恢复时停止并向用户报告。

同时必须通过 `${pluginPath}/hooks/e2e_result_writer.py` 写入 `E2E_RESULT.json` 作为机器事实源，禁止直接整份写入或编辑该 JSON。JSON 只承载结构化结论，不和 Markdown 做文本对账；每个 case 必须用 `specRefs` 回链 Requirement / Scenario，并引用对应 `evidenceIds`。若 case 指向 `UI_CONTEXT.json` 中的 UI task 或 UI scenario，必须投影 `uiRequired=true`、`pageRefs`、`interactionRefs`、`visualSourceRefs`；非 UI case 不要伪造 UI refs。`scenarioCoverage` 必须以 specs 中全部 `SCN-xxx` 为分母，逐行写出 `pass` / `fail` / `manual` / `missing`；`pass` 行必须引用能通过 `specRefs` 覆盖该场景的 evidence。优先使用 `e2e_result_writer.py init`、`add-case/update-case`、`derive-scenario-coverage` 与 `set-verdict`；caseId 由 writer 生成 `E2E-{feature}-001` 形式。

推进 `e2e_done` 或 `needs_fix` 前必须运行 `${pluginPath}/hooks/stage_gate.py validate --stage dev.e2e --feature "${feature}"`。writer 的本地 `validate` 只做结构检查，不能替代 stage gate。

```json
{
  "version": 1,
  "verdict": "PASS",
  "scenarioCoverage": [
    {"scenarioRef": "SCN-001", "evidenceIds": ["ev_0001"], "verdict": "pass"}
  ],
  "cases": [
    {
      "caseId": "E2E-alpha-001",
      "taskId": "T001",
      "specRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
      "evidenceIds": ["ev_0001"],
      "uiRequired": true,
      "pageRefs": ["PAGE-001"],
      "interactionRefs": ["UIX-001"],
      "visualSourceRefs": ["VIS-001"],
      "executionMode": "manual",
      "steps": [{"action": "open", "expected": "visible", "result": "PASS"}],
      "verdict": "PASS"
    }
  ]
}
```

## Checkpoint 写入

开始 E2E 前推进到 `e2e_in_progress`，写入后立即刷新 `CHECKPOINT`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

E2E 通过后推进到 `e2e_done`；若存在明确失败并需要回流，必须先写 `FIX_REQUEST.json`，再推进到 `needs_fix`。每次写入后都必须刷新 `CHECKPOINT`：

```json
{
  "version": 1,
  "featureId": "alpha",
  "sourceCheckpoint": "e2e_in_progress",
  "sourceNodeId": "dev.e2e",
  "suggestedCheckpoint": "code_in_progress",
  "rootCause": "implementation_bug",
  "blockingReason": "E2E case failed",
  "humanActionRequired": false,
  "failedSpecRefs": ["specs/cap/spec.md#REQ-001", "specs/cap/spec.md#SCN-001"],
  "failedEvidenceIds": ["ev_0001"],
  "failedDesignRefs": [],
  "failedUiRefs": {
    "pageRefs": ["PAGE-001"],
    "interactionRefs": ["UIX-001"],
    "visualSourceRefs": ["VIS-001"]
  },
  "createdAt": "2026-06-24T00:00:00Z"
}
```

若失败用例不是 UI 用例，`failedUiRefs` 可省略；若失败指向 UI 页面、交互或视觉输入，必须引用 `UI_CONTEXT.json` 中真实存在的 ID。

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.e2e --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.e2e --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。**
