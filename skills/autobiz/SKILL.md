---
name: autobiz
description: Biz 阶段统一入口。负责前置准入校验、流程编排、子技能路由与关键产出物脚本校验。所有 Biz 阶段工作应通过本入口进入。
version: v1.1.1604
---


# /autobiz — Biz 阶段统一入口

> 本技能是 Biz 阶段的唯一统一入口，负责 workspace 前置准入、流程编排和产出物脚本校验。
>
> 下游包含两个子技能：
> - `/autobiz-requirement-discuss` — 需求澄清与讨论收敛
> - `/autobiz-prd-generate` — 正式 PRD 提炼


### 读取 State 快照

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

后续流程编排和子技能准入直接取用 `CHECKPOINT`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `CHECKPOINT`。

随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python "${pluginPath}/hooks/resolve_next_skill.py" --json
```

## 流程编排

根据 `CHECKPOINT`、用户意图和 `resolve_next_skill.py --json` 结果路由到对应子技能。`recommendedNextSkill` 和 `nextAction` 均来自 `${pluginPath}/board_core/board_config.json` 的有效 workflow；静态说明不得覆盖脚本结果。

| 用户意图 | 当前状态要求 | 路由目标 |
|---------|------------|---------|
| 需求澄清、讨论需求、完善需求文档 | 无硬性前置 | `/autobiz-requirement-discuss` |
| 生成正式 PRD、整理 PRD、PRD 定稿 | 如有 `PRD_DISCUSS.md` 时必须先有收敛的讨论稿|


下游子技能执行前，必须通过脚本校验上游产出物
## 关键产出物校验（强制脚本）

所有产出物校验必须通过脚本执行，不得仅做 Markdown 勾选。
### 各阶段校验命令

```bash
# 需求澄清完成后
set PYTHONIOENCODING=utf-8 && python ${pluginPath}/autobiz/hooks/biz_validate.py discuss --feature "${feature}"

# PRD 生成完成后
set PYTHONIOENCODING=utf-8 && python ${pluginPath}/autobiz/hooks/biz_validate.py prd --feature "${feature}"
```

### 校验不通过时的处理

1. **阻断下游**：脚本返回非 0 时，不得进入下一阶段
2. **向用户展示具体错误**：调用脚本时输出可读错误列表
3. **引导修复**：明确告知用户需要补齐的文件或内容

## 输出清单

本入口完成一次完整的 Biz 阶段编排后，应确认：

- [ ] 已按正确顺序路由并执行子技能
- [ ] 每个子技能完成后，对应 stage 的 `biz_validate.py` 校验已通过

## 约束

1. 不允许跳过前置准入直接调用子技能
2. 不允许仅通过 Markdown 勾选替代脚本校验
3. 契约含 `PRD_DISCUSS.md` 时，不允许在讨论稿缺失时进入下一阶段。
