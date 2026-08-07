# 阶段手动回退操作指南

> **适用场景：** 当自动回退工具失效或不可用时，按照本指南手动清理产物和修改状态文件。

---

## 一、回退操作三步骤

### 步骤 1：删除当前阶段及后续阶段的产物文件
### 步骤 2：修改 `.autobizdevops/state.json` 中的 checkpoint
### 步骤 3：（如需要）回退业务代码的 Git 提交

---

## 二、各阶段手动回退清单

### 2.1 回退到需求澄清完成（discuss_done）

**场景：** 从 PRD 阶段回退到需求澄清

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 PRD 产物
rm -f $FEATURE_DIR/PRD.md

# 删除后续所有阶段产物（如果有）
rm -f $FEATURE_DIR/proposal.md
rm -rf $FEATURE_DIR/specs/
rm -f $FEATURE_DIR/design.md
rm -f $FEATURE_DIR/PLAN.md
rm -f $FEATURE_DIR/plan.json
rm -rf $FEATURE_DIR/plans/
rm -rf $FEATURE_DIR/evidence/
rm -rf $FEATURE_DIR/.task-runs/
rm -rf $FEATURE_DIR/.batch-runs/
# ... 以此类推
```

**步骤 2：修改 state.json**

```bash
# 编辑 .autobizdevops/state.json
vim .autobizdevops/state.json
```

找到对应 Feature 的记录，修改：

```json
{
  "feature": "<feature>",
  "checkpoint": "discuss_done",  // 改为 discuss_done
  "stage": "Biz / 需求澄清",
  "updated_at": "2026-08-07 14:30:00"  // 更新时间戳
}
```

**步骤 3：业务代码处理**

无需处理（discuss 阶段没有业务代码）

---

### 2.2 回退到 PRD 完成（prd_done）

**场景：** 从 Specs 或更后阶段回退到 PRD

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 Specs 产物
rm -f $FEATURE_DIR/proposal.md
rm -rf $FEATURE_DIR/specs/

# 删除后续所有阶段产物
rm -f $FEATURE_DIR/design.md
rm -f $FEATURE_DIR/PLAN.md
rm -f $FEATURE_DIR/plan.json
rm -rf $FEATURE_DIR/plans/
rm -f $FEATURE_DIR/DETAIL_DESIGN.md
rm -f $FEATURE_DIR/SMOKE_TEST_PLAN.json
rm -rf $FEATURE_DIR/evidence/
rm -rf $FEATURE_DIR/.task-runs/
rm -rf $FEATURE_DIR/.batch-runs/
rm -rf $FEATURE_DIR/.batch-task-validation-runs/
rm -rf $FEATURE_DIR/cache/
rm -f $FEATURE_DIR/BATCH_HANDOFF.json
rm -f $FEATURE_DIR/REVIEW_FINDINGS.json
rm -f $FEATURE_DIR/REQUIREMENTS_EVAL.md
rm -f $FEATURE_DIR/UNIT_TEST_RESULT.json
rm -f $FEATURE_DIR/![img.png](img.png)UNIT_TEST_REPORT.md
rm -f $FEATURE_DIR/test-output.log
rm -f $FEATURE_DIR/E2E_TEST_CASES.yaml
rm -f $FEATURE_DIR/E2E_RESULT.json
rm -f $FEATURE_DIR/E2E_REPORT.md
rm -f $FEATURE_DIR/e2e-run.log
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "prd_done",
  "stage": "Biz / PRD",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

无需处理（prd 阶段没有业务代码）

---

### 2.3 回退到行为规格完成（specs_done）

**场景：** 从 Plan 或更后阶段回退到 Specs

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 Plan 产物
rm -f $FEATURE_DIR/design.md
rm -f $FEATURE_DIR/PLAN.md
rm -f $FEATURE_DIR/plan.json
rm -rf $FEATURE_DIR/plans/
rm -f $FEATURE_DIR/DETAIL_DESIGN.md
rm -f $FEATURE_DIR/SMOKE_TEST_PLAN.json

# 删除 Code 产物
rm -rf $FEATURE_DIR/evidence/
rm -rf $FEATURE_DIR/.task-runs/
rm -rf $FEATURE_DIR/.batch-runs/
rm -rf $FEATURE_DIR/.batch-task-validation-runs/
rm -rf $FEATURE_DIR/cache/
rm -f $FEATURE_DIR/BATCH_HANDOFF.json

# 删除 Review 产物
rm -f $FEATURE_DIR/REVIEW_FINDINGS.json
rm -f $FEATURE_DIR/REQUIREMENTS_EVAL.md

# 删除 UT 产物
rm -f $FEATURE_DIR/UNIT_TEST_RESULT.json
rm -f $FEATURE_DIR/UNIT_TEST_REPORT.md
rm -f $FEATURE_DIR/test-output.log

# 删除 E2E 产物
rm -f $FEATURE_DIR/E2E_TEST_CASES.yaml
rm -f $FEATURE_DIR/E2E_RESULT.json
rm -f $FEATURE_DIR/E2E_REPORT.md
rm -f $FEATURE_DIR/e2e-run.log

# 删除 Verify 产物
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "specs_done",
  "stage": "Specs 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

无需处理（specs 阶段没有业务代码）

---

### 2.4 回退到技术设计与计划完成（plan_done）

**场景：** 从 Code 或更后阶段回退到 Plan

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 Code 产物
rm -rf $FEATURE_DIR/evidence/
rm -rf $FEATURE_DIR/.task-runs/
rm -rf $FEATURE_DIR/.batch-runs/
rm -rf $FEATURE_DIR/.batch-task-validation-runs/
rm -rf $FEATURE_DIR/cache/
rm -f $FEATURE_DIR/BATCH_HANDOFF.json

# 删除 Review 产物
rm -f $FEATURE_DIR/REVIEW_FINDINGS.json
rm -f $FEATURE_DIR/REQUIREMENTS_EVAL.md

# 删除 UT 产物
rm -f $FEATURE_DIR/UNIT_TEST_RESULT.json
rm -f $FEATURE_DIR/UNIT_TEST_REPORT.md
rm -f $FEATURE_DIR/test-output.log

# 删除 E2E 产物
rm -f $FEATURE_DIR/E2E_TEST_CASES.yaml
rm -f $FEATURE_DIR/E2E_RESULT.json
rm -f $FEATURE_DIR/E2E_REPORT.md
rm -f $FEATURE_DIR/e2e-run.log

# 删除 Verify 产物
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "plan_done",
  "stage": "Plan 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

```bash
# 查看 Code 阶段开始后的所有提交
git log --oneline --since="<code开始时间>"

