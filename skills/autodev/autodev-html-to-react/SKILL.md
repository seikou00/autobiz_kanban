---
name: html-to-react
description: Convert HTML, static pages, copied markup, or small static sites into a working React project or React components, including recognition of HTML UI patterns that should become Ant Design components. Use when Codex is asked to turn HTML code/files into React, JSX, TSX, a Vite/Next React app, componentized frontend code, or when migrating static HTML/CSS/JS into reusable React engineering code with built-in craft, extract, layout, and publish phases.
---

**路径变量约定（必须区分）：**
- **PLUGIN_ROOT**：插件代码根目录；调用插件脚本必须使用 `$PLUGIN_ROOT/...`。
- **PLUGIN_WORKSPACE**：项目集合工作区，不直接包含 `.autobizdevops/state.json`。
- **PROJECT_CODE**：当前项目目录名；`PROJECT_PLUGIN_DIR = {PLUGIN_WORKSPACE}/{PROJECT_CODE}`，必须包含 `.autobizdevops/state.json`。
- **FEATURE_ID**：当前 Feature 名称；状态脚本未显式传 `--feature` 时会使用它。
- **FEATURE_DIR**：当前 Feature 产物目录，固定为 `{PROJECT_PLUGIN_DIR}/.autobizdevops/features/{FEATURE_ID}`；只用于读写 PRD、proposal、specs、design、PLAN、报告等 Feature 产物，不得作为状态脚本路径来源。
- **CODE_WORKSPACE**：真实代码工作区根目录，包含业务代码、构建脚本和项目级 `AGENTS.md`；用于前端代码探索、实现和验证。

# HTML To React

## Overview

Use this skill to transform raw HTML into maintainable React code, not merely to replace `class` with `className`. Preserve the source intent, extract reusable structure, map product UI to Ant Design when appropriate, improve layout when requested, and deliver a runnable project or component set.

This skill is self-contained. Do not require or call another design skill to perform the conversion. Use these built-in quality phases:

- **craft**: shape the conversion target and implementation approach before coding.
- **extract**: identify reusable components, data arrays, props, and design tokens.
- **layout**: preserve or improve spatial hierarchy, responsiveness, and visual rhythm.
- **publish**: run the app, verify visually, and deliver the project with clear entry points.

## AutobizDevOps Workflow Integration

This skill is an alternative entry for the existing `frontend_before_specs` frontend node. It does not introduce a second frontend stage or new checkpoints. When invoked inside an AutobizDevOps Feature, it shares the same lifecycle as `autodev-frontend`:

- If the current checkpoint is `prd_done`, first enter the frontend node:

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint frontend_in_progress --workflow-profile frontend_before_specs
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

- If the current checkpoint is already `frontend_in_progress`, continue without changing the workflow profile.
- After implementation and verification, finish the shared frontend node:

```bash
python "$PLUGIN_ROOT/hooks/update_checkpoint.py" --checkpoint frontend_done
CHECKPOINT=$(python "$PLUGIN_ROOT/read_state_json.py" --feature "$FEATURE_ID")
```

- Report changed files, verification commands, unresolved risks, and prompt the user to continue with `/autodev-specs`.

Use this entry when the source is normal static HTML, copied DOM markup, semantic HTML/CSS/JS, a small static site, or the user explicitly asks for HTML to React/JSX/TSX/Vite/Next conversion. Do not use this entry for Figma/low-code high-fidelity HTML with absolute positioning, dense inline styles, pixel coordinates, or pure-div visual exports; those belong to `../autodev-frontend/SKILL.md` and its `route/route-with-html.md` pipeline.

## Workflow

### 1. Intake

Inspect the workspace before editing. Find local instructions before generating React code:

- Search from each selected/source HTML file's directory upward for `AGENT.md` and `AGENTS.md`.
- Search from the target output directory upward for `AGENT.md` and `AGENTS.md`.
- Read the nearest relevant file first; more specific directory instructions override broader parent instructions.
- If source and target instructions conflict, follow target-project rules for generated React code and preserve source-file rules only when they describe source semantics or required assets.

Follow discovered instructions for architecture, naming, styling, component placement, dependency policy, and verification.

Determine whether the user wants:

- A new React project from pasted HTML or static files.
- A conversion inside an existing React project.
- Reusable components only, without a full app scaffold.
- Pixel-faithful migration, design cleanup, or a stronger visual reinterpretation.

If no target stack is specified, prefer the existing project stack. If no React project exists, create a Vite React TypeScript app unless the user requested JavaScript, Next.js, or another framework.

Ask only for missing information that changes the output substantially, such as target framework, whether visual fidelity matters more than cleanup, or required assets that are unavailable.

### 2. Craft

