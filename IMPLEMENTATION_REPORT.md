# 预校验改进实施报告

## 问题回顾

用户反馈的核心问题：
1. **designRefs 格式错误**（`design.md - API-001` 使用 ` - ` 而非 `#`）可以进入 Draft，直到执行时才报错
2. **测试类重名**（两个任务都创建 `SimpleTest`）可以填入 Draft，在 `preflight-task-draft` 时才发现冲突

**根本原因**：校验职责后置，错误可以进入 Draft 阶段，用户浪费时间填写后续任务。

本报告同时记录了第一版实现在代码评审中被指出的三个问题，以及对应的修复。

---

## 实施方案

### ✅ P0-1: designRefs/specRefs 格式校验前移

**新增文件**: `hooks/artifact_ref_validator.py` (241 行)

**核心功能**:
- 解析引用格式：必须包含 `#`，不接受 ` - ` 等变体
- 校验 anchor 格式：REQ-001, SCN-002, API-003, DATA-004, D-005
- 类型匹配：designRefs 只能引用 API/DATA/D，specRefs 只能引用 REQ/SCN
- 文件存在性检查
- Anchor 在文件中的存在性检查（表格行或 Markdown 标题）

**触发时机**: `set-draft-task-detail` → 任务详情写入前

**关于缓存（评审修复）**：初版实现引入了一个以引用字符串为键的持久化缓存，一旦校验
通过就不再重新检查文件是否存在、anchor 是否还在。评审指出这会让已经失效的引用（文件被
删除、anchor 被移除、引用从 `designRefs` 移到 `specRefs`）永久放行，在"暂不禁止手工编辑"
的前提下尤其危险。**修复**：移除了这个不安全的持久化缓存；`cache` 形参仍被接受（保持
签名兼容）但函数体内不再用它跳过任何检查——格式、类型、文件、anchor 每次都重新校验。
新增专项测试 `test_cache_does_not_skip_format_or_type_checks` 断言这一点。

### ✅ P0-2: 跨任务测试类冲突检测前移

**修改文件**: `hooks/plan_writer.py` `_cmd_set_draft_task_detail()`

**核心逻辑**: 构建包含当前候选任务的临时 Draft 视图，调用既有的
`_duplicate_created_test_target_errors()` 检测与已 ready 任务的测试类冲突，写入前拒绝。

**触发时机**: `set-draft-task-detail` → 格式校验通过后、写入前

**效果**:
- **修改前**: T001 → T002 → T003 → preflight 发现 T001/T002 冲突
- **修改后**: T001（ready）→ T002 填写时立即发现与 T001 冲突

**评审修复**：初版的"集成测试"只是打印说明文字，没有真正驱动 CLI。现在
`tests/test_prevalidation_integration.py::test_set_draft_task_detail_rejects_cross_task_test_class_conflict`
真实调用 `prepare-task-draft` → `set-draft-task-detail`（T001）→
`set-draft-task-detail`（T002，同名测试类）并断言第二次调用返回非零、错误码为
`duplicate_created_test_target`，且 T002 的内容没有被写入磁盘上的 Draft 批次文件。

### ✅ P1: Maven 测试类歧义检测

**新增函数**: `hooks/validation_policy.py` `check_maven_test_target_ambiguity()`

**检测规则**:
- 简单类名（不含 `.`）在**命令实际会搜索的 reactor 范围内**匹配多个不同包 → 报错
- 全限定类名（`com.example.SimpleTest`）→ 放行
- 同一包下多个文件 → 放行

**评审修复**：初版直接在整个 `command_dir` 下 `rglob` 搜索，没有理会 `-pl`/`--projects`。
`mvn -pl module-a test -Dtest=SimpleTest` 会被误报为歧义，即使 `module-b` 的同名类根本
不在这次 Maven 调用的 reactor 范围内。**修复**：新增 `_maven_reactor_search_roots()`，
解析 `-pl`/`--projects` 的路径型选择器，把搜索范围限定到这些解析出的模块目录；坐标型
选择器（`-pl :artifactId`）无法从文件系统映射到目录，保持不限定范围搜索以避免漏报。
新增测试覆盖：`test_pl_scoping_excludes_other_module`（不同模块同名类不报歧义）、
`test_pl_scoping_still_flags_ambiguity_within_selected_module`（同一 `-pl` 范围内仍报
歧义）、`test_coordinate_selector_falls_back_to_unrestricted_search`。

**示例**:
```bash
# ❌ 歧义：src/test/java/foo/SimpleTest.java 和 src/test/java/bar/SimpleTest.java 都在搜索范围内
mvn test -Dtest=SimpleTest

# ✅ 不歧义：-pl 把搜索限定到 module-a，module-b 的同名类不参与判断
mvn -pl module-a test -Dtest=SimpleTest

# ✅ 明确：使用全限定类名
mvn test -Dtest=com.example.foo.SimpleTest
```

