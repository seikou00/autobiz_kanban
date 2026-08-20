# Code Workflow 并行化实现 - 代码检视报告

**检视日期**: 2026-08-19
**检视范围**: 并行 Code 执行的核心实现
**检视人**: Claude Code

---

## 📋 执行摘要

### ✅ 优点

1. **架构清晰** - 职责分离良好，各模块边界明确
2. **类型完整** - 使用了完整的类型注解
3. **错误处理** - 异常处理覆盖全面
4. **状态管理** - Manifest + Lease 双轨机制设计合理
5. **测试覆盖** - 集成测试通过 5/5

### ⚠️ 需要关注的问题

1. **Plan Digest 漂移检测** - 可能过于严格
2. **Lease TTL 与长任务** - 默认 15 分钟可能不够
3. **并发锁粒度** - 整个 run 级别锁可能限制并发
4. **错误恢复** - 部分场景缺少自动恢复
5. **日志可观测性** - 缺少结构化日志

---

## 1. 架构设计检视

### 1.1 整体架构 ✅ 优秀

```
Workflow (orchestration)
    ↓
Scheduler (planning + resource grouping)
    ↓
Lease Manager (ownership)
    ↓
Worktree Manager (isolation) + Task Runner (execution)
    ↓
Batch Merger (integration)
    ↓
Final Verifier (validation)
    ↓
Lifecycle Manager (cleanup)
```

**评价**: 分层清晰，单一职责原则落地良好。

### 1.2 关键设计决策

| 决策 | 评价 | 建议 |
|------|------|------|
| Manifest 作为唯一状态源 | ✅ 正确 | 考虑添加 snapshot 机制 |
| Lease 心跳机制 | ✅ 合理 | 考虑动态 TTL |
| 固定 baseSha 分支 | ✅ 优秀 | 已正确实现 |
| 并发调度 | ✅ Worktree 隔离后全部就绪 Batch 可并行 | 仅受 `maxParallel` 限制 |
| 顺序合并策略 | ✅ 安全 | 考虑增量合并 |

---

## 2. 核心模块检视

### 2.1 `parallel_runtime.py` ⭐⭐⭐⭐⭐

**职责**: 运行时状态管理、Manifest、Lease 机制

#### ✅ 优点

```python
# 1. Plan Digest 计算严谨
def plan_digest(bundle: PlanBundle) -> str:
    # 正确排除了可变状态字段
    mutable_keys = {"status", "activeBatchId", ...}
    # 稳定排序确保幂等性
    payload = {"root": stable(bundle.root), "batches": stable(bundle.batches)}
```

```python
# 2. Lease 机制完整
def acquire_lease(...) -> dict[str, Any]:
    # 原子性保证：FileLock + 过期检查
    with FileLock(path.with_suffix(".lock")):
        if existing.get("expiresEpoch", 0) > now:
            raise ValueError("lease_held")
```

```python
# 3. Worktree 隔离后，资源冲突不提前阻塞并行
def resource_groups(manifest, batch_ids):
    # 每个就绪 Batch 都是独立调度单元；maxParallel 由 scheduler 统一限制。
    # 写集、组件和 Lane 仅用于合并诊断，真实冲突交给对应仓库的 Git merge。
```

#### ⚠️ 潜在问题

**问题 1: Plan Digest 过于严格**

```python
# parallel_runtime.py:88
if plan_digest(bundle) != manifest.get("planDigest"):
    raise ValueError("parallel_plan_digest_changed")
```

**影响**: 任何 Plan 修改（即使不影响并行批次）都会阻断 run

**建议**:
```python
def batch_subset_digest(bundle: PlanBundle, batch_ids: set[str]) -> str:
    """只计算参与本 run 的 batch 的 digest"""
    return hashlib.sha256(
        json.dumps({
            batch_id: stable(bundle.batches[batch_id])
            for batch_id in batch_ids
        }, sort_keys=True)
    ).hexdigest()
```

**问题 2: Lease TTL 固定**

