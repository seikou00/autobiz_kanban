# LA6407 后端操作契约

## 项目结构

- 后端根目录: `{workspace}/ArchBackend/`

## 必读文档

- `{PLUGIN_DIR}/sys/LA01/references/BACKEND_ARCHITECTURE.md`

## 按需读取

- 数据库任务: `{PLUGIN_DIR}/sys/LA01/references/BACKEND_DB_GUIDE.md`

## 常见命令

- 编译: `mvn clean compile`
- 打包: `mvn clean package`
- 跳过测试打包: `mvn clean package -DskipTests`
- 运行测试: `mvn test`
- 运行後端应用: `mvn spring-boot:run`

## 后端编码规则

- Controller 位于 `{workspace}/ArchBackend/src/main/java/com/cmb/pipm/base/controller/**`。
- Controller 只负责 HTTP 映射、参数接收、调用 service、返回统一响应模型，不编排复杂业务。
- Service 位于 `{workspace}/ArchBackend/src/main/java/com/cmb/pipm/base/service/**`，负责业务编排、事务控制和领域规则。
- 对外接口统一沿用当前项目 `R<T>` 或模块既有统一响应模型。
- 新增接口路径须沿用现有 `pipm/...` 风格。
- 涉及数据库时，脚本落位、表字段命名和 SQL 写法遵循 `{PLUGIN_DIR}/sys/LA01/references/BACKEND_DB_GUIDE.md`。
