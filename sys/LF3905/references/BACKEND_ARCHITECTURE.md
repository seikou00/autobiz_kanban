# LF3905 后端架构约束

## 技术基线

- 语言: Java 8
- 框架: Spring Boot
- 构建工具: Maven
- 持久化: MyBatis + MyBatis XML
- 数据库: MySQL
- 公共依赖: ZA21 bee 体系、pagehelper、hutool 等现有基础组件

## 项目结构

- 业务代码根目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng`
- 后端源码目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng`
- 四层架构包（bc层）: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc`
- 资源配置目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\resources`
- 数据库脚本目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\resources\db`

## 四层架构说明

项目采用经典四层架构（DDD 分层），包结构如下：

```
bcdeventpmng/
├── bc/                      # 四层架构-业务组件层
│   ├── adapter/             # 适配层 - Controller、Schedule、外部接口适配
│   │   ├── web/             # Web Controller
│   │   ├── schedule/        # 定时任务
│   │   └── ...              # 其他适配器
│   ├── application/         # 应用层 - 应用服务、DTO、Assembler、Converter
│   │   ├── busevt/          # 业务事件相关
│   │   ├── buslst/          # 商机列表相关
│   │   ├── busopr/          # 商机操作相关
│   │   ├── dto/             # DTO 定义
│   │   ├── service/         # 应用服务
│   │   └── ...              # 其他应用服务
│   ├── domain/              # 领域层 - 领域实体、领域服务、仓储接口
│   │   ├── entity/          # 实体对象
│   │   ├── repository/      # 仓储接口
│   │   ├── valueobject/     # 值对象
│   │   └── ...              # 其他领域组件
│   ├── infrastructure/      # 基础设施层 - 仓储实现、数据库访问
│   │   ├── mapper/          # MyBatis Mapper 接口
│   │   ├── repository/      # 仓储实现类
│   │   └── ...              # 其他基础设施组件
│   └── ...                  # 其他业务包
├── common/                  # 公共模块
├── util/                    # 工具类
└── ...                      # 其他包
```

## 四层架构职责

### Adapter（适配层）

- 位置: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc\adapter\**`
- 职责: HTTP 请求映射、参数接收、调用应用服务、返回统一响应模型、定时任务调度
- 包含: Web Controller、Schedule 定时任务、消息消费者等外部适配入口

### Application（应用层）

- 位置: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc\application\**`
- 职责: 业务用例编排、事务控制、领域服务调用、DTO 转换
- 包含: 应用服务（AppService）、DTO、Assembler、Converter 等

### Domain（领域层）

- 位置: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc\domain\**`
- 职责: 领域实体、领域规则、领域服务、仓储接口定义
- 包含: 实体（Entity）、值对象（Value Object）、仓储接口（Repository 接口）

### Infrastructure（基础设施层）

- 位置: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc\infrastructure\**`
- 职责: 仓储实现、数据库访问、外部服务调用
- 包含: Mapper 接口实现、Repository 实现、数据库配置
- XML 映射文件位于: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\resources\mapper\**`

## 响应与异常处理

- 对外接口统一返回统一响应模型。
- 异常必须显式处理，不得吞错。
- 不得向外暴露 stack trace、SQL 细节或敏感配置。
- 新增错误码、错误信息或异常分支时，需同步检查调用方和 feature 契约。

## 配置管理

- 主应用配置位于 `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\resources\`。
- 新增配置必须按模块归属落位。

## 禁止事项

- 不得在 Adapter 层编排复杂业务逻辑。
- 不得绕过 Application 层直接从 Adapter 操作 Infrastructure 层。
- 不得在响应中暴露敏感信息。
- 不得让调用方依赖未记录、未核对的接口字段。