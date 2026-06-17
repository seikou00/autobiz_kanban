# 看板 UI ↔ 插件交互 · 快速验证手册

本文用于**不依赖真实看板前端**、纯命令行复现 UI 会调用的整条命令链路，验证插件侧
（`inspect_state.py` / `hooks/*.py`）是否正常工作。流程顺序与 UI 一致：
`init_workspace（建项目） → 创建 Feature → 逐阶段拿命令 → 推进 checkpoint`。

所有命令模板都来自 `board_core/board_config.json` 的 `inspectCommands`（darwin / linux /
win32 三套，下文用 darwin）。本文中的「期望输出」均为在本机实跑后的真实结果。

> 维护者注意：当前 `board_config.json` 实际仍有 **两个 profile**（`standard`、
> `frontend_before_specs`），所以 `prd_done` 会触发 profile 选择（见 §4.1）。这与
> `docs/inspect-json-dynamic-workflow.md` 里「只剩 standard、prd_done 不再弹选择」的
> 描述不一致，以本文实测为准。

---

## 0. 四个路径变量（UI 注入，脚本里务必先约定）

UI 在调用任何命令前，会用这四个占位符拼出真实命令。手动验证时先把它们想清楚：

| 占位符 | 含义 | 本文示例值 |
| --- | --- | --- |
| `${pluginPath}` | 插件**代码**根目录（本仓库） | `/Users/seikou/Documents/GitHub/autobiz_kanban` |
| `${pluginWorkspace}` | **项目集合**工作区，下面每个子目录是一个项目 | `$SBX`（临时沙盒） |
| `${projectDir}` | 项目目录名，`${pluginWorkspace}/${projectDir}` 是该项目根 | `demo_proj` |
| `${feature}` | Feature slug | `checkout` |

派生路径（写 skill / 推进 checkpoint 时用，来自 `system_prompt_inject`）：

- `PROJECT_PLUGIN_DIR = ${pluginWorkspace}/${projectDir}` —— 真正含
  `.autobizdevops/state.json` 的目录。
- `FEATURE_DIR = ${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}` ——
  Feature 产物目录，**所有阶段产物都落在这里**。

> 关键区分：`inspect_state.py` / `init_workspace.py` / `skip_node.py` 用 **参数**
> 定位（`--workspace`/`--project`/`--feature`）；`update_checkpoint.py` 用 **环境变量**
> 定位（`PLUGIN_WORKSPACE`/`PROJECT_DIR`/`FEATURE_ID`），并且**显式拒绝** `--workspace`
> 参数。

---

## 1. 一键冒烟脚本（复制即可跑，验证 UI 命令链路本身）

这一段只跑「UI 与插件交互」的纯命令面，不需要任何业务产物，应当全绿。

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="/Users/seikou/Documents/GitHub/autobiz_kanban"     # ${pluginPath}
SBX="$(mktemp -d /tmp/abdo_smoke.XXXXXX)"                  # ${pluginWorkspace}
PROJ="demo_proj"                                          # ${projectDir}
FEAT="checkout"                                           # ${feature}
mkdir -p "$SBX/$PROJ"   # create_project 要求项目目录已存在（它只在里面建 .autobizdevops）

echo "== 1) create_project（即 init_workspace） =="
python3 "$ROOT/hooks/init_workspace.py" --mode createProject --workspace "$SBX" --project "$PROJ"

echo "== 2) feature_status：Feature 尚不存在 → 降级看板（不报错） =="
python3 "$ROOT/inspect_state.py" --mode run --workspace "$SBX" --project "$PROJ" --feature "$FEAT" | head -c 200; echo

echo "== 3) dynamic_workflow：模板 + 自定义节点目录 =="
python3 "$ROOT/hooks/inspect_workflow_templates.py" --mode templates | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print('templates=',[t['id'] for t in d['templates']]);print('nodes=',[n['id'] for n in d['nodes']])"

echo "== 4) create_feature（standard 模板） =="
python3 "$ROOT/hooks/init_workspace.py" --mode createFeature --workspace "$SBX" \
  --project "$PROJ" --feature "$FEAT" --workflow-template standard --workflow-nodes '[]'

