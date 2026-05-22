#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最佳实践记录系统 - Python实现
用于在任务完成后自动记录最佳实践和经验教训
"""
import os
import json
import datetime
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
class Category(Enum):
"""记录分类枚举"""
ENVIRONMENT = ("pitfalls/environment.md", "环境配置问题")
COMMANDS = ("pitfalls/commands.md", "指令使用问题")
PARAMETERS = ("pitfalls/parameters.md", "参数设置问题")
BEST_PRACTICES = ("guidelines/best-practices.md", "最佳实践指南")
TOOLS_FRAMEWORKS = ("guidelines/tools-and-frameworks.md", "工具框架指南")
SKILL_CONFIG = ("config/skill-config.json", "技能配置")

def init(self, file_path: str, description: str):
self.file_path = file_path
self.description = description
class BestPracticeRecorder:
"""最佳实践记录器"""

def init(self, skill_name: str, task_id: str):
"""
初始化记录器

Args:
skill_name: 技能名称
task_id: 任务ID
"""
self.skill_name = skill_name
self.task_id = task_id
self.base_dir = Path(".autobizdevops") / "references" / "autodev-utest"
self.records: Dict[Category, List[str]] = {category: [] for category in Category}
self.execution_log: List[str] = []

def record(self, category: Category, content: str) -> None:
"""
记录最佳实践（带重复检查）

Args:
category: 记录分类
content: 记录内容
"""
# 检查是否已经存在相同内容
if self._is_duplicate_record(category, content):
print(f"⚠️ 跳过重复记录: {content[:50]}...")
return

timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
record_entry = f"## {timestamp} - 任务ID: {self.task_id}\n\n{content}\n\n---\n\n"
self.records[category].append(record_entry)
print(f"✓ 已记录到 {category.description}")

def isduplicate_record(self, category: Category, content: str) -> bool:
"""
检查是否为重复记录

Args:
category: 记录分类
content: 记录内容

Returns:
bool: 是否为重复记录
"""
# 检查当前会话中的重复记录
for existing_record in self.records[category]:
# 提取内容部分进行比较（去掉时间戳和任务ID）
existing_content = self._extract_content_from_record(existing_record)
if self._is_similar_content(existing_content, content):
return True

# 检查已保存文件中的重复记录
return self._check_file_duplicates(category, content)

def extractcontent_from_record(self, record: str) -> str:
"""从记录中提取内容部分（去掉时间戳和任务ID）"""
# 使用正则表达式提取内容部分
import re
# 匹配时间戳和任务ID之后的内容
match = re.search(r'## .*?\n\n(.*?)\n\n---\n\n', record, re.DOTALL)
if match:
return match.group(1).strip()
return record

def issimilar_content(self, content1: str, content2: str, similarity_threshold: float = 0.8) -> bool:
"""
检查两个内容是否相似

Args:
content1: 第一个内容
content2: 第二个内容
similarity_threshold: 相似度阈值

Returns:
bool: 是否相似
"""
# 简单的相似度检查：基于关键词匹配
words1 = set(content1.lower().split())
words2 = set(content2.lower().split())

if len(words1) == 0 or len(words2) == 0:
return False

# 计算Jaccard相似度
intersection = len(words1.intersection(words2))
union = len(words1.union(words2))
similarity = intersection / union if union > 0 else 0

# 如果内容完全一致，直接返回True
if content1.strip() == content2.strip():
return True

return similarity >= similarity_threshold

def checkfile_duplicates(self, category: Category, content: str) -> bool:
"""
检查文件中是否已存在相似记录（更全面的检查）

Args:
category: 记录分类
content: 记录内容

Returns:
bool: 是否存在重复
"""
file_path = self.base_dir / category.file_path

if not file_path.exists():
return False

try:
with open(file_path, 'r', encoding='utf-8') as f:
file_content = f.read()

# 使用正则表达式提取所有历史记录的内容
import re
# 匹配每个记录的内容部分（## 时间戳 - 任务ID 后面的内容）
pattern = r'## .*?\n\n(.*?)\n\n---\n\n'
existing_records = re.findall(pattern, file_content, re.DOTALL)

# 检查每个历史记录是否与新内容相似
for existing_content in existing_records:
if self._is_similar_content(existing_content.strip(), content.strip(), similarity_threshold=0.7):
return True

# 额外的安全检查：如果内容完全包含在文件中
if content.strip() in file_content:
return True

except Exception as e:
print(f"⚠️ 检查文件重复记录失败: {e}")

return False

def organize_best_practices(self, similarity_threshold: float = 0.8) -> Dict[str, int]:
"""
一键整理最佳实践，删除重复和相似的内容

