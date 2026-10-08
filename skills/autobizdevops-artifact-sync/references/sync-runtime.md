# 产物同步运行契约

## 所有权与路径

- `scripts/artifact_sync.py`：产物选择、快照、目录清单、同步状态和历史格式迁移。
- `scripts/sync_artifacts.py`：CLI、上传前校验、multipart 上传与成功/失败记录。
- `hooks/artifact_sync.py`、`hooks/sync_artifacts.py` 仅保留旧 Python 导入与 CLI 的兼容转发。新调用使用技能内脚本。
- 接口文档由本技能根据实际代码生成，上传脚本只收集已有文件。

Feature 根目录下维护 `FEATURE_API_DETAIL.md`、`ARTIFACT_CATALOG.json`、`sync-status.json`。日志沿用 `hooks.ndjson` 的历史同步事件格式。旧 `artifact-sync/sync-status.json` 会迁移到 Feature 根目录；状态文件不能删除，否则会失去增量和重试依据。

## 上传协议

沿用插件既有服务 `https://tscode-cos-plugin.paasuat.cmbchina.cn/file/upload`，以 multipart POST 的 `path` 与 `file` 字段上传。对象目录为 `PROJECT_CODE/DEV/Features/FEATURE_ID/产物相对父目录`；文件名使用原产物文件名。

单文件上限为 5 MiB。超限文件记录为 `skipped`，原因 `file_size_exceeds_5mb`；缺失文件记录为 `missing`。其余存在文件继续同步，不能把跳过文件算作已上传。上传前检查文件可读性、大小与 SHA-256 是否仍符合快照。

每个阶段事件先上传业务产物，最后上传 `ARTIFACT_CATALOG.json`。HTTP 200 表示单文件上传成功，阶段全部上传成功后才写入 success 事件和 published hashes。目录清单是拟发布的远端索引，其本地 `uploaded` 条目不替代 `sync-status.json` 中的成功记录。

## 命令语义

| 参数 | 行为 |
| --- | --- |
| `--sync` | 发现当前有效 workflow 已完成的 Biz/Dev 阶段，并检查历史同步阶段；未变化的阶段跳过 |
| `--reconcile` | 仅检查已有发布、pending 或 failed 记录的阶段；不发现首次同步阶段 |
| `--prepare-only` | 配合 `--sync`/`--reconcile`，只准备本地事件和目录清单，不上传 |
| `--retry-failed` | 各重试一次 pending/failed 事件；`--drain-outbox` 是兼容别名 |
| `--event-id` | 执行单个事件 |

重试会重新读取当前文件并刷新快照，不使用旧文件内容。每个 HTTP 请求默认超时 50 秒。失败返回非零并保留事件；不要通过修改状态、伪造哈希或推进 checkpoint 消除失败。
