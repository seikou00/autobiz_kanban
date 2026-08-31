# 乐观并行执行 MVP 实施文档

## 一、背景与目标

### 1.1 当前问题

现有批量执行系统采用"写集保守串行"策略：
- 同仓库、路径重叠的 Batch 被拆分成不同波次串行执行
- 即使依赖已满足，也会因写集冲突提前拆波
- 限制了并发度，影响整体执行效率

### 1.2 改造目标

**从"写集保守串行"切换为"依赖驱动的乐观并行"**：
- 普通 `parallel` 阶段：忽略写集重叠，所有依赖满足的 Batch 并行执行
- 冲突后置到 Merge Train 统一检测与处理
- 特殊阶段（proto/global/integration）保持强制串行

### 1.3 设计原则

1. **最小改动**：只替换普通 parallel 阶段的分组规则，约 700 行新增代码
2. **保持正确性**：依赖释放、candidate 验证、worktree 隔离机制不变
3. **明确状态**：利用现有 Merge Train，增加冲突状态与恢复路径
4. **运行时配置**：feature flag 控制，不污染 Plan 定义
5. **可回退**：配置开关可随时切回保守模式

---

## 二、架构变更

### 2.1 流程对比

#### 当前流程
```text
依赖满足 → 写集判冲突 → 拆波串行 → 单波 candidate 验证 → 推广
```

#### 改造后流程
```text
依赖已 merged
  ├─ proto/global/integration → 强制单 Batch 串行
  └─ 普通 parallel → 忽略写集，按 maxParallel 并发
                          ↓
              每仓库每波建立 candidate worktree
                          ↓
         ├─ Git 冲突 → candidate_conflicted（保留 worktree）
         ├─ B-INT 失败 → validation_failed（保留 worktree）
         └─ 通过 → fast-forward 推广到 main
                          ↓
         成功: 释放下游依赖
         失败: needs_resolution（阻塞下游，等待人工介入）
```

### 2.2 关键变化点

| 组件 | 当前行为 | 改造后行为 |
|---|---|---|
| **调度器** | 写集重叠 → 拆波串行 | 忽略写集，按 maxParallel 分组 |
| **Merge Train** | 候选合并失败 → 整体失败 | 区分 `candidate_conflicted` / `validation_failed` |
| **冲突处理** | 无明确流程 | 保留 worktree，记录冲突上下文，等待解决 |
| **依赖释放** | 上游 merged → 释放 | **不变**：仍需真实 merged |
| **预览** | 与实际执行不一致 | 复用真实调度器逻辑 |

---

## 三、代码改动清单

### 3.1 调度规则改造

**文件**: `hooks/parallel_runtime.py`

**改动点 1：增加配置开关**

```python
class ParallelRuntime:
    def __init__(self, plan: dict, config: dict = None):
        # ... 现有初始化
        self.config = config or {}
        
        # 新增配置项
        self.optimistic_parallel = self.config.get('parallelSchedulingMode') == 'optimistic'
        self.max_parallel = self.config.get('maxParallel', 4)
```

**改动点 2：替换分组逻辑（parallel_runtime.py:726）**

```python
def resource_groups(self, ready_batches: List[Batch]) -> List[List[Batch]]:
    """
    将 ready Batch 按资源冲突分组
    
    改造：
    - proto/global/integration 阶段：仍强制单 Batch
    - 普通 parallel 阶段 + optimistic 模式：忽略写集，按 maxParallel 分组
    - 保守模式：保持现有写集判断逻辑（向后兼容）
    """
    if not ready_batches:
        return []
    
    # 1. 检查是否有强制串行阶段
    critical_batches = [
        b for b in ready_batches 
        if b.phase in ['proto', 'global', 'integration']
    ]
    if critical_batches:
        # 每次只返回一个
        return [[critical_batches[0]]]
    
    # 2. 普通 parallel 阶段
    if self.optimistic_parallel:
        return self._optimistic_grouping(ready_batches)
    else:
        # 保持现有逻辑（写集判断）
        return self._conservative_grouping(ready_batches)

def _optimistic_grouping(self, batches: List[Batch]) -> List[List[Batch]]:
    """
    乐观分组：所有 ready Batch 进入同一波，受 maxParallel 限制
    """
    groups = []
    for i in range(0, len(batches), self.max_parallel):
        groups.append(batches[i:i + self.max_parallel])
    return groups

def _conservative_grouping(self, batches: List[Batch]) -> List[List[Batch]]:
    """
    保守分组：现有逻辑（写集重叠 → 串行）
    """
    # 保持现有 parallel_runtime.py:726-780 的代码
    # ...
```

