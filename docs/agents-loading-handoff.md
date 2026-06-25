# Agents 加载功能 · 对接现状文档

> 用途：插件侧已实现，这份文档面向两个对接方——**agents 仓库维护同事**、**UI / 宿主同事**——说明各自需要产出/对接什么。可直接转发。
> 技术细节参考：[agents-service-units.md](agents-service-units.md)；清单示例：[examples/agents.manifest.example.json](examples/agents.manifest.example.json)。
> v2 增量（单元级 `agentsPath`；remote 优先/local 兜底；`agentmdPrompt` 三段＝适用范围＋系统级 AGENTS.md＋各单元 description.md；返回 `agentmdPrompt`/`agentmdLoadStatus[]`）见 [agents-loading-remote-local.md](agents-loading-remote-local.md)（注入段契约变更，落地前需与宿主对齐；仅 local 文件名约定待定）。

## 1. 这个功能在做什么

把后端系统的 `AGENTS.md` 知识库带进特性会话。整条链路分两段：

```
  ┌─────────────── 拉取段（插件已实现）──────────────┐
  │ UI 触发 → sync_agents.py 克隆 agents 仓库到本机    │
  │ → 解析仓库根的 agents.manifest.json               │── stdout JSON ──▶ 宿主合并进 board.json
  └──────────────────────────────────────────────────┘                        │
                                                                               ▼
                                                          UI 渲染「服务单元选择 + AGENTS.md Ready」
                                                                               │ 用户在 createFeature 勾选
                                                                               ▼
  ┌─────────────── 注入段（插件已实现）──────────────┐
  │ createFeature 传 --selected-serviceUnit            │
  │ → render_session_context.py 把选中单元映射到        │── {ok,message,inlineSystemPrompt} ──▶ 注入系统提示词
  │   对应系统的 AGENTS.md，拼成提示词                  │
  └──────────────────────────────────────────────────┘
```

数据模型：**一个系统（systemId / 系统编号）下挂多个后端单元（serviceUnitId）；`AGENTS.md` 按系统维护**。例：系统 `LF39` 下有 `LF39.18_Outservice`、`LF39.20_Inservice`，共用一份 `LF39/AGENTS.md`。

## 2. 现状一览

| 模块 | 责任方 | 状态 | 说明 |
| --- | --- | --- | --- |
| `sync_agents.py` 拉取脚本 | 插件 | ✅ 已完成 | clone/pull + 解析清单 + stdout 输出，已注册三平台命令 |
| `render_session_context.py` 注入脚本 | 插件 | ✅ 已完成 | 选中单元 → AGENTS.md 注入，固定返回结构 |
| 清单 schema `agents.manifest.json` | 插件设计 | ✅ 已定义 | 见 §3，需 git 仓库同事按此产出 |
| 单元测试 | 插件 | ✅ 28 个全过 | 含本地 git 仓库端到端 clone+update |
| **agents 仓库（清单 + AGENTS.md 内容）** | **git 仓库同事** | ⬜ 待产出 | 见 §3 |
| **`agentsRepo.url` 填真实地址** | 待定（建议 UI/宿主） | ⬜ 待填 | 当前为空，空则拉取返回 `ok:false` 引导填写 |
| **board.json 合并 + UI 触发/渲染** | **UI 同事** | ⬜ 待对接 | 见 §4 |
| **inlineSystemPrompt 注入到系统提示词** | UI / 宿主 | ⬜ 待确认 | 见 §4 |

---

## 3. 给「agents 仓库维护同事」

你需要产出**一个 git 仓库**，包含一份清单 + 每个系统一份 `AGENTS.md`。

### 3.1 仓库目录结构

```
agents-kb/                       ← 这个仓库的地址就是 agentsRepo.url
├── agents.manifest.json         ← 清单（必须在仓库根，文件名固定）
├── LF39/
│   └── AGENTS.md                ← 系统 LF39 的知识库（默认路径 = <systemId>/AGENTS.md）
├── LA64/
│   └── AGENTS.md
└── shared/
    └── gateway/AGENTS.md        ← 也可放别处，清单里用 agents 字段指定
```

### 3.2 清单格式 `agents.manifest.json`

