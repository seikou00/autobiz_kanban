# Specs Review

来源: critic-autodev 回检结论 + 主 agent 逐条复核
创建时间: [ISO 日期时间]

## Verdict

[PASS / PASS_WITH_WARNINGS / FAIL / DEGRADED，只写一个词]

## Review Baseline

五个必查项一项都不能省。没查过就不要写「通过」——证据列空着的「通过」不构成回检。

| 必查项 | 结论 | 证据 |
|--------|------|------|
| 需求覆盖 | [通过/发现问题/不适用] | [PRD 功能点与 REQ/SCN 的对应，或缺口所在] |
| 实现范围符合性 | [通过/发现问题/不适用] | [IMPLEMENTATION_SCOPE.json 的范围与逐条 Scenario 的核对结果] |
| 操作分类与代码事实 | [通过/发现问题/不适用] | [New 组各项 `**Existing:**` 断言的实际核对结果] |
| 上游资料引用 | [通过/发现问题/不适用] | [SRC-NNN 与 targets 含 spec 的要求落位情况] |
| 待确认项消解 | [通过/发现问题/不适用] | [Open Questions 各行 Status 与消解依据] |

## Findings

Critical / Major 每条都必须有分类与处置。本轮无结论时整表删掉，正文写「无」。

| ID | 来源 | 原文严重度 | 结论 | 证据 | 分类 | 处置 |
|----|------|-----------|------|------|------|------|
| F-001 | [critic-autodev] | [Critical/Major/Minor，角色原样输出的词] | [一句话] | [file:line 或产物原文] | [产物可修/需用户裁定/回流上游/仅列出/结论不成立] | [落到哪个产物的哪一条，或不改的依据] |

## Unresolved

仍需用户裁定、尚未拿到答复的条目。有条目就不能推进 `specs_done`。

- [未裁定条目；无则写「无」]
