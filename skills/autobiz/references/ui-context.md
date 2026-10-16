# UI_CONTEXT.json Reference

`UI_CONTEXT.json` is the machine fact source for UI scope. Write it before
leaving Biz stages, and use this reference before creating or editing the file.
Do not infer UI scope from Markdown after this file exists.

## UI_CONTEXT.md Auto-Generation

When `UI_CONTEXT.json` is written or updated, a human-readable `UI_CONTEXT.md`
is automatically generated in the same directory. This Markdown file:

- When `uiRequired=true`, focuses on frontend developer concerns: pages, interactions, visual sources, and restoration paths
- When `uiRequired=false`, shows the UI decision, decision source/status, and `notApplicableReason`, and omits empty frontend tables
- Before Plan exists, leads with one PAGE / UIX / VIS table built from capability `pageRefs`, `interactionRefs`, and `visualSourceRefs`; it does not render TASK columns or Task placeholders
- Before Specs is finalized, unbound UIX/VIS scope uses `待specs阶段归属`; after `specs_done`, any still-unbound scope uses `未明确页面归属`
- After formal Plan exists, replaces that table with one TASK / PAGE / UIX / VIS mapping table using Plan `uiRefs` and real Task IDs/titles
- Uses PAGE as the primary dimension, follows `pages[]` declaration order, and aggregates each PAGE's related values
- Orders pre-Plan columns as PAGE → VIS → UIX and formal-Plan columns as PAGE → TASK → VIS → UIX; every ID stays separate from its name
- PAGE displays only `name`, VIS displays only `path`, UIX displays `summary` as its interaction name, and TASK displays `title`
- Omits capabilities whose effective PAGE/UIX/VIS scope is entirely empty; after Plan exists, preserves meaningful unbound scope and unresolved Task ownership explicitly
- Omits the association-evidence column and internal `capability`, `specRefs`, and Plan `uiRefs` details from all Markdown mapping tables; those references remain available for association and validation in the machine-readable sources
- Keeps PAGE and visual-source data in the primary mapping table; no supplementary visual-source or page-definition tables are generated
- Appends one restoration-route guide after the mapping table
- The generated mapping table is a read-only projection; `sync-from-md` only supports legacy list sections, not these generated tables or derived task bindings
- Serves as a quick reference for frontend teams to review UI scope and implementation strategy

**Synchronization**:
- JSON → MD: automatic (on every JSON write)
- Specs → MD: mandatory final pre-Plan refresh before `specs_done`; checkpoint completion is blocked if the Markdown projection cannot be written
- Plan → MD: automatic (on every formal Plan write, to refresh TASK/PAGE/UIX/VIS mappings)
- Legacy MD list sections → JSON: manual (using `python -X utf8 hooks/ui_context_writer.py sync-from-md --feature <feature>`); edit generated mappings through the source JSON/Plan writers instead

See `docs/ui-context-md-generation.md` for detailed documentation.

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
standard HTML, design links, images, or other visual references.
Prototype links are navigation references rather than restorable visual inputs;
do not record them in `visualSources[]`.
Do not paste HTML or design links into PRD text as requirements.

Allowed `type` values:

- `high_fidelity_html`
- `standard_html`
- `design_link`
- `image`
- `other`

Allowed `route` values for visual sources:

- `absolute-html`
- `standard-html`
- `spec-driven-ui`
- `missing-html`

When the user provides a high-fidelity HTML file, archive it into the Feature
artifact directory before leaving the discussion stage. Use the writer's
`--source-file` mode; do not persist an absolute local path as the stable
source. A bundle with local assets can be archived with `--source-root`:

```bash
python -X utf8 "${pluginPath}/hooks/ui_context_writer.py" add-visual-source \
  --feature "${feature}" \
  --type high_fidelity_html \
  --source-file "<HTML_PATH>" \
  --source-root "<HTML_BUNDLE_ROOT>" \
  --route absolute-html \
  --required true
```

The writer stores the entry under `frontend-html/VIS-xxx/`, records content
and bundle SHA-256 values, and later stages must use the generated `VIS-xxx`.

High-fidelity HTML that is known but not yet provided may be recorded as a
traceable required source. While its file is missing, the referencing UI Task
runs on the `spec-driven-ui` fallback instead of blocking Code: the resolver
records the gap in `requiredVisualSourceMissing` and sets
`requiredVisualSourceFallbackRoute=spec-driven-ui`, so the missing input stays
visible in the evidence and in the unified frontend re-check instead of being
silently treated as satisfied. Archive the file as soon as it is available:

```json
{
  "sourceId": "VIS-001",
  "type": "high_fidelity_html",
  "path": "frontend-html/<待提供>.html",
  "route": "absolute-html",
  "required": true
}
```

If a UI capability does not require high-fidelity input, leave its
`visualSourceRefs` empty and use `spec-driven-ui`; this is valid even when
another capability in the same Feature uses a required `VIS-xxx`.

## Capability to Visual Source Binding

`capabilities[]` owns the requirement-to-UI binding. Its optional
`visualSourceRefs` must contain only existing `VIS-xxx` IDs:

```json
{
  "capabilityId": "order-create-ui",
  "uiRequired": true,
  "pageRefs": ["PAGE-001"],
  "interactionRefs": ["UIX-001"],
  "visualSourceRefs": ["VIS-001"],
  "specRefs": [
    "specs/order-create/spec.md#REQ-001",
    "specs/order-create/spec.md#SCN-001"
  ]
}
```

An ordinary UI capability without a high-fidelity source uses
`"visualSourceRefs": []`; it must not be forced to reference a placeholder
HTML file.