```json
{
  "schemaVersion": "autobizdevops.agents.manifest.v1",
  "systems": [
    {
      "systemId": "LF39",
      "systemName": "外联服务系统",
      "serviceUnits": [
        { "serviceUnitId": "LF39.18_Outservice", "name": "外联出站服务" },
        { "serviceUnitId": "LF39.20_Inservice",  "name": "外联入站服务" }
      ]
    },
    {
      "systemId": "LA64",
      "systemName": "UEX 网关系统",
      "agents": "shared/gateway/AGENTS.md",
      "serviceUnits": [
        { "serviceUnitId": "LA64.05_UEXgateway", "name": "UEX 网关" }
      ]
    }
  ]
}
```

### 3.3 字段说明

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `schemaVersion` | 否 | 默认 `autobizdevops.agents.manifest.v1` |
| `systems[].systemId` | **是** | 系统编号，全清单唯一；默认对应 `<systemId>/AGENTS.md` |
| `systems[].systemName` | 否 | 展示名 |
| `systems[].agents` | 否 | 自定义 AGENTS.md 相对路径；**不写则默认 `<systemId>/AGENTS.md`**；禁止绝对路径与 `..` 越界 |
| `systems[].serviceUnits[].serviceUnitId` | **是** | 后端单元 id（= UI 传给插件的 `serviceUnitId`），**全局唯一** |
| `systems[].serviceUnits[].name` | 否 | 展示名 |

### 3.4 三条硬规则（违反则脚本报 `ok:false`）

1. **`systemId` 全清单唯一**。
2. **`serviceUnitId` 跨所有系统全局唯一**（插件靠它一步反查系统；建议沿用 `LF39.18_xxx` 这种带系统前缀的命名，天然唯一）。
3. **`agents` 路径不得越出仓库根**（防目录穿越）。

### 3.5 注意事项

- `AGENTS.md` 内容随意（Markdown），会被原文拼进会话系统提示词，建议写**该系统的开发约束/接口规范/排查要点**。
- 仓库可随时更新；UI 再次触发拉取会 `git fetch + reset --hard` 拉到最新，并清理已删除的旧系统目录。

---

## 4. 给「UI / 宿主同事」

插件已在 `board_config.json` 的 `inspectCommands` 注册两条命令（darwin/linux/win32 都有）。你负责：触发它们、消费它们的 stdout、把结果落到 board.json 与系统提示词。

### 4.1 拉取：`sync_agents` 命令

- **命令**：`python3 ${pluginPath}/hooks/sync_agents.py`（无参，仓库地址读 `board_config.json` 的 `agentsRepo`）。
- **触发时机**：由你决定（建议项目页一个「同步 Agents」按钮）。
- **前置**：需要先把 `board_config.json` 顶层的 `agentsRepo.url` 填成真实仓库地址（**当前为空**）。空时命令返回 `ok:false` 且 message 引导去填。

  ```json
  "agentsRepo": { "url": "https://git.example.com/agents-kb.git", "ref": "main" }
  ```

- **stdout 契约**（你据此合并进 board.json）：

  ```json
  {
    "ok": true,
    "schemaVersion": "autobizdevops.agents.sync.v1",
    "message": "agents 仓库已同步：2 个系统、3 个服务单元，AGENTS.md 就绪 2/2",
    "repo": { "url": "...", "ref": "main", "commit": "<sha>" },
    "supported_service_units": ["LF39.18_Outservice", "LF39.20_Inservice", "LA64.05_UEXgateway"],
    "systems": [
      {
        "systemId": "LF39",
        "systemName": "外联服务系统",
        "agentsReady": true,
        "agentsPath": "sys/LF39/AGENTS.md",
        "serviceUnits": [
          { "serviceUnitId": "LF39.18_Outservice", "name": "外联出站服务" },
          { "serviceUnitId": "LF39.20_Inservice",  "name": "外联入站服务" }
        ]
      },
      { "systemId": "LA64", "systemName": "UEX 网关系统", "agentsReady": true,
        "agentsPath": "sys/shared/gateway/AGENTS.md",
        "serviceUnits": [ { "serviceUnitId": "LA64.05_UEXgateway", "name": "UEX 网关" } ] }
    ]
  }
  ```

  - `supported_service_units`：**扁平数组**，所有系统全部单元；按附件 4.5 语义，这是「可选的发布单元」，**不用来过滤**。
  - `systems[].agentsReady`：磁盘上该系统 `AGENTS.md` 是否存在 → **用来渲染「AGENTS.md Ready」徽标**。可以出现「在列表里但 `agentsReady=false`」（单元能选、知识库还没就绪）。
  - 失败统一返回 `{ "ok": false, "message": ..., "errors": [...] }` 且 **进程 exit 0**（绝不返回非 JSON）。

