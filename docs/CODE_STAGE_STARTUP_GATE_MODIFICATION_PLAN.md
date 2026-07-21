# Code 阶段启动门禁完整改造方案

## 1. 背景

当前 `/autodev-code` 阶段的强制门禁主要是 `.autobizdevops/modules_compile.json` 里的 `compile_command`。
现有示例是：

```json
{
  "version": 1,
  "modules": [
    {
      "module": "root",
      "path": "/absolute/path/to/code/module",
      "compile_command": "mvn compile"
    }
  ]
}
```

这只能证明主源码可以编译，不能证明应用可以启动。Spring Bean 装配、配置绑定、profile、migration、外部依赖、classpath、端口、启动 runner 等问题，都可能在 `mvn compile` 之后才暴露。

本方案目标是把 `code_done` 的含义从“编译通过”升级为：

> 当前代码已经完成，并且目标应用在测试/冒烟环境下至少可以启动，基础健康检查通过。

## 2. 结论

> 当前策略更新：本文件中的 `startup/smoke gate 必须阻断 code_done` 方案不作为当前主策略落地。现阶段采用 advisory smoke 混合策略：`plan.json.validationCommands` 与 code_done evidence gate 继续承担强门禁；更慢、更依赖环境的启动/主链路冒烟放入 `SMOKE_TEST_PLAN.json`，由 code 阶段执行并写入 `SMOKE_RESULT.json` 与 `action=smoke` evidence。smoke 必须尝试执行，但 `SMOKE_RESULT.json.verdict=FAIL/BLOCKED/SKIPPED` 不阻断 `code_done`。若未来要恢复 startup/smoke 强阻断，必须先明确它与 advisory smoke 的边界，不能让同一条 smoke 命令同时既阻断又不阻断。

> 下文第 3-13 节保留为历史阻断式启动门禁方案记录，当前不要按这些章节实施 `modules_compile.json v2 startup/smoke 必须阻断`。当前落地入口以 `SMOKE_TEST_PLAN.json`、`SMOKE_RESULT.json` 和 `run_advisory_smoke.py` 为准。

不是只改 `plan.json`。

需要改两层：

1. `plan.json.validationCommands`
   - 作用：让 `/autodev-code` 在任务执行过程中主动跑验证，并把结果写入 evidence。
   - 局限：AI 可能漏写；lean/custom 工作流可能没有 `plan.json`；最终 hook 不一定强制执行这些命令。

2. `code_done` 最终强制门禁
   - 作用：兜底阻止无法启动的代码进入 `code_done`。
   - 这是必须改的核心。

推荐关系：

```text
plan.json.validationCommands = 任务级验证与证据
modules_compile.json / code_done hook = 最终准出强制门禁
```

## 3. 需要修改的文件

### 3.1 `skills/autodev/autodev-code/SKILL.md`

现状：

- 要求开始编码前生成 `.autobizdevops/modules_compile.json`。
- 示例使用 `mvn compile`。
- 完成条件写的是“Java/Maven 至少编译”。

改造：

- 把“至少编译”改成“按模块类型生成运行期 gate”。
- Java 后端服务，尤其 Spring Boot 服务，不允许只生成 `mvn compile`。
- 默认要求生成 `compile -> test/verify -> startup/smoke` gate。
- 如果无法判断启动命令或 smoke profile，必须停止询问用户，不允许降级成 compile-only。

建议文案方向：

```text
开始任何业务代码修改前，根据 AGENTS.md 与项目 manifest 生成模块运行门禁清单
`.autobizdevops/modules_compile.json`。Java 后端服务不得只配置 compile gate；
Spring Boot 服务必须包含 startup/smoke gate，推荐命令为 `mvn -q -Psmoke verify`。
无法确定启动方式、测试 profile 或健康检查入口时，停止并询问用户。
```

### 3.2 `hooks/code_done_compile_guard.py`

现状：

- 读取 `.autobizdevops/modules_compile.json`。
- 只支持 `version: 1`。
- 每个模块只支持一个 `compile_command`。
- 失败文案是“编译失败/编译未通过”。

改造：

- 支持 `version: 2` 的 gates 模型。
- 保留 `version: 1` 兼容。
- 执行顺序从单一 compile 改为多 gate：
  `compile -> unit/test -> package/verify -> startup/smoke -> health`
- 对 Spring Boot 服务强制要求 startup/smoke gate。
- hook 输出从“编译校验”改成“code 运行期门禁”。

### 3.3 `hooks/hooks.json`

现状：

- execute hook 已注册 `python hooks/code_done_compile_guard.py`。
- 文案写的是“code_done 转移前编译 modules_compile.json 中的模块”。

改造：

- hook 命令可以不变。
- 修改 `additionalContext` 和 block 文案为“运行期门禁”，避免用户误解为只检查编译。

### 3.4 `tests/test_code_done_compile_guard.py`

需要补测试：

