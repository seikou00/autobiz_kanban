# Code 测试资产交接与 Compile-only 批次验证方案

> 状态：设计基线
>
> 日期：2026-08-10
>
> 适用范围：后端 Maven Feature；旧 Feature 保留原有 `deferred_batch` 行为，新 Feature 使用本文定义的 `defer_to_test_stages` 模式。

## 1. 背景与问题

当前 Code 阶段的单个 TASK 实现期间不直接运行验证命令，但最后一个 TASK 收口后，Code 仍会启动 deferred task validation，执行 TASK 的 `validationCommands`，之后才执行 Batch 验证。现行 Code 协议明确要求 `create_in_code` 测试目标在 Code 中创建，并将新测试归为 `transientValidationFiles`；这些规则见 [autodev-code/SKILL.md](../skills/autodev/autodev-code/SKILL.md)。

目标流程调整为：

```text
Code：生产代码 + 测试资产生成，不运行测试
  -> Batch：只运行一次 required mvn compile
  -> Review：只读审查并校验测试资产/生产代码同步
  -> UTest：运行单元测试和单元级集成测试
  -> E2E：生成/运行 E2E 资产和跨服务集成测试
  -> Verify：确认全部测试资产已闭环
```

“测试单”需要拆成两个概念：

- Java/JUnit/集成测试代码：可以在 Code 生成，并通过 `testHandoff` 交接。
- `E2E_TEST_CASES.yaml`：仍由 E2E 阶段生成；Code 只登记 E2E 资产意图，不伪造 YAML 文件。

## 2. 设计目标

- Code 阶段不运行 TASK 测试、Surefire、Failsafe、`test`、`verify` 或 `test-compile`。
- 每个 Batch 的唯一 required 工程校验是 Maven `compile`。
- Code 生成的测试文件必须可追踪、可校验、可判断是否过时。
- Review、UTest、E2E 均不得消费已删除、被重命名或与生产代码脱节的测试资产。
- 每个 TASK 对测试文件和共享测试工具有明确所有权，禁止互相覆盖。
- 测试失败能区分测试资产问题、生产代码问题、契约问题和环境问题，并路由到正确阶段。
- Evidence 能区分“测试资产已生成”和“测试实际执行通过”。
- 旧 `deferred_batch` Feature 不受新策略影响。

## 3. 非目标与工程决策

### 3.1 不用固定字节数作为完整性标准

`sizeBytes > 100` 只能拦截极端空文件，不能证明测试有效；不同编码、注解、参数化测试和测试框架都会使该阈值失真。最终规则为：文件存在、是普通文件、非空，并由框架适配器确认计划 selector 对应的测试类/方法结构存在。

### 3.2 不强制“一生产类一个测试类”

一个生产类可能对应多个独立行为和多个 TASK。强制共用 `OrderServiceTest.java` 会导致多个 TASK 共同写入同一路径，破坏当前 TASK 的独立快照和 Evidence 归属。默认采用行为命名的独占测试资产，例如：

```text
OrderCreateBehaviorTest.java
OrderCancelBehaviorTest.java
```

同一个 TASK 内可以向自己的测试文件追加多个测试方法；不同 TASK 不得共同创建或追加同一路径。

### 3.3 不把简单方法调用抽取当作强门禁

Java 的重载、继承、静态导入、方法引用、mock、生成代码和多模块类型解析无法通过简单 `extract_method_calls()` 可靠判断。方法引用扫描可以作为 Review 的提示信息，但不能作为最终阻断条件。强门禁使用源文件指纹和结构化 API 指纹。

## 4. Plan 契约

### 4.1 Feature 策略

新计划根对象增加或更新：

```json
{
  "taskValidationPolicy": {
    "mode": "defer_to_test_stages",
    "orchestration": "single_batch_subagent",
    "codeGate": "batch_compile_only",
    "testAssetIntegrity": "fingerprint_v1",
    "maxTestStageRepairAttempts": 3
  }
}
```

`mode` 的语义：

