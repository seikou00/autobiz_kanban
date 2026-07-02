---
name: autodev-e2e
description: 对单个 feature 执行端到端测试。作为 Autodev 根流程中的正式阶段，承接 autodev-utest 的 UNIT_TEST_RESULT.json，输出 E2E_RESULT.json / E2E_TEST_CASES.yaml / e2e-run.log，并按 checkpoint 做 e2e_done / needs_fix 分支决策。默认由当前会话内联执行；可使用后台进程启动服务或运行长时间测试命令。
version: v1.1.1604
---

# autodev-e2e — E2E 阶段技能


<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（执行清单）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `${pluginPath}/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，取当前 Feature 的执行清单：

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-e2e --feature "${feature}" --plain
```

- **逐条执行**：`## 输入产物` 下每个 input 只有一行确定指令，按序执行即可，不需要自己判断产物是否存在或该走哪个分支。
- **已生成**：按其 `读取方式` 读原件并纳入上下文；`读取方式` 是该 input 在场时的专属指令，优先于技能正文的通用默认。
- **未生成**：按其 `缺失处理` 执行——必需 input 停止并回流上游补齐；可选 input 按其降级动作继续，不因缺失而停止。
- **不列即不存在**：清单未列出的 id 不属于本 workflow 的正式流程产物 input，不要把它当作上游阶段产物读取、等待或索要。
- **适用边界**：上一条只约束正式流程产物 input；不限制用户本轮直接提供的材料、代码工作区上下文、AGENTS.md、内部 route SKILL/deps 或技能正文明确要求读取的辅助素材。
- **输出与校验**：`## 输出产物` 是本节点应产出的产物；`## Validators`/`## Guards` 是推进 checkpoint 的校验项。

无 `FEATURE_ID` 时可省略 `--feature` 查看基线清单（此时按 `读取方式` 预览，不含产物状态）。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


## State 快照读取

确定 `{slug}` 后，第一步调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续准入、恢复和分支决策直接取用 `CHECKPOINT`。若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。

## 输入与行为依据

消费执行清单：按「流程契约」一节取本 Feature 的执行清单，读取 `## 输入产物` 列出的产物原件，按各自 `读取方式` 抽取重点；标『未生成』的可选 input 按其 `缺失处理`（降级）继续，清单未列出的产物不读不等。清单未列出的上游产物（其所属阶段被跳过或不在本工作流链）在可选人类报告中统一注明「不在本工作流产物清单」，而非「缺失」。

各输入的用途以其 `读取方式` 为准；行为契约（specs 的 Requirement / Scenario）是 E2E pass/fail 的主要行为依据。

禁止写入：

- 不要修改执行清单列出的任何 input（凡在清单中即只读）。
- 不要为通过 E2E 而弱化断言、删除用例、伪造报告。

每轮 E2E 必须优先以 specs 中属于用户主链路的 Requirement / Scenario 生成结构化测试用例；相关 API Decision 或 Data Decision 只作为执行和断言上下文。涉及页面、按钮、点击、弹窗、跳转、表单、前端组件、路由、用户可见流程的 P0/P1 用例必须标记 `ui_required: true`。

E2E 用例的稳定 ID 规则：

- 用例 `id` 统一使用 `E2E-{slug}-001`、`E2E-{slug}-002` ...
- `source.specs_contract` 必须优先引用稳定 ID，例如 `specs/<capability>/spec.md#REQ-001` / `#SCN-001`
- `E2E_RESULT.json` 中的失败项与回流说明必须回链到相同的 `REQ-001` / `SCN-001`

每次 E2E 命令或人工驱动执行结束后，必须把运行结果追加到 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/evidence/EVIDENCE.jsonl` 末尾：使用 `hooks/evidence_store.py append` 写入 taskId（优先来自 `plan.json`）、specRefs、designRefs、changedFiles、validation.command/exitCode/result，并把运行日志尾部作为 evidence tail 保存。`ev_XXXX` 按全流顺序自动递增，不按阶段重排；`E2E_RESULT.json` 的每个用例结论都必须引用对应 `ev_XXXX`。不得插入旧记录前、重编号、截断、重写、删除 `EVIDENCE.index.json` 后重建或手动修改 `EVIDENCE.index.json`。若 append 或 checkpoint 报 `evidence_stream_rewritten_or_truncated` / `missing_evidence_index_for_nonempty_stream`，必须恢复被改写前的 `EVIDENCE.jsonl` / `EVIDENCE.index.json`，无法恢复时停止并向用户报告。

同时必须写入 `E2E_RESULT.json` 作为机器事实源。JSON 只承载结构化结论，不和 Markdown 做文本对账；每个 case 必须用 `specRefs` 回链 Requirement / Scenario，并引用对应 `evidenceIds`。`scenarioCoverage` 必须以 specs 中全部 `SCN-xxx` 为分母，逐行写出 `pass` / `fail` / `manual` / `missing`；`pass` 行必须引用能通过 `specRefs` 覆盖该场景的 evidence。

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
  "createdAt": "2026-06-24T00:00:00Z"
}
```

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint e2e_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint needs_fix
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

**Skill 完成。** 推进 `e2e_done` 后下一步以 `resolve_next_skill.py` 为准（不假设固定下一技能）：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py"
```
