# 产物同步执行注意事项

- 本技能负责根据实际代码生成接口文档，具体要求见 [接口文档生成规则与模板](feature-api-detail.md)。
- 汇报本次上传结果时，以退出码和 `sync-status.json` 的事件状态为依据；目录中的 `uploaded` 也可能来自历史成功记录，本地目录生成不代表目录已上传。缺失和超限原因看执行结果或本地日志。
- 不删除或手工修改 `sync-status.json`、`ARTIFACT_CATALOG.json`；不要通过伪造哈希或改动 checkpoint 消除同步失败。