echo "== 5) feature_status：真实 run 看板 =="
python3 "$ROOT/inspect_state.py" --mode run --workspace "$SBX" --project "$PROJ" --feature "$FEAT" | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print('run keys=',list(d['run'].keys()));print('currentNodeId=',d['run']['currentNodeId'])"

echo "== 6) 取「当前阶段命令」：推荐 skill + 允许的下一步 =="
python3 "$ROOT/hooks/resolve_next_skill.py" --workspace "$SBX/$PROJ" --feature "$FEAT"

echo "== 7) project_status：项目列表轻量摘要 =="
python3 "$ROOT/inspect_state.py" --mode project --workspace "$SBX" --projects "$PROJ" | python3 -c \
  "import sys,json;d=json.load(sys.stdin);[print(p,'=>',[r['featureId']+':'+r['currentNodeId'] for r in v['runs']]) for p,v in d['projects'].items()]"

echo "ALL GREEN. sandbox: $SBX"
```

跑完应看到 create_project / create_feature 成功，第 6 步输出：

```text
feature: checkout
checkpoint: discuss_in_progress
workflowProfile: standard
recommendedNextSkill: autobiz-requirement-discuss
allowedNextCheckpoints: discuss_done
```

---

## 2. UI 命令逐条说明

下表是 `inspectCommands`（darwin）的全部命令，以及手动验证时的关键点。

### 2.1 `create_project`（建项目 / init_workspace）

```bash
python3 ${pluginPath}/hooks/init_workspace.py --mode createProject \
  --workspace ${pluginWorkspace} --project ${projectDir}
```

- **前置**：`${pluginWorkspace}/${projectDir}` 目录**必须已存在**（它是目标代码库目录），
  脚本只在其中创建 `.autobizdevops/`。目录不存在会报
  `ERROR: Workspace does not exist`。
- **产出**：`features/`、`archive/`、`issues/active`、`issues/completed`、`PROJECT.md`、
  `state.json`、`STATE.md`。
- 期望首行：`Workspace initialized successfully at .../.autobizdevops`。

### 2.2 `create_feature`（建 Feature）

```bash
python3 ${pluginPath}/hooks/init_workspace.py --mode createFeature \
  --workspace ${pluginWorkspace} --project ${projectDir} --feature ${feature} \
  --workflow-template ${workflowTemplate} --workflow-nodes ${workflowNodes}
```

- `--workflow-template`：`standard` | `lean` | `custom`。
- `--workflow-nodes`：**JSON 数组字符串**。
  - `standard` / `lean`：传 `'[]'`（lean 用内置固定子集，`workflowNodes` 记为 `None`）。
  - `custom`：传选中节点，如 `'["dev.specs","dev.code"]'`；创建时会经 closure 求解，
    自动补 `ops.archive`（必含），最终 `workflowNodes=["dev.specs","dev.code","ops.archive"]`。
- 新建后初始 checkpoint：standard 从 `discuss_in_progress`；lean / custom（无 biz.*）从
  `specs_in_progress`。
- 期望首行：`Feature created successfully: .../features/${feature}`。

### 2.3 `feature_status`（单 Feature 看板）

```bash
python3 ${pluginPath}/inspect_state.py --mode run \
  --workspace ${pluginWorkspace} --project ${projectDir} --feature ${feature}
```

- 输出顶层：`{ "workflow": {...}, "run": {...} }`。
- `run` 关键字段：`currentNodeId`、`workflowProfile`、`workflowTemplate`、`workflowId`、
  `workflowDecisions`、`nodes`（每个节点带 `states` + `nextAction.slashSkill`）、
  `watchRefs`、`hookLogRefs`。
- **UI 在每个节点上显示的「下一步命令」就来自该节点 `nextAction.slashSkill`。**

### 2.4 `project_status`（多项目列表）

```bash
python3 ${pluginPath}/inspect_state.py --mode project \
  --workspace ${pluginWorkspace} --projects ${projectDirs...}