### 4.2 注入：`session_context` 命令

- **命令**：`python3 ${pluginPath}/hooks/render_session_context.py --selected-serviceUnit ${selectedServiceUnits}`
- **入参**（与 createFeature 同源，附件已约定的 JSON 字符串）：

  ```
  --selected-serviceUnit '[{"serviceUnitId":"LF39.18_Outservice","localRepoPath":"/repo/out"}]'
  ```

- **返回**（固定结构，把 `inlineSystemPrompt` 注入「项目模式系统提示词」）：

  ```json
  { "ok": true,
    "message": "匹配 1 个服务单元 / 1 个系统知识库；未匹配 0，缺 AGENTS.md 0",
    "inlineSystemPrompt": "（路径绑定 + 各系统 AGENTS.md 内容，按系统去重拼接）" }
  ```

- **降级**（绝不中断会话）：空选择 → 空 prompt；未知单元 → 仅绑路径不注入；缺 AGENTS.md → 跳过该系统段；清单缺失 → 仅绑路径。**只有 `--selected-serviceUnit` JSON 非法才回 `ok:false`**。

### 4.3 重要约束

> 插件把 `AGENTS.md` 缓存在插件目录 `<pluginPath>/sys/`，**不会写进用户选中的 `localRepoPath`**，从而避开附件提醒的「devclaw 自动加载 + 插件双加载」冲突。你那边若也要往 `localRepoPath` 下载文档，**切勿命名为 `AGENTS.md`**。

---

## 5. 决策与待确认

### 5.1 已确认的架构决策（已拍板，按此实现）

- **克隆位置**：下到**插件根目录 `<pluginPath>/sys/`**，全机共享一份（不按工作区分别克隆）。
- **选择不落库**：插件**不**把"选了哪些服务单元"写进 `state.json`；每次由 UI 通过 `--selected-serviceUnit` 现传给 `session_context`。
- **仓库地址来源**：配在 `board_config.json` 顶层 `agentsRepo.url/ref`，由 `sync_agents` 读取（`--repo-url/--ref` 仅作兜底覆盖）。
- 连带：`create_feature` **不**接收 `--selected-serviceUnit`（无需持久化），只有 `session_context` 接收；`session_context` 命令也**不需** `--workspace`（靠插件路径定位 `sys/`）。

### 5.2 待确认问题（插件侧的假设，需各方对齐）

| # | 问题 | 插件当前假设 | 找谁确认 |
| --- | --- | --- | --- |
| 1 | `serviceUnitId` 命名 | 全局唯一、带系统前缀（`LF39.18_xxx`） | git 仓库同事 |
| 2 | 系统↔单元层级 | 一系统多单元、一份 AGENTS.md 服务多个单元 | git 仓库同事 |
| 3 | `agents` 字段 | 可选，默认 `<systemId>/AGENTS.md` | git 仓库同事（要不要改必填？） |
| 4 | `board.json` 合并落点 | `supported_service_units` 进哪个字段、`agentsReady` 怎么映射徽标 | UI/宿主 |
| 5 | `sync_agents` 触发入口与时机 | 由 UI 决定（按钮？项目打开时？） | UI/宿主 |
| 6 | `inlineSystemPrompt` 注入位置 | 项目模式系统提示词 | UI/宿主 |

---

## 6. 代码与测试位置（插件内部）

- 共享模块（清单 schema / 缓存布局 / 校验 / 整形，唯一事实来源）：`hooks/agents_repo.py`
- 拉取脚本：`hooks/sync_agents.py`
- 注入脚本：`hooks/render_session_context.py`
- 命令注册 + `agentsRepo`：`board_core/board_config.json`
- 缓存目录 `sys/` 已加入 `.gitignore`
- 测试：`tests/test_agents_manifest.py`、`tests/test_render_session_context.py`、`tests/test_sync_agents.py`

本地自验：

```bash
# 单元测试
python3 -m unittest tests.test_agents_manifest tests.test_render_session_context tests.test_sync_agents -v

# 端到端（用本地临时 git 仓库当 agents 仓库）
python3 hooks/sync_agents.py --repo-url <本地agents仓库路径> --ref main
python3 hooks/render_session_context.py --selected-serviceUnit '[{"serviceUnitId":"LF39.18_Outservice","localRepoPath":"/repo/out"}]'
```
