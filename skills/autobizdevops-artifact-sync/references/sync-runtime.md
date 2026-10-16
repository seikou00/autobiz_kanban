# 产物同步执行注意事项

- 本技能负责根据实际代码生成接口文档，具体要求见 [接口文档生成规则与模板](feature-api-detail.md)。
- 上传目标为 `{PROJECT_CODE}/DEV/Features/{feature}/{employee_id}/`，普通产物和 `ARTIFACT_CATALOG.json` 使用同一目标目录。清单只包含该特性已有上传成功记录的产物，不混入其他工号的记录。
- `sync-status.json` 按完整上传目标分别保存发布记录，事件绑定 `employee_id` 和 `upload_base`。仅复用及重试当前目标的事件；显式指定其他目标的事件 ID 时应报告错误，不能改写为当前目标后上传。
- 汇报本次上传结果时，以退出码和 `sync-status.json` 的事件状态为依据；目录中的 `uploaded` 也可能来自历史成功记录，本地目录生成不代表目录已上传。缺失和超限原因以脚本执行输出为准。
- 不删除或手工修改 `sync-status.json`、`ARTIFACT_CATALOG.json`；不要通过伪造哈希或改动 checkpoint 消除同步失败。
