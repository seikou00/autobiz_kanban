# UI_CONTEXT.json Reference

`UI_CONTEXT.json` is the machine fact source for UI scope. Write it before
leaving Biz stages, and use this reference before creating or editing the file.
Do not infer UI scope from Markdown after this file exists.

## Required Root Fields

- `version`: must be `1`.
- `featureId`: current feature slug.
- `uiRequired`: boolean.
- `decisionStatus`: one of `defaulted`, `confirmed`, `locked`.
- `decisionSource`: one of `user_confirmed`, `prd_inferred`, `default_false`, `legacy_import`.
- `notApplicableReason`: required when `uiRequired=false`.
- `pages`: array.
- `interactions`: array.
- `visualSources`: array.
- `capabilities`: array.

Optional checkpoint fields:

- `confirmedAtCheckpoint`: non-empty string when known, for example `discuss_done` or `prd_done`.
- `lockedAtCheckpoint`: non-empty string when specs lock the UI decision.

## ID Formats

- Page id: `PAGE-001`, `PAGE-002`, ...
- Interaction id: `UIX-001`, `UIX-002`, ...
- Visual source id: `VIS-001`, `VIS-002`, ...
- Capability id: lowercase kebab-case, for example `order-create-ui`.

Do not create `capabilities[].specRefs` in discuss or PRD. REQ/SCN ids are
defined by specs and locked later.

## Non-UI Template

```json
{
  "version": 1,
  "featureId": "{feature}",
  "uiRequired": false,
  "decisionStatus": "defaulted",
  "decisionSource": "default_false",
  "notApplicableReason": "未确认存在 UI 范围",
  "pages": [],
  "interactions": [],
  "visualSources": [],
  "capabilities": []
}
```

When the user confirms this is a non-UI feature, set:

- `decisionStatus` to `confirmed`
- `decisionSource` to `user_confirmed`
- `confirmedAtCheckpoint` to the current done checkpoint
- `notApplicableReason` to the confirmed reason

## UI Template

```json
{
  "version": 1,
  "featureId": "{feature}",
  "uiRequired": true,
  "decisionStatus": "confirmed",
  "decisionSource": "user_confirmed",
  "confirmedAtCheckpoint": "prd_done",
  "notApplicableReason": "",
  "pages": [
    {
      "pageId": "PAGE-001",
      "name": "页面名称",
      "goal": "页面目标",
      "states": ["loading", "empty", "error", "success"]
    }
  ],
  "interactions": [
    {
      "interactionId": "UIX-001",
      "pageId": "PAGE-001",
      "summary": "用户执行的核心交互"
    }
  ],
  "visualSources": [],
  "capabilities": []
}
```

## Visual Sources

Use `visualSources[]` only for design inputs such as high-fidelity HTML,
standard HTML, design links, prototypes, images, or other visual references.
Do not paste HTML or design links into PRD text as requirements.

Allowed `type` values:

- `high_fidelity_html`
- `standard_html`
- `design_link`
- `prototype_link`
- `image`
- `other`

Allowed `route` values for visual sources:

- `absolute-html`
- `standard-html`
- `spec-driven-ui`
- `missing-html`

High-fidelity HTML that is known but not yet provided should still be recorded
as a traceable visual source:

```json
{
  "sourceId": "VIS-001",
  "type": "high_fidelity_html",
  "path": "frontend-html/<待提供>.html",
  "route": "absolute-html",
  "required": true
}
```

The code stage will ask the user for the file first. If the user does not
provide it, code falls back to `spec-driven-ui` and continues without blocking.
