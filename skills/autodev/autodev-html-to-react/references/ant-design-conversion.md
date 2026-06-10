# Ant Design Conversion Reference

Use this reference when `html-to-react` decides to generate Ant Design code. This skill supports Ant Design v4 and v5 only. Convert by semantic intent, not by tag name alone. Preserve the source behavior first, then choose the smallest Ant Design component that expresses that behavior.

## Contents

- Version gate
- Data model extraction
- General conversion rules
- General components
- Layout and structure
- Navigation
- Data entry
- Data display
- Feedback and overlays
- App-level providers
- Verification checklist

## Version Gate

- Detect the installed major version from `package.json`, lockfiles, imports, and existing code before generating Ant Design components.
- For Ant Design v4, preserve v4 conventions: `antd/dist/antd.css`, Less variable theming when present, `visible` props on overlay components where the local codebase uses them, and static feedback APIs where provider context is not available.
- For Ant Design v5, preserve v5 conventions: `antd/dist/reset.css`, `ConfigProvider` theme tokens, CSS-in-JS setup, `App` provider for contextual feedback APIs, `open` props on overlay components, and data-driven APIs such as `items` when supported.
- Do not generate components or APIs outside Ant Design v4/v5. If the target project uses another Ant Design major version, ask whether to target v5-compatible code, align dependencies to v4/v5, or skip Ant Design conversion.
- Match `@ant-design/icons` to the installed Ant Design major when icons are needed.
- If the project already has local wrappers around Ant Design components, use those wrappers instead of direct imports.

## Data Model Extraction

Extract data before writing JSX. Prefer data-driven component APIs when the source is repetitive or structured.

- `options`: Use for simple choices in `Select`, `Radio.Group`, `Checkbox.Group`, `Segmented`, `AutoComplete`, `Cascader`, and related controls. Preserve `label`, `value`, `disabled`, `title`, and nested `children` when present.
- `items`: Use for `Menu`, `Tabs`, `Breadcrumb`, `Steps`, `Dropdown`, and v5-compatible components that accept `items`. Preserve `key`, `label`, `children`, `icon`, `disabled`, `danger`, and href/navigation metadata.
- `columns`: Use for `Table`. Derive `title`, `dataIndex`, `key`, `render`, `sorter`, `filters`, `fixed`, `width`, and action renderers from source headers/cells.
- `dataSource`: Use for `Table`, record lists, and dashboard datasets. Each record needs a stable `key` or a separate `rowKey`.
- `treeData`: Use for `Tree`, `TreeSelect`, hierarchical menus, and nested selectors. Preserve `title`, `value`, `key`, `children`, disabled state, checked state, and expanded state.
- `initialValues`: Use for `Form` defaults. Do not duplicate the same default through child `defaultValue` when a child is controlled by `Form.Item name`.
- Stable keys should come from source ids, `name`, `value`, `href`, `data-*`, row ids, labels normalized to slugs, or explicit indexes only as a last resort.
- Keep the extracted data near the component when it is page-specific. Move it to a data module only when it is large, reused, or would make the component hard to scan.

## General Conversion Rules

- Prefer existing project patterns for imports, file placement, CSS, wrappers, and version-specific APIs.
- Keep native HTML when the source is article content, decorative markup, marketing composition, custom illustration, or a layout that Ant Design would make less faithful.
- Use Ant Design for product UI: forms, tables, filters, CRUD actions, navigation, dashboards, overlays, feedback, upload, selection, and record views.
- Preserve source ids, labels, selected states, disabled states, loading states, validation hints, empty/error states, keyboard semantics, and ARIA intent.
- Do not use `any` for component data when TypeScript row/item/option types are obvious.
- Do not use unstable array indexes for keys when source ids, href targets, labels, or values can provide stable keys.
- Prefer declarative data arrays when labels/content are simple. Use explicit children when labels contain rich markup, custom tooltips, descriptions, badges, icons, or nested interactive content.
- Avoid wrapping every region in `Card`, every spacing problem in `Space`, or every page in `Layout`; use these only when they match the source semantics.

## General Components

### Button, FloatButton, Icon

