# 回检协议 · dev.code

## 前提与角色

本节发生在所有批次交付合并之后。此刻 Code runner 的实现流程已收口，已完成 TASK 不得重启；批次重入、旧逐 TASK 验证和项目检查入口均不可用。

使用 task 工具，先从 git 中获取本轮改动的代码，对照 `plan.json` 与 `design.md` 同时审查三个方面：

1. 使用 `Explore-autodev` 角色，逐 TASK 对照 `goal` / `specRefs` / `designRefs` 核对 diff：每个任务的改动是否兑现其引用的 REQ/SCN 行为与 API/DATA/D 形态，有无未覆盖的 `acceptanceCriteria`、有无越过 `scope` / `nonGoals` 的改动；
2. 使用 `code-reviewer-autodev` 角色，查看代码是否有不满足设计与需求的地方；
3. 使用 `code-simplifier-autodev` 角色，代码是否有冗余或不合理的地方。

`code-simplifier-autodev` 的默认契约是**直接改文件**（它的输出格式就是 `## Files Simplified` / `## Changes Applied`）。本阶段没有可用的改码通道，因此启动它时必须在 task prompt 中显式要求：只报告建议、给出 `file:line` 与替代写法，不要落笔修改任何文件。

**主 agent 禁止**：不得因回检结论修改任何业务源码、测试或配置；不得改写任何 `action=validation` evidence 或 `validationDisposition`；不得为回检启动新的 task run。

子代理若仍然改了文件（它有写权限，约束只在 prompt 层），不要自行还原——本阶段无法重验，静默还原可能覆盖用户自己的改动。改为在结论块中把「子代理已直接修改 <文件清单>，未经任何验证」作为一条 `仅列出` 结论如实记录，交由用户处置。

## 严重度词表

三个角色的词表不同，本阶段不做归一：

- `code-reviewer-autodev` 输出 CRITICAL / HIGH / MEDIUM / LOW（没有 MAJOR），并把低置信的高危项单列到它自己的 Open Questions；
- `Explore-autodev` 与 `code-simplifier-autodev` 没有严重度轴。

`原文严重度` 一律原样转录角色自己的词，无严重度轴时写 `无`。

角色的总评行（code-reviewer 的 `APPROVE` / `REQUEST CHANGES` / `COMMENT`）只是总评，不作为动作依据。即使总评是 `APPROVE`，下方逐条处理仍须完整完成。

## 逐条复核与分类

回检结论逐条处理：先用原文复核该条是否成立，再按下表定动作。本阶段不改代码，所有分类的动作都只是记录与交接。

| 分类 | 判定 | 动作 |
|------|------|------|
| 交接下游 | 实现与引用的行为或已定形态不符、缺失、越界改动，或冗余需整理 | 记入结论块，`处置` 写具体交接阶段 `dev.review` / `dev.utest` / `dev.e2e` |
| 需用户裁定 | 结论要求的做法与已定 REQ/SCN 或 API/DATA/D 冲突 | 按「实现差异协议」发起确认，不得先动代码 |
| 回流上游 | 行为契约本身缺失或矛盾 | 记入结论块并建议回 `/autodev-specs` 或 `/autodev-plan` |
| 仅列出 | 成立但不足以交接（风格类、低置信） | 记入结论块，不产生下游动作 |
| 结论不成立 | 复核后与 diff、产物实际不符 | 不交接，在 `处置` 中引 file:line 说明依据 |

## 产出义务

分类处置完成后，必须在回复中输出下面形状的块，每条结论一行：

```
【回检结论】
- 来源: <角色名> | 原文严重度: <角色原样输出的词，无严重度轴时写 无> | 结论: <一句话> | 证据: <file:line 或产物原文> | 分类: <上表五个取值之一> | 处置: <交接到哪个阶段，或不交接的依据>
```

- 每条结论都必须落到一个 `分类`，不允许留空或自造取值：`交接下游` | `需用户裁定` | `回流上游` | `仅列出` | `结论不成立`。
- 无结论时写：`【回检结论】本轮回检无结论`。

## 收口

本阶段不因回检产生产物修改，无需重跑验证。结论块输出后继续 Code 完成门禁。
