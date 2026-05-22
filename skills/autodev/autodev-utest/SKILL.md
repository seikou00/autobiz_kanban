---
name: autodev-utest
description: "编写高质量单元测试。适用于需要为现有代码编写测试、验证功能正确性，分析大型类并拆分成可测试单元的场景。当用户提到：写单测、修复单测、单元测试、编写测试、test case、单元测试生成。默认由当前会话内联执行。"
---

**PLUGIN_OUTPUT_DIR**：插件产物的目录。SKILL生产的任务产物都只能写入或读取这个位置。
```
工作目录 = {PLUGIN_OUTPUT_DIR}/.autobizdevops/features/{slug}/
```
# 单元测试生成器

> ⚠️ **强制规则：按顺序执行每一步，禁止修改被测源码。**

## 执行主体

本 skill 默认且只能由当前会话内联执行：

- 当前会话直接读取源码、生成测试、执行验证、更新状态文件。
- 不得把测试生成或测试修复工作委派给下级 agent或子agent。


状态文件统一使用 `.autobizdevops/STATE.md`；每次推进 checkpoint 时，只允许更新当前 `{slug}` 对应的 Feature 行，不得改写其他 slug 的状态。
若 checkpoint 为空、未知，或无法唯一确定当前 Feature，必须停止并提示用户选择 Feature。
## Step 1: 写入 Checkpoint（标记开始）