**集成位置**: `_draft_task_validation_errors()` 调用链中

### 增量缓存：未采用

原计划的 P2（增量校验缓存）与 P0-1 的安全缓存问题是同一件事——没有找到一种以引用
字符串为键、又能安全反映文件/anchor 变化的缓存方式。按用户"先不要禁止手工编辑"的约束，
这里选择不做缓存，代价是每次 `set-draft-task-detail` 都会重新读取并解析涉及的
design.md/specs 文件。这些文件通常很小（几十到几百行），实测未观察到明显延迟；如果
未来确认需要缓存，键必须是 `refType + resolvedPath + fileSha256 + anchor`，而不是
引用字符串本身。

---

## 测试覆盖

### 单元测试（`unittest.TestCase`，非 pytest 风格函数）

**`tests/test_artifact_ref_validator.py`** (11 个测试):
```
✓ test_valid_design_ref_with_path
✓ test_valid_design_ref_short_form
✓ test_invalid_ref_missing_hash                    # "design.md - API-001" 被拦截
✓ test_invalid_ref_wrong_anchor_format             # "design.md#INVALID" 被拦截
✓ test_invalid_ref_wrong_anchor_type                # REQ 不能在 designRefs
✓ test_invalid_ref_file_not_exists
✓ test_invalid_ref_anchor_not_found
✓ test_valid_spec_ref
✓ test_cache_does_not_skip_format_or_type_checks    # 缓存不得绕过任何检查
✓ test_validate_task_artifact_refs_all_valid
✓ test_validate_task_with_invalid_refs
```

**`tests/test_maven_ambiguity.py`** (9 个测试):
```
✓ test_no_ambiguity_fully_qualified
✓ test_ambiguous_simple_class_name                  # SimpleTest 在 foo/ 和 bar/ 都存在
✓ test_no_ambiguity_single_match
✓ test_no_ambiguity_same_package
✓ test_not_maven_command
✓ test_maven_without_test_selector
✓ test_pl_scoping_excludes_other_module             # -pl module-a 不受 module-b 影响
✓ test_pl_scoping_still_flags_ambiguity_within_selected_module
✓ test_coordinate_selector_falls_back_to_unrestricted_search
```

**`tests/test_prevalidation_integration.py`** (4 个测试，真实驱动 `plan_writer.py` CLI):
```
✓ test_set_draft_task_detail_rejects_malformed_design_ref_immediately
✓ test_set_draft_task_detail_rejects_dangling_design_anchor
✓ test_set_draft_task_detail_rejects_cross_task_test_class_conflict
✓ test_set_draft_task_detail_accepts_valid_refs_and_persists
```

### 既有回归（评审修复）

初版实现在没有额外 fixture 的情况下让引用校验前移，导致 `tests/test_json_writers.py`
中 7 个原本测试其他失败路径（`implementation_points_exceeds_limit`、`nonGoals_missing`
等）的用例被新的 `missing_ref_file` 错误抢先命中并失败——这些用例复用了
`designRefs: ["design.md#D-001"]` 却没有创建 `design.md`。**修复**：在下列用例中补上
`_write_design(feature_dir)` / `_code_module()` fixture，让它们继续只暴露各自要测试的
那一个目标错误：
- `test_plan_writer_binds_each_task_to_one_of_multiple_repositories`
- `test_plan_writer_builds_and_finalizes_draft_batches_without_task_directory`
- `test_plan_writer_draft_derives_workspace_root_pages_and_validation_cwd`
- `test_plan_writer_draft_detects_group_changes_and_rebuilds_selectively`
- `test_plan_writer_draft_rejects_invalid_detail_before_writing`
- `test_plan_writer_draft_requires_non_goals_for_every_task`
- `test_plan_writer_external_dependency_has_no_local_validation_contract`
- `test_plan_writer_rejects_create_in_code_for_verified_existing_task`
- `test_plan_writer_rebuild_resets_only_tasks_bound_to_changed_repository`
- `test_plan_writer_splits_same_lane_repositories_and_routes_batch_commands`

修复后运行 `python3 -m unittest discover -s tests`：**779 个测试全部通过**（1 个 skip，
与本次改动无关）。

---

## 校验顺序（避免相互遮蔽）

`_cmd_set_draft_task_detail` 内部执行顺序：

1. `_normalize_draft_task_detail` — 结构/字段级校验
2. `_annotate_validation_test_plan` — 计算 Maven create/reuse 目标
3. `_draft_task_validation_errors` — 既有任务级校验（granularity、workspace 等）
4. `validate_task_artifact_refs`（新增）— designRefs/specRefs 格式与存在性
5. 跨任务测试类冲突检测（新增）

前一步失败即返回，不继续执行后续步骤，因此负向测试仍然只报一个目标错误。

