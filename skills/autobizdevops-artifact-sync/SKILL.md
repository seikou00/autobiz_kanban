---
name: autobizdevops-artifact-sync
description: 在 Feature 到达 code_done 或后续阶段后，按实际代码生成或更新 FEATURE_API_DETAIL.md，并同步产物、补传变化和重试失败上传。用户要求产物同步、补传、重传或整理接口交付文档时使用。
---

# /autobizdevops-artifact-sync - 产物同步

由用户调用本技能，执行 Feature 接口文档生成与产物上传；不依赖 checkpoint 变化，也不推进 checkpoint。

## 确定同步上下文

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --check
```

先执行只读检查，使用返回的 Feature、checkpoint 和产物目录。检查失败时停止并报告原因，不生成接口文档，也不为同步推进阶段。

准入 checkpoint、节点、产物路径与简述统一维护在 [同步配置](config/artifact-sync.json)。需要调整同步范围时修改该配置。

## 生成接口交付文档

准入通过后，常规同步先检查并生成或更新 Feature 产物目录下的 `FEATURE_API_DETAIL.md`，再上传。仅补传失败事件时复用现有文档。

读取 [接口文档生成规则与模板](references/feature-api-detail.md)。保留文件名 `FEATURE_API_DETAIL.md`。

- 用 `plan.json` 的 `codeWorkspaces`、当前 Feature 的 implementation evidence、Batch manifest 与 Git 变更确定仓库和实现范围。文档以当前 Feature 实际交付的代码为依据，不能把整个仓库或其他 Feature 的接口算入本次交付。
- 读取接口入口、请求/响应类型、返回包装、Service、Mapper/Repository、错误码与枚举；多仓库代码依据同时注明 `workspaceRef` 和仓库相对路径。记录可确认的提交或变更范围，便于判断旧文档是否需要更新。
- 需求和设计只用于定位范围，不能据此编造接口字段或实现逻辑。按模板展开复杂字段，给出真实 SQL/查询构造片段和调用顺序。
- 无法确认的信息标为“代码中未确认”；检索后不存在的内容标为“代码中未发现”。确认没有接口变更时，在文档中说明并列出检查范围。
- 源码不可用时保留已有文档并报告未更新的原因，不宣称文档已反映最新代码；缺失文档不阻断其他已存在产物的同步。

## 同步产物

首次同步和后续增量同步都使用同一入口；checkpoint 保持不变时也可以再次调用：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --sync
```

执行同步及汇报结果时，遵循 [同步执行注意事项](references/sync-runtime.md)。

当要检查或准备同步清单时，加 `--prepare-only`：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --sync --prepare-only
```

该命令会准备本地 pending 事件和基于已有上传成功记录的目录，不发起网络上传。查看待上传文件应读取事件中的 `artifacts`；目录不包含尚未上传的文件。

## 失败恢复与结果

按用户请求或在已修复错误后，重试 pending/failed 事件：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --retry-failed
```

网络、凭据或服务错误未解决时保留失败记录并报告，不循环重试。

以退出码和 `sync-status.json` 的事件状态确认结果；本地生成目录清单不代表上传成功。汇报接口文档是否更新、成功/失败/未变化的同步结果，以及缺失和超过 5 MiB 被跳过的文件。同步失败不修改业务代码或测试结论。