**影响范围**：
- `parallel` 阶段的 Batch 不再因写集重叠拆波
- `maxParallel` 仍生效，限制单波最大 Batch 数
- 特殊阶段（proto/global/integration）行为不变

---

### 3.2 Merge Train 增加冲突状态

**文件**: `hooks/parallel_merge_train.py`

**改动点 1：定义冲突状态与上下文**

```python
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional

class CandidateStatus(Enum):
    BUILDING = "building"
    CLEAN = "clean"                      # 合并成功，等待验证
    VALIDATED = "validated"              # 验证通过，等待推广
    PROMOTED = "promoted"                # 已推广到 main
    CANDIDATE_CONFLICTED = "candidate_conflicted"  # Git 冲突
    VALIDATION_FAILED = "validation_failed"        # 验证失败
    NEEDS_RESOLUTION = "needs_resolution"          # 需要人工/Agent 解决

@dataclass
class ConflictContext:
    """冲突上下文，用于恢复和解决"""
    base_sha: str                        # 候选基于的 main SHA
    batch_ids: List[str]                 # 涉及的 Batch IDs
    conflicted_files: List[str]          # 冲突文件列表
    candidate_worktree: str              # 候选 worktree 路径
    conflict_markers: dict               # {file_path: conflict_content}
    attempts: int = 0                    # 解决尝试次数
```

**改动点 2：改造候选验证与推广（parallel_merge_train.py:95）**

```python
def validate_and_promote_wave(self, batches: List[Batch], wave_num: int) -> dict:
    """
    验证并推广一波 Batch
    
    返回：
    {
        'status': 'promoted' | 'candidate_conflicted' | 'validation_failed',
        'merged_batches': [...],  # 成功合并的 Batch IDs
        'conflict_context': ConflictContext | None
    }
    """
    repo = batches[0].repo
    repo_path = batches[0].repo_path
    
    # 1. 创建 candidate worktree（使用现有 worktree 管理）
    candidate_name = f"candidate_wave{wave_num}_{repo}"
    candidate_worktree = self._create_candidate_worktree(repo_path, candidate_name)
    
    try:
        # 2. 获取 base SHA（当前 main HEAD）
        base_sha = self._get_current_main_sha(repo_path)
        
        # 3. 尝试合并所有 Batch 到 candidate
        merge_result = self._merge_batches_to_candidate(
            batches, candidate_worktree, base_sha
        )
        
        if merge_result['status'] == 'conflict':
            # Git 冲突 → 记录状态，保留 worktree
            conflict_ctx = ConflictContext(
                base_sha=base_sha,
                batch_ids=[b.id for b in batches],
                conflicted_files=merge_result['conflicted_files'],
                candidate_worktree=candidate_worktree,
                conflict_markers=self._extract_conflict_markers(
                    candidate_worktree, merge_result['conflicted_files']
                ),
                attempts=0
            )
            
            self.logger.warning(
                f"Wave {wave_num}: Git conflict detected. "
                f"Files: {conflict_ctx.conflicted_files}"
            )
            
            return {
                'status': 'candidate_conflicted',
                'merged_batches': [],
                'conflict_context': conflict_ctx
            }
        
        # 4. 运行 B-INT 验证
        candidate_sha = merge_result['candidate_sha']
        validation_result = self._run_batch_integration_tests(
            candidate_worktree, batches
        )
        
        if not validation_result['passed']:
            # 验证失败 → 保留 worktree
            self.logger.error(
                f"Wave {wave_num}: B-INT failed. "
                f"Details: {validation_result['failures']}"
            )
            
            return {
                'status': 'validation_failed',
                'merged_batches': [],
                'validation_details': validation_result,
                'candidate_worktree': candidate_worktree
            }
        
        # 5. 推广到 main（fast-forward）
        promote_result = self._promote_candidate_to_main(
            repo_path, candidate_sha, candidate_name
        )
        
        if not promote_result['success']:
            self.logger.error(
                f"Wave {wave_num}: Promotion failed. "
                f"Reason: {promote_result['reason']}"
            )
            return {
                'status': 'promotion_failed',
                'merged_batches': [],
                'reason': promote_result['reason']
            }
        
        # 6. 成功 → 清理 worktree
        self._cleanup_candidate_worktree(candidate_worktree)
        
        return {
            'status': 'promoted',
            'merged_batches': [b.id for b in batches],
            'promoted_sha': promote_result['sha']
        }
    
    except Exception as e:
        self.logger.exception(f"Wave {wave_num}: Unexpected error")
        # 保留 worktree 用于诊断
        return {
            'status': 'error',
            'merged_batches': [],
            'error': str(e),
            'candidate_worktree': candidate_worktree
        }
```

