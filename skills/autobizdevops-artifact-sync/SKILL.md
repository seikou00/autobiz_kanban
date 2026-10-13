---
name: autobizdevops-artifact-sync
description: 按实际代码生成或更新 FEATURE_API_DETAIL.md，并同步产物、补传变化和重试失败上传。
---

# /autobizdevops-artifact-sync - 产物同步

根据当前 Feature 的实际代码生成接口文档，已有计划和实现记录用于辅助定位。

## 确定同步上下文

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --check
```

先执行只读检查，使用返回的 Feature、checkpoint 和产物目录。检查失败时停止并报告原因，不生成接口文档，也不为同步推进阶段。

准入 checkpoint、节点、产物路径与简述统一维护在 [同步配置](config/artifact-sync.json)。需要调整同步范围时修改该配置。

按该插件的实际产物文件名维护配置。

## 生成接口交付文档

准入通过后，常规同步先检查并生成或更新 `--check` 返回的 `feature_dir` 下的 `FEATURE_API_DETAIL.md`，再上传。仅检查、准备同步清单或补传失败事件时复用现有文档。

读取 [接口文档生成规则与模板](references/feature-api-detail.md)。保留文件名 `FEATURE_API_DETAIL.md`。

- 从当前会话或用户指定的代码工作区确认业务仓库，结合当前 Feature 的需求、计划和实际变更确定实现范围。产物目录不等于代码仓库，不能把整个仓库或其他 Feature 的接口算入本次交付。
- 如果存在 `plan.json.codeWorkspaces`、implementation evidence 或 Batch manifest，可用来定位仓库和实现；这些内容不是必须的前置条件，若没有可使用已有的 `plan.md` 和实现记录辅助定位。
- 有提交记录时，用"cmbdevclaw提交"或"[cmbdevclaw]"标记辅助定位并核对当前 Feature 的关联；没有该标记或尚未提交时，结合可确认的提交范围、暂存和未暂存变更、未跟踪的新文件核查实现。记录本次采用的代码范围，便于判断旧文档是否需要更新。
- 读取接口入口、请求/响应类型、返回包装、Service、Mapper/Repository、错误码与枚举；代码依据注明仓库相对路径，多仓库时补充仓库名称或路径，如有 `workspaceRef` 时可一并标注。
- 需求和设计只用于定位范围，不能据此编造接口字段或实现逻辑。按模板展开复杂字段，给出真实 SQL/查询构造片段和调用顺序。
- 无法确认的信息标为“代码中未确认”；检索后不存在的内容标为“代码中未发现”。确认没有接口变更时，在文档中说明并列出检查范围。
- 源码不可用或无法确认当前 Feature 的实现范围时，保留已有文档并报告未更新的原因，不宣称文档已反映最新代码，也不据此判定“没有接口变更”；缺失文档不阻断其他已存在产物的同步。

## 同步产物

首次同步和后续增量同步都使用同一入口：

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

该命令会准备本地 pending 事件和基于已有上传成功记录的目录，不发起网络上传。查看待上传文件应读取事件中的 `artifacts`。

## 失败恢复与结果

按用户请求或在已修复错误后，重试 pending/failed 事件：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --retry-failed
```

网络、凭据或服务错误未解决时保留失败记录并报告，不循环重试。

以退出码和 `sync-status.json` 的事件状态确认结果；本地生成目录清单不代表上传成功。汇报接口文档是否更新、成功/失败/未变化的同步结果，以及缺失和超过 5 MiB 被跳过的文件。同步失败不修改业务代码或测试结论。
