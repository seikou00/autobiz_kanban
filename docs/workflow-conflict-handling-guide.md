# 工作流冲突处理改造指南

## 当前实现：后台命令结果与冲突恢复

`validateAndPromoteBatch()` 会先检查构建结果的 `status`。若为
`candidate_conflicted`，立即对同一个 repository/wave 执行以下交接：

1. 插件 `prepare-resolution` 检查候选仓库/分支和正在进行的 merge，登记模型的文件写入授权，返回 `resolution_required`、`worktreePath` 和 `conflictedFiles`。
2. 独立冲突 Agent 只编辑这些文件，保留双方业务逻辑并移除冲突标记；完成后返回 `success=true,status=resolved`。不得自行 `git add`、`git commit` 或 `git merge --continue`，也不得使用脚本包装这些操作。
3. Agent 返回后，Workflow 单独调用插件 `complete-resolution`。插件检查 Git 身份、修改范围、剩余冲突标记，精确暂存冲突文件，保留 Git hooks 并非交互创建带 task-card 信息的 merge commit，登记实际候选 SHA。
4. 只有插件返回 `built` 且没有失败信号，才调用 `promote-candidate`。

写入 hook 按 `prepare-resolution` 登记的候选和文件授权，不要求为合并重新启动已完成的生产 TASK；未授权文件和其他 Worktree 仍不可写。
若提交成功但登记中断，准备命令返回 `ready_to_complete`，Workflow 跳过模型编辑，插件核对提交的父节点、tree 和消息后恢复登记，不会创建第二个提交。
即时恢复与最终恢复共用此协议；模型编辑次数由持久化 `maxAttempts` 控制。插件提交失败时返回具体错误，不让模型尝试替代提交命令。

原 `resolve-candidate` 保留为旧自动策略入口，`resume-candidate` 保留为人工已提交候选的登记入口；固定 Workflow 使用上述新协议。
无法恢复时保留候选 Worktree 和冲突上下文，记录 `needs_resolution`。

后台执行的子 Agent 可能返回包装对象：

```json
{
  "command": "python ... build-candidate ...",
  "run_in_background": true,
  "task_id": "f005fa70",
  "exit_code": 1,
  "final_result": {"ok": false, "success": false, "status": "candidate_conflicted"}
}
```

`unwrap()` 统一展开 `final_result`（包括 JSON 字符串和嵌套包装），使状态、
`candidateSha`、`conflictContext` 等命令字段直接可用。无效的 `final_result`
不得通过 `requireSuccess()`；包装层非零 `exit_code` 或显式失败也不会被内部成功字段覆盖。
冲突构建退出 1 时仍先进入恢复分支，恢复命令的结果独立判断。
即时恢复和最终恢复都检查失败信号；推广的 `stale` 字段也在展开后判断。

回归验证：`python3 -m unittest tests.test_merge_resolution_handoff` 和
`node --test tests/test_workflow_command_results.mjs`，后者也由
`python3 tests/test_code_workflow_integration.py` 调用。

## 历史改造方案

以下保留早期冲突处理设计供参考，当前行为以以上实现为准。
早期 Workflow 在 build-candidate 返回 `{success:false, status:"candidate_conflicted"}` 时，
直接使用 `requireSuccess()` 会立即抛错，无法处理冲突。

## 修改方案

### 1. 修改 build-candidate 调用逻辑

**位置**: `code-batched-execution.workflow.js` 约 405-414 行

**当前代码**:
```javascript
const buildResult = requireSuccess(await agent(
  `Build merge candidate...`,
  { label: `build-candidate-wave-${waveNum}`, phase: "候选验证" }
), "build candidate");
```

