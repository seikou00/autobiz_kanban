# plan.json 字段说明文档

基于 `hooks/plan_json.py` 的完整字段定义和枚举值说明。

---

## 📦 根级别结构 (Root Plan)

### 基础字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `featureId` | string | ✓ | 特性ID，用于标识整个计划 |
| `status` | enum | ✓ | 特性整体状态，见 [特性状态](#特性状态) |
| `taskSetStatus` | enum | ✓ | 任务集状态，见 [任务集状态](#任务集状态) |
| `taskSetDigest` | string | - | 任务集摘要（SHA256），用于防篡改校验 |
| `implementationScope` | enum | - | 实现范围，见 [实现范围](#实现范围) |

### 批次管理

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batches` | array | ✓ | 批次列表，每个批次包含一组任务 |
| `batches[].id` | string | ✓ | 批次ID，格式：`B001`, `B002` ... |
| `batches[].path` | string | ✓ | 批次计划文件路径，格式：`plans/{batchId}/plan.json` |
| `batches[].title` | string | ✓ | 批次标题 |
| `batches[].status` | enum | ✓ | 批次状态，见 [批次状态](#批次状态) |
| `batches[].executionLane` | enum | ✓ | 执行通道，见 [执行通道](#执行通道) |
| `batches[].specRoots` | array | ✓ | 规格根引用列表 |
| `batches[].deps` | array | - | 依赖的批次ID列表（格式：`B001`） |
| `batches[].taskIds` | array | ✓ | 包含的任务ID列表（格式：`T001`） |
| `activeBatchId` | string | - | 当前活动批次ID |
| `nextBatchId` | string | - | 下一个待执行批次ID |

### 批次策略

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchPolicy` | object | ✓ | 批次策略配置 |
| `batchPolicy.maxTasks` | number | ✓ | 每批最大任务数（固定值：5） |
| `batchPolicy.strategy` | string | ✓ | 批次策略（固定值：`spec_capability_execution_lane_topological`） |

### 任务验证策略

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskValidationPolicy` | object | ✓ | 任务验证策略 |
| `taskValidationPolicy.mode` | enum | ✓ | 验证模式，见 [验证策略模式](#验证策略模式) |
| `taskValidationPolicy.orchestration` | string | ✓ | 编排方式（固定值：`inline`） |
| `taskValidationPolicy.codeGate` | string | ✓ | 代码门控（固定值：`batch_compile_only`） |
| `taskValidationPolicy.maxTestStageRepairAttempts` | number | ✓ | 最大测试阶段修复尝试次数（固定值：3） |

### 批次验证配置文件

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchValidationProfiles` | object | ✓ | 按执行通道分组的验证配置 |
| `batchValidationProfiles.{lane}` | object | ✓ | 特定通道的验证配置（`backend`/`frontend`） |
| `batchValidationProfiles.{lane}.mode` | enum | ✓ | 验证模式，见 [批次验证模式](#批次验证模式) |
| `batchValidationProfiles.{lane}.commands` | array | ✓ | 验证命令列表 |

### 项目级验证

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `projectValidationCommands` | array | ✓ | 项目级验证命令列表 |
| `projectValidationCommands[].id` | string | ✓ | 命令ID，格式：`PROJECT-VAL-001` |
| `projectValidationCommands[].argv` | array | ✓ | 命令参数数组 |
| `projectValidationCommands[].cwd` | string | ✓ | 工作目录（相对路径） |
| `projectValidationCommands[].kind` | enum | ✓ | 验证类型，见 [项目验证类型](#项目验证类型) |
| `projectValidationCommands[].required` | boolean | ✓ | 是否必需 |
| `projectValidationCommands[].repo` | string | - | 仓库标识 |
| `projectCheckEvidenceIds` | array | - | 项目检查证据ID列表（格式：`ev_0001`） |
| `latestProjectCheckEvidenceId` | string | - | 最新项目检查证据ID |
| `projectValidationDisposition` | object | - | 项目验证延期信息，见 [验证延期](#验证延期) |
| `projectValidationFailedRunIds` | array | - | 失败的验证运行ID列表 |
| `deferredValidationIssues` | array | - | 延期的验证问题列表 |

---

## 📋 批次计划结构 (Batch Plan)

路径：`plans/{batchId}/plan.json`

### 批次基础信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `featureId` | string | ✓ | 特性ID，需与根计划一致 |
| `batchId` | string | ✓ | 批次ID，格式：`B001` |
| `title` | string | ✓ | 批次标题 |
| `status` | enum | ✓ | 批次状态，见 [批次状态](#批次状态) |
| `executionLane` | enum | ✓ | 执行通道，见 [执行通道](#执行通道) |
| `taskCount` | number | ✓ | 任务总数 |
| `completedTaskCount` | number | ✓ | 已完成任务数 |
| `startedAt` | string | - | 开始时间（ISO 8601） |
| `completedAt` | string | - | 完成时间（ISO 8601） |
| `completionEvidenceIds` | array | - | 完成证据ID列表 |

### 批次验证

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchValidation` | object | ✓ | 批次验证配置 |
| `batchValidation.profile` | string | ✓ | 使用的验证配置文件（应与 `executionLane` 一致） |
| `batchValidation.mode` | enum | ✓ | 验证模式，见 [批次验证模式](#批次验证模式) |
| `batchValidation.status` | enum | ✓ | 验证状态，见 [批次验证状态](#批次验证状态) |
| `batchValidation.commands` | array | ✓ | 验证命令列表 |
| `batchValidation.commands[].id` | string | ✓ | 命令ID，格式：`BATCH-B001-VAL-001` |
| `batchValidation.commands[].argv` | array | ✓ | 命令参数 |
| `batchValidation.commands[].cwd` | string | ✓ | 工作目录 |
| `batchValidation.commands[].kind` | string | ✓ | 命令类型（固定值：`compile`） |
| `batchValidation.commands[].required` | boolean | ✓ | 是否必需 |
| `batchValidation.commands[].repo` | string | - | 仓库标识 |
| `batchValidation.coverageCommandIds` | array | - | 覆盖的命令ID列表 |
| `batchValidation.evidenceIds` | array | - | 证据ID列表 |
| `batchValidation.latestPassEvidenceIds` | array | - | 最新通过的证据ID列表 |
| `batchValidation.activeRunId` | string | - | 活动运行ID |
| `batchValidation.deferredIssues` | array | - | 延期问题列表 |

### 批次编译（defer_to_test_stages 策略下）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchCompile` | object | - | 批次编译状态（仅在 defer_to_test_stages 策略下） |
| `batchCompile.status` | enum | ✓ | 编译状态，见 [批次编译状态](#批次编译状态) |
| `batchCompile.commandId` | string | - | 使用的编译命令ID |
| `batchCompile.repairAttempts` | number | ✓ | 修复尝试次数（默认：0） |
| `batchCompile.maxRepairAttempts` | number | ✓ | 最大修复尝试次数（固定值：3） |
| `batchCompile.repairTaskId` | string | - | 当前修复任务ID（status=repairing时） |
| `batchCompile.repairOwnerTaskIds` | array | - | 修复责任任务ID列表 |
| `batchCompile.output` | string | - | 编译输出 |
| `batchCompile.failureCategory` | string | - | 失败类别 |
| `batchCompile.diagnosticPaths` | array | - | 诊断文件路径列表 |
| `batchCompile.requestedCodeWorkspaces` | array | - | 请求的代码工作空间列表 |
| `batchCompile.workspaceSnapshotSha256` | string | - | 工作空间快照SHA256 |
| `batchCompile.implementationEvidenceByTask` | object | - | 按任务ID映射的实现证据ID |
| `batchCompile.implementationRevisionByTask` | object | - | 按任务ID映射的实现版本号 |

### 任务列表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tasks` | array | ✓ | 任务列表，见 [任务结构](#任务结构) |

---

## 📝 任务结构 (Task)

### 基础信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✓ | 任务ID，格式：`T001`, `T002` ... |
| `title` | string | ✓ | 任务标题 |
| `status` | enum | ✓ | 任务状态，见 [任务状态](#任务状态) |
| `goal` | string | ✓ | 任务目标描述 |
| `executionMode` | enum | - | 执行模式，见 [任务执行模式](#任务执行模式) |
| `completionPolicy` | enum | ✓ | 完成策略，见 [完成策略](#完成策略) |

### 依赖与引用

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `deps` | array | - | 依赖的任务ID列表（格式：`T001`） |
| `specRefs` | array | ✓ | 规格引用（必须包含 `REQ-NNN` 和 `SCN-NNN`） |
| `designRefs` | array | - | 设计引用列表 |
| `mergedScenarioRefs` | array | - | 合并的场景引用 |
| `apiIds` | array | - | API ID列表（格式：`API-001`） |
| `dataIds` | array | - | 数据对象ID列表（格式：`DATA-001`） |
| `decisionIds` | array | - | 技术决策ID列表（格式：`D-001`） |
| `blockers` | array | - | 阻塞项列表 |

### 范围定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `scope` | object | ✓ | 任务范围 |
| `scope.modules` | array | ✓ | 涉及的模块列表 |
| `scope.entrypoints` | array | ✓ | 入口点列表 |
| `scope.pages` | array | ✓ | 页面ID列表（格式：`PAGE-001`） |
| `scope.dataObjects` | array | ✓ | 数据对象列表 |
| `scope.paths` | array | - | 文件路径列表（可带仓库前缀：`repo:path/to/file`） |
| `scope.workspaceRoots` | object | - | 工作空间根目录映射（key: 仓库ID，value: 路径） |
| `workspaceRef` | string | ✓ | 工作空间引用（仓库ID或 `default`） |

### 实现要点

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `implementationPoints` | array | ✓ | 实现要点列表（2-6个） |
| `nonGoals` | array | ✓ | 非目标列表（明确不做什么） |
| `validationBoundary` | string | ✓ | 验证边界描述（至少10个字符） |
| `expectedFiles` | array | - | 预期文件列表 |

### 验收标准

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `acceptanceCriteria` | array | ✓ | 验收标准列表 |
| `acceptanceCriteria[].id` | string | ✓ | 标准ID，格式：`AC-T001-01` |
| `acceptanceCriteria[].text` | string | ✓ | 标准描述 |
| `acceptanceCriteria[].scenarioRefs` | array | ✓ | 关联的场景引用 |

### 验证命令

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `validationCommands` | array | ✓ | 验证命令列表（`executionMode=external_dependency` 时为空） |
| `validationCommands[].id` | string | ✓ | 命令ID，格式：`VAL-T001-01` |
| `validationCommands[].argv` | array | ✓ | 命令参数数组 |
| `validationCommands[].cwd` | string | ✓ | 工作目录（相对路径） |
| `validationCommands[].kind` | enum | ✓ | 验证类型，见 [任务验证类型](#任务验证类型) |
| `validationCommands[].required` | boolean | ✓ | 是否必需 |
| `validationCommands[].repo` | string | - | 仓库标识 |
| `validationCommands[].covers` | array | ✓ | 覆盖的验收标准ID列表 |

### 验证测试计划

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `validationTestPlan` | array | - | 验证测试计划 |
| `validationTestPlan[].commandId` | string | ✓ | 关联的验证命令ID |
| `validationTestPlan[].assetType` | enum | ✓ | 资产类型，见 [测试资产类型](#测试资产类型) |
| `validationTestPlan[].executionStage` | enum | ✓ | 执行阶段，见 [测试执行阶段](#测试执行阶段) |
| `validationTestPlan[].covers` | array | ✓ | 覆盖的验收标准ID |
| `validationTestPlan[].testIntent` | object | ✓ | 测试意图 |
| `validationTestPlan[].testIntent.behavior` | string | ✓ | 测试行为描述 |
| `validationTestPlan[].testIntent.acceptanceCriteria` | array | ✓ | 关联的验收标准列表 |

### UI相关

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uiRequired` | boolean | - | 是否需要UI |
| `uiRefs` | object | - | UI引用（`uiRequired=true` 时必填） |
| `uiRefs.pageRefs` | array | ✓ | 页面ID列表（格式：`PAGE-001`） |
| `uiRefs.interactionRefs` | array | ✓ | 交互ID列表（格式：`UIX-001`） |
| `uiRefs.visualSourceRefs` | array | ✓ | 视觉源ID列表（格式：`VIS-001`） |
| `uiRefs.frontendRoute` | enum | ✓ | 前端路由类型，见 [前端路由](#前端路由) |

### 外部依赖

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `externalDependency` | object | - | 外部依赖信息（`executionMode=external_dependency` 时必填） |
| `externalDependency.system` | string | ✓ | 外部系统名称 |
| `externalDependency.owner` | string | ✓ | 负责人 |
| `externalDependency.trackingRefs` | array | ✓ | 跟踪引用列表 |

### 运行时字段（自动维护）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `evidenceIds` | array | - | 所有证据ID列表（格式：`ev_0001`） |
| `implementationEvidenceIds` | array | - | 实现证据ID列表 |
| `latestImplementationEvidenceId` | string | - | 最新实现证据ID |
| `implementationRevision` | number | - | 实现版本号 |
| `validationEvidenceIds` | array | - | 验证证据ID列表 |
| `validationRepairAttempts` | number | - | 验证修复尝试次数（默认：0） |
| `validationDisposition` | object | - | 验证延期信息，见 [验证延期](#验证延期) |
| `completionEvidenceIds` | array | - | 完成证据ID列表 |
| `latestPassEvidenceId` | string | - | 最新通过证据ID |
| `pendingRevalidation` | boolean | - | 待重新验证 |
| `completedRevalidation` | boolean | - | 已完成重新验证 |

---

## 📊 枚举值详解

### 特性状态

**字段**: `status` (根计划)

| 值 | 说明 |
|----|------|
| `todo` | 待开始 |
| `in_progress` | 进行中 |
| `failed` | 失败 |
| `done` | 已完成 |

---

### 任务集状态

**字段**: `taskSetStatus`

| 值 | 说明 |
|----|------|
| `collecting` | 收集中（任务集尚未最终确定） |
| `finalized` | 已最终确定（任务集已锁定） |

---

### 批次状态

**字段**: `batches[].status`, `status` (批次计划)

| 值 | 说明 |
|----|------|
| `todo` | 待开始 |
| `in_progress` | 进行中 |
| `failed` | 失败 |
| `done` | 已完成 |

---

### 任务状态

**字段**: `tasks[].status`

支持多语言别名，标准化后的值：

| 标准值 | 别名 | 说明 |
|--------|------|------|
| `todo` | `pending`, `not_started`, `待做`, `未开始` | 待开始 |
| `in_progress` | `doing`, `进行中` | 进行中 |
| `implemented` | `awaiting_validation`, `待验证` | 已实现，待验证 |
| `validating` | `验证中` | 验证中 |
| `done` | `completed`, `complete`, `pass`, `passed`, `完成`, `已完成` | 已完成 |
| `failed` | `fail`, `blocked`, `失败`, `阻断` | 失败 |

---

### 执行通道

**字段**: `batches[].executionLane`, `executionLane` (批次计划)

| 值 | 说明 |
|----|------|
| `backend` | 后端通道 |
| `frontend` | 前端通道 |

**注意**: 
- 前端批次必须在所有后端批次之后
- 批次内所有任务必须属于同一通道
- 任务通过 `uiRequired=true` 自动分配到 `frontend`，否则为 `backend`

---

### 实现范围

**字段**: `implementationScope`

| 值 | 说明 |
|----|------|
| `full_stack` | 全栈（包含前后端） |
| `backend_only` | 仅后端 |
| `frontend_only` | 仅前端 |

---

### 任务执行模式

**字段**: `tasks[].executionMode`

| 值 | 说明 |
|----|------|
| `code` | 编码实现（默认） |
| `verified_existing` | 验证现有实现 |
| `external_dependency` | 外部依赖（不编码，仅记录） |
# plan.json 字段说明文档

基于 `hooks/plan_json.py` 的完整字段定义和枚举值说明。

---

## 📦 根级别结构 (Root Plan)

### 基础字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `featureId` | string | ✓ | 特性ID，用于标识整个计划 |
| `status` | enum | ✓ | 特性整体状态，见 [特性状态](#特性状态) |
| `taskSetStatus` | enum | ✓ | 任务集状态，见 [任务集状态](#任务集状态) |
| `taskSetDigest` | string | - | 任务集摘要（SHA256），用于防篡改校验 |
| `implementationScope` | enum | - | 实现范围，见 [实现范围](#实现范围) |

### 批次管理

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batches` | array | ✓ | 批次列表，每个批次包含一组任务 |
| `batches[].id` | string | ✓ | 批次ID，格式：`B001`, `B002` ... |
| `batches[].path` | string | ✓ | 批次计划文件路径，格式：`plans/{batchId}/plan.json` |
| `batches[].title` | string | ✓ | 批次标题 |
| `batches[].status` | enum | ✓ | 批次状态，见 [批次状态](#批次状态) |
| `batches[].executionLane` | enum | ✓ | 执行通道，见 [执行通道](#执行通道) |
| `batches[].specRoots` | array | ✓ | 规格根引用列表 |
| `batches[].deps` | array | - | 依赖的批次ID列表（格式：`B001`） |
| `batches[].taskIds` | array | ✓ | 包含的任务ID列表（格式：`T001`） |
| `activeBatchId` | string | - | 当前活动批次ID |
| `nextBatchId` | string | - | 下一个待执行批次ID |

### 批次策略

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchPolicy` | object | ✓ | 批次策略配置 |
| `batchPolicy.maxTasks` | number | ✓ | 每批最大任务数（固定值：5） |
| `batchPolicy.strategy` | string | ✓ | 批次策略（固定值：`spec_capability_execution_lane_topological`） |

### 任务验证策略

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskValidationPolicy` | object | ✓ | 任务验证策略 |
| `taskValidationPolicy.mode` | enum | ✓ | 验证模式，见 [验证策略模式](#验证策略模式) |
| `taskValidationPolicy.orchestration` | string | ✓ | 编排方式（固定值：`inline`） |
| `taskValidationPolicy.codeGate` | string | ✓ | 代码门控（固定值：`batch_compile_only`） |
| `taskValidationPolicy.maxTestStageRepairAttempts` | number | ✓ | 最大测试阶段修复尝试次数（固定值：3） |

### 批次验证配置文件

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchValidationProfiles` | object | ✓ | 按执行通道分组的验证配置 |
| `batchValidationProfiles.{lane}` | object | ✓ | 特定通道的验证配置（`backend`/`frontend`） |
| `batchValidationProfiles.{lane}.mode` | enum | ✓ | 验证模式，见 [批次验证模式](#批次验证模式) |
| `batchValidationProfiles.{lane}.commands` | array | ✓ | 验证命令列表 |

### 项目级验证

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `projectValidationCommands` | array | ✓ | 项目级验证命令列表 |
| `projectValidationCommands[].id` | string | ✓ | 命令ID，格式：`PROJECT-VAL-001` |
| `projectValidationCommands[].argv` | array | ✓ | 命令参数数组 |
| `projectValidationCommands[].cwd` | string | ✓ | 工作目录（相对路径） |
| `projectValidationCommands[].kind` | enum | ✓ | 验证类型，见 [项目验证类型](#项目验证类型) |
| `projectValidationCommands[].required` | boolean | ✓ | 是否必需 |
| `projectValidationCommands[].repo` | string | - | 仓库标识 |
| `projectCheckEvidenceIds` | array | - | 项目检查证据ID列表（格式：`ev_0001`） |
| `latestProjectCheckEvidenceId` | string | - | 最新项目检查证据ID |
| `projectValidationDisposition` | object | - | 项目验证延期信息，见 [验证延期](#验证延期) |
| `projectValidationFailedRunIds` | array | - | 失败的验证运行ID列表 |
| `deferredValidationIssues` | array | - | 延期的验证问题列表 |

---

## 📋 批次计划结构 (Batch Plan)

路径：`plans/{batchId}/plan.json`

### 批次基础信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `featureId` | string | ✓ | 特性ID，需与根计划一致 |
| `batchId` | string | ✓ | 批次ID，格式：`B001` |
| `title` | string | ✓ | 批次标题 |
| `status` | enum | ✓ | 批次状态，见 [批次状态](#批次状态) |
| `executionLane` | enum | ✓ | 执行通道，见 [执行通道](#执行通道) |
| `taskCount` | number | ✓ | 任务总数 |
| `completedTaskCount` | number | ✓ | 已完成任务数 |
| `startedAt` | string | - | 开始时间（ISO 8601） |
| `completedAt` | string | - | 完成时间（ISO 8601） |
| `completionEvidenceIds` | array | - | 完成证据ID列表 |

### 批次验证

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchValidation` | object | ✓ | 批次验证配置 |
| `batchValidation.profile` | string | ✓ | 使用的验证配置文件（应与 `executionLane` 一致） |
| `batchValidation.mode` | enum | ✓ | 验证模式，见 [批次验证模式](#批次验证模式) |
| `batchValidation.status` | enum | ✓ | 验证状态，见 [批次验证状态](#批次验证状态) |
| `batchValidation.commands` | array | ✓ | 验证命令列表 |
| `batchValidation.commands[].id` | string | ✓ | 命令ID，格式：`BATCH-B001-VAL-001` |
| `batchValidation.commands[].argv` | array | ✓ | 命令参数 |
| `batchValidation.commands[].cwd` | string | ✓ | 工作目录 |
| `batchValidation.commands[].kind` | string | ✓ | 命令类型（固定值：`compile`） |
| `batchValidation.commands[].required` | boolean | ✓ | 是否必需 |
| `batchValidation.commands[].repo` | string | - | 仓库标识 |
| `batchValidation.coverageCommandIds` | array | - | 覆盖的命令ID列表 |
| `batchValidation.evidenceIds` | array | - | 证据ID列表 |
| `batchValidation.latestPassEvidenceIds` | array | - | 最新通过的证据ID列表 |
| `batchValidation.activeRunId` | string | - | 活动运行ID |
| `batchValidation.deferredIssues` | array | - | 延期问题列表 |

### 批次编译（defer_to_test_stages 策略下）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `batchCompile` | object | - | 批次编译状态（仅在 defer_to_test_stages 策略下） |
| `batchCompile.status` | enum | ✓ | 编译状态，见 [批次编译状态](#批次编译状态) |
| `batchCompile.commandId` | string | - | 使用的编译命令ID |
| `batchCompile.repairAttempts` | number | ✓ | 修复尝试次数（默认：0） |
| `batchCompile.maxRepairAttempts` | number | ✓ | 最大修复尝试次数（固定值：3） |
| `batchCompile.repairTaskId` | string | - | 当前修复任务ID（status=repairing时） |
| `batchCompile.repairOwnerTaskIds` | array | - | 修复责任任务ID列表 |
| `batchCompile.output` | string | - | 编译输出 |
| `batchCompile.failureCategory` | string | - | 失败类别 |
| `batchCompile.diagnosticPaths` | array | - | 诊断文件路径列表 |
| `batchCompile.requestedCodeWorkspaces` | array | - | 请求的代码工作空间列表 |
| `batchCompile.workspaceSnapshotSha256` | string | - | 工作空间快照SHA256 |
| `batchCompile.implementationEvidenceByTask` | object | - | 按任务ID映射的实现证据ID |
| `batchCompile.implementationRevisionByTask` | object | - | 按任务ID映射的实现版本号 |

### 任务列表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tasks` | array | ✓ | 任务列表，见 [任务结构](#任务结构) |

---

## 📝 任务结构 (Task)

### 基础信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✓ | 任务ID，格式：`T001`, `T002` ... |
| `title` | string | ✓ | 任务标题 |
| `status` | enum | ✓ | 任务状态，见 [任务状态](#任务状态) |
| `goal` | string | ✓ | 任务目标描述 |
| `executionMode` | enum | - | 执行模式，见 [任务执行模式](#任务执行模式) |
| `completionPolicy` | enum | ✓ | 完成策略，见 [完成策略](#完成策略) |

### 依赖与引用

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `deps` | array | - | 依赖的任务ID列表（格式：`T001`） |
| `specRefs` | array | ✓ | 规格引用（必须包含 `REQ-NNN` 和 `SCN-NNN`） |
| `designRefs` | array | - | 设计引用列表 |
| `mergedScenarioRefs` | array | - | 合并的场景引用 |
| `apiIds` | array | - | API ID列表（格式：`API-001`） |
| `dataIds` | array | - | 数据对象ID列表（格式：`DATA-001`） |
| `decisionIds` | array | - | 技术决策ID列表（格式：`D-001`） |
| `blockers` | array | - | 阻塞项列表 |

### 范围定义

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `scope` | object | ✓ | 任务范围 |
| `scope.modules` | array | ✓ | 涉及的模块列表 |
| `scope.entrypoints` | array | ✓ | 入口点列表 |
| `scope.pages` | array | ✓ | 页面ID列表（格式：`PAGE-001`） |
| `scope.dataObjects` | array | ✓ | 数据对象列表 |
| `scope.paths` | array | - | 文件路径列表（可带仓库前缀：`repo:path/to/file`） |
| `scope.workspaceRoots` | object | - | 工作空间根目录映射（key: 仓库ID，value: 路径） |
| `workspaceRef` | string | ✓ | 工作空间引用（仓库ID或 `default`） |

### 实现要点

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `implementationPoints` | array | ✓ | 实现要点列表（2-6个） |
| `nonGoals` | array | ✓ | 非目标列表（明确不做什么） |
| `validationBoundary` | string | ✓ | 验证边界描述（至少10个字符） |
| `expectedFiles` | array | - | 预期文件列表 |

### 验收标准

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `acceptanceCriteria` | array | ✓ | 验收标准列表 |
| `acceptanceCriteria[].id` | string | ✓ | 标准ID，格式：`AC-T001-01` |
| `acceptanceCriteria[].text` | string | ✓ | 标准描述 |
| `acceptanceCriteria[].scenarioRefs` | array | ✓ | 关联的场景引用 |

### 验证命令

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `validationCommands` | array | ✓ | 验证命令列表（`executionMode=external_dependency` 时为空） |
| `validationCommands[].id` | string | ✓ | 命令ID，格式：`VAL-T001-01` |
| `validationCommands[].argv` | array | ✓ | 命令参数数组 |
| `validationCommands[].cwd` | string | ✓ | 工作目录（相对路径） |
| `validationCommands[].kind` | enum | ✓ | 验证类型，见 [任务验证类型](#任务验证类型) |
| `validationCommands[].required` | boolean | ✓ | 是否必需 |
| `validationCommands[].repo` | string | - | 仓库标识 |
| `validationCommands[].covers` | array | ✓ | 覆盖的验收标准ID列表 |

### 验证测试计划

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `validationTestPlan` | array | - | 验证测试计划 |
| `validationTestPlan[].commandId` | string | ✓ | 关联的验证命令ID |
| `validationTestPlan[].assetType` | enum | ✓ | 资产类型，见 [测试资产类型](#测试资产类型) |
| `validationTestPlan[].executionStage` | enum | ✓ | 执行阶段，见 [测试执行阶段](#测试执行阶段) |
| `validationTestPlan[].covers` | array | ✓ | 覆盖的验收标准ID |
| `validationTestPlan[].testIntent` | object | ✓ | 测试意图 |
| `validationTestPlan[].testIntent.behavior` | string | ✓ | 测试行为描述 |
| `validationTestPlan[].testIntent.acceptanceCriteria` | array | ✓ | 关联的验收标准列表 |

### UI相关

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `uiRequired` | boolean | - | 是否需要UI |
| `uiRefs` | object | - | UI引用（`uiRequired=true` 时必填） |
| `uiRefs.pageRefs` | array | ✓ | 页面ID列表（格式：`PAGE-001`） |
| `uiRefs.interactionRefs` | array | ✓ | 交互ID列表（格式：`UIX-001`） |
| `uiRefs.visualSourceRefs` | array | ✓ | 视觉源ID列表（格式：`VIS-001`） |
| `uiRefs.frontendRoute` | enum | ✓ | 前端路由类型，见 [前端路由](#前端路由) |

### 外部依赖

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `externalDependency` | object | - | 外部依赖信息（`executionMode=external_dependency` 时必填） |
| `externalDependency.system` | string | ✓ | 外部系统名称 |
| `externalDependency.owner` | string | ✓ | 负责人 |
| `externalDependency.trackingRefs` | array | ✓ | 跟踪引用列表 |

### 运行时字段（自动维护）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `evidenceIds` | array | - | 所有证据ID列表（格式：`ev_0001`） |
| `implementationEvidenceIds` | array | - | 实现证据ID列表 |
| `latestImplementationEvidenceId` | string | - | 最新实现证据ID |
| `implementationRevision` | number | - | 实现版本号 |
| `validationEvidenceIds` | array | - | 验证证据ID列表 |
| `validationRepairAttempts` | number | - | 验证修复尝试次数（默认：0） |
| `validationDisposition` | object | - | 验证延期信息，见 [验证延期](#验证延期) |
| `completionEvidenceIds` | array | - | 完成证据ID列表 |
| `latestPassEvidenceId` | string | - | 最新通过证据ID |
| `pendingRevalidation` | boolean | - | 待重新验证 |
| `completedRevalidation` | boolean | - | 已完成重新验证 |

---

## 📊 枚举值详解

### 特性状态

**字段**: `status` (根计划)

| 值 | 说明 |
|----|------|
| `todo` | 待开始 |
| `in_progress` | 进行中 |
| `failed` | 失败 |
| `done` | 已完成 |

---

### 任务集状态

**字段**: `taskSetStatus`

| 值 | 说明 |
|----|------|
| `collecting` | 收集中（任务集尚未最终确定） |
| `finalized` | 已最终确定（任务集已锁定） |

---

### 批次状态

**字段**: `batches[].status`, `status` (批次计划)

| 值 | 说明 |
|----|------|
| `todo` | 待开始 |
| `in_progress` | 进行中 |
| `failed` | 失败 |
| `done` | 已完成 |

---

### 任务状态

**字段**: `tasks[].status`

支持多语言别名，标准化后的值：

| 标准值 | 别名 | 说明 |
|--------|------|------|
| `todo` | `pending`, `not_started`, `待做`, `未开始` | 待开始 |
| `in_progress` | `doing`, `进行中` | 进行中 |
| `implemented` | `awaiting_validation`, `待验证` | 已实现，待验证 |
| `validating` | `验证中` | 验证中 |
| `done` | `completed`, `complete`, `pass`, `passed`, `完成`, `已完成` | 已完成 |
| `failed` | `fail`, `blocked`, `失败`, `阻断` | 失败 |

---

### 执行通道

**字段**: `batches[].executionLane`, `executionLane` (批次计划)

| 值 | 说明 |
|----|------|
| `backend` | 后端通道 |
| `frontend` | 前端通道 |

**注意**:
- 前端批次必须在所有后端批次之后
- 批次内所有任务必须属于同一通道
- 任务通过 `uiRequired=true` 自动分配到 `frontend`，否则为 `backend`

---

### 实现范围

**字段**: `implementationScope`

| 值 | 说明 |
|----|------|
| `full_stack` | 全栈（包含前后端） |
| `backend_only` | 仅后端 |
| `frontend_only` | 仅前端 |

---

### 任务执行模式

**字段**: `tasks[].executionMode`

| 值 | 说明 |
|----|------|
| `code` | 编码实现（默认） |
| `verified_existing` | 验证现有实现 |
| `external_dependency` | 外部依赖（不编码，仅记录） |

---

### 完成策略

**字段**: `tasks[].completionPolicy`

| 值 | 说明 | 适用场景 |
|----|------|----------|
| `all_required_validations_pass` | 所有必需验证通过 | `executionMode=code` 或 `verified_existing` |
| `external_dependency_recorded` | 外部依赖已记录 | `executionMode=external_dependency` |

---

### 验证策略模式

**字段**: `taskValidationPolicy.mode`

| 值 | 说明 |
|----|------|
| `defer_to_test_stages` | 延迟到测试阶段（批次编译+批次验证） |

---

### 批次验证模式

**字段**: `batchValidation.mode`, `batchValidationProfiles.{lane}.mode`

| 值 | 说明 |
|----|------|
| `commands` | 基于命令的验证 |

---

### 批次验证状态

**字段**: `batchValidation.status`

| 值 | 说明 |
|----|------|
| `pending` | 待验证 |
| `running` | 运行中 |
| `failed` | 失败 |
| `revalidation_required` | 需要重新验证 |
| `passed` | 已通过 |
| `deferred` | 已延期 |

---

### 批次编译状态

**字段**: `batchCompile.status`

| 值 | 说明 |
|----|------|
| `pending` | 待编译 |
| `repairing` | 修复中 |
| `failed` | 失败 |
| `passed` | 已通过 |

---

### 任务验证类型

**字段**: `validationCommands[].kind`

从 `hooks/validation_policy.py` 中的 `TASK_VALIDATION_KINDS` 导入，通常包括：

| 值 | 说明 | 适用通道 |
|----|------|----------|
| `unit_test` | 单元测试 | `backend` / `frontend` |
| `integration_test` | 集成测试 | `backend` / `frontend` |
| `compile` | 编译（前端专用） | `frontend` |
| `typecheck` | 类型检查（前端专用） | `frontend` |

---

### 项目验证类型

**字段**: `projectValidationCommands[].kind`

| 值 | 说明 |
|----|------|
| `integration_test` | 集成测试 |
| `e2e_test` | 端到端测试 |
| `static_check` | 静态检查 |

---

### 前端路由

**字段**: `uiRefs.frontendRoute`

| 值 | 说明 |
|----|------|
| `none` | 无前端路由 |
| `spec-driven-ui` | 规格驱动UI |
| `absolute-html` | 绝对路径HTML |
| `standard-html` | 标准HTML |
| `missing-html` | 缺失HTML |

---

### 测试资产类型

**字段**: `validationTestPlan[].assetType`

| 值 | 说明 |
|----|------|
| `unit_test` | 单元测试 |
| `integration_test` | 集成测试 |
| `e2e_test` | 端到端测试 |

---

### 测试执行阶段

**字段**: `validationTestPlan[].executionStage`

| 值 | 说明 |
|----|------|
| `with_code` | 与代码一起执行 |
| `post_batch` | 批次后执行 |

---

### 验证延期

**结构**: `validationDisposition`, `projectValidationDisposition`, `deferredValidationIssues[]`, `batchValidation.deferredIssues[]`

| 字段 | 类型 | 说明 |
|------|------|------|
| `issueId` | string | 问题ID |
| `scope` | enum | 范围：`task` / `batch` / `project` |
| `status` | string | 状态（固定值：`deferred`） |
| `reason` | enum | 原因，见下表 |
| `errorCategory` | enum | 错误类别，见下表 |
| `commandId` | string | 关联的命令ID |
| `repairAttempts` | number | 修复尝试次数 |
| `maxRepairAttempts` | number | 最大修复尝试次数 |
| `evidenceIds` | array | 证据ID列表 |
| `handoffStages` | array | 移交阶段列表 |
| `createdAt` | string | 创建时间 |
| `taskId` | string | 任务ID（仅 `scope=task`） |

#### 延期原因

| 值 | 说明 |
|----|------|
| `environment_failure` | 环境失败 |
| `repair_attempts_exhausted` | 修复尝试已耗尽 |
| `external_dependency` | 外部依赖 |

#### 错误类别

| 值 | 说明 |
|----|------|
| `external_dependency` | 外部依赖 |
| `environment_failure` | 环境失败 |
| `source_compile_failure` | 源码编译失败 |
| `behavior_test_failure` | 行为测试失败 |
| `validation_contract_failure` | 验证契约失败 |
| `workspace_changed` | 工作空间变更 |
| `runner_integrity_failure` | 运行器完整性失败 |

---

## 📐 ID 格式规范

| ID 类型 | 格式 | 示例 |
|---------|------|------|
| 任务ID | `T\d{3}` | `T001`, `T099` |
| 批次ID | `B\d{3}` | `B001`, `B010` |
| 需求ID | `REQ-\d{3}` | `REQ-001` |
| 场景ID | `SCN-\d{3}` | `SCN-002` |
| API ID | `API-\d{3}` | `API-005` |
| 数据ID | `DATA-\d{3}` | `DATA-003` |
| 技术决策ID | `D-\d{3}` | `D-001` |
| 证据ID | `ev_\d{4}` | `ev_0001`, `ev_0123` |
| 验收标准ID | `AC-T\d{3}-\d{2,3}` | `AC-T001-01` |
| 任务验证命令ID | `VAL-T\d{3}-\d{2,3}` | `VAL-T001-01` |
| 项目验证命令ID | `PROJECT-VAL-\d{3}` | `PROJECT-VAL-001` |
| 批次验证命令ID | `BATCH-B\d{3}-VAL-\d{3}` | `BATCH-B001-VAL-001` |
| 页面ID | `PAGE-\d{3}` | `PAGE-001` |
| 交互ID | `UIX-\d{3}` | `UIX-001` |
| 视觉源ID | `VIS-\d{3}` | `VIS-001` |
| 仓库ID | `[A-Za-z0-9._-]+` | `default`, `my-repo` |
| Digest | `[0-9a-f]{64}` | SHA256 哈希值 |

---

## 🔒 常量约束

| 常量 | 值 | 说明 |
|------|-----|------|
| `MAX_BATCH_TASKS` | 5 | 每批最大任务数 |
| `BATCH_STRATEGY` | `spec_capability_execution_lane_topological` | 批次策略（固定值） |
| `BATCH_COMPILE_MAX_REPAIR_ATTEMPTS` | 3 | 批次编译最大修复尝试次数 |
| `DEFAULT_WORKSPACE_ROOT` | `default` | 默认工作空间根标识 |
| `VALIDATION_DEFERRAL_STATUS` | `deferred` | 验证延期状态（固定值） |

---

## 📖 使用示例

### 最小根计划结构

```json
{
  "featureId": "FEAT-001",
  "status": "todo",
  "taskSetStatus": "collecting",
  "taskSetDigest": null,
  "implementationScope": "full_stack",
  "batchPolicy": {
    "maxTasks": 5,
    "strategy": "spec_capability_execution_lane_topological"
  },
  "taskValidationPolicy": {
    "mode": "defer_to_test_stages",
    "orchestration": "inline",
    "codeGate": "batch_compile_only",
    "maxTestStageRepairAttempts": 3
  },
  "batches": [],
  "batchValidationProfiles": {
    "backend": {
      "mode": "commands",
      "commands": []
    },
    "frontend": {
      "mode": "commands",
      "commands": []
    }
  },
  "projectValidationCommands": []
}
```

### 最小任务结构

```json
{
  "id": "T001",
  "title": "实现用户登录",
  "status": "todo",
  "goal": "实现用户登录功能",
  "executionMode": "code",
  "completionPolicy": "all_required_validations_pass",
  "deps": [],
  "specRefs": ["REQ-001", "SCN-001"],
  "scope": {
    "modules": ["user"],
    "entrypoints": ["login"],
    "pages": [],
    "dataObjects": []
  },
  "workspaceRef": "default",
  "implementationPoints": [
    "创建登录表单",
    "实现认证逻辑"
  ],
  "nonGoals": ["不实现注册功能"],
  "validationBoundary": "登录接口返回正确的token",
  "acceptanceCriteria": [
    {
      "id": "AC-T001-01",
      "text": "用户输入正确的用户名密码可以登录",
      "scenarioRefs": ["SCN-001"]
    }
  ],
  "validationCommands": [],
  "uiRequired": false
}
```

---

## 📚 相关文件

- `hooks/plan_json.py` - 主要实现文件
- `hooks/validation_policy.py` - 验证策略定义
- `scripts/disable_digest_check.py` - digest 校验屏蔽脚本

---

**文档生成时间**: 2026-08-14  
**基于版本**: hooks/plan_json.py (当前版本)

---

### 完成策略

**字段**: `tasks[].completionPolicy`

| 值 | 说明 | 适用场景 |
|----|------|----------|
| `all_required_validations_pass` | 所有必需验证通过 | `executionMode=code` 或 `verified_existing` |
| `external_dependency_recorded` | 外部依赖已记录 | `executionMode=external_dependency` |

---

### 验证策略模式

**字段**: `taskValidationPolicy.mode`

| 值 | 说明 |
|----|------|
| `defer_to_test_stages` | 延迟到测试阶段（批次编译+批次验证） |

---

### 批次验证模式

**字段**: `batchValidation.mode`, `batchValidationProfiles.{lane}.mode`

| 值 | 说明 |
|----|------|
| `commands` | 基于命令的验证 |

---

### 批次验证状态

**字段**: `batchValidation.status`

| 值 | 说明 |
|----|------|
| `pending` | 待验证 |
| `running` | 运行中 |
| `failed` | 失败 |
| `revalidation_required` | 需要重新验证 |
| `passed` | 已通过 |
| `deferred` | 已延期 |

---

### 批次编译状态

**字段**: `batchCompile.status`

| 值 | 说明 |
|----|------|
| `pending` | 待编译 |
| `repairing` | 修复中 |
| `failed` | 失败 |
| `passed` | 已通过 |

---

### 任务验证类型

**字段**: `validationCommands[].kind`

从 `hooks/validation_policy.py` 中的 `TASK_VALIDATION_KINDS` 导入，通常包括：

| 值 | 说明 | 适用通道 |
|----|------|----------|
| `unit_test` | 单元测试 | `backend` / `frontend` |
| `integration_test` | 集成测试 | `backend` / `frontend` |
| `compile` | 编译（前端专用） | `frontend` |
| `typecheck` | 类型检查（前端专用） | `frontend` |

---

### 项目验证类型

**字段**: `projectValidationCommands[].kind`

| 值 | 说明 |
|----|------|
| `integration_test` | 集成测试 |
| `e2e_test` | 端到端测试 |
| `static_check` | 静态检查 |

---

### 前端路由

**字段**: `uiRefs.frontendRoute`

| 值 | 说明 |
|----|------|
| `none` | 无前端路由 |
| `spec-driven-ui` | 规格驱动UI |
| `absolute-html` | 绝对路径HTML |
| `standard-html` | 标准HTML |
| `missing-html` | 缺失HTML |

---

### 测试资产类型

**字段**: `validationTestPlan[].assetType`

| 值 | 说明 |
|----|------|
| `unit_test` | 单元测试 |
| `integration_test` | 集成测试 |
| `e2e_test` | 端到端测试 |

---

### 测试执行阶段

**字段**: `validationTestPlan[].executionStage`

| 值 | 说明 |
|----|------|
| `with_code` | 与代码一起执行 |
| `post_batch` | 批次后执行 |

---

### 验证延期

**结构**: `validationDisposition`, `projectValidationDisposition`, `deferredValidationIssues[]`, `batchValidation.deferredIssues[]`

| 字段 | 类型 | 说明 |
|------|------|------|
| `issueId` | string | 问题ID |
| `scope` | enum | 范围：`task` / `batch` / `project` |
| `status` | string | 状态（固定值：`deferred`） |
| `reason` | enum | 原因，见下表 |
| `errorCategory` | enum | 错误类别，见下表 |
| `commandId` | string | 关联的命令ID |
| `repairAttempts` | number | 修复尝试次数 |
| `maxRepairAttempts` | number | 最大修复尝试次数 |
| `evidenceIds` | array | 证据ID列表 |
| `handoffStages` | array | 移交阶段列表 |
| `createdAt` | string | 创建时间 |
| `taskId` | string | 任务ID（仅 `scope=task`） |

#### 延期原因

| 值 | 说明 |
|----|------|
| `environment_failure` | 环境失败 |
| `repair_attempts_exhausted` | 修复尝试已耗尽 |
| `external_dependency` | 外部依赖 |

#### 错误类别

| 值 | 说明 |
|----|------|
| `external_dependency` | 外部依赖 |
| `environment_failure` | 环境失败 |
| `source_compile_failure` | 源码编译失败 |
| `behavior_test_failure` | 行为测试失败 |
| `validation_contract_failure` | 验证契约失败 |
| `workspace_changed` | 工作空间变更 |
| `runner_integrity_failure` | 运行器完整性失败 |

---

## 📐 ID 格式规范

| ID 类型 | 格式 | 示例 |
|---------|------|------|
| 任务ID | `T\d{3}` | `T001`, `T099` |
| 批次ID | `B\d{3}` | `B001`, `B010` |
| 需求ID | `REQ-\d{3}` | `REQ-001` |
| 场景ID | `SCN-\d{3}` | `SCN-002` |
| API ID | `API-\d{3}` | `API-005` |
| 数据ID | `DATA-\d{3}` | `DATA-003` |
| 技术决策ID | `D-\d{3}` | `D-001` |
| 证据ID | `ev_\d{4}` | `ev_0001`, `ev_0123` |
| 验收标准ID | `AC-T\d{3}-\d{2,3}` | `AC-T001-01` |
| 任务验证命令ID | `VAL-T\d{3}-\d{2,3}` | `VAL-T001-01` |
| 项目验证命令ID | `PROJECT-VAL-\d{3}` | `PROJECT-VAL-001` |
| 批次验证命令ID | `BATCH-B\d{3}-VAL-\d{3}` | `BATCH-B001-VAL-001` |
| 页面ID | `PAGE-\d{3}` | `PAGE-001` |
| 交互ID | `UIX-\d{3}` | `UIX-001` |
| 视觉源ID | `VIS-\d{3}` | `VIS-001` |
| 仓库ID | `[A-Za-z0-9._-]+` | `default`, `my-repo` |
| Digest | `[0-9a-f]{64}` | SHA256 哈希值 |

---

## 🔒 常量约束

| 常量 | 值 | 说明 |
|------|-----|------|
| `MAX_BATCH_TASKS` | 5 | 每批最大任务数 |
| `BATCH_STRATEGY` | `spec_capability_execution_lane_topological` | 批次策略（固定值） |
| `BATCH_COMPILE_MAX_REPAIR_ATTEMPTS` | 3 | 批次编译最大修复尝试次数 |
| `DEFAULT_WORKSPACE_ROOT` | `default` | 默认工作空间根标识 |
| `VALIDATION_DEFERRAL_STATUS` | `deferred` | 验证延期状态（固定值） |

---

## 📖 使用示例

### 最小根计划结构

```json
{
  "featureId": "FEAT-001",
  "status": "todo",
  "taskSetStatus": "collecting",
  "taskSetDigest": null,
  "implementationScope": "full_stack",
  "batchPolicy": {
    "maxTasks": 5,
    "strategy": "spec_capability_execution_lane_topological"
  },
  "taskValidationPolicy": {
    "mode": "defer_to_test_stages",
    "orchestration": "inline",
    "codeGate": "batch_compile_only",
    "maxTestStageRepairAttempts": 3
  },
  "batches": [],
  "batchValidationProfiles": {
    "backend": {
      "mode": "commands",
      "commands": []
    },
    "frontend": {
      "mode": "commands",
      "commands": []
    }
  },
  "projectValidationCommands": []
}
```

### 最小任务结构

```json
{
  "id": "T001",
  "title": "实现用户登录",
  "status": "todo",
  "goal": "实现用户登录功能",
  "executionMode": "code",
  "completionPolicy": "all_required_validations_pass",
  "deps": [],
  "specRefs": ["REQ-001", "SCN-001"],
  "scope": {
    "modules": ["user"],
    "entrypoints": ["login"],
    "pages": [],
    "dataObjects": []
  },
  "workspaceRef": "default",
  "implementationPoints": [
    "创建登录表单",
    "实现认证逻辑"
  ],
  "nonGoals": ["不实现注册功能"],
  "validationBoundary": "登录接口返回正确的token",
  "acceptanceCriteria": [
    {
      "id": "AC-T001-01",
      "text": "用户输入正确的用户名密码可以登录",
      "scenarioRefs": ["SCN-001"]
    }
  ],
  "validationCommands": [],
  "uiRequired": false
}
```

---

## 📚 相关文件

- `hooks/plan_json.py` - 主要实现文件
- `hooks/validation_policy.py` - 验证策略定义
- `scripts/disable_digest_check.py` - digest 校验屏蔽脚本

---

**文档生成时间**: 2026-08-14  
**基于版本**: hooks/plan_json.py (当前版本)