- Convert native buttons and action-like anchors to `Button` when they trigger actions. Preserve submit/reset behavior with `htmlType`, danger intent with `danger`, disabled/loading state, icon-only labels with accessible text, and href/target for link buttons.
- Use `type="primary"` only for the main action in a local context; preserve secondary/ghost/text/link visual hierarchy instead of making every action primary.
- `FloatButton` is v5-only. Use it only when the source has floating quick actions, back-to-top actions, help/contact bubbles, or persistent page-level shortcuts. In v4, preserve custom fixed-position buttons.
- Use `@ant-design/icons` only when the source icon intent maps to an available Ant Design icon or the project already uses it. Keep custom SVG/icon assets when they are brand-specific or unavailable in the icon set.

## Layout And Structure

### Layout, Grid, Flex, Space, Divider

- Convert app shells with header/sidebar/content/footer into `Layout`, `Layout.Header`, `Layout.Sider`, `Layout.Content`, and `Layout.Footer` only when the source is a real application shell.
- Use `Row`/`Col` when the project already uses Ant Design grid or when source markup is a form/dashboard grid. Use CSS grid/flex for custom responsive compositions.
- `Flex` is v5-only. In v4, use CSS flex or existing layout helpers.
- Use `Space` for small component-level alignment such as button groups, inline actions, form extras, toolbar clusters, and icon+text pairs. Do not replace page composition with nested `Space` components.
- Convert visual separators to `Divider` only when they separate content groups, not for purely decorative borders.

### Typography

- Convert product text primitives to `Typography.Title`, `Typography.Text`, `Typography.Paragraph`, or `Typography.Link` when the project uses Ant Design typography or when ellipsis/copyable/editable behavior exists.
- Keep semantic `h1`-`h6`, `p`, `strong`, `em`, and links when preserving custom editorial or marketing typography.

## Navigation

### Menu

- Convert sidebars, top navs, and command navigation with selectable/open states into `Menu`.
- In v5 and v4.20+, prefer `items`. In older v4 codebases or where the project already uses children, use `Menu.Item`, `SubMenu`, and `Menu.ItemGroup`.
- Derive `key` from route path, href, id, or stable source value.
- Preserve selected/open state with `selectedKeys` and `openKeys` when source markup has active/expanded classes.
- Use `mode="horizontal"` for top nav, `mode="inline"` for sidebars, and `mode="vertical"` for compact vertical menus.
- Do not convert route navigation to `Tabs` just because it is horizontal.

### Tabs

- Convert tablist/tab-panel structures, nav pills with associated panels, or same-page anchors that reveal panels into `Tabs`.
- In v5 and v4.23+, prefer the `items` API. In older v4 codebases or where local code already uses it, use `Tabs.TabPane`.
- Derive stable item keys from panel ids, `href="#panel-id"`, `data-*`, or active values.
- Use `defaultActiveKey` for static initial state; use controlled `activeKey` only when source behavior implies dynamic state.
- Use `tabBarExtraContent` for actions beside the tab bar.
- Use `type="card"` for card-like tabs and `type="editable-card"` only when add/remove behavior exists.
- Do not convert breadcrumbs, top-level routes, side menus, or external links into `Tabs`.

### Breadcrumb, Steps, Anchor, Pagination, Dropdown, Affix

- Convert hierarchical path trails to `Breadcrumb`; preserve route hrefs and current page text. Use the local project's preferred `items` or child-item API based on version/convention.
- Convert process indicators to `Steps`; preserve current step, status, descriptions, and disabled/future steps.
- Convert in-page section navigation to `Anchor`; do not use it for route navigation.
- Convert page controls to `Pagination`; preserve current page, total/page size if inferable, disabled state, and simple/compact style.
- Convert hover/click menus attached to buttons/links to `Dropdown`. In v5 and newer v4 code, prefer `menu={{ items }}`. In older v4 projects, preserve `overlay={<Menu />}` when that is the local convention.
- Convert sticky toolbars, sticky side anchors, or fixed-position content that should stick after scrolling to `Affix`; keep CSS `position: sticky` when it is simpler and already works.

## Data Entry

### Form

- Convert real forms to `Form` and `Form.Item` when validation, labels, grouped fields, submission, or Ant Design controls are present.
- Convert HTML validation attributes into `rules`: `required`, `minLength`, `maxLength`, `pattern`, `type="email"`, `type="url"`, numeric ranges, and source-specific error text.
- Put defaults in `initialValues` for named fields. Avoid also setting `value`/`defaultValue` on the child input.
- Use `valuePropName="checked"` for standalone `Checkbox` and `Switch`.
- Use direct values for `Radio.Group`, `Checkbox.Group`, `Select`, `TreeSelect`, `Cascader`, `Slider`, `Rate`, and date/time pickers.
- Preserve submit/reset behavior. Use typed `onFinish`/`onFinishFailed` handlers in TypeScript.
- Keep custom form markup if the source is a newsletter/search block with no validation or Ant Design controls and fidelity matters more.

