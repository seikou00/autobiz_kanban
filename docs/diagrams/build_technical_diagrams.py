"""Render separate component and Code execution views for the proposal."""

from pathlib import Path
import math

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'output/pdf/autobiz-technical-architecture.pdf'
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('CN', '/System/Library/Fonts/STHeiti Light.ttc', subfontIndex=0))
pdfmetrics.registerFont(TTFont('CNB', '/System/Library/Fonts/STHeiti Medium.ttc', subfontIndex=0))
W, H = 1200, 850
c = canvas.Canvas(str(OUTPUT), pagesize=(W, H))
c.setTitle('AutoBizDevOps 总体技术架构与 Code 阶段执行流程')
c.setAuthor('AutoBizDevOps')
INK, MUTED, BLUE, TEAL = '#142B47', '#51647A', '#215A96', '#086B64'


def text(x, y, value, size=14, color=INK, bold=False):
    c.setFont('CNB' if bold else 'CN', size)
    c.setFillColor(HexColor(color))
    c.drawString(x, H-y, value)


def box(x, y, w, h, fill='#FFFFFF', border='#CCD7E3', radius=10):
    c.setFillColor(HexColor(fill))
    c.setStrokeColor(HexColor(border))
    c.setLineWidth(1)
    c.roundRect(x, H-y-h, w, h, radius, fill=1, stroke=1)


def path(points, color='#6381A2', dashed=False, tip=True):
    c.setStrokeColor(HexColor(color))
    c.setFillColor(HexColor(color))
    c.setLineWidth(1.6)
    if dashed:
        c.setDash(5, 4)
    p = c.beginPath()
    p.moveTo(points[0][0], H-points[0][1])
    for x, y in points[1:]:
        p.lineTo(x, H-y)
    c.drawPath(p, stroke=1, fill=0)
    c.setDash()
    if tip:
        x1, y1 = points[-2]
        x2, y2 = points[-1]
        a = math.atan2(y2-y1, x2-x1)
        p = c.beginPath()
        p.moveTo(x2, H-y2)
        for side in [-1, 1]:
            p.lineTo(x2-7*math.cos(a)+side*3*math.sin(a), H-(y2-7*math.sin(a)-side*3*math.cos(a)))
        p.close()
        c.drawPath(p, fill=1, stroke=0)


def page(title, subtitle, index):
    c.setFillColor(HexColor('#F5F8FC'))
    c.rect(0, 0, W, H, fill=1, stroke=0)
    text(40, 49, title, 27, bold=True)
    text(41, 79, subtitle, 15, MUTED)
    text(958, 46, 'AutoBizDevOps', 16, BLUE, True)
    text(958, 71, f'代码依据 0461924   |   {index} / 2', 11, MUTED)


def component_row(y, label, items, height=90, divider=True):
    """Render a group inside one continuous plugin boundary.

    The group labels and cards are separated with subtle rules only.  The
    surrounding plugin boundary remains the sole container, preventing the
    component view from being visually read as several disconnected boxes.
    """
    if divider:
        c.setStrokeColor(HexColor('#DCE5EF'))
        c.setLineWidth(0.8)
        c.line(60, H-y, 1140, H-y)
    text(78, y+34, label, 17, BLUE, True)
    n = len(items)
    gap = 12
    width = (922-(n-1)*gap)/n
    for i, (title, lines) in enumerate(items):
        x = 204+i*(width+gap)
        box(x, y+10, width, height-20, '#F8FAFD', '#E3EAF2', 7)
        text(x+13, y+34, title, 15, INK, True)
        for j, line in enumerate(lines):
            text(x+13, y+56+j*18, line, 12, MUTED)


page('图 1  总体技术架构与实现边界', '按组件职责分组，展示插件组成及其与宿主、外部系统的依赖关系', 1)
box(40, 102, 1120, 64, '#E9EFF7', '#D0DCE9')
text(60, 128, '宿主 AI Coding 平台', 17, BLUE, True)
text(325, 128, '大模型调用  /  工具执行  /  代理运行  /  看板交互', 15, MUTED)
text(60, 152, '宿主提供运行环境，加载插件配置并调用插件入口。', 13, MUTED)
path([(600, 168), (600, 194)])

box(40, 195, 1120, 476, '#FFFFFF', '#95ACC7', 13)
text(60, 222, '插件实现边界', 18, BLUE, True)
text(241, 222, '组件视图  |  Python 运行内核 + JavaScript 工作流 + 配置与文件产物', 13, MUTED)
component_row(244, '接入与适配', [
    ('插件声明与入口', ['plugin.json / 状态查询']),
    ('技能与角色配置', ['Skills / 角色职责与协议']),
    ('工具调用钩子', ['Hooks / 上下文与行为检查']),
    ('外部服务接入', ['MCP 配置 / 交付适配']),
], 80, divider=False)
component_row(329, '编排与控制', [
    ('流程编译与状态管理', ['节点配置 / 标准与精简路线', '阶段迁移 / 统一状态更新']),
    ('契约与执行约束', ['规格引用 / 设计锁 / 任务范围', '产物校验 / 写入检查']),
    ('依赖调度与运行恢复', ['DAG / 槽位 / 租约', '检查点 / 回退与恢复']),
], 97)
component_row(438, '执行与支撑', [
    ('技能任务与角色协作', ['需求 / 设计 / 编码 / 评审 / 测试', '代理执行能力由宿主提供']),
    ('代码集成与验证', ['Worktree / 候选合并 / E2E', 'Code 阶段运行过程见图 2']),
    ('证据与知识服务', ['证据写入 / 审计 / 一致性校验', '知识筛选 / 上下文 / 来源追踪']),
], 97)
component_row(547, '状态与产物', [
    ('状态与计划', ['state.json / plan.json']),
    ('需求与设计', ['PRD / specs / 设计锁']),
    ('运行与证据', ['manifest / JSONL / 日志']),
    ('知识与来源', ['上下文 / 来源记录 / 快照']),
], 100)

