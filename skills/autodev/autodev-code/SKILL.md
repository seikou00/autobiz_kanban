---
name: autodev-code
description: 进行代码实现。
---

# /autodev-code — 代码执行


## 准入检查

先读快照并捕获 `CHECKPOINT`：

```bash
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "{feature}")
```


开始任何业务代码修改前，根据 系统约束（如有） 与项目 manifest 生成模块编译清单 `.autobizdevops/modules_compile.json`：

```json
{
  "version": 1,
  "modules": [
    { "module": "root", "path": "/absolute/path/to/code/module", "compile_command": "mvn compile" }
  ]
}
```

识别规则：优先遵守 系统约束声明的构建方式；否则按项目 manifest 的单/多模块入口生成；`path` 用模块目录绝对路径，`compile_command` 以该目录为 cwd 执行（命令本身不要再写 `cd`）；无法确定时停止并询问用户，不得开始编码。

## 缺失产物处理
```bash
python "${pluginPath}/hooks/inspect_skill_contract.py" autodev-code --feature "${feature}" --plain
```

## 写入 checkpoint

开始编码前推进到 `code_in_progress`：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_in_progress
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "{feature}")
```

## 捕获代码审查基线

推进到 `code_in_progress` 后、修改任何业务文件前，生成当前 Feature 的正式审查基线：

```bash
python "${pluginPath}/skills/autodev/autodev-reviewer/scripts/capture_review_baseline.py" \
  --output "${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/review-baseline.json" \
  --module-manifest "${pluginWorkspace}/${projectDir}/.autobizdevops/modules_compile.json"