Args:
similarity_threshold: 相似度阈值

Returns:
Dict[str, int]: 整理结果统计
"""
stats = {
'total_categories': 0,
'processed_files': 0,
'removed_duplicates': 0,
'remaining_records': 0
}

print("🔧 开始整理最佳实践...")

for category in Category:
file_path = self.base_dir / category.file_path

if not file_path.exists():
continue

stats['total_categories'] += 1

try:
with open(file_path, 'r', encoding='utf-8') as f:
content = f.read()

# 提取所有记录
import re
pattern = r'(## .*?\n\n.*?\n\n---\n\n)'
all_records = re.findall(pattern, content, re.DOTALL)

if not all_records:
continue

stats['processed_files'] += 1
print(f"📁 处理 {category.description}: 共 {len(all_records)} 条记录")

# 去重处理
unique_records = []
seen_contents = set()

for record in all_records:
# 提取内容部分
content_match = re.search(r'## .*?\n\n(.*?)\n\n---\n\n', record, re.DOTALL)
if not content_match:
continue

record_content = content_match.group(1).strip()

# 检查是否已存在相似内容
is_duplicate = False
for seen_content in seen_contents:
if self._is_similar_content(seen_content, record_content, similarity_threshold):
is_duplicate = True
stats['removed_duplicates'] += 1
break

if not is_duplicate:
unique_records.append(record)
seen_contents.add(record_content)

# 重新写入去重后的内容
if unique_records:
# 保留文件头
header_match = re.match(r'(#.*?\n\n---\n\n)', content, re.DOTALL)
if header_match:
new_content = header_match.group(1)
else:
new_content = self._get_file_header(category.file_path)

new_content += ''.join(unique_records)

with open(file_path, 'w', encoding='utf-8') as f:
f.write(new_content)

stats['remaining_records'] += len(unique_records)
print(f"✅ {category.description}: 保留 {len(unique_records)} 条，删除 {len(all_records) - len(unique_records)} 条重复记录")
else:
print(f"⚠️ {category.description}: 无有效记录")

except Exception as e:
print(f"❌ 整理 {category.description} 失败: {e}")

print(f"\n📊 整理完成统计:")
print(f" 处理分类数: {stats['total_categories']}")
print(f" 处理文件数: {stats['processed_files']}")
print(f" 删除重复记录: {stats['removed_duplicates']}")
print(f" 剩余记录数: {stats['remaining_records']}")

return stats

def record_environment_issue(self, issue: str, solution: str) -> None:
"""记录环境配置问题"""
content = f"**问题**: {issue}\n\n**解决方案**: {solution}"
self.record(Category.ENVIRONMENT, content)

def record_command_issue(self, command: str, issue: str, correct_usage: str) -> None:
"""记录指令使用问题"""
content = f"**错误指令**: `{command}`\n\n**问题**: {issue}\n\n**正确用法**: `{correct_usage}`"
self.record(Category.COMMANDS, content)

def record_parameter_issue(self, parameter: str, issue: str, correct_value: str) -> None:
"""记录参数设置问题"""
content = f"**参数**: `{parameter}`\n\n**问题**: {issue}\n\n**正确值**: `{correct_value}`"
self.record(Category.PARAMETERS, content)

def checkpython_environment(self) -> bool:
"""
检查Python环境是否满足记录要求

