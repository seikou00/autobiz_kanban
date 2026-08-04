# FEATURE_API_DETAIL.md 生成模板

用于 `/autodev-verify` 阶段生成 `${pluginWorkspace}/${projectDir}/.autobizdevops/features/${feature}/FEATURE_API_DETAIL.md`。

只在当前 Feature 的实际代码改动涉及新增或修改接口时生成。没有接口新增或修改时，不生成该文件。

## 生成约束

1. 以实际代码为依据，只记录当前 Feature 新增或修改的接口。
2. 不得根据 PRD、proposal、specs、design 或 PLAN 编造接口字段、SQL、错误码、枚举或内部逻辑。
3. 接口入口、DTO / VO、Service 实现、Mapper / Repository、错误码 / 枚举都要尽量从代码中定位。
4. 无法从代码确认的信息，写“代码中未确认”；已经检索但未发现的内容，写“代码中未发现……”。
5. 入参、出参遇到复杂类型必须展开字段，不能只写 `List<XxxVO>`、`XxxDTO`、`Result<XxxVO>` 这类类型名。
6. 错误码、枚举值、数据源、SQL 的写法必须保持统一。没有内容时也保留对应结构，不要一处写表格、一处只写“无”。
7. 接口整体逻辑按代码执行顺序写步骤。没有找到实现类时，不要推断实现类内部逻辑。
8. 涉及 SQL 时，必须贴出代码中的具体 SQL / 动态 SQL / 查询构造片段。只写“具体 SQL 位于 xxxMapper.xml”不符合要求。

## 复杂字段展开规则

- `List<XxxVO>`：出参说明中先写集合字段，再继续写 `字段名[].子字段`。
- `Page<XxxVO>` / 分页包装：写分页字段，再展开 `records[].子字段` 或项目实际字段名。
- `Result<XxxVO>` / 统一返回包装：写 `code`、`message`、`data` 等包装字段，再展开 `data.子字段`。
- `Map<String, XxxVO>`：写 map 字段含义，再展开 `字段名.{key}.子字段`。
- 文件流返回：说明文件类型、文件名来源、Content-Type、字段或表头。
- 如果复杂类型源码确实找不到，保留类型名，并在说明中写“代码中未确认该类型字段明细”。

## 未确认和未发现写法

- 未确认：代码依据不足，无法判断，写“代码中未确认……”。
- 未发现：已经看了相关代码，但没有对应内容，写“代码中未发现……”。
- 没有接口专用错误码：写“未发现接口专用错误码；异常按项目统一异常处理”，并给出异常处理或返回包装的代码依据。
- 没有枚举字段：在枚举表格中写一行“未发现枚举字段”，不要单独写“无”。
- 不涉及 SQL：写“SQL：接口不涉及 SQL。”。
- 不涉及数据源：写“数据源：接口不涉及数据库表或外部数据源。”。

## SQL 写法要求

- MyBatis XML：贴出对应 `<select>` / `<insert>` / `<update>` / `<delete>` 片段。动态 SQL 要保留 `<where>`、`<if>`、`<choose>`、`<foreach>` 等关键标签。
- 注解 SQL：贴出 `@Select` / `@Update` / `@Insert` / `@Delete` 中的 SQL 字符串。
- QueryWrapper / Criteria / JPA：没有原生 SQL 时，贴出查询构造代码片段，并说明“代码中未发现原生 SQL”。
- SQL 很长时，至少保留完整的 SELECT/FROM/JOIN/WHERE/GROUP BY/ORDER BY 结构和动态条件；确需省略非核心字段时，用注释说明省略内容，不能只写文件路径。
- 没有找到 SQL 片段时，写“代码中未确认 SQL 片段”，并列出已经检查过的 Mapper / Repository / Service 位置。

## 文档模板

模板里的示例和兜底写法用于说明格式。生成最终文档时，不要原样输出“无入参时也保留表格”“没有接口专用错误码时”“写作要求”这类模板元说明；按实际代码情况选择一种结果写入。

````markdown
# Feature 接口详细说明

- **Feature:** ${feature}
- **生成时间:** {当前时间}
- **生成依据:** 当前 Feature 实际代码改动
- **说明:** 本文档仅记录当前 Feature 新增或修改的接口。无法从代码确认的信息会标注为“代码中未确认”。

# 1、{接口名称}

## 1.1、接口基本信息

- **变更类型：** 新增 / 修改
- **请求方式：** GET / POST / PUT / DELETE / 代码中未确认
- **代码入口：** `src/main/java/.../XxxController.java`

```plain-text
接口地址：
/example/path
```

```json
接口入参示例：
{
  "field": "value"
}
```

```json
接口出参示例：
{
  "code": "success",
  "message": null,
  "data": {
    "id": "123"
  }
}
```

## 1.2、入参说明

| 字段路径 | 字段含义 | 字段类型 | 是否必填 | 枚举/范围 | 代码依据/说明 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| field | 字段含义 | String | 是 / 否 | 枚举或范围 | `XxxRequest.java` |
| items[].name | 集合元素字段含义 | String | 是 / 否 | 枚举或范围 | `XxxItemDTO.java` |

无入参时也保留表格：

| 字段路径 | 字段含义 | 字段类型 | 是否必填 | 枚举/范围 | 代码依据/说明 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| - | 无入参 | - | - | - | `XxxController.java` |

## 1.3、出参说明

