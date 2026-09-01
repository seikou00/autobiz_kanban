# Draft/Finalize 流程重构 - 实施总结

> 本文下半部分保留首轮实施快照；其中的“待修复”项均已完成，不能作为当前状态使用。

## 当前状态（2026-08-31）

- 三个工程命令已经纳入 Draft 命令分发，Draft 尚未物化时不会再错误读取正式 `plan.json`。
- `preflight-task-draft` / `finalize-task-draft` 同时校验每个 code workspace 的 lane 编译命令和 required B-INT；缺失时不落正式产物。
- 工程命令默认复用 `prepare-task-draft` 锁定的 workspace；同 lane + workspace 的编译命令、required B-INT 为确定性替换，质量门默认追加，`--replace` 才覆盖该 workspace 的质量门集合。
- 已覆盖：Draft 配置、缺失校验、多仓库路由、finalize 后拒绝、diagnose → reopen → 重配 → `--force` 重新物化，以及执行开始后的冻结。
- 回归通过：`python -m unittest tests.test_draft_engineering_commands tests.test_json_writers tests.test_batched_plan tests.test_prevalidation_integration`（126 tests）。完整 `unittest discover` 执行了 985 tests；唯一错误为环境未安装 `pytest`，导致 `tests/test_code_stage_iterative_editing.py` 无法导入，和本次改动无关。

## 首轮实施记录（历史）

### 1. 核心功能实现 ✅

#### 三个工程命令函数已迁移到 Draft 阶段
- `_cmd_add_compile_command()` - line 4350
- `_cmd_add_quality_gate_command()` - line 4456  
- `_cmd_add_project_validation_command()` - line 4558

**变更内容**：
- 添加 finalized 守卫：如果 `plan.json` 已存在，拒绝操作并返回 `plan_already_finalized` 错误
- 从读取正式 plan 改为读取 Draft：`_load(workspace, feature)` → `_load_draft_bundle(workspace, feature)`
- 从写入正式 plan 改为写入 Draft：`_write(workspace, feature, data)` → `_write_draft_bundle(workspace, feature, data, lock)`

#### 新增工程命令完整性校验 ✅
- 新函数 `_validate_draft_engineering_commands(data)` - line 3223
- 检查逻辑：
  - 收集 backend code 模式任务，检查是否有 `compileProfiles.backend.commands`
  - 收集 frontend code 模式任务，检查是否有 `compileProfiles.frontend.commands`
  - external_dependency 和 verified_existing 任务不需要编译命令
- 错误信息包含：
  - `reason`: `missing_backend_compile_command` 或 `missing_frontend_compile_command`
  - `detail`: 中文说明，包含任务数量
  - `repairSuggestion`: 完整的命令示例
- 集成到 `_draft_preflight()` - line 3307：finalize 前自动调用

### 2. 文档更新 ✅

**skills/autodev/autodev-plan/SKILL.md**:
- 删除了 line 468+ 的"finalize 后追加工程命令"流程
- 新增"配置工程命令（Draft 阶段，finalize 之前）"章节
- 明确说明：工程命令必须在 Draft 阶段配置，finalize 会校验完整性
- 更新流程顺序：任务详情 → 工程命令 → finalize

### 3. 测试覆盖 ✅

**tests/test_draft_engineering_commands.py** (新文件):
- 8 个单元测试全部通过 ✅
- 测试覆盖：
  - backend 任务需要 backend 编译命令
  - frontend 任务需要 frontend 编译命令
  - external_dependency 任务不需要编译命令
  - verified_existing 任务不需要编译命令
  - 完整配置通过校验
  - 混合 backend/frontend 任务检测
  - 空任务列表通过校验
  - 空命令列表视为缺失

**tests/test_json_writers.py** (部分修复):
- 已修复 2 个使用 Draft 流程的测试 ✅
  - `test_plan_writer_projects_execution_stage_and_planning_touches`
  - `test_plan_writer_splits_same_lane_repositories_and_routes_batch_commands`
- 修改：在 finalize 前添加编译命令配置
- 新增：验证 finalize 后不可添加编译命令

### 4. 代码提交 ✅

- Commit: `c91e396` - "refactor: 工程命令迁移到 Draft 阶段配置"
- 已推送到 `origin/dev_autpbiz_fullstack_worktree`
- 核心功能变更：
  - `hooks/plan_writer.py`: +106 -12
  - `skills/autodev/autodev-plan/SKILL.md`: 文档重组

---

## 首轮发现的测试问题（已解决）

### test_batched_plan.py (2 个失败)

#### 1. `test_plan_writer_projects_lane_compile_commands` - line 394
**问题**：使用旧流程 `init` → `add-task` → `add-compile-command`
- 测试在正式 plan 创建后调用 `add-compile-command`，现在被拒绝
- 错误：`plan_already_finalized`

**原因**：这个测试使用的是已废弃的 `init` + `add-task` 流程，不是 Draft 流程

**建议**：
- 选项 A：完全重写为 Draft 流程（需要 30-45 分钟）
- 选项 B：标记为 `@unittest.skip("Uses deprecated init flow, conflicts with new Draft-stage engineering commands")`

#### 2. `test_plan_writer_finalizes_only_after_complete_scenario_coverage` - line 787
**问题**：使用 `finalize-task-set`（旧命令）后调用 `add-compile-command`
- Line 801-809: 在 finalize 后添加编译命令
- 错误：`plan_already_finalized`

**建议**：将 `add-compile-command` 移到 `finalize-task-set` 之前

### test_json_writers.py (3 个失败)

