# autodev-plan 死循环：根因、缺口与修复方案

- 分析日期：2026-08-27
- 证据来源：4 份 `/autodev-plan` 会话导出 + 1 份 `/autodev-specs` 会话导出（Feature `test_ruoyi_dev`，工作区 `ruoyi-vue-pro`）
- 运行插件：`~/.cmbcoworkagent/plugins/AutobizDevOps_Plugin_Kanban_latest`（打包时间 19:18，与本仓 21:13 之前的状态一致）
- 状态：**只出方案，未落盘**。本仓 `hooks/plan_writer.py`、`hooks/validation_capabilities.py`、`skills/autodev/autodev-plan/SKILL.md` 正在被另一会话改动，本文第 4 节已把对方改动作为基线剥离，第 5 节起才是待实施内容。

---

## 1. 结论

死循环不是 Plan 逻辑写坏了，而是 **Runtime 验证能力契约（`.runtime/VALIDATION_CAPABILITIES.json`）是一份只在 Feature 会话启动时生成一次的快照，出错后没有任何模型可执行的出口**。所有错误路径最终都收敛到一句「修复：重新启动 Feature」——而"重新启动 Feature"在 Plan 阶段不是模型能执行的动作，模型只能在「手改 catalog → digest 失败 → 删 Draft 重建 → 详情丢失 → 重填 → 回到原点」之间空转，直到上下文耗尽。

四个会话的空转量：

| 会话 | 消息数 | writer 调用 | preflight | finalize | prepare/rebuild | `rm -rf draft` | 手改 catalog |
| --- | --- | --- | --- | --- | --- | --- | --- |
| session-2026-08-27 2 | 291 | 51 | 14 | 4 | 5 / 3 | 5 | 8 |
| session-2026-08-27-2 | 181 | 24 | 2 | 5 | 1 / 1 | 0 | 2 |
| session-2026-08-27-3 | 136 | 17 | 4 | 0 | 0 / 5 | 0 | 0 |
| session-2026-08-27-4 | 159 | 33 | 1 | 4 | 2 / 1 | 1 | 0 |

四个会话 100% 卡在同一个 reason：`validation_capability_unresolved / lane=frontend`。

---

## 2. 根因链

```
① 部署单元只登记了后端仓库根（LB3920_ruoyi_backend → /…/ruoyi-vue-pro）
        ↓  RUN_CONTEXT.modules = [仓库根]
② capability 发现只扫「模块根目录」一层：根目录只有 pom.xml
        ↓  catalog = [ mvn compile @ "." ]，无任何 package.json 能力
③ Feature 是 full_stack，Plan 有 frontend lane 任务
        ↓  _apply_runtime_validation_profiles 找不到 frontend 能力
④ 报 validation_capability_unresolved，repairable=false，
   detail 只有 "lane=frontend;inspect module manifests/toolchains"
        ↓  没有任何可执行动作
⑤ 模型手写 catalog → "catalog digest 不匹配；修复：重新启动 Feature"
        ↓  重开 Feature 不可执行
⑥ 模型删 Draft 重建 → 12 个 task detail 全部丢失 → draft_task_not_ready
        ↓  重填 12 个 detail（每轮 ~30 条消息）
⑦ 回到 ③
```

### 关键放大器（让环转得更快、更贵）

**A. 修了也没用的反馈。** 会话 `2` 第 82 条消息里模型执行了 `npm install -g pnpm`（耗时 43s），第 84 条重跑 preflight —— 报错一字未变。因为 catalog 是启动快照，装完工具不会重算。模型由此得到「装工具无效」的错误结论，转向更激进的乱试：写 `vite.config.ts` 进业务仓库（第 94 条）、`finalize --force`（第 88 条）、删 Draft（第 126 条）。

**B. 自校验文件诱导手改。** `catalogDigest` 是对 `{schemaVersion, contextDigest, capabilities}` 排序后整体 sha256。模型看到「digest 不匹配」，自然去重算 digest，但算错了 8 次（第 118–124、226、268 条）。一个「模型能读、能写、但写了必然失败、且没有生成命令」的文件，是死循环发生器。

