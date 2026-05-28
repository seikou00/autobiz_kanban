# LF3905 后端操作契约

## 项目结构

- 业务代码根目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng`
- 后端源码目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng`
- 四层架构包（bc层）: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc`
- 四层架构: adapter（适配层）, application（应用层）, domain（领域层）, infrastructure（基础设施层）
- 资源配置目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\resources`
- 数据库脚本目录: `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\resources\db`

## 必读文档

- `sys/LF3905/references/BACKEND_ARCHITECTURE.md`

## 按需读取

- 数据库任务: `sys/LF3905/references/BACKEND_DB_GUIDE.md`

## 常见命令

- 编译: `cd {project_root} && mvn clean compile`
- 打包: `cd {project_root} && mvn clean package`
- 跳过测试打包: `cd {project_root} && mvn clean package -DskipTests`
- 运行测试: `cd {project_root} && mvn test`
- 安装依赖: `cd {project_root} && mvn clean install -DskipTests`
- 启动后端服务: `cd {project_root} && mvn spring-boot:run -Dspring-boot.run.profiles=local`

## 本地启动规则

- 后端本地 profile 使用 `local`，HTTP 端口为 `8080`，管理端口为 `28280`。
- 后端是 Spring Boot 单模块工程，从根目录启动。
- 首次启动或本地依赖缺失时，先执行 `mvn clean install -DskipTests`，再执行启动命令。
- 启动任务不要先搜索 K8s `BuildScript` / `deploy_*.yaml`，这些是部署配置，不是本地启动入口。
- 启动任务不要把全量编译当作前置阻塞步骤；优先直接执行后端启动命令，启动失败后再按错误信息做编译或依赖诊断。
- 后端启动是长运行进程，agent 应后台运行并轮询日志或端口 `8080`，不要等待命令自然退出。
- agent 启动后端后，必须记录后台任务 ID 和端口 `8080` 对应 PID。
- 停止后端优先停止 agent 创建的后台任务；如需按端口停止，在 Windows PowerShell 中先执行 `netstat -ano | findstr :8080` 获取 PID，再执行 `taskkill /PID <pid> /T /F`。
- 如果 `taskkill` 提示权限不足，说明当前 agent 无权结束该进程，必须提示用户关闭启动窗口，或在任务管理器中结束对应 `java.exe`。

## 本地启动前置

- 涉及数据库读写、脚本执行或数据验证时，必须使用 `sys/LF3905/references/BACKEND_DB_GUIDE.md` 中定义的本地 MySQL 库。
- 后端启动前确认本地 MySQL 可连接，且目标数据库存在。

## 本地 E2E 免鉴权规则

- 免鉴权仅适用于后端以 `local` profile 启动的本地验证或 E2E。
- 本地白名单用户: `ystId=276882`。
- 直接验证后端受保护接口时，请求头使用 `X-LF3905-LOCAL-YSTID: 276882`。
- 需要获取登录态时，先调用 `POST /local-auth/login?ystId=276882`，再使用响应中的本地 token 作为后续 `Authorization`。
- 如果接口响应仍为认证重定向、401/403、或无法证明请求已进入业务逻辑，E2E 必须记录为鉴权失败或未确认，不能写成通过。
- 不得在 dev、st、uat、prod 环境使用本地免鉴权方式。

## 后端编码规则

- 四层架构位于 `D:\WorkProjects1\LF39.05_BCWplus\后台服务\企业经营\LF39_bcdeventpmng\src\main\java\com\cmb\dw\rtl\bcdeventpmng\bc\`:
  - Adapter（适配层）: `bc/adapter/**` - Controller、Schedule 等外部接口适配
  - Application（应用层）: `bc/application/**` - 应用服务、DTO、Assembler
  - Domain（领域层）: `bc/domain/**` - 领域实体、领域服务、仓储接口
  - Infrastructure（基础设施层）: `bc/infrastructure/**` - 仓储实现、数据库访问
- Controller 只负责 HTTP 映射、参数接收、调用 service、返回统一响应模型，不编排复杂业务。
- Service 位于 `bc/application/` 目录下，负责业务编排、事务控制和领域规则。
- Mapper 接口与数据库对象位于 `bc/infrastructure/` 目录下。
- MyBatis XML 位于 `src/main/resources/mapper/` 目录下。
- Entity 实体位于 `bc/domain/` 目录下。
- 对外接口统一沿用当前项目统一响应模型。
- 新增接口路径须沿用现有风格。
- 涉及数据库时，脚本落位、表字段命名和 SQL 写法遵循 `sys/LF3905/references/BACKEND_DB_GUIDE.md`。