### Input, InputNumber, Mentions, AutoComplete

- Convert text, password, search, textarea, and affixed inputs to `Input`, `Input.Password`, `Input.Search`, `Input.TextArea`, and `Input` with `prefix`/`suffix`.
- Convert numeric fields to `InputNumber`; preserve min, max, step, precision, disabled, placeholder, and formatter/parser intent.
- Convert mention-style text entry to `Mentions` only when the source has `@` suggestions or mention-specific behavior.
- Convert text inputs with suggestion lists to `AutoComplete`; do not use `Select` for free text suggestions.

### Select, Cascader, TreeSelect, Transfer

- Convert simple dropdowns to `Select` with `options`; preserve placeholder, disabled options, multiple selection, clear/search behavior, and default selected value.
- Use `mode="multiple"` or `mode="tags"` only when the source allows multiple or custom entries.
- Avoid `labelInValue` unless source behavior needs both label and value.
- Convert hierarchical selects to `Cascader` or `TreeSelect`: use `Cascader` for path-like choices and `TreeSelect` for tree selection.
- Use `fieldNames` only when preserving existing data shape is better than renaming data to Ant Design defaults.
- Convert dual-list move selectors to `Transfer` only when the source has available/selected lists and move controls.

### Checkbox, Radio, Switch, Segmented

- Convert same-name radio inputs to one `Radio.Group`; preserve `name`, values, disabled state, checked state, and labels.
- Use `Radio.Group options` for simple choices; use `Radio` children for rich labels.
- Use `optionType="button"` only for button-like radio choices.
- Convert checkbox groups to `Checkbox.Group` when multiple values share a field name; use standalone `Checkbox` for a single boolean.
- Use `Switch` for immediate binary settings, not for form multi-choice values.
- `Segmented` exists in newer v4 and v5. Use it for compact view-mode toggles where form semantics are not needed; otherwise use `Radio.Group` or native/custom controls.
- Do not convert button-like toggles to `Tabs` unless they switch content panels.

### DatePicker, TimePicker, Slider, Rate, ColorPicker, Upload

- Convert date/time inputs and date ranges to `DatePicker`, `DatePicker.RangePicker`, `TimePicker`, or `TimePicker.RangePicker`; preserve format hints, disabled dates/times if explicit, and placeholder text.
- Ant Design date/time pickers use date objects, not raw input strings. Use the project's date library convention: Moment in v4, Dayjs in v5 unless the project config says otherwise.
- Convert range inputs to `Slider`; preserve min, max, step, marks, disabled, and single vs range behavior.
- Convert rating widgets to `Rate`; preserve count, allow half, disabled/read-only, and labels.
- `ColorPicker` is v5-only. In v4, preserve native color input or existing custom picker.
- Convert file inputs/dropzones to `Upload`; preserve multiple, accept, disabled, drag-and-drop, preview/list behavior, and use the project upload normalization pattern inside `Form.Item`.
- For client-side file selection without immediate upload, use `beforeUpload={() => false}` or the existing local pattern. For real upload behavior, preserve or map `action`, `headers`, `customRequest`, and progress/error handling.

## Data Display

### Table

- Convert semantic record tables to `Table`; keep simple content/comparison tables as semantic HTML when that is more readable.
- Extract `columns` and `dataSource`; define a row type in TypeScript.
- Use stable record `key` or set `rowKey`.
- Convert action cells to `Button`, `Dropdown`, `Popconfirm`, or links.
- Preserve sorting/filtering/search hints, selected rows, pagination, summary rows, expandable rows, fixed columns, and horizontal scroll when implied.
- Add `scroll={{ x: ... }}` or responsive handling when columns risk mobile overflow.

### List, Card, Descriptions, Statistic

- Convert repeated simple records to `List` when the project already uses it or when list item metadata/actions are useful. Use `Table` for columnar data and `Card` for distinct actionable/content units.
- Convert product/detail summary pairs to `Descriptions`; preserve labels, values, status tags, and column count.
- Convert prominent metrics to `Statistic`; preserve prefix/suffix, precision, trend color only if meaningful.
- Use `Card` only for bounded content blocks with title/actions/cover/extra or distinct repeated items. Avoid nested cards.

