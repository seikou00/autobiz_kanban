---
name: autoops-cicd
description: CI/CD 阶段技能。支持承接 Dev 产物，或在已有代码仓库中直接进入流水线准备与阻断问题记录。
version: 1.1.0
author: zhangQiuFeng
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。

# /autoops-cicd — CI/CD 清单与流水线阻断处理

### 正常模式

- 上游入口：`checkpoint = e2e_done`
- 这是 Dev 阶段进入 CI/CD 的唯一合法交接点
- 使用场景：完整走完 Biz / Dev 链路后，继续生成 CI/CD 清单与 PR 描述


## 产物输出约定

如本技能为某个 Feature 生成交付物，产物统一写入最外层工作目录 `.autobizdevops`：

- CI/CD 清单：`.autobizdevops/features/{slug}/CICD_CHECKLIST.md`
- PR 描述草稿：`.autobizdevops/features/{slug}/PR_BODY.md`
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

### Step 1: 标准化工作目录与状态文件

1. 确定 `{slug}`，进入 `.autobizdevops/features/{slug}/`
2. 若尚未执行 workspace 初始化，先执行 `python hooks/init_workspace.py .`
3. 读取仓库构建配置、流水线配置、已有流程产物和用户输入，整理 CI/CD 所需上下文
4. 使用统一脚本更新 `.autobizdevops/state.json` 中对应 Feature 为 `cicd_in_progress`。写 `CI/CD（来源: Dev 交接）`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint cicd_in_progress --stage "CI/CD（来源: 用户直供）" --allow-create
```

### Step 2: 生成交付文档

1. 生成 `{工作目录}/CICD_CHECKLIST.md`
2. 生成 `{工作目录}/PR_BODY.md`
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
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint cicd_done
```

### Step 6: 是否再次执行

1. 需要再次触发流水线或重新整理清单时，必须先询问用户
2. 未经用户同意，不能擅自重跑
