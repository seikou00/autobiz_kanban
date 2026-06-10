---
name: autoops-cicd
description: CI/CD 阶段技能。支持承接 Dev 产物，或在已有代码仓库中直接进入流水线准备与阻断问题记录。
version: v1.1.1604
author: zhangQiuFeng
---

<!-- AUTODEV_RUNTIME_CONTRACT:BEGIN -->
## 流程契约（Source Bundle + Method Bundle）

当前 skill 的 checkpoint、输入/输出产物、读取方式和 validators 以 `$PLUGIN_ROOT/board_core/board_config.json` 的编译结果为唯一事实来源；本文档不维护产物清单，不要依赖文中写死的文件名。
进入执行前，先取当前 Feature 的契约（一次返回两个 bundle）：

```bash
python "$PLUGIN_ROOT/hooks/inspect_skill_contract.py" autoops-cicd --feature "$FEATURE_ID" --json
```

- **Source Bundle（读什么）**：`sourceBundle`/`required_inputs` 列出本 Feature 当前工作流下要读取的真实产物文件；按清单读原件，不要读取清单之外的阶段产物作为硬依赖。
- **Method Bundle（怎么读）**：每个 input 的 `extract` 给出读取重点（focus）、读取方式（method）和缺失降级（degrade）；按它决定读哪些部分、如何提取上下文。
- **停止条件**：仅当 `required_inputs` 中的产物缺失时停止；契约未列出的产物不要硬等。
- **降级语义**：`external: true` 的输入不在本工作流内生成；缺失时按其 `extract.degrade` 的退化读法继续执行，不要因缺失而停止。

无 `$FEATURE_ID` 时可省略 `--feature` 查看基线契约。
<!-- AUTODEV_RUNTIME_CONTRACT:END -->


**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；只用于代码探索、实现和验证。

# /autoops-cicd — CI/CD 清单与流水线阻断处理

### 正常模式

- 上游入口：`checkpoint = e2e_done`
- 这是 Dev 阶段进入 CI/CD 的唯一合法交接点
- 使用场景：完整走完 Biz / Dev 链路后，继续生成 CI/CD 清单与 PR 描述


## 产物输出约定

如本技能为某个 Feature 生成交付物，产物统一写入最外层工作目录 `.autobizdevops`：

- CI/CD 清单：`{FEATURE_DIR}/CICD_CHECKLIST.md`
- PR 描述草稿：`{FEATURE_DIR}/PR_BODY.md`
- 全局状态：`.autobizdevops/state.json`

如用户额外提供 `PRD.md` 或 `design.md`，可在 `CICD_CHECKLIST.md` 中记录其来源；未提供时允许继续，但必须明确写明“需求/设计文档缺失或未提供”。

## 使用场景

- 用户希望生成发布前检查清单
- 用户希望准备 PR 描述
- 用户希望触发或跟踪流水线构建
- 用户希望整理流水线阻断问题并沉淀到过程文档

## 输入参数

- `pipeline_code`（string，可选）：流水线编号，例如 `pl689872`
- `--feature {slug}`（推荐）：指定当前 Feature

## 执行步骤

### Step 1: 标准化工作目录与 State 快照

1. 确定 `{slug}`，进入 `{FEATURE_DIR}/`
2. 调用脚本读取当前 Feature 快照，并把 stdout 捕获为 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

3. 后续准入、恢复和完成判断直接取用 `CHECKPOINT`。若脚本提示 Feature 不存在，仅用户直供 CI/CD 场景可继续通过 `--allow-create` 创建；创建后必须刷新 `CHECKPOINT`。
4. 若尚未执行 workspace 初始化，先执行 `python hooks/init_workspace.py .`
5. 读取仓库构建配置、流水线配置、已有流程产物和用户输入，整理 CI/CD 所需上下文
6. 使用统一脚本将当前 Feature 的 checkpoint 推进为 `cicd_in_progress`。写 `CI/CD（来源: Dev 交接）`：

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint cicd_in_progress --stage "CI/CD（来源: 用户直供）" --allow-create
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

### Step 2: 生成交付文档

1. 生成 `{FEATURE_DIR}/CICD_CHECKLIST.md`
2. 生成 `{FEATURE_DIR}/PR_BODY.md`
3. 若已知 PRD 或 API 来源，在 `CICD_CHECKLIST.md` 或 `PR_BODY.md` 中标注引用路径
4. 若需求文档缺失，必须在 `CICD_CHECKLIST.md` 中记录：

```markdown
- **需求文档状态:** 缺失/未提供
- **影响:** 仅能按当前代码仓库状态生成 CI/CD 清单，后续需人工复核需求一致性
```

### Step 3: 构建流水线

1. 如用户提供 `pipeline_code`，可进入流水线构建与状态轮询
2. 流水线构建：使用工具pipelineBuild构建流水线，员工编号为12345，用户姓名为张三，返回值为构建编号`pipeline_build_num`
3. 轮询命令保持现有约定：

```bash
python hooks/poll_pipeline_status.py --pipelineCode <pipeline_code> --pipelineNum <pipeline_build_num>
```

4. 该脚本为耗时操作，可后台运行

### Step 4: 处理流水线状态

1. 构建中：
   - 提示用户已达到最大轮询周期，需要人工继续观察
   - 在 `CICD_CHECKLIST.md` 中记录“流水线仍在执行中”
2. 构建成功或无法获取状态：
   - 告知用户当前状态
   - 保持 `cicd_in_progress`，等待用户确认是否完成
3. 构建失败：
   - 不再分发到仓库中不存在的技能
   - 必须把阻断问题分类记录到 `CICD_CHECKLIST.md`
   - 暂停并等待用户人工处理，或等待后续明确的技能接管

推荐记录格式：

```markdown
## 已知阻断问题
- [类型] [问题标题]
  - 来源: 流水线/阻断文件/人工说明
  - 当前状态: 待处理
  - 建议回流阶段: Biz / Dev / Ops
  - 备注: [需要的人工动作或上下文]
```

### Step 5: 用户确认后完成阶段

1. 本技能不得执行 git 写命令
2. 只有在用户明确回复“已执行 / 已完成 / done / ok”后，才允许用统一脚本把 checkpoint 推进到 `cicd_done`
3. 若用户未确认，保持 `cicd_in_progress`

macOS/Linux:

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint cicd_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

### Step 6: 是否再次执行

1. 需要再次触发流水线或重新整理清单时，必须先询问用户
2. 未经用户同意，不能擅自重跑