```

- 模块清单未覆盖某个受影响仓库时，为每个遗漏仓库追加 `--repo "<repository-id>=<path>"`。
- 首次进入 `code_in_progress` 时必须捕获；捕获失败立即停止，不得开始编码。
- 恢复 `code_in_progress` 时必须复用已有 `review-baseline.json`，不得重新捕获或覆盖。文件缺失时停止并说明无法重建修改前基线，不得继续推进 `code_done`。`legacy_scope` 只兼容升级前已经完成代码阶段的旧 Feature，不是新代码阶段的绕过入口。
- 基线中的 `scope_confidence: partial` 表示仓库在编码前已有脏文件。不得擅自把这些文件全部归入当前 Feature；完成摘要必须指出其中哪些路径与当前任务重叠，交由 reviewer 独立判定范围可信度。

## 执行协议

### 1. 建立执行上下文与任务队列

对每个 input 按其 `extract.focus` / `method` 抽取并记住关键信息。

**任务队列：** 把本轮变更拆成 2–5 个需求闭环任务，逐个推进，每个含 做什么 / 依据（指向对应 input 中的具体条目）/ 验证方法；队列与状态记录在完成摘要。
- 若当前运行模式支持 `write_todos`，必须把这 2–5 个任务写成可见任务清单，状态用 待做 / 进行中 / 完成 / 失败，并与"完成摘要"保持同步；每次只置一个任务为"进行中"，完成或失败后立即更新对应条目。若不支持 `write_todos`，仍按完成摘要维护同一份队列与状态，不得省略。`write_todos` 只反映任务进度，不替代 checkpoint 脚本与产物校验。
（依"方法优先"：若某 input 的 method 给了现成队列，按其指示执行、不再自行拆解。）

### 2. 选择下一个任务

跳过「完成」；优先恢复「进行中」；否则取第一个依赖已满足的「待做」；有「失败」先读原因，仅在用户要求修复时再处理。每次只做一个，完成后再进入下一个，并同步更新 `write_todos` 条目（如启用）。

### 3. 执行单个任务

1. 任务状态置「进行中」，保留原内容（如启用 `write_todos`，将该任务条目置为进行中）。
2. 读任务的 做什么 / 依据 / 验证方法。
3. 改代码前做有界探索定位真实文件与既有模式：只读plan.md和design.md、 `rg` 命中的相关文件；先识别项目分层、命名、错误处理、校验、日志、测试风格；形成简短修改映射（依据、拟改文件、复用模式、验证命令）再动手。修改映射还必须读取当前任务设计依据中的 `MOD-xx` Module Decisions 与 `DEP-xx` Dependency Decisions。真实入口/集成点仍无法定位则停止记录阻断，不要凭空造路径或猜测性抽象。

   **架构影响检查（条件触发）：**新增/修改公共 Interface，拆分/合并 Module 或移动 Seam，引入远程/第三方/I/O 依赖，业务规则将分散到多个调用方，或拟新增疑似纯转发层时，修改映射必须额外记录：

   ```text
   架构影响: 有
   Module / Interface / Seam / Dependency Category
   Adapter Strategy / Test Surface / Depth-Locality 判断
   对应设计: MOD-xx / DEP-xx
   ```

   普通局部修改不增加额外设计流程，明确记录 `架构影响: 无`。上述词汇只用于分析，不得为了统一词汇重命名项目现有代码。深模块检查只约束当前 Feature 的新增和修改，不授权顺手重构无关历史代码。
4. 实现并自检：
   - 不得为通过验证削弱校验、安全、日志、错误处理。
   - 最小 patch：观察局部风格保持一致，不重排、不格式化无关代码；完成前查本轮 diff，无关格式变化先还原。
   - 不得无理由扩大公共 Interface；不得新增纯转发 Module。新增 Module 必须通过删除测试，即删除后复杂度会回流到多个调用方。
   - 不得为测试暴露内部 Seam；没有真实替换需求时不得创建猜测性 Port/Adapter。
   - 业务规则、校验和错误转换集中在已确认 Module 内，不分散到调用方；依赖与 Adapter 接线遵守对应 `DEP-xx`。
5. 补必要注释：重要业务逻辑、非显然分支、边界、权限/租户/审计/幂等/状态流说明"为什么"；新增/改的 PO/DTO/Entity/VO 按既有风格补注释；不给自解释代码加噪音注释。
6. 执行任务「验证方法」（缺失则基于 系统约束 / 项目脚本选最小可行验证并记回任务）。通过 → 状态「完成」+ 记录验证；失败 → 代码问题就继续最小修复重跑，环境/依赖/需求不清/契约冲突则停止、状态「失败」、记原因与建议回流阶段。

> 一致性：任务的依据在对应 input 里找不到，或上游有影响本任务的「待确认」项 → 停止并回流。（逐条引用解析的确定性校验拟由上游 traceability validator 承担，见后续轨道；本阶段暂为人工判断。）

> 设计阶段边界：Code 阶段只落实已确认设计，不执行 Design It Twice。外部可观察行为缺失或冲突 → 回流 `/autodev-specs`；Interface、Module、Seam、依赖类别或 Adapter 策略未确认/出现多个合理方案 → 回流 `/autodev-plan`；设计已确认但真实文件落点、调用链或接线细节仍无法确定 → 回流 `/autodev-detail-design`。不得在编码过程中自行补成新的已确认设计。

### 4. 全部任务完成后的验证

队列无「待做」「进行中」后，跑项目级验证（优先 系统约束 / Java/Maven 至少编译）。失败回到相关任务，不推进。通过后：

```bash
python "${pluginPath}/hooks/update_checkpoint.py" --checkpoint code_done
CHECKPOINT=$(python "${pluginPath}/read_state_json.py" --feature "{feature}")
```

## 写入边界

允许：与当前任务需求闭环直接相关的业务代码/测试/配置；能追溯到任务依据与队列的新增文件


为完成任务必须改队列未直接提到的业务文件时，先确认与各输入产物确立的依据一致，再把文件与原因记入验证证据或完成/失败摘要，不要悄悄扩大范围。

完成摘要必须记录本轮 `MOD-xx` / `DEP-xx` 的实现结果、设计偏差和对应验证证据；未触发架构影响检查时明确记录「架构影响：无」。

## 完成条件

- 队列所有任务「完成」；有「失败」则不算完成、不得推进 `code_done`，须说明阻断与建议回流阶段。
- 未无理由扩大公共 Interface，未新增纯转发 Module，未为测试暴露内部 Seam，且依赖接线符合已确认 `DEP-xx`；不适用时在完成摘要明确说明。
- 必要验证通过；项目编译通过（code_done execute hook 另记模块编译结果，非阻断）。
- 刷新后的 `CHECKPOINT` 为 `code_done`。

**Skill 完成。**
提醒用户：请回到特性面板新开新对话。
如果用户仍在当前对话输入“继续”“下一步”等续办意图，必须读取并遵循 `${pluginPath}/skills/references/ui-continuation-guide.md`；当前技能尚未完成时不得使用该引导。
