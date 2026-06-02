---
name: autobizdevops
description: 完成项目研发的全流程，按 biz / dev / ops 三个可独立起步的阶段组织
version: v1.1.0_v0602
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

# 核心工作原则

- 根工作目录总入口，分为 `biz`、`dev`、`ops` 三个阶段。完整链路 `biz -> dev -> ops`，每个阶段执行完成后必须由用户确认后继续执行。
- Dev 阶段除 `/autodev-reviewer` 可启动独立只读 reviewer 外，其余阶段均由当前会话内联执行，不得委派给下级 agent。

## 目录结构与路径约定

### 本仓库目录功能速览

| 目录 | 用途                                                                        |
|------|---------------------------------------------------------------------------|
| `autobiz/` | Biz 阶段技能集合。存放需求澄清、PRD 生成等                                                 |
| `autodev/` | Dev 阶段技能集合。存放计划、编码、单测、评审、验证、E2E 等                                         |
| `autoops/` | Ops 阶段技能集合。当前含 CI/CD 流水线相关技能和归档技能。                                        |
| `templates/` | 标准产物模板。当前产物模板分布在各阶段 skill 内，供 Biz / Dev 阶段生成 `PRD.md`、`proposal.md`、`specs/**/*.md`、`design.md`、`PLAN.md` 时读取。 |



### 路径概念区分
- **PLUGIN_DIR**：本插件的根目录（即 `../`）。所有 SKILL.md 文件、校验脚本、hooks 都存放在此目录下。脚本调用路径均以此为基准。
- **PROJECT_PLUGIN_DIR**：项目插件根目录，固定为 `{PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`；`read_state_json.py` / `update_checkpoint.py` 固定从这里读写状态，命令中不得传 `--workspace/-w`。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`。`CODE_WORKSPACE` 可能与 `PROJECT_PLUGIN_DIR` 相同，但不得默认把 `PROJECT_PLUGIN_DIR` 当作代码工作区。

`AGENTS.md` 属于代码工作区约束文件。读取或检查 `AGENTS.md` 时，目标必须是 `{CODE_WORKSPACE}/AGENTS.md`；只有明确确认代码根目录和 `PROJECT_PLUGIN_DIR` 是同一目录时，才允许检查 `{PROJECT_PLUGIN_DIR}/AGENTS.md`。

## 入口约定

以下三个为 `autobizdevops` 的唯一直接入口。所有 Biz / Dev / Ops 阶段工作均应通过这些统一入口进入，各阶段内部子技能由对应入口按 checkpoint 路由，不允许跳过前置准入直接调用子技能。
**本 skill 的规则不得覆盖 AGENTS.md；如冲突，以 AGENTS.md 中项目约束为准，除非系统级指令另有要求。**
**在执行autobiz和autodev技能时，约束必须参考AGENTS.md中存在的定制约束，不能仅遵守技能的约束。**

### 技能映射
| 阶段                  | 调用 Skill   | 本工程文件                               |
|---------------------|------------|-------------------------------------|
| autobiz             | `/autobiz` | `autobiz/SKILL.md`                  |
| autodev             | `/autodev` | `autodev/SKILL.md`                  |
| autoops             | `/autoops` | `autoops/SKILL.md`                  |


### Checkpoint 路由映射

完成前置准入后，根入口必须先通过脚本读取当前 State 快照，并按 `CHECKPOINT` 路由到对应阶段入口。根入口只负责选择 `/autobiz` / `/autodev` /
`/autoops`，不得直接跳入阶段内部子技能；阶段入口会继续按自身 `SKILL.md` 的 checkpoint 映射路由到具体子技能。

### State 快照读取

所有需要当前 FEATURE_ID checkpoint 的判断，第一步必须调用脚本读取 `.autobizdevops/state.json`：已知 Feature 时把 stdout 捕获为 `CHECKPOINT`；未知 Feature 时读取全量 JSON 并记为 `STATE`，仅用于从 `STATE.records` 选择 Feature。不得绕过脚本重新手工读取 `state.json`。

```bash
# 已知 Feature
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")

# 未知 Feature：先读取全部 records，再选择或要求用户选择 Feature
python "$PLUGIN_ROOT/read_state_json.py"
```

只有执行 `update_checkpoint.py` 后、子技能返回后，或明确需要确认外部状态变化时，才再次调用 `read_state_json.py` 刷新 `CHECKPOINT`。

### Checkpoint 更新命令

所有阶段推进 checkpoint 时，必须使用统一脚本更新 `.autobizdevops/state.json`，不得手工修改 `state.json` 或生成视图 `STATE.md`。脚本会同步重生 `.autobizdevops/STATE.md`，并在写入前复用 checkpoint 流转和 Autodev 产物校验；进入 `code_done` 时，execute hook 会基于 `.autobizdevops/modules_compile.json` 非阻塞执行编译并写入 hook 日志，编译失败不阻止 checkpoint 更新。

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint {checkpoint}
```

| Checkpoint | 根路由目标 | 说明 |
|------------|------------|------|
| `discuss_in_progress` | `/autobiz` | 恢复需求澄清 |
| `discuss_done` | `/autobiz` | 继续生成 PRD |
| `prd_in_progress` | `/autobiz` | 恢复 PRD 生成 |
| `prd_done` | `/autodev` | 进入 Dev Specs 阶段 |
| `specs_in_progress` | `/autodev` | 恢复 Dev Specs |
| `specs_done` | `/autodev` | 进入 Dev 计划阶段 |
| `plan_in_progress` | `/autodev` | 恢复 Dev 计划 |
| `plan_done` | `/autodev` | 进入编码阶段 |
| `code_in_progress` | `/autodev` | 恢复编码 |
| `code_done` | `/autodev` | 进入需求实现评审 |
| `requirements_eval_in_progress` | `/autodev` | 恢复需求实现评审 |
| `requirements_eval_done` | `/autodev` | 进入单元测试 |
| `unit_test_in_progress` | `/autodev` | 恢复单元测试 |
| `unit_test_done` | `/autodev` | 进入 E2E |
| `e2e_in_progress` | `/autodev` | 恢复 E2E |
| `e2e_done` | `/autodev` | 进入验收汇总 |
| `verify_in_progress` | `/autodev` | 恢复验收汇总 |
| `verify_done` | `/autoops` | 进入 Ops CI/CD |
| `cicd_in_progress` | `/autoops` | 恢复 CI/CD |
| `cicd_done` | `/autoops` | 进入归档 |
| `archived` | `/autoops` | Ops 终态，提示已归档 |
| `needs_fix` | 停止 | 读取最近阶段报告中的建议回流阶段并提示用户 |

若 `CHECKPOINT` 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature，不得猜测路由。