**改动点 3：合并到候选分支**

```python
def _merge_batches_to_candidate(self, batches: List[Batch], 
                                 worktree_path: str, base_sha: str) -> dict:
    """
    将多个 Batch 合并到 candidate 分支
    
    关键：在 candidate worktree 内操作，不污染主仓库 checkout
    
    返回：
    {
        'status': 'success' | 'conflict',
        'candidate_sha': str | None,
        'conflicted_files': List[str]
    }
    """
    for batch in batches:
        result = subprocess.run(
            ['git', 'merge', '--no-ff', '--no-commit', batch.branch],
            cwd=worktree_path,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            # 检测冲突
            conflicted_files = self._parse_conflicted_files(result.stderr)
            
            return {
                'status': 'conflict',
                'candidate_sha': None,
                'conflicted_files': conflicted_files
            }
    
    # 所有 Batch 合并成功 → 提交
    subprocess.run(
        ['git', 'commit', '-m', f'Merge wave: {", ".join(b.id for b in batches)}'],
        cwd=worktree_path,
        check=True
    )
    
    candidate_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()
    
    return {
        'status': 'success',
        'candidate_sha': candidate_sha,
        'conflicted_files': []
    }

def _parse_conflicted_files(self, git_error_output: str) -> List[str]:
    """从 Git 错误输出中提取冲突文件"""
    import re
    pattern = r'CONFLICT \(.*?\): Merge conflict in (.+)'
    matches = re.findall(pattern, git_error_output)
    return matches

def _extract_conflict_markers(self, worktree_path: str, 
                               conflicted_files: List[str]) -> dict:
    """读取冲突文件的内容（包含 <<<< ==== >>>> 标记）"""
    conflict_contents = {}
    for file_path in conflicted_files:
        full_path = Path(worktree_path) / file_path
        if full_path.exists():
            conflict_contents[file_path] = full_path.read_text()
    return conflict_contents

def _create_candidate_worktree(self, repo_path: str, candidate_name: str) -> str:
    """
    创建 candidate worktree
    
    重要：使用现有的 worktree 管理机制，不直接操作主仓库 checkout
    """
    worktree_path = Path(repo_path) / '.git' / 'worktrees' / candidate_name
    
    subprocess.run(
        ['git', 'worktree', 'add', '-b', candidate_name, 
         str(worktree_path), 'origin/main'],
        cwd=repo_path,
        check=True,
        capture_output=True
    )
    
    return str(worktree_path)

def _cleanup_candidate_worktree(self, worktree_path: str):
    """清理 candidate worktree（仅在成功推广后）"""
    subprocess.run(
        ['git', 'worktree', 'remove', worktree_path, '--force'],
        capture_output=True
    )
```

**关键设计**：
- **不污染主仓库 checkout**：所有操作在 candidate worktree 内完成
- **冲突时保留 worktree**：便于集成 Agent 或人工解决
- **明确状态返回**：调用方可根据状态决定后续动作

---

### 3.3 冲突解决 Agent

**文件**: `hooks/conflict_resolution_agent.py`（新增）

