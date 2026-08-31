# 工作流冲突处理改造指南

## 问题

当前 `code-batched-execution.workflow.js` 在 build-candidate 返回 `{success:false, status:"candidate_conflicted"}` 时，使用 `requireSuccess()` 会立即抛错，无法处理冲突。

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
  `Execute: python "${mergeTrainPath}" build-candidate ` +
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
  log(`║   5. python "${mergeTrainPath}" resume-candidate --workspace "${artifactWorkspace}" \\`);
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
      `Execute: python "${mergeTrainPath}" build-candidate ` +
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
          `Execute: python "${schedulerPath}" mark-batches --workspace "${artifactWorkspace}" ` +
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