| mode | 语义 |
| --- | --- |
| `deferred_batch` | 旧模式：Code Batch 末尾运行 TASK validation，再运行 Batch validation |
| `defer_to_test_stages` | 新模式：Code 只生成资产和运行 Batch compile，测试交给 UTest/E2E |

### 4.2 测试资产计划

保留现有 `validationTestPlan`，增加生成和执行元数据：

```json
{
  "validationTestPlan": [
    {
      "commandId": "VAL-T001-01",
      "kind": "behavior_test",
      "assetType": "java_test",
      "generationStage": "dev.code",
      "executionStage": "dev.utest",
      "sourceRefs": [
        "src/main/java/com/example/order/OrderService.java"
      ],
      "targets": [
        {
          "selector": "com.example.order.OrderCreateBehaviorTest#shouldCreateOrder",
          "mode": "create_in_code"
        }
      ],
      "covers": ["AC-T001-01"],
      "required": true
    }
  ]
}
```

字段约束：

- `assetType=java_test`：Code 生成，UTest 或 E2E 执行。
- `assetType=java_integration_test`：Code 生成，按 `executionStage` 在 UTest 或 E2E 执行。
- `assetType=e2e_case_yaml`：`generationStage=dev.e2e`，Code 不要求文件存在。
- `assetType=playwright_spec`、`selenium_test`：默认由 E2E 生成并执行。
- `executionStage` 只能是 `dev.utest` 或 `dev.e2e`；Code 不拥有测试执行命令。
- 既有 `reuse_existing/create_in_code` 冲突校验继续保留，并扩展到文件路径级冲突。

### 4.3 Batch compile 契约

后端 Maven Batch 必须配置 `mode=commands`，至少一条 required compile 命令，命令必须使用 Maven compile 生命周期：

```json
{
  "batchValidation": {
    "mode": "commands",
    "commands": [
      {
        "kind": "compile",
        "argv": ["mvn", "compile"],
        "cwd": ".",
        "required": true
      }
    ]
  }
}
```

多模块项目可使用 `mvn -pl <module> -am compile`。Plan 校验必须拒绝 `test`、`verify`、`package`、`install`、`test-compile`、`-Dtest`、`-Dit.test` 和其它测试选择器出现在该 Batch 命令中。

## 5. testHandoff 运行态契约

`testHandoff` 写入对应 `plans/Bxxx/plan.json`，根 `plan.json` 只保存汇总索引。示例：

```json
{
  "testHandoff": {
    "version": 1,
    "status": "pending",
    "items": [
      {
        "handoffId": "TH-T001-01",
        "taskId": "T001",
        "commandId": "VAL-T001-01",
        "assetType": "java_test",
        "generationStage": "dev.code",
        "executionStage": "dev.utest",
        "assetStatus": "integrity_verified",
        "executionStatus": "not_run",
        "files": [
          {
            "repo": "order-service",
            "path": "src/test/java/com/example/order/OrderCreateBehaviorTest.java",
            "sizeBytes": 1260,
            "sha256": "...",
            "framework": "junit5",
            "selectors": ["OrderCreateBehaviorTest#shouldCreateOrder"]
          }
        ],
        "sourceFingerprints": [
          {
            "path": "src/main/java/com/example/order/OrderService.java",
            "sha256": "...",
            "apiSha256": "..."
          }
        ],
        "latestAssetEvidenceId": "ev_0012"
      }
    ],
    "sharedAssets": []
  }
}
```

状态分离，避免把“文件状态”和“执行状态”混成一个枚举：

| 字段 | 值 |
| --- | --- |
| `assetStatus` | `generated`、`reused`、`integrity_verified`、`updated`、`missing`、`obsolete` |
| `executionStatus` | `not_run`、`running`、`passed`、`failed`、`blocked` |

Code Done 允许 `assetStatus=integrity_verified` 且 `executionStatus=not_run`；Verify 不允许 required item 停留在 `not_run`、`failed` 或 `blocked`。

## 6. 测试文件完整性与生产代码同步

### 6.1 检查时机

同一校验器必须在以下位置运行：

1. `finish-implementation`：生成/复用测试文件后建立首个指纹。
2. `code_done`：确认当前工作区与交接指纹一致。
3. Review 准入和完成：阻止 Review 放行过时测试资产。
4. UTest/E2E 准入：执行前再次确认文件和生产指纹。