#### 1-2. 已修复但有遗留问题 ⚠️
- `test_plan_writer_projects_execution_stage_and_planning_touches` 
- `test_plan_writer_splits_same_lane_repositories_and_routes_batch_commands`
- 已修复：在 finalize 前添加编译命令
- 但可能还有其他断言失败需要调整

#### 3. `test_plan_writer_preflights_workspace_and_derives_batch_cwd` - line 1529
**问题**：使用 `materialize-task-set`（旧命令）后调用 `add-compile-command`
- Line 1564-1575: 使用 `materialize-task-set` 然后添加编译命令
- 错误：`plan_already_finalized`

**建议**：
- 选项 A：改用 Draft 流程
- 选项 B：在 `materialize-task-set` 之前添加编译命令（如果 materialize-task-set 也支持）

---

## 首轮实施进度（历史）

### 完成项 ✅
- [x] 核心功能：三个工程命令函数迁移到 Draft
- [x] 核心功能：finalize 前强制校验工程命令
- [x] 核心功能：finalize 后拒绝添加工程命令
- [x] 文档：删除 finalize 后追加流程
- [x] 文档：新增 Draft 阶段工程命令配置
- [x] 测试：新增工程命令校验单元测试（8/8 通过）
- [x] 测试：部分修复 test_json_writers.py (2/5 修复完成)
- [x] 代码提交并推送

### 当时未完成项（现已完成）
- [ ] 测试：完成 test_json_writers.py 所有修复 (3 个遗留)
- [ ] 测试：修复 test_batched_plan.py (2 个失败)
- [ ] 端到端：完整流程验证
- [ ] 回归：确保所有测试通过

### 测试状态
- ✅ test_draft_engineering_commands.py: **8/8 通过**
- ⚠️ test_json_writers.py: **65/68 通过** (3 个失败)
- ⚠️ test_batched_plan.py: **33/35 通过** (2 个失败)
- **总计：106/111 通过 (95.5%)**

---

## 破坏性变更说明

**BREAKING CHANGE**: add-compile-command、add-quality-gate-command、add-project-validation-command 现在只能在 Draft 阶段调用。

### 影响范围
1. **工作流调整**：
   - 所有手动/自动化流程需要调整：先配置工程命令，再 finalize
   - finalize 后需要先 `reopen-finalized-draft` 才能修改配置

2. **强制校验**：
   - backend code 任务必须有 backend 编译命令
   - frontend code 任务必须有 frontend 编译命令
   - 缺少必需工程命令时 finalize 会失败并给出修复建议

3. **旧流程不可用**：
   - `init` + `add-task` + `add-compile-command` 流程已废弃
   - `materialize-task-set` + `add-compile-command` 流程已废弃
   - 必须使用 Draft 流程

### 迁移指引

```bash
# ❌ 旧流程（已废弃）
finalize-task-draft
add-compile-command --lane backend ...
add-quality-gate-command --lane backend ...

# ✅ 新流程
prepare-task-draft --group-file ... --code-workspace ...
set-draft-task-detail --task-id T001 --body-stdin < detail.json
add-compile-command --lane backend ...      # 在 finalize 之前
add-quality-gate-command --lane backend ...
add-project-validation-command ...
finalize-task-draft                          # 一次性物化，不可再改
```

---

## 下一步建议

### 选项 A：完成所有测试修复（推荐）
**预计时间**：2-3 小时
- 修复 test_batched_plan.py 的 2 个测试
- 修复 test_json_writers.py 的 3 个测试
- 运行完整测试套件确保全部通过
- 提交测试修复

**优点**：
- 完整的测试覆盖
- 确保功能正确性
- 便于后续维护

### 选项 B：标记遗留测试为 skip（快速）
**预计时间**：10-15 分钟
- 标记使用旧流程的测试为 skip
- 添加注释说明原因
- 提交当前状态

**优点**：
- 快速完成重构
- 核心功能已验证

**缺点**：
- 测试覆盖率下降
- 部分边缘情况未验证

---

## 文件清单

### 已修改并提交
- `hooks/plan_writer.py` - 核心功能实现
- `skills/autodev/autodev-plan/SKILL.md` - 文档更新

### 已修改未提交
- `tests/test_json_writers.py` - 部分测试修复
- `tests/test_draft_engineering_commands.py` - 新增测试（全部通过）
- `REFACTOR_SUMMARY.md` - 本文档

### 需要关注的测试文件
- `tests/test_batched_plan.py` - 2 个测试失败
- `tests/test_json_writers.py` - 3 个测试失败

---

## 验证清单

核心功能验证：
- [x] Draft 阶段可以添加编译命令
- [x] Draft 阶段可以添加质量门命令
- [x] Draft 阶段可以添加项目验证命令
- [x] finalize 前会校验工程命令完整性
- [x] 缺少必需命令时 finalize 失败并提示
- [x] finalize 后尝试添加命令被拒绝
- [ ] 完整的 Draft → 工程命令 → finalize 流程端到端验证
- [ ] reopen 后可以修改工程命令

测试覆盖验证：
- [x] 单元测试：工程命令校验逻辑
- [x] 集成测试：Draft 流程 + 工程命令（部分）
- [ ] 集成测试：多仓库场景
- [ ] 集成测试：错误恢复场景
- [ ] 回归测试：现有功能不受影响

---

生成时间：2026-08-31  
最后更新：2026-08-31  
状态：**核心功能完成并提交，测试修复进行中（95.5% 通过）**  
下一步：选择测试修复策略并执行
