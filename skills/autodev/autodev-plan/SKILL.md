---
name: autodev-plan
description: 将已确认的需求与技术设计收敛为可调度的任务计划。
version: v3.0
---

# /autodev-plan — Delivery Plan

Plan 的职责是确定交付结果、仓库归属、真实依赖、主要实现要点和测试要点。它不提前指定实现文件、类/方法、测试命令、测试目录或 Batch 编号；这些由 Code、UTest 和 writer 在拥有真实代码上下文时决定。

## 开始

读取状态与当前 Feature 的 `proposal.md`、`specs/**/*.md`、`UI_CONTEXT.json`、`design.md`、`.design-contract.lock.json`。只在仓库归属不明确时读取实际代码仓库确认 Git 根，不要为了猜文件清单做全面探索。

```bash
python "${pluginPath}/read_state_json.py" --feature "${feature}"
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-plan --feature "${feature}" --plain
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_in_progress --stage "执行计划"
```

## 唯一模型输入

模型只生成一份 `autodev.plan.v2`。完整模板在 `templates/task-groups.json`。每个 Task 只需要：

- `outcome`：一个用户或系统可观察的交付结果；
- `workspace`：实际 Git 仓库；
- `dependsOn`：确实存在的前置交付；
- `refs.requirements`、`refs.scenarios`：上游行为引用；
- `refs.api` / `refs.design` / `refs.data` / `refs.decisions`：仅在任务确实依赖相应设计时填写；
- `implementationPoints`：一到数条主要实现方向，描述行为或边界，不写文件、类或方法；
- `testPoints`：一到数条后续必须证明的行为、边界或失败路径，不写测试命令或文件；
- `verification.intent`：希望后续测试验证的行为；
- UI Task 才填写来自 `UI_CONTEXT.json` 的 `ui` 引用。

不要输出文件、目录、方法、`writeSet`、`scope.paths`、`expectedFiles`、`nonGoals`、测试命令、命令 cwd、Batch、验收 ID、测试资产或 `PLAN.md`。实现要点和测试要点只说明目标与边界，不能伪装成文件或命令清单。

场景引用仍必须逐条写成 `specs/<capability>/spec.md#SCN-NNN`；这是覆盖计算的机器事实。任务可以覆盖任意数量的场景、API 或页面。是否拆分只看交付结果和真实依赖，不按固定数量阈值拆分。

## 发布

提交一次输入即可原子生成 `plan.json`、Batch 计划和 `PLAN.md`：

```bash
python "${pluginPath}/hooks/plan_writer.py" publish-plan \
  --feature "${feature}" \
  --code-workspace "<ACTUAL_CODE_WORKSPACE>" \
  --body-stdin
```

writer 负责验证引用、覆盖、Design ID、仓库绑定和 DAG，并生成 Task/Batch 身份、运行时 workspace roots、验收记录、测试意图、Batch 与投影视图。发布失败时只修返回的业务引用、仓库或依赖；不要补造实现文件清单来通过校验。

`PLAN.md` 是 `plan.json` 的人类视图，由 writer 同一次发布落盘，不能独立维护。依赖就绪的隔离工作树可乐观并行；真实文件冲突由 Merge Train 处理。

Plan v2 的正常路径没有 Draft、Detail、全量 lint、重复提交或 hash patch。已发布但尚未执行的计划发生真实需求或设计变更时，重新生成一份完整 Plan v2；已经执行的计划由 Code workflow 的受控恢复处理。

## 完成

Plan 回检只检查：行为是否覆盖、任务归属和依赖是否可信、以及任务结果是否足够让 Code 结合上游契约实施。它不要求提前指定文件、方法、测试类或实现步骤。

运行「产物契约预检（机器校验）」只验证引用、覆盖、仓库绑定和 DAG 等机器事实；失败时修正 Plan v2 的业务引用、归属或依赖，不增加实现细节来绕过校验。

```bash
python "${pluginPath}/hooks/render_review_protocol.py" --stage dev.plan
```

完整遵循其输出，不得凭记忆执行本节。

```bash
python "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
```

技能完成后，读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`。