### Tag, Badge, Avatar, Image, Empty, QRCode

- Convert statuses, categories, labels, and removable chips to `Tag`; use meaningful colors only when source semantics are clear.
- Convert counts/online/status marks to `Badge`; preserve dot/count/status semantics.
- Convert user/entity images or initials to `Avatar` or `Avatar.Group`; preserve alt text where images remain visible.
- Convert previewable image galleries to `Image`/`Image.PreviewGroup`; keep plain `img` for decorative or custom responsive imagery.
- Use `Empty` when source has an intentional no-data state.
- `QRCode` is v5-only. In v4, preserve image/canvas/custom QR markup unless a local QR component exists.

### Collapse, Carousel, Timeline, Tree, Calendar

- Convert accordions to `Collapse`. In v5 and newer v4 code, prefer `items` when the project uses it; in older v4 code, use `Collapse.Panel`.
- Convert sliders with multiple media/content frames to `Carousel`; preserve autoplay/dots/arrows only when source implies them.
- Convert chronological event lists to `Timeline`; preserve order, status markers, timestamps, and pending state.
- Convert hierarchical expandable lists to `Tree`; preserve checked/selected/expanded states with `treeData`.
- Convert month/date grids or scheduling views to `Calendar` only when the source is calendar-like, not just a date input.

### Tooltip, Popover, Tour, Watermark

- Convert hover/focus helper text to `Tooltip`; use `Popover` for richer interactive content.
- `Tour` and `Watermark` are v5-era components. Use them only in v5 projects; in v4, preserve existing custom markup or use local alternatives.

## Feedback And Overlays

### Alert, Message, Notification, Result

- Convert inline persistent messages to `Alert`; preserve type, title/description, closable state, and action links.
- Convert transient operation feedback to `message` or `notification`.
- In v5, use `App.useApp()` or hook APIs when provider context is needed. In v4, preserve the static API pattern unless the project already uses hook APIs.
- Convert full-page success/error/empty outcomes to `Result`; preserve status, title, subtitle, and actions.

### Modal, Drawer, Popconfirm

- Convert blocking dialogs to `Modal`; preserve title, footer actions, confirm/cancel labels, danger/loading state, width, and close behavior.
- For v5, use `open`; for v4, use `visible` unless the local v4 codebase already migrated to `open`.
- Convert side panels/edit drawers to `Drawer`; preserve placement, width, title, extra actions, and close behavior. Use `open` in v5 and `visible` in v4 unless local convention differs.
- Convert destructive or uncertain inline confirmations to `Popconfirm`; keep simple delete buttons as buttons only when no confirmation exists.
- Do not create modals for content that is already inline unless the source behavior implies overlay interaction.

### Spin, Skeleton, Progress

- Convert loading spinners to `Spin` for short blocking loads.
- Convert skeleton placeholders to `Skeleton` when source has placeholder layout or loading cards/rows.
- Convert progress bars/circles to `Progress`; preserve percent, status, steps, success segments, and labels.

## App-Level Providers

- Wrap the app with `ConfigProvider` only when theme, locale, component config, prefix, or direction is needed or already used locally.
- In v4, preserve Less variable theming, locale providers, and static feedback APIs unless local wrappers say otherwise.
- In v5, use `ConfigProvider` theme tokens and add Ant Design `App` when contextual feedback APIs (`message`, `notification`, modal helpers) are used.
- Keep provider setup in existing root files rather than duplicating providers in leaf components.
- Respect existing locale, theme token, CSS reset, and icon package conventions.

## Verification Checklist

- Build succeeds with no unresolved Ant Design imports.
- Styles load for the installed Ant Design major version.
- No console warnings about deprecated props, missing keys, invalid form child values, overlay prop mismatches, or static feedback context.
- Form defaults, validation, submission, disabled/read-only states, upload lists, and checked values work.
- Radio, Checkbox, Select, Segmented, Switch, DatePicker, and Tabs preserve active/default state.
- Tables have stable `rowKey`/keys, readable columns, actions, pagination/scroll where needed, and no mobile overflow.
- Menus/Tabs/Breadcrumbs represent the correct navigation type.
- Modals/Drawers/Dropdowns/Popconfirms use the correct v4/v5 props and open/close correctly.
- Loading, empty, error, and success states render intentionally.
