# 拉取知识库方案补充 · remote/local 解析 + agentmdPrompt 组成

> 本文是对 [agents-service-units.md](agents-service-units.md) / [agents-loading-handoff.md](agents-loading-handoff.md) 的 **v2 增量**，只描述「与现状不同的部分」，未提及处沿用原方案。
> 触发场景：UI 在 `session_context`（createFeature 选中发布单元）时，把每个选中单元的「适用范围 + 系统级 AGENTS.md + 单元级 description.md」拼成一份 `agentmdPrompt` 注入项目模式系统提示词，并回传**每个 md 文件的加载状态**。
>
> ✅ **设计已全部锁定，可落代码**：清单 schema（§2）、来源解析 remote→local→miss（§3）、local 兜底文件 = `<localRepoPath>/AGENTS.md`、`agentmdPrompt` 三段组成（§4）、返回结构 + 状态去重（§5）、`inlineSystemPrompt → agentmdPrompt` 直接切（§6）。

## 1. 变更总览（现状 → v2）

| 维度 | 现状（已实现） | v2 补充 |
| --- | --- | --- |
| 清单 schema | `autobizdevops.agents.manifest.v1`；系统级 `agents`；`systemName`/`name` | `v1`；系统级 `agentsPath`；新增**单元级 `agentsPath`**；展示名统一 `description` |
| 注入正文 | 单段：按 systemId 去重的 AGENTS.md | **三段**：①适用范围（json 引用范围 + UI localRepoPath）②系统级 AGENTS.md ③各单元 description.md |
| 来源解析 | 只有 git 克隆（`sys/`） | 每个 md 文件 **remote 优先 → local 兜底 → 都无 loaded:false** |
| 返回结构 | `{ ok, message, inlineSystemPrompt }` | `{ ok, message, agentmdPrompt, agentmdLoadStatus[] }` |

> 拉取段（`sync_agents.py`）**不变**；本补充只动**注入段**（`render_session_context.py`）与**清单 schema**。

## 2. 清单 schema（git 仓库根 `agents.manifest.json`）

```json
{
  "schemaVersion": "v1",
  "systems": [
    {
      "systemId": "LF39",
      "description": "外联服务系统",
      "agentsPath": "LF3905/AGENTS.md",
      "serviceUnits": [
        { "serviceUnitId": "LF39.18_Outservice", "description": "外联出站服务",
          "agentsPath": "LF3918中台导航/Backend/services/focusone/descition.md" },
        { "serviceUnitId": "LF39.20_Inservice",  "description": "外联入站服务", "agentsPath": "" }
      ]
    },
    {
      "systemId": "LA64",
      "description": "UEX 网关系统",
      "agentsPath": "shared/gateway/AGENTS.md",
      "serviceUnits": [
        { "serviceUnitId": "LA64.05_UEXgateway", "description": "UEX 网关", "agentsPath": "" }
      ]
    }
  ]
}
```

| 字段 | 必填 | 含义 / 用途 |
| --- | --- | --- |
| `schemaVersion` | 否 | 默认 `v1` |
| `systems[].systemId` | **是** | 系统编号，全清单唯一 |
| `systems[].description` | 否 | 系统展示名 → §4 ① 适用范围的「引用范围」 |
| `systems[].agentsPath` | 否 | **系统级** AGENTS.md 相对路径 → §4 ② |
| `serviceUnits[].serviceUnitId` | **是** | 后端单元 id（= UI 传入），**全局唯一** |
| `serviceUnits[].description` | 否 | 单元展示名 → §4 ① 适用范围的「引用范围」 |
| `serviceUnits[].agentsPath` | 否 | **单元级** description.md 相对路径 → §4 ③；为空则该单元无 ③ 段 |

**安全约束**（沿用现状 `_safe_join`）：`agentsPath` 不得为绝对路径、不得含 `..` 越出 `sys/`；违反按「remote 取不到」处理，转 local 兜底。

## 3. 来源解析：每个 md 文件 remote 优先 → local 兜底

§4 的 ② ③ 每个 md 文件，按下面顺序定来源，并各产出**一条**加载状态（§5）：

```
resolve(owner_uid, rel_in_manifest, localRepoPath):
    # ① remote：清单里有该路径 且 sys/ 下文件存在 → 用 remote，忽略 local
    if rel_in_manifest:
        abs = safe_join(sys_root, rel_in_manifest)
        if abs.is_file() and nonempty(abs):
            return status(owner_uid, "sys/"+rel_in_manifest, loaded=True,  source="remote", "")

    # ② local 兜底：未命中清单 或 remote 文件缺失
    local_abs = localRepoPath + "/AGENTS.md"        # local 兜底直接读用户仓库既有 AGENTS.md
    if local_abs and local_abs.is_file() and nonempty(local_abs):
        return status(owner_uid, str(local_abs), loaded=True,  source="local",  "")

    # ③ 都没有
    return status(owner_uid, str(local_abs or ""), loaded=False, source="local", "file not exist")
```

