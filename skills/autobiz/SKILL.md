---
name: autobiz
description: Biz 阶段统一入口。负责前置准入校验、流程编排、子技能路由与关键产出物脚本校验。所有 Biz 阶段工作应通过本入口进入。
version: v1.1.08131
---


# /autobiz — Biz 阶段统一入口

> 本技能是 Biz 阶段的唯一统一入口，负责 workspace 前置准入、流程编排和产出物脚本校验。
>
> 下游包含一个子技能：
> - `/autobiz-requirement-discuss` — 需求澄清与讨论收敛

### 读取 State

```bash
python3 "${pluginPath}/read_state_json.py" --feature "${feature}"
```

每次需要当前 checkpoint 时，运行上面的脚本读取，不得从 `hooks.ndjson` 等其他文件推断。

随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python3 "${pluginPath}/hooks/resolve_next_skill.py" --json
```

## 流程编排

根据 `CHECKPOINT`、用户意图和 `resolve_next_skill.py --json` 结果路由到对应子技能。`recommendedNextSkill` 和 `nextAction` 均来自 `${pluginPath}/board_core/board_config.json` 的有效 workflow；静态说明不得覆盖脚本结果。

| 用户意图 | 当前状态要求 | 路由目标 |
|---------|------------|---------|
| 需求澄清、讨论需求、完善或生成 PRD | 无硬性前置 | `/autobiz-requirement-discuss` |

**执行顺序约束**：

```
/autobiz-requirement-discuss
```

下游子技能执行前，必须通过脚本校验上游产出物
## 关键产出物校验（强制脚本）

所有产出物校验必须通过脚本执行，不得仅做 Markdown 勾选。`biz_validate.py` 在各 stage 的校验中已包含 `.autobizdevops/state.json` 的 checkpoint 同步检查。

### 各阶段校验命令

```bash
# PRD 完成后
set PYTHONIOENCODING=utf-8 && python3 "${pluginPath}/skills/autobiz/hooks/biz_validate.py" prd --feature "${feature}"
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
