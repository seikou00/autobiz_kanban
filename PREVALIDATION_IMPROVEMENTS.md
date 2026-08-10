# 预校验改进总结

## 实施内容

已完成以下预校验增强，全部在 `set-draft-task-detail` 阶段触发：

### P0-1: designRefs/specRefs 格式校验前移

**新增模块**: `hooks/artifact_ref_validator.py` (241 行)

- 解析引用格式（必须包含 `#`，不能是 ` - `）
- 校验 anchor 格式（REQ-001, SCN-002, API-003, DATA-004, D-005）
- 检查 anchor 类型匹配（designRefs 只能引用 API/DATA/D，specRefs 只能引用 REQ/SCN）
- 验证文件存在性
- 验证 anchor 在文件中存在

**触发时机**: `set-draft-task-detail` → 写入任务详情前立即校验

**关于缓存**：`validate_artifact_ref()` / `validate_task_artifact_refs()` 保留了 `cache`
形参，但函数体内**不使用**它来跳过任何检查——格式、类型、文件存在性、anchor 存在性每次
都会重新执行。原因：引用字符串本身不足以判断结果是否仍然有效（文件可能被删除、anchor
可能被移除、同一引用可能从 `designRefs` 挪到 `specRefs` 从而类型要求改变）。在“暂不禁止
手工编辑正式 PLAN.md”的前提下，任何以引用字符串为键的缓存都可能放行已经失效的引用，
所以这里选择安全但重复计算，而不是走一个不安全的缓存路径。若未来要加缓存，键至少要包含
`refType + resolvedPath + fileSha256 + anchor`。

**错误示例**:
```json
{
  "reason": "invalid_artifact_ref_format",
  "detail": "task=T001;ref=design.md - API-001;引用缺少 # 符号"
}
```

### P0-2: 跨任务测试类冲突检测前移

**修改**: `hooks/plan_writer.py` `_cmd_set_draft_task_detail`

- 构建包含当前任务的临时 Draft
- 调用 `_duplicate_created_test_target_errors()` 检测与已有任务的测试类重名
- **在用户填写当前任务时立即拦截**，而非等到 `preflight-task-draft`

**触发时机**: `set-draft-task-detail` → 格式校验通过后，写入前检测冲突

**错误示例**:
```json
{
  "reason": "duplicate_created_test_target",
  "detail": "target=SimpleTest;taskIds=T001,T002;at=default:."
}
```

### P1: Maven 测试类歧义检测

**新增函数**: `hooks/validation_policy.py` `check_maven_test_target_ambiguity()`

- 检测简单类名（不含包路径）是否匹配多个不同包下的测试类
- 全限定类名不受此限制
- **遵循 `-pl`/`--projects` 聚合模块范围**：通过 `_maven_reactor_search_roots()` 只在
  命令实际会搜索的 reactor 模块内查找同名类；`mvn -pl module-a test -Dtest=SimpleTest`
  不会因为 `module-b` 里也有一个 `SimpleTest` 而被误报
- 坐标选择器（`-pl :artifactId`）无法映射到目录，回退为不限定范围的搜索，避免漏报
- 集成到 `_draft_task_validation_errors()` 中

**触发时机**: `set-draft-task-detail` → 验证命令校验阶段

**错误示例**:
```json
{
  "reason": "maven_test_selector_ambiguous",
  "detail": "task=T001;command=1;use_fully_qualified_class_name"
}
```

---

## 校验顺序（避免相互遮蔽）

`_cmd_set_draft_task_detail` 内部按以下顺序执行，前一步失败就不会进入下一步，这也是让
现有回归测试（校验字段缺失、验收范围越界等）在补充引用校验后仍然只报"一个目标错误"的关键：

1. `_normalize_draft_task_detail` — 结构/字段级校验（缺字段、类型不对等）
2. `_annotate_validation_test_plan` — 计算 Maven create/reuse 目标
3. `_draft_task_validation_errors` — 既有的任务级校验（granularity、workspace 等）
4. `validate_task_artifact_refs` — designRefs/specRefs 格式与存在性（本次新增）
5. 跨任务测试类冲突检测（本次新增）

---

## 校验流程对比

### 修改前
```
fill T001 → fill T002 → fill T003 → preflight-task-draft
                                    ↓
                        ❌ T002/T003 测试类重名
                        ❌ T001 designRefs 格式错误
用户浪费时间填写了 T003，发现 T001 就有问题
```

### 修改后
```
fill T001 → ❌ designRefs 格式错误（立即拦截）
修正 T001 → fill T002 → fill T003 → ❌ 与 T002 测试类重名（立即拦截）
                        ↓
            用户立即修改 T003，无需等到最后
```

---

## 阶段门保留

**preflight-task-draft 继续执行所有检查**，作为纵深防御：
- 防止直接修改 JSON 文件绕过交互式校验
- 作为最后一道关卡给出完整错误汇总
- 校验逻辑本身的 bug 保护

---

## 未实施项（按原计划）

**禁止手工编辑正式 PLAN.md**：需要 digest 机制，按用户明确要求本次不实施。

---

## 测试

新增/调整测试文件（均为 `unittest.TestCase`，可被 `python3 -m unittest discover -s tests`
发现和执行）：
- `tests/test_artifact_ref_validator.py` — 引用格式/类型/存在性校验，含"缓存参数不得
  跳过任何检查"的专项用例（11 个测试）
- `tests/test_maven_ambiguity.py` — Maven 歧义检测，含 `-pl` 范围限定、坐标选择器回退
  等边界（9 个测试）
- `tests/test_prevalidation_integration.py` — 驱动真实的 `plan_writer.py` CLI
  （`prepare-task-draft` → `set-draft-task-detail`），覆盖格式错误拦截、anchor 缺失
  拦截、跨任务测试类冲突拦截、正常任务写入成功（4 个测试）

此外为让既有回归通过，修复/调整了：
- `tests/test_json_writers.py` 中缺少 `_write_design(feature_dir)` fixture 的用例
  （这些用例原本使用了 `designRefs: ["design.md#D-001"]` 但没有创建 `design.md`，
  在引用不再被忽略之后必须补齐 fixture）

运行结果：全部既有测试（`python3 -m unittest discover -s tests`，779 个测试）加上
新增测试全部通过。

---

## 向后兼容

- 不引入任何新的持久化缓存字段，不改变 Draft/Lock 的磁盘结构
- 现有 CLI 调用方式、参数、返回结构不变
- 唯一的行为变化：`set-draft-task-detail` 现在会在写入前多做两类检查（引用、跨任务
  测试类冲突），因此某些之前会被接受、之后才在 preflight 报错的输入现在会更早报错
