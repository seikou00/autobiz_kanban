# AI Coding Harness 优化实现文档

> 本文是 `docs/ai-coding-harness-optimization.md` 的实施版，目标是把 13 个优化点拆成可执行的改动、文件落点和验收条件。  
> 原则上保持 Markdown 给人看，增加 JSON sidecar 给机器读；现有工作流不推倒重来，只补结构化事实源和校验链。

## 0. 当前基线

现状可以先记住四件事：

- `board_core/board_config.json` 的顶层只放 `apiVersion / inspectCommands / workflow / checkpointSuffixState`；`checkpoints / artifacts / validators` 主要在 `workflow` 子树和各节点里。
- `autodev-plan` 目前只产出 `design.md` 和 `PLAN.md`。
- `autodev-code` 当前主要依赖 `proposal.md`、`specs/**/*.md`、`design.md`、`PLAN.md`。
- `autodev-verify` 目前只读 Markdown 报告，不读结构化决策文件。
- `detail_design_before_code` 是真实存在的动态阶段，`autodev-detail-design` 也是已存在的 skill，不是概念草案。

另外，仓库里还存在一个语义分歧：

- `board_core/board_config.json`、`skills/autodev/SKILL.md`、`skills/autodev/autodev-frontend/`、`tests/test_dynamic_workflow.py`、`tests/test_workflow_skip.py`、`tests/test_workflow_subset.py`、`projects.json` 都还在使用 `frontend_before_specs`。
- `docs/inspect-json-dynamic-workflow.md` 里写成“旧的 `frontend_before_specs` 已移除，HTML 并入 `dev.code` 的可选输入”，这一点和代码不一致。

因此这个实现文档不能把 frontend 当成“已删干净”的完成态。正确做法是：

1. 先把文档、测试、状态迁移计划和契约说清楚。
2. 再决定是否真的收口到 `dev.code` 可选输入。
3. 在迁移完成前，必须把 `frontend_before_specs` 当作活跃兼容路线处理，不能直接假设删除。

## 1. 实施原则

1. Markdown 继续做人类视图，JSON sidecar 作为机器事实源。
2. 所有跨阶段事实必须有稳定 ID。
3. 所有验证必须有证据落盘，且是 append-only。
4. `board_core/board_config.json` 仍然是契约源，新增文件必须纳入契约。
5. 不新增“靠提示词自觉”的隐式协议，能配置的就配置，能校验的就校验。

## 2. 统一文件族

建议先把这些文件族定下来，再做后续功能：

| 类别 | 文件 |
|---|---|
| 行为契约 | `proposal.md`, `specs/**/*.md` |
| 计划契约 | `design.md`, `PLAN.md`, `plan.json` |
| 执行证据 | `evidence/EVIDENCE.jsonl`, `evidence/*.log` |
| 评审结果 | `completion-proposal.json`, `REQUIREMENTS_EVAL.md`, `REVIEW_FINDINGS.json` |
| 测试结果 | `UNIT_TEST_REPORT.md`, `UNIT_TEST_RESULT.json`, `test-output.log` |
| E2E 结果 | `E2E_TEST_CASES.yaml`, `E2E_REPORT.md`, `E2E_RESULT.json`, `e2e-run.log` |
| 验收决策 | `VERIFY_REPORT.md`, `VERIFY_DECISION.json` |
| 回流请求 | `FIX_REQUEST.json` |
| 远程评测 | `eval_bundle.zip`, `manifest.json`, `EVAL_RESULT.json` |
| 知识沉淀 | `knowledge_candidates.json` |

## 3. 分阶段实施

### Phase 1: 先把结构化事实源补齐

目标是先让 `plan / evidence / verify` 可以机器读取，不改主流程行为。

落地项：

- 稳定 ID
- `plan.json`
- `EVIDENCE.jsonl`
- Scenario 覆盖矩阵
- `REVIEW_FINDINGS.json`
- `UNIT_TEST_RESULT.json`
- `E2E_RESULT.json`
- `VERIFY_DECISION.json`
- `FIX_REQUEST.json`

