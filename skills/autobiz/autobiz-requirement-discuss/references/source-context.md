# 外部资料要求索引

存在 `外部资料与实现约束` 时，在 Feature 目录写 `source-context.json`，原件或快照写入 `sources/SRC-NNN/`。直接写 JSON，不创建额外 writer、文本投影或覆盖报告。

字段含义：`items` 是逐条原文证据，`original` 是复制自快照的原句或原表格行，`targets` 是这条要求必须进入的下游产物，可多选。

```json
{
  "version": 1,
  "sources": [
    {
      "id": "SRC-001",
      "name": "支付网关接口文档",
      "path": "sources/SRC-001/payment-api.docx",
      "availability": "snapshot_only",
      "readStatus": "complete",
      "freshness": "unknown",
      "sha256": "<64 位小写十六进制>",
      "items": [
        {
          "id": "SRC-001-I001",
          "location": "表 2 第 7 行",
          "original": "超时时间为 3 秒；超时后返回最近 5 分钟缓存",
          "disposition": "requirement",
          "requirements": [
            {
              "id": "SRC-001-R001",
              "text": "外部接口调用超时时间为 3 秒",
              "targets": ["spec", "design", "plan", "code", "reviewer", "e2e"]
            }
          ]
        }
      ]
    }
  ]
}
```

`availability` 取 `live`、`snapshot_only`、`never_provided`；`readStatus` 取 `complete`、`partial`、`unreadable`；`freshness` 取 `current`、`stale`、`unknown`。SHA256 只记录快照指纹，不作为来源变化的完成阻断。

`never_provided` 写 `path: null`、`sha256: null`、`readStatus: "unreadable"`、`items: []`；只有该状态允许向用户索取资料。已有快照但原地址失联时写 `snapshot_only`。

每个表格数据行、字段行、列表项或承载事实的段落分别建立 `items` 条目，`original` 逐字摘录，`location` 指向快照位置。`disposition` 取 `requirement`、`background`、`non_goal`、`duplicate`、`superseded`；只有 `requirement` 包含非空 `requirements`，`superseded` 另写 `replacedBy`。

一条原文可拆出多条要求。`targets` 可多选：可观察输出、时序、错误、状态、副作用、重试、幂等或降级结果必须包含 `spec`；技术实现约束包含 `design`；需要实施的要求包含 `plan` 与 `code`；需要审查或端到端验证时包含 `reviewer` 或 `e2e`。

ID 按来源内出现顺序递增。已有 `SRC-NNN`、`SRC-NNN-INNN`、`SRC-NNN-RNNN` 不重编号、不复用。