# 回退到 plan_done 时的提交
git reset --hard <plan_done时的commit>

# 或者使用交互式回退（更安全）
git log --oneline -20  # 查看最近20次提交
git reset --hard <commit-hash>
```

---

### 2.5 回退到代码实现完成（code_done）

**场景：** 从 Review 或更后阶段回退到 Code

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 Review 产物
rm -f $FEATURE_DIR/REVIEW_FINDINGS.json
rm -f $FEATURE_DIR/REQUIREMENTS_EVAL.md

# 删除 UT 产物
rm -f $FEATURE_DIR/UNIT_TEST_RESULT.json
rm -f $FEATURE_DIR/UNIT_TEST_REPORT.md
rm -f $FEATURE_DIR/test-output.log

# 删除 E2E 产物
rm -f $FEATURE_DIR/E2E_TEST_CASES.yaml
rm -f $FEATURE_DIR/E2E_RESULT.json
rm -f $FEATURE_DIR/E2E_REPORT.md
rm -f $FEATURE_DIR/e2e-run.log

# 删除 Verify 产物
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "code_done",
  "stage": "Code 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

无需处理（业务代码保留，只删除 Review 及后续阶段产物）

---

### 2.6 回退到需求实现评审完成（requirements_eval_done）

**场景：** 从 UT 或更后阶段回退到 Review

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 UT 产物
rm -f $FEATURE_DIR/UNIT_TEST_RESULT.json
rm -f $FEATURE_DIR/UNIT_TEST_REPORT.md
rm -f $FEATURE_DIR/test-output.log

# 删除 E2E 产物
rm -f $FEATURE_DIR/E2E_TEST_CASES.yaml
rm -f $FEATURE_DIR/E2E_RESULT.json
rm -f $FEATURE_DIR/E2E_REPORT.md
rm -f $FEATURE_DIR/e2e-run.log

# 删除 Verify 产物
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "requirements_eval_done",
  "stage": "Requirements Review 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

```bash
# 如果 UT 阶段添加了单测代码，需要删除
find . -name "*.test.ts" -o -name "*.test.js" -o -name "*.test.py" | xargs rm -f
# 或手动删除测试目录
rm -rf tests/unit/<feature>/
```

---

### 2.7 回退到单元测试完成（unit_test_done）

**场景：** 从 E2E 或更后阶段回退到 UT

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 E2E 产物
rm -f $FEATURE_DIR/E2E_TEST_CASES.yaml
rm -f $FEATURE_DIR/E2E_RESULT.json
rm -f $FEATURE_DIR/E2E_REPORT.md
rm -f $FEATURE_DIR/e2e-run.log

# 删除 Verify 产物
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "unit_test_done",
  "stage": "Unit Test 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

```bash
# 如果 E2E 阶段添加了 E2E 测试代码，需要删除
rm -rf tests/e2e/<feature>/
rm -rf e2e/**/*.spec.ts
```

---

### 2.8 回退到 E2E 测试完成（e2e_done）

**场景：** 从 Verify 或更后阶段回退到 E2E

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 Verify 产物
rm -f $FEATURE_DIR/VERIFY_DECISION.json
rm -f $FEATURE_DIR/VERIFY_REPORT.md
rm -f $FEATURE_DIR/FIX_REQUEST.json
rm -f $FEATURE_DIR/FEATURE_API_DETAIL.md

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "e2e_done",
  "stage": "E2E 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

无需处理

---

### 2.9 回退到验收汇总完成（verify_done）

**场景：** 从 CI/CD 或归档回退到 Verify

**步骤 1：删除产物文件**

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 删除 CI/CD 产物
rm -f $FEATURE_DIR/CICD_CHECKLIST.md
rm -f $FEATURE_DIR/PR_BODY.md
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "verify_done",
  "stage": "Verify 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

无需处理

---

### 2.10 回退到 CI/CD 完成（cicd_done）

**场景：** 从归档回退到 CI/CD

**步骤 1：移动归档目录**

```bash
# 将归档目录移回活跃目录
WORKSPACE=".autobizdevops"
FEATURE="<feature>"