### 6.2 文件检查

- `os.path.exists()` 等价的路径检查必须通过。
- 必须是普通文件，不能是目录、符号链接到工作区外的文件或空文件。
- 按 `framework` 适配器解析类、方法和 selector；JUnit 4/5、TestNG 等使用各自的测试入口规则。
- `sha256`、`sizeBytes`、selector 列表和相对路径必须与 handoff 一致。
- 文件删除、重命名、替换、selector 消失都返回 `test_asset_missing` 或 `test_asset_corrupt`。

### 6.3 生产代码同步

每个测试 item 必须记录 `sourceRefs` 的文件指纹和 API 指纹：

- 被测生产文件任意内容变化：默认标记 item `obsolete`，要求回 Code 更新测试资产并重新 Batch compile。
- 仅公开 API 签名变化：必须标记 `obsolete`，不能依赖测试编译在后续阶段才发现。
- 测试文件在 Review 期间变化：视为未授权资产变更，回 Code 重新建立 handoff。
- Reviewer 只读，不直接修改源代码；当前 Reviewer 契约见 [autodev-reviewer/SKILL.md](../skills/autodev/autodev-reviewer/SKILL.md)。

方法调用扫描可以输出 advisory finding，但不能替代上述指纹门禁。

## 7. 多 TASK 文件所有权

### 7.1 独占测试资产

- 一个 `create_in_code` 测试路径只能归属一个 TASK。
- 推荐按业务行为命名测试类，不按生产类通用命名，例如 `OrderCreateBehaviorTest`，避免天然重名。
- 同一 TASK 可在同一文件中追加方法；不同 TASK 不得追加、覆盖或删除该文件。
- Plan preflight、`set-draft-task-detail` 和 Code runner 都必须检查 `(repo, cwd, path)` 冲突。

### 7.2 共享测试工具

共享 fixture、builder、mock data 必须登记：

```json
{
  "assetId": "TSA-001",
  "path": "src/test/java/com/example/order/MockOrderData.java",
  "kind": "shared_fixture",
  "ownerTaskId": "T001",
  "consumerTaskIds": ["T002", "T003"]
}
```

`ownerTaskId` 由 Plan 按拓扑顺序确定，不由运行时“第一个碰到的 TASK”决定。消费者只能复用；需要修改共享工具时回到 owner TASK，不能复制三份或由消费者覆盖。

## 8. E2E 资产生成边界

| 资产 | Plan 记录 | Code 行为 | E2E 行为 |
| --- | --- | --- | --- |
| Java 单测 | `assetType=java_test` | 生成并交接 | 不重复生成，UTest 执行 |
| Java 集成测试 | `assetType=java_integration_test` | 生成并交接 | 按 `executionStage` 执行 |
| `E2E_TEST_CASES.yaml` | `assetType=e2e_case_yaml`、`generationStage=dev.e2e` | 只保留生成意图 | 生成、校验、执行 |
| Playwright/Selenium spec | `assetType=playwright_spec` 等 | 默认不生成 | 生成并执行 |

Code 阶段不生成 `E2E_TEST_CASES.yaml`，也不要求该文件存在。E2E 阶段必须根据 Plan 的 `executionStage=dev.e2e` 项补齐 YAML、脚本、fixture 和环境信息，并将实际生成文件加入 E2E 产物和 Evidence。

## 9. 失败路由

```text
test_asset_missing/corrupt/obsolete -> needs_fix -> Code -> Batch compile -> Review
test_code_error（只涉及测试）       -> UTest/E2E 修测试 -> 同阶段重试
prod_code_bug                       -> needs_fix -> Code -> Batch compile -> Review
contract_gap                        -> Specs/Plan 阻断
environment/auth/data               -> 同阶段限次重试 -> BLOCKED
```

结构化失败对象：

```json
{
  "failureType": "test_code_error",
  "route": {
    "targetCheckpoint": "unit_test_in_progress",
    "retryable": true,
    "repairOwner": "dev.utest"
  },
  "commandId": "VAL-T001-01",
  "handoffId": "TH-T001-01",
  "evidenceIds": ["ev_0042"]
}
```

