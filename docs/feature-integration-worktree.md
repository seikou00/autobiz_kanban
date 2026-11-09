# 需求集成 Worktree 与用户工作区交付

固定 Workflow 按 Batch 将集成成果同步到用户原工作区，以**未提交改动**展示。用户分支 HEAD、原暂存区保持不变；已有未提交修改不阻止同步。Runtime 在临时 Worktree 中三方合并，冲突由 Workflow 自动编辑临时文件，再由 Runtime 校验并写回。后续 Batch 仍从集成分支开发。`workspaceSync` / `workspaceSyncPending` 返回实际同步结果，最后直接报告完成和遗留问题，不再询问是否合并或验收。下文显式基线合并命令仅用于用户另行明确要求正式 Git 合并的场景。

同步使用上次 `lastSyncedSha`、用户当前内容及最新集成 SHA。备份保存在 run 的 `workspace-sync` 目录，含暂存区副本、文件原始内容及逐文件写回日志；私有 `refs/autobiz-workspace-backups/` 引用保存 Git 快照，不进入用户分支历史。写回不清空、不 stash 用户工作区；中断后跳过已完成文件并继续，用户在计算或解冲突期间再次编辑时重新计算。暂存、未暂存、未跟踪、二进制及符号链接内容均保留。同步问题单独记录，不撤销已合并 Batch；最终收口和恢复会继续同步。

自动收口不执行 `sync-main`。最终报告使用 aggregate 本次读取的 `workspaceSync` 与 `repositoryHeads`，不沿用先前的同步缓存；同步 SHA 尚未追上当前集成 SHA 时仍显示待同步。用户基线分支、需求集成分支及其 Worktree 均保留，交付清理队列只处理已交付的 Batch 和候选临时 Worktree。

`settle-delivery` 完成合并后只登记同步待办并立即返回 `merged`，Workflow 随后执行独立同步；最终收口同样先返回待办，再补同步。临时同步 Worktree 的创建和合并按命令指定空 hooks 目录，保留 LFS 内容过滤，不改用户全局 Git 配置。Git 原始输出保存在同步备份目录的 `*.stdout.log` / `*.stderr.log`；错误文本容错转为 UTF-8。`preparing` 中断会校验并复用已创建的临时 Worktree，已合并 Batch 的后续诊断不会重新登记为业务失败。

独立恢复使用 `python hooks/parallel_workspace_sync.py sync --workspace <artifact> --feature <feature> --run-id <runId> --repository-ref <ref> --resolver-id <invocationId>`。冲突返回临时 `worktreePath`、`contextPath`、`conflictedFiles`、`attemptId`；仅编辑这些临时冲突文件，随后执行同入口 `complete` 并追加 `--attempt-id <attemptId>`。`refresh_required` 表示用户期间又有修改，重新 `sync` 即可。不得直接在用户目录处理 Runtime 冲突标记，也不调用 commit/reset。

固定 Code Workflow 的阶段为 prepare → implement → review → 交付 → 证据汇总。前后端均不包含 UTest/E2E，不创建测试阶段、测试环境、验证 Worktree 或 SKIP 证据。Review 通过后直接交付，最终使用 aggregate `--finish-workflow` 完成报告。

新 scheduler run 默认使用 `isolated` 模式：从 Plan 中每个业务仓库启动时的**当前分支及其已提交 HEAD** 创建需求集成分支，Batch 从集成 HEAD 创建各自 Worktree，Merge Train 只推进集成分支的提交。每批成果随后写回用户原工作区，分支引用与暂存区不变。最终证据、测试未执行记录、同步状态和清理待办一同进入报告。

