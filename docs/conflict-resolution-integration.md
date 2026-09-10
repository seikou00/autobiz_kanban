# 冲突解决集成示例

## 在工作流中处理 candidate_conflicted 状态

当 Merge Train 返回 `candidate_conflicted` 状态时，工作流需要：

1. 调用 `ConflictResolutionAgent` 尝试自动解决
2. 如果自动解决成功，重新验证并推广
3. 如果解决失败，通知人工介入并阻塞下游

### Python 集成示例

```python
# 在 parallel_merge_train.py 或 coordinator 中

from hooks.conflict_resolution_agent import ConflictResolutionAgent
from hooks.conflict_types import ConflictContext, CandidateStatus

def handle_merge_result(build_result: dict, workspace: Path, feature: str, run_id: str):
    """处理 build_candidate 的结果"""
    
    if build_result.get("status") == CandidateStatus.CANDIDATE_CONFLICTED.value:
        # 冲突检测到，尝试解决
        conflict_ctx_data = build_result["conflictContext"]
        conflict_ctx = ConflictContext(
            base_sha=conflict_ctx_data["baseSha"],
            batch_ids=conflict_ctx_data["batchIds"],
            conflicted_files=conflict_ctx_data["conflictedFiles"],
            candidate_worktree=conflict_ctx_data["candidateWorktree"],
            conflict_markers=conflict_ctx_data["conflictMarkers"],
            repository_ref=conflict_ctx_data["repositoryRef"],
            wave=conflict_ctx_data["wave"],
            attempts=conflict_ctx_data.get("attempts", 0),
            error_message=conflict_ctx_data.get("errorMessage", ""),
        )
        
        # 尝试自动解决
        resolver = ConflictResolutionAgent(max_attempts=2)
        resolution_result = resolver.resolve_conflict(conflict_ctx)
        
        if resolution_result.status == "resolved":
            # 解决成功，重新验证候选
            print(f"✓ Conflicts auto-resolved in {resolution_result.strategy_used}")
            print(f"  Resolved files: {', '.join(resolution_result.resolved_files)}")
            
            # 更新 manifest
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                train_key = f"{conflict_ctx.repository_ref}:wave-{conflict_ctx.wave:03d}"
                train = manifest.get("mergeTrains", {}).get(train_key)
                if train:
                    train["status"] = "built"
                    train["candidateSha"] = resolution_result.new_candidate_sha
                    train["resolutionAttempts"] = conflict_ctx.attempts + 1
                    train["resolutionStrategy"] = resolution_result.strategy_used
                save_manifest(workspace, feature, run_id, manifest)
            
            # 继续验证流程
            return {"success": True, "resolved": True, "candidateSha": resolution_result.new_candidate_sha}
        
        else:
            # 解决失败，需要人工介入
            print(resolver.notify_manual_intervention(conflict_ctx))
            
            # 更新 manifest 为 needs_resolution
            with run_lock(workspace, feature, run_id):
                manifest = load_manifest(workspace, feature, run_id)
                train_key = f"{conflict_ctx.repository_ref}:wave-{conflict_ctx.wave:03d}"
                train = manifest.get("mergeTrains", {}).get(train_key)
                if train:
                    train["status"] = CandidateStatus.NEEDS_RESOLUTION.value
                    train["resolutionAttempts"] = conflict_ctx.attempts + 1
                    train["resolutionReason"] = resolution_result.reason
                manifest["status"] = "blocked"
                save_manifest(workspace, feature, run_id, manifest)
            
            # 阻塞下游依赖
            return {"success": False, "needs_manual_resolution": True, "reason": resolution_result.reason}
    
    # 其他状态（成功、其他失败）
    return build_result
```

### JavaScript 工作流集成

