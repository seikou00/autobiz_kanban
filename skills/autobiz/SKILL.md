---
name: autobiz
description: Biz 阶段统一入口。负责前置准入校验、流程编排、子技能路由与关键产出物脚本校验。所有 Biz 阶段工作应通过本入口进入。
version: v1.1.1604
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

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
- 任何需要操作 `{FEATURE_DIR}/` 目录的场景

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
python "$PLUGIN_ROOT/read_state_json.py"
```

确定 `{slug}` 后，立即读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

后续流程编排和子技能准入直接取用 `CHECKPOINT`；只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用脚本刷新 `CHECKPOINT`。若脚本提示 Feature 不存在，仅 `/autobiz-requirement-discuss` 可通过 `--allow-create` 创建；创建后必须刷新 `CHECKPOINT`。

随后调用动态路由脚本读取 board_config 派生出的下一步：

```bash
python "$PLUGIN_ROOT/hooks/resolve_next_skill.py" --workspace "$PROJECT_PLUGIN_DIR" --feature "$FEATURE_ID" --json
```

## 流程编排

根据 `CHECKPOINT`、用户意图和 `resolve_next_skill.py --json` 结果路由到对应子技能。`recommendedNextSkill` 和 `nextAction` 均来自 `$PLUGIN_ROOT/board_core/board_config.json` 的有效 workflow；静态说明不得覆盖脚本结果。

| 用户意图 | 当前状态要求 | 路由目标 |
|---------|------------|---------|
| 需求澄清、讨论需求、完善需求文档 | 无硬性前置 | `/autobiz-requirement-discuss` |
| 生成正式 PRD、整理 PRD、PRD 定稿 | 必须先有收敛的 `PRD_DISCUSS.md` | `/autobiz-prd-generate` |

**执行顺序约束**：

```
/autobiz-requirement-discuss → /autobiz-prd-generate
```

- 下游子技能执行前，必须通过脚本校验上游产出物
- Biz 阶段完成到 `prd_done` 后，跨阶段出口必须提示 `/autodev`。

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
