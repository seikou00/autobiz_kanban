# Agents 加载：服务单元 + session_context 注入

把后端系统的 `AGENTS.md` 知识库带入特性会话。整条链路分两段：**拉取**（同步知识库仓库、产出可选服务单元）与**注入**（按 createFeature 选中的服务单元注入对应系统的 `AGENTS.md`）。本插件实现两个脚本与一份清单约定。

## 总览

```
                 ┌── sync_agents.py（UI 触发）──────────────┐
 agents git 仓库 ─┤ git clone/pull → <pluginPath>/sys/        │→ stdout JSON
 (含清单+AGENTS.md)│ 解析 agents.manifest.json                │   宿主合并进 board.json
                 └──────────────────────────────────────────┘        │
                                                                      ▼
                                                       UI 渲染服务单元 + AGENTS.md Ready
                                                                      │ 用户在 createFeature 选择
                                                                      ▼
              ┌── render_session_context.py ─────────────────────────┐
  --selected-serviceUnit '[{serviceUnitId,localRepoPath}]'           │
              │ serviceUnitId → systemId → sys/<systemId>/AGENTS.md  │→ {ok,message,inlineSystemPrompt}
              └──────────────────────────────────────────────────────┘   注入项目模式系统提示词
```

映射关系：一个 **systemId（系统编号）拥有多个 serviceUnitId（后端单元 id）**；**`AGENTS.md` 按 systemId 维护**。例：系统 `LF39` 下有 `LF39.18_Outservice`、`LF39.20_Inservice`，共用 `sys/LF39/AGENTS.md`。

## 约定一：清单 JSON（你维护在 agents 仓库根）

文件名 `agents.manifest.json`，放在 agents 仓库根；克隆后位于 `<pluginPath>/sys/agents.manifest.json`。完整示例见 [examples/agents.manifest.example.json](examples/agents.manifest.example.json)。

```json
{
  "schemaVersion": "autobizdevops.agents.manifest.v1",
  "systems": [
    {
      "systemId": "LF39",
      "systemName": "外联服务系统",
      "agents": "LF39/AGENTS.md",
      "serviceUnits": [
        { "serviceUnitId": "LF39.18_Outservice", "name": "外联出站服务" }
      ]
    }
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schemaVersion` | 否 | 默认 `autobizdevops.agents.manifest.v1` |
| `systems[].systemId` | 是 | 系统编号，全清单唯一；默认对应 `sys/<systemId>/AGENTS.md` |
| `systems[].systemName` | 否 | 展示名 |
| `systems[].agents` | 否 | 自定义 `AGENTS.md` 相对路径（相对 `sys/`，禁止绝对路径/`..` 越界），缺省 `<systemId>/AGENTS.md` |
| `systems[].serviceUnits[].serviceUnitId` | 是 | 后端单元 id，**全局唯一**（= UI 传入的 `serviceUnitId`） |
| `systems[].serviceUnits[].name` | 否 | 展示名 |

agents 仓库布局：

```
agents-repo/
  agents.manifest.json
  LF39/AGENTS.md
  LA64/AGENTS.md
```

> ⚠️ 这些 `AGENTS.md` 只停留在插件托管的 `<pluginPath>/sys/` 缓存，**不会写入用户选中的 localRepoPath**，从而避开 devclaw 自动加载 + 插件双加载冲突。

## 约定二：仓库地址配置

在 `board_core/board_config.json` 顶层配置（`sync_agents.py` 读取，可被 `--repo-url`/`--ref` 覆盖）：

```json
"agentsRepo": { "url": "https://git.example.com/agents-kb.git", "ref": "main" }
```

## 脚本一：sync_agents.py（拉取，UI 触发）

注册命令（`board_config.json` 的 `inspectCommands`，三平台均已加）：

```
"sync_agents": "python3 ${pluginPath}/hooks/sync_agents.py"
```

行为：`<pluginPath>/sys/.git` 存在则 `git fetch` + `reset --hard FETCH_HEAD` + `clean -fd`，否则 `git clone`；随后解析清单并整形输出。**stdout JSON（宿主据此把 `supported_service_units` 合并进 board.json）**：

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
      "serviceUnits": [ { "serviceUnitId": "LF39.18_Outservice", "name": "外联出站服务" } ]
    }
  ]
}
```

失败（未配置 url / 无 git / 克隆失败 / 清单非法）输出 `{ "ok": false, "message": ..., "errors": [...] }` 且 **exit 0**（UI 直调约定，绝不返回非 JSON）。

## 脚本二：render_session_context.py（注入）

注册命令：

```
"session_context": "python3 ${pluginPath}/hooks/render_session_context.py --selected-serviceUnit ${selectedServiceUnits}"
```

入参 `--selected-serviceUnit` 是 JSON 数组字符串（与 createFeature 同源）：

```
'[{"serviceUnitId":"LF39.18_Outservice","localRepoPath":"/repo/out"}]'
```

输出（固定形状，注入项目模式系统提示词）：

```json
{ "ok": true, "message": "匹配 1 个服务单元 / 1 个系统知识库；未匹配 0，缺 AGENTS.md 0",
  "inlineSystemPrompt": "项目模式已为当前特性绑定...（路径绑定 + 各系统 AGENTS.md，按 systemId 去重拼接）" }
```

降级（**任何情况不抛异常中断会话**）：

- 空选择 → `ok:true`，`inlineSystemPrompt:""`。
- 未知 serviceUnitId → 仍把 `localRepoPath` 作为运行时路径绑定写入，但无知识库段。
- 系统缺 `AGENTS.md` → 跳过该系统知识库段。
- 清单缺失 → 仅绑定路径、不注入知识库。
- `--selected-serviceUnit` JSON 非法 → 唯一返回 `ok:false` 的情况。

## 代码位置

- 共享模块（清单 schema / 缓存布局 / 校验 / 整形，唯一事实来源）：`hooks/agents_repo.py`
- 拉取脚本：`hooks/sync_agents.py`
- 注入脚本：`hooks/render_session_context.py`
- 缓存目录 `sys/` 已加入 `.gitignore`
- 测试：`tests/test_agents_manifest.py`、`tests/test_render_session_context.py`、`tests/test_sync_agents.py`
