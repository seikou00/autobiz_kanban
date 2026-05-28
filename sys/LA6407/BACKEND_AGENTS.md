# LA6407 后端操作契约

## 必读文档

- `{project_root}/references/BACKEND_ARCHITECTURE.md`

## 按需读取

- 数据库任务: `{project_root}/references/BACKEND_DB_GUIDE.md`

## 常见命令

- 根目录编译: `cd {project_root}/pipm-service && mvn clean compile`
- 根目录打包: `cd {project_root}/pipm-service && mvn clean package`
- 跳过测试打包: `cd {project_root}/pipm-service && mvn clean package -DskipTests`
- 运行测试: `cd {project_root}/pipm-service && mvn test`
- 安装本地模块依赖: `cd {project_root}/pipm-service && mvn -U clean install -DskipTests`
- 启动后端服务: `cd {project_root}/pipm-service && mvn -pl pipm-service-base spring-boot:run -Dspring-boot.run.profiles=local`

## 本地启动规则

- 后端本地 profile 使用 `local`，HTTP 端口为 `8090`，管理端口为 `28280`。
- 后端是 Maven 多模块工程，必须从 `{project_root}/pipm-service/` 根目录启动，不能在 `pipm-service-base/` 子目录直接启动。
- 首次启动或本地依赖缺失时，先执行 `mvn -U clean install -DskipTests`，再执行启动命令。
- 启动命令不要使用 `-am`；`-am` 会让 `spring-boot:run` 先作用到父工程 `pipm`，导致父工程找不到 main class。
- 启动入口模块为 `pipm-service-base`，启动类为 `com.cmb.pipm.base.PipmBaseApp`。
- 启动任务不要先搜索 K8s `BuildScript` / `deploy_*.yaml`，这些是部署配置，不是本地启动入口。
- 启动任务不要把根目录全量编译当作前置阻塞步骤；优先直接执行后端启动命令，启动失败后再按错误信息做编译或依赖诊断。
- 后端启动是长运行进程，agent 应后台运行并轮询日志或端口 `8090`，不要等待命令自然退出。
- agent 启动后端后，必须记录后台任务 ID 和端口 `8090` 对应 PID。
- 停止后端优先停止 agent 创建的后台任务；如需按端口停止，在 Windows PowerShell 中先执行 `netstat -ano | findstr :8090` 获取 PID，再执行 `taskkill /PID <pid> /T /F`。
- 如果 `taskkill` 提示权限不足，说明当前 agent 无权结束该进程，必须提示用户关闭启动窗口，或在任务管理器中结束对应 `java.exe`。

## 本地启动前置

- 涉及数据库读写、脚本执行或数据验证时，必须使用 `{project_root}/{project_root}references/BACKEND_DB_GUIDE.md` 中定义的本地 MySQL `pipm` 库。
- 后端启动前确认本地 MySQL 可连接，且目标数据库为 `pipm`。

## 本地 E2E 免鉴权规则

- 免鉴权仅适用于后端以 `local` profile 启动的本地验证或 E2E。
- 本地白名单用户: `ystId=276882`。
- 直接验证后端受保护接口时，请求头使用 `X-PIPM-LOCAL-YSTID: 276882`。
- 需要获取前端可复用登录态时，先调用 `POST /pipm/local-auth/login?ystId=276882`，再使用响应中的本地 token 作为后续 `Authorization`。
- 如果接口响应仍为认证重定向、401/403、或无法证明请求已进入业务逻辑，E2E 必须记录为鉴权失败或未确认，不能写成通过。
- 不得在 dev、st、uat、prod 环境使用本地免鉴权方式。

## 后端编码规则

- Controller 位于 `{project_root}/pipm-service/pipm-service-base/src/main/java/com/cmb/pipm/base/controller/**`。
- Controller 只负责 HTTP 映射、参数接收、调用 service、返回统一响应模型，不编排复杂业务。
- Service 位于 `{project_root}/pipm-service/pipm-service-base/src/main/java/com/cmb/pipm/base/service/**`，负责业务编排、事务控制和领域规则。
- Mapper 接口与数据库对象位于 `{project_root}/pipm-service/pipm-common-db/src/main/java/**`。
- MyBatis XML 位于 `{project_root}/pipm-service/pipm-common-db/src/main/resources/mapper/**`。
- 对外接口统一沿用当前项目 `R<T>` 或模块既有统一响应模型。
- 新增接口路径须沿用现有 `pipm/...` 风格。
- 涉及数据库时，脚本落位、表字段命名和 SQL 写法遵循 `{project_root}/{project_root}references/BACKEND_DB_GUIDE.md`。
