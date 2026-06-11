---
name: route-review
description: 主线生成完成后，按用户确认进入回检路线。它负责把回检任务路由到对应的回检子技能；当前支持 HTML 结果复查和前端性能检查。
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；用于前端代码探索、实现和验证。

# 回检路由

这是独立的 route 技能目录。

- 当前 route 技能入口：`SKILL.md`
- 当前 route 技能依赖：`deps/`

本文中提到的 `SKILL.md` 均指仓库根技能 `../../SKILL.md`。

## 当前分流

| 场景 | 处理 |
| --- | --- |
| 含 HTML 主线结果的结构 / 还原 / 选型复查 | 转交 `deps/html-post-review.md` |
| 需要做前端性能代码扫描 | 转交 `deps/frontend-performance-checker.md` |

## 进入条件

- 只有主线代码生成、联调 / 校验和主线抽取都完成后，才允许进入本 route
- 进入前必须先拿到用户确认；若当前运行模式支持 `request_user_input`，优先使用它；若不支持，则必须做显式文本确认
- 在拿到用户确认之前，本 route 不能被隐式触发，也不能作为主线收尾的一部分自动进入
- 主线交付总结后的“是否回检”必须是一个真实决策动作，而不是一句可选提示；没有用户答复时，本 route 视为尚未获准进入
