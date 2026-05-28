# LA6407 PIP 项目补充约束

## 项目边界
- 后端入口: `{workspace}/ArchBackend/`
- 前端入口: `{workspace}/ArchFront/`

## 文档地图

- `{PLUGIN_DIR}/sys/LA01/AGENTS.md`: LA6407 项目结构和加载入口。
- `{PLUGIN_DIR}/sys/LA01/BACKEND_AGENTS.md`: LA6407 后端模块结构、命令和后端特有约束。
- `{PLUGIN_DIR}/sys/LA01/FRONT_AGENTS.md`: LA6407 前端模块结构、技术栈和前端特有约束。
- `{PLUGIN_DIR}/sys/LA01/references/BACKEND_ARCHITECTURE.md`: LA6407 后端技术栈、分层结构与模块职责。
- `{PLUGIN_DIR}/sys/LA01/references/BACKEND_DB_GUIDE.md`: LA6407 数据库脚本落位、命名和 SQL 习惯。
- `{PLUGIN_DIR}/sys/LA01/references/FRONT_ARCHITECTURE.md`: LA6407 前端目录职责和组件组织习惯。

## 按任务类型加载

- 涉及后端时读取: `{PLUGIN_DIR}/sys/LA01/BACKEND_AGENTS.md`
- 涉及前端时读取: `{PLUGIN_DIR}/sys/LA01/FRONT_AGENTS.md`
- 涉及数据库落位、字段命名或 SQL 写法时读取: `{PLUGIN_DIR}/sys/LA01/references/BACKEND_DB_GUIDE.md`

## 通用项目习惯

- 语义化命名: 变量、函数、模块命名须准确反映用途与职责。
- 最小变更: 仅修改与当前任务直接相关的代码，避免顺带重构。
- 可读性优先: 通过命名和结构自解释，必要注释说明原因而不是重复代码含义。
- 避免魔法值: 硬编码数值、字符串须提取为具名常量或配置项。
- 显式错误处理: 异常情况须明确处理，对外暴露清晰错误信息。
- 模块边界清晰: 跨模块调用须通过明确定义的接口或契约进行。
- 消除重复: 相同或高度相似逻辑须提取到合适的已有公共位置。