这一阶段的验收标准只有一个：**Markdown 还在，但机器已经不再只靠 Markdown 活着。**

### Phase 2: 再把 gate 接上

目标是让 `code_done`、`needs_fix`、`verify_done` 变成可证明、可路由的状态。

落地项：

- `code_done` gate
- `verify_done` / `needs_fix` 由 `VERIFY_DECISION.json` 驱动
- `needs_fix` 必须生成 `FIX_REQUEST.json`
- runtime policy

这一阶段的验收标准是：**没有结构化证据，不允许“声称完成”。**

### Phase 3: 接远程评测

目标是让远程系统直接消费标准 bundle，而不是重新推理 Markdown 上下文。

落地项：

- `eval_bundle.zip`
- `manifest.json`
- `EVAL_RESULT.json`
- `knowledge_candidates.json`

这一阶段的验收标准是：**远程评测能回写到本地事实源，并绑定到 evidence / task / spec。**

### Phase 4: 收口语义和文档

目标是把 frontend 语义、skill 路径、docs 说明统一起来，避免实现和文档继续分叉。

落地项：

- frontend 融合语义
- skill 执行路径优化
- docs 同步

这一阶段的验收标准是：**workflow 语义、技能文档和实现配置说同一件事。**

## 4. 逐点实施清单

### 1) 稳定 ID

#### 要改什么

- 在 `specs/**/*.md` 里给每个 Requirement / Scenario 加稳定编号。
- 在 `PLAN.md` / `plan.json` 里给每个任务加 `taskId`。
- 在 `EVIDENCE.jsonl` 里给每条证据加 `evidenceId`。
- 在 `VERIFY_DECISION.json` / `EVAL_RESULT.json` 里保留对 `taskId / specRef / evidenceId` 的引用。

#### 文件落点

- `skills/autodev/autodev-specs/templates/spec.md`
- `skills/autodev/autodev-plan/templates/plan.md`
- `skills/autodev/autodev-plan/templates/design.md`
- `skills/autodev/autodev-reviewer/references/schemas.md`
- `skills/autodev/autodev-utest/SKILL.md`
- `skills/autodev/autodev-e2e/SKILL.md`
- `skills/autodev/autodev-verify/SKILL.md`

#### 具体做法

1. 统一使用“文件路径 + 本地 ID”的引用方式，例如 `specs/order/spec.md#REQ-001`。
2. 约定本地 ID 的前缀：
   - Requirement: `REQ-001`
   - Scenario: `SCN-001`
   - Task: `T001`
   - Evidence: `ev_0001`
   - Eval: `eval_0001`
3. 在模板里写明“新建内容必须继续递增，不允许复用旧 ID”。
4. 在 review / test / verify 的报告模板里把 ID 作为表格主键。

#### 验收标准

- 任意一条验证结论都能回指到具体 Requirement / Scenario。
- 任意一条证据都能回指到具体任务和代码修改。

---

### 2) `plan.json`

#### 要改什么

- `PLAN.md` 继续保留给人看。
- 新增 `plan.json` 作为任务 DAG 的机器事实源。
- `autodev-code` 优先消费 `plan.json`，而不是只扫 Markdown。

#### 文件落点

- `board_core/board_config.json`
- `skills/autodev/autodev-plan/SKILL.md`
- `skills/autodev/autodev-plan/templates/plan.md`
- `hooks/plan_json.py`（新）