**C. 反馈时机太晚。** frontend lane 有没有可用能力，在 `prepare-task-draft` 之前（分组表里 `uiRequired` 已知）就能判定。现在拖到 12 个 task detail 全部写完后的 `preflight-task-draft` 才报，每次失败的重试成本被放大到几十条消息。

**D. 机械阻断缺位。** SKILL.md 写了「不得删除 Draft」，但四个会话里模型删了 6 次，全部成功。`rm -rf .tmp/plan_writer/draft` 没有任何 hook 拦截。**同时 `runtime_artifact_guard.py` 事实上没有生效**：会话 `2` 第 122 条用 `edit_file` 直接改 `.runtime/VALIDATION_CAPABILITIES.json` 返回 `Successfully replaced 1 occurrence(s)`，而该文件在 `PROTECTED_RUNTIME_FILES` 里。需排查 `PLUGIN_WORKSPACE` / `PROJECT_DIR` / `FEATURE_ID` 三个环境变量在该宿主下是否注入（`_feature_root()` 返回 None 时 guard 直接放行）。

---

## 3. 沙箱复现

复现脚本：`autoDevDoc/plan阶段死循环复现脚本.py`（`python3` 直接运行，自建临时 git 仓库，不触碰任何真实 Feature）。仓库根放 `pom.xml`，`yudao-ui/yudao-ui-admin-vue3/` 放带 `build`/`typecheck` 脚本的 `package.json`，RunContext 只登记仓库根为唯一模块。

复现结果与真实会话逐字对齐 —— 生成的后端能力 ID 就是真实会话 catalog 里的 `CAP-F3E7E059F421`（`mvn compile @ "."`，moduleId `LB3920_ruoyi_backend`，repositoryId `repo-01`）。三种情形：

| 情形 | 结果 |
| --- | --- |
| A：递归发现 + mvn/npm 都在 PATH | catalog 含 `mvn compile @ .` / `npm run build @ yudao-ui/yudao-ui-admin-vue3` / `npm run typecheck @ …`，profiles 正常生成 |
| B：catalog 生成时 npm 不在 PATH | 只有 `mvn compile`，报 `validation_capability_unresolved / lane=frontend` |
| B2：事后装上 npm，不重算 catalog | 报错与 B 完全一致 ← **真实会话的处境** |
| C：手改 catalog 补一条能力 | `SCOPE_UNRESOLVED / catalog digest 不匹配；修复：重新启动 Feature` |
| D：单仓两个前端应用 | `validation_capability_ambiguous`，终态且重开 Feature 无法解决（见第 6 节 R1） |

---

## 4. 已有基线（另一会话已改，不要重复做）

截至 21:18 磁盘状态：

| 项 | 位置 | 内容 |
| --- | --- | --- |
| A1 递归发现 manifest | `validation_capabilities._manifest_roots` | `os.walk` 模块根，深度 ≤5，跳过 `node_modules`/`target`/`dist` 等，避开嵌套 module root；`cwd` 改为相对仓库根 |
| A2 包管理器识别 | `validation_capabilities._package_manager` | 增加 `package.json#packageManager` 优先 |
| A3 错误终态化 | `plan_writer._runtime_scope_error` | 统一带 `repairable:false` / `retryable:false` / `requiredAction:restart_feature_after_runtime_fix` / 中文 `repairSuggestion` |
| A4 多候选判定 | `plan_writer._preferred_runtime_capability` | 同模块多 `cwd` 候选时报 `validation_capability_ambiguous` |
| A5 技能侧指令 | `autodev-plan/SKILL.md`「Runtime 验证契约」段（v1.9.0827） | 遇终态错误报告后停止，不重跑、不 `--force`、不编辑 `.runtime`、不删 Draft |
| A6 批次命令拒绝 | `_cmd_add_batch_validation_command` | Runtime 模式下的拒绝也走终态结构 |

