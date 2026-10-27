---
name: autodev-plan
description: 将已确认的行为与技术设计转为任务、真实依赖和仓库归属，生成并发布 Plan。
version: v4.0
---

# /autodev-plan — Delivery Plan

默认主会话执行，只生成一份 Plan 输入；writer 生成 `plan.json`、Batch 和人类视图 `PLAN.md`。

## 输入与查证

读取 proposal、specs、design、`.design-contract.lock.json`、`IMPLEMENTATION_SCOPE.json` 与 `UI_CONTEXT.json`，复用已确认事实。只为影响仓库归属或真实依赖的问题补查相关入口；有独立检索价值时才向 `Explore-autodev` 提供具体问题、范围和已有证据，足以规划即停止。

```bash
python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_in_progress --stage "执行计划"
```

输入缺失时用 `inspect_skill_contract.py autodev-plan --feature "${feature}" --plain` 获取缺失处理；正常执行无需预读 writer 或门禁源码。

## Plan 输入

使用 `templates/plan-input.json`（`schemaVersion=autodev.plan`）：

- 一个 Task 交付同一仓库内完整、可独立评审的行为；同一行为的正常、边界、权限和失败分支放一起。不同独立交付结果或跨仓库工作才拆分，不按文件、方法或断言拆；边界难判断时再读 `references/task-planning.md`。
- `id` 为稳定 `TNNN`，`outcome` 写可观察交付结果，`workspace` 写仓库逻辑 ID，`dependsOn` 只写真实前置交付。数组顺序不代表依赖。
- `implementationPoints` 写关键改动与边界，`testPoints` 写需证明的行为及失败路径，`verification.intent` 写一句验收目标；避免重复描述，不预测实现文件、方法、测试命令或 Batch。
- `refs.requirements` / `refs.scenarios` 引用真实 `specs/<capability>/spec.md#REQ-NNN` / `#SCN-NNN`，逐条覆盖本期场景。`refs.api` / `refs.data` 按需填写；每个本期 `D-NNN` 在 `refs.decisions` 中只有一个主交付 Task。其他协作任务引用 API/Data/Scenario，既有范围不得为通过校验而缩小。
- UI Task 才填写 `ui.pages`、`ui.interactions`、`ui.route`；只读页面 interactions 可为空。writer 从 UI capability 投影视觉源，从 Spec 的 Source References 投影外部资料，不由模型重复维护这些派生字段。

## 生成、回检与发布

```bash
python -X utf8 "${pluginPath}/hooks/plan_writer.py" prepare-plan \
  --feature "${feature}" --code-workspace "<ACTUAL_CODE_WORKSPACE>" --body-stdin
```

多仓库重复传入 `--code-workspace`，单仓库可用逻辑 ID `default`。writer 校验引用、覆盖、设计归属、仓库绑定及 DAG，生成 `reviewing` 的完整计划；失败按结构化问题修正输入，重新 prepare，不手改机器产物。

按 `${pluginPath}/skills/references/review-protocol-plan.md` 做一次 Plan Critic 语义回检：只查交付边界、仓库职责和真实依赖，不重做设计探索。修复后根据 `reviewChanges` 复核受影响任务与依赖；共享需求/设计/范围变化时全量复核。输入与上下文未变且已有通过结论时复用结论，不重复生成或回检。

```bash
python -X utf8 "${pluginPath}/hooks/plan_writer.py" publish-plan --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/stage_gate.py" validate --stage dev.plan --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/update_checkpoint.py" --checkpoint plan_done
```

`publish-plan` 将计划定稿为 `finalized` 并投影 `PLAN.md`；Markdown 不独立维护。定稿后的行为或计划变更走 Plan rollback，执行恢复交由 Code workflow。只有改变已确认行为、公开契约或用户取舍时按 `${pluginPath}/skills/references/ask-user-question.md` 提问；内部拆分与排序自行修正。同一问题修复后仍失败则报告阻断。

简报产物与验证结果，再按 `${pluginPath}/skills/references/ui-continuation-guide.md` 处理阶段完成。
