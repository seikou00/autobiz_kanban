# LA6407 后端架构约束

## 技术基线

- 语言: Java 8
- 框架: Spring Boot
- 构建工具: Maven 多模块工程
- 持久化: MyBatis + MyBatis XML
- 数据库: MySQL / TDSQL
- 公共依赖: ZA21 bee 体系、pagehelper、hutool 等现有基础组件

## 模块职责

- `{workspace}/ArchBackend/`: 主应用模块，承载 controller、业务 service、启动与资源配置。
- `{workspace}/ArchBackend/pipm-common-db/`: 数据库访问相关实现，包含 mapper 接口、XML、数据库配置与 DO。
- `{workspace}/ArchBackend/pipm-common-api/`: 外部服务调用、开放接口相关配置与共用模型。
- `{workspace}/ArchBackend/pipm-common-util/`: 通用工具、基础能力与共享配置。

## Controller 层

- 位置以 `{workspace}/ArchBackend/src/main/java/com/cmb/pipm/base/controller/**` 为准。
- 职责是 HTTP 请求映射、参数接收、调用 service、返回统一响应模型。
- 当前项目统一沿用 `R<T>` 作为响应模型，不自行发明新的外部响应包装格式。
- 接口路径以前缀 `pipm/...` 的现有 controller 定义为准，新增接口须与现有模块路径风格一致。

## Service 层

- 主要位于 `{workspace}/ArchBackend/src/main/java/com/cmb/pipm/base/service/**`。
- 职责是业务逻辑编排、事务控制、领域规则实现、第三方能力调用。
- Controller 不得直接下钻到 mapper 或 XML 层。

## 数据访问层

- Mapper 接口与数据库相关对象位于 `{workspace}/ArchBackend/pipm-common-db/src/main/java/**`。
- XML 映射文件位于 `{workspace}/ArchBackend/pipm-common-db/src/main/resources/mapper/**`。
- 数据访问逻辑继续沿用 MyBatis + XML，避免混入新的持久化范式。

## 响应与异常处理

- 对外接口统一返回 `R<T>` 或当前模块既有统一响应模型。
- 异常必须显式处理，不得吞错。
- 不得向外暴露 stack trace、SQL 细节或敏感配置。
- 新增错误码、错误信息或异常分支时，需同步检查前端调用方和 feature 契约。

## 配置管理

- 主应用配置位于 `{workspace}/ArchBackend/src/main/resources/`。
- 公共模块配置分别位于各模块 `src/main/resources/`。
- 新增配置必须按模块归属落位，避免把公共配置硬塞进业务模块。

## API 项目约定

- DTO、VO、返回字段变更后，必须同步检查前端 `src/apis/*.js` 与对应页面逻辑。
- 具体 API 契约产物与验证流程遵循 autobizdevops 通用规则。

## 禁止事项

- 不得在 Controller 中编排复杂业务逻辑。
- 不得绕过 service 直接从 Controller 操作 mapper。
- 不得在响应中暴露敏感信息。
- 不得让前端依赖未记录、未核对的接口字段。
