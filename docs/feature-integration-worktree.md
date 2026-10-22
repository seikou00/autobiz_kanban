# 需求集成 Worktree 与最终人工合并

新 scheduler run 默认使用 `isolated` 模式：从 Plan 中每个业务仓库启动时的**当前分支及其已提交 HEAD** 创建需求集成分支，Batch 从集成 HEAD 创建各自 Worktree，Merge Train 只推进集成分支。启动时的当前分支记录为基线分支，也作为最终合入目标。最终 E2E 结果登记、证据汇总报告生成后，run 等待用户确认；E2E 失败和聚合校验未通过均作为明确问题交给用户审阅，不阻断准备合入报告；只有确认入口会修改基线分支。

```text
启动时的当前分支 → integration → Batch Worktrees
                     ↓ Review / UTest
       integration ← Merge Train
             ↓ 最终 E2E / evidence aggregate
       awaiting_user_confirmation → prepare → 用户确认 → promote → 原基线分支
```

## 配置与兼容

artifact workspace 的 `.autobiz/runtime_config.json` 可配置：

```json
{
  "integrationMode": "isolated"
}
```

无需配置分支或创建配置文件。插件读取的是 `plan.json.codeWorkspaces` 对应业务 checkout 的当前分支；`main`、`master`、`develop` 或任意功能分支均按同一规则处理。旧配置中的 `targetBranches` 已不参与新 run 的分支选择。不自动 fetch、push 或创建远程 PR。

分支名和 HEAD 在创建 run 时冻结，恢复时直接复用 manifest；用户之后切换源 checkout 分支不会改变本次基线或最终合入目标。Detached HEAD 或当前分支尚无提交时明确阻断，不猜测替代分支，也不自动创建 bootstrap 提交。

当前分支已提交的内容会带入集成分支；源 checkout 的未提交修改不会带入。插件仅在 Git 的本地 exclude 中加入运行目录忽略规则。业务源码、原分支和 index 不被自动提交或清理。

没有 `integrationMode=isolated` 标记的旧 manifest 保持原来的 direct 模式。显式配置 `integrationMode=direct` 可用于兼容旧流程；切换配置不会改变已存在 run 的模式或基线。

Git 2.20 可使用最终合入入口。读取目标分支的 Worktree 登记时，插件优先使用 `worktree list --porcelain -z`，仅在 Git 返回不支持选项的退出码 129 时回退到 `worktree list --porcelain`；中文、空格和引号按完整路径保留。读取失败或旧格式无法明确解析时会报错，不能当作目标分支未检出而跳过工作区检查。已停在 `promotion_prepared` 的 run，候选、目标及确认内容未变化时，更新插件后可沿用已有确认重试，无需重跑 Batch/E2E。

每个物理仓库创建一个集成 Worktree。同一物理 Git 仓库的多个逻辑 workspaceRef 共用它，并同步记录集成 HEAD；对应 checkout 的当前分支和提交必须一致。多个物理仓库保留同一个 scheduler run，分别从各自当前分支创建集成 Worktree。

## 开发、验证与恢复

- 仓库绑定的 `gitRoot` 保留源 checkout 身份；`integration.worktreePath`、`integration.branchName` 保存集成工作区。`baseBranch`、`baseSha` 冻结启动时的当前分支和提交，`headSha` 跟随集成分支推进。
- Batch 创建时保存 `startSha`。后续依赖 Batch 从依赖已集成后的 HEAD 创建；已经开始的 Batch 保留起始 SHA 和改动，冲突交给 Merge Train。
- prepare 证据绑定 Batch 的 `startSha`，不会因集成 HEAD 推进而失效。UTest 可新增测试资产、修改 POM 的 test scope 依赖及必要 Surefire/Failsafe 配置；最终聚合读取证据提交到最终提交的真实 Git 差异，复用封存的 POM 内容校验。多次测试封存或 Batch Worktree 清理后仍可核验，既有阶段 evidence 不重写；生产代码、生产依赖和失败屏蔽配置的变更不能沿用旧 Review 证据。
- Batch 的 `merged` 和 `mergeCommitSha` 表示已合入集成分支。`mainPromotion` 单独记录最终基线分支合并；两者不能混用。`mainPromotion`、`main-promotion.json` 和 `sync-main` 沿用现有接口名称，其中的 main 指本次 run 冻结的基线分支，不限定分支名为 `main`。
- `cleanup-merged` 可以清理已集成的 Batch，始终保留集成 Worktree。等待确认、保留成果和基线分支同步状态都属于活动 run；launcher 返回验收入口，不重新派发 Batch。
- 最终 E2E 在集成 SHA 对应的临时验证 Worktree 中运行。证据汇总采用 `report_only` 策略，保留真实 `passed`、全部 `errors`、`validationStatus`、deferred 问题及原严重性，不重写既有 Evidence、不自动重跑测试。报告成功持久化且所有 Batch 已集成后，校验未通过也进入 `awaiting_user_confirmation`。固定 Workflow 准备 `main-promotion.json`，返回目标/集成 SHA、diff stat、diff digest、`evidencePassed`、`evidenceErrors`、证据 digest 和 `approvalDigest`。E2E 失败仍保留 `e2eStatus=failed`，不声称测试通过。用户明确确认绑定这些问题和本次实际提交。
- 合入仍要求 Batch 交付及 merge 提交实际包含于当前候选、集成 Worktree 干净且无未解决冲突、目标分支已同步、Plan 和确认内容未变化。报告或证据在确认后变化，必须重新准备并确认；合入带审计问题的候选后状态为 `succeeded_with_issues`。
- 历史 run 若因聚合校验失败停在 `blocked` 且已有聚合报告，可直接调用下文 `prepare`；插件重新生成审计报告后准备确认，不重跑 Batch 或 E2E。原 stage Evidence 保留。读取状态或持久化报告失败时仍须先解决该执行错误。
- 固定 Workflow 从不调用 `promote`，也不把阶段成功视为用户同意。Agent 必须展示实际分支、SHA、变更及验证结果，等待用户明确同意后才执行最终合并。