path([(600, 673), (600, 710)])
text(615, 698, '工具访问与交付对接', 12, MUTED)
box(40, 713, 1120, 82, '#E9EFF7', '#D0DCE9')
text(60, 743, '外部工具与平台', 17, BLUE, True)
text(303, 743, 'Git 与业务代码仓库  /  企业知识库  /  MCP 服务  /  CI/CD 平台', 15, MUTED)
text(60, 775, '上述系统提供版本管理、知识来源及发布能力，其运行环境位于插件边界之外。', 13, MUTED)
text(41, 826, '图注：框内按组件职责组织；箭头表示接入或访问关系。Code 阶段的执行顺序单独见图 2。', 12, MUTED)
c.showPage()

page('图 2  Code 阶段执行流程', '独立运行视图，展示任务调度、批次并发、集成验证及状态反馈', 2)
box(40, 103, 1120, 72, '#EFF5FF', '#CEDFF4')
text(60, 132, '入口预检与运行准备', 17, BLUE, True)
text(328, 132, '当前流程契约  /  执行计划  /  代码工作区  /  运行状态', 15, MUTED)
text(60, 158, '依据输入及已有运行状态创建或恢复执行；按当前路线检查适用产物。', 13, MUTED)
path([(135, 177), (135, 274)])
text(47, 218, '依赖已满足', 12, MUTED)
text(47, 240, '且存在空闲槽位', 12, MUTED)

box(40, 274, 190, 110, '#EFF5FF', '#CEDFF4')
text(59, 306, 'DAG 动态调度', 18, BLUE, True)
text(59, 334, '依赖判断 / 容量控制', 13, MUTED)
text(59, 359, '领取任务 / 运行租约', 13, MUTED)

box(270, 204, 570, 301, '#FFFFFF', '#A8CEC7', 13)
text(288, 231, '并行批次：独立 Git Worktree', 17, TEAL, True)
text(288, 252, '批次之间可并行；每个批次按实现、评审、单测顺序推进', 12, MUTED)
ys = [272, 348, 424]
for i, y in enumerate(ys):
    for j, title in enumerate([f'批次 {chr(65+i)} 实现', 'Review', 'UTest']):
        x = 289+j*180
        box(x, y, 154, 53, '#EAF7F4', '#BDDDD6', 8)
        text(x+14, y+32, title, 16, TEAL, True)
        if j < 2:
            path([(x+156, y+27), (x+177, y+27)], '#388F86')
path([(230, 329), (250, 329)], tip=False)
path([(250, 299), (250, 451)], tip=False)
for y in ys:
    path([(250, y+27), (286, y+27)])
    path([(805, y+27), (863, y+27)], '#388F86', tip=False)
path([(863, 299), (863, 451)], '#388F86', tip=False)
path([(863, 375), (882, 375)], '#388F86')

box(886, 315, 274, 108, '#EAF7F4', '#BDDDD6')
text(904, 348, 'Merge Train 候选合并', 18, TEAL, True)
text(904, 376, '基线检查 / 合并 / 快进推广', 13, MUTED)
text(904, 401, '记录合并结果与恢复检查点', 13, MUTED)
text(905, 272, '批次完成交付检查后', 13, MUTED)
text(905, 294, '即可进入候选合并', 13, MUTED)

path([(1023, 425), (1023, 548), (135, 548), (135, 387)], '#578393', True)
box(321, 530, 541, 35, '#F5F8FC', '#F5F8FC', 0)
text(332, 553, '已合并状态回传，释放后续依赖并触发动态补位', 14, TEAL, True)

path([(1142, 425), (1142, 589), (173, 589), (173, 608)])
text(448, 582, '全部交付批次合并后，进入最终验证', 13, MUTED)
finals = [(40, 265, '交付批次已合并', '汇总当前代码与依赖状态'),
          (349, 245, '合并后 E2E', '验证最终业务链路'),
          (638, 244, '证据聚合', '核验版本、摘要及问题记录'),
          (926, 234, '完成判断', '完成 / 带问题完成 / 阻断')]
for i, (x, width, title, sub) in enumerate(finals):
    box(x, 611, width, 79, '#EFF5FF', '#CEDFF4')
    text(x+16, 643, title, 18, BLUE, True)
    text(x+16, 671, sub, 12, MUTED)
    if i < len(finals)-1:
        path([(x+width+3, 650), (finals[i+1][0]-4, 650)])

box(40, 720, 1120, 78, '#FFF8E9', '#E9D8B3')
text(59, 746, '运行约束与异常处理', 16, '#866025', True)
text(59, 770, '关键阶段串行；复杂冲突转人工处理；延期测试或非阻塞问题保留记录，最终检查阻塞问题与证据一致性。', 13, MUTED)
text(41, 826, '图注：由图 1 的编排、执行与证据组件共同实现。实线表示执行推进，虚线表示调度反馈。', 12, MUTED)
c.showPage()
c.save()
print(OUTPUT)
