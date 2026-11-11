# HTML 资源处理

原始资源是视觉证据。未知 SVG、图片、头像、Logo 默认保留；资源保留率没有上限。文件名只能提供线索，不能决定替换。图标/图表选择遵循 frontend-policy.md。

只读盘点可在 TASK start 前；映射写入和资源复制必须在当前 TASK start 成功后，project-root 为绑定的业务工作区。

绝对定位路线的 prepare_html_analysis.py 自动执行 process_assets.py。标准路线按需执行一次同一脚本（无本地资源则无需额外执行）：

```bash
python -X utf8 <frontend-html>/with-absolute-html/scripts/process_assets.py --project-root <CODE_WORKSPACE> --task-stem <task> --html-file <HTML> --output-dir <CODE_WORKSPACE>/.frontend/html-analysis
```

多片段重复 --html-file。prepare_html_analysis.py 也支持 --public-root，并传递给资源处理；托管参考截图复用映射中同一个 publicRoot。脚本支持 HTML/srcset、CSS url/import、SVG href；每个引用按原文档位置解析，复制目标用摘要避免重名。根相对 URL（/assets/...）必须提供真实 --public-root，不得猜操作系统根目录。URL fragment/query 保留在映射 urlSuffix 中。

读取 `<task>-assets-mapping.json`：

- schemaVersion=2；assetsReady=true 才表示资源复制完整。
- copyToProject 使用已校验 targetPath，引用按真实项目 alias/import/public 规则生成。
- unchanged 的远程/data URL 保留；远程不可访问时说明差异，不能凭空换图。
- 未识别资源维持原样。candidateReplacement 仅建议；采用替换必须有真实项目组件、视觉相似性证据，记录理由。
- copyStatus=failed 必须修复真实路径/复制问题，不能用占位符掩盖。分析脚本失败可以回到原稿，但不能绕过已知资源丢失。
- CSS/脚本等被复制并不表示已完成工程接入；核对其内部 URL/依赖并按项目方式导入。原始脚本不要直接作为业务代码执行。

复用当前输入对应的映射，不重复运行资源分析。映射旧于输入或为 v1 时重建；只修改当前任务页面，避免批量重写既有资源。
