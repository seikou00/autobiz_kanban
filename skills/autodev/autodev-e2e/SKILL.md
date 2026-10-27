---
name: autodev-e2e
description: 固定 Workflow 最终 B-E2E 的真实 Playwright 验证、质量检查与证据收口。
version: v2.0
---

# Final B-E2E

只在固定 Workflow 启动 V-E2E 的 e2e_test 后运行，全局保持 code_in_progress。
最终 aggregate 只核验证据，不再重复测试，不创建独立 Verify。

## 上下文与用例

读取 Runtime 当前候选、任务契约、deferredValidationIssues 和相关 Specs/Design。
沿本期行为读取命中的 SRC 原件/快照与项目 Playwright 配置，不重新读取所有历史
报告或已退役 Review 报告。来源约束有矛盾时核对原始材料，不只信实现摘要。

E2E_TEST_CASES.yaml 是唯一用例计划。首次按 `reference/testcase-generation.md`
生成覆盖全部本期场景和必须外部接口的用例；每个相关 SRC 必须有真实机械断言。
测试资产按 caseId 绑定，现有 spec/fixture/helper/Page Object 复用并补缺。

UI 的入口、locator、鉴权或数据未知时才按 `reference/test-playwright-script.md`
有头探索；已验证且仍有效的页面信息直接复用。探索、截图、静态代码存在不是 PASS。
副作用调用仅在测试/沙箱执行，生产只做用户明确授权的只读验证；缺安全环境或凭据
保留 BLOCKED/missing，snapshot_only 使用已有快照，不重复索取原件。

## 质量、执行和恢复

1. 用 e2e_result_writer.py init/add-case 建立用例，begin-round --kind initial 开轮。
2. 按 `reference/e2e-quality-gate.md` 进行质量扫描与具体 finding 裁定。扫描可复用
   相同完整输入版本，但新依赖、删除文件、目录或配置变化必须失效。
3. 每个 case 用 run_e2e_command.py run 执行精确 Playwright Test 命令和 --grep caseId。
   使用真实任务/spec/测试文件/入口/鉴权绑定；runner 生成报告、日志与 Evidence 链。
   不通过包脚本或探索 CLI 裁定 PASS。零用例、未知执行数、输出或哈希失效均不能 PASS。
4. pending 只用 run_e2e_command.py resume --run-id <原ID> 补登记，不重跑原命令。
   只读 Runtime 返回的当前诊断与恢复位置，不枚举全部历史日志。
5. 修复前 begin-round --kind repair，修复与扫描后 refresh-reuse 获取 affectedCaseIds。
   项目未配置已提交的只读不可变环境探针时默认 full，不把 URL/配置哈希当环境证明。
   探针契约按需见 `reference/execution.md`。仅 Runtime 明确列为可继承、原执行链有效且
   源码/共享配置/环境/用例绑定未变的独立用例可复用原 execution。test asset、fixture、
   鉴权或任何未知影响按 Runtime 的 affectedCaseIds/full 重新执行，不复制旧 PASS。
6. 用 derive-scenario-coverage、finalize、validate --gate 收口。finalize 依据当前执行
   或合法继承、质量门禁及真实 Evidence 派生结果；不存在手写 PASS 参数。

首次执行或需要精确 CLI 字段时读取 `reference/execution.md`，不预读插件源码。

## 失败与交接

按当前 trace/report、console/network 定位具体根因。test_bug 只修测试资产；
source_bug 交固定 Workflow 的生产修复；contract_gap 回 Specs/Design；环境、权限、
数据、flaky 和 unknown 保留真实分类与证据。最多三轮，不增加 retry、timeout 或
固定 sleep 洗绿。源码/共享依赖变化或影响不能证明局部时完整验证。

FAIL/BLOCKED 由固定 Workflow 保留诊断与验证 Worktree，按现有策略登记问题后进入
aggregate 和最终报告，不自行更新全局 checkpoint 或扩大到未授权外部操作。
E2E_REPORT.md 是派生人类视图；API 详情与上传由独立 artifact-sync 技能按用户请求执行。