分类规则：

| failureType | 判定 | 路由 |
| --- | --- | --- |
| `test_asset_missing/corrupt/obsolete` | 文件或指纹不一致 | Code |
| `test_code_error` | 测试导入、测试类、fixture、断言或命令错误 | 当前 UTest/E2E 重试 |
| `prod_code_bug` | 生产行为不满足已确认契约 | Code，随后 Review |
| `contract_gap` | specs/design/实现/测试契约冲突 | Specs/Plan |
| `environment` | 依赖、网络、权限、数据库或服务不可用 | 重试或 BLOCKED |
| `auth_blocked`、`data_blocked` | E2E 认证或数据条件不满足 | E2E 重试或 BLOCKED |

UTest/E2E 只能直接修改测试资产。任何生产代码修改都必须失效相关 Code compile、Review 和下游测试结果，回 Code 重新走闭环。

## 10. Evidence 分型

新增 `action=test_asset`，用于记录测试资产生命周期；未执行的测试不能写成 `validation`：

| action | 语义 |
| --- | --- |
| `implementation` | 生产代码实现事实；`changedFiles` 可包含测试文件，但必须有 `fileRoles` 分类 |
| `test_asset` | 测试资产 generated/reused/updated/integrity_verified/obsolete |
| `batch_validation` | Batch 的唯一 required `mvn compile` 结果 |
| `validation` | UTest/E2E 实际执行结果 |
| `review` | Review 只读审查结果 |

测试文件保留在 Git diff 中，但 Evidence 必须明确区分：

```json
{
  "changedFiles": [
    "src/main/java/.../OrderService.java",
    "src/test/java/.../OrderCreateBehaviorTest.java"
  ],
  "fileRoles": {
    "production": ["src/main/java/.../OrderService.java"],
    "testAsset": ["src/test/java/.../OrderCreateBehaviorTest.java"]
  }
}
```

## 11. Runner 与工作流改造

### 11.1 Code

- `code-session` 在新策略下不创建 `start-batch-task-validation`。
- 最后一个 TASK 完成后直接进入 Batch compile。
- `validate-batch-task` 对新策略返回 `task_validation_not_allowed_in_code_stage`。
- Batch compile 只接受 `kind=compile` 且 Maven goal 为 `compile` 的命令。
- Compile 通过后，TASK 标记为 Code 完成，`testHandoff` 保持 pending；不写测试 PASS Evidence。
- 删除 [board_core/board_config.json](../board_core/board_config.json) 中的重复 `code_compile` guard，避免 Batch compile 后再次执行一次 Maven compile。

### 11.2 Review

- Review 入口和完成前执行 `test_handoff_integrity`。
- 发现测试文件缺失、指纹变化、生产引用源变化时生成 FAIL finding。
- Reviewer 仍然只读；修复必须通过 Code 回流。

### 11.3 UTest/E2E/Verify

- UTest 初始化时从 `testHandoff` 投影 target，不重复创建已有资产。
- E2E 初始化时读取 `generationStage=dev.e2e` 的资产意图，生成 `E2E_TEST_CASES.yaml` 和脚本。
- UTest/E2E 成功后更新对应 `executionStatus` 和 validation Evidence。
- Verify 阻止 required handoff item 停留在 `not_run`、`failed`、`blocked`，并要求 `UNIT_TEST_RESULT.json`、`E2E_RESULT.json` 和 Evidence 一致。

### 11.4 工作流转移

新增或允许以下回流：

```text
requirements_eval_in_progress -> needs_fix
unit_test_in_progress          -> needs_fix
needs_fix                      -> code_in_progress / specs_in_progress / plan_in_progress
```

回流必须由结构化失败路由触发，禁止手工修改 checkpoint 绕过失效 Evidence。

## 12. 影响文件