#### 建议 schema

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
      "validationCommands": [
        { "command": "mvn test -Dtest=OrderServiceTest", "cwd": "/abs/path" }
      ],
      "expectedFiles": ["src/order/OrderService.java"],
      "evidenceIds": [],
      "blockers": []
    }
  ]
}
```

#### 具体做法

1. `autodev-plan` 在生成 `PLAN.md` 时同步生成 `plan.json`。
2. `board_core/board_config.json` 里把 `plan.json` 加入 plan/code/review/test/e2e/verify 的输入或输出契约。
3. `autodev-code` 读取 `plan.json` 作为任务队列。
4. `plan.json` 里的任务状态成为 `PLAN.md` 的唯一机器同步目标。
5. `plan.md` 模板增加“机器视图和人类视图一致”的说明。

#### 验收标准

- `PLAN.md` 和 `plan.json` 一致。
- `autodev-code` 可以不依赖自由文本解析就获得任务 DAG。

---

### 3) `EVIDENCE.jsonl`

#### 要改什么

- 所有关键动作写入 append-only 证据流。
- 证据覆盖 code / test / e2e / verify / checkpoint transition。

#### 文件落点

- `hooks/evidence_store.py`（新）
- `hooks/evidence_integrity_gate.py`（新）
- `skills/autodev/autodev-code/SKILL.md`
- `skills/autodev/autodev-utest/SKILL.md`
- `skills/autodev/autodev-e2e/SKILL.md`
- `skills/autodev/autodev-verify/SKILL.md`
- `board_core/board_config.json`
- `.gitignore`

#### 建议 schema

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
    "cwd": "/abs/path",
    "exitCode": 0,
    "result": "pass",
    "durationMs": 12034,
    "outputTailPath": "evidence/ev_0001.log"
  },
  "createdAt": "2026-06-11T10:00:00+08:00"
}
```

#### 具体做法

1. 在 `autodev-code` 里，每完成一次任务、一次验证、一次失败回流，都追加一条 evidence。
2. `autodev-utest` 和 `autodev-e2e` 只要跑测试命令，就把命令和结果写成 evidence。
3. `autodev-verify` 只做汇总，但要把结论引用到 evidenceId。
4. `evidence/*.log` 只保留原始输出尾部或完整输出，不把摘要当原始证据。
5. 因为根目录 `.gitignore` 当前忽略 `*.log`，如果继续使用 `evidence/*.log`，就必须给这个目录加白名单例外；否则改用 `.txt` / `.ndjson` 等不会被忽略的后缀。这里建议保留 `.log` 命名并加窄例外，减少和文档、bundle 语义的偏差。
6. `EVIDENCE.jsonl` 必须由追加写入实现，`evidence_integrity_gate.py` 要能拒绝截断、重写、重排或回滚式写法。

#### 验收标准

- 每个完成任务至少有一条 `validation.result = pass` 的 evidence。
- 失败和修复尝试都能从 evidence 追溯。

---

### 4) 细分场景覆盖

#### 要改什么

- 让 `spec` 中的 Scenario 成为最小验收单元。
- 让 `PLAN.md` / `UNIT_TEST_REPORT.md` / `E2E_TEST_CASES.yaml` 都能映射到 Scenario。
- 把 UI 类和数据类场景分开标记。

#### 文件落点

- `skills/autodev/autodev-specs/templates/spec.md`
- `skills/autodev/autodev-plan/templates/plan.md`
- `skills/autodev/autodev-utest/SKILL.md`
- `skills/autodev/autodev-e2e/SKILL.md`
- `skills/autodev/autodev-verify/SKILL.md`

#### 具体做法

1. Requirement 下面必须有可枚举 Scenario。
2. `autodev-plan` 的覆盖矩阵里，任务必须明确覆盖哪些 Scenario。
3. `autodev-utest` 的 Coverage Matrix 以 Scenario 为主映射单位。
4. `autodev-e2e` 对涉及页面、按钮、表单、跳转、弹窗的 P0/P1 场景打 `ui_required: true`。
5. `autodev-verify` 不再只看“测试通过”，而是检查“场景是否被覆盖”。

#### 验收标准

- 能判断“某个场景没覆盖”，而不是只知道“测试总数通过”。
 - 这个矩阵必须在 Sprint 1 就进计划，否则后面的 `plan.json`、`unit_test_report`、`verify` 会没有统一锚点。

---

### 5) `code_done` Gate

#### 要改什么

- `code_done` 不再只看模块编译通过。
- 必须检查 `plan.json`、`EVIDENCE.jsonl`、任务状态、blocker、验证结果。