**「忽略本地 md」被精确为**：remote **成功**时才忽略 local；remote 失败（文件缺/路径非法）或不在清单 → local 兜底。与原话「git 仓库有就用 remote、没有就用 local」一致，「没有」涵盖「不在清单」与「在清单但文件缺」。`source` = 内容实际来源（成功）或最后尝试来源（失败恒 `local`）。

## 4. `agentmdPrompt` 组成（注入正文格式）

一份拼接 Markdown，**按顺序三段**：

**① 适用范围**（绑定表，来自清单 json + UI）
对每个选中单元列「引用范围 → serviceUnitId → 代码地址」：**引用范围**取清单里该单元的 `description`；**代码地址取 UI `--selected-serviceUnit` 里的 `localRepoPath`**（不是清单里的路径），提供运行时真实代码地址，覆盖系统级 AGENTS.md 内写死的静态工程地址。**serviceUnitId 用 Markdown 锚点链接 `[id](#anchor)` 指向下方对应段**：有 ③ 单元段→指向单元段，否则→指向 ② 系统段，无内容→纯文本不加链接。

**② 系统级 AGENTS.md**（每个涉及系统一份，**按 systemId 去重**）
选中单元所属系统 `system.agentsPath` 的全文。例：LF39 → `sys/LF3905/AGENTS.md`（即「LF3905 统一 Agent 指令」全文）。

**③ 各单元 description.md**（每个选中单元一份，**按选择顺序**）
每个选中 `serviceUnit.agentsPath`（= description.md）的全文；该字段为空的单元跳过此段（其规则由 ② 覆盖）。

②③ 各内容段用 **Markdown 二级标题 + 显式 `<a id>` 锚点**分隔（**不用 `=====` 围栏**）；锚点 id 约定：系统段 `sys-<systemId>`、单元段 `unit-<serviceUnitId>`（显式 `<a id>`，对 CJK/点号/下划线稳定）。只有 `loaded:true` 的内容进正文。

正文模板：

```markdown
# 适用范围
项目模式已为当前特性绑定以下引用范围与本机代码地址……（serviceUnitId 链接指向下方对应段）：

| 引用范围 | serviceUnitId | 代码地址（localRepoPath） |
| --- | --- | --- |
| 外联出站服务 | [LF39.18_Outservice](#unit-LF39.18_Outservice) | D:/repo/out |
| 外联入站服务 | [LF39.20_Inservice](#sys-LF39) | D:/repo/in |

## <a id="sys-LF39"></a>系统级 AGENTS.md：LF39（外联服务系统）

<sys/LF3905/AGENTS.md 全文>

## <a id="unit-LF39.18_Outservice"></a>服务单元 description.md：LF39.18_Outservice（外联出站服务）

<sys/LF3918中台导航/Backend/services/focusone/descition.md 全文>
```

> `LF39.20_Inservice` 无独立 `description.md`，故其 serviceUnitId 回退链接到 `#sys-LF39`。
> ⚠️ 注：被嵌入的 AGENTS.md / description.md **自带 `#`/`##` 标题**，会与结构标题 `## 系统级…/服务单元…` 同级交错（这是去掉 `=====` 围栏的固有代价）。结构标题带 `系统级 AGENTS.md：`/`服务单元 description.md：` 前缀可区分；若要更干净可后续把嵌入内容标题整体降级，但会改写原文，暂不做。

## 5. 返回结构（本轮锁定）

