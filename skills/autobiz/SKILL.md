---
name: autobiz
description: Biz 阶段统一入口。负责前置准入校验、流程编排、子技能路由与关键产出物脚本校验。所有 Biz 阶段工作应通过本入口进入。
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。
```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```
# /autobiz — Biz 阶段统一入口

> 本技能是 Biz 阶段的唯一统一入口，负责 workspace 前置准入、流程编排和产出物脚本校验。
>
> 下游包含两个子技能：
> - `/autobiz-requirement-discuss` — 需求澄清与讨论收敛
> - `/autobiz-prd-generate` — 正式 PRD 提炼

## 触发条件

以下场景应自动触发本技能：

- 用户要求进入 Biz 阶段（需求澄清、PRD 生成）
- 用户提到"完善需求""整理 PRD"
- 用户从其他阶段（如 Dev）回溯到 Biz 阶段
- 任何需要操作 `.autobizdevops/features/{slug}/` 目录的场景

## 前置准入条件

所有子技能执行前，必须先通过本入口完成前置准入检查。
**本 skill 的规则不得覆盖 AGENTS.md；如冲突，以 AGENTS.md 中项目约束为准，除非系统级指令另有要求。**
**在执行autobiz与子技能时，约束必须参考AGENTS.md中存在的定制约束，不能仅遵守技能的约束。**

### Step 1: 确定工作目录

- `{slug}` 由用户指定，或从当前上下文推导
- 若目录不存在，`init_workspace.py` 已确保 `.autobizdevops/features/` 父目录存在，可安全创建子目录

### Step 2: 读取 State 快照

若 `{slug}` 未确定，先读取全部 State 快照，再从 `STATE.records` 选择或要求用户选择 Feature：

```bash
python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}"
```

确定 `{slug}` 后，立即读取当前 Feature 快照，并把返回 JSON 记为 `STATE`：

```bash
python "{PLUGIN_DIR}/read_state_json.py" --workspace "{WORKSPACE}" --feature "{slug}"
```

后续流程编排和子技能准入直接取用 `STATE.checkpoint` / `STATE.record`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `STATE`。若 `STATE` 提示 Feature 不存在，仅 `/autobiz-requirement-discuss` 可通过 `--allow-create` 创建；创建后必须刷新 `STATE`。

## 流程编排

根据 `STATE.checkpoint` 和用户意图，路由到对应子技能：

| 用户意图 | 当前状态要求 | 路由目标 |
|---------|------------|---------|
| 需求澄清、讨论需求、完善需求文档 | 无硬性前置 | `/autobiz-requirement-discuss` |
| 生成正式 PRD、整理 PRD、PRD 定稿 | 必须先有收敛的 `PRD_DISCUSS.md` | `/autobiz-prd-generate` |

**执行顺序约束**：

```
/autobiz-requirement-discuss → /autobiz-prd-generate
```

- 下游子技能执行前，必须通过脚本校验上游产出物

## 关键产出物校验（强制脚本）

所有产出物校验必须通过脚本执行，不得仅做 Markdown 勾选。`biz_validate.py` 在各 stage 的校验中已包含 `.autobizdevops/state.json` 的 checkpoint 同步检查。

### 各阶段校验命令

```bash
# 需求澄清完成后
set PYTHONIOENCODING=utf-8 && python autobiz/hooks/biz_validate.py discuss --feature {slug}

# PRD 生成完成后
set PYTHONIOENCODING=utf-8 && python autobiz/hooks/biz_validate.py prd --feature {slug}
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
3. 不允许在 `PRD_DISCUSS.md` 缺失时进入 `/autobiz-prd-generate`