#### 文件落点

- `hooks/state_checkpoint.py`
- `hooks/code_done_compile_guard.py`
- `hooks/code_done_gate.py`（新）
- `hooks/traceability_validator.py`（新）
- `board_core/board_config.json`
- `hooks/hooks.json`

#### 具体做法

1. 把“硬门禁”放在 `hooks/hooks.json` 的 `execute` matcher，`code_done_gate.py` 负责阻断 `update_checkpoint.py --checkpoint code_done`。
2. 把“静态契约校验”放在 `board_core/board_config.json` 的 validators，专门检查 artifact schema、引用完整性和产物格式。
3. 新增 `traceability_validator`，检查：
   - `plan.json` schema 正常
   - 所有任务已完成
   - 每个任务至少一个通过的 validation evidence
   - 没有 unresolved blocker
   - evidence 引用的 task/spec/design 都存在
4. `code_done_compile_guard.py` 保留编译检查，但它只是一部分，不再承担全部门禁语义。
5. `state_checkpoint.py` 在推进 `code_done` 前先跑 traceability gate，再跑编译子项。
6. 实现上不要只靠一个 validator 或只靠一个 hook，必须两层都在：validator 负责静态契约，hook 负责状态转移阻断。

#### 验收标准

- 缺 plan、缺 evidence、缺 blocker 说明时，不能进入 `code_done`。

---

### 6) Review / Test / Verify JSON

#### 要改什么

- 所有 Markdown 报告保留。
- 同时新增结构化 JSON 结果文件，作为机器读取主入口。

#### 文件落点

- `skills/autodev/autodev-reviewer/references/schemas.md`
- `skills/autodev/autodev-reviewer/SKILL.md`
- `skills/autodev/autodev-utest/SKILL.md`
- `skills/autodev/autodev-e2e/SKILL.md`
- `skills/autodev/autodev-verify/SKILL.md`

#### 建议文件

- `REVIEW_FINDINGS.json`
- `UNIT_TEST_RESULT.json`
- `E2E_RESULT.json`
- `VERIFY_DECISION.json`

#### 具体做法

1. `REVIEW_FINDINGS.json` 只放结构化发现项，Markdown 仍保留完整论证。
2. `UNIT_TEST_RESULT.json` 记录测试方法、结果、覆盖率、失败归因。
3. `E2E_RESULT.json` 记录场景、步骤、证据和 verdict。
4. `VERIFY_DECISION.json` 只写最终 verdict、通过/失败的 specRefs、下一步 checkpoint。
5. `autodev-verify` 以后优先读 JSON 决策，再回看 Markdown 证据。

#### 验收标准

- 下游不需要从 Markdown 推理就能知道测试与验收结论。

---

### 7) `FIX_REQUEST.json`

#### 要改什么

- `needs_fix` 必须结构化。
- 回流阶段不再靠读自然语言报告猜 checkpoint。

#### 文件落点

- `hooks/fix_request.py`（新）
- `hooks/route_checkpoint.py`
- `skills/autodev/SKILL.md`
- `skills/autodev/autodev-verify/SKILL.md`

#### 建议 schema

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

#### 具体做法

1. `autodev-verify` 在失败时写 `FIX_REQUEST.json`。
2. `route_checkpoint.py` 读取 `FIX_REQUEST.json` 给出建议回流阶段。
3. `skills/autodev/SKILL.md` 里把 `needs_fix` 的处理逻辑改成“先读 FIX_REQUEST，再路由”。
4. 如果没有结构化 fix request，就不自动假设回流目标。

#### 验收标准

- `needs_fix` 可以直接机器路由，不依赖人工脑补。

---

### 8) 远程 Eval Bundle

#### 要改什么

- `verify_done` 后可以标准打包。
- 远程评测直接消费 bundle，不再临时拼接 Markdown。

#### 文件落点

- `hooks/eval_bundle.py`（新）
- `hooks/import_eval_result.py`（新）
- `skills/autodev/autodev-verify/SKILL.md`
- `board_core/board_config.json`