Create a short implementation brief before coding:

1. Source HTML scope and dependencies.
2. Target app structure.
3. Component boundaries and the extraction standard used.
4. Ant Design decision and component mapping plan.
5. Styling strategy.
6. Static assets and external resources.
7. Behaviors to preserve or rebuild.
8. States to support: default, loading, empty, error, disabled, hover/focus, and responsive.
9. Verification plan.

For design-heavy conversions, clarify only the context that materially changes implementation: audience, use case, brand tone, key states, responsive behavior, accessibility expectations, and whether fidelity or cleanup matters more. For exact migrations, treat the source HTML as the design brief. Decide whether the result should preserve the original visual identity, normalize into Ant Design product UI, or blend Ant Design controls into the original layout.

### 3. Parse And Normalize

Prefer structured parsing or DOM inspection over regex-only conversion when files are non-trivial. Convert markup carefully:

- `class` -> `className`
- `for` -> `htmlFor`
- Inline styles -> React style objects or CSS classes.
- SVG attributes -> React-compatible names where needed.
- Repeated content -> arrays mapped into JSX.
- Form controls -> controlled or uncontrolled React patterns based on existing project conventions.
- Inline scripts -> React state, event handlers, effects, or small utility functions.

Remove unsafe or obsolete patterns unless explicitly required. Avoid `dangerouslySetInnerHTML` except for truly user-provided rich text or when the user asks to preserve raw HTML.

### 4. Recognize Ant Design Components

During conversion, actively identify HTML structures that should become Ant Design components. Prefer Ant Design for standard product UI controls and data surfaces, while keeping custom semantic HTML/CSS for bespoke marketing art direction, irregular layouts, article content, or visuals that Ant Design would flatten.

Use this decision order:

1. If the project already has a component library or generated UI rules in `AGENT.md`/`AGENTS.md`, follow those rules first.
2. If the user explicitly requested Ant Design or the HTML is clearly product/admin UI, use Ant Design for matching controls.
3. If this is a new React project and HTML contains forms, tables, navigation, feedback UI, or dashboard controls, install and use Ant Design.
4. If this is an existing project without Ant Design and the user did not explicitly request it, ask before adding the dependency unless the task wording already implies Ant Design conversion.
5. Keep native/custom React markup for bespoke visual sections where Ant Design would reduce fidelity.

Before writing Ant Design code:

- Read [Ant Design Conversion Reference](references/ant-design-conversion.md) and apply the relevant component rules for every Ant Design component being generated.
- Inspect `package.json`, lockfiles, imports, and existing components to see whether `antd` is installed and which major version/style conventions the project uses.
- Use APIs, imports, and CSS reset patterns that match the installed Ant Design version. This skill supports Ant Design v4 and v5 only; do not generate APIs or components outside those versions.
- For v4-style projects, preserve existing `antd/dist/antd.css` usage if present.
- For v5 projects, prefer project conventions for `antd/dist/reset.css`, `ConfigProvider`, design tokens, and CSS-in-JS setup.
- If a project uses another Ant Design major version, ask the user whether to target v5-compatible code, align dependencies to v4/v5, or skip Ant Design conversion.
- Use `ConfigProvider` for theme tokens, locale, component config, and prefix settings. Use v5 `App` plus `App.useApp()` or hook APIs for `message`, `notification`, and modal helpers that need provider context.

Common mappings:

- Buttons and action links -> `Button`
- Text inputs, password/search fields, textareas -> `Input`, `Input.Password`, `Input.Search`, `Input.TextArea`
- Selects, checkboxes, radios, segmented toggles, switches, sliders, date/time fields, uploads -> `Select`, `Checkbox`, `Radio`, `Segmented`, `Switch`, `Slider`, `DatePicker`, `TimePicker`, `Upload`
- Labeled form rows, validation hints, submit groups -> `Form`, `Form.Item`
- Tables and repeated record grids -> `Table` when data has columns/actions; `List` when content is simpler or more narrative
- Cards, stats, tags, avatars, badges, images -> `Card`, `Statistic`, `Tag`, `Avatar`, `Badge`, `Image`
- Menus, tabs, breadcrumbs, steps, pagination -> `Menu`, `Tabs`, `Breadcrumb`, `Steps`, `Pagination`
- Dialogs, side panels, accordions, tooltips, popovers -> `Modal`, `Drawer`, `Collapse`, `Tooltip`, `Popover`
- Alerts, empty states, loading states, messages -> `Alert`, `Empty`, `Spin`, `message`, `notification`

Do not force every `div` into `Card`, `Space`, `Flex`, or `Layout`. Use Ant Design components when they carry real behavior, accessibility, or product-UI semantics. Keep native elements for simple text, decorative wrappers, article content, and highly custom visual sections.

