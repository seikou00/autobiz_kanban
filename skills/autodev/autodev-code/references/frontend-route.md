# 当前前端 TASK 的 Route

只有 `taskContract.uiRequired=true` 才执行。本协议位于 Batch 内，不是全局启动前置条件。UI 范围来自 UI_CONTEXT.json，行为来自 Specs，技术边界来自 Design/Plan；HTML 只提供布局和视觉，不覆盖行为契约。

1. 用 `resolve_frontend_html_route.py --workspace <artifact> --feature <feature> --task-id <TASK> --start-route-run --json` 解析当前作用域。required VIS 只能来自当前 capability 的归档，不用临时 HTML 替换。
2. 按 route 读取一个分支：absolute-html → `frontend-html/with-absolute-html/SKILL.md`；standard-html → `frontend-html/with-standard-html/SKILL.md`。读完后登记 `--mark route-skill-read-complete`；工具截断时续读。spec-driven-ui 不读 HTML/parser；none 禁止写前端。
3. 将命中 Route 的实际步骤放入可见清单，登记 `--mark route-todos-created`。只读准备后先完成 TASK start，再生成 `.frontend/html-analysis` / `.frontend/html-input`、处理资源及写源码。
4. 仅在 Route 步骤需要时读取它指定的 parser，登记 `--mark parser-read`；不是 Code 根技能直接加载全部 parser。
5. 完成清单登记 `--mark route-todos-completed`，执行 Route 的统一前端回检后登记 `--review-status passed|has-suggestions|skipped-by-user|failed`。缺少回检记录或 failed 不能假报完成。机器证据在 FRONTEND_ROUTE.json。

VIS 文件不可读可由 resolver 以 `requiredVisualSourceMissing` 明确降级 spec-driven-ui，缺口保留在回检结果。已归档输入的路径、内容摘要或 bundle 摘要不匹配必须修复，不静默降级。其他 capability 的缺失 VIS 不阻断本 TASK。

使用现有组件、图标、图表与依赖；不为实现页面新增安装或等待确认，必要时最小兼容实现并保留非阻断差异。HTML Route 完成后回到当前 TASK 的 finish、封存和 Batch 流水线。
