# LA6407 数据库规范

## 本地数据库

- 涉及数据库的操作与验证必须连接本地 MySQL 完成。
- 本地连接信息:
  - Host: `127.0.0.1`
  - Port: `3306`
  - Username: `root`
  - Password: `123456`
  - Database: `pipm`
- JDBC URL 示例: `jdbc:mysql://127.0.0.1:3306/pipm`
- 禁止将生产库连接信息写入仓库。

## 连接与验证方式

- 启动后端服务前，必须先确认本地 MySQL `pipm` 库可连接。
- 优先使用本机已安装的 MySQL CLI: `mysql -h127.0.0.1 -P3306 -uroot -p123456 pipm`
- 如果 Windows / PowerShell 提示 `mysql` 无法识别，说明 `mysql.exe` 未安装或未加入 `PATH`，不要反复执行该命令。
- CLI 不可用时，使用 Python `pymysql` 连接本地库完成查询和验证；如果缺少依赖，可安装 `pymysql` 后再执行。
- 表清单查询示例:

```python
import pymysql

conn = pymysql.connect(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="123456",
    database="pipm",
    charset="utf8mb4",
)
try:
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        for (table_name,) in cursor.fetchall():
            print(table_name)
finally:
    conn.close()
```

## 命名规范

- 表名: 小写字母 + 下划线分隔，使用业务可读名称。
- 字段名: 小写字母 + 下划线分隔，避免数据库保留字。
- 主键: 每张表必须有主键，优先沿用现有项目主键策略。
- 普通索引: `idx_表名_字段名`。
- 唯一索引: `uk_表名_字段名`。

## 设计规范

- 字段类型选择合适的最小类型。
- 金额类数据使用 `DECIMAL`，避免滥用 `TEXT`、`BLOB`。
- 字符集统一使用 `utf8mb4`，排序规则按现网数据库约定保持一致。
- 外键约束根据性能与维护需求酌情使用，须在文档或注释中明确表间关系。
- 新增逻辑删除字段前，先确认现有表设计习惯。

## SQL 与 MyBatis 规范

- 禁止 `SELECT *`，查询须显式列出所需字段。
- 批量插入或更新单次控制在合理范围内，避免大事务导致锁表。
- 列表查询必须分页，沿用项目内已有 PageHelper 用法。
- 关联数据优先使用一次性查询、批量查询或明确的结果映射方案，避免 N+1。
- 并发修改场景须显式说明锁策略或幂等策略。

## 脚本落位

- 数据库脚本统一放在 `{project_root}/pipm-service/pipm-db/` 下，按业务目录或版本目录维护。
- 当前仓库尚未统一脚本模板时，新脚本仍须落在 `{project_root}/pipm-service/pipm-db/`，并保持现有命名风格可读、可追溯。

## 迁移规范

- 数据库结构变更须通过脚本管理，禁止手工直连生产库修改表结构。
- 脚本须保证幂等性或明确执行前提。
- 涉及存量数据修复、索引变更、字段下线时，须在 feature 文档中说明执行顺序与兼容策略。
