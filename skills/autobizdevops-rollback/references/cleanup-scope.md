# 产物归属与清理范围

可执行清单在 `board_core/board_config.json` 各节点的 `artifacts.outputs` 和 `rollback.archive`；公共临时文件由 `hooks/rollback_stage.py` 的 `FEATURE_TRANSIENT_ROLLBACK_PATHS` 声明。下表用于解释 dry-run，不替代脚本的实际清理清单。

以下路径均相对于当前 Feature 目录。只有目标及后续有效阶段拥有的产物才会清理，例如回退 Code 会保留 Plan，回退 CI/CD 会保留 Code 缓存。清理是从活动目录移入 `.autobizdevops/rollback/history/<rollbackId>/artifacts/`，成功后下一次运行不会复用；事务失败时恢复已移动产物。

| 所属阶段 | 一起归档的产物 |
| --- | --- |
| PRD | `PRD.md`、派生来源索引 `source-context.json` |
| Specs | `proposal.md`、`specs/**/*.md`、旧规格评审 `SPECS_REVIEW.md` |
| Design | `design.md`、`.design-contract.lock.json` |
| Plan | `PLAN.md`、`plan.json`、完整 `plans/` 批次计划 |
| 详细设计（启用时） | `DETAIL_DESIGN.md` |
| Code | 下表所列运行态、阶段结果与同步派生物 |
| CI/CD | `CICD_CHECKLIST.md`、`PR_BODY.md` |

## Code Session 的完整目录

| 路径 | 包含内容 |
| --- | --- |
| `.engineering/` | `facts/`（含依赖指纹）、`dependencies/`、`environments/`、`diagnostics/`、`deployments/`、`observations/`、`commands-*.json` 以及内部 claim、lock、临时文件；新版本在此目录内新增的派生文件也随目录归档 |
| `.parallel-runs/` | manifest、事件、lease、dispatch、stages、`engineering/` 下的 context/usage/handoff/snapshot/repair、`utest/` 下的环境/执行绑定/命令脚本/原始输出/期限、候选验证、`resolution-context/`、集成推广记录与最终证据聚合；没有 manifest 的中断 run 目录也归档 |
| `.task-runs/` | TASK 执行记录、快照、源码变更记录与附属日志 |
| `evidence/` | 完整证据流、索引、单条证据、日志和 `.pending/`，不能只移除索引 |
| `.runtime/` | Runtime 评审记录等派生状态 |
| `.frontend/html-analysis/`、`.frontend/html-input/` | Feature 内的 HTML 分析、检查清单、素材映射、合并或复制的 HTML 输入；不清空 `.frontend/` 下的其他文件 |
| 产物工作区 `.cmbdevclaw/workflows/<feature>/` | 固定脚本副本、journal、state、toolstream、sidecar 和锁；单独归档到 history 的 `workflow-runtime/` |

Code 根目录还会归档：

- 前端路由 `FRONTEND_ROUTE.json`，Smoke 计划与结果、`completion-proposal.json`、`REQUIREMENTS_EVAL.md`。
- `UNIT_TEST_REPORT.md`、`UNIT_TEST_RESULT.json`、`test-output.log`，以及旧版本位于根目录的 `UTEST_ENVIRONMENT.json`、`UTEST_EXECUTION.json`。
- E2E 用例、报告、结果、质量扫描、`e2e-run.log` 和完整 `e2e-diagnostics/`；验证报告、验证决策、`FIX_REQUEST.json`。
- `FEATURE_API_DETAIL.md`、`ARTIFACT_CATALOG.json`、`sync-status.json`、`artifact-sync/` 和旧 `hooks.ndjson`。回退事件可能重新创建 `hooks.ndjson`，这是新的审计记录。

所有阶段回退还会归档实现约定的 `.tmp/` 草稿目录、`.autobiz-*.tmp` 原子写入残留、Plan 写事务与锁，以及现有规则列出的报告、同步和 HTML 临时文件。不使用递归 `*.json`、`*.lock` 或整目录清空来发现产物。

## 锁文件清理

`FileLock` 释放系统锁后会保留磁盘文件，文件存在不代表仍被占用。先结束当前 Feature 的写入进程，再由回退脚本归档以下路径；只有锁、没有 JSON 或 manifest 的中断目录同样属于清理范围。

| 位置 | 随回退一起清理的锁 |
| --- | --- |
| Feature 根目录 | `.plan.lock`；Code 执行态重置会短暂重新创建它，脚本在重置释放句柄后再次删除，成功后不得残留 |
| `.engineering/` | facts、依赖解析、诊断、环境缓存的 `*.lock`，观测统计的 `<shard>.lock`，以及缓存 claim 文件 |
| `.parallel-runs/` | 根目录 `.run-id.lock`；各 run 的 `.lock`、`events.lock`、`leases/<batch>.lock`、`integration-locks/<repo>.lock`、`merge-trains/<repo>/.candidate.lock`；工程 handoff/usage 的 `<task>.lock` 和 `.repair.lock` |
| `.task-runs/`、`evidence/` | 两个目录各自的 `.lock`，随执行记录、证据完整归档 |
| `e2e-diagnostics/` | `e2e-run.lock`，随诊断目录归档 |
| 产物工作区 `.cmbdevclaw/workflows/<feature>/` | 当前 Feature 的脚本/Workflow 锁随目录归档，不依赖具体锁文件名 |

`.design-contract.lock.json` 是 Design 的已确认契约快照，不是互斥锁：回退 Design 或更早阶段时归档，单独回退 Plan、Code 或 CI/CD 时保留。

项目级 `.autobizdevops/.parallel-run-id.lock`、`.autobizdevops/rollback/history.lock`、`.autobizdevops/rollback/locks/<feature>.lock`，以及 Git common dir 中的 `autobiz-main-promotion.lock` 是持久化并发控制文件，不随 Feature 产物清理。即便当前没有持有者，也不能通过删除文件来“解锁”，否则等待旧句柄的进程与新建文件的进程可能同时进入临界区。业务仓库的 `index.lock`、依赖管理器 lockfile 和普通用户 `*.lock` 也不按后缀删除；插件自有 Worktree 的 Git 元数据随生命周期管理器回收。

## 保留的输入与目录外资产

- 已确认的 `UI_CONTEXT.json`、其人类视图 `UI_CONTEXT.md`、`IMPLEMENTATION_SCOPE.json`、`SCOPE_SPLIT.md` 和原始资料 `sources/` 继续作为输入保留；回退 PRD 只清除由 PRD 派生的来源索引，原始快照可以再次用于重建。普通用户文件不在清理清单内。
- 原生 Batch、候选及集成 Worktree 由生命周期管理器先清理 Git 注册、临时分支和租约，再归档 run。清理失败时不能继续重置阶段状态。
- 业务仓库中的 `.frontend/`、HTML 源文件及平台 Workflow 记录不因同名而自动删除。插件自有 Worktree 内的派生物随其回收；基线分支上的文件遵循 `--code-source keep/restore`，平台外部资产遵循主技能中按明确 runId/path 检查的规则。
- 项目级 `state.json`、其他 Feature 的缓存/Worktree，以及回退基线和历史不属于本次中间产物清理范围。History 只有通过独立的 `--prune-history` 流程才会删除。