#### 建议 bundle 内容

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
  UNIT_TEST_REPORT.md
  E2E_REPORT.md
  VERIFY_REPORT.md
  VERIFY_DECISION.json
  git.diff
  changed_files_manifest.json
```

#### 具体做法

1. 远程评测包里必须带 `artifactHashes` 和 `baseCommit/headCommit`。
2. `manifest.json` 作为入口索引，不把 zip 里所有文件都靠脚本猜。
3. `eval_bundle.zip` 的构建是本地行为，不需要网络。
4. 远程返回 `EVAL_RESULT.json` 的导入应单独放在一个明确的导入入口里，不要塞回 `dev.verify`；如果后续需要联网上传，应该落到独立的 Ops 命令或专门的上传节点，而不是把 `network:true` 直接写死到所有 dev 节点。

#### 验收标准

- 任意 `verify_done` feature 都能导出标准 bundle。

---

### 9) `EVAL_RESULT.json` 回写 + 知识候选

#### 要改什么

- 远程评测结果要回写本地。
- 知识沉淀必须有证据链。

#### 文件落点

- `hooks/import_eval_result.py`
- `skills/autodev/autodev-verify/SKILL.md`
- `skills/autodev/autodev/SKILL.md`
- `knowledge_candidates.json`（新）

#### 具体做法

1. `EVAL_RESULT.json` 里要能绑定 `evidenceId / taskId / specRef`。
2. 只有本地 `verify_done` 且远程 verdict `pass`，才生成知识候选。
3. 知识候选必须分开记录成功经验和失败模式。
4. 不能直接把原始 prompt 或未验证输出沉淀为知识。
5. 知识候选与失败模式要和 `FIX_REQUEST.json` 互相引用，避免同一问题在不同入口各写一份。

#### 验收标准

- 知识沉淀可以追溯到验证证据，不会污染仓库知识库。

---

### 10) Runtime Policy

#### 要改什么

- 阶段权限从提示词约束升级成运行时校验。
- 每个节点明确读写范围、命令范围、网络边界。

#### 文件落点

- `board_core/board_config.json`
- `board_core/workflow_compiler.py`
- `board_core/contracts.py`
- `hooks/state_checkpoint.py`
- `hooks/check_plugin_read.py`
- `hooks/runtime_policy.py`（新）

#### 建议字段

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

#### 具体做法

1. `board_config.json` 每个 node 都补 `runtimePolicy`，而且是按节点定制，不是全局一份通吃。
2. `workflow_compiler.py` 编译有效 workflow 时也编译 policy。
3. `state_checkpoint.py` 和 read/write hooks 在执行时校验 policy。
4. `verify` 阶段默认 `network:false`，只允许写本地报告和决策文件；如果以后要做远程上传/导入，那应由独立 Ops 命令或专门的 export/import 节点承接，不要混进 `dev.verify`。
5. `code`、`test`、`e2e` 节点的 policy 可以继续默认 `network:false`，但 `verify` 输出 bundle 的动作和网络上传动作要拆开。

#### 验收标准

- 越权写入和越权命令能被运行时阻断。
- 网络权限是按节点配置的，不会和远程评测的导入动作互相打架。

---

### 11) 前端融合演进

#### 要改什么

- 统一 frontend 语义。
- 不再保留两套互相冲突的主路线。

#### 文件落点

- `board_core/board_config.json`
- `skills/autodev/SKILL.md`
- `skills/autodev/autodev-code/SKILL.md`
- `skills/autodev/autodev-specs/SKILL.md`
- `skills/autodev/autodev-plan/SKILL.md`
- `skills/autodev/autodev-verify/SKILL.md`
- `docs/inspect-json-dynamic-workflow.md`

#### 具体做法

1. 不要把 `frontend_before_specs` 直接当成“已删除”。当前仓库里它还在 `projects.json`、测试和 skill 文档里活着，先做兼容和迁移，不要先删。
2. 先列出 frontend 相关的真实依赖面：`projects.json`、`tests/test_dynamic_workflow.py`、`tests/test_workflow_skip.py`、`tests/test_workflow_subset.py`、`skills/autodev/autodev-frontend/`、`skills/autodev/SKILL.md`、`docs/ui-plugin-verify-quickstart.md`。
3. 如果最终目标是收口到 `dev.code` 的前端可选输入，必须先写清楚 state 迁移、测试更新和文档同步计划，再动 workflow 语义。
4. 在迁移完成前，`frontend_before_specs` 继续视为活跃兼容路线；如果只是做语义补全，就把文档改成与代码一致，而不是反过来。
5. `detail_design_before_code` 要和 frontend 同样按真实 workflow 处理，任务、证据和 design refs 里都要保留 `DETAIL_DESIGN.md` 的引用位置。

#### 验收标准

- `board_config`、技能文档、动态工作流文档和 `projects.json` 对 frontend 的说法一致。
- 迁移前不删除活跃路线，迁移后再统一收口。

---

### 12) 技能执行路径优化

#### 要改什么

- skill 在执行前必须先读取运行时契约。
- 不再依赖静态文件名作为准入依据。

#### 文件落点

- `hooks/inspect_skill_contract.py`
- `hooks/sync_skill_contract_hints.py`
- `skills/autodev/*/SKILL.md`
- `skills/autobiz/*/SKILL.md`
- `skills/autoops/*/SKILL.md`

#### 具体做法

1. 所有 skill 统一先执行 `inspect_skill_contract.py --feature ... --json`。
2. skill 文档只保留“如何使用契约”，不再维护硬编码产物清单。
3. 只读输入、降级语义、禁止项都从 contract 的 Source/Method bundle 获取。
4. 文档里写死的文件名只作为展示，不作为准入事实。

#### 验收标准

- skill 不再靠猜测上下文执行。

---

### 13) 文档同步

#### 要改什么

- 把实现后的语义同步回文档。
- 避免“实现已变、说明还旧”。

#### 文件落点

- `docs/ai-coding-harness-optimization.md`
- `docs/inspect-json-dynamic-workflow.md`
- `docs/ui-plugin-verify-quickstart.md`
- 本文档自身

#### 具体做法

1. 在优化分析文档里补上实现路线链接。
2. 在动态工作流文档里统一 frontend 语义。
3. 在 quickstart 里补充新的 JSON sidecar、gate、policy 说明。
4. 文档中的示例命令保持和实际脚本一致。

#### 验收标准

- 用户看文档，不会再被旧语义带偏。

## 5. 推荐实施顺序

### Sprint 1

- 稳定 ID
- `plan.json`
- `EVIDENCE.jsonl`
- Scenario 覆盖矩阵
- `DETAIL_DESIGN.md` 引用点和 `detail_design_before_code` 兼容约定

### Sprint 2

- `code_done` gate
- `VERIFY_DECISION.json`
- `FIX_REQUEST.json`

### Sprint 3

- 远程 Eval Bundle
- `EVAL_RESULT.json`
- `knowledge_candidates.json`

### Sprint 4

- runtime policy
- frontend 迁移与语义收口
- skill 执行路径优化
- docs 同步

## 6. 最终验收

当以下条件全部满足时，认为这轮实现闭环成立：

1. 每个 Requirement / Scenario / Task / Evidence / Eval 都有稳定 ID。
2. `PLAN.md`、`plan.json`、`EVIDENCE.jsonl` 三者可互相追踪。
3. `code_done`、`verify_done`、`needs_fix` 都能被结构化文件驱动。
4. 远程评测能导入 bundle 并回写结果。
5. runtime policy 能拦住越权行为。
6. frontend 语义与 workflow 文档一致。
7. skill 执行路径不再依赖自由猜测。

## 7. 非目标

- 不在本文档里直接改业务实现。
- 不在本文档里替换现有全部 Markdown 报告。
- 不在本文档里引入新的外部服务依赖。
