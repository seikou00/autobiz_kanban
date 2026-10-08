---
name: autobizdevops-artifact-sync
description: 同步 Feature 已完成阶段的产物，按实际代码生成或更新 FEATURE_API_DETAIL.md，并维护上传目录、增量同步和失败重试。用户要求产物同步、补传、重传或整理接口交付文档时使用。
---

# /autobizdevops-artifact-sync - 产物同步

独立承担 Feature 接口文档生成与产物上传。它是横切技能，不是 Board 节点，不推进 checkpoint，也不启动 Code、Review、UTest 或 E2E。Code/E2E 完成后需要同步时进入本技能；普通 checkpoint 更新不会自动触发上传。

## 确定同步上下文

所有 Python 命令使用 `python -X utf8`。本技能随 AutobizDevOps 插件分发，脚本复用插件的 `board_core` 契约与 `hooks/paths.py` 路径解析。

```bash
python -X utf8 "${pluginPath}/read_state_json.py" --feature "${feature}"
```

上传需要插件环境提供 `PLUGIN_WORKSPACE`、`PROJECT_DIR`、`FEATURE_ID` 和 `PROJECT_CODE`。产物工作区是 `${pluginWorkspace}/${projectDir}`；`PROJECT_CODE` 是远端对象路径前缀。兼容旧环境时 `PROJECT_DIR` 回退到 `PROJECT_CODE`。`--feature` 必须与 `FEATURE_ID` 一致，不接受任意业务仓库作为产物工作区。

从状态确认 Feature 和当前 checkpoint。用户未指定且当前环境也不能唯一确定 Feature 时，先列出候选并要求选择。归档 Feature 使用状态中 iteration 对应的归档目录；不得新建同名活动目录来绕过归档定位。

## 生成接口交付文档

常规同步时，如果 Code 已完成（`code_done` 或其后续阶段），先检查并生成或更新 Feature 产物目录下的 `FEATURE_API_DETAIL.md`，再上传。仅补传失败事件时复用现有文档；用户只要求生成接口文档时，生成后结束，不上传。

读取 [接口文档生成规则与模板](references/feature-api-detail.md)。保留文件名 `FEATURE_API_DETAIL.md`，不另建 `feature_detail_api.md`。

- 用 `plan.json` 的 `codeWorkspaces`、当前 Feature 的 implementation evidence、Batch manifest 与 Git 变更确定仓库和实现范围。文档以当前 Feature 实际交付的代码为依据，不能把整个仓库或其他 Feature 的接口算入本次交付。
- 读取接口入口、请求/响应类型、返回包装、Service、Mapper/Repository、错误码与枚举；多仓库代码依据同时注明 `workspaceRef` 和仓库相对路径。记录可确认的提交或变更范围，便于判断旧文档是否需要更新。
- 需求和设计只用于定位范围，不能据此编造接口字段或实现逻辑。按模板展开复杂字段，给出真实 SQL/查询构造片段和调用顺序。
- 无法确认的信息标为“代码中未确认”；检索后不存在的内容标为“代码中未发现”。确认没有接口变更时，在文档中说明并列出检查范围。
- Code 尚未完成时仅同步此前已完成阶段；不为同步启动 Code/E2E。源码不可用时保留已有文档并报告未更新的原因，不宣称文档已反映最新代码；缺失文档不阻断其他已存在产物的同步。

## 同步产物

首次同步和后续增量同步都使用：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --sync
```

`--sync` 根据当前 Feature 的有效 workflow 契约，发现已完成的 Biz/Dev 阶段，并复查已有同步记录。支持首次上传、哈希变化后的增量上传，以及已有 pending/failed 记录的继续处理。正在执行和尚未到达的阶段不会首次发布。同步内容包括阶段契约输出、Biz 的 `prd_original/` 与 `sources/` 资料，以及 Code 的 `FEATURE_API_DETAIL.md`。上传地址、目录格式、限制与恢复细节见 [同步运行契约](references/sync-runtime.md)。

用户只要检查或准备同步清单时，加 `--prepare-only`：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --sync --prepare-only
```

该命令会写本地目录清单和 pending 事件，但不会发起网络上传，不是只读 dry-run。不要手工修改 `ARTIFACT_CATALOG.json` 或 `sync-status.json`。

## 失败恢复与结果

按用户请求或在已修复错误后，重试 pending/failed 事件：

```bash
python -X utf8 "${pluginPath}/skills/autobizdevops-artifact-sync/scripts/sync_artifacts.py" \
  --feature "${feature}" --retry-failed
```

仅重试指定事件用 `--event-id "<event-id>"`；仅复查历史发布阶段用 `--reconcile`。每次调用对选中事件各尝试一次；网络、凭据或服务错误未解决时保留失败记录并报告，不循环重试。

以退出码和 `sync-status.json` 的事件状态确认结果；本地生成目录清单不代表上传成功。汇报接口文档是否更新、成功/失败/未变化的同步结果，以及缺失和超过 5 MiB 被跳过的文件。同步失败不回退 checkpoint，也不修改业务代码或测试结论。
