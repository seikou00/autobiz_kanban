---
name: autodev-frontend
description: Dev 阶段可选前端实现节点。用于 frontend_before_specs workflow profile，在行为规格生成前基于 PRD、HTML 和现有前端工程完成前端实现准备或落地。
version: v1.1.1604
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约

当前 skill 的 checkpoint、输入/输出产物和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 为唯一事实来源。
运行前如需查看当前契约，执行：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autodev-frontend --json
```
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；用于前端代码探索、实现和验证。

# /autodev-frontend - 前端实现

本技能是 `frontend_before_specs` workflow profile 中的正式 Dev 节点，用于在 `PRD.md` 已完成、进入行为规格前，先处理用户明确要求的前端实现工作。

## 流程状态

开始前端实现时必须使用统一脚本推进 checkpoint：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint frontend_in_progress
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

完成前端实现和必要验证后：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint frontend_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

## 工作方式

- 优先读取 `{FEATURE_DIR}/PRD.md`、用户提供的 HTML/截图/页面说明，以及 `CODE_WORKSPACE` 中的前端工程约定。
- 若用户未提供 HTML 或明确页面目标，先澄清输入，不要编造页面。
- 只修改真实前端工程相关文件；Feature 过程产物仍写入 `{FEATURE_DIR}`。
- 完成后汇报变更文件、验证命令、未覆盖风险，并提示下一步进入 `/autodev-specs`。