```

- `--projects` 可接多个项目名（空格分隔）。
- 输出 `projects` 是 **字典**：`{ "demo_proj": { "runs": [ {featureId, currentNodeId,
  currentNodeStatus, currentNodeStatusLabel, nodeIds}, ... ] } }`。
- 列表页用的轻量摘要，**不扫描每个节点产物**。

### 2.5 `dynamic_workflow`（模板 + 节点目录 / closure 预览）

```bash
# 模板清单 + 自定义可选节点目录（一次返回）
python3 ${pluginPath}/hooks/inspect_workflow_templates.py --mode templates
# 自定义选择的依赖闭包预览（创建前给 UI 算 dropped/suggestions）
python3 ${pluginPath}/hooks/inspect_workflow_templates.py --mode closure \
  --nodes dev.specs,dev.code --template custom
```

- templates 模式返回 `templates`（standard/lean/custom）和 `nodes`（11 个节点目录，
  含 inputs/outputs）。
- closure 模式返回 `nodes`/`added`/`dropped`/`suggestions`/`initialCheckpoints`/
  `transitions`；默认**不**自动补全上游，加 `--auto-include` 才递归拉入 producer。

### 2.6 `skip_node`（中途跳过节点）

```bash
python3 ${pluginPath}/hooks/skip_node.py --plugin-workspace ${pluginWorkspace} \
  --project ${projectDir} --feature ${feature} --skip-node ${nodeId}
```

- 默认输出 JSON。加 `--dry-run` 只校验不写入。
- 只能跳「当前有效链中、未完成、非 locked、当前节点须处于 `*_in_progress`」的节点；
  非法跳过返回 `ok:false` 且 `message` 说明原因。

### 2.7 路由 / 写入两个辅助脚本（UI 间接依赖）

`resolve_next_skill.py` —— 取当前阶段命令（只读）：

```bash
python3 ${pluginPath}/hooks/resolve_next_skill.py \
  --workspace ${pluginWorkspace}/${projectDir} --feature ${feature} [--json]
```

`update_checkpoint.py` —— 推进 checkpoint / 写动态决策（**用环境变量**）：

```bash
PLUGIN_WORKSPACE=${pluginWorkspace} PROJECT_DIR=${projectDir} FEATURE_ID=${feature} \
  python3 ${pluginPath}/hooks/update_checkpoint.py --feature ${feature} \
  --checkpoint <target_checkpoint> [--workflow-profile ...] [--workflow-decision id=enabled|skipped] [--json]