# 查找归档目录
ls $WORKSPACE/archive/ | grep "^${FEATURE}-iter"

# 移动回活跃目录
mv $WORKSPACE/archive/${FEATURE}-iter<N> $WORKSPACE/features/$FEATURE
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "cicd_done",
  "stage": "CI/CD 完成",
  "updated_at": "2026-08-07 14:30:00"
}
```

**步骤 3：业务代码处理**

无需处理

---

## 三、特殊情况处理

### 3.1 从 needs_fix 恢复

**场景：** Feature 当前在 needs_fix 状态，需要回到原失败的 checkpoint

**步骤 1：查看原失败 checkpoint**

```bash
cat .autobizdevops/state.json | jq '.records[] | select(.feature=="<feature>") | .needsFixFromCheckpoint'
```

**步骤 2：修改 state.json**

```json
{
  "feature": "<feature>",
  "checkpoint": "<原失败的checkpoint>",  // 例如：e2e_done
  "stage": "<对应的stage>",
  "updated_at": "2026-08-07 14:30:00"
  // 删除 needsFixFromCheckpoint 字段
}
```

---

### 3.2 清理 evidence 流（谨慎操作）

**注意：** evidence 流是 append-only 的，通常不建议手动删除。如果确实需要：

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 备份现有 evidence
cp -r $FEATURE_DIR/evidence $FEATURE_DIR/evidence.backup

# 删除 evidence 流
rm -rf $FEATURE_DIR/evidence/

# 如果需要恢复
mv $FEATURE_DIR/evidence.backup $FEATURE_DIR/evidence
```

