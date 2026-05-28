# LA6407 前端操作契约

## 必读文档

- `{project_root}/LA6407/references/FRONT_ARCHITECTURE.md`

## 常见命令

- 安装依赖: `cd {project_root}/pipm-web && npm install`
- 启动前端服务: `cd {project_root}/pipm-web && npm run serve`
- 构建前端: `cd {project_root}/pipm-web && npm run build`
- 运行 lint: `cd {project_root}/pipm-web && npm run lint`

## 技术栈

- Vue 2
- Vue CLI
- Vuex
- Vue Router 3
- Element UI
- Axios 现有封装

## 本地启动规则

- 前端本地服务端口固定为 `8080`。
- 前端 dev server 代理 `/pipm` 到后端服务；本地联调时后端应运行在 `8090`。
- 如果 `node_modules/` 不存在，先执行 `npm install`，再执行 `npm run serve`。
- 前端启动是长运行进程，agent 应后台运行并轮询日志或端口 `8080`，不要等待命令自然退出。
- agent 启动前端后，必须记录后台任务 ID 和端口 `8080` 对应 PID。
- 停止前端优先停止 agent 创建的后台任务；如需按端口停止，在 Windows PowerShell 中先执行 `netstat -ano | findstr :8080` 获取 PID，再执行 `taskkill /PID <pid> /T /F`。
- 如果 `taskkill` 提示权限不足，说明当前 agent 无权结束该进程，必须提示用户关闭启动窗口，或在任务管理器中结束对应 `node.exe`。

## 本地 E2E 免鉴权访问规则

- 访问受保护前端页面前，后端必须已按 `{project_root}/LA6407/BACKEND_AGENTS.md` 使用 `local` profile 启动。
- Playwright 或无痕窗口首次访问目标页面时，在 URL query 追加 `localYstId=276882`。
- 如果目标 URL 没有 query，使用 `?localYstId=276882`；如果已有 query，使用 `&localYstId=276882`。
- Playwright 也可以先在 browser context 中设置 cookie `PIPM_LOCAL_YSTID=276882`，再访问目标页面。
- 页面访问后必须验证鉴权已生效，例如目标页面可见、关键元素可见、页面内接口不再返回认证重定向，或业务 API 返回非认证失败结果。
- E2E 报告和运行日志必须记录使用了 query、cookie 或其他方式完成免鉴权，以及页面/API 成功访问的证据。

## 前端编码规则

- 单文件组件按 `<template>` -> `<script>` -> `<style>` 顺序组织。
- 新增组件命名沿用所在目录既有风格。
- 所有 `props` 须显式声明类型、默认值与必要性。
- 可复用逻辑优先放入现有 `utils/`、`plugins/`、`directive/` 或已有 mixin 位置。
- 所有后端接口调用须收敛到 `{project_root}/pipm-web/src/apis/`。
- 页面组件不得直接写裸 `axios` 请求，不得自行拼接未记录的后端路径。
- 全局状态使用 Vuex 管理，局部状态留在组件内部。
- 路由集中维护在 `{project_root}/pipm-web/src/router/`，新增页面优先保持现有路由组织方式。
- 样式优先放在组件内或主题文件中，避免污染全局样式。