**修改为**:
```javascript
const buildResult = await agent(
  `Build merge candidate for wave ${waveNum}. ` +
  `Execute: python -X utf8 "${mergeTrainPath}" build-candidate ` +
  `--workspace "${artifactWorkspace}" --feature "${feature}" ` +
  `--run-id "${runId}" --wave ${waveNum} ` +
  `${batchIds.map(id => `--batch-id "${id}"`).join(' ')}. ` +
  `Return JSON with status field (built/candidate_conflicted/failed).`,
  { label: `build-candidate-wave-${waveNum}`, phase: "候选验证" }
);

// Check status instead of requireSuccess
if (!buildResult || !buildResult.success) {
  if (buildResult && buildResult.status === "candidate_conflicted") {
    // Handle conflict
    log(`⚠️  Wave ${waveNum}: Git conflict detected`);
    log(`   Conflicted files: ${(buildResult.conflictContext?.conflictedFiles || []).join(', ')}`);
    log(`   Candidate worktree preserved at: ${buildResult.conflictContext?.candidateWorktree}`);
    
    // For MVP: Mark batches as needs_resolution and skip
    // Future: Call conflict resolution agent here
    mergeResults.push({
      wave: waveNum,
      status: "needs_resolution",
      batchIds: batchIds,
      conflictContext: buildResult.conflictContext,
    });
    continue;  // Skip to next wave
  } else {
    // Other failure
    throw new Error(`build-candidate failed: ${buildResult?.error || 'unknown error'}`);
  }
}

// Success: continue with verification
```

### 2. 添加冲突波次跟踪

**在工作流开头添加**:
```javascript
const mergeResults = [];
const conflictedWaves = [];
```

### 3. 在候选验证阶段结束后报告冲突

**在所有波次处理完后添加**:
```javascript
if (conflictedWaves.length > 0) {
  log(`\n╔══════════════════════════════════════════════════════════════`);
  log(`║ MANUAL INTERVENTION REQUIRED`);
  log(`╠══════════════════════════════════════════════════════════════`);
  log(`║ ${conflictedWaves.length} wave(s) have unresolved conflicts:`);
  
  for (const wave of conflictedWaves) {
    log(`║`);
    log(`║ Wave ${wave.wave}:`);
    log(`║   Batches: ${wave.batchIds.join(', ')}`);
    log(`║   Worktree: ${wave.conflictContext.candidateWorktree}`);
    log(`║   Conflicted files:`);
    for (const file of wave.conflictContext.conflictedFiles) {
      log(`║     - ${file}`);
    }
  }
  
  log(`║`);
  log(`║ To resolve manually:`);
  log(`║   1. cd <worktree-path>`);
  log(`║   2. Edit conflicted files`);
  log(`║   3. git add <files>`);
  log(`║   4. git commit`);
  log(`║   5. python -X utf8 "${mergeTrainPath}" resume-candidate --workspace "${artifactWorkspace}" \\`);
  log(`║      --feature "${feature}" --run-id "${runId}" --wave <wave-num>`);
  log(`╚══════════════════════════════════════════════════════════════\n`);
}
```

### 4. 完整示例

