---
name: autobizdevops
description: 完成项目研发的全流程，按 biz / dev / ops 三个可独立起步的阶段组织
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

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
| `templates/` | 标准产物模板。当前包含 `prd.md`、`design.md` 与 `plan.md`，供 Biz / Dev 阶段生成 `PRD.md`、`design.md`、`PLAN.md` 时读取。 |



### 路径概念区分
- **PLUGIN_DIR**：本插件的根目录（即 `../`）。所有 SKILL.md 文件、校验脚本、hooks 都存放在此目录下。脚本调用路径均以此为基准。
- **WORKSPACE**：用户项目工作空间目录（运行初始化脚本的目录）。初始化后会在该目录下创建 `{PLUGIN_OUTPUT_DIR}/.autobizdevops/`。

## 入口约定

以下三个为 `autobizdevops` 的唯一直接入口。所有 Biz / Dev / Ops 阶段工作均应通过这些统一入口进入，各阶段内部子技能由对应入口自动路由，不允许跳过前置准入直接调用子技能。
**本 skill 的规则不得覆盖 AGENTS.md；如冲突，以 AGENTS.md 中项目约束为准，除非系统级指令另有要求。**
**在执行autobiz和autodev技能时，约束必须参考AGENTS.md中存在的定制约束，不能仅遵守技能的约束。**

### 技能映射
| 阶段                  | 调用 Skill   | 本工程文件                               |
|---------------------|------------|-------------------------------------|
| autobiz             | `/autobiz` | `autobiz/SKILL.md`                  |
| autodev             | `/autodev` | `autodev/SKILL.md`                  |
| autoops             | `/autoops` | `autoops/SKILL.md`                  |


### Checkpoint 路由映射

完成前置准入后，根入口必须读取 `.autobizdevops/state.json` 中当前 Feature 的 checkpoint，并按下表路由到对应阶段入口。根入口只负责选择 `/autobiz` / `/autodev` / 
`/autoops`，不得直接跳入阶段内部子技能；阶段入口会继续按自身 `SKILL.md` 的 checkpoint 映射路由到具体子技能。

### Checkpoint 更新命令

所有阶段推进 checkpoint 时，必须使用统一脚本更新 `.autobizdevops/state.json`，不得手工修改 `state.json` 或生成视图 `STATE.md`。脚本会同步重生 `.autobizdevops/STATE.md`，并在写入前复用 checkpoint 流转、Autodev 产物和 `code_done` 编译校验。

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint {checkpoint}
```

| Checkpoint | 根路由目标 | 说明 |
|------------|------------|------|
| `discuss_in_progress` | `/autobiz` | 恢复需求澄清 |
| `discuss_done` | `/autobiz` | 继续生成 PRD |
| `prd_in_progress` | `/autobiz` | 恢复 PRD 生成 |
| `prd_done` | `/autodev` | 进入 Dev 计划阶段 |
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
| `needs_fix` | 停止自动路由 | 读取最近阶段报告中的建议回流阶段并提示用户 |

若 checkpoint 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature，不得猜测路由。
