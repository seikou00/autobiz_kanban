# AI Coding Harness 优化分析

## 背景

本文基于 `autobiz_kanban` 当前 harness 架构，并参考论文 [Code as Agent Harness, arXiv:2605.18747](https://arxiv.org/abs/2605.18747)，整理两类内容：

1. 论文提出了、但当前 harness 还做得不够好的地方。
2. 接下来建议优先推进的优化点。

当前 harness 已经具备比较完整的流程骨架：

- `board_core/board_config.json` 定义了 Biz、Dev、Ops 全流程 checkpoint、阶段流转、artifact 输入输出和 validators。
- `skills/autodev/autodev-code/SKILL.md` 已经要求基于 `proposal.md`、`specs/**/*.md`、`design.md`、`PLAN.md` 做逐任务实现和验证。
- `hooks/state_checkpoint.py` 已经能做 checkpoint 合法转移、生命周期检查和 hook 日志记录。
- `hooks/code_done_compile_guard.py` 已经在 `code_done` 前引入模块编译检查。

整体判断：当前架构不是缺流程，而是缺“机器可读的执行证据闭环”。很多关键事实仍停留在 Markdown 文档、agent 自述或零散 hook log 中，导致后续远程评测、知识沉淀、失败归因和自动演进都不够稳定。

## 1. 论文提出但当前 Harness 做得不够好的地方

### 1.1 Code 不只是产物，而应是 Agent 的运行底座

论文强调 Code 可以作为 agent 的 harness：承载状态、动作、环境反馈、验证结果和长期演进，而不只是最后生成的业务代码。

当前已有能力：

- 通过 checkpoint 管理需求、规格、计划、编码、评审、测试、验收、CI/CD。
- 通过 artifact contract 约束每个阶段的输入和输出。
- 通过 skill 文案约束 agent 在不同阶段的行为边界。

不足：

- 代码修改行为本身没有结构化记录。
- 每次探索、修改、验证、失败重试，没有统一的 evidence model。
- 业务代码 diff、规格引用、设计引用、验证命令之间没有稳定关联。

风险：

- 后续很难回答“这段代码是为了满足哪个 requirement 改的”。
- 远程评测只能看最终报告，不能可靠复盘完整执行链路。
- 采纳率、返工率、失败原因等指标无法和具体任务、规格、验证证据绑定。

建议方向：

把每一次关键执行动作都记录为 Evidence Bundle，让 code 修改从“最终结果”变成“可审计过程”。

### 1.2 Harness Interface 还不够机器可读

论文中的 harness interface 需要把 agent 的输入、可用动作、环境状态和反馈结果规范化。

当前已有能力：

- `board_config.json` 中已经有 workflow、checkpoints、artifacts、validators。
- `inspect_skill_contract.py` 可以输出某个 skill 的契约。
- `system_prompt_inject` 会注入 `FEATURE_ID`、`FEATURE_DIR`、`CODE_WORKSPACE` 等运行上下文。

不足：

- `PLAN.md` 是 Markdown，人可读，但机器解析不稳定。
- `VERIFY_REPORT.md`、`UNIT_TEST_REPORT.md`、`E2E_REPORT.md` 主要是文本报告，不是稳定的事件/结果协议。
- 每个阶段的“允许动作”和“禁止动作”主要写在 skill 文案里，缺少统一 runtime policy。

风险：

- agent 遵守程度依赖提示词，不依赖运行时强约束。
- Markdown 格式稍有漂移，hook 或后续分析就可能失效。
- 后续做远程评测时，需要大量 ad hoc 解析。

建议方向：

保留 Markdown 给人看，同时增加结构化文件：

- `plan.json`
- `EVIDENCE.jsonl`
- `FIX_REQUEST.json`
- `eval_bundle_manifest.json`

### 1.3 Planning 机制已有雏形，但缺少结构化 DAG

论文关注 agent 的 planning 能力，包括任务拆解、依赖关系、执行顺序、反馈后调整。

当前已有能力：

- `autodev-plan` 会生成 `design.md` 和 `PLAN.md`。
- `autodev-code` 会按 `PLAN.md` 选择下一个任务。
- 任务有“待做 / 进行中 / 完成 / 失败”状态。

不足：

- 任务 DAG 只存在于 Markdown。
- 任务 ID、依赖、规格引用、设计引用、验证命令、执行证据没有统一 schema。
- `code_done` 无法稳定验证“所有任务都完成且都有证据”。

风险：

- 任务状态可能被 agent 写错格式。
- 中断恢复时只能依赖文本扫描。
- 无法自动统计任务完成率、验证覆盖率、失败重试次数。

建议方向：

让 `/autodev-plan` 同步生成 `plan.json`，作为机器事实源；`PLAN.md` 作为人类视图。

### 1.4 Execution Feedback 不够闭环

论文强调执行反馈是 harness 的关键能力：agent 不只是生成代码，还要运行工具、观察反馈、修复问题。

当前已有能力：

- `autodev-code` 要求每个任务执行验证方法。
- `autodev-utest`、`autodev-e2e`、`autodev-verify` 分别承担单测、E2E、验收汇总。
- `code_done_compile_guard.py` 会读取 `.autobizdevops/modules_compile.json` 并执行编译。

不足：

- 验证命令、退出码、输出摘要没有统一进入结构化 evidence。
- 编译检查结果目前写 hook log，但没有和具体 task/spec/design 关联。
- `VERIFY_REPORT.md` 读上游报告，但证据来源仍偏文本化。

风险：

- 远程服务难以判断验证是否真实执行过。
- 失败归因容易依赖 agent 总结，缺少原始命令证据。
- 同一个失败反复出现时，无法稳定聚类。

建议方向：

所有验证动作统一记录：

- command
- cwd
- exitCode
- durationMs
- outputTailPath
- result
- relatedTaskId
- relatedSpecRefs
- relatedDesignRefs

### 1.5 Memory / Evolution 机制还没有形成闭环

论文里的 harness 不只是执行一次任务，还应能从执行历史中学习，让 agent、工具、prompt、规则持续演进。

当前已有能力：

- Feature 产物会被保留在 `.autobizdevops/features/{feature}`。
- hook log 会记录部分 checkpoint 和事件。
- Verify 阶段会形成验收报告。

不足：

- 没有从成功/失败 trace 中抽取可复用知识的流程。
- 没有区分“可沉淀知识”和“一次性上下文”。
- skill/prompt/validator 的优化没有和评测结果闭环。

风险：

- 成功经验无法复用。
- 失败模式无法系统性减少。
- 知识库如果直接沉淀 prompt/output，容易污染。

建议方向：

只沉淀经过验证的知识候选：

- 通过本地 verify。
- 通过远程评测。
- 有 evidenceId 支撑。
- 用户确认或规则确认可复用。

### 1.6 多 Agent 协作还停留在阶段分工，不是角色协作

论文提到多 agent 扩展：planner、coder、reviewer、tester、verifier 等角色可以围绕共享 harness 协作。

当前已有能力：

- 你已经把流程拆成 `autodev-specs`、`autodev-plan`、`autodev-code`、`autodev-reviewer`、`autodev-utest`、`autodev-e2e`、`autodev-verify`。
- 每个阶段都有自己的输入输出契约。

不足：

- 阶段之间是 artifact handoff，但不是结构化 role handoff。
- reviewer/tester/verifier 的发现没有统一进入 issue/evidence 模型。
- 多 agent 同时工作时，没有明确的 ownership、锁、冲突检测和合并策略。

风险：

- 多 agent 扩展后容易互相覆盖文件。
- reviewer 的发现可能只停留在报告里，无法驱动后续修复。
- tester 发现的问题无法精确绑定到 task/spec/design。

建议方向：

定义角色协议：

- Planner 产出 `plan.json`
- Coder 消费 `plan.json` 并写 `EVIDENCE.jsonl`
- Reviewer 产出 `REVIEW_FINDINGS.json`
- Tester 产出 `TEST_EVIDENCE.jsonl`
- Verifier 产出 `VERIFY_DECISION.json`

### 1.7 失败回流机制过粗

当前 `needs_fix` 是一个重要设计，但还比较粗。

当前已有能力：

- `board_config.json` 允许从 `needs_fix` 回到 `discuss_in_progress`、`prd_in_progress`、`specs_in_progress`、`plan_in_progress`、`code_in_progress`、`cicd_in_progress`。
- `autodev-verify` 会在失败时写报告并推进 `needs_fix`。

不足：

- 回流建议主要来自报告文本。
- 没有统一字段表达 root cause、source stage、failed specs、failed evidence。
- 根路由器无法稳定自动判断应该回到哪个阶段。

风险：

- 失败后用户需要人工读报告判断下一步。
- 同类失败无法统计。
- 远程评测返回的问题无法直接驱动本地状态机。

建议方向：

每次进入 `needs_fix` 必须生成 `FIX_REQUEST.json`。

### 1.8 远程离线评测缺少标准 Bundle

你之前定义的“离线评测”是：客户端提交完整 trace/artifacts 到远程服务，由远程完成评估。

当前已有能力：

- 本地有完整的阶段产物。
- 有部分 hook log。
- 有测试和验收报告。

不足：

- 没有统一 eval bundle schema。
- 没有 artifact hash、diff、trace、evidence、环境信息的统一 manifest。
- 本地没有远程 eval result 的回写位置。

风险：

- 每次远程评测都需要临时拼数据。
- 评测结果无法和本地 checkpoint、knowledge、采纳率指标打通。
- 远程评测服务很难稳定比较不同任务的质量。

建议方向：

新增 `eval_bundle.zip` 和 `EVAL_RESULT.json` 标准。

### 1.9 权限和安全边界不够运行时化

当前 skill 文案对写入边界描述很详细，但主要仍是提示词约束。

当前已有能力：

- `autodev-code` 明确禁止修改 PRD、proposal、specs、design 等上游产物。
- hook 会在写状态时检查 checkpoint 和 lifecycle。
- `check_plugin_read.py` 会做部分读路径检查。

不足：

- 每个阶段的读写权限没有作为配置化 runtime policy。
- 命令权限没有按阶段定义。
- 高风险动作没有统一审批模型。

风险：

- agent 可能越权修改阶段产物。
- verify 阶段可能重新执行测试或改代码，破坏“只汇总”的语义。
- ops 阶段可能提前执行危险命令。

建议方向：

在 `board_config.json` 的节点上增加 `runtimePolicy`。

## 2. 接下来的优化点

### 2.1 P0：引入 Evidence Bundle

目标：

把每个关键动作变成结构化证据，让 harness 能证明 agent 做了什么、为什么做、怎么验证。

建议路径：

```text
.autobizdevops/features/{feature}/evidence/
  EVIDENCE.jsonl
  ev_0001.log
  ev_0002.log
```

建议 schema：

```json
{
  "version": 1,
  "evidenceId": "ev_0001",
  "featureId": "feature-demo",
  "checkpoint": "code_in_progress",
  "nodeId": "dev.code",
  "skill": "autodev-code",
  "taskId": "T001",
  "action": "validation",
  "specRefs": ["specs/order/spec.md#REQ-001"],
  "designRefs": ["design.md#API-001"],
  "changedFiles": ["src/order/OrderService.java"],
  "validation": {
    "command": "mvn test -Dtest=OrderServiceTest",
    "cwd": "/absolute/path/to/module",
    "exitCode": 0,
    "result": "pass",
    "durationMs": 12034,
    "outputTailPath": "evidence/ev_0001.log"
  },
  "createdAt": "2026-06-11T10:00:00+08:00"
}
```

需要覆盖的事件类型：

- `task_started`
- `code_explored`
- `code_modified`
- `validation`
- `task_completed`
- `task_failed`
- `checkpoint_transition`
- `review_finding`
- `test_result`
- `verify_decision`

验收标准：

- 每个完成任务至少有一条 `validation.result = pass` 的 evidence。
- 每次 checkpoint 推进都有 `checkpoint_transition` evidence。
- 每条 validation evidence 都有 command、exitCode、result、outputTailPath。

### 2.2 P0：把 `PLAN.md` 拆出结构化 `plan.json`

目标：

减少 Markdown 解析不稳定，让任务 DAG、覆盖关系、验证命令可被 hook 和远程服务稳定消费。

建议路径：

```text
.autobizdevops/features/{feature}/PLAN.md
.autobizdevops/features/{feature}/plan.json
```

建议 schema：

```json
{
  "version": 1,
  "featureId": "feature-demo",
  "tasks": [
    {
      "id": "T001",
      "title": "实现订单状态校验",
      "status": "todo",
      "deps": [],
      "specRefs": ["specs/order/spec.md#REQ-001"],
      "designRefs": ["design.md#API-001"],
      "expectedFiles": [],
      "validationCommands": [
        {
          "command": "mvn test -Dtest=OrderServiceTest",
          "cwd": "/absolute/path/to/module"
        }
      ],
      "evidenceIds": [],
      "blockers": []
    }
  ]
}
```

状态枚举：

- `todo`
- `in_progress`
- `done`
- `failed`
- `blocked`

验收标准：

- `PLAN.md` 和 `plan.json` 表达一致。
- `autodev-code` 选择任务时以 `plan.json` 为准。
- hook 可以验证所有任务是否完成。

### 2.3 P0：强化 `code_done` Gate

目标：

让 `code_done` 从“agent 声称完成”变成“harness 可证明完成”。

建议 gate 条件：

- `plan.json` 存在且 schema 合法。
- 所有 task 状态为 `done`。
- 每个 task 至少有一个通过的 validation evidence。
- 每个 task 的 `specRefs` 和 `designRefs` 非空。
- 所有 validation evidence 都有 exitCode。
- `.autobizdevops/modules_compile.json` 存在。
- 编译检查通过，或失败被明确记录为 warning/blocker。
- 没有 unresolved blocker。

可落点：

- 扩展 `hooks/state_checkpoint.py` 的 lifecycle 校验。
- 或新增 `hooks/evidence_gate.py`，由 `state_checkpoint.py` 调用。

验收标准：

- 缺少 evidence 时不能推进 `code_done`。
- 有失败 task 时不能推进 `code_done`。
- evidence 指向不存在 task 时给出明确错误。

### 2.4 P0：结构化 `FIX_REQUEST.json`

目标：

让失败回流可自动路由，而不是依赖用户读报告。

建议路径：

```text
.autobizdevops/features/{feature}/FIX_REQUEST.json
```

建议 schema：

```json
{
  "version": 1,
  "featureId": "feature-demo",
  "sourceCheckpoint": "verify_in_progress",
  "sourceNodeId": "dev.verify",
  "suggestedCheckpoint": "code_in_progress",
  "rootCause": "implementation_bug",
  "failedSpecRefs": ["specs/order/spec.md#REQ-003"],
  "failedDesignRefs": ["design.md#D-002"],
  "failedEvidenceIds": ["ev_0017"],
  "blockingReason": "E2E assertion failed: expected status APPROVED",
  "humanActionRequired": false,
  "createdAt": "2026-06-11T10:10:00+08:00"
}
```

rootCause 建议枚举：

- `requirement_ambiguous`
- `spec_gap`
- `design_conflict`
- `implementation_bug`
- `test_bug`
- `environment_issue`
- `permission_issue`
- `dependency_issue`
- `unknown`

验收标准：

- 进入 `needs_fix` 必须存在 `FIX_REQUEST.json`。
- 根路由器读取 `FIX_REQUEST.json` 后能提示建议回流阶段。
- 远程评测返回失败时也能生成同样结构。

### 2.5 P1：增加 Runtime Policy

目标：

把阶段权限从提示词约束升级为配置和运行时校验。

建议在 `board_config.json` 每个 node 增加：

```json
{
  "runtimePolicy": {
    "readScopes": ["FEATURE_DIR", "CODE_WORKSPACE"],
    "writeScopes": ["FEATURE_DIR/PLAN.md", "FEATURE_DIR/plan.json", "CODE_WORKSPACE"],
    "forbiddenWrites": ["FEATURE_DIR/PRD.md", "FEATURE_DIR/proposal.md", "FEATURE_DIR/specs/**", "FEATURE_DIR/design.md"],
    "allowedCommands": ["mvn test", "mvn compile", "npm test", "pytest"],
    "requiresApproval": ["git push", "deploy", "db migrate"],
    "network": false
  }
}
```

不同阶段建议：

- Specs：允许写 `proposal.md`、`specs/**/*.md`，不允许改业务代码。
- Plan：允许写 `design.md`、`PLAN.md`、`plan.json`，不允许改业务代码。
- Code：允许改业务代码、测试、`PLAN.md`、`plan.json`、evidence，不允许改 specs/design。
- Verify：只允许写 `VERIFY_REPORT.md`、`VERIFY_DECISION.json`，不允许执行测试、不允许改代码。
- Ops：允许 CI/CD 相关产物，高风险命令需要审批。

验收标准：

- 阶段越权写入会被 hook 阻断。
- verify 阶段执行测试命令会被阻断或记录违规。
- 远程评测可以根据 runtimePolicy 判断 agent 是否越权。

### 2.6 P1：标准化远程 Eval Bundle

目标：

把本地完整 trace、artifact、diff、evidence 打包交给远程评测服务。

建议 bundle 内容：

```text
eval_bundle.zip
  manifest.json
  board_config.snapshot.json
  state.json
  proposal.md
  specs/**/*.md
  design.md
  PLAN.md
  plan.json
  evidence/EVIDENCE.jsonl
  evidence/*.log
  REQUIREMENTS_EVAL.md
  UNIT_TEST_REPORT.md
  test-output.log
  E2E_TEST_CASES.yaml
  E2E_REPORT.md
  e2e-run.log
  VERIFY_REPORT.md
  VERIFY_DECISION.json
  git.diff
  changed_files_manifest.json
```

manifest 建议字段：

```json
{
  "version": 1,
  "featureId": "feature-demo",
  "projectCode": "demo-project",
  "baseCommit": "abc123",
  "headCommit": "def456",
  "checkpoint": "verify_done",
  "artifactHashes": {},
  "evidenceCount": 42,
  "createdAt": "2026-06-11T10:20:00+08:00"
}
```

远程返回：

```json
{
  "evalId": "eval_0001",
  "score": 86,
  "verdict": "pass",
  "dimensions": {
    "specCoverage": 0.92,
    "verificationReliability": 0.8,
    "implementationMinimality": 0.75,
    "traceCompleteness": 0.95,
    "policyCompliance": 1.0
  },
  "issues": [],
  "knowledgeCandidates": []
}
```

验收标准：

- 任意 `verify_done` feature 都能生成 eval bundle。
- 远程返回结果能写入 `EVAL_RESULT.json`。
- eval result 能关联 evidenceId、taskId、specRef。

### 2.7 P1：把 Review / Test / Verify 结果结构化

目标：

让评审、单测、E2E、验收都能产生统一机器结果，而不是只生成 Markdown。

建议新增：

```text
REVIEW_FINDINGS.json
UNIT_TEST_RESULT.json
E2E_RESULT.json
VERIFY_DECISION.json
```

`REVIEW_FINDINGS.json` 示例：

```json
{
  "version": 1,
  "findings": [
    {
      "id": "R001",
      "severity": "high",
      "specRefs": ["specs/order/spec.md#REQ-001"],
      "file": "src/order/OrderService.java",
      "line": 120,
      "message": "Missing tenant boundary check",
      "suggestedCheckpoint": "code_in_progress"
    }
  ]
}
```

`VERIFY_DECISION.json` 示例：

```json
{
  "version": 1,
  "verdict": "pass",
  "passedSpecRefs": [],
  "failedSpecRefs": [],
  "manualVerificationRefs": [],
  "evidenceIds": [],
  "nextCheckpoint": "verify_done"
}
```

验收标准：

- Markdown 报告和 JSON 决策一致。
- `verify_done` / `needs_fix` 由 `VERIFY_DECISION.json` 驱动。
- reviewer/tester 的失败项能自动进入 `FIX_REQUEST.json`。

### 2.8 P2：知识沉淀闭环

目标：

把成功经验和失败教训沉淀为可复用知识，但避免污染。

不建议沉淀：

- 原始 prompt。
- 原始模型输出。
- 未验证方案。
- 临时绕过。
- 失败但未归因的内容。

建议沉淀：

```json
{
  "version": 1,
  "type": "engineering_pattern",
  "scope": "project",
  "trigger": "Spring service requires tenant boundary before repository query",
  "solution": "Use existing TenantContext and validate tenantId before querying repository",
  "evidenceIds": ["ev_0012", "ev_0013"],
  "validatedBy": ["verify_done", "remote_eval"],
  "confidence": 0.86,
  "createdAt": "2026-06-11T10:30:00+08:00"
}
```

沉淀条件：

- 本地 `verify_done`。
- 远程 eval verdict 为 pass。
- 有 evidence 支撑。
- 不涉及敏感数据。
- 用户确认或规则确认可复用。

验收标准：

- 每条知识都能追溯到 evidenceId。
- 失败经验和成功经验分开存储。
- skill/prompt 优化可以引用这些知识。

### 2.9 P2：多 Agent 角色协议

目标：

在现有阶段拆分基础上，进一步支持多 agent 协作。

建议角色：

- Planner：产出 `plan.json`。
- Coder：消费 `plan.json`，写业务代码和 `EVIDENCE.jsonl`。
- Reviewer：产出 `REVIEW_FINDINGS.json`。
- Tester：产出 `UNIT_TEST_RESULT.json`、`E2E_RESULT.json`。
- Verifier：产出 `VERIFY_DECISION.json`。
- Evolver：消费 eval result，提出 skill/harness 优化建议。

每个角色必须声明：

- 输入 artifact。
- 输出 artifact。
- 可写范围。
- 可执行命令。
- evidence 类型。
- handoff 条件。

验收标准：

- 多角色输出都能进入统一 evidence。
- reviewer/tester/verifier 发现的问题能生成统一 fix request。
- 并行 agent 有明确文件 ownership，避免互相覆盖。

## 推荐实施路线

### Phase 1：只增强观测，不改变主流程

目标：

先不改变 checkpoint 行为，只增加结构化产物。

落地内容：

- `plan.json`
- `EVIDENCE.jsonl`
- `FIX_REQUEST.json`
- `VERIFY_DECISION.json`

好处：

- 风险低。
- 可以快速积累真实 trace。
- 不影响现有 skill 使用习惯。

### Phase 2：把 Evidence 接入 Gate

目标：

让 `code_done`、`verify_done`、`needs_fix` 开始依赖结构化证据。

落地内容：

- `code_done` evidence gate。
- `verify_done` decision gate。
- `needs_fix` fix request gate。

好处：

- checkpoint 质量显著提升。
- 失败回流更准确。
- 远程评测数据更完整。

### Phase 3：接入远程离线评测

目标：

把完整 trace、artifact、diff、evidence 提交远程服务。

落地内容：

- `eval_bundle.zip`
- `manifest.json`
- `EVAL_RESULT.json`
- 远程评分维度和问题归因。

好处：

- 技能质量可以量化。
- agent 表现可以横向比较。
- 知识沉淀有可信依据。

### Phase 4：知识沉淀和 Harness 演进

目标：

让 harness 从执行系统升级为可演进系统。

落地内容：

- `knowledge_candidates.json`
- 用户确认机制。
- skill/prompt/validator 优化建议。
- 失败模式聚类。

好处：

- 成功经验复用。
- 高频失败逐步减少。
- harness 能根据真实任务持续优化。

## 优先级总结

| 优先级 | 优化项 | 价值 | 风险 |
|---|---|---|---|
| P0 | `EVIDENCE.jsonl` | 建立执行证据闭环 | 低 |
| P0 | `plan.json` | 稳定任务 DAG 和覆盖关系 | 中 |
| P0 | `FIX_REQUEST.json` | 失败自动回流 | 低 |
| P0 | `code_done` gate | 防止虚假完成 | 中 |
| P1 | runtime policy | 阶段权限治理 | 中 |
| P1 | eval bundle | 支撑远程离线评测 | 中 |
| P1 | review/test/verify JSON | 统一评测结果 | 中 |
| P2 | 知识沉淀 | 长期演进 | 中 |
| P2 | 多 Agent 角色协议 | 并行协作和扩展 | 高 |

## 最小可行闭环

建议先做一条最小闭环：

```text
autodev-plan 生成 plan.json
  ↓
autodev-code 按 task 写 EVIDENCE.jsonl
  ↓
code_done gate 校验 plan.json + evidence
  ↓
autodev-verify 读取 evidence 生成 VERIFY_DECISION.json
  ↓
打包 eval_bundle.zip 提交远程评测
  ↓
远程返回 EVAL_RESULT.json 和 knowledgeCandidates
```

这条链路跑通后，当前 harness 就会从“流程编排 + 文档产物”升级为真正的 AI coding harness：可执行、可验证、可审计、可评测、可沉淀、可演进。