| 字段路径 | 字段含义 | 字段类型 | 是否必返 | 枚举/范围 | 代码依据/说明 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| code | 返回码 | String | 是 | 项目统一返回码 | `Result.java` |
| message | 返回信息 | String | 否 | - | `Result.java` |
| data | 业务数据 | XxxVO / List<XxxVO> | 是 / 否 | - | 复杂类型字段见下方 |
| data.id | 业务对象 ID | String | 是 / 否 | - | `XxxVO.java` |
| data.items[].name | 集合元素名称 | String | 是 / 否 | - | `XxxItemVO.java` |

文件流返回时：

| 字段路径 | 字段含义 | 字段类型 | 是否必返 | 枚举/范围 | 代码依据/说明 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 文件流 | 文件内容 | Binary | 是 | .xlsx / .pdf / 代码中未确认 | `XxxController.java` |
| Content-Type | 响应类型 | String | 是 | application/... | `XxxController.java` |

## 1.4、错误码和枚举值

### 错误码

| 错误码 | 错误信息 | 触发条件 | 代码依据 |
|:---:|:---:|:---:|:---:|
| CODE | 错误信息 | 触发条件 | `ErrorCode.java` / `XxxServiceImpl.java` |

没有接口专用错误码时：

| 错误码 | 错误信息 | 触发条件 | 代码依据 |
|:---:|:---:|:---:|:---:|
| 未发现接口专用错误码 | - | 当前接口代码中未发现专用错误码；异常按项目统一异常处理 | `GlobalExceptionHandler.java` / `Result.java` / `XxxController.java` |

### 枚举值

| 字段路径 | 枚举值 | 含义 | 代码依据 |
|:---:|:---:|:---:|:---:|
| status | S | 成功 | `XxxEnum.java` |

没有枚举字段时：

| 字段路径 | 枚举值 | 含义 | 代码依据 |
|:---:|:---:|:---:|:---:|
| 未发现枚举字段 | - | 当前接口入参和出参未发现枚举字段 | `XxxRequest.java` / `XxxResponse.java` |

## 1.5、数据源

```plain-text
数据源：
- 数据库表：table_name，用途：说明读写目的。
- 外部接口：service.method，用途：说明调用目的。
- 文件：template.xlsx，用途：说明读取或返回目的。
```

不涉及数据源时：

```plain-text
数据源：
- 接口不涉及数据库表或外部数据源。
```

```sql
-- SQL 来源：src/main/resources/mapper/XxxMapper.xml#selectXxx
-- SQL 用途：查询用途说明
select
  ...
from table_name
where ...
```

MyBatis 动态 SQL 示例：

```xml
<!-- SQL 来源：src/main/resources/mapper/XxxMapper.xml#selectXxx -->
<select id="selectXxx" resultType="...">
  select
    ...
  from table_name
  <where>
    <if test="field != null">
      and field = #{field}
    </if>
  </where>
</select>
```

不涉及 SQL 时：

```plain-text
SQL：
接口不涉及 SQL。
```

## 1.6、接口功能介绍及应用场景

（1）功能：说明该接口提供的能力。

（2）应用场景：说明调用方、使用场景、触发时机。

## 1.7、接口整体逻辑

1. Controller 接收请求，读取路径参数、query 参数、请求体或上传文件。
2. 对必填字段、格式、权限、租户、状态等进行校验；没有对应逻辑时写“代码中未发现专用校验逻辑”。
3. 调用 Service / Use Case / Handler 处理业务逻辑。
4. 按代码实际情况说明数据库查询、外部接口调用、缓存读取、分页、排序、过滤、状态转换、字段映射等处理过程。
5. 组装响应对象或文件流并返回。
6. 说明异常处理方式；没有接口专用错误码时，说明按项目统一异常处理。

## 1.8、代码依据

| 内容 | 代码位置 | 说明 |
|:---:|:---:|:---:|
| 接口入口 | `src/main/java/.../XxxController.java` | 定义接口路径、请求方式和入参绑定 |
| 入参对象 | `src/main/java/.../XxxRequest.java` | 定义请求字段 |
| 出参对象 | `src/main/java/.../XxxResponse.java` | 定义响应字段 |
| 统一返回包装 | `src/main/java/.../Result.java` | 定义 code / message / data 等包装字段 |
| 业务实现 | `src/main/java/.../XxxServiceImpl.java` | 处理主流程 |
| 数据访问 | `src/main/resources/mapper/XxxMapper.xml` | 查询或写入数据 |
| 外部调用 | `src/main/java/.../XxxClient.java` | 调用外部接口 |
| 错误码 / 枚举 | `src/main/java/.../ErrorCode.java` / `XxxEnum.java` | 定义异常返回或枚举值 |

如果某类代码未定位到，可以保留一行说明，但正文不得基于它推断细节：

| 内容 | 代码位置 | 说明 |
|:---:|:---:|:---:|
| 业务实现 | 代码中未确认实现类位置 | 未定位到实现类，因此本文不展开 Service 内部逻辑 |
````

多接口时，按 `# 1、{接口名称}`、`# 2、{接口名称}`、`# 3、{接口名称}` 继续追加。

## VERIFY_REPORT.md 回写片段

生成 `FEATURE_API_DETAIL.md` 时，在 `VERIFY_REPORT.md` 中补充：

```markdown
## 接口详细说明文档

本 Feature 涉及新增或修改接口，已生成：

- `FEATURE_API_DETAIL.md`
```

未生成时，在 `VERIFY_REPORT.md` 中补充：

```markdown
## 接口详细说明文档

未从当前 Feature 实际代码改动中发现新增或修改接口，因此未生成 `FEATURE_API_DETAIL.md`。
```

或：

```markdown
## 接口详细说明文档

上游设计提到接口变更，但当前代码改动中未能确认接口入口或请求响应定义，因此未生成 `FEATURE_API_DETAIL.md`。请人工确认是否需要补充接口详细说明。
```
