# autodev-specs 门禁收敛（2026-08-30）

对象：`AutobizDevOps_Plugin_Kanban` · `dev.specs`
起因：`test_ruoyi_dev` 复跑一轮后，`autodev-specs门禁审计-合并版.md` 落地的 P1 审计链本身成了主要成本来源。

## 复跑观察

同一条流程在新一轮 session 中出现的实际阻断：

- `python` 不存在，第一条命令即失败（技能与 hooks 混用 `python` / `python3`）。
- `alloc_spec_ids.py` 在 proposal 生成前被调用，报 `SPEC_ID_ALLOCATION_MISSING_PROPOSAL`。
- `SPECS_REVIEW_RUN_ARTIFACT_STALE` 连续 3 次：处置 finding 造成的任何产物字节变化都让摘要失效，唯一出口是重跑 critic。模型最后去读插件自身的 `specs_review_state.py` 源码找出路。
- final gate 报 `specs_review_new_run_unrecorded`，要求先 record，而 record 又因 stale 拒绝执行。

审计链把「保留审查证据」做成了「每次编辑都要重新取得审查证据」，形成一个只能靠再跑一轮 critic 才能离开的循环。

## 本轮收敛

`dev.specs` 阻断器收敛为四个：`proposal_contract`、`specs_contract`、`capability_spec_correspondence`、简化后的 `specs_review_verdict`。

| 原机制 | 处置 |
|---|---|
| `specs_review_finding_ledger` | 删除 finding 级解析；PostToolUse 只把 critic 原始响应归档到 `.runtime/CRITIC_REVIEWS.jsonl`，不再参与放行 |
| `specs_decision_ledger` | 删除；用户裁定以会话记录与最终产物为准，不重复维护本地账本 |
| `specs_review_freshness` | 删除：`specs_review_state.py` 与 `SPECS_REVIEW_STATE.json` 双摘要机制整体移除 |
| `SPEC_ID_ALLOCATIONS.json` 号段分配 | 删除：`alloc_spec_ids.py` 移除，改为顺序取未占用三位 ID |
| `SPECS_REVIEW.md` 的 `## Review Baseline` | 删除：五项必查内容交给 critic，机器只判 verdict / findings / unresolved |
| Findings 的分类与严重度闭集 | 删除：措辞由 critic 与主模型把关 |

critic 不再接收 fenced JSON、run ID、finding ID 或严重度闭集等增强提示。归档 hook 由运行时生成幂等 ID，原样保存 task response；缺少响应或写入失败时放行，不要求模型修复日志格式。

回检收口改为：critic 提出的问题由主模型复核、修复并收口；只有修改改变了行为契约（新增或改写 Requirement/Scenario、调整 capability 分类、变更范围）时才重跑一轮回检。

## 取舍

放弃的是「critic finding 逐条机器对账」「用户裁定本地账本」与「review 覆盖当前版本」三条机械保证。critic 的原始返回仍可用于故障复盘，但不要求弱模型维护第二套审计协议；用户裁定不再从工具响应中猜测并复制。

留下的四个阻断器都只判形状与解析：产物与章节是否齐全、ID 格式与唯一性、能力与 spec 是否双向对应、回检结论是否落盘且为终态。
