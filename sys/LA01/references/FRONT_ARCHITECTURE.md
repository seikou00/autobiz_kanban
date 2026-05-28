# LA6407 前端架构约束

## 技术栈

- Vue 2.x
- Element UI 2.x
- Vue Router 3.x
- Vuex 3.x
- Axios 现有封装
- Less / SCSS 现有配置

## 目录结构

当前前端目录以 `{workspace}/ArchFront/src/` 为准。

```
src/
├── apis/              # API 接口定义
├── assets/            # 静态资源
├── components/        # 公共组件
├── directive/         # 自定义指令
├── layout/            # 布局组件
├── plugins/           # Vue 插件
├── router/            # 路由配置
├── static/            # 业务静态文件
├── store/             # Vuex 状态管理
├── theme/             # 主题样式
├── utils/             # 工具函数
├── views/             # 页面视图
├── App.vue
├── main.js
└── permission.js
```

## 目录职责

- `{workspace}/ArchFront/src/apis/`: 统一维护接口请求方法，页面组件不得直接拼接后端地址。
- `{workspace}/ArchFront/src/router/`: 统一维护路由表和导航入口。
- `{workspace}/ArchFront/src/store/`: 统一维护全局状态。
- `{workspace}/ArchFront/src/views/`: 页面级组件，按业务域划分目录。
- `{workspace}/ArchFront/src/components/`: 跨页面复用组件。
- `{workspace}/ArchFront/src/utils/`、`plugins/`、`directive/`: 放置通用能力，不与单页业务逻辑混用。

## 命名与组织

- 组件文件沿用现有项目命名风格。
- API 文件按业务模块命名。
- 工具文件使用语义化命名，体现用途而非实现细节。
- 页面目录按业务域组织，不为抽象而抽象出无明确职责的公共目录。

## Vue 组件规范

- 单文件组件保持 `<template>`、`<script>`、`<style>` 顺序。
- `props`、`data`、`computed`、`watch`、`methods` 职责分明。
- 组件内复杂逻辑优先抽离到现有可复用位置，避免页面文件持续膨胀。
- 保持与现有 Vue 2 Options API 风格一致，除非本模块已有明确例外。

## API 与状态管理

- API 调用统一通过 `{workspace}/ArchFront/src/apis/` 和现有请求封装发起。
- 全局状态统一通过 Vuex 管理，禁止在不受控位置写共享状态。
- 接口字段、错误语义、分页参数不得脱离契约自行发明。