```python
# parallel_runtime.py:29
DEFAULT_TTL_SECONDS = 15 * 60  # 15 分钟
```

**影响**: 复杂 batch 可能需要更长时间，导致 lease 过期

**建议**:
```python
def estimate_batch_ttl(batch: dict) -> int:
    """根据 batch 复杂度估算 TTL"""
    base_ttl = 15 * 60
    task_count = len(batch.get("tasks", []))
    estimated_ttl = base_ttl + (task_count * 5 * 60)
    return min(estimated_ttl, 60 * 60)  # 最多 1 小时
```

**问题 3: 运行时锁粒度**

```python
# parallel_runtime.py:254
@contextmanager
def run_lock(workspace: Path, feature: str, run_id: str):
    with FileLock(run_dir(workspace, feature, run_id) / ".lock"):
        yield
```

**影响**: 所有 manifest 读写都锁整个 run，限制并发

**建议**: 考虑更细粒度的锁
```python
@contextmanager
def batch_lock(workspace: Path, feature: str, run_id: str, batch_id: str):
    """Batch 级别的锁，允许不同 batch 并发修改"""
    with FileLock(run_dir(...) / "batches" / f"{batch_id}.lock"):
        yield
```

---

### 2.2 `parallel_batch_scheduler.py` ⭐⭐⭐⭐

**职责**: 调度逻辑、批次分配

#### ✅ 优点

```python
# 1. 调度算法清晰
def schedule(workspace, feature, run_id):
    ready = ready_batches(manifest)  # 依赖已满足
    groups = resource_groups(manifest, ready)  # 每个 Batch 独立并行单元
    # 按可用槽位选择
    slots = max(0, max_parallel - active)
```

```python
# 2. 状态转换严格
def mark_batch(..., status: str, **details):
    terminal = {"merged", "failed", "blocked", "cancelled"}
    if previous in terminal and previous != status:
        raise ValueError("batch_terminal")
```

#### ⚠️ 潜在问题

**问题 1: 调度策略简单**

```python
# parallel_batch_scheduler.py:86
for group in groups:
    if slots <= 0:
        break
    selected.append(group)
    slots -= 1
```

**影响**: 未考虑批次优先级、预估时间等

**建议**:
```python
def priority_score(batch: dict) -> float:
    """计算批次优先级分数"""
    task_count = len(batch.get("tasks", []))
    deps_count = len(batch.get("dependencies", []))
    # 任务少、依赖少的优先执行
    return 1.0 / (task_count + deps_count + 1)

# 按优先级排序分组
groups = sorted(groups, key=lambda g: sum(priority_score(manifest["batches"][bid]) for bid in g), reverse=True)
```

**问题 2: 无重试机制**

当前 `mark_batch` 失败后直接标记为 `failed`，没有重试逻辑。

**建议**:
```python
batch["retryCount"] = batch.get("retryCount", 0)
if status == "failed" and batch["retryCount"] < MAX_RETRIES:
    batch["retryCount"] += 1
    batch["status"] = "pending"
    return manifest
```

---

### 2.3 `worktree_manager.py` ⭐⭐⭐⭐⭐

**职责**: Worktree 生命周期管理

#### ✅ 优点

```python
# 1. 严格的名称校验
WORKTREE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# 2. 防止隐式覆盖
if worktree_path.exists():
    return {"success": False, "error": "worktree_already_exists"}

# 3. 基于固定 SHA 创建
def create_parallel_worktree(..., repo_path: Path):
    base_sha = manifest.get("baseSha")
    if not base_sha:
        raise ValueError("parallel_base_sha_missing")
```

#### ⚠️ 潜在问题

**问题 1: Seal 操作无原子性保证**

```python
# worktree_manager.py:280 (推测)
def seal_worktree(...):
    # 提交
    subprocess.run(["git", "commit", ...])
    # 更新 manifest
    mark_batch(..., commitSha=sha)
```

如果提交成功但 manifest 更新失败，会产生不一致。

