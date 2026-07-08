---
name: autodev-code
description: 进行代码实现。
version: v1.2.0703
---

## 缺失产物处理

```bash
python "{PLUGIN_ROOT}/hooks/inspect_skill_contract.py" autodev-code --feature "{FEATURE_ID}" --json
```


# /autodev-code — 代码执行

执行优先级：

1. 行为契约以 `specs/**/*.md` 为最高依据。
2. 技术边界以 `design.md` 与 `PLAN.md` 为实现依据。

## 准入检查

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 执行协议

### 建立执行上下文与任务队列

- 使用`write_todos`，把 `plan.md` 映射成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败；每次只置一个任务为"进行中"。`write_todos` 只反映任务进度。

###  选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目。

###  执行单个任务

1. 任务状态置「进行中」，保留原内容（启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。先依各 的读取方式确认行为契约与约束，再在其之上按现有代码模式做最小实现决策（读取方式优先于此默认）。
3. 改代码前做有界探索定位真实文件与既有模式：只读上游产物或 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 任务需要写 / 改测试时，遵循 `${pluginPath}/skills/references/test-quality.md`：站在 seam 上验证、期望值来自独立事实源（勿同义反复）、mock 只在系统边界。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。

###  全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（至少编译）。失败回到相关任务，不推进。

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "${feature}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件

为完成任务必须改队列未直接提到的业务文件，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 必要验证通过；项目编译通过。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**