```javascript
// 在 code-batched-execution.workflow.js 中

async function buildAndMergeWave(wave, batchIds) {
  // 构建候选
  const buildResult = requireSuccess(await agent(
    `Build merge candidate for wave ${wave} with batches ${batchIds.join(', ')}. ` +
    `Execute: python "${mergeTrainPath}" build-candidate --workspace "${artifactWorkspace}" ` +
    `--feature "${feature}" --run-id "${runId}" --wave ${wave} ${batchIds.map(id => `--batch-id "${id}"`).join(' ')}. ` +
    `Return JSON result with status field.`,
    { label: `build-candidate-wave-${wave}`, phase: "候选验证" }
  ), "build candidate");

  // 检查是否有冲突
  if (buildResult.status === "candidate_conflicted") {
    log(`⚠️  Conflict detected in wave ${wave}, attempting auto-resolution...`);
    
    // 尝试自动解决
    const resolutionResult = await agent(
      `Resolve merge conflicts for wave ${wave}. ` +
      `Conflict context: ${JSON.stringify(buildResult.conflictContext)}. ` +
      `Use ConflictResolutionAgent to analyze and resolve conflicts automatically. ` +
      `If successful, commit the resolution and return new candidate SHA. ` +
      `If unresolvable, return manual_required status with reason.`,
      { 
        label: `resolve-conflicts-wave-${wave}`,
        phase: "候选验证",
        effort: "high"  // 需要更强的推理能力
      }
    );
    
    if (resolutionResult.status === "resolved") {
      log(`✓ Conflicts auto-resolved using ${resolutionResult.strategy_used}`);
      // 继续验证流程
      return { ...buildResult, candidateSha: resolutionResult.new_candidate_sha, resolved: true };
    } else {
      log(`❌ Auto-resolution failed: ${resolutionResult.reason}`);
      log(`   Manual intervention required. See worktree: ${buildResult.conflictContext.candidateWorktree}`);
      
      // 阻塞此波次，但允许其他无依赖的波次继续
      return { ...buildResult, needs_manual_resolution: true };
    }
  }
  
  // 无冲突或已解决，继续验证
  return buildResult;
}

// 主循环中处理
phase("候选验证");
for (let waveIndex = 0; waveIndex < mergeableWaves.length; waveIndex++) {
  const wave = mergeableWaves[waveIndex];
  const waveNum = waveIndex + 1;
  
  log(`Processing wave ${waveNum} with ${wave.length} batches...`);
  
  const mergeResult = await buildAndMergeWave(waveNum, wave);
  
  if (mergeResult.needs_manual_resolution) {
    // 记录需要人工处理的波次
    pendingResolution.push({ wave: waveNum, batches: wave, context: mergeResult.conflictContext });
    continue;  // 跳过此波次，继续处理其他波次
  }
  
  // 验证候选
  const verifyResult = await verifyCan didate(mergeResult);
  
  if (verifyResult.passed) {
    // 推广
    await promoteCandidate(mergeResult);
  }
}

// 最后报告需要人工处理的项
if (pendingResolution.length > 0) {
  log(`\n⚠️  ${pendingResolution.length} wave(s) require manual conflict resolution:`);
  for (const item of pendingResolution) {
    log(`   - Wave ${item.wave}: ${item.batches.join(', ')}`);
    log(`     Worktree: ${item.context.candidateWorktree}`);
  }
}
```

## 配置运行时模式

在 `.autobiz/runtime_config.json` 中配置：

```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 4,
  "conflictResolution": {
    "maxAttempts": 2,
    "enableAutoResolve": true,
    "notifyOnManualRequired": true
  }
}
```

这个配置会被 `parallel_runtime.py` 中的 `resource_groups()` 读取。

## 恢复被阻塞的候选

当人工解决冲突后，可以恢复候选的验证流程：

```bash
# 1. 进入 worktree
cd /path/to/candidate_worktree

# 2. 解决冲突
vim src/conflicted_file.py

# 3. 标记为已解决
git add src/conflicted_file.py
git commit -m "Resolve conflicts manually"

# 4. 恢复验证流程
autobiz resume-merge-train --workspace /path/to/workspace --feature myfeature --run-id cw-20260830-001 --wave 1
```

`resume-merge-train` 命令会：
1. 检查候选 worktree 是否已解决所有冲突
2. 更新 manifest 状态为 `built`
3. 触发 B-INT 验证
4. 如果验证通过，推广到 main
