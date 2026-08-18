# Code 阶段批次内迭代修改功能

## 快速概览

**问题**：批次中任务完成后，用户继续对话修改代码时被拒绝。

**解决**：允许在批次编译前重新启动已完成的任务。

## 核心变更

```python
# hooks/task_runner.py (line 723-730)
if normalize_status(task.get("status")) == "implemented" and not is_compile_repair:
    # 新增：仅在编译执行后才拒绝
    if compile_status != "pending":
        raise TaskRunnerError(...)
```

## 使用示例

```bash
# 第一次实现
start T1 → finish-implementation → status: implemented (rev 1)

# 继续修改（✅ 允许）
start T1 → finish-implementation → status: implemented (rev 2)

# 批次编译
batch-compile → compileStatus: passed

# 再次修改（❌ 拒绝）
start T1 → error: task_locked_after_compile
```

## 文档索引

- 📘 [技术设计](./CODE_STAGE_ITERATIVE_EDITING.md)
- 📗 [用户指南](./docs/code_stage_iterative_editing_guide.md)
- 📊 [流程图](./docs/code_stage_iterative_editing_diagrams.md)
- 📝 [变更总结](./CHANGE_SUMMARY.md)
- ✅ [实施报告](./IMPLEMENTATION_REPORT.md)

## 验证

```bash
# 快速验证
python scripts/verify_iterative_editing.py

# 完整测试
pytest tests/test_code_stage_iterative_editing.py -v
```

## 关键保障

- ✅ 编译前允许修改（compile_status = pending）
- ✅ 编译后锁定任务（compile_status = passed/failed/repairing）
- ✅ Evidence 完整追踪（所有版本保留）
- ✅ 向后兼容（不影响现有流程）

## 状态

**实施日期**：2026-08-13  
**状态**：✅ 完成，待审核  
**影响**：+7 -3 lines in task_runner.py
