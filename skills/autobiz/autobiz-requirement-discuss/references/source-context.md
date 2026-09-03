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
              "targets": ["spec"]
            }
          ]
        }
      ]
    }
  ]
}
```

`availability` 取 `live`、`snapshot_only`、`never_provided`；`readStatus` 取 `complete`、`partial`、`unreadable`；`freshness` 取 `current`、`stale`、`unknown`。

`never_provided` 写 `path: null`、`readStatus: "unreadable"`、`items: []`；只有该状态允许向用户索取资料。已有快照但原地址失联时写 `snapshot_only`。

表格数据行、字段行、列表项或承载事实的段落可分别建立 `items` 证据条目，`original` 逐字摘录，`location` 指向快照位置。证据条目不等于下游要求：纯标题、布局、空白填充位和说明性内容标为 `background`；重复字段或模板变体中的重复结构标为 `duplicate`。只有能独立改变行为、实现或验收的语义约束标为 `requirement` 并包含非空 `requirements`；`superseded` 另写 `replacedBy`。

多行共同表达同一语义约束时，只在一个代表性条目中生成一条要求，其余条目标为 `background` 或 `duplicate`。模板仅容量不同而字段结构重复时，只生成模板选择或容量边界要求。

`targets` 只选择实际消费该要求的阶段，不复制固定的全阶段列表：可观察输出、时序、错误、状态、副作用、重试、幂等或降级结果包含 `spec`；技术实现约束包含 `design`；需要实施的要求包含 `plan` 与 `code`；需要审查或端到端验证时包含 `reviewer` 或 `e2e`。

ID 按来源内出现顺序递增。已有 `SRC-NNN`、`SRC-NNN-INNN`、`SRC-NNN-RNNN` 不重编号、不复用。
