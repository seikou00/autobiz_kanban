# 单元测试生成器 (autodev-utest)
一个智能的单元测试自动生成工具，采用迭代增量开发模式，专注于生成高质量的Java单元测试代码。
## 🔄 最近更新
- 移除同步功能: 最佳实践不再同步到项目references文件夹，简化系统架构
- 移除用户确认: 删除交互式确认机制，改为自动环境检查后直接记录
- 添加环境检查: 自动检查Python环境和文件权限，确保记录可靠性
## 🎯 核心特性
### 迭代增量开发模式
- 小增量: 每次只处理一个方法的一个测试场景
- 快验证: 编写即测试，失败即修复
- 可持续: 始终保持测试可运行状态
- 渐进式: 从核心到边缘，从简单到复杂
### 🔒 源码保护原则（最高优先级）
- 只读源码: 源码类只能读取分析，不能修改任何内容
- 无害化: 绝对禁止修改单测对应的源码类
- 安全第一: 所有操作确保对源码零影响
## 🚀 快速开始
### 环境要求
- Java 8+
- Maven 或 Gradle
- JUnit 4
- Mockito
### 基本使用流程
```bash
# 1. 环境检测
mvn dependency:tree
# 2. 检查测试依赖
grep -E "junit|mockito" pom.xml
# 3. 运行单元测试生成
# 系统会自动分析源码并生成测试
```
## 📋 完整工作流程
### 10步执行流程
| 步骤 | 内容 | 核心动作 |
|------|------|----------|
| 1 | 环境检测 | 检查项目结构和依赖 |
| 2 | 检查依赖 | 确保测试框架存在 |
| 3 | 查找模板 | 搜索测试模板文件 |
| 4 | 分析代码 | 提取类/方法/依赖（只读） |
| 5 | 填充模板 | 生成测试代码 |
| 6 | 制定计划 | 场景优先级排序 |
| 7 | 迭代验证 | 逐场景编写+验证 |
| 8 | 增量执行 | 保持测试可运行 |
| 9 | 完成后清理 | 清理临时文件 |
| 10 | 总结经验 | 记录最佳实践 |
## 🔧 核心功能
### 源码分析（只读操作）
```java
// ✅ 只读分析，不能修改源码
public class UserService {
public User findById(Long id) { ... } // 读取方法签名
@Autowired
private UserRepository repo; // 读取依赖关系
}
```
### 测试生成示例
```java
@RunWith(MockitoJUnitRunner.class)
public class UserServiceTest {
@Mock
private UserRepository userRepository;

@InjectMocks
private UserService userService;

@Test
public void testFindById_WhenIdIsNull() {
// 自动生成的测试代码
User result = userService.findById(null);
assertNull(result);
}
}
```
### 增量执行命令
```bash
# ✅ 正确：每次只运行一个测试方法
mvn test -Dtest=UserServiceTest#testFindById_WhenIdIsNull
# ❌ 错误：一次性运行多个测试
mvn test -Dtest=UserServiceTest
```
## 📊 最佳实践记录系统
### 智能经验记录（自动环境检查）
系统在任务完成后自动记录最佳实践和经验教训，自动检查Python环境后直接记录：
```python
# 自动检查环境并记录最佳实践
recorder.record_best_practice(
"增量测试",
"每次只测试一个场景，确保快速反馈",
"@Test\npublic void testFindByIdWhenIdIsNull() {\n // 单个测试场景\n}"
)
# 自动记录环境配置问题
recorder.record_environment_issue("Java版本冲突", "使用Maven管理版本")
# 自动记录指令使用问题
recorder.record_command_issue("mvn test", "一次性运行所有测试",
"mvn test -Dtest=ClassName#methodName")
```
### 环境检查机制
- Python版本检查: 确保Python 3.6+
- 文件权限检查: 验证写入权限
- 模块依赖检查: 确保必要模块可用
- 自动记录: 环境满足时直接记录，无需用户确认
### 记录分类
- 环境配置问题: Java版本、构建工具配置等
- 指令使用问题: Maven/Gradle命令使用经验
- 最佳实践指南: 测试编写技巧和经验
- 工具框架指南: JUnit、Mockito使用技巧
### 存储位置
- 工作目录: `.autobizdevops/references/autodev-utest/`
- 本地项目: 最佳实践统一写入工作目录（简化架构）
### 一键整理功能
```python
# 自动删除重复和相似的最佳实践
stats = recorder.organize_best_practices(similarity_threshold=0.8)
print(f"删除 {stats['removed_duplicates']} 条重复记录")
```
## 🎨 测试策略
### 方法优先级
| 优先级 | 方法类型 | 测试策略 |
|--------|----------|----------|
| P0 | public方法 | 必须测试，核心功能 |
| P1 | 边界/异常处理 | 高风险，重点测试 |
| P2 | 非public方法 | 通过public方法间接测试 |
### 场景优先级（同一方法内）
1. 正常路径 - 第一个测试
2. 边界值 - 第二个测试
3. 异常/错误 - 第三个测试
4. 特殊情况 - 最后测试
## ⚠️ 重要规则
### 源码保护禁令
- ❌ 禁止修改被测类的任何代码
- ❌ 禁止添加/删除/修改源码中的方法
- ❌ 禁止修改源码中的注解、字段、逻辑
- ✅ 只允许读取源码进行分析
- ✅ 只允许修改测试类文件
### 增量执行规则
- 每次只生成一个@Test方法
- 使用精确的测试命令（类名#方法名）
- 测试失败时只修复当前测试
- 所有测试通过后再运行完整测试类
## 📁 项目结构
```
autodev-utest/
├── hooks/
│ ├── best_practice_recorder.py # 最佳实践记录器
│ └── example_usage.py # 使用示例
├── SKILL.md # 详细技能说明
└── README.md # 本文档
最佳实践存储位置:
.autobizdevops/references/autodev-utest/
```
## 🧪 演示运行
```bash
# 运行使用示例
cd hooks
python example_usage.py
# 查看生成的最佳实践
ls references/
```
## 🔍 故障排除
### 常见问题
Q: 测试依赖缺失？
A: 检查pom.xml是否包含JUnit和Mockito依赖
Q: 源码保护检查失败？
A: 确认只修改测试文件，不修改源码类
Q: 增量执行报错？
A: 使用精确的测试命令：`mvn test -Dtest=ClassName#methodName`
## 📈 性能优化
### 5分钟迭代节奏
- 1分钟: 分析当前场景
- 2分钟: 编写单个测试方法
- 1分钟: 执行验证
- 1分钟: 修复问题或准备下一个
### 覆盖率追踪
| 迭代 | 方法 | 用例 | 行覆盖 | 分支覆盖 |
|------|------|------|--------|----------|
| 1 | findById | 1/4 | 15% | 10% |
| 2 | findById | 2/4 | 30% | 25% |
## 🤝 贡献指南
欢迎提交Issue和Pull Request来改进这个项目！
## 📄 许可证
MIT License
---
重要提醒: 始终牢记源码保护原则，确保对项目代码零风险操作。