---

### 3.3 重置 plan.json 中的 Batch 状态

**场景：** 需要重新执行某个 Batch

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 编辑 plan.json
vim $FEATURE_DIR/plan.json
```

找到对应的 Batch，修改其 `status`：

```json
{
  "batches": [
    {
      "id": "B001",
      "status": "done"  // 保持不变
    },
    {
      "id": "B002",
      "status": "todo"  // 从 done 改为 todo，重新执行
    }
  ],
  "activeBatchId": "B002"  // 修改为要重新执行的 Batch
}
```

同时删除该 Batch 的运行快照：

```bash
rm -rf $FEATURE_DIR/.batch-runs/B002/
rm -rf $FEATURE_DIR/.task-runs/T003/  # 删除该 Batch 中所有 Task 的运行快照
rm -rf $FEATURE_DIR/.task-runs/T004/
```

---

## 四、验证回退是否成功

### 4.1 检查产物文件

```bash
FEATURE_DIR=".autobizdevops/features/<feature>"

# 列出所有产物文件
ls -la $FEATURE_DIR/
ls -la $FEATURE_DIR/specs/
ls -la $FEATURE_DIR/plans/
ls -la $FEATURE_DIR/evidence/
```

**确认：**
- 目标阶段的产物存在
- 后续阶段的产物已删除

### 4.2 检查 state.json

```bash
# 查看当前 checkpoint
cat .autobizdevops/state.json | jq '.records[] | select(.feature=="<feature>")'
```

**确认：**
- `checkpoint` 字段为目标值
- `stage` 字段匹配
- `updated_at` 已更新

### 4.3 检查 Git 状态

```bash
# 查看当前分支和提交
git status
git log --oneline -10
```

**确认：**
- 业务代码在正确的提交点
- 没有未提交的改动（或改动符合预期）

### 4.4 重新进入对应阶段

使用对应的 skill 命令重新进入，确认可以正常推进：

```bash
# 例如，回退到 plan_done 后
# 使用 /autodev-code 进入 Code 阶段
# 系统应该能正常加载 plan.json 并开始执行
```

---

## 五、常见错误与修复

### 5.1 state.json 格式错误

**症状：** 修改后系统报 JSON 解析错误

**修复：**
```bash
# 使用 jq 验证 JSON 格式
cat .autobizdevops/state.json | jq .

# 如果报错，使用备份恢复
cp .autobizdevops/state.json.backup .autobizdevops/state.json
```

### 5.2 STATE.md 与 state.json 不一致

**症状：** STATE.md 显示的 checkpoint 与 state.json 不同

**修复：**
```bash
# STATE.md 是自动生成的，删除后会重新生成
rm .autobizdevops/STATE.md

# 下次执行任何 checkpoint 更新时会自动重新生成
```

### 5.3 产物文件删除不干净

**症状：** 重新执行阶段时提示产物已存在

**修复：**
```bash
# 查找残留的产物文件
find .autobizdevops/features/<feature>/ -type f

# 根据上面的清单，手动删除遗漏的文件
```

---

## 六、回退前备份建议

在执行手动回退前，建议先备份：

```bash
FEATURE="<feature>"
BACKUP_DIR="./rollback-backup-$(date +%Y%m%d_%H%M%S)"

# 创建备份目录
mkdir -p $BACKUP_DIR

# 备份 Feature 产物目录
cp -r .autobizdevops/features/$FEATURE $BACKUP_DIR/

# 备份 state.json
cp .autobizdevops/state.json $BACKUP_DIR/

# 备份 Git 当前状态
git stash save "Backup before rollback $(date +%Y%m%d_%H%M%S)"

echo "备份已保存到: $BACKUP_DIR"
```

如果回退失败，可以从备份恢复：

```bash
# 恢复产物目录
cp -r $BACKUP_DIR/<feature> .autobizdevops/features/

# 恢复 state.json
cp $BACKUP_DIR/state.json .autobizdevops/

# 恢复 Git 状态
git stash pop
```

---

**版本：** v1.0  
**更新日期：** 2026-08-07  
**维护者：** AutoBizDevOps 开发团队