Returns:
bool: 环境是否满足
"""
try:
# 检查必要的Python模块
import sys
import json
import datetime
import re
from pathlib import Path

# 检查Python版本
if sys.version_info < (3, 6):
print(f"⚠️ Python版本过低: {sys.version}")
return False

# 检查文件写入权限
test_dir = Path(".autobizdevops") / "references" / "autodev-utest"
test_dir.mkdir(parents=True, exist_ok=True)

# 测试文件写入
test_file = test_dir / "test_write_permission.txt"
try:
with open(test_file, 'w', encoding='utf-8') as f:
f.write("test")
test_file.unlink() # 删除测试文件
except Exception as e:
print(f"⚠️ 文件写入权限检查失败: {e}")
return False

print("✅ Python环境检查通过")
return True

except Exception as e:
print(f"⚠️ Python环境检查失败: {e}")
return False

def record_best_practice(self, practice: str, description: str, example: str) -> None:
"""
记录最佳实践（自动检查环境并记录）

Args:
practice: 实践名称
description: 实践描述
example: 实践示例
"""
# 检查Python环境是否满足
if not self._check_python_environment():
print("❌ 环境不满足，跳过最佳实践记录")
return

# 直接记录最佳实践
content = f"**实践**: {practice}\n\n**描述**: {description}\n\n**示例**: ```python\n{example}\n```"
self.record(Category.BEST_PRACTICES, content)
print(f"✅ 最佳实践 '{practice}' 已记录")

def addto_pending_confirmation(self, practice: str, description: str, example: str) -> None:
"""添加最佳实践到待确认列表"""
pending_file = self.base_dir / "pending" / "best-practices-pending.json"
pending_file.parent.mkdir(parents=True, exist_ok=True)

pending_item = {
"practice": practice,
"description": description,
"example": example,
"timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
"task_id": self.task_id
}

# 读取现有的待确认列表
pending_list = []
if pending_file.exists():
try:
with open(pending_file, 'r', encoding='utf-8') as f:
pending_list = json.load(f)
except:
pending_list = []

# 添加新项目
pending_list.append(pending_item)

# 保存到文件
with open(pending_file, 'w', encoding='utf-8') as f:
json.dump(pending_list, f, ensure_ascii=False, indent=2)

print(f"📋 最佳实践 '{practice}' 已添加到待确认列表")
print(f"位置: {pending_file}")

def confirm_best_practice(self, practice: str) -> bool:
"""
确认并保存最佳实践

Args:
practice: 要确认的实践名称