```python
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
import logging

@dataclass
class ResolutionResult:
    status: str  # 'resolved' | 'manual_required'
    resolved_files: List[str]
    unresolved_files: List[str]
    new_candidate_sha: Optional[str] = None
    reason: Optional[str] = None

class ConflictResolutionAgent:
    """
    MVP 版本：不做自动文本合并，由单个集成 Agent 在 worktree 中解决
    """
    
    def __init__(self, max_attempts: int = 2):
        self.max_attempts = max_attempts
        self.logger = logging.getLogger(__name__)
    
    def resolve_conflict(self, conflict_ctx: 'ConflictContext') -> ResolutionResult:
        """
        解决冲突候选
        
        MVP 策略：
        1. 不自动合并文本（太危险）
        2. 调用一个受控的集成 Agent，在 candidate worktree 中解决
        3. Agent 需要：
           - 理解双方改动意图
           - 手动编辑冲突文件
           - 保留业务语义
        """
        if conflict_ctx.attempts >= self.max_attempts:
            return ResolutionResult(
                status='manual_required',
                resolved_files=[],
                unresolved_files=conflict_ctx.conflicted_files,
                reason=f'Exceeded max attempts ({self.max_attempts})'
            )
        
        self.logger.info(
            f"Attempting conflict resolution (attempt {conflict_ctx.attempts + 1}). "
            f"Batches: {conflict_ctx.batch_ids}"
        )
        
        # MVP: 返回 manual_required，等待后续集成 Agent
        return ResolutionResult(
            status='manual_required',
            resolved_files=[],
            unresolved_files=conflict_ctx.conflicted_files,
            reason='Automatic resolution not implemented in MVP. Agent intervention required.'
        )
    
    def notify_manual_intervention(self, conflict_ctx: 'ConflictContext'):
        """
        通知需要人工介入
        
        输出：
        - Worktree 路径
        - 冲突文件列表
        - 涉及的 Batch IDs
        - 解决指引
        """
        message = f"""
╔══════════════════════════════════════════════════════════════
║ CONFLICT RESOLUTION REQUIRED
╠══════════════════════════════════════════════════════════════
║ Batches:  {', '.join(conflict_ctx.batch_ids)}
║ Base SHA: {conflict_ctx.base_sha[:8]}
║ Worktree: {conflict_ctx.candidate_worktree}
║ 
║ Conflicted files:
║   {chr(10).join('  - ' + f for f in conflict_ctx.conflicted_files)}
║ 
║ Next steps:
║   1. cd {conflict_ctx.candidate_worktree}
║   2. Manually resolve conflicts in the files above
║   3. git add <resolved_files>
║   4. git commit
║   5. Run: autobiz resume-merge-train --candidate {Path(conflict_ctx.candidate_worktree).name}
║ 
║ Or: autobiz discard-candidate --candidate {Path(conflict_ctx.candidate_worktree).name}
╚══════════════════════════════════════════════════════════════
"""
        print(message)
        self.logger.warning(message)
```

**职责**：
- MVP 阶段仅通知人工介入
- 预留接口供后续集成自动解决能力
- 限制尝试次数，避免无限循环

---

### 3.4 Coordinator 集成

**文件**: `hooks/repository_workflow_coordinator.py`

**改动点：处理 Merge Train 返回的新状态（_process_ready_batches 方法）**

```python
def _process_ready_batches(self, repo: str, ready_batches: List[Batch]):
    """
    处理该仓库的就绪 Batch
    
    改动：处理 candidate_conflicted 状态
    """
    # 1. 调度器分组
    groups = self.scheduler.resource_groups(ready_batches)
    
    for wave_num, group in enumerate(groups):
        self.logger.info(f"Processing wave {wave_num} for {repo}: {[b.id for b in group]}")
        
        # 2. 并行执行实现
        impl_results = self._parallel_implement(group)
        successful = [b for b, r in zip(group, impl_results) if r['success']]
        
        if not successful:
            self.logger.warning(f"Wave {wave_num}: No successful implementations")
            continue
        
        # 3. Merge Train 验证与推广
        merge_result = self.merge_train.validate_and_promote_wave(successful, wave_num)
        
        # 4. 根据状态处理
        if merge_result['status'] == 'promoted':
            # 成功 → 标记 Batch 为 merged，释放下游依赖
            for batch_id in merge_result['merged_batches']:
                self._mark_batch_merged(batch_id, merge_result['promoted_sha'])
        
        elif merge_result['status'] == 'candidate_conflicted':
            # 冲突 → 尝试解决
            conflict_ctx = merge_result['conflict_context']
            resolution_agent = ConflictResolutionAgent()
            
            resolution_result = resolution_agent.resolve_conflict(conflict_ctx)
            
            if resolution_result.status == 'resolved':
                # 重新验证并推广
                retry_result = self.merge_train.validate_and_promote_wave(
                    successful, wave_num
                )
                if retry_result['status'] == 'promoted':
                    for batch_id in retry_result['merged_batches']:
                        self._mark_batch_merged(batch_id, retry_result['promoted_sha'])
                else:
                    # 解决后仍失败 → 人工介入
                    self._handle_unresolved_conflict(conflict_ctx, resolution_result)
            else:
                # 无法自动解决 → 人工介入
                resolution_agent.notify_manual_intervention(conflict_ctx)
                self._handle_unresolved_conflict(conflict_ctx, resolution_result)
        
        elif merge_result['status'] == 'validation_failed':
            # 验证失败 → 标记 Batch，保留 worktree
            self.logger.error(
                f"Wave {wave_num}: Validation failed. "
                f"Worktree: {merge_result.get('candidate_worktree')}"
            )
            for batch in successful:
                self._mark_batch_failed(batch.id, 'validation_failed')

def _handle_unresolved_conflict(self, conflict_ctx: 'ConflictContext', 
                                  resolution_result: ResolutionResult):
    """
    处理无法自动解决的冲突
    
    行为：
    - 标记涉及的 Batch 为 needs_resolution
    - 不释放下游依赖（阻塞）
    - 保留 candidate worktree
    - 记录状态到 manifest
    """
    for batch_id in conflict_ctx.batch_ids:
        self._mark_batch_needs_resolution(
            batch_id, 
            worktree=conflict_ctx.candidate_worktree,
            reason=resolution_result.reason
        )
    
    self.logger.warning(
        f"Batches {conflict_ctx.batch_ids} blocked due to unresolved conflicts. "
        f"Downstream batches will not start."
    )
```