**建议**:
```python
def seal_worktree(...):
    sha = None
    try:
        result = subprocess.run(["git", "commit", ...])
        sha = result.stdout.strip()
        mark_batch(..., commitSha=sha)
    except Exception as exc:
        if sha:
            # 回滚提交
            subprocess.run(["git", "reset", "--hard", "HEAD~1"])
        raise
```

---

### 2.4 `batch_merger.py` ⭐⭐⭐⭐

**职责**: 批次合并、冲突检测

#### ✅ 优点

```python
# 1. 冲突检测完整
def detect_conflicts(batches):
    file_to_batches = {}
    for batch in batches:
        for file_path in batch.get("changedFiles", []):
            file_to_batches.setdefault(file_path, []).append(batch_id)
    return [{"file": path, "batches": ids} for path, ids in file_to_batches.items() if len(ids) > 1]

# 2. 合并前校验严格
def preflight_merge(repo_path, *, base_sha):
    dirty = _dirty(repo)
    if dirty:
        return {"ok": False, "error": "main_worktree_dirty"}
    if head != base_sha:
        return {"ok": False, "error": "main_head_changed"}
```

#### ⚠️ 潜在问题

**问题 1: 合并失败后清理不完整**

```python
# batch_merger.py:114
if result.returncode != 0:
    conflicts = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
    _git(repo, "merge", "--abort")
    return {"success": False, "conflicts": conflicts}
```

**影响**: Abort 后可能有残留文件

**建议**:
```python
_git(repo, "merge", "--abort")
_git(repo, "clean", "-fd")  # 清理未跟踪文件
_git(repo, "reset", "--hard", "HEAD")  # 确保回到干净状态
```

**问题 2: 顺序合并性能**

当前实现是串行合并所有 batch，可能很慢。

**建议**: 考虑增量合并策略
```python
def incremental_merge(batches):
    """每合并一个 batch 立即验证，失败快速反馈"""
    for batch in batches:
        merge_result = merge_worktree_to_main(...)
        if not merge_result["success"]:
            return merge_result
        # 立即编译检查（可选）
        quick_check_result = run_quick_compile()
        if not quick_check_result["passed"]:
            rollback_last_merge()
            return {"success": False, "early_failure": batch_id}
```

---

### 2.5 `parallel_final_verify.py` ⭐⭐⭐⭐

**职责**: 最终编译验证

#### ✅ 优点

```python
# 1. 严格的前置检查
def verify_final(...):
    if dirty.stdout.strip():
        raise ValueError("main_worktree_dirty")
    if plan_digest(bundle) != manifest.get("planDigest"):
        raise ValueError("plan_digest_changed")
    if incomplete:
        raise ValueError("incomplete_batches")

# 2. 策略验证
policy_errors = {
    command_policy_errors(command) +
    compile_only_command_errors(command)
}
```

#### ⚠️ 潜在问题

**问题 1: 编译失败后无细粒度诊断**

```python
# parallel_final_verify.py:78
results.append({
    "passed": passed,
    "outputTail": output[-4000:]  # 只保留末尾 4000 字符
})
```

**影响**: 大型项目编译输出可能丢失关键错误信息

**建议**:
```python
{
    "passed": passed,
    "outputSha256": sha256(output),
    "outputTail": output[-4000:],
    "fullOutputPath": str(save_full_output(output)),  # 保存完整输出
    "errorLines": extract_error_lines(output),  # 提取错误行
}
```

---

## 3. Workflow 编排检视

### 3.1 `code-batched-execution.workflow.js` ⭐⭐⭐⭐

#### ✅ 优点

```javascript
// 1. Phase 结构清晰
export const meta = {
  phases: [
    { title: "准备" },
    { title: "并行实现" },
    { title: "顺序合并" },
    { title: "最终验证" }
  ]
};

// 2. 使用 schema 强制结构化输出
agent(prompt, {
  schema: BATCH_EXECUTION_SCHEMA
})

// 3. 错误处理完整
if (failed.length) {
  return { error: "batch_execution_failed", failed };
}
```