- v1 `compile_command` 仍然兼容。
- v2 gates 全部成功才允许 `code_done`。
- v2 compile gate 失败会 block。
- v2 startup gate 失败会 block。
- Spring Boot service 缺少 startup/smoke gate 会 block。
- gate 超时会 block，并返回输出尾部。
- 多模块时任一模块任一 gate 失败都会 block。
- 非 `code_in_progress` 状态仍跳过。
- dry-run 仍跳过。

### 3.5 `skills/autodev/autodev-plan/SKILL.md` 和模板

如果希望从计划阶段就把启动验证放进任务，应同步更新 plan 规则：

- Java/Spring Boot 任务的 `validationCommands` 不应只写 `mvn compile`。
- 默认写 `mvn -q test` 或 `mvn -q -Psmoke verify`。
- 对涉及启动配置、Bean、依赖注入、数据库 migration、外部集成的任务，必须包含 startup/smoke 验证。

注意：这是增强任务级 evidence，不是最终兜底。

### 3.6 项目级 `AGENTS.md`

业务项目应明确：

- 模块路径。
- Maven 命令。
- 启动 profile。
- smoke profile。
- 健康检查地址。
- 外部依赖如何在测试环境中提供。

没有这些信息时，AI 不应该猜启动命令。

## 4. `modules_compile.json` v2 设计

继续沿用文件名，避免大范围迁移；把内容升级为 gate 模型。

示例：

```json
{
  "version": 2,
  "modules": [
    {
      "module": "backend-service",
      "path": "/absolute/path/to/backend-service",
      "moduleType": "spring-boot-service",
      "gates": [
        {
          "id": "compile",
          "type": "compile",
          "command": "mvn -q -DskipTests compile",
          "timeoutSeconds": 1800,
          "required": true
        },
        {
          "id": "unit-test",
          "type": "test",
          "command": "mvn -q test",
          "timeoutSeconds": 1800,
          "required": true
        },
        {
          "id": "startup-smoke",
          "type": "startup",
          "command": "mvn -q -Psmoke verify",
          "timeoutSeconds": 2400,
          "required": true
        }
      ]
    }
  ]
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `version` | 新格式为 `2`；旧格式 `1` 继续支持 |
| `module` | 模块名，用于日志和报错 |
| `path` | 模块目录绝对路径，命令以它为 cwd 执行 |
| `moduleType` | 模块类型，如 `java-library`、`spring-boot-service`、`frontend`、`custom` |
| `gates` | 本模块 code_done 前必须通过的门禁 |
| `gates[].id` | gate 唯一标识 |
| `gates[].type` | `compile`、`test`、`verify`、`startup`、`health`、`custom` |
| `gates[].command` | 实际执行命令 |
| `gates[].timeoutSeconds` | 单个 gate 超时时间 |
| `gates[].required` | 是否必过，默认 `true` |

## 5. 模块类型默认规则

### 5.1 Java library

适合普通 jar、工具包、非启动应用。

最低要求：

```text
mvn -q verify
```

可拆成：

```json
[
  { "type": "compile", "command": "mvn -q -DskipTests compile" },
  { "type": "verify", "command": "mvn -q verify" }
]
```

### 5.2 Spring Boot service

适合 Web 服务、后台服务、批处理应用。

最低要求：

```text
mvn -q -Psmoke verify
```

必须能覆盖：

- Spring context 启动。
- profile 配置加载。
- Bean 装配。
- 配置属性绑定。
- migration 初始化。
- 必要健康检查。

### 5.3 多模块 Maven

推荐：

```text
mvn -q -pl <module> -am -Psmoke verify
```

`-pl` 限定目标模块，`-am` 自动构建依赖模块。

### 5.4 前端模块

如果 code 阶段同时改前端，建议：

```text
npm run typecheck
npm test
npm run build
```

前端启动冒烟可另设 `startup` gate，但后端服务启动问题的核心还是 Spring Boot smoke。

## 6. Spring Boot 启动冒烟标准

业务项目应提供 smoke profile。

### 6.1 最小启动测试

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ApplicationStartupSmokeTest {
    @Test
    void applicationStarts() {
    }
}
```

### 6.2 健康检查测试

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ApplicationHealthSmokeTest {
    @LocalServerPort
    int port;

    @Test
    void healthIsUp() {
        var body = new RestTemplate()
            .getForObject("http://localhost:" + port + "/actuator/health", String.class);

        assertThat(body).contains("\"status\":\"UP\"");
    }
}
```

### 6.3 Maven profile 示例

业务项目可以用 `smoke` profile 绑定 integration test：

```xml
<profile>
  <id>smoke</id>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-failsafe-plugin</artifactId>
        <executions>
          <execution>
            <goals>
              <goal>integration-test</goal>
              <goal>verify</goal>
            </goals>
          </execution>
        </executions>
      </plugin>
    </plugins>
  </build>
