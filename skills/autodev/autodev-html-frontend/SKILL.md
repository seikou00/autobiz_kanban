---
name: html-frontend
description: HTML→前端代码生成阶段统一入口（autobizdevops 第 2 阶段，位于 Biz 之后、Dev 之前）。负责前置准入校验、流程编排、子技能路由与关键产出物脚本校验。所有 HTML→前端代码生成工作应通过本入口进入。当用户提到"autobizdevops"且涉及 HTML 或前端代码生成时，优先匹配本技能。
version: v1.0.0
---

# /html-frontend — HTML→前端代码生成阶段统一入口（Biz 之后、Dev 之前）

> 本技能是 HTML→前端代码生成阶段的唯一统一入口，负责 workspace 前置准入、流程编排和产出物脚本校验。
>
> 本阶段位于 Biz 阶段（PRD 生成完成）之后、Dev 阶段（Specs 生成）之前。
>
> 下游包含一个子技能：
> - `/auto-html-to-frontend` — 高保真 HTML 转前端代码实现

## 触发条件

以下场景应自动触发本技能：

- 用户提供高保真 HTML（上传 `.html` / `.htm` 文件或粘贴 HTML 内容）
- 用户要求从 HTML 生成前端代码
- 用户提到"autobizdevops"且涉及 HTML、前端代码生成或页面还原
- 由 autodev 在 checkpoint 为 `prd_done` 时询问用户后路由进入
- 用户从其他阶段（如 Dev）回溯到 HTML→前端阶段
- checkpoint 为 `html_frontend_in_progress` 时恢复执行

## 前置准入条件

所有子技能执行前，必须先通过本入口完成前置准入检查。

### Step 1: 确定工作目录

```
工作目录 = .autobizdevops/features/{slug}/
```

- `{slug}` 由用户指定，或从当前上下文推导
- 若目录不存在，可安全创建

### Step 2: 确认 HTML 输入

- 用户必须提供高保真 HTML 文件或内容
- 只有截图/图片/Figma 链接但拿不到实际 HTML 时，停下并要求用户补 HTML

## 流程编排

根据当前状态和用户意图，路由到对应子技能：

| 用户意图 | 当前状态要求 | 路由目标 |
|---------|------------|---------|
| 从 HTML 生成前端代码 | 无硬性前置 | `/auto-html-to-frontend` |
| 恢复 HTML→前端代码生成 | `html_frontend_in_progress` | `/auto-html-to-frontend` |

**执行顺序约束**：

```
Biz 阶段（PRD 生成完成）→ /auto-html-to-frontend → Dev 阶段（Specs 生成）
```

## 关键产出物校验（强制脚本）

所有产出物校验必须通过脚本执行，不得仅做 Markdown 勾选。

### 校验命令

```bash
set PYTHONIOENCODING=utf-8 && python "$PLUGIN_ROOT/autodev/autodev-html-frontend/hooks/html_frontend_validate.py" --feature {slug}
```

### 校验不通过时的处理

1. **阻断下游**：脚本返回非 0 时，不得进入下一阶段
2. **向用户展示具体错误**：调用脚本时输出可读错误列表
3. **引导修复**：明确告知用户需要补齐的文件或内容

## 输出清单

本入口完成一次完整的 HTML→前端阶段编排后，应确认：

- [ ] 已按正确顺序路由并执行子技能
- [ ] 子技能完成后，`html_frontend_validate.py` 校验已通过
- [ ] checkpoint 已推进到 `html_frontend_done`

## 约束

1. 不允许跳过前置准入直接调用子技能
2. 不允许仅通过 Markdown 勾选替代脚本校验
3. 不允许在无 HTML 输入时进入 `/auto-html-to-frontend`
4. 子技能执行完成后，必须通过 `update_checkpoint.py` 将 checkpoint 推进到 `html_frontend_done`