```text
启动时的当前分支 → integration → Batch Worktrees
                     ↓ Review
       integration ← Merge Train
             ↓ 每批三方合并并同步文件
       用户原工作区（未提交，可直接编辑，HEAD 不变）
             ↓ evidence aggregate / 测试未执行记录
       succeeded / succeeded_with_issues（直接报告，不自动提交）
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

Git 2.20 可使用独立的正式合入入口。读取目标分支的 Worktree 登记时，插件优先使用 `worktree list --porcelain -z`，仅在 Git 返回不支持选项的退出码 129 时回退到 `worktree list --porcelain`；中文、空格和引号按完整路径保留。读取失败或旧格式无法明确解析时会报错，不能当作目标分支未检出而跳过工作区检查。旧 run 的等待确认状态通过 launcher 恢复后直接进入结果汇总，不重跑 Batch。

每个物理仓库创建一个集成 Worktree。同一物理 Git 仓库的多个逻辑 workspaceRef 共用它，并同步记录集成 HEAD；对应 checkout 的当前分支和提交必须一致。多个物理仓库保留同一个 scheduler run，分别从各自当前分支创建集成 Worktree。

## 开发、验证与恢复

- 固定 Workflow 的 `settle-delivery` 不转写提交 SHA；Runtime 在锁内读取当前封存版本，并在整个交付过程中保留版本校验。排队或仓库锁占用返回 `queued`、操作 ID、阻塞 Batch 和调度快照，释放 dispatch 执行名额，不消耗 Batch 失败重试次数。之后重新 claim，沿用同一交付记录继续。
- 阶段均已结束的 `sealed` Batch 从交付 gate/settle 恢复，不重跑实现或 Review。候选构建或合并中断时复用已登记候选及仓库占用记录；Git 与 Plan 已完成后，只补齐操作回执和 dispatch 收尾，不重复合并。Runtime 用进程持有的执行锁区分正在执行和已中断的结算，避免仅因长效 dispatch 尚未过期就一直阻塞队列。
- 仓库绑定的 `gitRoot` 保留源 checkout 身份；`integration.worktreePath`、`integration.branchName` 保存集成工作区。`baseBranch`、`baseSha` 冻结启动时的当前分支和提交，`headSha` 跟随集成分支推进。
- Batch 创建时保存 `startSha`。后续依赖 Batch 从依赖已集成后的 HEAD 创建；已经开始的 Batch 保留起始 SHA 和改动，冲突交给 Merge Train。
- prepare 证据绑定 Batch 的 `startSha`，不会因集成 HEAD 推进而失效。最终聚合校验实现、Review 与交付证据；生产实现变化必须重新封存并按修复协议处理。历史 Run 的测试封存只作为旧证据兼容校验，不再触发测试阶段。
- Batch 的 `merged` 和 `mergeCommitSha` 表示已合入集成分支。`mainPromotion` 单独记录最终基线分支合并；两者不能混用。`mainPromotion`、`main-promotion.json` 和 `sync-main` 沿用现有接口名称，其中的 main 指本次 run 冻结的基线分支，不限定分支名为 `main`。
- 每个 Batch 合并后触发一次 20 秒预算的 `parallel_cleanup.py drain`，只清理已推广且所有权、SHA、工作区状态均符合的交付和候选 Worktree，不再等待其他 Batch。最终再执行一次 120 秒预算的清理。用户原目录和需求集成 Worktree 保留。
- 固定 Workflow 使用 `aggregate --finish-workflow`，采用 `report_only` 策略，保留真实 `passed`、全部 `errors`、`validationStatus`、deferred 问题及原严重性，不重写既有 Evidence、不自动重跑测试。交付完整且报告持久化后，直接进入 `succeeded` 或 `succeeded_with_issues`，不准备确认报告。Code 报告不生成测试状态字段。
- 完成报告写入 `workflowCompletedAt`，审计发现不会反复启动流程。工作区同步仍有待办时，launcher 恢复同一 run 处理同步，不重新实现或 Review 已合并 Batch。旧等待确认状态也按此路径恢复。清理在最终收口时再做一次有预算的尝试，仍失败仅保留 `cleanupPending`，不重新拉起已完成 run；以后可独立执行 drain。
- Workflow 汇总后调用现有 `update_checkpoint.py --checkpoint code_done --json`，通过真实 Plan/证据门禁推进 Code 完成。清理失败不影响推进；检查点推进失败则单独报告 `retry_code_checkpoint`。下一阶段由当前 Feature 的配置路由，不在 Code Workflow 中直接启动。
- 固定 Workflow 不调用 `promote`、不提交用户工作区、不移动用户分支 HEAD、不发起合并或验收提问。清理失败记录 pending，保留已合并状态；无法自动处理的问题如实报告。

## 独立的正式 Git 合入入口

以下入口保留给用户另行明确要求正式合入分支的场景，不属于固定 Workflow 的自动收口。报告中的 `mainPromotion.snapshot.repositories` 包含源目录、目标分支、目标及候选 SHA、集成目录和 diff 信息；执行正式合入前须绑定有效报告与用户授权。用户只要求按未提交文件交付时，不调用这些命令，也不主动追问是否使用。

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

所有 Batch 集成后，固定 Workflow 会先调用 `sync-main`，再汇总已有证据。目标分支提交变化时，可重新执行：

```bash
python hooks/parallel_integration.py sync-main --workspace <artifact> --feature <feature> --run-id <runId>
```

此操作仅将目标分支合入集成 Worktree，废弃旧确认与最终证据。无冲突时返回 `running`，通过 launcher 恢复同一 Workflow，重新同步并汇总证据。

有冲突时返回 `integration_sync_conflicted`、集成 Worktree 和准确的 `conflictedFiles`。只解决这些冲突文件，然后由插件完成暂存和提交：

```bash
python hooks/parallel_integration.py complete-sync --workspace <artifact> --feature <feature> --run-id <runId>
```

插件校验 merge parents、冲突标记和冲突文件之外的变化；成功后恢复同一 Workflow 汇总。独立的正式合入需重新准备有效报告。禁止直接在基线分支修复，禁止用 reset、force push 或旧 approvalDigest 绕过验证。

## 清理与回滚

普通 `cleanup`（包括 `--force`）不能删除尚未合入基线分支的集成成果。显式 Code 阶段回退才允许清理并放弃这些 Worktree；正在执行最终合并时必须先完成恢复。集成模式的 run rollback 不修改基线分支。成果已合入基线分支后，如需撤销该分支内容，必须作为单独的分支回滚处理。