</profile>
```

也可以用 Spring Boot Maven Plugin 的 `spring-boot:start` / `spring-boot:stop` 在集成测试阶段启动应用，再跑 HTTP smoke test。

## 7. 外部依赖处理

启动门禁不能依赖“开发者电脑上刚好有数据库/Redis/Kafka”。

推荐顺序：

1. test profile + mock/stub
2. H2 或内存替代
3. Testcontainers
4. 专用测试环境

如果项目必须连真实外部环境，`AGENTS.md` 里要明确：

- 环境变量。
- 账号密钥注入方式。
- 网络可达性要求。
- 不可用时是否允许跳过。

默认不建议跳过 startup gate；如果跳过，必须写入阻断或风险说明，不能进入 `code_done`。

## 8. `plan.json.validationCommands` 应该怎么改

计划阶段应该把验证命令写得更接近真实风险。

示例：

```json
{
  "id": "T003",
  "title": "修复订单查询接口启动配置",
  "status": "pending",
  "validationCommands": [
    {
      "command": "mvn -q -pl order-service -am test"
    },
    {
      "command": "mvn -q -pl order-service -am -Psmoke verify"
    }
  ]
}
```

规则：

- 改普通业务方法：至少 `mvn -q test`。
- 改 Controller、Service、Repository 装配：至少加 Spring context test。
- 改配置、Bean、starter、migration、外部依赖：必须加 `-Psmoke verify`。
- 改多模块依赖：用 `-pl <module> -am`。

但再次强调：`plan.json` 只负责任务执行过程中的 evidence，最终是否允许 `code_done` 仍由 code_done hook 决定。

## 9. code_done hook 执行流程

建议新流程：

```text
收到 update_checkpoint.py --checkpoint code_done
  -> 确认当前 feature 是 code_in_progress
  -> 先跑 evidence 完整性门禁
  -> 读取 .autobizdevops/modules_compile.json
  -> 如果 version=1，按旧 compile_command 执行，并输出兼容警告
  -> 如果 version=2，逐模块逐 gate 执行
  -> Spring Boot service 缺 startup/smoke gate 则 block
  -> 任一 gate 失败则 block
  -> 全部通过才允许 code_done
```

失败输出应包含：

```text
module: backend-service
gate: startup-smoke
type: startup
command: mvn -q -Psmoke verify
exit_code: 1
timeout: false
output_tail:
  ...
```

这样 AI 能直接看到启动失败原因，并在 code 阶段继续修。

## 10. 兼容策略

### 阶段 1：支持 v2，不强制

- `version: 1` 继续可用。
- `version: 2` 开始可用。
- v1 输出 warning：`compile_command 只能覆盖编译，建议升级 gates`。

### 阶段 2：Spring Boot 服务强制 startup gate

- 如果 `moduleType=spring-boot-service` 且没有 `startup` 或 `smoke` gate，block。
- 如果无法判断 moduleType，可以先不强制，但输出风险提示。

### 阶段 3：默认生成 v2

- `/autodev-code` 默认生成 v2。
- `mvn compile` 只允许作为 compile gate，不允许作为唯一 gate。

### 阶段 4：逐步弃用 v1

- 保留读取能力，但不再由 skill 生成。

## 11. 测试矩阵

需要补的测试：

| 场景 | 预期 |
| --- | --- |
| v1 compile 成功 | 兼容通过 |
| v1 compile 失败 | block |
| v2 compile/test/startup 全成功 | 通过 |
| v2 compile 失败 | block，显示 compile gate |
| v2 startup 失败 | block，显示 startup gate |
| Spring Boot service 缺 startup gate | block |
| Java library 无 startup gate | 可通过，只要 verify 过 |
| 多模块第二个模块失败 | block，指出失败模块 |
| gate timeout | block，显示 timeout |
| 非 code_in_progress | 跳过 |
| dry-run code_done | 跳过 |

## 12. 完成验收标准

改造完成后，应满足：

- `/autodev-code` 不再默认生成 compile-only 清单。
- Spring Boot 服务无法启动时，不能进入 `code_done`。
- 启动失败日志能展示给用户和 AI。
- `plan.json.validationCommands` 能记录任务级启动验证 evidence。
- 旧项目 v1 清单短期不崩，但有升级路径。
- 多模块项目可以按模块粒度执行 smoke。

## 13. 推荐实施顺序

1. 先改 `code_done_compile_guard.py`，支持 v2 gates 和 v1 兼容。
2. 补 `tests/test_code_done_compile_guard.py`。
3. 改 `autodev-code/SKILL.md`，让 AI 默认生成 v2。
4. 改 `hooks/hooks.json` 文案。
5. 改 `autodev-plan` 规则和模板，让计划阶段写入 smoke validation。
6. 在业务项目补 smoke profile 和启动测试。
7. 逐步把已有项目的 `modules_compile.json` 从 v1 升级到 v2。

## 14. 官方参考

- Maven Build Lifecycle: https://maven.apache.org/guides/introduction/introduction-to-the-lifecycle.html
- Spring Boot Testing Applications: https://docs.spring.io/spring-boot/reference/testing/spring-boot-applications.html
- Spring Boot Maven Plugin Integration Tests: https://docs.spring.io/spring-boot/maven-plugin/integration-tests.html
- Testcontainers JUnit 5 Quickstart: https://java.testcontainers.org/quickstart/junit_5_quickstart/