**关键行为**：
- `candidate_conflicted` 时尝试调用 resolution agent
- 解决失败时**不释放下游依赖**，保持阻塞
- 保留 candidate worktree，便于人工介入

---

### 3.5 预览逻辑修复

**文件**: `hooks/workflow_launcher.py`

**改动点：复用真实调度器的分组逻辑（preview_execution_plan 方法，约 300 行）**

```python
def preview_execution_plan(self, plan: dict) -> str:
    """
    预览执行计划的波次划分
    
    改动：复用真实 ParallelRuntime 的分组逻辑
    """
    from parallel_runtime import ParallelRuntime
    
    # 使用与实际执行相同的配置
    runtime_config = self._load_runtime_config()
    scheduler = ParallelRuntime(plan, config=runtime_config)
    
    # 模拟依赖释放，按拓扑排序
    batches = self._build_batch_objects(plan['batches'])
    topo_sorted = self._topological_sort(batches)
    
    preview_output = ["Execution Plan Preview", "=" * 60, ""]
    
    wave_num = 0
    remaining = topo_sorted.copy()
    merged = set()
    
    while remaining:
        # 找出当前可执行的 Batch（依赖都在 merged 中）
        ready = [
            b for b in remaining 
            if all(dep in merged for dep in b.dependencies)
        ]
        
        if not ready:
            # 检测死锁
            preview_output.append(f"⚠️  Deadlock detected. Remaining: {[b.id for b in remaining]}")
            break
        
        # 调用真实调度器分组
        groups = scheduler.resource_groups(ready)
        
        for group in groups:
            wave_num += 1
            preview_output.append(f"Wave {wave_num}:")
            preview_output.append(f"  Batches: {', '.join(b.id for b in group)}")
            
            # 显示并发策略
            if group[0].phase in ['proto', 'global', 'integration']:
                preview_output.append(f"  Strategy: Serial (critical phase)")
            elif scheduler.optimistic_parallel:
                preview_output.append(f"  Strategy: Optimistic parallel (maxParallel={scheduler.max_parallel})")
            else:
                preview_output.append(f"  Strategy: Conservative (write-set conflict avoidance)")
            
            # 显示风险提示（如果有 writeSet）
            overlapping_files = self._find_write_set_overlap(group)
            if overlapping_files:
                preview_output.append(f"  ⚠️  Write-set overlap: {', '.join(overlapping_files)}")
                preview_output.append(f"      Conflicts will be resolved in Merge Train")
            
            preview_output.append("")
            
            # 模拟合并
            for batch in group:
                merged.add(batch.id)
                remaining.remove(batch)
    
    return "\n".join(preview_output)

def _find_write_set_overlap(self, batches: List[Batch]) -> List[str]:
    """查找批次间写集重叠（仅用于预览风险提示）"""
    all_files = set()
    overlapping = set()
    
    for batch in batches:
        batch_files = set(batch.write_set or [])
        overlapping.update(all_files & batch_files)
        all_files.update(batch_files)
    
    return list(overlapping)

def _load_runtime_config(self) -> dict:
    """加载运行时配置"""
    config_path = Path('.autobiz/runtime_config.json')
    
    if config_path.exists():
        return json.loads(config_path.read_text())
    
    # 默认配置（保守模式）
    return {
        'parallelSchedulingMode': 'conservative',
        'maxParallel': 4
    }
```

**效果**：
- 预览与实际执行使用相同的分组规则
- 显示写集重叠作为**风险提示**，而非决定性的串行原因
- 明确标注策略：`Optimistic parallel` 或 `Conservative`

---

### 3.6 运行时配置

**文件**: `.autobiz/runtime_config.json`（新增，项目级配置）

```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 4,
  "conflictResolution": {
    "maxAttempts": 2,
    "notifyOnManualRequired": true
  }
}
```

**不污染 Plan**：
- `plan.json` 仍只包含 Batch 定义和依赖
- 执行策略在运行时配置中，按项目/团队统一管理

