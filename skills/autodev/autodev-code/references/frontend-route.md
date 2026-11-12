# 当前前端 TASK 的 Route 与统一回检

仅 taskContract.uiRequired=true 执行。业务来自 Specs，视觉来自可读 HTML；没有高保真、仅部分页面有稿或稿件不可读时正常 spec-driven-ui，记录缺口，不阻止写入，也不声称已对照稿件还原。

1. 执行 resolve_frontend_html_route.py --workspace <artifact> --feature <feature> --task-id <TASK> --start-route-run --json。读取返回 frontendPolicyPath 一次及对应 Route SKILL；standard/absolute 读完登记 route-skill-read-complete，创建 route todos。spec-driven-ui 不读 HTML parser。
2. 按 Route 完成只读准备，TASK start 成功后才运行分析、复制 assets 或写源码；按顺序转交 parser 并登记 parser-read。原稿优先，分析脚本仅辅助。
3. Route 实现清单完成登记 route-todos-completed（HTML 路线），随后执行下述统一回检。不能仅登记 reviewStatus 代替回检。

## 统一回检（初次实现与返修共用）

```bash
python -X utf8 <pluginPath>/hooks/frontend_verification.py --workspace <artifact> --feature <feature> --task-id <TASK> --run-id <真实TASK runId> --code-workspace <业务工作区> --parallel-run-id <真实parallelRunId> --lease-token <真实token> --workspace-ref <真实ref> --target <当前页面/相关源码相对目录>
```

非并行任务省略 parallel-run-id/lease-token；target 可重复，务必包含本任务页面及相关组件，路径为工作区内相对路径。工具绑定当前源码/输入/检查器，未变化时复用静态回检和已有截图，不重复分析。报告在返回 reportPath；只在有 findings/未验证时读取完整报告。读取 html-post-review.md 做必要专业复核，不把静态脚本当最终视觉判断。

每个 TASK/run 使用一份实现回检报告。首次 finish-implementation 核对当前源码并登记 Evidence，重复 finish 复用已登记的结果。

- failed：读取 static.reports 的 must-fix 定向修复并重跑，最多两轮。执行失败如实报告，不能假写 passed。
- passed/has-suggestions：可 finish-implementation；建议项透明保留。缺少多模态能力、浏览器环境或视觉核对结果时，静态与资产检查通过即可按 has-suggestions 继续，fidelityStatus=unverified，advisories 记录视觉缺口并进入最终报告。

可选 `--check-script typecheck`（或 build/check:types）：仅必要时执行一个项目已配置的简单编译脚本，同一源码和脚本结果复用，不默认 build+lint+test 全跑。

页面检查直接使用平台内置 `browser_*` 工具。按当前实际暴露的工具调用 `browser_navigate`、`browser_snapshot`、`browser_evaluate` 等，访问当前 Batch 工作树对应的页面；原稿使用平台支持的本地文件地址或已有预览地址。纯文本模型使用 DOM、文本和样式信息做辅助检查。项目没有可访问的页面服务时，记录该缺口并继续，不把平台浏览器可用等同于业务页面已启动。

只有模型支持图像输入且平台实际提供可用截图工具时，才截取当前页面及原稿。平台返回的本地 PNG 通过 `--actual-screenshot <png> --reference-screenshot <png>` 登记，多份原稿重复 reference-screenshot。截图必须属于当前 Batch 源码；使用工具返回的真实路径。检查截图后写临时 JSON：`{"bindingDigest":"<报告 binding 对象的 digest；优先使用 capture.bindingDigest>","decision":"passed|failed","findings":[...]}`，以 `--visual-report <json>` 再执行同一命令。findings 中 must-fix 必须判失败，不能以“文件存在”替代视觉判断。平台截图工具未暴露、被禁用或无法提供本地 PNG 时，保留视觉未验证 advisory 并继续。

本平台已提供浏览器能力，不搜索项目、插件或全局的 Playwright 包、浏览器缓存，不执行 npx/npm 安装，也不为本流程启动插件的独立 `--capture` 截图链路。工具不可用时原样记录原因，按现有降级规则收口。

无高保真稿的页面 fidelityStatus=not-applicable；按需做运行检查。有可读稿件且模型具备图像能力、已有浏览器环境时，使用当前页面及原稿截图核对视觉。缺少条件时直接保留视觉未验证 advisory 并继续 finish，后续汇总展示该缺口。截图环境启动失败会保留具体原因；已发现的视觉缺陷、静态错误和资产错误仍阻塞完成。

TASK 实现或生产返修期间修改了相关源码，需在 finish 前完成当前版本的回检。封存后，前后端统一使用封存提交及其 Review 证据交付。候选推广按 Git 提交比较本 Batch 改过的文件，存在内容差异时 Workflow 只读复核这些文件。原 TASK 报告继续保存在原路径。