Returns:
bool: 确认是否成功
"""
pending_file = self.base_dir / "pending" / "best-practices-pending.json"

if not pending_file.exists():
print("❌ 没有找到待确认的最佳实践")
return False

try:
with open(pending_file, 'r', encoding='utf-8') as f:
pending_list = json.load(f)
except:
print("❌ 读取待确认列表失败")
return False

# 查找匹配的实践
found_item = None
remaining_items = []

for item in pending_list:
if item["practice"] == practice:
found_item = item
else:
remaining_items.append(item)

if not found_item:
print(f"❌ 未找到名为 '{practice}' 的待确认实践")
return False

# 保存确认的实践
content = f"**实践**: {found_item['practice']}\n\n**描述**: {found_item['description']}\n\n**示例**: ```python\n{found_item['example']}\n```"
self.record(Category.BEST_PRACTICES, content)

# 更新待确认列表
with open(pending_file, 'w', encoding='utf-8') as f:
json.dump(remaining_items, f, ensure_ascii=False, indent=2)

print(f"✅ 最佳实践 '{practice}' 已确认并保存")
return True

def list_pending_practices(self) -> List[Dict]:
"""列出所有待确认的最佳实践"""
pending_file = self.base_dir / "pending" / "best-practices-pending.json"

if not pending_file.exists():
return []

try:
with open(pending_file, 'r', encoding='utf-8') as f:
return json.load(f)
except:
return []

def record_tool_framework(self, tool: str, experience: str, recommendation: str) -> None:
"""记录工具框架使用经验"""
content = f"**工具/框架**: {tool}\n\n**经验**: {experience}\n\n**推荐**: {recommendation}"
self.record(Category.TOOLS_FRAMEWORKS, content)

def log_step(self, step_name: str) -> None:
"""记录执行步骤"""
log_entry = f"步骤: {step_name}"
self.execution_log.append(log_entry)
print(log_entry)

def save_all_records(self) -> None:
"""保存所有记录到文件"""
self._ensure_base_directory_exists()

for category, records in self.records.items():
if records:
self._save_to_file(category.file_path, records)

# 保存技能配置
self._save_skill_config()

print("✓ 最佳实践记录完成")
print(f"记录位置: {self.base_dir}")

def ensurebase_directory_exists(self) -> None:
"""确保基础目录存在"""
directories = [
self.base_dir / "pitfalls",
self.base_dir / "guidelines",
self.base_dir / "config"
]

for directory in directories:
directory.mkdir(parents=True, exist_ok=True)

def saveto_file(self, relative_path: str, records: List[str]) -> None:
"""保存内容到文件（追加模式）"""
full_path = self.base_dir / relative_path

try:
# 如果文件不存在，先写入文件头
if not full_path.exists():
with open(full_path, 'w', encoding='utf-8') as f:
f.write(self._get_file_header(relative_path))

# 追加内容到文件
with open(full_path, 'a', encoding='utf-8') as f:
for record in records:
f.write(record)

print(f"✓ 记录已保存到: {full_path}")
except Exception as e:
print(f"✗ 保存文件失败: {full_path} - {e}")

def getfile_header(self, file_path: str) -> str:
"""获取文件头信息"""
category_desc = self._get_category_description(file_path)
timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

header = f"# 最佳实践记录 - {category_desc}\n\n"
header += f"**技能名称**: {self.skill_name}\n\n"
header += f"**记录时间**: {timestamp}\n\n"
header += "---\n\n"
return header

def getcategory_description(self, file_path: str) -> str:
"""根据文件路径获取分类描述"""
for category in Category:
if category.file_path == file_path:
return category.description
return "未知分类"

def saveskill_config(self) -> None:
"""保存技能配置"""
config = {
"skillName": self.skill_name,
"lastUpdated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
"taskId": self.task_id,
"config": {
"incrementalTesting": True,
"sourceCodeProtection": True,
"maxIterationTime": 300,
"testPriority": ["public", "boundary", "exception", "special"]
}
}

config_content = json.dumps(config, ensure_ascii=False, indent=2)
self._save_to_file(Category.SKILL_CONFIG.file_path, [config_content])

@classmethod
def read_best_practice(cls, category: Category) -> str:
"""读取历史最佳实践"""
base_dir = Path(".autobizdevops") / "references" / "autodev-utest"
full_path = base_dir / category.file_path

try:
if full_path.exists():
with open(full_path, 'r', encoding='utf-8') as f:
return f.read()
except Exception as e:
print(f"✗ 读取最佳实践文件失败: {full_path} - {e}")

return "暂无历史记录"

@classmethod
def read_references(cls, max_file_size: int = 1024 * 1024) -> Dict[str, str]:
"""
读取项目references文件夹中的最佳实践

Args:
max_file_size: 最大文件大小（字节），默认1MB

Returns:
Dict[str, str]: 文件名到文件内容的映射
"""
# 查找项目根目录的references文件夹
# 从当前目录向上查找，直到找到包含references文件夹的目录
current_dir = Path.cwd()
project_root = None

# 向上查找项目根目录（包含references文件夹的目录）
for parent in [current_dir] + list(current_dir.parents):
references_dir = parent / "references"
if references_dir.exists():
project_root = parent
break

references = {}

if project_root:
references_dir = project_root / "references"
if references_dir.exists():
for file_path in references_dir.glob("*.*"):
if file_path.suffix in ['.md', '.json']:
try:
# 检查文件大小
file_size = file_path.stat().st_size
if file_size > max_file_size:
print(f"⚠️ 文件过大，跳过: {file_path.name} ({file_size} 字节)")
continue

with open(file_path, 'r', encoding='utf-8') as f:
references[file_path.stem] = f.read()

print(f"✓ 已读取参考文件: {file_path.name}")
except Exception as e:
print(f"✗ 读取参考文件失败: {file_path} - {e}")

print(f"✓ 共读取 {len(references)} 个参考文件")
return references

def auto_categorize_and_record(self, content: str) -> Category:
"""
自动分类并记录最佳实践

Args:
content: 需要记录的内容