---

## 四、测试覆盖

### 4.1 单元测试

**文件**: `tests/test_optimistic_parallel.py`（新增）

```python
import pytest
from hooks.parallel_runtime import ParallelRuntime, Batch

def test_optimistic_grouping_ignores_write_set():
    """乐观模式：忽略写集重叠，按 maxParallel 分组"""
    config = {'parallelSchedulingMode': 'optimistic', 'maxParallel': 3}
    runtime = ParallelRuntime(plan={}, config=config)
    
    batches = [
        Batch('B001', phase='parallel', write_set=['src/core.py']),
        Batch('B002', phase='parallel', write_set=['src/core.py']),
        Batch('B003', phase='parallel', write_set=['src/api.py']),
        Batch('B004', phase='parallel', write_set=['src/api.py'])
    ]
    
    groups = runtime.resource_groups(batches)
    
    # 期望：2 组，每组最多 3 个
    assert len(groups) == 2
    assert len(groups[0]) == 3
    assert len(groups[1]) == 1

def test_critical_phase_still_serial():
    """proto/global/integration 阶段仍强制串行"""
    config = {'parallelSchedulingMode': 'optimistic'}
    runtime = ParallelRuntime(plan={}, config=config)
    
    batches = [
        Batch('B001', phase='proto', write_set=[]),
        Batch('B002', phase='proto', write_set=[])
    ]
    
    groups = runtime.resource_groups(batches)
    
    # 期望：每次只返回 1 个
    assert len(groups) == 1
    assert len(groups[0]) == 1
    assert groups[0][0].id == 'B001'

def test_conservative_mode_respects_write_set():
    """保守模式：写集重叠仍拆波"""
    config = {'parallelSchedulingMode': 'conservative'}
    runtime = ParallelRuntime(plan={}, config=config)
    
    batches = [
        Batch('B001', phase='parallel', write_set=['src/core.py']),
        Batch('B002', phase='parallel', write_set=['src/core.py'])
    ]
    
    groups = runtime.resource_groups(batches)
    
    # 期望：拆成 2 组
    assert len(groups) >= 2

def test_merge_train_detects_conflict():
    """Merge Train 应检测 Git 冲突并返回正确状态"""
    merge_train = MergeTrain()
    
    # Mock: 两个 Batch 修改同一行
    batch1 = Batch('B001', branch='feature/b001')
    batch2 = Batch('B002', branch='feature/b002')
    
    result = merge_train.validate_and_promote_wave([batch1, batch2], wave_num=1)
    
    # 期望：冲突状态
    assert result['status'] == 'candidate_conflicted'
    assert 'conflict_context' in result
    assert len(result['conflict_context'].conflicted_files) > 0

def test_conflict_context_preserves_worktree():
    """冲突时应保留 candidate worktree"""
    merge_train = MergeTrain()
    
    result = merge_train.validate_and_promote_wave([...], wave_num=1)
    
    if result['status'] == 'candidate_conflicted':
        worktree_path = result['conflict_context'].candidate_worktree
        assert Path(worktree_path).exists()
        assert (Path(worktree_path) / '.git').exists()

def test_resolution_agent_limits_attempts():
    """解决 Agent 应限制尝试次数"""
    agent = ConflictResolutionAgent(max_attempts=2)
    
    conflict_ctx = ConflictContext(
        base_sha='abc123',
        batch_ids=['B001', 'B002'],
        conflicted_files=['src/core.py'],
        candidate_worktree='/tmp/candidate',
        conflict_markers={},
        attempts=2  # 已达上限
    )
    
    result = agent.resolve_conflict(conflict_ctx)
    
    assert result.status == 'manual_required'
    assert 'Exceeded max attempts' in result.reason
```

### 4.2 集成测试

**文件**: `tests/integration/test_optimistic_workflow.py`（新增）