A1 解决了根因链的第 ②步（前端子目录发现不了），A3/A5 抑制了模型的重试冲动。**但第 ⑤步的出口仍然只有「重开 Feature」，且 A4 新引入了一个更硬的死路（见第 6 节 R1）。**

---

## 5. 待实施缺口与补丁

### G1【必须】给 catalog 一条机器可执行的重算通道

**问题**：catalog 陈旧（工具后装、目录后建、manifest 后改、digest 被破坏）唯一出口是重开 Feature 会话，模型无法执行，用户也不知道要做什么。

**方案**：`hooks/validation_capabilities.py` 增加 `refresh` 子命令。

```bash
python "${pluginPath}/hooks/validation_capabilities.py" refresh --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
```

行为：加载 `RUN_CONTEXT.json`（失败按原文案报 `SCOPE_UNRESOLVED` 并保持终态）→ `discover()` → 与旧 catalog 比对 → 原子写入 → 输出：

```json
{
  "ok": true,
  "changed": true,
  "contextDigest": "66b4642e…",
  "catalogDigest": "…",
  "added": [{"capabilityId": "CAP-5C3E0DC1E42F", "kind": "build", "cwd": "yudao-ui/yudao-ui-admin-vue3", "argv": ["npm", "run", "build"], "source": "package.json#scripts.build"}],
  "removed": [],
  "unavailable": [],
  "lanes": {"backend": 1, "frontend": 2}
}
```

**信任边界不破坏**：`refresh` 只从磁盘 manifest 机器派生，模型无法注入任意 argv/cwd，等价于把「重开会话时本来就会发生的那次 discover」显式暴露出来。写入仍走 `atomic_write_json`，`.runtime` 仍禁止模型直接编辑。

实现约束：该文件为 py2 风格（`from __future__ import print_function`、`.format()`、无类型注解），新增代码保持一致；Python 目标版本 3.7.3。

### G2【必须】错误分级：可重算 vs 真终态

`plan_writer._runtime_scope_error` 目前把所有 Runtime 错误压成同一条终态文案。拆成两级：

| 触发 | requiredAction | retryable | repairSuggestion |
| --- | --- | --- | --- |
| catalog 不存在 / digest 不匹配 / 与 RunContext 不一致 | `refresh_validation_capabilities` | `true`（`retryLimit: 1`） | 给出上面的 `refresh` 完整命令，并说明「refresh 成功后只允许重跑一次 preflight」 |
| refresh 后仍无该 lane 能力 | `report_to_user_and_stop` | `false` | 见 G3 分情形文案 |
| 模块根失效 / RunContext 无法解析 | `restart_feature_after_runtime_fix` | `false` | 保持现文案 |

`validation_capabilities.load()` 内四条 `ValueError` 文案里的「修复：重新启动 Feature。」同步改为指向 `refresh` 命令；只有 RunContext 本身不可解析时才保留「重新启动 Feature」。

### G3【必须】把「为什么没有能力」写进错误里

**问题**：`discover()` 里 `shutil.which(manager) is None` 直接 `continue`，工具缺失被静默吞掉。模型看到的只有「lane=frontend 找不到能力」，无法区分「目录没登记」和「工具没装」，只能乱试。

**方案**：`discover()` 增加 `unavailable` 数组并纳入 `catalogDigest` 计算：

```python
{"cwd": "yudao-ui/yudao-ui-admin-vue3", "manifest": "package.json",
 "tool": "pnpm", "reason": "tool_not_on_path", "scripts": ["build", "typecheck"]}
```

`_apply_runtime_validation_profiles` 报错时拼进 detail，并按情形给文案：

- `unavailable` 命中该 lane → `lane=frontend;found=yudao-ui/yudao-ui-admin-vue3:package.json#scripts.build;blocked=pnpm_not_on_path`，建议「请用户安装 pnpm 后重跑 refresh」。
- `unavailable` 为空 → `lane=frontend;modules=…;scanned=<manifest 根数量>`，建议「部署单元可能遗漏前端仓库；或本 Feature 应为 backend_only，请与用户确认后修改 `IMPLEMENTATION_SCOPE.json`」。

