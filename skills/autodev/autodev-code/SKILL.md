---
name: autodev-code
description: 进行代码实现。
version: v1.2.0703
---

## 缺失产物处理

```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```


# /autodev-code — 代码执行

## 准入检查


```bash
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

识别规则：按项目 manifest 的单/多模块入口生成；`path` 用模块目录绝对路径，`compile_command` 以该目录为 cwd 执行（命令本身不要再写 `cd`）；无法确定时停止并询问用户，不得开始编码。

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## 执行协议

### 建立执行上下文与任务队列

- 使用`write_todos`，把 `plan.json`（如有） 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度。

###  选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目。

###  执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的结构化执行契约。存在 `plan.json` 时，必须读取当前 task 的 `goal`、`scope`、`implementationPoints`、`acceptanceCriteria`、`nonGoals`、`splitRationale`（若存在）、`specRefs`、`designRefs`、`validationCommands`；不得只根据 `title` / `specRefs` 脑补实现范围。缺少 `goal` / `scope` / `implementationPoints` / `acceptanceCriteria` / `nonGoals` 时停止编码，回到 `/autodev-plan` 补齐，不得边做边猜。先依各输入的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。`splitRationale` 只用于理解合并背景，不得作为扩大 scope 的理由。
3. 改代码前做有界探索定位真实文件与既有模式：只读上游产物或 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：只实现 `scope` / `implementationPoints` / `acceptanceCriteria` 指向的范围；不得实现 `nonGoals` 中列出的内容。观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 任务需要写 / 改测试时，遵循 `${pluginPath}/skills/references/test-quality.md`：站在 seam 上验证、期望值来自独立事实源（勿同义反复）、mock 只在系统边界。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（存在 `plan.json` 时优先 `plan.json.tasks[].validationCommands`；缺失或契约未列出 `plan.json` 时，基于 specs、项目脚本选最小可行验证）。每次验证完成后用 `hooks/evidence_store.py append` 追加一条 evidence；目标必须是 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/evidence/EVIDENCE.jsonl`，与 plan/discuss 等产物同属 feature 产物目录，不得写到业务代码仓库根目录或当前 cwd 下的临时 `.autobizdevops`。append 工具会默认从 `PLUGIN_WORKSPACE/PROJECT_DIR` 定位产物根；手写命令时可显式加 `--workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"`。evidence 记录 taskId（无 plan 时使用本轮轻量任务 ID）、specRefs、designRefs（无 design 契约时可为空）、changedFiles、validation.command/exitCode/result；`ev_XXXX` 按全流顺序自动递增，不按阶段重排，不得插入旧记录前、重编号、截断、重写、删除 `EVIDENCE.index.json` 后重建或手动修改 `EVIDENCE.index.json`。若 append 或 checkpoint 报 `evidence_stream_rewritten_or_truncated` / `missing_evidence_index_for_nonempty_stream`，必须恢复被改写前的 `EVIDENCE.jsonl` / `EVIDENCE.index.json`，无法恢复时停止并向用户报告。通过 → 状态「完成」；存在 `plan.json` 时还要将新增 evidenceId 写回 `plan.json.tasks[].evidenceIds`，`PLAN.md` 若存在再同步人类视图。失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。
7. 若 `SMOKE_TEST_PLAN.json`存在，按其中 `tests[]` 生成或补齐旁路冒烟测试源码/脚本。每条 smoke 必须按计划中的 `seam` 站在公开边界上验证，不测私有方法、不查内部实现细节；按 `verticalSlice` 一次只实现一个最小闭环，不把多个场景合成一条大烟测；按 `mockPolicy` 只 mock 系统边界，不 mock 自有模块或内部协作者。冒烟测试必须是 opt-in：Java/Spring 可用 `*SmokeIT` + `-Psmoke`，前端可用 `tests/smoke/` + 单独 smoke script，CLI/API 可用 `scripts/smoke/`；这些源码/脚本只用于本地验证，可以放在业务项目测试目录，但不得进入业务项目 Git 托管。生成后必须确保 `sourcePath` 被目标项目 Git 忽略，优先把精确路径或窄范围 AutoDev smoke 模式写入 `.git/info/exclude`，不要把 smoke 源码 `git add`，也不得让默认 `validationCommands` 无意中跑到慢/脆的冒烟。全部强 validation 通过后，运行：

```bash
python "${pluginPath}/hooks/run_advisory_smoke.py" --feature "${feature}"
```

`run_advisory_smoke.py` 会写入 `SMOKE_RESULT.json` 并向 `EVIDENCE.jsonl` 追加 `action=smoke` evidence。冒烟 PASS/FAIL/BLOCKED/SKIPPED 都只作为旁路风险信号：不得把 smoke evidence 写入 `plan.json.tasks[].evidenceIds`，不得把冒烟失败改成任务失败，不得因为 `SMOKE_RESULT.json.verdict` 非 PASS 而阻断 `code_done`。但如果 `SMOKE_TEST_PLAN.json.tests[]` 非空，必须产出覆盖每个 `SMK-xxx` 的 `SMOKE_RESULT.json`。

若 `run_advisory_smoke.py` 在执行前置检查阶段返回非 0（例如 `sourcePath` 对应测试源码不存在、测试条目非法、命令缺失、sourcePath 已被 Git 跟踪或未被 Git ignore 命中），这表示 Code 阶段尚未按 `SMOKE_TEST_PLAN.json` 补齐本地冒烟测试资产；必须先补齐测试源码/修正计划/更新 `.git/info/exclude` 后重跑。只有冒烟命令已经实际执行后的 PASS/FAIL/BLOCKED/SKIPPED 结果才属于不阻断流转的旁路风险信号。

策略边界：`plan.json.tasks[].validationCommands`、`action=validation` evidence 与 `code_done_gate` 仍是强门禁；`SMOKE_TEST_PLAN.json` / `SMOKE_RESULT.json` 只表达旁路冒烟风险。不要把启动/主链路 smoke 命令同时放进强门禁和 advisory smoke；除非用户明确要求恢复阻断式 startup gate，否则不得让 `SMOKE_RESULT.json.verdict` 影响 `code_done` 流转。

> 一致性：任务的依据在对应上游产物里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

###  全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（至少编译）。失败回到相关任务，不推进。

项目级验证收敛后：

```bash
python "{PLUGIN_ROOT}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "{PLUGIN_ROOT}/read_state_json.py" --feature "{FEATURE_ID}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件

为完成任务必须改队列未直接提到的业务文件，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 若 `plan.json`存在：`plan.json` 中所有任务为完成态，每个任务至少有一条通过的 evidence；若 `plan.json`不存在：本轮轻量任务队列全部完成，并在 evidence 中记录对应 specs/proposal 依据。
- `evidence/EVIDENCE.jsonl` 与 `evidence/EVIDENCE.index.json` 完整性校验通过，不存在截断、重写、重排、重编号或 index 缺失绕过。
- 若 `SMOKE_TEST_PLAN.json`存在：已按计划生成/补齐冒烟测试源码并确认其被目标项目 Git 忽略，已运行 `run_advisory_smoke.py`；`SMOKE_RESULT.json` 已写入。`SMOKE_RESULT.json.verdict` 为 `FAIL` / `BLOCKED` / `SKIPPED` 时，记录为风险但不阻断本阶段流转。
- 必要验证通过；项目编译通过（code_done execute hook 会在推进前再次校验 plan/evidence 闭环）。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**