```javascript
// 候选验证阶段
phase("候选验证");
const mergeResults = [];
const conflictedWaves = [];

// Group mergeable batches by repository
const batchesByRepo = {};
for (const batchId of mergeableBatches) {
  const batch = batchData[batchId];
  const repo = batch.repositoryRef || "default";
  if (!batchesByRepo[repo]) batchesByRepo[repo] = [];
  batchesByRepo[repo].push(batchId);
}

// Process each repository
for (const [repo, repoBatches] of Object.entries(batchesByRepo)) {
  // Group into waves by resource_groups logic (simplified)
  const waves = [repoBatches];  // For now, treat as single wave per repo
  
  for (let waveIndex = 0; waveIndex < waves.length; waveIndex++) {
    const waveBatches = waves[waveIndex];
    const waveNum = waveIndex + 1;
    
    log(`Processing ${repo} wave ${waveNum} with ${waveBatches.length} batches...`);
    
    // Build candidate
    const buildResult = await agent(
      `Build merge candidate for wave ${waveNum} batches ${waveBatches.join(', ')}. ` +
      `Execute: python -X utf8 "${mergeTrainPath}" build-candidate ` +
      `--workspace "${artifactWorkspace}" --feature "${feature}" ` +
      `--run-id "${runId}" --wave ${waveNum} ` +
      `${waveBatches.map(id => `--batch-id "${id}"`).join(' ')}. ` +
      `Return JSON with success, status, and conflictContext fields.`,
      { label: `build-candidate-${repo}-wave${waveNum}`, phase: "候选验证" }
    );
    
    // Handle result
    if (!buildResult || !buildResult.success) {
      if (buildResult && buildResult.status === "candidate_conflicted") {
        // Conflict detected
        log(`⚠️  Wave ${waveNum}: Git conflict in ${buildResult.conflictContext.conflictedFiles.length} file(s)`);
        
        conflictedWaves.push({
          wave: waveNum,
          repo: repo,
          batchIds: waveBatches,
          conflictContext: buildResult.conflictContext,
        });
        
        // Mark batches as needs_resolution
        await agent(
          `Mark batches ${waveBatches.join(', ')} as needs_resolution in manifest. ` +
          `Execute: python -X utf8 "${schedulerPath}" mark-batches --workspace "${artifactWorkspace}" ` +
          `--feature "${feature}" --run-id "${runId}" ` +
          `${waveBatches.map(id => `--batch-id "${id}"`).join(' ')} ` +
          `--status needs_resolution`,
          { label: `mark-conflicted-wave${waveNum}` }
        );
        
        continue;  // Skip verification for this wave
      } else {
        // Other failure
        throw new Error(`build-candidate failed: ${JSON.stringify(buildResult)}`);
      }
    }
    
    // Success: verify candidate
    const verifyResult = requireSuccess(await agent(
      `Verify candidate for wave ${waveNum}. Execute B-INT tests...`,
      { label: `verify-candidate-wave${waveNum}`, phase: "候选验证" }
    ), "verify candidate");
    
    if (!verifyResult.passed) {
      throw new Error(`B-INT failed for wave ${waveNum}`);
    }
    
    // Promote candidate
    await agent(
      `Promote candidate for wave ${waveNum} to main...`,
      { label: `promote-wave${waveNum}`, phase: "候选验证" }
    );
    
    mergeResults.push({
      wave: waveNum,
      status: "promoted",
      batchIds: waveBatches,
    });
  }
}

// Report conflicts
if (conflictedWaves.length > 0) {
  log(`\n╔══════════════════════════════════════════════════════════════`);
  log(`║ MANUAL INTERVENTION REQUIRED`);
  log(`╠══════════════════════════════════════════════════════════════`);
  log(`║ ${conflictedWaves.length} wave(s) have unresolved conflicts`);
  
  for (const wave of conflictedWaves) {
    log(`║`);
    log(`║ Wave ${wave.wave} (${wave.repo}):`);
    log(`║   Batches: ${wave.batchIds.join(', ')}`);
    log(`║   Worktree: ${wave.conflictContext.candidateWorktree}`);
    log(`║   Files: ${wave.conflictContext.conflictedFiles.join(', ')}`);
  }
  
  log(`╚══════════════════════════════════════════════════════════════\n`);
}
```

## 实施步骤

1. **备份原文件**: `cp workflows/code-batched-execution.workflow.js workflows/code-batched-execution.workflow.js.bak`

2. **找到 build-candidate 调用**: 搜索 `build-candidate` 或 `parallel_merge_train`

3. **应用上述修改**: 替换 `requireSuccess()` 为状态检查

4. **测试**: 
   - 无冲突场景：应正常推广
   - 有冲突场景：应保留 worktree 并显示通知

## 注意事项

- **不要立即实现自动解决**: MVP 阶段只保留 worktree 和通知
- **保持其他波次继续**: 只阻塞有依赖关系的 Batch
- **幂等性**: 重新运行 workflow 应该能识别已冲突的状态

## 后续增强

在 MVP 稳定后，可以添加：
```javascript
// 尝试自动解决
const resolutionResult = await agent(
  `Attempt to auto-resolve conflicts for wave ${waveNum} using ConflictResolutionAgent`,
  { label: `resolve-wave${waveNum}`, effort: "high" }
);

if (resolutionResult.status === "resolved") {
  // 重新验证并推广
  ...
}
```