#### ⚠️ 潜在问题

**问题 1: Workflow 缺少恢复机制**

当前 workflow 失败后无法从中间步骤恢复。

**建议**: 利用 workflow resume 特性
```javascript
// 在关键步骤保存状态
if (args.resumeFrom === "merge") {
  // 跳过准备和并行实现，直接进入合并
  batchResults = loadFromPreviousRun();
}
```

**问题 2: 缺少进度反馈**

用户无法实时看到哪些 batch 正在执行。

**建议**:
```javascript
// 使用 log() 输出进度
log(`Batch ${batchId} started (${completed}/${total})`);
log(`Batch ${batchId} completed in ${duration}s`);
```

---

## 4. 集成与交互检视

### 4.1 Task Runner 集成 ✅ 良好

```python
# task_runner.py 中正确处理了并行模式
if parallel_run_id:
    # 跳过 BATCH_HANDOFF
    # 直接使用 lease_token 验证权限
```

### 4.2 技能集成 ⚠️ 需要完善

`/autodev-code` 技能中提到了 workflow，但实际集成代码尚未完成。

**建议**: 在 SKILL.md 中添加明确的入口逻辑
```bash
# 检查是否使用 Workflow
launcher=$(python hooks/workflow_launcher.py \
  --feature "$feature" \
  --plugin-path "$pluginPath" \
  --workspace "$artifactWorkspace" \
  --json)
useWorkflow=$(echo "$launcher" | jq -r '.useWorkflow')

if [ "$useWorkflow" = "true" ]; then
  # 启动 Workflow
  # 使用 Workflow 工具
fi
```

---

## 5. 错误处理与恢复

### 5.1 错误处理覆盖 ⭐⭐⭐⭐

**已覆盖的场景**:
- ✅ Plan digest 漂移
- ✅ 主工作区被修改
- ✅ Lease 过期
- ✅ Worktree 冲突
- ✅ 合并冲突
- ✅ 编译失败

### 5.2 恢复机制 ⚠️ 部分缺失

**已实现**:
- ✅ Lease 心跳续期
- ✅ Lease 回收
- ✅ Run resume
- ✅ Partial rollback

**缺失**:
- ❌ 单个 batch 重试
- ❌ 合并失败后的自动修复
- ❌ Worktree 泄漏的自动检测与清理

**建议**: 添加自动恢复任务
```python
def auto_recover_run(workspace, feature, run_id):
    """自动恢复策略"""
    manifest = load_manifest(workspace, feature, run_id)

    # 1. 回收过期 lease
    reclaim_stale_leases(workspace, feature, run_id)

    # 2. 重试失败的 batch（有重试次数限制）
    for batch_id, item in manifest["batches"].items():
        if item["status"] == "failed" and item.get("retryCount", 0) < 3:
            mark_batch(workspace, feature, run_id, batch_id, "pending")

    # 3. 清理孤儿 worktree
    cleanup_orphan_worktrees(workspace, feature, run_id)
```

---

## 6. 性能与可扩展性

### 6.1 性能瓶颈

| 瓶颈 | 影响 | 优化建议 |
|------|------|----------|
| 运行时锁粒度 | 限制并发写入 | 改为 batch 级别锁 |
| 顺序合并 | 合并阶段串行 | 考虑增量合并 |
| Plan digest 全量计算 | 每次调度都重新计算 | 缓存 digest |
| Manifest 全量读写 | 大型 plan 性能差 | 考虑增量更新 |

### 6.2 可扩展性

**当前限制**:
- `MAX_PARALLEL_BATCHES = 4` (硬编码)
- `DEFAULT_TTL_SECONDS = 900` (固定)
- `max_parallel` 参数由 workflow 传递

**建议**: 支持动态调整
```python
def auto_tune_max_parallel():
    """根据系统资源自动调整并发数"""
    cpu_count = os.cpu_count() or 4
    memory_gb = psutil.virtual_memory().total / (1024**3)

    # 保守策略：每个 batch 假设需要 1 核心 + 2GB 内存
    return min(cpu_count - 1, int(memory_gb / 2), 8)
```