```python
def test_full_workflow_no_conflict():
    """完整工作流：无冲突场景"""
    plan = {
        'batches': [
            {'id': 'B001', 'repo': 'backend', 'dependencies': [], 'phase': 'parallel'},
            {'id': 'B002', 'repo': 'backend', 'dependencies': [], 'phase': 'parallel'},
            {'id': 'B003', 'repo': 'backend', 'dependencies': ['B001', 'B002'], 'phase': 'parallel'}
        ]
    }
    
    config = {'parallelSchedulingMode': 'optimistic', 'maxParallel': 2}
    coordinator = RepositoryWorkflowCoordinator(plan, config)
    
    result = coordinator.execute()
    
    # 期望：
    # - Wave 1: B001, B002 并行
    # - Wave 2: B003（等待依赖）
    # - 所有 Batch 成功合并
    assert result['status'] == 'success'
    assert set(result['merged_batches']) == {'B001', 'B002', 'B003'}

def test_full_workflow_with_conflict():
    """完整工作流：冲突场景"""
    # Mock: B001 和 B002 修改同一文件同一行
    plan = {
        'batches': [
            {'id': 'B001', 'repo': 'backend', 'writeSet': ['src/core.py:10']},
            {'id': 'B002', 'repo': 'backend', 'writeSet': ['src/core.py:10']}
        ]
    }
    
    config = {'parallelSchedulingMode': 'optimistic'}
    coordinator = RepositoryWorkflowCoordinator(plan, config)
    
    result = coordinator.execute()
    
    # 期望：
    # - B001 和 B002 并行执行
    # - Merge Train 检测到冲突
    # - Resolution agent 尝试解决（MVP 返回 manual_required）
    # - Batches 标记为 needs_resolution
    assert result['status'] == 'partial'
    assert len(result['needs_resolution']) > 0

def test_preview_matches_actual_execution():
    """预览的波次应与实际执行一致"""
    plan = {...}
    config = {'parallelSchedulingMode': 'optimistic'}
    
    launcher = WorkflowLauncher(plan, config)
    preview = launcher.preview_execution_plan(plan)
    
    # 提取预览中的波次
    preview_waves = parse_waves_from_preview(preview)
    
    # 执行并记录实际波次
    coordinator = RepositoryWorkflowCoordinator(plan, config)
    coordinator.execute()
    actual_waves = coordinator.get_executed_waves()
    
    # 期望：完全一致
    assert preview_waves == actual_waves
```

---

## 五、用户文档

### 5.1 启用乐观并行

在项目根目录创建 `.autobiz/runtime_config.json`：

```json
{
  "parallelSchedulingMode": "optimistic",
  "maxParallel": 4
}
```

### 5.2 行为变化

#### 保守模式（默认）
- 同仓库、写集重叠的 Batch 串行执行
- 提前避免 Git 冲突

#### 乐观模式
- 所有依赖满足的 Batch 并行执行（受 `maxParallel` 限制）
- Git 冲突在 Merge Train 中检测
- 冲突时保留 candidate worktree，等待人工解决

### 5.3 冲突处理流程

当 Merge Train 检测到 Git 冲突时：

**1. 系统输出冲突详情**
```
╔══════════════════════════════════════
║ CONFLICT RESOLUTION REQUIRED
║ Batches:  B001, B002
║ Worktree: /path/to/candidate_wave1_backend
║ Conflicted files:
║   - src/core.py
║   - src/api.py
╚══════════════════════════════════════
```

**2. 手动解决**
```bash
cd /path/to/candidate_wave1_backend

# 编辑冲突文件，保留双方业务意图
vim src/core.py

# 标记为已解决
git add src/core.py src/api.py

# 提交
git commit -m "Resolve conflicts between B001 and B002"

# 恢复执行
autobiz resume-merge-train --candidate candidate_wave1_backend
```

**3. 或放弃候选**
```bash
autobiz discard-candidate --candidate candidate_wave1_backend
```

### 5.4 何时使用乐观模式

**适合**：
- 独立的功能开发（不同模块）
- 团队协作密集，但改动分散
- 对并发度有较高要求

**不适合**：
- 高度耦合的代码（频繁修改相同文件）
- 重构类任务（大范围改动）
- 缺少人工介入能力的自动化流程

### 5.5 CLI 命令

```bash
# 查看当前执行状态
autobiz status

# 恢复冲突的 candidate
autobiz resume-merge-train --candidate <candidate_name>

# 放弃冲突的 candidate
autobiz discard-candidate --candidate <candidate_name>

# 查看冲突详情
autobiz show-conflict --candidate <candidate_name>

# 清理所有 candidate worktree
autobiz cleanup-worktrees
```

---

## 六、实施清单

### Week 1: 基础改造
- [ ] 实现 `ParallelRuntime._optimistic_grouping()`
- [ ] 增加配置开关 `parallelSchedulingMode`
- [ ] 单元测试：分组逻辑

### Week 2: Merge Train 状态改造
- [ ] 定义 `CandidateStatus` 和 `ConflictContext`
- [ ] 改造 `validate_and_promote_wave()`：检测冲突并保留 worktree
- [ ] 单元测试：冲突检测

