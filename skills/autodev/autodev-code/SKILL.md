---
name: autodev-code
description: 进行代码实现。
version: v1.2.0704
---

# /autodev-code — 代码执行


## 准入检查

先获取当前 checkpoint：

```bash
python "${pluginPath}/read_state_json.py" --feature "{feature}"
```


开始任何业务代码修改前，根据项目 manifest 生成模块编译清单 `.autobizdevops/modules_compile.json`：

```json
{
  "version": 1,
  "modules": [
    { "module": "root", "path": "/absolute/path/to/code/module", "compile_command": "mvn compile" }
  ]
}
```

识别规则：优先遵守已声明的构建方式；否则按项目 manifest 的单/多模块入口生成；`path` 用模块目录绝对路径，`compile_command` 以该目录为 cwd 执行（命令本身不要再写 `cd`）；无法确定时停止并询问用户，不得开始编码。

## 缺失产物处理
```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
```

## 执行协议

### 建立执行上下文与任务队列

**任务队列：** 把本轮变更拆成 2–5 个需求闭环任务，逐个推进，每个含 做什么 / 依据（指向对应 input 中的具体条目）/ 验证方法；队列与状态记录在完成摘要。
- 使用 `write_todos`，把这 2–5 个任务写成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败，并与"完成摘要"保持同步；每次只置一个任务为"进行中"，完成或失败后立即更新对应条目。

### 选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

### 执行单个任务

1. 任务状态置「进行中」，保留原内容（如启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。
3. 改代码前做有界探索定位真实文件与既有模式：只读plan.md和design.md、 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加注释。
6. 执行任务「验证方法」（缺失则基于 系统约束 / 项目脚本选最小可行验证并记回任务）。通过 → 状态「完成」+ 记录验证；失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。

### 全部任务完成后的验证
使用task工具，先从git cache中获取当前改动的代码，同时从两个维度进行检查：1.指定code-reviewer-autodev角色,检查代码是否完成需求与质量，2.指定code-simplifier-autodev角色，检查代码是否有冗余或不合理的地方。
如任一子代理返回有问题，则需要修复代码。
队列无「待做」「进行中」后，跑项目级验证（优先 系统约束 / Java/Maven 至少编译）。失败回到相关任务，不推进。通过后先回填领域词汇表锚点：会话工作区 `CONTEXT.md` 中锚点为「规划中」且本轮已落地的词条，回填为实际类/表/枚举与相对路径（协议见 `${pluginPath}/skills/references/domain-context.md`；无该文件或无「规划中」词条则跳过）。再推进 checkpoint：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件；会话工作区 `CONTEXT.md` 的领域词汇表锚点回填


为完成任务必须改队列未直接提到的业务文件时，先确认与各输入产物确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 必要验证通过；项目编译通过（code_done execute hook 另记模块编译结果，非阻断）。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