Returns:
Category: 自动选择的分类
"""
content_lower = content.lower()

# 基于关键词自动分类
if any(keyword in content_lower for keyword in ["java", "maven", "gradle", "jdk", "环境", "配置"]):
category = Category.ENVIRONMENT
elif any(keyword in content_lower for keyword in ["mvn", "test", "命令", "执行", "compile"]):
category = Category.COMMANDS
elif any(keyword in content_lower for keyword in ["参数", "选项", "设置", "配置项"]):
category = Category.PARAMETERS
elif any(keyword in content_lower for keyword in ["实践", "经验", "技巧", "最佳"]):
category = Category.BEST_PRACTICES
elif any(keyword in content_lower for keyword in ["junit", "mockito", "框架", "工具", "library"]):
category = Category.TOOLS_FRAMEWORKS
else:
category = Category.BEST_PRACTICES # 默认分类

self.record(category, content)
return category


def auto_summarize_after_unit_test(self, test_results: Dict) -> None:
"""
在单元测试完成后记录相关经验

Args:
test_results: 测试结果字典，包含测试统计信息
"""
# 记录测试结果相关的经验
if test_results.get("total_tests", 0) > 0:
success_rate = (test_results.get("passed", 0) / test_results.get("total_tests", 1)) * 100
self.record_best_practice(
"测试覆盖率分析",
f"本次测试通过率: {success_rate:.1f}%",
f"总测试数: {test_results.get('total_tests', 0)}, 通过: {test_results.get('passed', 0)}, 失败: {test_results.get('failed', 0)}"
)

if test_results.get("execution_time"):
self.record_best_practice(
"测试执行时间优化",
f"总执行时间: {test_results.get('execution_time')}秒",
"建议使用增量测试减少执行时间"
)

# 根据测试结果生成建议
suggestions = []
if test_results.get("failed", 0) > 0:
suggestions.append("重点关注失败的测试用例，分析失败原因")

if test_results.get("total_tests", 0) < 10:
suggestions.append("建议增加测试用例覆盖更多场景")

if suggestions:
suggestion_content = "测试完成后建议：\n" + "\n".join(f"- {s}" for s in suggestions)
self.auto_categorize_and_record(suggestion_content)

print("✓ 测试结果经验记录完成")

@classmethod
def has_historical_records(cls) -> bool:
"""检查是否有历史最佳实践记录"""
base_dir = Path(".autobizdevops") / "references" / "autodev-utest"
return base_dir.exists() and any(base_dir.iterdir())

def main():
"""演示最佳实践记录系统的使用"""
print("=== 最佳实践记录系统演示（Java单元测试版）===\n")

# 创建记录器
recorder = BestPracticeRecorder("unit-test-gen", "demo-java-001")

# 记录各种经验
recorder.record_environment_issue(
"Java版本冲突",
"解决方案：使用Maven或Gradle管理Java版本"
)

recorder.record_command_issue(
"mvn test",
"问题：一次性运行所有测试导致反馈延迟",
"正确用法：mvn test -Dtest=TestClass#testMethod"
)

recorder.record_best_practice(
"增量测试",
"每次只测试一个场景，确保快速反馈",
"@Test\npublic void testFindByIdWhenIdIsNull() {\n // 单个测试场景\n}"
)

recorder.record_tool_framework(
"JUnit",
"使用JUnit框架进行单元测试",
"推荐使用@BeforeEach和@AfterEach方法管理测试环境"
)

# 使用自动分类功能
recorder.auto_categorize_and_record("在测试过程中发现Mock对象注入问题，需要检查@InjectMocks注解的使用")

# 记录执行步骤
recorder.log_step("环境检测")
recorder.log_step("依赖检查")
recorder.log_step("测试执行")
recorder.log_step("结果分析")

# 模拟单元测试结果
test_results = {
"total_tests": 15,
"passed": 12,
"failed": 3,
"execution_time": 45
}

# 记录测试结果经验（不生成总结报告）
print("\n=== 记录测试结果经验 ===")
recorder.auto_summarize_after_unit_test(test_results)

# 保存所有记录
recorder.save_all_records()

print("\n=== 演示完成 ===")
if name == "__main__":
main()