## 最终确认命令

以下命令中的 workspace 为 artifact workspace；run-id 必须复用返回的 runId。

```bash
# 查看成果与当前状态
python hooks/parallel_integration.py status --workspace <artifact> --feature <feature> --run-id <runId>

# 准备或刷新可审阅的合并报告，不修改基线分支
python hooks/parallel_integration.py prepare --workspace <artifact> --feature <feature> --run-id <runId>

# 仅在用户明确确认这份报告后执行；digest 必须原样取自报告
python hooks/parallel_integration.py promote --workspace <artifact> --feature <feature> --run-id <runId> --approval-digest <approvalDigest> --approved-by <用户标识> --confirm

# 暂不合入，保留成果；以后仍可以 prepare 并确认
python hooks/parallel_integration.py keep --workspace <artifact> --feature <feature> --run-id <runId>
```

确认绑定目标分支 SHA、集成 SHA、Plan 契约及最终证据。确认后任何一项改变都不能沿用旧确认。目标分支检出在某个 Worktree 时，必须先保证该 checkout 干净，再使用普通 fast-forward merge；目标未检出时使用带旧 SHA 校验的 ref 更新，不切换用户的当前分支。

每个仓库在 Git 操作前落盘 promotion intent，操作成功后单独记结果。中断后重复执行同一确认命令会恢复已完成的操作。多个仓库的合并不具有跨仓库原子性；部分成功后保留逐仓库结果，继续前先检查尚未合并的目标。

如果部分成功后，剩余目标分支又发生变化，执行 `sync-main` 会保留原合并历史、使旧确认失效，再恢复同一 Workflow 验证并重新准备报告。新报告的 `previousPromotions` 明确列出此前的逐仓库结果；已合入的内容不会被撤销或重复创建提交。

## 基线分支变化与冲突

所有 Batch 集成后，固定 Workflow 会先调用 `sync-main`，再执行最终 E2E。目标在验收期间变化时，可重新执行：

```bash
python hooks/parallel_integration.py sync-main --workspace <artifact> --feature <feature> --run-id <runId>
```

此操作仅将目标分支合入集成 Worktree，废弃旧确认与最终证据。无冲突时返回 `running`，通过 launcher 恢复同一 Workflow，重新执行最终 E2E 与证据汇总。

有冲突时返回 `integration_sync_conflicted`、集成 Worktree 和准确的 `conflictedFiles`。只解决这些冲突文件，然后由插件完成暂存和提交：

```bash
python hooks/parallel_integration.py complete-sync --workspace <artifact> --feature <feature> --run-id <runId>
```

插件校验 merge parents、冲突标记和冲突文件之外的变化；成功后仍需重新验证、prepare 和用户确认。禁止直接在基线分支修复，禁止用 reset、force push 或旧 approvalDigest 绕过验证。

## 清理与回滚

普通 `cleanup`（包括 `--force`）不能删除尚未合入基线分支的集成成果。显式 Code 阶段回退才允许清理并放弃这些 Worktree；正在执行最终合并时必须先完成恢复。集成模式的 run rollback 不修改基线分支。成果已合入基线分支后，如需撤销该分支内容，必须作为单独的分支回滚处理。