### Week 3: Coordinator 集成
- [ ] 修改 `repository_workflow_coordinator.py`：处理 `candidate_conflicted`
- [ ] 实现 `ConflictResolutionAgent`（MVP 版，返回 manual_required）
- [ ] 集成测试：完整流程

### Week 4: 预览与文档
- [ ] 修复 `workflow_launcher.py` 预览逻辑
- [ ] 编写用户文档
- [ ] 增加 CLI 命令

### Week 5: 验证与灰度
- [ ] 在测试项目上启用 `optimistic` 模式
- [ ] 收集冲突率、解决成功率数据
- [ ] 根据反馈调整阈值和策略

---

## 七、风险与回退

### 7.1 已知风险

| 风险 | 缓解措施 |
|---|---|
| 冲突率过高 | 配置开关可随时切回 `conservative` |
| 人工解决成本高 | 限制重试次数，保留 worktree 便于诊断 |
| 下游依赖阻塞 | 明确的 `needs_resolution` 状态，不释放下游 |
| Worktree 泄漏 | 失败时保留，提供清理命令 |

### 7.2 回退方案

**如果生产环境出现问题**：

1. **立即回退**
```json
{
  "parallelSchedulingMode": "conservative"
}
```

2. **清理遗留 worktree**
```bash
git worktree list
git worktree remove --force <worktree_path>
```

3. **数据不丢失**
- 所有 Batch 分支保留
- Candidate worktree 保留（可手动推广）

---

## 八、后续演进方向

MVP 稳定后，可考虑：

### 8.1 简单冲突自动解决
- 仅处理"追加式冲突"（两边都在类末尾添加方法）
- 需要 AST 级别的分析

### 8.2 智能降级
- 实时监控冲突率
- 自动切换 `optimistic` ↔ `conservative`

### 8.3 冲突预测
- 基于历史数据预测高风险 Batch 对
- 有选择地串行化

### 8.4 分布式 Merge Train
- 多个 candidate 并行验证
- 按确定性顺序推广

---

## 九、代码量估算

| 模块 | 新增 | 修改 | 总计 |
|---|---|---|---|
| `parallel_runtime.py` | 40 | 20 | 60 |
| `parallel_merge_train.py` | 120 | 30 | 150 |
| `conflict_resolution_agent.py` | 80 | 0 | 80 |
| `repository_workflow_coordinator.py` | 50 | 20 | 70 |
| `workflow_launcher.py` | 30 | 10 | 40 |
| 测试代码 | 200 | 0 | 200 |
| 文档 | 100 | 0 | 100 |
| **总计** | **620** | **80** | **700** |

**实际预估**：约 **700-800 行**（含注释、文档字符串），远低于初版方案的 2000 行。

---

## 十、关键设计决策总结

### 10.1 保持现有正确性
- **依赖释放契约不变**：上游必须真实 `merged` 后才释放下游
- **Worktree 隔离不变**：所有 Git 操作在独立 worktree 中进行
- **Candidate 验证流程不变**：B-INT 通过后才推广

### 10.2 最小改动范围
- **只改普通 parallel 阶段**：proto/global/integration 保持串行
- **只改分组规则**：执行、验证、推广逻辑复用现有代码
- **向后兼容**：保守模式保持现有行为

### 10.3 明确状态与恢复
- **区分冲突类型**：`candidate_conflicted` vs `validation_failed`
- **保留冲突现场**：candidate worktree + 冲突文件内容
- **阻塞下游依赖**：冲突未解决时不释放

### 10.4 运行时配置
- **不污染 Plan**：执行策略在 `.autobiz/runtime_config.json`
- **项目级控制**：团队统一决定使用乐观/保守模式
- **随时可切换**：feature flag 控制，无需修改代码

---

## 附录：关键文件清单

### 修改的现有文件
1. `hooks/parallel_runtime.py` - 调度规则改造
2. `hooks/parallel_merge_train.py` - 冲突状态与检测
3. `hooks/repository_workflow_coordinator.py` - 冲突处理集成
4. `hooks/workflow_launcher.py` - 预览逻辑修复

### 新增文件
1. `hooks/conflict_resolution_agent.py` - 冲突解决 Agent
2. `.autobiz/runtime_config.json` - 运行时配置
3. `tests/test_optimistic_parallel.py` - 单元测试
4. `tests/integration/test_optimistic_workflow.py` - 集成测试
5. `docs/optimistic_parallel_execution.md` - 用户文档

---

**预估工期**：4-5 周  
**代码量**：约 700-800 行（含测试和文档）  
**核心改动**：80 行现有代码  
**风险等级**：低（可随时回退，不破坏现有功能）