```

> `update_checkpoint.py` 主要由各阶段 skill 在产出产物后调用；手动验证整条链路时
> 也可直接调它。注意它会跑 transition 白名单 + precheck/postcheck（见 §5）。

---

## 3. 每个阶段（standard 主干）的命令一览

「这个阶段该跑什么命令」= 对应节点的 skill；「这个阶段算不算完成」由 **postcheck 必产物**
决定。下表即 standard 模板的 11 个节点：

| # | 节点 | 阶段 slashSkill | 进入 checkpoint | 完成 checkpoint | postcheck 必产物（落在 FEATURE_DIR） |
| --- | --- | --- | --- | --- | --- |
| 1 | `biz.discuss` | `/autobiz-requirement-discuss` | `discuss_in_progress` | `discuss_done` | `PRD_DISCUSS.md` |
| 2 | `biz.prd` | `/autobiz-prd-generate` | `prd_in_progress` | `prd_done` | `PRD.md` |
| 3 | `dev.specs` | `/autodev-specs` | `specs_in_progress` | `specs_done` | `proposal.md`、`specs/**/*.md` |
| 4 | `dev.plan` | `/autodev-plan` | `plan_in_progress` | `plan_done` | `design.md`、`PLAN.md` |
| 5 | `dev.code` | `/autodev-code` | `code_in_progress` | `code_done` | （无文件产物，但有编译 guard） |
| 6 | `dev.review` | `/autodev-reviewer` | `requirements_eval_in_progress` | `requirements_eval_done` | `REQUIREMENTS_EVAL.md` |
| 7 | `dev.utest` | `/autodev-utest` | `unit_test_in_progress` | `unit_test_done` | `UNIT_TEST_REPORT.md`、`test-output.log` |
| 8 | `dev.e2e` | `/autodev-e2e` | `e2e_in_progress` | `e2e_done` | `E2E_TEST_CASES.yaml`、`E2E_REPORT.md`、`e2e-run.log` |
| 9 | `dev.verify` | `/autodev-verify` | `verify_in_progress` | `verify_done` | `VERIFY_REPORT.md` |
| 10 | `ops.cicd` | `/autoops-cicd` | `cicd_in_progress` | `cicd_done` | `CICD_CHECKLIST.md` |
| 11 | `ops.archive` | `/autoops-archive` | — | `archived` | （无） |

> 取某阶段命令的标准做法：`resolve_next_skill.py --json` 看 `nextAction.slashSkill`
> / `recommendedNextSkill`，或从 `feature_status` 的对应节点 `states[].nextAction` 读。

转移图（checkpoint → 允许的下一步）按上表顺序串联；`e2e_in_progress` /
`verify_in_progress` 额外允许 `needs_fix`，`needs_fix` 可回退到各 `*_in_progress`。

---

## 4. 两个必验的「交互分支点」+ 一个跳过

这三处是 UI 真正要弹选择 / 弹按钮的地方，务必单独验证。

### 4.1 `prd_done`：Profile 选择（HTML 转前端分流）

到达 `prd_done` 后查询路由会返回 `requiresProfileChoice: true`：

```bash
python3 ${pluginPath}/hooks/resolve_next_skill.py -w ${pluginWorkspace}/${projectDir} -f ${feature} --json
```

实测 `profileChoices`：

| id | label | 下一步 checkpoint | 推荐 skill |
| --- | --- | --- | --- |
| `standard` | 不需要，进入 autodev-specs | `specs_in_progress` | `autodev-specs` |
| `frontend_before_specs` | 需要，进入 HTML 转前端 | `frontend_in_progress` | `autodev-frontend` |

UI 选定后写入（以 standard 为例）：

```bash
PLUGIN_WORKSPACE=${pluginWorkspace} PROJECT_DIR=${projectDir} FEATURE_ID=${feature} \
  python3 ${pluginPath}/hooks/update_checkpoint.py --feature ${feature} \
  --checkpoint specs_in_progress --workflow-profile standard
```

### 4.2 `plan_done`：动态阶段选择（是否先做详细设计）

到达 `plan_done` 后路由返回 `requiresWorkflowChoice: true`，实测 `workflowChoices`：

| decision | label | 目标 checkpoint | 推荐 skill | 产物 |
| --- | --- | --- | --- | --- |
| `enabled` | 需要，生成详细设计 | `detail_design_in_progress` | `autodev-detail-design` | `DETAIL_DESIGN.md` |
| `skipped` | 不需要，直接编码 | `code_in_progress` | `autodev-code` | — |

UI 选定后，把决策和目标 checkpoint **一次写入**（决策只能在 `plan_done` 写）：

```bash
PLUGIN_WORKSPACE=${pluginWorkspace} PROJECT_DIR=${projectDir} FEATURE_ID=${feature} \
  python3 ${pluginPath}/hooks/update_checkpoint.py --feature ${feature} \
  --checkpoint code_in_progress --workflow-decision detail_design_before_code=skipped
```

写入后 `state.json` 的该 Feature 多出
`"workflowDecisions": {"detail_design_before_code": "skipped"}`，再查路由
`requiresWorkflowChoice` 归 `false`，`nextAction` 指向 `/autodev-code`。

### 4.3 中途 `skip_node`

```bash
# 先 dry-run 看能不能跳
python3 ${pluginPath}/hooks/skip_node.py --plugin-workspace ${pluginWorkspace} \
  --project ${projectDir} --feature ${feature} --skip-node dev.utest --dry-run