### G4【必须】把 lane×capability 判定前移到分组阶段

**问题**：12 个 task detail 写完才发现 frontend 没有能力，单次失败成本 ~30 条消息。

**方案**：`preflight-task-groups` / `prepare-task-draft` 增加一次轻量判定 —— 分组表里 `uiRequired` 已经能推出 lane 集合，此时就能比对 catalog。失败时报同样的 reason，但发生在 0 成本处。`preflight-task-draft` 保留原判定作为兜底。

### G5【建议】preflight 同签名失败熔断

**问题**：技能文字约束在上下文压力下失效（四个会话都违反了「不得删除 Draft」）。需要机械熔断。

**方案**：Draft lock 记录 `lastPreflight: {signature, count, firstSeenAt}`，`signature = sha1(reason + "|" + detail)`。

- 同 signature 第 2 次：结果顶层加 `loopGuard: {repeatCount: 2, action: "stop_and_report"}`，message 改成明确停止指令。
- 第 3 次：不再执行校验，直接回显停止指令（避免继续烧上下文）。

配套：`prepare-task-draft` / `rebuild-task-draft` 检测到「Draft 目录被外部删除 且 上次 preflight 是终态 Runtime 错误」时拒绝重建，返回 `draft_rebuild_blocked_by_terminal_runtime_error`。

### G6【建议】补齐机械阻断

1. 排查 `runtime_artifact_guard.py` 为何未生效（会话 `2` 第 122 条 `edit_file` 改 catalog 成功）。重点看该宿主下 `PLUGIN_WORKSPACE`/`PROJECT_DIR`/`FEATURE_ID` 是否注入 —— `_feature_root()` 返回 None 时 guard 静默放行。建议 guard 在环境变量缺失时输出一条可观测的告警，而不是静默返回 0。
2. `.tmp/plan_writer/draft` 纳入保护：拦截 `rm`/`mv` 该目录，提示改用 `rebuild-task-draft` 或先跑 `diagnose-plan-repair`。合法的全量重建走 writer 命令，不走 shell。

---

## 6. 新引入的风险（针对第 4 节基线）

### R1【高】`validation_capability_ambiguous` 是一条比原问题更硬的死路

`_preferred_runtime_capability` 要求「同一模块的 lane 候选能力必须收敛到唯一 `cwd`」，否则报 `validation_capability_ambiguous`（终态、不可重试、建议重开 Feature）。

在当前 checkout 里 `yudao-ui/` 只有 `yudao-ui-admin-vue3` 一个 `package.json`，所以侥幸通过。**真实 ruoyi-vue-pro 上游同时存在 `yudao-ui-admin-vue3`、`yudao-ui-admin-vue2`、`yudao-ui-admin-uniapp`、`yudao-ui-mall-uniapp` 多个前端应用** —— 一旦完整 checkout，frontend lane 必然 ambiguous，而"重开 Feature"解决不了结构性歧义，Plan 直接不可用。

已实测确认（复现脚本情形 D）：仓库放两个前端应用，任务明确声明 `workspaceRoots = yudao-ui/yudao-ui-admin-vue3`，仍然报

```json
{"reason": "validation_capability_ambiguous",
 "detail": "lane=frontend;module=LB3920_ruoyi_backend;candidates=yudao-ui/yudao-ui-admin-vue2,yudao-ui/yudao-ui-admin-vue3",
 "retryable": false, "requiredAction": "restart_feature_after_runtime_fix"}
```

—— 消解歧义所需的信息（任务声明的 workspace root）就在手边却没被使用。

**建议**：歧义用 **lane 任务已声明的 workspace roots** 来消解，而不是报错。lane 的任务里 `scope.workspaceRoots` 已经指明了实际要改的目录：