```json
{
  "ok": true,
  "message": "remote 2 / local 1 / 缺 1",
  "agentmdPrompt": "（§4 三段拼接后的结果）",
  "agentmdLoadStatus": [
    { "serviceUnitId": "LF39.18_Outservice", "path": "sys/LF3905/AGENTS.md",
      "loaded": true,  "source": "remote", "message": "" },
    { "serviceUnitId": "LF39.18_Outservice", "path": "sys/LF3918中台导航/Backend/services/focusone/descition.md",
      "loaded": true,  "source": "remote", "message": "" },
    { "serviceUnitId": "web", "path": "/repo/web/AGENTS.md",
      "loaded": false, "source": "local",  "message": "file not exist" }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `agentmdPrompt` | 取代现状 `inlineSystemPrompt`；§4 三段拼接，注入项目模式系统提示词 |
| `agentmdLoadStatus[]` | 一条对应注入正文里**实际加载/尝试的一个 md 文件**，**与正文一样去重**：每个系统的 AGENTS.md 记一条、每个单元的 description.md 记一条。系统级 AGENTS.md 跨同系统多个单元**只记一条**（归属该系统**首个被选中单元**）。单选一个单元时它产生两条（系统级 + 自身 description.md），故同一 `serviceUnitId` 可出现两次（与你给的示例一致） |
| └ `serviceUnitId` | 该 md 文件归属的选中单元 |
| └ `path` | remote → `sys/` 前缀展示路径；local → 本机绝对路径；都无 → 尝试的 local 路径（或空串） |
| └ `loaded` | 是否成功加载到非空内容（仅 true 的进正文） |
| └ `source` | `"remote"` \| `"local"`——实际来源（成功）或最后尝试来源（失败恒 `local`） |
| └ `message` | 单条说明；失败固定 `"file not exist"`，成功为空 |
| `message`（顶层） | 汇总计数，如 `remote N / local M / 缺 K` |

> **「正文只拼一次」**：多个选中单元同属一个系统时，②该系统 AGENTS.md 在 `agentmdPrompt` 正文里**只出现一次**（按 systemId 去重，不随单元数重复粘贴）；`agentmdLoadStatus` 同样去重——这份共享文件**只记一条**，归属该系统首个被选中单元（§8 #6 选 B）。

## 6. 迁移影响（✅ 已实现）

| 文件 | 改动 |
| --- | --- |
| [hooks/agents_repo.py](../hooks/agents_repo.py) | `ServiceUnit` 增 `agents_rel`；`parse_manifest` 接受 `agentsPath`/`description`/`schemaVersion:"v1"`（**向后兼容**：旧 `agents`/`systemName`/`name` 仍解析）；新增 `index_unit_pairs`（serviceUnitId →(system,unit)）、`sys_abspath`/`sys_display` |
| [hooks/render_session_context.py](../hooks/render_session_context.py) | 按 §3+§4 重写：①适用范围 + 逐文件 remote→local→miss 解析 ②③；输出 `inlineSystemPrompt → agentmdPrompt` + `agentmdLoadStatus[]`（B 去重） |
| [hooks/sync_agents.py](../hooks/sync_agents.py) | 无改动；解析新字段由 `agents_repo` 承担，`build_sync_payload` 输出键（`systemName`/`name`）保持稳定，`supported_service_units` 链路不变 |
| 测试 | `tests/test_render_session_context.py` 全量改为新契约；`tests/test_agents_manifest.py` 增 v1 schema + `index_unit_pairs` 用例。**34 个全过**（旧 sync/manifest 测试因向后兼容仍绿） |

**兼容**：`inlineSystemPrompt → agentmdPrompt` 是**破坏性契约变更**。**已直接切**，不保留 `inlineSystemPrompt` 别名；宿主须同步把读取字段从 `inlineSystemPrompt` 改为 `agentmdPrompt`，否则注入失效。清单 schema 反之**向后兼容**：旧字段名仍可解析，存量 `agents.manifest.json` 不必立即迁移。

## 7. 降级（沿用「绝不中断会话」）

- 空选择 → `ok:true`，`agentmdPrompt:""`，`agentmdLoadStatus:[]`。
- 清单缺失/非法 → 所有单元退化为「未命中」直接走 local 兜底（不报错）。
- 某 md 文件 remote 命中但缺失/路径非法 → 回退 local。
- remote 与 local 都无 → 该条 `loaded:false`/`source:"local"`/`message:"file not exist"`，跳过其内容、计入顶层 message。
- 仅 `--selected-serviceUnit` JSON 非法 → 唯一返回 `ok:false`。

## 8. 待确认 / 待详细设计

| # | 项 | 状态 / 默认 | 归属 |
| --- | --- | --- | --- |
| 1 | **local 兜底文件名** | ✅ 已定：直接读 `<localRepoPath>/AGENTS.md`（注：若该 localRepoPath 同时被 devclaw 自动加载同名文件，会与本注入重复，属已知权衡） | — |
| 2 | **系统级 + 单元级都注入**（非二选一） | ✅ 已定：②系统级 AGENTS.md + ③各单元 description.md 都拼 | — |
| 3 | **拼接顺序**：①适用范围 → ②系统级(去重) → ③各单元 description.md | ✅ 已定 | — |
| 4 | **`inlineSystemPrompt` 兼容** | ✅ 已定：直接切，不留别名 | UI/宿主 |
| 5 | **schemaVersion 命名**：`v1` vs `autobizdevops.agents.manifest.v1` | 采用 `v1`，解析放宽为「缺省即 v1」 | git 仓库同事 |
| 6 | **`agentmdLoadStatus` 中系统级 AGENTS.md 的归属** | ✅ 已定（B）：跨同系统多个单元**只记一条**（与正文一样去重），归属该系统首个被选中单元 | — |
| 7 | **「引用范围」取值** | ✅ 已定：取 `description` 展示名，附 `serviceUnitId` | — |
