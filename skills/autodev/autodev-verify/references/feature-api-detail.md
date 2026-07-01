# FEATURE_API_DETAIL.md 生成模板

用于 `/autodev-verify` 阶段生成 `{FEATURE_DIR}/FEATURE_API_DETAIL.md`。

本文件只在当前 Feature 的实际代码改动涉及新增或修改接口时使用。没有接口新增或修改时，不生成 `FEATURE_API_DETAIL.md`。

## 生成约束

1. 必须以实际代码为依据。
2. 只记录当前 Feature 新增或修改的接口。
3. 无法从代码确认的信息必须标注“代码中未确认”。
4. 不得根据 PRD、proposal、specs、design 或 PLAN 编造接口字段、SQL、错误码、枚举或内部逻辑。
5. 文档结构参考测试提供的接口说明案例：接口基本信息使用代码块展示地址和报文示例，字段说明使用表格，数据源和 SQL 使用代码块，接口功能和整体逻辑使用自然语言描述。

## 未确认信息写法

- 接口地址无法确认：写“代码中未确认接口地址”。
- 入参或出参示例无法确认：写“代码中未确认入参示例”或“代码中未确认出参示例”。
- 字段含义无法确认：写“代码中未确认字段业务含义”。
- 枚举值无法确认：写“代码中未确认枚举范围”。
- 错误码无法确认：写“代码中未确认专用错误码”。
- SQL 无法确认：写“代码中未发现明确 SQL”。
- 外部接口调用无法确认：写“代码中未确认外部接口调用”。

## 文档模板

````markdown
# Feature 接口详细说明

- **Feature:** {FEATURE_ID}
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
  "data": {}
}
```

## 1.2、入参说明

| 字段名称 | 字段含义 | 字段类型 | 是否必填 | 枚举/范围 | 说明 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| field | 字段含义 | String | 是 / 否 | 枚举或范围 | 代码依据：`XxxRequest.java` |

## 1.3、出参说明

| 字段名称 | 字段含义 | 字段类型 | 是否必返 | 枚举/范围 | 说明 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| field | 字段含义 | String | 是 / 否 | 枚举或范围 | 代码依据：`XxxResponse.java` |

## 1.4、错误码和枚举值

### 错误码

| 错误码 | 错误信息 | 触发条件 | 代码依据 |
|:---:|:---:|:---:|:---:|
| CODE | 错误信息 | 触发条件 | `ErrorCode.java` |

### 枚举值

| 字段名称 | 枚举值 | 含义 | 代码依据 |
|:---:|:---:|:---:|:---:|
| status | S | 成功 | `XxxEnum.java` |

## 1.5、数据源

```plain-text
数据源：
database.table_name
external-service-name
```

```sql
-- sql语句1（用途说明）
select ...
```

```sql
-- sql语句2（用途说明）
select ...
```

如代码中未发现明确 SQL，写：

```plain-text
代码中未发现明确 SQL。
```

## 1.6、接口功能介绍及应用场景

（1）功能：说明该接口提供的能力。

（2）应用场景：说明调用方、使用场景、触发时机。

## 1.7、接口整体逻辑

（1）说明参数校验、权限 / 租户 / 鉴权逻辑。

（2）说明数据查询、外部接口调用、缓存读取等处理过程。

（3）说明分页、排序、过滤、状态转换、字段映射等特殊逻辑。

（4）说明异常处理、错误码返回、兜底逻辑。

## 1.8、代码依据

| 内容 | 代码位置 | 说明 |
|:---:|:---:|:---:|
| 接口入口 | `src/main/java/.../XxxController.java` | 定义接口路径和请求方式 |
| 入参对象 | `src/main/java/.../XxxRequest.java` | 定义请求字段 |
| 出参对象 | `src/main/java/.../XxxResponse.java` | 定义响应字段 |
| 业务逻辑 | `src/main/java/.../XxxService.java` | 处理主流程 |
| 数据访问 | `src/main/resources/mapper/XxxMapper.xml` | 查询数据 |
| 错误码 / 枚举 | `src/main/java/.../ErrorCode.java` | 定义异常返回或枚举值 |
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