# 去掉 --dry-run 实跳；被跳节点在看板仍显示但标 "skipped":true，contracts 不再计入
```

---

## 5. 推进一个阶段会被哪些 gate 拦住（排障必读）

`update_checkpoint.py` 推进时按顺序校验，任一不过都会 `ok:false`：

1. **transition 白名单**：只能走转移图允许的下一步，否则
   `非法转移: A -> B；允许的下一个状态: ...`。
2. **precheck（进入节点）**：该节点 `required` 输入必须在 FEATURE_DIR 存在。
3. **postcheck（离开节点 → `*_done`）**：不仅查文件存在，还查**内容契约**
   （`skills/autodev/hooks/artifact_check.py`）。例如：
   - `specs_done`：`proposal.md` 必含 `Why / What Changes / Capabilities / Impact /
     Out of Scope` 五节；每个 `specs/**/*.md` 必含 `## ADDED Requirements`、
     `### Requirement:`、`#### Scenario:`。
   - `plan_done`：`design.md` 必含 6 节 + `x-auto-no-http-api:` / `x-auto-no-sql:` 标记；
     `PLAN.md` 必含「任务总览 / 任务详情」、≥1 个 `### 1.` 任务、初始状态全为 `待做`。
   - `requirements_eval_done` / `unit_test_done` 等还要 verdict=PASS、覆盖矩阵表等。
4. **code_done 编译 guard**：`dev.code` 带 `code_compile`，进入 `code_done` 前会跑编译，
   失败阻塞转移。

> 因此：纯**空 stub 文件**只能过 1/2 两个 biz 节点（`discuss_done` / `prd_done`）；
> 从 `specs_done` 起需要**格式合法**的产物。下面给前面 §4 验证到 `plan_done` 用的
> 最小合法产物，足以打通 profile / workflow 两个分支点。

<details>
<summary>最小合法产物速查（写进 FEATURE_DIR 后即可走到 plan_done）</summary>

```bash
F="$SBX/$PROJ/.autobizdevops/features/$FEAT"; mkdir -p "$F/specs"
echo x > "$F/PRD_DISCUSS.md"; echo x > "$F/PRD.md"
cat > "$F/proposal.md" <<'EOF'
## Why
## What Changes
## Capabilities
## Impact
## Out of Scope
EOF
cat > "$F/specs/main.md" <<'EOF'
## ADDED Requirements
### Requirement: demo
#### Scenario: happy
- WHEN x
- THEN y
EOF
cat > "$F/design.md" <<'EOF'
## Context / 输入上下文
## Spec Traceability
## API Decisions
x-auto-no-http-api: true
## Data Decisions
x-auto-no-sql: true
## Technical Design
## Risks / Open Questions
EOF
cat > "$F/PLAN.md" <<'EOF'
## 任务总览
## 任务详情
### 1. 首个任务
- **状态:** 待做
EOF
```

`dev.review` / `dev.utest` / `dev.e2e` / `dev.verify` 各有更重的内容校验，整条链
端到端打通需要由对应 skill 真正产出；以 `artifact_check.py` 为准。
</details>

---

## 6. 降级 / 异常行为（也要验证 UI 不崩）

inspect 在状态缺失时**不报进程错误**，而是给降级看板，UI 应能展示「未初始化 / 状态异常」：

| 场景 | 期望行为 |
| --- | --- |
| `state.json` 不存在 | summary 说明项目尚未初始化 |
| `state.json` 无该 Feature | summary 说明没有 Feature 记录（仍返回 workflow 骨架） |
| Feature 目录不存在 | 仍按约定路径扫描，产物显示「未生成」 |
| checkpoint 未知 | summary 说明无法映射到流程节点 |

---

## 7. 收尾

```bash
rm -rf "$SBX"   # 清理临时沙盒
```

---

## 附：命令速查

| 阶段 / 动作 | 命令入口 | 定位方式 |
| --- | --- | --- |
| 建项目 | `hooks/init_workspace.py --mode createProject` | 参数 |
| 建 Feature | `hooks/init_workspace.py --mode createFeature` | 参数 |
| 单 Feature 看板 | `inspect_state.py --mode run` | 参数 |
| 多项目列表 | `inspect_state.py --mode project` | 参数 |
| 模板 / 节点目录 / closure | `hooks/inspect_workflow_templates.py` | 无需定位 |
| 取当前阶段命令 | `hooks/resolve_next_skill.py` | 参数 |
| 推进 / 写决策 | `hooks/update_checkpoint.py` | **环境变量** |
| 中途跳过节点 | `hooks/skip_node.py` | 参数 |
</content>
