# LA6407 前端操作契约

## 项目结构

- 前端根目录: `{workspace}/ArchFront/`
- 源码目录: `{workspace}/ArchFront/src/`
- 页面视图: `{workspace}/ArchFront/src/views/`
- 公共组件: `{workspace}/ArchFront/src/components/`
- 路由配置: `{workspace}/ArchFront/src/router/`
- 状态管理: `{workspace}/ArchFront/src/store/`
- API 服务: `{workspace}/ArchFront/src/apis/`
- 静态资源: `{workspace}/ArchFront/src/assets/`、`{workspace}/ArchFront/src/static/`

## 技术栈

- Vue 2
- Vue CLI
- Vuex
- Vue Router 3
- Element UI
- Axios 现有封装

## 必读文档

- `{PLUGIN_DIR}/sys/LA01/references/FRONT_ARCHITECTURE.md`

## 前端编码规则

- 单文件组件按 `<template>` -> `<script>` -> `<style>` 顺序组织。
- 新增组件命名沿用所在目录既有风格。
- 所有 `props` 须显式声明类型、默认值与必要性。
- 可复用逻辑优先放入现有 `utils/`、`plugins/`、`directive/` 或已有 mixin 位置。
- 所有后端接口调用须收敛到 `{workspace}/ArchFront/src/apis/`。
- 页面组件不得直接写裸 `axios` 请求，不得自行拼接未记录的后端路径。
- 全局状态使用 Vuex 管理，局部状态留在组件内部。
- 路由集中维护在 `{workspace}/ArchFront/src/router/`，新增页面优先保持现有路由组织方式。
- 样式优先放在组件内或主题文件中，避免污染全局样式。