使用统一脚本只更新当前 `{slug}` 对应行的 checkpoint 为 `unit_test_in_progress`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint unit_test_in_progress
```

## 产物输出约定

当技能面向某个 Feature 运行时，生成的产物统一落在最外层工作目录 `.autobizdevops` 下：

- 单测报告：`.autobizdevops/features/{slug}/UNIT_TEST_REPORT.md`
- 单测执行日志：`.autobizdevops/features/{slug}/test-output.log`
- 全局状态：`.autobizdevops/STATE.md`
- 最佳实践沉淀：`.autobizdevops/references/autodev-utest/`

## 写入 Checkpoint（标记完成）

使用统一脚本只更新当前 `{slug}` 对应行的 checkpoint 为 `unit_test_done`：

```bash
python "{PLUGIN_DIR}/hooks/update_checkpoint.py" --workspace "{WORKSPACE}" --feature "{slug}" --checkpoint unit_test_done
```

### 自检 — 完成状态

- [ ] `{工作目录}/UNIT_TEST_REPORT.md` 已写入
- [ ] `{工作目录}/test-output.log` 已写入（如当前项目无法执行测试，必须在报告中明确原因与替代证据）

## 核心思想：迭代增量开发 + 源码保护
> 核心特点:
>
> - 小增量: 每次只处理一个方法的一个场景
> - 快验证: 写完即测，失败即修
> - 可持续: 始终保持测试可运行状态
> - 渐进式: 从核心到边缘，从简单到复杂
> - 无害化: 绝对禁止修改单测对应的源码类 - 这是最高原则
> - 只读源码: 源码类只能读取分析，不能修改任何内容
> ⚠️ 最高禁令:
> - ❌ 禁止修改被测类的任何代码
> - ❌ 禁止添加/删除/修改源码中的方法
> - ❌ 禁止修改源码中的注解、字段、逻辑
> - ✅ 只允许读取源码进行分析
> - ✅ 只允许修改测试类文件
---
## 迭代增量流程图
```
┌─────────────────────────────────────────────────────────────┐
│ 迭代循环 (每个方法) │
├─────────────────────────────────────────────────────────────┤
│ │
│ [分析1个场景] → [编写测试] → [执行验证] → [检查结果] │
│ │ │ │ │
│ ↓ ↓ ↓ ↓
│ 失败 失败 成功 成功 │
│ │ │ │ │
│ ↓ ↓ ↓ ↓
│ [修复问题] → [重新执行] → [记录状态] → [继续下一个场景] │
│ │
└─────────────────────────────────────────────────────────────┘
│
↓
┌─────────────────┐
│ 全部方法完成 │
│ → 清理记忆 │
│ → 输出报告 │
└─────────────────┘
```
---
## 完整流程 (10步)
| 步骤 | 内容 | 核心动作 |
| --- | --------- |---------------------|
| 1 | 环境检测 | 检查项目结构 |
| 2 | 检查依赖 | 确保 JUnit/Mockito 存在 |
| 3 | 查找模板 | 按优先级搜索模板 |
| 4 | 分析代码 | 提取类/方法/依赖 |
| 5 | 填充模板 | 变量替换生成代码 |
| 6 | 制定计划 | 场景优先级排序 |
| 7 | 迭代验证 | 逐场景编写+验证 |
| 8 | 增量执行 | 保持测试可运行 |
| 9 | 完成后清理 | 清理临时文件 |
| 10 | 总结经验、迭代组件 | 将执行单测的最佳实践放指定目录下 |
---
### 第一步：环境检测 (Java/Maven)
首先检测项目环境：
1. 构建工具

- 检查 `pom.xml` 或 `build.gradle`
- Maven/Gradle 版本
2. 测试框架

- JUnit 4 (`@RunWith`, `@Test`, `@Before`, `@After`)
- Spring Boot Test (`@SpringBootTest`)
3. 项目结构

```
src/
├── main/java/ # 源代码
└── test/java/ # 测试代码
```
4. 常用命令

```bash
mvn dependency:tree
mvn test -X 2>&1 | grep -A5 "surefire"
mvn test-compile
```
---
### 第二步：检查测试依赖项
必须检查以下依赖是否存在于 pom.xml：
```xml
<!-- JUnit 4 -->
<dependency>
<groupId>junit</groupId>
<artifactId>junit</artifactId>
<version>4.13.2</version>
<scope>test</scope>
</dependency>
<!-- Mockito -->
<dependency>
<groupId>org.mockito</groupId>
<artifactId>mockito-core</artifactId>
<version>4.11.0</version>
<scope>test</scope>
</dependency>
<!-- Spring Boot Test (如果是Spring项目) -->
<dependency>
<groupId>org.springframework.boot</groupId>
<artifactId>spring-boot-starter-test</artifactId>
<scope>test</scope>
</dependency>
<!-- JaCoCo (覆盖率) -->
<plugin>
<groupId>org.jacoco</groupId>
<artifactId>jacoco-maven-plugin</artifactId>
<version>0.8.10</version>
</plugin>
```
检查命令：
```bash
# 检查是否包含依赖
grep -E "junit|mockito|jacoco" pom.xml
# 如果缺失，请报错
```
---
### 第三步：查找/创建测试模板
模板查找顺序：
```
1. 同目录下查找: {ClassName}Test.java.template
2. 项目根目录: test-templates/TemplateTest.java
3. 用户目录: ~/.unit-test-templates/DefaultTemplate.java
4. 使用内置默认模板
```
---
### 第四步：基于代码分析填充模板 (只读操作)
⚠️ 重要提醒: 此步骤为**只读操作**，只能读取源码进行分析，绝对不能修改源码。
读取被测类，分析以下内容（只读）：
1. 类名和方法 (只读分析)

```java
// ✅ 只读分析，不能修改
public class UserService {
public User findById(Long id) { ... } // 读取方法签名
public void save(User user) { ... } // 读取方法签名
public void delete(Long id) { ... } // 读取方法签名
}
```
2. 依赖项 (只读分析)

```java
// ✅ 只读分析字段和注解
@Autowired
private UserRepository userRepository; // 读取依赖关系
@Autowired
private CacheService cacheService; // 读取依赖关系
```
3. 填充模板变量 (只读提取)

| 变量 | 来源 | 示例 | 操作性质 |
| -------------- | -------- | ------------------------------ | -------- |
| `{ClassName}` | 被测类名 | `UserService` | 只读提取 |
| `{Package}` | 包路径 | `com.example.service` | 只读提取 |
| `{MockFields}` | @Mock 声明 | `@Mock private UserRepository` | 只读提取 |
| `{Methods}` | 待测方法 | `findById, save, delete` | 只读提取 |
❌ 绝对禁止的操作:
- 修改源码类的任何内容
- 添加/删除源码类的方法
- 修改源码类的注解或字段
- 改变源码类的逻辑结构
---
### 第五步：生成测试代码
模板引擎替换示例：
```java
// 模板
package {Package};
@RunWith(MockitoJUnitRunner.class)
public class {ClassName}Test {
{MockFields}
@InjectMocks
private {ClassName} {ClassNameLower};
{MethodTests}
}
// 填充后
package com.example.service;
@RunWith(MockitoJUnitRunner.class)
public class UserServiceTest {
@Mock
private UserRepository userRepository;
@Mock
private CacheService cacheService;
@InjectMocks
private UserService userService;
@Test
public void testFindById() {
// 测试代码
}
}
```
---
### 第六步：分析被测类 + 制定测试计划 + 场景优先级
#### 方法优先级排序
| 优先级 | 方法类型 | 原因 |
| ------ | --------------- | --------- |
| P0 | public 方法 | 核心功能，必须测试 |
| P1 | 边界/异常处理 | 高风险 |
| P2 | 非 public 方法 | 不需要直接测试 |
#### 重要规则：非 public 方法不直接测试
规则：
> 非 public 方法（private/protected/package-private）不需要编写单独的单元测试。
>
> 这些方法应该通过调用它们的 public 方法间接测试。
原因：
1. 非 public 方法是实现细节，可能随时重构
2. 通过 public 方法的测试已经覆盖了这些路径
3. 直接测试私有方法会降低代码的可维护性
示例：
```java
public class UserService {
public void save(User user) {
// 调用私有方法
validate(user); // 通过 public 方法间接测试
persist(user); // 通过 public 方法间接测试
notify(user); // 通过 public 方法间接测试
}
private void validate(User user) { ... } // 不需要单独测试
protected void persist(User user) { ... } // 不需要单独测试
void notify(User user) { ... } // 不需要单独测试
}
```
何时需要测试私有方法？
- 极其复杂的业务逻辑
- 单独的工具类/静态方法
- 经过评估确认需要直接测试（需标注 `@RareCase`）
#### 场景优先级 (同一方法内)
| 优先级 | 场景类型 | 迭代顺序 |
| --- | ----- | ---- |
| 1 | 正常路径 | 第一个测 |
| 2 | 边界值 | 第二个测 |
| 3 | 异常/错误 | 第三个测 |
| 4 | 特殊情况 | 最后测 |
---
### 第七步：迭代增量执行验证 (核心) - 强制增量执行 + 源码保护
#### 迭代原则 (必须严格遵守)
| 原则 | 说明 | 强制执行措施 |
| -------- | ---------------- | ------ |
| 原子添加 | 每次只添加一个 @Test 方法 | 生成代码时只允许添加一个@Test方法 |
| 独立运行 | 每个测试用例独立，不依赖其他用例 | 使用精确的测试命令 |
| 快速反馈 | 写完即运行验证 | 每次添加后立即执行测试 |
| 可回滚 | 单个用例失败不影响其他用例 | 失败时只修复当前测试 |
| 源码保护 | 绝对禁止修改源码类 | 只允许修改测试类文件 |
| 及时清理 | 每次迭代完成后清理临时文件 | 每次迭代后重置状态 |
#### 强制增量执行流程 (含源码保护检查点)
```
┌─────────────────────────────────────────────────────────────┐
│ 迭代 N: 严格的一个测试一个验证 + 源码保护 │
├─────────────────────────────────────────────────────────────┤
│ 1. 选取场景: 按优先级选择下一个待测场景 │
│ 2. 源码保护检查: 确认只读取源码，不修改任何源码文件 │
│ 3. 生成测试: 只生成当前场景的@Test方法 │
│ 4. 文件修改检查: 确认只修改测试类文件，不修改源码类 │
│ 5. 执行验证: mvn test -Dtest=ClassName#testMethod │
│ 6. 检查结果: │
│ ├─ ✅ 成功 → 保存状态 → 迭代 N+1 │
│ └─ ❌ 失败 → 分析原因 → 修复当前测试 → 重试步骤5 │
└─────────────────────────────────────────────────────────────┘
```
每个迭代的源码保护检查点:
- 检查点1 (步骤2): 分析源码时确认只读操作
- 检查点2 (步骤4): 生成代码时确认只修改测试文件
- 检查点3 (步骤6): 失败修复时确认不修改源码
#### 强制执行规则
规则1: 一次只生成一个测试方法
```java
// ✅ 正确：每次只添加一个@Test方法
@Test
public void testFindById_WhenIdIsNull() {
// 只包含当前场景的测试代码
}
// ❌ 错误：一次性生成多个测试方法
@Test
public void testFindById_WhenIdIsNull() { ... }
@Test
public void testFindById_WhenIdIsNegative() { ... }
```
规则2: 使用精确的测试命令
```bash
# ✅ 正确：精确到单个测试方法
mvn test -Dtest=UserServiceTest#testFindById_WhenIdIsNull
# ❌ 错误：运行整个测试类
mvn test -Dtest=UserServiceTest
```
规则3: 失败时只修复当前测试
- 如果测试失败，只修改当前失败的测试方法
- 不允许修改其他已通过的测试方法
- 不允许跳过失败直接进行下一个测试
规则4: 绝对禁止修改源码类 (最高优先级)
```java
// ✅ 正确操作：只修改测试类
// UserServiceTest.java - 可以修改
@Test
public void testFindById_WhenIdIsNull() { ... }
// ❌ 禁止操作：修改源码类
// UserService.java - 绝对禁止修改
public class UserService {
// 不能添加/删除/修改任何内容
}
```
违反源码保护规则的后果:
- 立即停止当前迭代
- 回滚所有修改
- 记录违规行为到最佳实践系统
- 重新开始严格遵守只读原则
#### 具体迭代示例 (严格执行)
```
迭代 1:
- 生成: testFindById_WhenIdIsNull()
- 执行: mvn test -Dtest=UserServiceTest#testFindById_WhenIdIsNull
- 结果: ✅ 成功 → 保存 → 迭代2
迭代 2:
- 生成: testFindById_WhenIdIsNegative()
- 执行: mvn test -Dtest=UserServiceTest#testFindById_WhenIdIsNegative
- 结果: ❌ 失败 → 分析 → 修复 → 重试 → ✅ 成功 → 迭代3
迭代 3:
- 生成: testFindById_WhenCacheHit()
- 执行: mvn test -Dtest=UserServiceTest#testFindById_WhenCacheHit
- 结果: ✅ 成功 → 迭代4
```
#### 迭代状态跟踪表
| 迭代 | 测试方法 | 状态 | 执行时间 | 备注 |
|------|----------|------|----------|------|
| 1 | testFindById_WhenIdIsNull | ✅ | 15s | 正常 |
| 2 | testFindById_WhenIdIsNegative | ✅ | 20s | 修复后通过 |
| 3 | testFindById_WhenCacheHit | ✅ | 18s | 正常 |
| 4 | testSave_WhenUserIsNull | 🔄 | - | 进行中 |
---
### 第八步：执行命令参考 (增量执行专用)
#### 增量执行命令 (必须使用)
```bash
# ✅ 强制增量执行：每次只运行一个测试方法
mvn test -Dtest=ClassName#testMethodName
# 示例：
mvn test -Dtest=UserServiceTest#testFindById_WhenIdIsNull
mvn test -Dtest=UserServiceTest#testFindById_WhenIdIsNegative
mvn test -Dtest=UserServiceTest#testFindById_WhenCacheHit
# ❌ 禁止使用：一次性运行多个测试
mvn test -Dtest=UserServiceTest#testFindById*
mvn test -Dtest=UserServiceTest#testSave*
mvn test -Dtest=UserServiceTest
```
#### 失败处理命令
```bash
# 查看详细错误信息
mvn test -Dtest=ClassName#testMethodName -X
# 跳过测试编译，快速重试
mvn test -Dtest=ClassName#testMethodName -DskipTests=false -Dmaven.test.skip=false
# 清理后重试
mvn clean test -Dtest=ClassName#testMethodName
```
#### 完成后的验证命令
```bash
# 所有测试完成后，验证整个测试类
mvn test -Dtest=ClassNameTest
# 生成覆盖率报告
mvn jacoco:report
open target/site/jacoco/index.html
```
#### 命令执行检查清单
- [ ] 每次只执行一个测试方法
- [ ] 使用精确的类名和方法名
- [ ] 测试失败时立即修复，不继续下一个
- [ ] 修复后重新执行当前测试
- [ ] 所有测试通过后再运行完整测试类
---
### 第九步：完成后清理
1. 输出测试报告
2. 统计覆盖率
3. 清理记忆 - 重置状态
#### 项目级未覆盖分支测试
获取覆盖率报告：
```bash
mvn jacoco:report
open target/site/jacoco/index.html
```
分析未覆盖分支：
| 类型 | 优先级 | 说明 |
| ------ | ------ | ---- |
| 异常分支 | 高 | 异常处理逻辑需要重点覆盖 |
| 边界条件 | 高 | 边界值测试确保系统稳定性 |
| 状态分支 | 中 | 不同状态转换路径需要测试 |
覆盖率分析流程：
1. 运行覆盖率报告生成
2. 识别未覆盖的分支和代码行
3. 根据优先级制定补充测试计划
4. 迭代添加测试用例覆盖遗漏分支
---

# 大类解决方案
## 测试套件
```java
@RunWith(Suite.class)
@Suite.SuiteClasses({
UserServiceCoreTest.class,
UserServiceValidationTest.class,
UserServiceRepositoryTest.class
})
public class UserServiceTestAll {}
```
## 分层测试
```
UserService
├── UserServiceCoreTest
├── UserServiceValidateTest
└── UserServiceCacheTest
```
---
# Mock 指南 (JUnit 4 + Mockito)
```java
@RunWith(MockitoJUnitRunner.class)
public class UserServiceTest {
@Mock
private UserRepository userRepository;
@InjectMocks
private UserService userService;
@Test
public void testFindById() {
when(userRepository.findById(1L)).thenReturn(user);
User result = userService.findById(1L);
Assert.assertNotNull(result);
verify(userRepository).findById(1L);
}
}
```
常用语法
```java
when(mock.method()).thenReturn(value);
when(mock.method()).thenThrow(new Exception());
verify(mock).method(any());
verify(mock, times(2)).method();
ArgumentCaptor<User> captor = ArgumentCaptor.forClass(User.class);
```
---
# 附加优化点
## 优化1: 测试覆盖率追踪
```
| 迭代 | 方法 | 用例 | 行覆盖 | 分支覆盖 |
|------|------|------|--------|----------|
| 1 | findById | 1/4 | 15% | 10% |
| 2 | findById | 2/4 | 30% | 25% |
```
---
## 优化2: 测试数据管理 (Fixture)
```java
// TestFixtures.java
public class TestFixtures {
public static User defaultUser() {
User user = new User();
user.setId(1L);
user.setName("test");
return user;
}
public static User user(Long id, String name) {
User user = new User();
user.setId(id);
user.setName(name);
return user;
}
}
// 使用
User user = TestFixtures.defaultUser();
```
---
## 优化3: 断言封装
```java
// 常用断言
public static void assertUserEquals(User expected, User actual) {
Assert.assertEquals(expected.getId(), actual.getId());
Assert.assertEquals(expected.getName(), actual.getName());
}
public static void assertThrows(Class<? extends Exception> ex, Runnable r) {
try {
r.run();
Assert.fail("Should throw " + ex.getName());
} catch (Exception e) {
Assert.assertTrue(ex.isInstance(e));
}
}
```
---
## 优化4: 测试失败诊断
| 失败类型 | 原因 | 解决方案 |
| ---------------------- | -------- | --------------- |
| NullPointerException | Mock 未注入 | 检查 @InjectMocks |
| Unexpected call | Mock 未设置 | 添加 when() |
| Wanted but not invoked | 方法未调用 | 检查调用顺序 |
| Argument mismatch | 参数不符 | 使用 any(), eq() |
---
## 优化5: 测试标记
```java
@Test
@Tag("integration") // 集成测试
public void testWithDb() { }
@Ignore("待实现")
public void testPending() { }
```
---
## 优化6: 迭代节奏 (5分钟强制循环)
```
├── 1分钟: 分析当前场景
├── 2分钟: 编写单个测试方法
├── 1分钟: 执行验证 (mvn test -Dtest=ClassName#method)
└── 1分钟: 修复问题 (如有) 或 准备下一个
```
强制要求: 每个迭代必须在5分钟内完成，确保快速反馈
---
## 灵魂三问 + 源码保护检查 (每次迭代)
| 问题 | 目的 | 源码保护检查 |
| -------- | ---- | -------- |
| 这个场景测了吗？ | 不遗漏 | ✅ 只读取源码分析 |
| 测试通过了吗？ | 快速反馈 | ✅ 只修改测试文件 |
| 源码保护了吗？ | 最高优先级 | ❌ 绝对不修改源码类 |
| 覆盖率提升了吗？ | 量化进度 | ✅ 通过测试覆盖源码 |