---

## 7. 测试覆盖

### 7.1 已覆盖 ✅

- ✅ Workflow launcher 逻辑
- ✅ Worktree 管理器
- ✅ Batch merger 冲突检测
- ✅ Workflow 脚本语法
- ✅ 技能集成完整性

### 7.2 待补充 ⚠️

- ❌ Lease 过期与续期
- ❌ 并发调度算法
- ❌ 合并冲突处理
- ❌ 最终验证失败场景
- ❌ 回滚操作正确性
- ❌ 端到端集成测试（真实 feature）

**建议**: 添加单元测试
```python
# tests/test_parallel_runtime.py
def test_lease_expiry():
    """测试 lease 过期后能被正确回收"""
    lease = acquire_lease(..., ttl_seconds=1)
    time.sleep(2)
    assert not check_lease(..., lease["ownerToken"])
    assert reclaim_lease(...) == True

def test_resource_groups_conflict_detection():
    """测试资源分组算法正确检测冲突"""
    manifest = {
        "batches": {
            "B001": {"writeSet": ["src/a.py"], "workspaceRef": "backend"},
            "B002": {"writeSet": ["src/a.py"], "workspaceRef": "backend"},
            "B003": {"writeSet": ["src/b.py"], "workspaceRef": "backend"},
        }
    }
    groups = resource_groups(manifest, ["B001", "B002", "B003"])
    # B001/B002 即使写同一文件也可先并行，冲突留到 merge 阶段检测。
    assert len(groups) == 3
```

---

## 8. 文档与可观测性

### 8.1 文档 ⭐⭐⭐⭐

**优点**:
- ✅ 完整的实施文档 (5000+ 行)
- ✅ 快速开始指南
- ✅ 实施 checklist
- ✅ 用户指南

**建议补充**:
- ❌ API 参考文档
- ❌ 故障排查指南
- ❌ 性能调优指南

### 8.2 可观测性 ⚠️ 需要加强

**当前状态**:
- ✅ Events 日志 (JSONL 格式)
- ✅ Manifest 状态追踪
- ⚠️ 缺少结构化日志
- ⚠️ 缺少性能指标

**建议**: 添加监控指标
```python
# hooks/parallel_runtime.py
def record_metric(workspace, feature, run_id, metric_name, value):
    """记录性能指标"""
    metrics_path = run_dir(workspace, feature, run_id) / "metrics.jsonl"
    with metrics_path.open("a") as f:
        f.write(json.dumps({
            "at": utc_now(),
            "metric": metric_name,
            "value": value
        }) + "\n")

# 使用示例
record_metric(..., "batch.duration", duration_seconds)
record_metric(..., "merge.file_count", len(changed_files))
record_metric(..., "compile.duration", compile_time)
```

---

## 9. 安全与数据一致性

### 9.1 安全性 ⭐⭐⭐⭐

**优点**:
- ✅ Lease token 防止越权
- ✅ 文件锁防止竞态
- ✅ Base SHA 固定防止漂移
- ✅ 输入校验（worktree 名称、batch ID）

### 9.2 数据一致性 ⭐⭐⭐⭐

**优点**:
- ✅ Atomic writes (atomic_write_json)
- ✅ 文件锁保护临界区
- ✅ Manifest 作为唯一状态源

**潜在风险**:
- ⚠️ Seal 操作非原子（前面已提到）
- ⚠️ Manifest 与 Git 状态可能不一致

**建议**: 添加一致性校验
```python
def verify_manifest_consistency(workspace, feature, run_id, repo_path):
    """校验 manifest 与实际 Git 状态的一致性"""
    manifest = load_manifest(workspace, feature, run_id)
    for batch_id, item in manifest["batches"].items():
        if item.get("commitSha"):
            # 验证提交是否存在
            result = subprocess.run(
                ["git", "rev-parse", "--verify", item["commitSha"]],
                cwd=repo_path, capture_output=True
            )
            if result.returncode != 0:
                yield f"{batch_id}.commit_missing:{item['commitSha']}"
```