### 5. Extract

Decompose the page into meaningful React components. Use the **Bounded Responsibility Standard**: create a component only when it has a clear semantic responsibility and at least one strong extraction signal:

- It represents a major page region or domain object with its own styling and structure.
- The same visual/semantic pattern appears at least twice, or a list has three or more items that should be rendered from data.
- It owns interaction, state, lifecycle work, or accessibility behavior.
- Keeping it inline would make the parent hard to scan, usually because the region is roughly 50+ JSX lines or mixes unrelated concerns.

Keep markup inline when it is small, one-off, tightly coupled to its parent, or a proposed component would only pass through props to wrap a single `div`. Prefer fewer, well-named components over many tiny fragments.

Common extraction targets:

- Layout components for page shell, sections, navigation, footer, and major regions.
- Domain components for repeated cards, feature rows, pricing items, forms, galleries, or data views.
- Data modules for repeated static content.
- Hooks or utilities only when behavior repeats or complexity justifies it.
- Design tokens when colors, spacing, type, shadows, or motion values repeat with the same semantic purpose.

Extract when it improves readability, reuse, testability, or alignment with an existing component system. Do not over-abstract one-off markup.

### 6. Layout

Preserve the source layout when fidelity is requested. Otherwise improve it using these built-in layout principles:

- Use semantic HTML landmarks and accessible structure.
- Use the repo's styling approach: CSS modules, Tailwind, styled components, plain CSS, or design-system components.
- When Ant Design components are used, prefer component props, layout primitives, and theme tokens for component-level behavior, then use custom CSS only for page composition and brand-specific polish.
- Build stable responsive layouts with flex, grid, container queries, and defined spacing scales.
- Keep text from overflowing buttons, cards, nav items, or fixed panels.
- Avoid generic card-heavy layouts, nested cards, arbitrary spacing, and decorative gradients unless they are part of the source or brief.
- Preserve original branding, typography, color, and spacing when the user asks for fidelity. Normalize toward Ant Design defaults only when the task is product/admin UI cleanup or when source styling is weak/incomplete.

Translate CSS intentionally. Keep global CSS only for resets, tokens, base typography, and app-wide behavior. Prefer component-scoped styles for component-specific rules unless the repo already uses another pattern.

### 7. Build The React Project

For new projects:

- Scaffold or create the smallest runnable React project that fits the requested stack.
- Place components under `src/components` or the repo's established structure.
- Place static assets in `public` or `src/assets` based on import needs.
- Keep entry files clear: `src/main.tsx`, `src/App.tsx`, and route files if applicable.
- Include realistic content from the source HTML instead of placeholder filler.
- Configure Ant Design only as much as needed: install `antd` and compatible icon packages when required, add imports, use `ConfigProvider` or theme tokens when the source design requires them, and avoid broad theme rewrites unless requested.

For existing projects:

- Match import aliases, file naming, component style, test setup, and formatting.
- Avoid unrelated refactors.
- Keep user changes intact.

### 8. Publish

Before final delivery:

- Run install/build/lint/test commands that are appropriate and available.
- Start the dev server for frontend work and provide the local URL.
- Use browser automation or screenshots when available to verify that the page renders, assets load, layout is responsive, and no obvious overlap or blank screen exists.
- Check desktop and mobile viewports for overflow, clipped text, broken spacing, and missing assets.
- When Ant Design is used, check that styles load, theme/provider config applies, console warnings are resolved, Radio/Checkbox/Select values update correctly, Tabs switch the intended panels with stable keys, Form validation works, Table rows have stable keys/`rowKey`, feedback APIs have provider context, and mobile layouts do not overflow because of tables or fixed-width controls.

In the final response, report the key files created or changed, the commands run, any verification results, and the dev URL if a server is running.

## Conversion Rules

- Preserve semantic meaning first; visual fidelity is second unless the user says pixel-perfect.
- Keep accessibility intact: labels, alt text, button semantics, focus states, keyboard paths, and ARIA only when needed.
- Do not invent dynamic behavior that the source does not imply. If behavior is unclear, implement a reasonable static state or ask.
- Do not keep third-party CDN scripts by default inside React. Replace them with packages or React-native implementation when practical.
- Do not leave obvious Ant Design-compatible controls as raw HTML when the task calls for Ant Design conversion.
- Do not wrap bespoke content in Ant Design components merely to appear "componentized"; use Ant Design where it improves behavior, consistency, accessibility, or maintainability.
- Do not silently drop assets. Copy, import, or document unavailable assets.
- Do not create a marketing landing page wrapper around an app or tool unless the HTML itself is a landing page.
- Do not stop at JSX syntax conversion when the user asked for React engineering code.
