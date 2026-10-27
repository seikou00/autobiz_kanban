---
name: autodev-utest
description: 固定 Batch 的测试生成、精确执行与有限修复，复用实现交接并保留真实 Evidence。
version: v2.0
---

# Batch UTest

只在 Code Workflow 的 test 子阶段运行，全局保持 code_in_progress。固定 Workflow 拥有跨 Batch 调度；同一 assignment 内顺序执行，不能另起全局串行队列或派发嵌套协调器。

## 准备与测试

1. 复用 Runtime 的 UTEST_ASSIGNMENT、engineeringContext brief、有效事实、实际实现交接和恢复位置。不重新打开 Plan 补字段，不从零探索工程。未知或失效部分才定向查证；已有适用测试/fixture 直接复用并补缺。
2. 当前前端 UTest 暂时禁用：prepare-test 记录 skipped、原因 frontend_utest_temporarily_disabled；不初始化环境、不生成测试、不编译、不记 PASS。后端保持实际测试。
3. 使用 `utest_runtime.py prepare` 的真实仓库/模块、preset、能力缺口和固化预算。环境信息由当前 Worktree 绑定，模型不重选仓库。多目标歧义只依据返回候选与 TASK 判断，无法判断时明确阻断。
4. 根据 testPoints、verificationIntent 与绑定 AC 编写真实行为断言；implementationPoints 仅用于定位。先用现有测试与最近相关示例，不做全仓覆盖分析。框架领域确有需要时使用 `render_spring_test_reference.py` / `render_frontend_test_reference.py` 读取对应 domain。
5. 测试文件落地后 `preflight --test-file <相对文件> -- <精确argv>`，原样执行返回 bashCommand。Bash 执行真实测试，Runtime 验证身份、期限、测试文件、argv、退出码、tee 状态和实际用例数，并登记 Evidence。登记成功不是测试通过；零/未知用例数不能 PASS。awaiting_record 只补登记原输出，不能重跑命令修复登记。

需要完整绑定参数和失败交接时读取 `reference/test-engineer-task-protocol.md`。只有缺测试环境时读 `reference/test-environment-profiles.md`，确有静态依赖问题才读 `reference/legacy-static-test-patterns.md`；不预读所有模板。

## 变更和失败边界

- 只修改当前 assignment 的测试、fixture/mock/helper 和测试配置。POM 的 test scope 依赖及必要 Surefire/Failsafe 可按语义 guard 修改，不重开生产 TASK；保留生产依赖、parent/BOM、profiles 和共享属性。生产代码回 Workflow 的 Rework。
- 依赖/环境诊断使用现有缓存。Maven setup 经 preflight-setup 返回的 Bash 命令执行；cached 直接复用。查类按已有 classpath 做 lookup-class/inspect-class，不递归扫描本地 Maven 仓库，不重复探测有效失败。
- test_bug 最多修一次，重跑原 target/argv；source_bug 必须有当前失败 Evidence 和 source-bug validator，交回一次受控生产修复后原命令续跑，不新增 Review。环境、权限或未知根因按 Runtime 的 deferred/阻断语义收口，不换 runner/target 洗绿。
- 预算以 Runtime utestPolicy 为准，准备/查询/修复都计入，恢复不重置；不在多处复制秒数。失败先读本次 evidenceLogPath，必要时读本 target 的完整日志段，不读插件源码找放行路径。
- 精确目标通过后，仅新增改动、失败或未覆盖影响才扩大验证。已有执行能证明当前编译时不额外重复编译；不能凭命令名称或历史 PASS 推定编译覆盖。

## 结果与收口

测试资产由 Runtime 封存（commit-stage utest，中文摘要描述实际改动），并记录真实 passed/deferred/timed_out。skipped/deferred 不等于 PASS，也不改变全局 checkpoint；是否进入候选由现有 gate 决定。

UNIT_TEST_RESULT.json、EVIDENCE 流和索引只由 writer/runner 维护；人类报告从这些结果派生，只补实际失败解释、未覆盖 testPoints 和 e2e_handoff，不生成空的七节报告或手填覆盖表。浏览器、多页面、真实网络路径交给 B-E2E。固定 Workflow 完成最终聚合，不重复执行已完成测试。