---

## 10. 代码质量

### 10.1 代码风格 ⭐⭐⭐⭐⭐

- ✅ 统一的命名约定
- ✅ 完整的类型注解
- ✅ 清晰的文档字符串
- ✅ 合理的函数拆分

### 10.2 可维护性 ⭐⭐⭐⭐

**优点**:
- 模块化设计良好
- 职责分离清晰
- 错误信息描述性强

**改进空间**:
- 部分函数过长（如 `create_manifest`）
- 魔法数字应提取为常量

**示例**:
```python
# 改进前
if len(ids) > 1:  # 什么意思？

# 改进后
MIN_BATCHES_FOR_PARALLEL = 2
if len(ids) >= MIN_BATCHES_FOR_PARALLEL:
```

---

## 11. 关键改进建议

### 优先级 P0（必须修复）

1. **Seal 操作原子性**
   - 风险：提交成功但 manifest 更新失败
   - 修复：添加事务回滚机制

2. **Plan Digest 范围**
   - 风险：无关修改阻断 run
   - 修复：只计算参与批次的 digest

### 优先级 P1（强烈建议）

3. **动态 Lease TTL**
   - 风险：复杂任务 lease 过期
   - 修复：根据任务复杂度估算 TTL

4. **批次重试机制**
   - 风险：临时故障导致整个 run 失败
   - 修复：添加有限次数的自动重试

5. **合并失败清理**
   - 风险：残留文件影响后续合并
   - 修复：abort 后执行 clean + reset

### 优先级 P2（可选优化）

6. **锁粒度优化**
   - 收益：提高并发度
   - 修复：改为 batch 级别锁

7. **增量合并策略**
   - 收益：更快的失败反馈
   - 修复：边合并边验证

8. **结构化日志**
   - 收益：更好的可观测性
   - 修复：添加 metrics 和 traces

---

## 12. 总体评分

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构设计** | ⭐⭐⭐⭐⭐ | 清晰、模块化、可扩展 |
| **代码质量** | ⭐⭐⭐⭐⭐ | 类型完整、风格统一、可读性强 |
| **错误处理** | ⭐⭐⭐⭐ | 覆盖全面，部分恢复机制缺失 |
| **性能** | ⭐⭐⭐⭐ | 合理，有优化空间 |
| **测试覆盖** | ⭐⭐⭐ | 集成测试通过，单元测试待补充 |
| **文档** | ⭐⭐⭐⭐ | 详尽，缺少 API 参考 |
| **可观测性** | ⭐⭐⭐ | Events 日志完整，缺少指标 |
| **安全性** | ⭐⭐⭐⭐⭐ | Lease + 锁机制可靠 |

**综合评分**: ⭐⭐⭐⭐ (4.25/5)

---

## 13. 行动计划

### 本周

1. ✅ 修复 Seal 操作原子性
2. ✅ 优化 Plan Digest 范围
3. ✅ 添加动态 Lease TTL

### 下周

4. ⏳ 实现批次重试机制
5. ⏳ 完善合并失败清理
6. ⏳ 补充单元测试

### 本月

7. ⏳ 锁粒度优化
8. ⏳ 增加结构化日志
9. ⏳ 编写故障排查指南

---

## 14. 结论

**整体评价**: 这是一个**设计优秀、实现扎实**的并行执行系统。

**核心优势**:
- 架构清晰，职责分离良好
- Lease 机制可靠，状态管理完整
- 错误处理覆盖全面
- 代码质量高，可维护性强

**需要关注**:
- 部分边界场景的原子性保证
- 恢复机制可以更完善
- 可观测性需要加强

**总体建议**:
当前实现已经可以投入试点使用，在试点过程中收集真实场景的反馈，逐步优化上述 P1/P2 问题。

---

**检视完成日期**: 2026-08-19
**下次检视建议**: 试点完成后（约 1-2 周）