- `hooks/plan_writer.py`：策略、`validationTestPlan`、`testHandoff`、资产所有权和 Batch compile 契约。
- `hooks/plan_json.py`：新策略解析、字段校验和兼容旧计划。
- `hooks/task_runner.py`：跳过 TASK validation、执行 compile-only、资产指纹和回流事务。
- `hooks/evidence_store.py`：`test_asset` action、`fileRoles`、资产指纹字段。
- `hooks/evidence_integrity_gate.py`：Code Done、Review、最终 handoff 闭环门禁。
- `hooks/unit_test_result_writer.py`：从 handoff 投影 UTest target 并回写执行状态。
- `hooks/e2e_result_writer.py`：E2E asset/case 与 handoff 关联。
- `hooks/route_checkpoint.py`、`board_core/board_config.json`：失败回流和移除重复 compile guard。
- `skills/autodev/autodev-plan/SKILL.md`：生成策略和字段说明。
- `skills/autodev/autodev-code/SKILL.md`：只生成测试、不运行测试、只做 Batch compile。
- `skills/autodev/autodev-utest/SKILL.md`、`skills/autodev/autodev-e2e/SKILL.md`：消费 handoff、执行测试和失败路由。

## 13. 迁移与兼容

- 新建 Feature 默认使用 `taskValidationPolicy.mode=defer_to_test_stages`。
- 已 finalized 或已开始执行的 `deferred_batch` Feature 不自动迁移。
- 只有未开始执行的旧计划允许通过 Plan repair 显式迁移，并重新生成 Batch、测试资产指纹和 Evidence 契约。
- 迁移不得删除已有测试文件、Evidence、Run 目录或 `deferredValidationIssues`。
- 旧模式的 `validate-batch-task`、Surefire/Failsafe 和 deferred repair 逻辑继续保留。

## 14. 验收测试

### 测试文件完整性

- Code 生成测试文件后手动删除，Code Done 失败。
- 文件被重命名，Review 准入失败。
- 文件为空、只有 package 声明或没有框架可识别测试入口，Code Done 失败。
- selector 对应测试方法被删除，UTest 准入失败。

### 生产代码同步

- Review 前生产文件内容变化，handoff 标记 obsolete。
- 生产公开 API 签名变化，Review 阻断并要求回 Code。
- UTest 修改生产代码后，不能直接推进 UTest Done，必须回 Code 并重新 Review。

### 多 TASK 资产所有权

- 两个 TASK 创建同一路径，Plan preflight 和 Code start 都失败。
- 两个 TASK 修改同一生产类，各自使用行为测试类，不发生覆盖。
- 多 TASK 需要同一 fixture 时只生成一个 shared asset，owner 和 consumer 关系正确。

### E2E 资产时机

- Code 不要求 `E2E_TEST_CASES.yaml` 存在。
- E2E 根据 `generationStage=dev.e2e` 生成并记录 YAML/脚本。
- Java 集成测试可以由 Code 生成，但按 executionStage 在 UTest 或 E2E 执行。

### 执行与 Evidence

- 新策略 Code 进程记录中不得出现 `test`、`verify`、`test-compile`、Surefire 或 Failsafe 执行。
- 每个 Batch 只有一条 required `mvn compile`，且 compile 失败不能进入 Review。
- 未执行测试只能有 `test_asset` generated/integrity_verified Evidence，不能有 validation PASS。
- UTest/E2E 执行后才生成 validation Evidence，并正确关联 `commandId`、`handoffId`、AC 和 scenario。
- Verify 遇到 required `not_run`、`failed` 或 `blocked` handoff item 必须失败。

### 回归兼容

- 旧 `deferred_batch` 计划仍执行原 TASK validation + Batch validation。
- 新旧模式不得共享运行态字段而互相误判；`taskValidationPolicy.mode` 必须作为唯一分流条件。

## 15. 实施顺序

1. 先落 Plan schema、资产指纹和所有权校验。
2. 再改 Runner 和 Evidence Store，建立 compile-only Code 路由。
3. 接入 Code Done、Review、UTest、E2E、Verify 门禁和失败回流。
4. 更新 Board 配置和四个阶段技能文档。
5. 增加上述回归测试，先验证旧模式，再验证新模式。
6. 新 Feature 灰度使用新策略，确认稳定后再考虑旧模式退役。