---

## 校验流程对比

### 修改前（延迟发现）

```
用户操作                     系统反馈
─────────────────────────────────────────────────
fill T001 详情              → ✓ 接受（实际有错）
  (designRefs: "design.md - API-001")

fill T002 详情              → ✓ 接受
  (validation: mvn test -Dtest=SimpleTest)

fill T003 详情              → ✓ 接受（实际冲突）
  (validation: mvn test -Dtest=SimpleTest)

preflight-task-draft        → ❌ 发现问题：
                              - T001 designRef 格式错误
                              - T002/T003 测试类重名

用户体验：浪费时间填写了 T003，发现要回头改 T001
```

### 修改后（立即拦截）

```
用户操作                     系统反馈
─────────────────────────────────────────────────
fill T001 详情              → ❌ 立即拒绝
  (designRefs: "design.md - API-001")
  错误: invalid_artifact_ref_format

修正 T001 后重新提交        → ✓ 接受

fill T002 详情              → ✓ 接受
  (validation: mvn test -Dtest=SimpleTest)

fill T003 详情              → ❌ 立即拒绝
  (validation: mvn test -Dtest=SimpleTest)
  错误: duplicate_created_test_target

修正 T003 使用不同测试类    → ✓ 接受

preflight-task-draft        → ✓ 通过（纵深防御复核）

用户体验：每个问题立即发现，无需等到最后
```

---

## 纵深防御保留

**`preflight-task-draft` 继续执行所有检查**，原因：
1. 防止直接修改 JSON 文件绕过交互式校验
2. 防止校验逻辑本身的 bug
3. 作为最后一道关卡给出完整错误汇总

---

## 向后兼容

- ✅ 不引入新的持久化缓存字段，Draft/Lock 磁盘结构不变
- ✅ 现有 CLI 参数与返回结构不变
- ⚠️ 行为变化：`set-draft-task-detail` 现在会做更多前置检查，部分之前能通过、只在
  `preflight-task-draft` 才报错的输入现在会更早被拒绝——这是本次改动的目的，不是回归
- ✅ 全部既有测试（779 个）在补齐 fixture 后保持通过

---

## 未实施项（按用户明确要求）

**禁止手工编辑正式 PLAN.md**：需要 digest/锁定机制，本次不实施。

---

## 文件变更清单

### 新增文件
- `hooks/artifact_ref_validator.py` (241 行) — 引用校验核心逻辑，无持久化缓存
- `tests/test_artifact_ref_validator.py` (11 个测试，`unittest.TestCase`)
- `tests/test_maven_ambiguity.py` (9 个测试，`unittest.TestCase`)
- `tests/test_prevalidation_integration.py` (4 个测试，真实驱动 CLI)
- `PREVALIDATION_IMPROVEMENTS.md` (技术文档)

### 修改文件
- `hooks/plan_writer.py`:
  - 导入 `validate_task_artifact_refs`
  - `_cmd_set_draft_task_detail()`: 增加引用校验 + 跨任务测试类冲突检测
- `hooks/validation_policy.py`:
  - 新增 `check_maven_test_target_ambiguity()`，含 `-pl`/`--projects` 范围限定
  - 新增 `_maven_reactor_search_roots()` / `_maven_test_source_package()` 辅助函数
- `tests/test_json_writers.py`:
  - 10 个既有用例补齐 `_write_design(feature_dir)` / `_code_module()` fixture

---

## 验证结果

```bash
$ python3 -m unittest discover -s tests -p "test_*.py"
...
Ran 779 tests in 339.088s

OK (skipped=1)
```

```bash
$ python3 -m unittest tests.test_artifact_ref_validator -v
Ran 11 tests ... OK

$ python3 -m unittest tests.test_maven_ambiguity -v
Ran 9 tests ... OK

$ python3 -m unittest tests.test_prevalidation_integration -v
Ran 4 tests ... OK
```

---

## 总结

**实施完成度**: 已完成 P0-1、P0-2、P1；P2（缓存）主动放弃，理由见上文。禁止手工编辑
按用户要求本次不实施。

**评审发现的三个阻断问题均已修复**：
1. 不安全的持久化缓存 → 移除，改为每次都重新校验
2. 新测试不在 unittest 体系内、"集成测试"未真正驱动 CLI → 全部改写为
   `unittest.TestCase`，集成测试改为真实调用 `plan_writer.py`
3. 改动破坏 7 个既有回归测试 → 补齐 fixture，全部 779 个既有测试恢复通过

**核心改进**:
1. designRefs 格式错误从"执行时报错"前移到"填写时立即拦截"
2. 测试类冲突从"所有任务填完后发现"前移到"填写冲突任务时立即拦截"
3. Maven 歧义检测在 Draft 阶段生效，且正确处理 `-pl`/聚合模块范围
