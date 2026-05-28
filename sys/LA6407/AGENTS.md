# LA6407 PIP 项目补充约束

## 项目边界

- 业务代码根目录: `{project_root}`
- 后端入口: `{project_root}/pipm-service/`
- 前端入口: `{project_root}/pipm-web/`

## 文档地图

- `{project_root}/AGENTS.md`: LA6407 项目结构和加载入口。
- `{project_root}/BACKEND_AGENTS.md`: LA6407 后端模块结构、命令和后端特有约束。
- `{project_root}/FRONT_AGENTS.md`: LA6407 前端模块结构、技术栈和前端特有约束。
- `{project_root}/references/BACKEND_ARCHITECTURE.md`: LA6407 后端技术栈、分层结构与模块职责。
- `{project_root}/references/BACKEND_DB_GUIDE.md`: LA6407 数据库连接、验证、脚本落位、命名和 SQL 习惯。
- `{project_root}/references/FRONT_ARCHITECTURE.md`: LA6407 前端目录职责和组件组织习惯。

## 按任务类型加载

- 涉及后端时读取: `{project_root}/BACKEND_AGENTS.md`
- 涉及前端时读取: `{project_root}/FRONT_AGENTS.md`
- 涉及启动前后端项目时同时读取: `{project_root}/BACKEND_AGENTS.md` 和 `{project_root}/FRONT_AGENTS.md`
- 涉及 E2E、Playwright、无痕窗口、本地接口验证或受保护页面访问时同时读取: `{project_root}/BACKEND_AGENTS.md` 和 `{project_root}/FRONT_AGENTS.md`
- 涉及数据库连接、操作、验证、脚本落位、字段命名或 SQL 写法时读取: `{project_root}/references/BACKEND_DB_GUIDE.md`

## 项目启动任务

- 启动前后端时，不要重新搜索启动类、`package.json`、K8s `BuildScript` 或 `deploy_*.yaml` 来推断命令。
- 后端使用 `{project_root}/BACKEND_AGENTS.md` 中的“启动后端服务”命令。
- 前端使用 `{project_root}/FRONT_AGENTS.md` 中的“启动前端服务”命令。
- 前后端启动都是长运行进程，agent 应后台运行并轮询端口或日志，不要等待命令自然退出。
- agent 为 E2E 或本地验证启动服务时，必须记录后台任务 ID、监听端口和 PID；验证结束后默认停止本次启动的服务，除非用户明确要求保持运行。
- 如果无法自动停止，必须告知用户具体端口、PID、进程名和手动关闭命令。
- 如果启动失败，只根据失败日志做针对性诊断，不要回到通用项目探索流程。

## E2E / 本地验证鉴权

- LA6407 本地 `local` 环境支持白名单用户免鉴权，E2E 访问受保护页面或接口前必须先按项目约束建立登录态。
- 后端接口鉴权绕过方式读取 `{project_root}/BACKEND_AGENTS.md` 的“本地 E2E 免鉴权规则”。
- 前端页面、Playwright 或无痕窗口访问方式读取 `{project_root}/FRONT_AGENTS.md` 的“本地 E2E 免鉴权访问规则”。
- E2E 报告必须记录实际采用的鉴权处理方式，以及页面加载、接口响应或关键元素可见等证据。
- 如果未完成鉴权处理或无法确认页面/API 已通过鉴权，不得把 E2E 结果写成通过。

## 本地数据库连接

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

## 通用项目习惯

- 语义化命名: 变量、函数、模块命名须准确反映用途与职责。
- 最小变更: 仅修改与当前任务直接相关的代码，避免顺带重构。
- 可读性优先: 通过命名和结构自解释，必要注释说明原因而不是重复代码含义。
- 避免魔法值: 硬编码数值、字符串须提取为具名常量或配置项。
- 显式错误处理: 异常情况须明确处理，对外暴露清晰错误信息。
- 模块边界清晰: 跨模块调用须通过明确定义的接口或契约进行。
- 消除重复: 相同或高度相似逻辑须提取到合适的已有公共位置。