1. 先筛出 `cwd` 等于或位于该 lane 任一 workspace root 之下的能力；
2. 若唯一 → 选中；
3. 若多个仓库/多个应用都被任务触及 → 每个应用各出一条 required 命令（多条 compile 是正确语义，不是错误）；
4. 只有「任务 workspace roots 与所有候选 `cwd` 都不相交」时才报错，且此时文案应指向 workspace root 配置，而不是"重开 Feature"。

（本仓库自带 44 个 `pom.xml`，backend lane 靠 `at_module_root` 优先规则侥幸收敛到根 pom；一旦部署单元登记的是子模块目录而不是仓库根，backend 也会踩到同样的歧义。）

### R2【中】根目录 `mvn compile` 让批次验证退化为全量编译

Runtime 投影出的 backend 命令是仓库根的 `mvn compile` —— 对 44 模块的 ruoyi 单仓，每个 Batch 验证都要全量编译。而 `add-batch-validation-command` 在 Runtime 模式下已被禁止，Plan 无法再声明 `mvn -pl <module> -am compile` 这类窄化命令（`validation_policy` 里的 `maven_project_selector_workspace_errors` 说明选择器原本是支持的）。

**建议**：capability 选择时优先取「覆盖 lane workspace roots 的最窄 manifest」；或允许 Runtime 在投影时按 workspace root 自动补 `-pl <相对路径> -am`，选择器由 Runtime 生成而非模型编写，可信性不变。

---

## 7. 测试用例清单（`tests/test_runtime_contracts.py`）

| 用例 | 断言 |
| --- | --- |
| `test_nested_frontend_manifest_is_discovered_under_module_root` | 单模块=仓库根 + `yudao-ui/app/package.json` → catalog 含 `cwd=yudao-ui/app` 的 build 能力 |
| `test_missing_toolchain_is_reported_as_unavailable_not_dropped` | `which` 返回 None → `capabilities` 不含该项且 `unavailable` 含 `tool_not_on_path` |
| `test_refresh_recovers_catalog_after_toolchain_install` | 先无 npm 生成 catalog → preflight 失败且 `requiredAction=refresh_validation_capabilities` → `refresh` → preflight 通过 |
| `test_refresh_repairs_hand_edited_catalog_digest` | 破坏 `catalogDigest` → `refresh` 后 `load()` 通过，且手工插入的伪能力被丢弃 |
| `test_multiple_frontend_apps_resolve_by_task_workspace_roots` | 两个前端应用 + 任务只声明其一 → 选中该应用，不报 ambiguous |
| `test_multiple_frontend_apps_all_touched_emit_multiple_commands` | 两个前端应用都被任务触及 → 生成两条 required compile 命令 |
| `test_lane_capability_gate_fires_at_task_group_preflight` | frontend 无能力时 `preflight-task-groups` 即失败，无需写 task detail |
| `test_repeated_identical_preflight_failure_trips_loop_guard` | 同 signature 第 2 次返回 `loopGuard.action=stop_and_report` |
| `test_draft_rebuild_blocked_after_terminal_runtime_error` | 终态错误后删除 Draft 再 `prepare-task-draft` → `draft_rebuild_blocked_by_terminal_runtime_error` |
| `test_runtime_guard_warns_when_feature_env_missing` | 缺 `FEATURE_ID` 时 guard 输出告警而非静默放行 |

---

## 8. 落地提醒（AGENTS.md 规则）

- 改 `skills/autodev/autodev-plan/SKILL.md` 必须同步升版本号（当前 `v1.9.0827`，同日再改升为 `v1.9.08271` 形式的末位递增）。
- 技能正文只写模型需要知道的动作，不写脚本内部逻辑；`refresh` 命令要用绝对路径 + `${pluginPath}` 占位符。
- 新增脚本代码固定按 Python 3.7.3 语法，`validation_capabilities.py` 保持现有 `.format()` 风格。
- 错误返回必须带可执行的修复方式 —— 这正是本次死循环的直接成因：`inspect module manifests/toolchains` 和「重新启动 Feature」都不是模型能执行的动作。
