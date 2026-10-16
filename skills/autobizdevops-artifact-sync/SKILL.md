---
name: autobizdevops-artifact-sync
description: 按实际代码生成或更新 FEATURE_API_DETAIL.md，并同步产物、补传变化和重试失败上传。
---

# /autobizdevops-artifact-sync - 产物同步

## 确定同步上下文

每次使用本技能，从系统提示词读取当前使用人的工号。工号始终作为字符串处理，保留前导零；不能从本机用户名推断，也不能假设已有提供 `${employeeId}` 占位符。缺失或无法唯一确定工号时停止并报告原因。

取得工号后，按[共享提问协议](../references/ask-user-question.md)先调用一次 `request_user_input`，用一个问题展示工号、Feature，以及完整目标目录 `{PROJECT_CODE}/DEV/Features/{feature}/{employee_id}/`，提供“确认并继续”和“取消同步”选项。用户明确确认后，才执行检查、生成或更新文档、同步、准备清单及失败重试。用户在回答中明确更正工号并要求继续时，采用更正值并说明更新后的目录；回答仍有歧义时只澄清未确定的信息。取消或未确认时不继续。若工具不可用，说明无法完成本技能要求的信息确认并停止，不能绕过确认步骤。

以下命令中的 `${employee_id}` 表示由模型填入用户已确认的工号，不是现成的宿主环境变量。`--employee-id` 为必填参数。

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --employee-id "${employee_id}" --check
```

确认后执行只读检查，使用返回的 Feature、checkpoint 和产物目录，并核对返回的 `employee_id`、`upload_base` 与用户确认的信息一致。检查失败或信息不一致时停止并报告原因，不生成接口文档，也不为同步推进阶段。

所有产物及 `ARTIFACT_CATALOG.json` 均上传到 `{PROJECT_CODE}/DEV/Features/{feature}/{employee_id}/` 下，保留产物的相对路径。Feature 名和本地产物目录不追加工号。

准入 checkpoint、节点、产物路径与简述统一维护在 [同步配置](config/artifact-sync.json)。需要调整同步范围时修改该配置。

## 生成接口交付文档

准入通过后，常规同步先检查并生成或更新 `--check` 返回的 `feature_dir` 下的 `FEATURE_API_DETAIL.md`，再上传。仅检查、准备同步清单或补传失败事件时复用现有文档。

读取 [接口文档生成规则与模板](references/feature-api-detail.md)。保留文件名 `FEATURE_API_DETAIL.md`。

- 用 `plan.json` 的 `codeWorkspaces`、当前 Feature 的 implementation evidence、Batch manifest 与 Git 变更（commit中带有“cmbdevclaw提交”的提交）确定仓库和实现范围。文档以当前 Feature 实际交付的代码为依据，不能把整个仓库或其他 Feature 的接口算入本次交付。
- 读取接口入口、请求/响应类型、返回包装、Service、Mapper/Repository、错误码与枚举；多仓库代码依据同时注明 `workspaceRef` 和仓库相对路径。记录可确认的提交或变更范围，便于判断旧文档是否需要更新。
- 需求和设计只用于定位范围，不能据此编造接口字段或实现逻辑。按模板展开复杂字段，给出真实 SQL/查询构造片段和调用顺序。
- 无法确认的信息标为“代码中未确认”；检索后不存在的内容标为“代码中未发现”。确认没有接口变更时，在文档中说明并列出检查范围。
- 源码不可用时保留已有文档并报告未更新的原因，不宣称文档已反映最新代码；缺失文档不阻断其他已存在产物的同步。

## 同步产物

首次同步和后续增量同步都使用同一入口：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --employee-id "${employee_id}" --sync
```

执行同步及汇报结果时，遵循 [同步执行注意事项](references/sync-runtime.md)。

当要检查或准备同步清单时，加 `--prepare-only`：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --employee-id "${employee_id}" --sync --prepare-only
```

该命令会准备本地 pending 事件和基于当前上传目标已有成功记录的目录，不发起网络上传。查看待上传文件应读取事件中的 `artifacts`。

## 失败恢复与结果

按用户请求或在已修复错误后，重试 pending/failed 事件：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --employee-id "${employee_id}" --retry-failed
```

网络、凭据或服务错误未解决时保留失败记录并报告，不循环重试。

以退出码和 `sync-status.json` 的事件状态确认结果；本地生成目录清单不代表上传成功。汇报已确认的工号、上传目录、接口文档是否更新、成功/失败/未变化的同步结果，以及缺失和超过 5 MiB 被跳过的文件。同步失败不修改业务代码或测试结论。
