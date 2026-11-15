"use strict";
const report = JSON.parse(document.getElementById("report-data").textContent);
const integration = report.integration ?? { items: [], legacyReviewCount: 0 };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const value = (raw) => (raw === null || raw === undefined || raw === "" ? 0 : raw);
function esc(raw) {
  return String(value(raw)).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}
const metric = (key) =>
  report.metrics[key]?.displayValue ??
  (key === "reviewRecords"
    ? report.risks.filter(isReviewSource).length
    : key === "reviewAdvisories"
      ? report.risks.filter(isReviewAdvisory).length
      : 0);
const count = (items, predicate) => items.filter(predicate).length;
const icon = (name) => '<svg aria-hidden="true"><use href="#i-' + name + '"/></svg>';
const labels = {
  contract_affected: "契约修改",
  potential_affected: "待核验依赖影响",
  missing: "数据缺失",
  coverage: "识别覆盖",
  info: "阶段说明",
  not_collected: "本阶段未采集",
  not_applicable: "不适用",
  verified_zero: "已核验无新增",
  source_dependency: "入口及本地依赖差异",
  page_file: "页面文件差异",
  delivered_task_diff: "AI 任务交付源码新增行",
  recorded_cumulative_generation: "累计生成记录",
  recorded_advisory: "保留为非阻断问题",
  review: "Review 发现",
  deferred: "遗留问题",
  audit: "证据审计",
  delivery: "交付检查",
  review_advisory: "Review 建议",
  merge_conflict: "合并冲突",
  candidate_review: "候选集成 Review",
  conflict_resolved: "冲突已解决",
  review_passed: "复核已通过",
  error: "调用异常",
  code_defect: "代码缺陷",
  advisory: "非阻断建议",
  task_plan: "问题明确关联的任务 Plan",
  batch_plan: "所属 Batch 的 Plan 范围",
  record_only: "仅原始问题记录",
  todo: "未开始",
  in_progress: "进行中",
  implemented: "已实现",
  delivered: "已交付",
  validating: "验证中",
  unverified: "待核验",
  pending: "待评估",
  passed: "通过",
  failed: "失败",
  skipped: "已跳过",
  blocked: "阻塞",
  open: "未关闭",
  synced: "已同步",
  resolved: "已解决",
  fixed: "已修复",
  recorded: "已留存",
  dimension: "验证维度",
  contract: "契约变更",
  implementation: "实现变更",
  potential: "待核验",
  method: "方法",
  mapper: "Mapper SQL",
  annotation: "类级注解",
  single_repair_accepted: "定向修复已接受",
  repair_snapshot: "修复快照",
  repair_reference: "修复记录回溯",
  stage_evidence: "Review 阶段证据",
  done: "已完成",
  closed: "已关闭",
  accepted: "已接受",
  security: "安全漏洞",
  architecture: "架构红线",
  api: "API 影响",
  complexity: "架构复杂度",
  measured: "已取得数据",
  missing: "缺少数据",
  partial: "部分数据",
  added: "新增",
  deleted: "删除",
  modified: "修改",
};
const label = (v) => labels[v] ?? value(v);
const isOpen = (r) => !["resolved", "closed", "fixed", "accepted", "已修复", "已接受"].includes(r.status);
const isReviewAdvisory = (r) =>
  r.findingType === "advisory" ||
  (r.findingType !== "code_defect" &&
    r.reviewFinding !== true &&
    ["review_advisory", "Review 建议"].includes(r.category));
const isReviewSource = (r) =>
  r.sourceStage === "review" || r.origin === "review" || r.reviewFinding === true || isReviewAdvisory(r);
const isReviewDefect = (r) => isReviewSource(r) && !isReviewAdvisory(r);
const issueStatus = (r) =>
  isReviewDefect(r) && ["resolved", "fixed", "已修复"].includes(r.status) ? "已修复" : label(r.status);
const issueBadge = (r) =>
  '<span class="issue-badge ' +
  (issueStatus(r) === "已修复" ? "issue-fixed" : "tone-" + tone(r.status)) +
  '">' +
  esc(issueStatus(r)) +
  "</span>";
const dialog = $("#details");
const detailRows = new Map();
const detailLabels = {
  history: "Review 历程",
  recordType: "记录类型",
  recordedAt: "记录时间",
  createdEpoch: "记录时间戳",
  attempt: "Review 轮次",
  revision: "修复快照序号",
  resolvedAt: "修复确认时间",
  resolutionSourceRefs: "修复证据",
  reviewHistoryRefs: "历史 Review 来源",
  commitSha: "关联提交",
  advisories: "非阻断建议",
  verified: "当前证据已核验",
  historyComplete: "引用的历史证据完整",
  implementationEvidence: "实现变更证据",
  changeType: "变更依据类型",
  version: "版本",
  chain: "入口调用链",
  line: "源码行号",
  implementationPoints: "实现要点",
  description: "统计口径",
  basis: "统计依据",
  affectedFiles: "影响文件",
  deliveryRunId: "交付运行",
  completionMode: "实现方式",
  blocksWorkflow: "是否阻断",
  disposition: "处置方式",
  origin: "问题来源",
  sourceStage: "发现阶段",
  planScope: "计划关联范围",
  taskIds: "关联任务",
  modules: "计划模块",
  reviewResult: "原始 Review 返回",
  originalStatus: "首次留存结果",
  attemptId: "调用标识",
  wave: "候选序号",
  active: "当前候选范围",
  startedAt: "首次记录时间",
  resolvedAt: "处理完成时间",
  reviewedCandidateSha: "已复核候选提交",
  reviewScope: "复核文件范围",
  candidateSha: "候选提交",
  findingType: "发现类型",
  reviewFinding: "Review 代码缺陷",
  findings: "Review 发现",
  review: "Batch Review 证据",
  tasks: "任务实现要点",
  fingerprint: "源码摘要",
  field: "来源字段或产物",
  reasonCode: "原因标识",
  affectedMetrics: "关联指标",
  diagnosticIds: "关联诊断",
  scope: "范围",
  originalPath: "原始路径",
  candidates: "候选路径",
  unresolved: "待核对文件",
  details: "任务归属详情",
  contract_affected: "契约修改",
  potential_affected: "待核验依赖影响",
  pathContext: "路径依据",
  baseSha: "基线提交",
  headSha: "交付提交",
  service: "服务",
  nextAction: "建议处理",
  category: "分类",
  sourceRefs: "证据来源",
  sourceFiles: "变更来源文件",
  planned: "计划累计任务",
  percentage: "占比（%）",
  reason: "说明",
  source: "来源",
  summary: "摘要",
  id: "标识",
  batch: "Batch",
  task: "任务",
  type: "类型",
  outcome: "交付目标",
  blockers: "阻塞事项",
  risks: "关联风险",
  screenshots: "截图证据",
  file: "关联文件",
  title: "名称",
  name: "名称",
  ref: "仓库",
  repository: "仓库",
  branch: "分支",
  owner: "负责人",
  status: "状态",
  value: "采集值",
  displayValue: "展示值",
  specRefs: "需求与场景",
  designRefs: "设计依据",
  acceptance: "验收要求",
  files: "变更文件",
  evidenceId: "证据 ID",
  action: "建议动作",
  level: "等级",
  argv: "命令",
  exitCode: "退出码",
  log: "日志",
  visual: "视觉验证",
  projectCheck: "工程检查",
  lastSyncedSha: "已同步 SHA",
  sha: "已同步 SHA",
  runId: "运行 ID",
  path: "路径",
  errors: "发现",
  changes: "接口 / 页面",
  nextActions: "下一步",
  passed: "通过",
  cleanupPending: "待清理",
  added: "新增",
  deleted: "删除",
  binary: "二进制",
  operation: "操作",
  kind: "类型",
  mergeCommitSha: "集成提交",
  merged: "已集成",
  needsConfirm: "待人工确认",
  implemented: "已实现",
  delivered: "已交付",
  result: "结果",
};
function describe(x) {
  if (Array.isArray(x))
    return x.length
      ? '<ul class="evidence-list">' + x.map((y) => "<li>" + describe(y) + "</li>").join("") + "</ul>"
      : "0";
  if (x && typeof x === "object")
    return (
      "<dl>" +
      Object.entries(x)
        .map(
          ([k, v]) =>
            "<dt>" +
            esc(detailLabels[k] ?? k) +
            "</dt><dd>" +
            (["implementationPoints", "acceptance", "outcome", "text"].includes(k)
              ? markdownValue(v)
              : describe(v)) +
            "</dd>",
        )
        .join("") +
      "</dl>"
    );
  if (typeof x === "boolean") return x ? "是" : "否";
  return esc(label(x));
}
function detail(id, title, obj, caption = title) {
  detailRows.set(id, { title, obj });
  return '<button type="button" class="row-open" data-detail="' + esc(id) + '">' + esc(caption) + "</button>";
}
const go = (tab, txt) =>
  '<button class="text-link" type="button" data-go="' + tab + '">' + txt + icon("arrow") + "</button>";
const heading = (name, tail = "") =>
  '<div class="section-heading compact"><h2>' + esc(name) + "</h2>" + tail + "</div>";
function table(columns, records, className = "") {
  const rows = records
    .map(
      (row) =>
        "<tr>" +
        row.map((v, i) => '<td data-label="' + esc(columns[i]) + '">' + v + "</td>").join("") +
        "</tr>",
    )
    .join("");
  return (
    '<div class="table-wrap' +
    (className ? " " + esc(className) : "") +
    '"><table class="data-table"><thead><tr>' +
    columns
      .map(
        (c, i) =>
          '<th scope="col" aria-sort="none"><button type="button" data-sort="' +
          i +
          '" aria-label="按' +
          esc(c) +
          '排序">' +
          esc(c) +
          '<span aria-hidden="true">↕</span></button></th>',
      )
      .join("") +
    "</tr></thead><tbody>" +
    rows +
    "</tbody></table></div>" +
    (records.length ? "" : '<div class="empty-state">共 0 项</div>')
  );
}
function tone(status) {
  if (
    [
      "passed",
      "delivered",
      "resolved",
      "fixed",
      "synced",
      "已修复",
      "已交付",
      "single_repair_accepted",
    ].includes(status)
  )
    return "success";
  if (["failed", "blocked", "P0", "P1"].includes(status)) return "danger";
  if (["partial", "missing", "pending", "unverified", "open", "potential_affected"].includes(status))
    return "warning";
  return "neutral";
}
function statusBadge(status, text = label(status)) {
  return '<span class="status-badge tone-' + tone(status) + '">' + esc(text) + "</span>";
}
function metricCard(title, key, unit, parts, symbol) {
  const m = report.metrics[key];
  return (
    '<section class="metric metric-' +
    key +
    '"><div class="metric-top"><span>' +
    title +
    "</span>" +
    '<span class="metric-icon">' +
    icon(symbol) +
    "</span>" +
    '</div><div class="metric-value">' +
    metric(key) +
    "<small>" +
    unit +
    '</small></div><div class="metric-breakdown">' +
    parts.map(([n, k]) => "<span>" + n + " <strong>" + metric(k) + "</strong></span>").join("") +
    "</div>" +
    (key === "api"
      ? '<p class="metric-subnote">实现链路 ' +
        (report.metrics.apiImplementationModified?.displayValue ??
          Math.max(0, metric("apiModified") - metric("apiContractAffected"))) +
        " · 契约修改 " +
        metric("apiContractAffected") +
        "，已计入总数</p>"
      : "") +
    '<div class="metric-foot">' +
    statusBadge(m.status) +
    detail("m-" + key, title, m, "统计依据 " + "↗") +
    "</div></section>"
  );
}
const gatePassed = count(report.gates, (g) => g.status === "passed");
const gateEvaluated = count(report.gates, (g) => g.status !== "pending");
const cats = ["架构红线", "架构质量", "安全漏洞", "API 影响"];
const category = (r) => (label(r.category) === "架构复杂度" ? "架构质量" : label(r.category));
const metricNames = {
  apiContractAffected: "已确认契约修改",
  apiImplementationModified: "实现链路修改",
  apiContractModified: "契约修改",
  apiPotentialAffected: "待核验接口影响",
  implemented: "已实现任务",
  delivered: "已交付任务",
  undone: "未实现任务",
  confirm: "人工待确认",
  p0: "P0 问题",
  p1: "P1 问题",
  reviewRecords: "Review 记录",
  reviewAdvisories: "Review 建议",
  reviewFindings: "代码缺陷",
  reviewFixed: "Review 已修复",
  nonBlocking: "非阻断遗留",
  workflow: "历史流程配置",
  api: "接口变更",
  apiAdded: "接口新增",
  apiModified: "接口修改",
  apiDeleted: "接口删除",
  page: "页面变更",
  pageAdded: "页面新增",
  pageModified: "页面修改",
  pageDeleted: "页面删除",
  aiLines: "AI 交付新增行",
  added: "源码新增行",
  deleted: "源码删除行",
  net: "源码净增行",
  tasks: "计划任务",
  completion: "交付完成率",
  lineCoverage: "行覆盖率",
  branchCoverage: "分支覆盖率",
  unit: "单元测试",
  e2e: "E2E",
  testing: "测试与覆盖率",
  untested: "未测试项",
  testPassRate: "测试通过率",
  caseCoverage: "用例执行覆盖",
  riskCount: "风险数量",
  open: "开放问题",
};
const dataGaps = (report.gaps ?? []).filter((g) => g.category === "missing");
const coverageNotes = [
  ...(report.diagnostics ?? []).filter((g) => g.category === "coverage"),
  ...(report.gaps ?? [])
    .filter((g) => !g.category)
    .map((g) => ({ ...g, reason: "历史记录待复核 · " + g.reason })),
];
const collectionNotes = (report.diagnostics ?? []).filter((g) => g.category === "info");
function diagnosticTitle(item) {
  return (
    metricNames[item.field] ?? (/\.(json|jsonl|md|log)(#.*)?$/.test(item.field) ? "产物数据" : item.field)
  );
}
function diagnosticTable(title, items, prefix) {
  return (
    '<details class="stage-row"><summary>' +
    esc(title) +
    " · " +
    items.length +
    "</summary>" +
    table(
      ["问题", "范围", "关联指标", "原因与文件", "建议处理"],
      items.map((item, i) => [
        detail(prefix + i, diagnosticTitle(item), item),
        esc(
          [item.scope?.repository, item.scope?.batch, item.scope?.task, item.scope?.subject]
            .filter(Boolean)
            .join(" / "),
        ),
        esc((item.affectedMetrics ?? []).map((k) => metricNames[k] ?? k).join("、")),
        esc(item.reason) +
          ((item.affectedFiles ?? []).length
            ? "<details><summary>关联 " +
              item.affectedFiles.length +
              " 个文件</summary>" +
              describe(item.affectedFiles) +
              "</details>"
            : ""),
        esc(item.nextAction),
      ]),
    ) +
    "</details>"
  );
}
function markdown(text, options = {}) {
  return '<div class="markdown-body">' + ReportMarkdown.render(text, options) + "</div>";
}
function markdownValue(value) {
  if (Array.isArray(value))
    return value.length
      ? '<ul class="evidence-list">' +
          value.map((item) => "<li>" + markdownValue(item) + "</li>").join("") +
          "</ul>"
      : "0";
  if (value && typeof value === "object")
    return (
      "<dl>" +
      Object.entries(value)
        .map(
          ([key, item]) =>
            "<dt>" +
            esc(detailLabels[key] ?? key) +
            "</dt><dd>" +
            (["text", "description", "implementationPoints", "acceptance", "outcome"].includes(key)
              ? markdownValue(item)
              : describe(item)) +
            "</dd>",
        )
        .join("") +
      "</dl>"
    );
  return typeof value === "string" ? markdown(value) : describe(value);
}
const documentMap = new Map();
for (const stage of report.stageArtifacts)
  for (const artifact of stage.artifacts) {
    if (!/\.md$/i.test(artifact.path)) continue;
    const full = typeof artifact.markdown === "string";
    const text = full
      ? artifact.markdown
      : (artifact.sections ?? []).map((part) => "## " + part.title + "\n\n" + part.text).join("\n\n") ||
        artifact.summary ||
        "";
    if (!documentMap.has(artifact.path))
      documentMap.set(artifact.path, { path: artifact.path, text, stage: stage.label, full });
  }
let activeDocument = documentMap.has("PRD.md") ? "PRD.md" : documentMap.keys().next().value;
let documentMode = "preview";
function documentSection(section, path, compact = false) {
  const source = path ?? section.path ?? section.sourceRefs?.[0] ?? "";
  const rendered = markdown(section.text, { scope: "excerpt-" + source + "-" + section.line, path: source });
  return (
    '<article class="document-section"><h3>' +
    esc(section.title) +
    '</h3><div class="' +
    (compact ? "md-preview" : "") +
    '">' +
    rendered +
    "</div>" +
    (compact && section.text.length > 240
      ? '<details class="brief-more"><summary>展开节选</summary>' +
        markdown(section.text, { scope: "expanded-" + source + "-" + section.line, path: source }) +
        "</details>"
      : "") +
    '<small class="source-caption">' +
    esc(source) +
    " · L" +
    esc(section.line) +
    (section.truncated ? " · 已节选" : "") +
    "</small>" +
    (documentMap.has(source)
      ? '<button type="button" class="text-link md-full-link" data-open-document="' +
        esc(source) +
        '">' +
        (documentMap.get(source).full ? "阅读完整文档" : "阅读文档节选") +
        " ↗</button>"
      : "") +
    "</article>"
  );
}
function documentLibrary() {
  if (!documentMap.size)
    return heading("需求与设计文档") + '<p class="empty-state">0 份已收录 Markdown 文档</p>';
  const current = documentMap.get(activeDocument) ?? documentMap.values().next().value;
  activeDocument = current.path;
  return (
    heading("需求与设计文档", '<span class="section-meta">' + documentMap.size + " 份 Markdown</span>") +
    '<div class="document-toolbar"><label>阅读文档<select id="document-picker" aria-label="选择 Markdown 文档">' +
    Array.from(documentMap.values())
      .map(
        (doc) =>
          '<option value="' +
          esc(doc.path) +
          '" ' +
          (doc.path === current.path ? "selected" : "") +
          ">" +
          esc(doc.path) +
          "</option>",
      )
      .join("") +
    "</select></label>" +
    '<div class="document-modes"><button type="button" data-document-mode="preview" aria-pressed="' +
    (documentMode === "preview") +
    '">预览</button><button type="button" data-document-mode="source" aria-pressed="' +
    (documentMode === "source") +
    '">Markdown 源文</button></div></div>' +
    '<div class="document-reader-meta"><span>' +
    esc(current.stage) +
    " · " +
    esc(current.path) +
    '</span><label>章节<select id="document-outline" aria-label="跳转 Markdown 章节"><option value="">选择章节</option></select></label></div>' +
    (!current.full ? '<p class="section-note">历史报告仅保留节选，重新生成后可阅读全文。</p>' : "") +
    '<div id="document-reader" class="document-reader" tabindex="0" aria-label="Markdown 文档内容">' +
    (documentMode === "preview"
      ? markdown(current.text, { scope: "reader", path: current.path })
      : '<pre class="markdown-source"><code>' + esc(current.text) + "</code></pre>") +
    "</div>"
  );
}
function openDocument(path, fragment = "") {
  const origin = activeTab;
  if (!documentMap.has(path)) {
    recordAction(origin, "文档未收录", path);
    return;
  }
  activeDocument = path;
  documentMode = "preview";
  renderPanel("plan", plan());
  selectTab("plan");
  recordAction(origin, "打开文档", path + (fragment ? "#" + fragment : ""));
  requestAnimationFrame(() => {
    const reader = $("#document-reader");
    if (fragment) scrollDocumentHeading(fragment);
    else {
      reader.focus({ preventScroll: true });
      reader.scrollIntoView({ block: "nearest", behavior: "auto" });
    }
  });
}
function scrollDocumentHeading(fragment) {
  const slug = ReportMarkdown.slug(fragment);
  const headings = Array.from(document.querySelectorAll("#document-reader [data-md-slug]"));
  const target =
    headings.find((h) => h.dataset.mdSlug === slug) ??
    headings.find((h) => h.dataset.mdSlug.startsWith(slug + "-"));
  if (target) {
    target.scrollIntoView({ block: "nearest", behavior: "auto" });
    recordAction("plan", "跳转章节", target.textContent);
  }
}
function deliveryBrief() {
  return (
    heading("本轮交付内容") +
    '<div class="brief-grid">' +
    (report.deliveryBrief ?? [])
      .map(
        (group) =>
          '<section class="brief-card"><h3>' +
          esc(group.title) +
          "</h3>" +
          ((group.sections ?? []).map((section) => documentSection(section, undefined, true)).join("") ||
            '<p class="empty-state">0 项已采集内容</p>') +
          "</section>",
      )
      .join("") +
    "</div>"
  );
}
function entryButton(id, change, obj) {
  detail(id, change.name, obj);
  const parts = /^(?:(.+) · )?(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|ANY) (.+)$/.exec(change.name);
  if (!parts) return detail(id, change.name, obj);
  return (
    '<button class="entry-button" type="button" data-detail="' +
    esc(id) +
    '">' +
    (parts[1] ? '<small class="entry-service">' + esc(parts[1]) + "</small>" : "") +
    '<span class="method-pill method-' +
    parts[2].toLowerCase() +
    '">' +
    esc(parts[2]) +
    "</span><code>" +
    esc(parts[3]) +
    "</code></button>"
  );
}
function filesSummary(files) {
  if (!files.length) return "0";
  return (
    '<div class="file-summary">' +
    files
      .slice(0, 2)
      .map((file) => '<code title="' + esc(file) + '">' + esc(file.split("/").pop()) + "</code>")
      .join("") +
    (files.length > 2 ? "<small>共 " + files.length + " 个文件，点击入口查看</small>" : "") +
    "</div>"
  );
}
function changeProof(change) {
  if (change.kind === "page")
    return '<span class="change-tag change-implementation">页面变更</span><p class="cell-note">路由 / 页面及本地依赖差异</p>';
  if (change.operation === "contract_affected")
    return '<span class="change-tag change-contract">契约修改</span><p class="cell-note">输入 / 输出结构变化</p>';
  const evidence = change.implementationEvidence ?? [];
  const chains = [...new Set(evidence.map((item) => item.chain.map((n) => n.name).join(" → ")))];
  return (
    '<span class="change-tag change-implementation">实现变更</span>' +
    (chains
      .slice(0, 2)
      .map((chain) => '<p class="call-chain">' + esc(chain) + "</p>")
      .join("") || '<p class="cell-note">入口或可解析依赖差异</p>')
  );
}
let changeKind = "全部",
  changeSearch = "",
  changePage = 1;
function changeInventory() {
  const all = report.changes.flatMap((repo) =>
    repo.changes.map((c) => ({
      ...c,
      repository: repo.repository,
      baseSha: repo.baseSha,
      headSha: repo.headSha,
    })),
  );
  const kinds = {
    全部: all,
    接口: all.filter((c) => c.kind === "api" && c.operation !== "potential_affected"),
    页面: all.filter((c) => c.kind === "page"),
    待核验: all.filter((c) => c.operation === "potential_affected"),
  };
  const filtered = kinds[changeKind].filter((c) =>
    [c.name, c.repository, ...(c.affectedFiles ?? [])]
      .join(" ")
      .toLowerCase()
      .includes(changeSearch.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 15));
  changePage = Math.min(changePage, pages);
  return (
    heading(
      "接口与页面变更明细",
      '<span class="section-meta">已确认 ' +
        (metric("api") + metric("page")) +
        " · 待核验 " +
        metric("apiPotentialAffected") +
        "</span>",
    ) +
    '<div class="change-toolbar">' +
    filters(
      "change",
      changeKind,
      Object.entries(kinds).map(([k, v]) => [k, v.length]),
    ) +
    '<label class="search-field"><span>搜索</span><input id="change-search" type="search" maxlength="160" placeholder="入口、文件或仓库" value="' +
    esc(changeSearch) +
    '"></label></div>' +
    table(
      ["入口", "类型", "操作", "变更依据与调用链", "影响文件"],
      filtered
        .slice((changePage - 1) * 15, changePage * 15)
        .map((c, j) => [
          entryButton("entry-" + changePage + "-" + j, c, c),
          esc(c.kind === "api" ? "接口" : "页面"),
          statusBadge(
            c.operation === "potential_affected"
              ? "pending"
              : c.operation === "deleted"
                ? "failed"
                : "recorded",
            c.operation === "contract_affected" ? "修改" : label(c.operation),
          ),
          c.operation === "potential_affected"
            ? '<span class="change-tag tone-warning">待核验关联</span>'
            : changeProof(c),
          filesSummary(c.affectedFiles ?? []),
        ]),
    ) +
    '<div class="pagination"><span>匹配 ' +
    filtered.length +
    " 条 · " +
    changePage +
    " / " +
    pages +
    ' 页</span><div><button type="button" data-page="-1" ' +
    (changePage === 1 ? "disabled" : "") +
    '>上一页</button><button type="button" data-page="1" ' +
    (changePage === pages ? "disabled" : "") +
    ">下一页</button></div></div>"
  );
}
function reviewHistory() {
  const reviews = report.quality.checks.filter((c) => c.type === "Batch Review");
  return (
    heading("Batch Review 历程") +
    '<p class="section-note">按本次 Batch 留存记录展示首次 Review、定向修复接受和已有复审记录。修复回溯会注明来源，缺少历史记录时保留 0。</p>' +
    (reviews
      .map((c, i) => {
        const history = c.history ?? [];
        return (
          '<details class="stage-row review-batch"' +
          (i === 0 ? " open" : "") +
          "><summary>" +
          esc(c.batch) +
          " · 首次留存 " +
          statusBadge(history[0]?.status) +
          " → 当前 " +
          statusBadge(c.status) +
          "</summary>" +
          '<p class="section-note">当前证据：' +
          (c.verified === true ? "已核验" : "待核验") +
          " · 已引用历史：" +
          (!history.length
            ? "未留存历史，不能据此认定首次无问题"
            : c.historyComplete === true
              ? "留存记录可核对"
              : "完整性待核对，不能据此认定首次无问题") +
          " · 以下保留原始结果，当前处置按具体问题匹配</p>" +
          table(
            ["记录", "原始结果", "问题与建议", "当前处置", "关联提交", "依据"],
            history.map((h, n) => [
              esc(
                (h.disposition === "single_repair_accepted"
                  ? "定向修复接受"
                  : n === 0
                    ? "首次留存 Review"
                    : "后续 Review") + (h.attempt && !h.disposition ? " · 第 " + h.attempt + " 次" : ""),
              ) +
                '<small class="source-caption">' +
                esc(label(h.recordType)) +
                "</small>",
              statusBadge(h.status),
              describe([
                ...h.findings.map((f) => f.title ?? f.id),
                ...(h.advisories ?? []).map((a) => a.message ?? a.title ?? a.issueId),
              ]),
              [...h.findings, ...(h.advisories ?? [])]
                .map((f) => {
                  const current = report.risks.find(
                    (r) =>
                      r.batch === c.batch &&
                      (r.id && (f.id ?? f.issueId)
                        ? r.id === (f.id ?? f.issueId)
                        : r.title === (f.title ?? f.message) && (!r.file || !f.file || r.file === f.file)),
                  );
                  return (
                    '<div class="review-current">' +
                    esc(f.title ?? f.message ?? f.id ?? f.issueId) +
                    " · " +
                    (current ? issueBadge(current) : statusBadge("unverified")) +
                    "</div>"
                  );
                })
                .join("") || "0 · 无问题记录",
              esc(h.commitSha),
              detail("review-history-" + i + "-" + n, h.sourceRefs.join("、"), h, "查看证据 ↗"),
            ]),
          ) +
          "</details>"
        );
      })
      .join("") || '<div class="empty-state">共 0 项</div>')
  );
}
function integrationHistory() {
  const items = integration.items;
  const conflicts = items.filter((item) => item.kind === "merge_conflict");
  return (
    heading("集成阻断与处理") +
    '<p class="section-note">合并冲突 ' +
    conflicts.length +
    " 次 · 已解决 " +
    conflicts.filter((item) => item.status === "conflict_resolved").length +
    " 次 · 候选 Review " +
    items.filter((item) => item.kind === "candidate_review").length +
    " 组。冲突解决和复核通过分别展示，不计入代码缺陷修复数。</p>" +
    (integration.legacyReviewCount
      ? '<p class="section-note">历史候选 ' +
        integration.legacyReviewCount +
        " 个仅保留复核通过 SHA，逐次问题明细未留存。</p>"
      : "") +
    table(
      ["类型", "仓库 / 候选", "Batch", "发现与原始结果", "当前处理", "时间", "依据"],
      items.map((item, i) => [
        esc(label(item.kind)),
        esc(item.repository + " / wave-" + item.wave),
        describe(item.batches),
        item.kind === "merge_conflict"
          ? esc("合并发生冲突 · " + item.files.length + " 个文件")
          : statusBadge(item.originalStatus) +
            '<p class="cell-note">' +
            esc(
              typeof item.reason === "string" && item.reason.length > 220
                ? item.reason.slice(0, 220) + "…（完整原因见过程）"
                : item.reason,
            ) +
            "</p>",
        statusBadge(
          ["conflict_resolved", "review_passed"].includes(item.status) ? "passed" : item.status,
          label(item.status),
        ) +
          (!item.active && !["conflict_resolved", "review_passed"].includes(item.status)
            ? '<small class="cell-note">历史候选，未确认原问题修复</small>'
            : ""),
        esc(item.startedAt) + '<small class="source-caption">处理完成：' + esc(item.resolvedAt) + "</small>",
        detail(
          "integration-" + i,
          label(item.kind) + " · " + item.repository + " / " + item.wave,
          item,
          "查看过程 ↗",
        ),
      ]),
    )
  );
}
const chartPalette = {
  green: "#69e4b1",
  blue: "#6bc8ff",
  purple: "#b99bff",
  amber: "#ffd08a",
  red: "#ff849f",
  gray: "#8b9db8",
};
function chartRegister(id, title, obj) {
  detailRows.set(id, { title, obj });
}
function taskDonut() {
  const tasks = report.tasks;
  const groups = [
    { name: "已交付", color: chartPalette.green, detail: { tasks: tasks.filter((t) => t.delivered) } },
    {
      name: "已实现待交付",
      color: chartPalette.blue,
      detail: { tasks: tasks.filter((t) => !t.delivered && t.implemented) },
    },
    {
      name: "未实现",
      color: chartPalette.amber,
      detail: {
        tasks: tasks.filter(
          (t) => !t.implemented && !t.delivered && ["todo", "in_progress", "failed"].includes(t.status),
        ),
      },
    },
  ];
  const known = new Set(groups.flatMap((g) => g.detail.tasks));
  groups.push({
    name: "待核验",
    color: chartPalette.gray,
    detail: { tasks: tasks.filter((t) => !known.has(t)) },
  });
  groups.forEach((g) => {
    g.value = g.detail.tasks.length;
  });
  return ReportCharts.donut("home-tasks", {
    title: "任务交付率",
    segments: groups,
    value: tasks.length ? Math.round((100 * groups[0].value) / tasks.length) : 0,
    unit: "%",
    caption: "当前任务快照 · 点击分区查看依据",
    emptyText: "暂无有效任务分母",
    register: chartRegister,
  });
}
function batchReviewChart() {
  const reviews = report.quality.checks.filter((c) => c.type === "Batch Review");
  const rows = report.batches.map((batch) => {
    const review = reviews.find((c) => c.batch === batch.id);
    const findings = report.risks.filter((r) => r.batch === batch.id && isReviewDefect(r));
    const fixed = findings.filter((r) => issueStatus(r) === "已修复");
    const unverified = findings.filter(
      (r) => !r.status || ["unverified", "missing", "pending"].includes(r.status),
    );
    const open = findings.filter((r) => isOpen(r) && !unverified.includes(r));
    const closed = findings.filter((r) => !isOpen(r) && !fixed.includes(r));
    const advisories = report.risks.filter((r) => r.batch === batch.id && isReviewAdvisory(r));
    return {
      name: batch.id,
      complete: Boolean(review?.verified && review.historyComplete && review.history?.length),
      segments: [
        { name: "已修复", value: fixed.length, color: chartPalette.green, detail: { findings: fixed } },
        { name: "仍开放", value: open.length, color: chartPalette.red, detail: { findings: open } },
        {
          name: "待核验",
          value: unverified.length,
          color: chartPalette.amber,
          detail: { findings: unverified },
        },
        { name: "其他关闭", value: closed.length, color: chartPalette.gray, detail: { findings: closed } },
      ],
      advisories: { value: advisories.length, detail: { findings: advisories } },
      detail: { ...batch, review: review ?? null, findings, advisories },
    };
  });
  return ReportCharts.reviewBars("batch-review", { rows, register: chartRegister });
}
function changeCharts() {
  const segments = [
    ["新增", "apiAdded", chartPalette.green],
    ["修改", "apiModified", chartPalette.blue],
    ["删除", "apiDeleted", chartPalette.red],
  ].map(([name, key, color]) => ({ name, value: metric(key), color, detail: report.metrics[key] }));
  const rows = report.changes
    .filter((repo) => (repo.modules ?? []).length)
    .map((repo) => ({
      name: repo.repository + " · " + String(repo.headSha ?? "0").slice(0, 7),
      added: repo.modules.reduce((n, m) => n + (m.added ?? 0), 0),
      deleted: repo.modules.reduce((n, m) => n + (m.deleted ?? 0), 0),
      detail: repo,
    }));
  return (
    heading("变更可视化", '<span class="section-meta">点击图形查看来源</span>') +
    '<div class="analytics-grid"><section class="chart-card"><h3>' +
    icon("branch") +
    "接口变更构成</h3>" +
    ReportCharts.donut("api-composition", {
      title: "已确认接口",
      segments,
      value: metric("api"),
      unit: "个",
      caption: "包含已确认契约修改 · 待核验影响单列",
      emptyText: "0 · 无已确认接口统计",
      register: chartRegister,
    }) +
    '</section><section class="chart-card"><h3>' +
    icon("chart") +
    "源码增删分布</h3>" +
    ReportCharts.bars("source-lines", { rows, register: chartRegister }) +
    "</section></div>"
  );
}
function reviewDonut() {
  const fixed = report.risks.filter((r) => ["fixed", "resolved", "已修复"].includes(r.status));
  const open = report.risks.filter(isOpen);
  const other = report.risks.filter((r) => !isOpen(r) && !fixed.includes(r));
  return ReportCharts.donut("issue-disposition", {
    title: "已记录问题",
    value: report.risks.length,
    unit: "项",
    segments: [
      { name: "已修复", value: fixed.length, color: chartPalette.green, detail: { findings: fixed } },
      { name: "仍开放", value: open.length, color: chartPalette.amber, detail: { findings: open } },
      { name: "其他已关闭", value: other.length, color: chartPalette.purple, detail: { findings: other } },
    ],
    caption: "当前记录的处置分布，非全仓安全扫描",
    emptyText: "0 · 暂无问题记录",
    register: chartRegister,
  });
}
function sourceStageFlow() {
  const glyphs = ["layers", "list", "chip", "branch", "code"];
  return (
    '<div class="stage-flow">' +
    report.stageArtifacts
      .filter((s) => s.id !== "dev.delivery_report")
      .map(
        (stage, i) =>
          '<button type="button" class="flow-node ' +
          (stage.artifacts.length ? "is-recorded" : "") +
          '" data-detail="home-stage-' +
          i +
          '"><span class="flow-dot">' +
          icon(glyphs[i] ?? "pulse") +
          "</span><strong>" +
          esc(stage.label) +
          "</strong><small>" +
          (stage.skipped ? "已跳过" : stage.artifacts.length + " 份产物") +
          "</small></button>",
      )
      .join("") +
    "</div>"
  );
}
function overview() {
  const c = releaseConclusion();
  const open = report.risks.filter(isOpen);
  const cards = [
    ["已交付任务", "delivered", "项", "plan"],
    ["接口变更", "api", "个", "changes"],
    ["页面变更", "page", "个", "changes"],
    ["开放问题", "open", "项", "risks"],
  ];
  const next = homeActions().slice(0, 3);
  return (
    '<section class="home-conclusion tone-border-' +
    tone(c.status) +
    '" id="decision">' +
    '<div class="conclusion-main"><p class="eyebrow"><span class="live-dot"></span>DELIVERY VERDICT / 研发交付结论</p>' +
    '<div class="verdict-badges">' +
    statusBadge(c.status, c.badge) +
    '<span class="scope-pill">统计截止 Code</span></div>' +
    '<h1 id="decisionTitle">' +
    esc(c.title) +
    '</h1><p class="conclusion-copy">' +
    esc(c.reason) +
    "</p>" +
    '<div class="conclusion-actions">' +
    go(c.tab, c.action) +
    "<span>Feature · " +
    esc(report.featureId) +
    "</span></div></div>" +
    '<div class="verdict-telemetry"><div class="telemetry-label">' +
    icon("radar") +
    "DELIVERY TELEMETRY<span>交付快照</span></div>" +
    taskDonut() +
    '<div class="telemetry-footer"><span>Review 已修复 <b>' +
    metric("reviewFixed") +
    "</b></span><span>发布验证 <b>" +
    gatePassed +
    " / " +
    report.gates.length +
    "</b></span>" +
    statusBadge(
      report.gates.some((g) => g.status === "failed")
        ? "failed"
        : report.gates.length && gatePassed === report.gates.length
          ? "passed"
          : "pending",
    ) +
    "</div></div></section>" +
    '<div class="home-kpis">' +
    cards
      .map(
        ([name, key, unit, tab]) =>
          '<button type="button" class="kpi-tile kpi-' +
          key +
          '" data-go="' +
          tab +
          '"><span>' +
          '<i class="kpi-symbol">' +
          icon({ delivered: "layers", api: "branch", page: "chip", open: "shield" }[key]) +
          "</i><em>" +
          name +
          "</em>" +
          icon("arrow") +
          "</span><strong>" +
          counter(metric(key)) +
          "<small>" +
          unit +
          "</small></strong><small>" +
          esc(
            key === "delivered"
              ? "计划 " + metric("tasks") + " 项 · 完成 " + metric("completion") + "%"
              : key === "api"
                ? "新增 " + metric("apiAdded") + " · 修改 " + metric("apiModified")
                : key === "page"
                  ? "新增 " + metric("pageAdded") + " · 修改 " + metric("pageModified")
                  : "非阻断 " + metric("nonBlocking") + " · P0 " + metric("p0"),
          ) +
          "</small></button>",
      )
      .join("") +
    "</div>" +
    '<div class="home-bottom"><section class="report-section home-next">' +
    heading("下一步重点", '<span class="section-meta">' + next.length + " 项</span>") +
    (next.length
      ? '<ol class="next-steps">' +
        next
          .map(
            (item, i) =>
              '<li><span class="step-index">0' +
              (i + 1) +
              "</span><div><strong>" +
              esc(item.title) +
              "</strong><p>" +
              esc(item.reason) +
              "</p></div>" +
              go(item.tab, "查看") +
              "</li>",
          )
          .join("") +
        "</ol>"
      : '<p class="empty-state">已记录的交付事项已闭环；发布执行状态见交付追溯。</p>') +
    "</section>" +
    '<section class="report-section home-stage home-chart">' +
    heading("Batch Review 闭环分布", go("risks", "查看问题")) +
    batchReviewChart() +
    "</section></div>"
  );
}

function releaseConclusion() {
  const failedEngineering = report.quality.checks.some((c) => c.status === "failed");
  const blocking =
    report.risks.some((r) => isOpen(r) && (r.blocksWorkflow === true || r.level === "P0")) ||
    integration.items.some((item) => item.active && item.status === "blocked");
  if (
    failedEngineering ||
    report.gates.some((g) => g.status === "failed") ||
    report.audits.some((a) => a.passed === false) ||
    blocking
  )
    return {
      status: "failed",
      badge: "存在阻断",
      title: "暂不具备发布条件",
      reason: "已有检查或问题记录存在阻断项，请先闭环后重新核对交付证据。",
      tab:
        failedEngineering || report.gates.some((g) => g.status === "failed")
          ? "tests"
          : report.audits.some((a) => a.passed === false)
            ? "audit"
            : "risks",
      action: "查看阻断与检查",
    };
  if (!report.tasks.length)
    return {
      status: "pending",
      badge: "证据待补齐",
      title: "当前证据不足，暂无法判断",
      reason: "尚无可核验的任务交付结论。缺失数值按 0 展示，具体原因可在交付追溯中查看。",
      tab: "audit",
      action: "查看数据来源",
    };
  if (!report.tasks.every((t) => t.delivered))
    return {
      status: "partial",
      badge: "部分交付",
      title: "交付尚未全部完成",
      reason: report.narrative.summary,
      tab: "plan",
      action: "查看未完成任务",
    };
  if (
    !report.gates.length ||
    report.gates.some((g) => g.status !== "passed") ||
    dataGaps.length ||
    coverageNotes.length ||
    report.risks.some(isOpen) ||
    integration.items.some((item) => item.active && item.status === "unverified") ||
    !report.audits.length ||
    report.audits.some((a) => a.passed !== true) ||
    report.workspaceSync.some((s) => !["synced", "skipped"].includes(s.status))
  )
    return {
      status: "pending",
      badge: "编码已交付 · 验证待补齐",
      title: "编码交付完成，发布仍需验证",
      reason:
        "已交付 " +
        metric("delivered") +
        " 项任务，当前仍有未闭环事项或待核验来源。上线结论以完整发布检查为依据。",
      tab: "tests",
      action: "查看质量验证",
    };
  return {
    status: "passed",
    badge: "交付证据已闭环",
    title: "可进入发布评估",
    reason: "当前已提供的交付、审计与发布检查均已闭环；本报告统计截止 Code，实际发布状态仍由后续流程记录。",
    tab: "audit",
    action: "查看交付证据",
  };
}
function homeActions() {
  const result = [];
  if (integration.items.some((item) => item.active && ["blocked", "unverified"].includes(item.status)))
    result.push({ title: "处理集成阻断", reason: "当前候选存在未闭环或待核验的集成记录。", tab: "risks" });
  if (report.risks.some(isOpen))
    result.push({
      title: "闭环开放问题",
      reason: metric("open") + " 项开放，包含 " + metric("nonBlocking") + " 项非阻断遗留。",
      tab: "risks",
    });
  if (report.gates.some((g) => g.status !== "passed"))
    result.push({
      title: "补齐发布检查",
      reason: report.gates
        .filter((g) => g.status !== "passed")
        .map((g) => g.label)
        .join("、"),
      tab: "tests",
    });
  if (dataGaps.length || coverageNotes.length)
    result.push({
      title: "核对来源与识别范围",
      reason: dataGaps.length + " 项数据缺口 · " + coverageNotes.length + " 项识别覆盖说明。",
      tab: "audit",
    });
  if (report.tasks.some((t) => !t.delivered))
    result.unshift({
      title: "完成任务交付",
      reason: "按 Plan 核对实现证据、Batch 集成和任务状态。",
      tab: "plan",
    });
  return result;
}
function counter(number) {
  return '<span data-count="' + esc(number) + '">' + esc(number) + "</span>";
}
function auditView() {
  const timeline = report.stageArtifacts
    .map(
      (s) =>
        '<details class="stage-row"><summary>' +
        esc(s.label) +
        " · " +
        (s.skipped ? "已跳过" : s.id === "dev.delivery_report" ? "本报告" : s.artifacts.length + " 份产物") +
        "</summary>" +
        table(
          ["产物", "摘要"],
          s.artifacts.map((a) => [
            esc(a.path),
            (/\.md$/i.test(a.path)
              ? markdown(a.summary ?? "", { scope: "artifact-" + a.path, path: a.path })
              : esc(a.summary)) +
              ((a.sections ?? []).length
                ? '<details class="artifact-sections"><summary>查看章节内容 · ' +
                  a.sections.length +
                  "</summary>" +
                  a.sections.map((section) => documentSection(section, a.path)).join("") +
                  "</details>"
                : ""),
          ]),
        ) +
        "</details>",
    )
    .join("");
  const audits = report.audits.map((r, i) => [
    detail("audit" + i, r.runId, r),
    esc(r.status),
    esc(r.passed == null ? "待核验" : r.passed ? "通过" : "失败"),
    esc(r.errors.length),
    esc(r.cleanupPending.length),
  ]);
  return (
    heading("阶段产物") +
    sourceStageFlow() +
    timeline +
    heading("Batch 集成") +
    table(
      ["Batch", "运行 ID", "集成状态", "提交"],
      report.batches.map((b, i) => [
        detail("batch" + i, b.id, b),
        esc(b.runId),
        esc(b.merged ? "已集成" : "待核验"),
        esc(b.mergeCommitSha),
      ]),
    ) +
    heading("交付版本") +
    table(
      ["仓库", "基线提交", "交付提交", "运行 ID"],
      (report.snapshot.repositories ?? []).map((repo) => [
        esc(repo.ref),
        esc(repo.baseSha),
        esc(repo.headSha),
        esc(repo.runId),
      ]),
    ) +
    heading("证据审计") +
    table(["运行 ID", "工作流状态", "审计", "问题", "待清理"], audits) +
    heading("工作区同步") +
    table(
      ["仓库", "分支", "状态", "已同步 SHA"],
      report.workspaceSync.map((r, i) => [
        detail("sync" + i, r.repository, r),
        esc(r.branch),
        esc(label(r.status)),
        esc(r.sha),
      ]),
    ) +
    heading("数据与采集说明") +
    diagnosticTable("数据缺口", dataGaps, "gap-") +
    diagnosticTable("识别覆盖说明", coverageNotes, "coverage-") +
    diagnosticTable("阶段采集说明", collectionNotes, "collection-")
  );
}
function changesView() {
  const groups = report.changes.flatMap((repo, i) =>
    (repo.modules ?? []).map((m, j) => [
      detail("module-" + i + "-" + j, m.name, { ...m, repository: repo.repository }),
      esc(repo.repository),
      esc(m.files.length),
      esc(m.api),
      esc(m.page),
      esc(m.added),
      esc(m.deleted),
    ]),
  );
  return (
    heading("本期变更", '<span class="section-meta">封存基线 → 交付版本</span>') +
    '<div class="metrics-grid">' +
    metricCard(
      "接口变更",
      "api",
      "个",
      [
        ["新增", "apiAdded"],
        ["修改", "apiModified"],
        ["删除", "apiDeleted"],
      ],
      "code",
    ) +
    metricCard(
      "页面变更",
      "page",
      "个",
      [
        ["新增", "pageAdded"],
        ["修改", "pageModified"],
        ["删除", "pageDeleted"],
      ],
      "list",
    ) +
    metricCard(
      report.metrics.aiLines.basis === "recorded_cumulative_generation" ? "AI 累计生成行" : "AI 交付新增行",
      "aiLines",
      "行",
      [
        ["Git 新增", "added"],
        ["Git 删除", "deleted"],
        ["Git 净增", "net"],
      ],
      "code",
    ) +
    "</div>" +
    changeCharts() +
    changeInventory() +
    heading("模块与代码分布") +
    table(["模块", "仓库", "文件", "接口", "页面", "新增行", "删除行"], groups)
  );
}
let issueFilter = "全部问题";
let planFilter = "全部",
  testView = "覆盖概览";
function filters(field, selected, options) {
  return (
    '<div class="filter-bar"><div class="filter-group">' +
    options
      .map(
        ([name, total]) =>
          '<button type="button" class="filter-button ' +
          (name === selected ? "active" : "") +
          '" data-filter="' +
          field +
          '" data-choice="' +
          esc(name) +
          '" aria-pressed="' +
          (name === selected) +
          '">' +
          esc(name) +
          " " +
          total +
          "</button>",
      )
      .join("") +
    "</div></div>"
  );
}
const confirmedTasks = new Set(
  report.risks
    .filter((r) => r.needsConfirm && isOpen(r))
    .flatMap((r) =>
      report.tasks.filter((t) => (r.task ? t.id === r.task : t.batch === r.batch)).map((t) => t.id),
    ),
);
function plan() {
  const groups = {
    全部: report.tasks,
    已实现: report.tasks.filter((t) => t.implemented),
    已交付: report.tasks.filter((t) => t.delivered),
    未实现: report.tasks.filter(
      (t) => !t.implemented && ["in_progress", "todo", "failed"].includes(t.status),
    ),
    待核验: report.tasks.filter((t) => t.status === "unverified"),
    人工待确认: report.tasks.filter((t) => confirmedTasks.has(t.id)),
  };
  return (
    deliveryBrief() +
    documentLibrary() +
    heading("Plan 任务") +
    '<div class="task-progress"><div><h3>已交付 ' +
    metric("delivered") +
    " / " +
    metric("tasks") +
    ' 项</h3><div class="stacked-bar"><span class="done" style="width:' +
    metric("completion") +
    '%"></span></div></div><div class="task-score"><strong>' +
    metric("completion") +
    "%</strong><span>交付完成率</span></div></div>" +
    filters(
      "plan",
      planFilter,
      Object.entries(groups).map(([k, v]) => [k, v.length]),
    ) +
    table(
      ["任务", "实现要点", "Batch", "模块", "仓库", "状态", "风险确认", "负责人"],
      groups[planFilter].map((t) => [
        detail("task" + t.id, t.title, t, t.id + " · " + value(t.title)),
        markdownValue(t.implementationPoints ?? []),
        esc(t.batch),
        esc((t.modules ?? []).join("、")),
        esc(t.repository),
        statusBadge(t.status),
        esc(confirmedTasks.has(t.id) ? "待确认" : 0),
        esc(t.owner),
      ]),
    ) +
    heading("需求、设计与任务验收关联") +
    '<p class="section-note">展示计划中的关联与验收要求，以及当前交付状态。实际测试结果见“测试质量”。</p>' +
    table(
      ["任务", "需求与场景", "设计依据", "验收要求", "交付状态"],
      report.tasks.map((t) => [
        detail("trace-" + t.id, t.id, t),
        describe(t.specRefs ?? []),
        describe(t.designRefs ?? []),
        markdownValue(t.acceptance),
        esc(label(t.status)),
      ]),
    ) +
    heading("Batch 实现要点") +
    table(
      ["Batch", "名称", "实现要点"],
      report.batches.map((b, i) => [
        detail("plan-batch-" + i, b.id, b),
        esc(b.title),
        markdownValue(b.implementationPoints?.length ? b.implementationPoints : (b.tasks ?? [])),
      ]),
    )
  );
}
function testResults() {
  const q = report.quality;
  const coverage = [
    ["行覆盖率", "lineCoverage"],
    ["分支覆盖率", "branchCoverage"],
    ["计划用例执行覆盖", "caseCoverage"],
  ];
  const bars = coverage
    .map(
      ([name, k]) =>
        '<div><div class="coverage-bar-label"><span>' +
        name +
        "</span><strong>" +
        metric(k) +
        "% · " +
        (q[k]
          ? value(q[k].covered) + " / " + value(q[k].total)
          : metric("unit") + metric("e2e") + " / " + q.cases.length) +
        '</strong></div><div class="coverage-bar-track"><span style="width:' +
        metric(k) +
        '%"></span></div></div>',
    )
    .join("");
  let result =
    heading("测试质量") +
    '<div class="coverage-hero"><section class="coverage-main"><div class="coverage-label">代码行覆盖率</div><div class="coverage-number">' +
    metric("lineCoverage") +
    '<small>%</small></div><div class="coverage-bars">' +
    bars +
    '</div></section><div class="test-counts">' +
    [
      ["单元测试", "unit"],
      ["E2E 测试", "e2e"],
      ["未测试", "untested"],
    ]
      .map(
        ([n, k]) =>
          '<div class="test-count"><strong>' +
          metric(k) +
          "</strong><span>" +
          n +
          "</span><small>" +
          detail("m-" + k, n, report.metrics[k], label(report.metrics[k].status)) +
          "</small></div>",
      )
      .join("") +
    "</div></div>" +
    '<div class="subtabs">' +
    ["覆盖概览", "未测试项"]
      .map(
        (v) =>
          '<button class="subtab ' +
          (v === testView ? "active" : "") +
          '" data-test="' +
          v +
          '" aria-pressed="' +
          (v === testView) +
          '">' +
          v +
          "</button>",
      )
      .join("") +
    "</div>";
  result += '<p class="coverage-legend">已执行用例通过率 ' + metric("testPassRate") + "%</p>";
  const cases =
    testView === "覆盖概览"
      ? q.cases
      : q.cases.filter((c) => !["PASS", "PASS_WITH_WARNINGS", "FAIL"].includes(c.result));
  const testTable = (items) =>
    table(
      ["用例 / 验证目标", "任务", "执行结果"],
      items.map((c) => [
        detail("test-" + c.type + "-" + c.id, c.title ?? c.id, c),
        esc(c.task),
        esc(c.result),
      ]),
    );
  if (testView === "覆盖概览") {
    result +=
      heading("单元测试分布") +
      testTable(cases.filter((c) => c.type === "unit")) +
      heading("E2E 主链路") +
      testTable(cases.filter((c) => c.type === "e2e"));
  } else {
    result += table(
      ["未测试场景", "测试类型", "风险", "原因", "建议动作"],
      cases.map((c) => [
        detail("untested-" + c.type + "-" + c.id, c.title ?? c.id, c),
        esc(c.type),
        esc(c.risk),
        esc(c.reason),
        esc(c.action),
      ]),
    );
  }
  if (testView === "覆盖概览")
    result +=
      heading("编译、Review 与前端检查") +
      table(
        ["检查", "Batch", "任务", "结果"],
        q.checks.map((c, i) => [
          detail("check" + i, c.type, c),
          esc(c.batch),
          esc(c.task),
          esc(label(c.status)),
        ]),
      );
  return result;
}
let validationFilter = "全部";
function validationRows() {
  const gate = (name) => report.gates.find((g) => g.id === name) ?? { status: "pending", sourceRefs: [] };
  const compiles = report.quality.checks.filter((c) => c.type === "生产编译");
  const implemented = report.tasks.filter((t) => t.implemented);
  const compileComplete =
    implemented.length &&
    implemented.every((t) => compiles.some((c) => c.task === t.id && c.status === "passed"));
  const refs = report.tasks.filter((t) => t.specRefs?.length && t.designRefs?.length);
  const audit = report.audits;
  const closedTasks =
    report.metrics.delivered.status === "measured" &&
    report.tasks.length &&
    report.tasks.every((t) => t.delivered);
  const rows = [
    [
      "验证概览",
      "输入产物与版本范围",
      "recorded",
      report.sources.length,
      "已读取来源清单与封存 Git 范围；数量表示留存来源，不代表逐项业务验证通过。",
      report.sources.map((s) => s.path),
    ],
    [
      "正确性",
      "生产编译",
      compiles.some((c) => c.status === "failed")
        ? "failed"
        : compileComplete
          ? "passed"
          : compiles.length
            ? "partial"
            : "not_collected",
      compiles.length,
      "只展示已有编译结果；编译不等于功能测试。",
      compiles.flatMap((c) => c.sourceRefs),
    ],
    [
      "正确性",
      "功能与数据处理测试",
      gate("tests").status,
      report.quality.cases.length,
      "结果来自绑定版本的用例与发布检查；未执行用例保留在测试明细。",
      [...gate("tests").sourceRefs, ...report.quality.cases.flatMap((c) => c.sourceRefs)],
    ],
    [
      "正确性",
      "边界与特殊字符处理",
      "not_collected",
      0,
      "尚无专门的边界或特殊字符验证记录；不从方法名或编译成功推断通过。",
      [],
    ],
    [
      "忠实性",
      "需求 → 任务 → 设计引用",
      refs.length === report.tasks.length && refs.length
        ? "recorded"
        : refs.length
          ? "partial"
          : "not_collected",
      refs.length,
      "统计计划引用已建立的任务；业务需求是否忠实实现仍需对应场景验证。",
      refs.flatMap((t) => t.sourceRefs),
    ],
    ["一致性", "命名与配置一致性", "not_collected", 0, "当前没有命名规范和运行配置的一致性扫描结果。", []],
    [
      "完备性",
      "计划任务交付",
      closedTasks ? "passed" : report.tasks.length ? "partial" : "not_collected",
      metric("delivered"),
      "按有效 Plan 分母与已集成实现证据核对，验收原文保留在需求与实现中。",
      report.metrics.delivered.sourceRefs,
    ],
    [
      "完备性",
      "数据与识别范围",
      dataGaps.length || coverageNotes.length ? "partial" : "recorded",
      dataGaps.length + coverageNotes.length,
      "分别保留真实数据缺口和静态识别边界；可在交付追溯逐条核对。",
      report.sources.map((s) => s.path),
    ],
    [
      "安全性",
      "高危安全评估",
      gate("security").status,
      gate("security").sourceRefs.length,
      "仅按已有显式安全评估展示结论；未评估时保持待评估。",
      gate("security").sourceRefs,
    ],
    [
      "安全性",
      "敏感字段、密码与 SQL 注入专项",
      "not_collected",
      0,
      "尚无字段级脱敏清单、密码检查或 SQL 注入专项结果；字段清单和各专项需要提供对应结果，当前按未采集展示。",
      [],
    ],
    [
      "可审计性",
      "封存版本与证据审计",
      audit.some((a) => a.passed === false)
        ? "failed"
        : audit.length && audit.every((a) => a.passed === true)
          ? "passed"
          : "not_collected",
      audit.length,
      "核对已有运行审计结果；Git 基线、交付 SHA 和来源可继续展开。",
      audit.flatMap((a) => a.sourceRefs),
    ],
    [
      "可控性",
      "已记录问题的处置",
      report.risks.some((r) => isOpen(r) && r.blocksWorkflow === true)
        ? "failed"
        : report.risks.some(isOpen)
          ? "partial"
          : report.metrics.open.status === "measured"
            ? "recorded"
            : "not_collected",
      metric("open"),
      "保留阻断、非阻断与已修复处置；零条记录不等于安全评估已通过。",
      report.risks.flatMap((r) => r.sourceRefs),
    ],
    ["可控性", "监控告警与应急处置", "not_collected", 0, "本报告截止 Code 阶段，尚无对应运维验证结果。", []],
    [
      "差异清单",
      "接口、页面与代码差异",
      "recorded",
      metric("api") + metric("page"),
      "已确认变更和待核验影响分开展示，详细调用链见变更记录。",
      report.changes.flatMap((r) => r.sourceRefs),
    ],
  ];
  return rows.map(([dimension, title, status, count, basis, sourceRefs]) => ({
    dimension,
    title,
    status,
    count,
    basis,
    sourceRefs: [...new Set(sourceRefs)],
  }));
}
function specialAssessments() {
  return [
    ["architectureRedlines", "架构红线", "分层边界、依赖规则与架构约束", "layers"],
    ["architectureQuality", "架构质量", "模块职责、耦合度与可维护性", "chip"],
    ["securityVulnerabilities", "安全漏洞", "代码安全、依赖漏洞与敏感信息", "shield"],
  ].map(([id, name, description, symbol]) => ({
    id,
    label: name,
    description,
    symbol,
    value: 0,
    status: "pending",
    reason: "专项评估数据待后续补充，当前为占位值。",
    sourceRefs: [],
    ...(report.quality.specialAssessments ?? []).find((item) => item.id === id),
  }));
}
function tests() {
  const rows = validationRows();
  const dimensions = ["全部", ...new Set(rows.map((r) => r.dimension))];
  const chosen = rows.filter((r) => validationFilter === "全部" || r.dimension === validationFilter);
  return (
    heading("研发验证矩阵", '<span class="section-meta">基于已有证据 · ' + rows.length + " 个检查项</span>") +
    '<p class="section-note">已留存、通过、待评估与未采集分别展示。缺失数据保持 0，专项安全结论需要对应证据。</p>' +
    filters(
      "validation",
      validationFilter,
      dimensions.map((d) => [d, d === "全部" ? rows.length : rows.filter((r) => r.dimension === d).length]),
    ) +
    table(
      ["维度", "检查项", "状态", "已采集数", "依据与边界", "来源"],
      chosen.map((r, i) => [
        esc(r.dimension),
        esc(r.title),
        statusBadge(r.status),
        esc(r.count),
        esc(r.basis),
        r.sourceRefs.length
          ? detail("validation-" + validationFilter + "-" + i, r.title, r, "查看证据 ↗")
          : '<span class="cell-note">0 · 待补充</span>',
      ]),
    ) +
    testResults()
  );
}
const scopeNames = (v) =>
  (Array.isArray(v) ? v : typeof v === "string" ? [v] : []).filter(
    (name) => typeof name === "string" && name,
  );
function riskPlanScope(r) {
  if (r.planScope) return r.planScope;
  const related = report.tasks.filter((t) =>
    r.task ? t.id === r.task && (!r.batch || t.batch === r.batch) : r.batch && t.batch === r.batch,
  );
  return {
    taskIds: r.task ? [r.task] : related.map((t) => t.id),
    modules: [...new Set(related.flatMap((t) => scopeNames(t.modules)))],
    basis: related.length ? (r.task ? "task_plan" : "batch_plan") : r.task ? "record_only" : "missing",
    sourceRefs: [
      ...new Set(
        related
          .flatMap((t) => t.sourceRefs ?? [])
          .filter((ref) => ref === "plan.json" || ref.startsWith("plans/")),
      ),
    ],
  };
}
function riskScopeCell(r) {
  const scope = riskPlanScope(r);
  const batchScope = scope.basis === "batch_plan";
  const directModules = scopeNames(r.module);
  const modules = directModules.length ? directModules : scopeNames(scope.modules);
  return (
    '<div class="risk-scope-main">' +
    esc(r.batch) +
    "<span>" +
    (batchScope ? "Batch 任务 " : "任务 ") +
    esc(scopeNames(scope.taskIds).join("、")) +
    "</span></div>" +
    '<div class="risk-scope-meta">' +
    (directModules.length ? "模块 " : batchScope ? "Batch 模块 " : "任务模块 ") +
    esc(modules.join("、")) +
    " · 负责人 " +
    esc(r.owner) +
    "</div>"
  );
}
function riskSummary(r, index, prefix = "risk") {
  const id = prefix + index;
  detailRows.set(id, {
    title: "问题详情 · " + value(r.id ?? r.batch),
    obj: { ...r, planScope: riskPlanScope(r) },
  });
  return (
    '<button type="button" class="risk-summary-open" data-detail="' +
    esc(id) +
    '"><span class="risk-excerpt">' +
    esc(r.title ?? r.id) +
    '</span><span class="risk-read-more">查看完整内容 ↗</span></button>' +
    '<div class="risk-summary-meta"><span class="risk-category">' +
    esc(category(r)) +
    "</span><span>" +
    esc(isReviewSource(r) ? "Code Review" : label(r.origin)) +
    "</span></div>"
  );
}
function risks() {
  const kinds = Object.assign(Object.create(null), {
    全部问题: report.risks,
    "Review 发现": report.risks.filter(isReviewSource),
    架构红线: report.risks.filter((r) => category(r) === "架构红线"),
    架构质量: report.risks.filter((r) => category(r) === "架构质量"),
    安全漏洞: report.risks.filter((r) => category(r) === "安全漏洞"),
    代码缺陷: report.risks.filter(isReviewDefect),
    "Review 建议": report.risks.filter(isReviewAdvisory),
    已修复: report.risks.filter((r) => issueStatus(r) === "已修复"),
    非阻断遗留: report.risks.filter(
      (r) => r.origin === "deferred" && r.blocksWorkflow === false && isOpen(r),
    ),
  });
  for (const name of new Set([...cats, ...report.risks.map(category)])) {
    if (!Object.hasOwn(kinds, name)) kinds[name] = report.risks.filter((r) => category(r) === name);
  }
  if (!Object.hasOwn(kinds, issueFilter)) issueFilter = "全部问题";
  const selected = kinds[issueFilter];
  const assessment = specialAssessments().find((item) => item.label === issueFilter);
  return (
    heading("重点关注问题") +
    '<div class="review-dashboard"><section class="chart-card"><h3>' +
    icon("shield") +
    "问题处置分布</h3>" +
    reviewDonut() +
    '</section><div class="risk-overview"><div class="risk-overview-main"><span>开放问题</span><strong>' +
    metric("open") +
    " 项</strong></div>" +
    [
      ["Review 记录", "reviewRecords"],
      ["Review 已修复", "reviewFixed"],
      ["非阻断遗留", "nonBlocking"],
      ["P0 阻断项", "p0"],
      ["P1 高优先项", "p1"],
      ["人工待确认", "confirm"],
    ]
      .map(
        ([n, k]) =>
          '<div class="risk-stat stat-' +
          k +
          (metric(k) ? " has-value" : "") +
          '"><strong>' +
          metric(k) +
          "</strong><span>" +
          n +
          "</span></div>",
      )
      .join("") +
    "</div></div>" +
    filters(
      "issue",
      issueFilter,
      Object.entries(kinds).map(([name, items]) => [name, items.length]),
    ) +
    '<div class="risk-filter-summary"><span>当前视图：' +
    esc(issueFilter) +
    " · 匹配 <b>" +
    selected.length +
    "</b> / 报告共 " +
    report.risks.length +
    " 项</span>" +
    '<button type="button" class="text-link" data-reset-risk>重置筛选</button></div>' +
    (assessment && !selected.length
      ? '<p class="section-note">' +
        statusBadge("pending", "待补充") +
        " " +
        esc(assessment.label) +
        "专项数据待后续补充，当前显示 0 项。</p>"
      : "") +
    (selected.length
      ? table(
          ["问题摘要", "关联范围", "级别 / 阻断", "处置状态"],
          selected.map((r, i) => [
            riskSummary(r, i),
            riskScopeCell(r),
            '<div class="risk-severity">' +
              statusBadge(r.level, r.level ?? 0) +
              '<span class="risk-blocking ' +
              (r.blocksWorkflow === true ? "is-blocking" : "") +
              '">' +
              esc(r.blocksWorkflow === false ? "非阻断" : r.blocksWorkflow === true ? "阻断" : "待核验") +
              "</span></div>",
            issueBadge(r),
          ]),
          "risk-table",
        )
      : '<p class="empty-state">当前视图无匹配问题 · 报告共 ' +
        report.risks.length +
        " 项，可重置筛选查看。</p>") +
    reviewHistory() +
    integrationHistory() +
    heading("交付后待办") +
    table(
      ["问题", "关联范围", "处理建议", "负责人"],
      report.risks
        .filter(isOpen)
        .map((r, i) => [riskSummary(r, i, "todo-risk"), riskScopeCell(r), esc(r.action), esc(r.owner)]),
    )
  );
}
function renderPanel(name, markup) {
  const panel = $("#panel-" + name);
  panel.innerHTML = markup;
  let section = null;
  for (const node of Array.from(panel.children)) {
    if (node.matches(".section-heading")) {
      section = document.createElement("section");
      section.className = "report-section";
      node.before(section);
    }
    if (section) section.appendChild(node);
  }
  panel.insertAdjacentHTML("beforeend", activityMarkup(name));
  enhancePanel(panel);
}
const tabs = $$(".tab");
const tabNames = {
  overview: "交付结论",
  plan: "需求与实现",
  changes: "变更记录",
  tests: "质量验证",
  risks: "Review 闭环",
  audit: "交付追溯",
};
let activeTab = null,
  openedDetail = null;
const activityKey =
  "autodev.report.activity.v1:" + (report.reportId ?? report.snapshot.inputDigest ?? report.featureId);
let storageAvailable = true;
const activities = Object.fromEntries(Object.keys(tabNames).map((key) => [key, []]));
try {
  const stored = JSON.parse(localStorage.getItem(activityKey) ?? "{}");
  for (const key of Object.keys(tabNames)) {
    if (Array.isArray(stored?.[key]))
      activities[key] = stored[key]
        .filter(
          (item) =>
            item &&
            typeof item.time === "number" &&
            Number.isFinite(item.time) &&
            item.time > 0 &&
            item.time <= Date.now() + 86400000 &&
            typeof item.action === "string" &&
            typeof item.target === "string",
        )
        .slice(-50)
        .map((item) => ({
          time: item.time,
          action: item.action.slice(0, 40),
          target: item.target.slice(0, 256),
        }));
  }
} catch {
  storageAvailable = false;
}
function activityMarkup(tab) {
  const events = activities[tab];
  return (
    '<details class="activity-panel" data-activity="' +
    tab +
    '"><summary><span>' +
    icon("list") +
    "本页交互记录 <b>" +
    events.length +
    "</b></span><small>查看 · 筛选 · 展开</small></summary>" +
    '<div class="activity-content"><div class="activity-toolbar"><p>' +
    (storageAvailable
      ? "仅保存在本机浏览器 · 按报告版本隔离"
      : "当前浏览器不支持持久化 · 仅保留本次打开期间") +
    '</p><button type="button" data-clear-activity="' +
    tab +
    '">清空本页记录</button></div>' +
    (events.length
      ? '<ol class="activity-list">' +
        [...events]
          .reverse()
          .map(
            (item) =>
              "<li><time>" +
              esc(new Date(item.time).toLocaleString("zh-CN", { hour12: false })) +
              '</time><span class="activity-action">' +
              esc(item.action) +
              "</span><span>" +
              esc(item.target) +
              "</span></li>",
          )
          .join("") +
        "</ol>"
      : '<p class="empty-state">0 条记录</p>') +
    '<p class="activity-footnote">页面操作记录不会改变交付事实、验证状态或报告 JSON；每页保留最近 50 条。</p></div></details>'
  );
}
function refreshActivity(tab) {
  const old = $("#panel-" + tab + " [data-activity]");
  if (!old) return;
  const wasOpen = old.open;
  old.outerHTML = activityMarkup(tab);
  $("#panel-" + tab + " [data-activity]").open = wasOpen;
}
function persistActivities() {
  try {
    localStorage.setItem(activityKey, JSON.stringify(activities));
    storageAvailable = true;
  } catch {
    storageAvailable = false;
  }
}
function recordAction(tab, action, target) {
  if (!activities[tab]) return;
  const event = {
    time: Date.now(),
    action: String(action).slice(0, 40),
    target: String(target).slice(0, 256),
  };
  const previous = activities[tab].at(-1);
  if (
    previous &&
    previous.action === event.action &&
    previous.target === event.target &&
    event.time - previous.time < 400
  )
    return;
  activities[tab].push(event);
  activities[tab] = activities[tab].slice(-50);
  persistActivities();
  refreshActivity(tab);
}
function enhancePanel(panel) {
  const outline = panel.querySelector("#document-outline");
  if (outline) {
    outline.disabled = documentMode !== "preview";
    for (const heading of panel.querySelectorAll("#document-reader [data-md-slug]")) {
      const option = document.createElement("option");
      option.value = heading.dataset.mdSlug;
      option.textContent = heading.textContent;
      outline.appendChild(option);
    }
  }
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  for (const element of panel.querySelectorAll("[data-count]")) {
    const end = Number(element.dataset.count);
    if (reduced || !Number.isFinite(end) || !end || element.dataset.animated) continue;
    element.dataset.animated = "true";
    const begin = performance.now();
    const frame = (now) => {
      if (!element.isConnected || panel.hidden) {
        element.textContent = end;
        return;
      }
      const progress = Math.min(1, (now - begin) / 650);
      element.textContent = Math.round(end * (1 - Math.pow(1 - progress, 3)));
      if (progress < 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }
}
function selectTab(id, update = true) {
  if (!tabs.some((t) => t.dataset.tab === id)) id = "overview";
  const changed = activeTab !== id;
  const initial = activeTab === null;
  activeTab = id;
  tabs.forEach((tab) => {
    const active = tab.dataset.tab === id;
    tab.setAttribute("aria-selected", active);
    tab.classList.toggle("active", active);
    tab.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  const panel = $("#panel-" + id);
  if (!panel.children.length) renderPanel(id, renderers[id]());
  if (changed) recordAction(id, initial ? "打开" : "查看", tabNames[id]);
  if (update && location.hash !== "#" + id) location.hash = id;
}
document.addEventListener("click", (event) => {
  const link = event.target.closest(".markdown-body a");
  if (link && (link.dataset.mdFile !== undefined || link.dataset.mdAnchor)) {
    event.preventDefault();
    const target = link.dataset.mdAnchor ? document.getElementById(link.dataset.mdAnchor) : null;
    if (target && !link.closest(".document-section")) {
      target.scrollIntoView({ block: "nearest", behavior: "auto" });
      recordAction(activeTab, "跳转章节", link.textContent);
    } else openDocument(link.dataset.mdFile, link.dataset.mdHash ?? "");
    return;
  }
  const summaryNode = event.target.closest("summary");
  if (summaryNode && !summaryNode.closest(".activity-panel"))
    recordAction(activeTab, summaryNode.parentElement.open ? "折叠" : "展开", summaryNode.textContent.trim());
  const b = event.target.closest("button, [data-chart-detail]");
  if (!b) return;
  if (b.dataset.openDocument) {
    openDocument(b.dataset.openDocument);
  } else if (b.dataset.documentMode) {
    documentMode = b.dataset.documentMode;
    renderPanel("plan", plan());
    recordAction("plan", "切换文档视图", documentMode === "preview" ? "预览" : "Markdown 源文");
  } else if (b.dataset.clearActivity) {
    const tab = b.dataset.clearActivity;
    if (activities[tab]) {
      activities[tab] = [];
      persistActivities();
      refreshActivity(tab);
    }
  } else if (b.dataset.series) {
    const layer = b.dataset.series;
    if (!["planned", "delivered"].includes(layer)) return;
    const chart = b.closest(".report-chart");
    const shown = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(shown));
    chart
      .querySelectorAll('[data-layer="' + layer + '"]')
      .forEach((node) => node.toggleAttribute("hidden", !shown));
    recordAction(activeTab, "切换图层", b.textContent.trim() + " · " + (shown ? "显示" : "隐藏"));
  } else if (b.dataset.sort !== undefined) {
    const th = b.closest("th"),
      tableNode = b.closest("table"),
      index = Number(b.dataset.sort);
    const ascending = th.getAttribute("aria-sort") !== "ascending";
    const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
    const rows = Array.from(tableNode.tBodies[0].rows);
    rows.sort(
      (left, right) =>
        collator.compare(left.cells[index].textContent.trim(), right.cells[index].textContent.trim()) *
        (ascending ? 1 : -1),
    );
    for (const header of tableNode.querySelectorAll("th")) header.setAttribute("aria-sort", "none");
    th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    tableNode.tBodies[0].append(...rows);
    recordAction(
      activeTab,
      "排序",
      b.textContent.replace("↕", "").trim() + (ascending ? " · 升序" : " · 降序"),
    );
  } else if (b.dataset.detail) {
    const item = detailRows.get(b.dataset.detail);
    if (!item) return;
    $("#detail-heading").textContent = value(item.title);
    $("#detail-body").innerHTML = describe(item.obj);
    openedDetail = { tab: activeTab, title: item.title };
    dialog.showModal();
    recordAction(activeTab, "查看明细", item.title);
  } else if (b.hasAttribute("data-reset-risk")) {
    issueFilter = "全部问题";
    renderPanel("risks", risks());
    recordAction("risks", "重置问题筛选", "全部问题");
  } else if (b.dataset.filter === "issue") {
    issueFilter = b.dataset.choice;
    renderPanel("risks", risks());
    recordAction("risks", "筛选问题", issueFilter);
  } else if (b.dataset.filter === "plan") {
    planFilter = b.dataset.choice;
    renderPanel("plan", plan());
    recordAction("plan", "筛选任务", planFilter);
  } else if (b.dataset.filter === "change") {
    changeKind = b.dataset.choice;
    changePage = 1;
    renderPanel("changes", changesView());
    recordAction("changes", "筛选变更", changeKind);
  } else if (b.dataset.filter === "validation") {
    validationFilter = b.dataset.choice;
    renderPanel("tests", tests());
    recordAction("tests", "筛选验证", validationFilter);
  } else if (b.dataset.page) {
    changePage += Number(b.dataset.page);
    renderPanel("changes", changesView());
    recordAction("changes", "翻页", "第 " + changePage + " 页");
  } else if (b.dataset.test) {
    testView = b.dataset.test;
    renderPanel("tests", tests());
    recordAction("tests", "查看测试", testView);
  } else if (b.dataset.tab || b.dataset.go) selectTab(b.dataset.tab ?? b.dataset.go);
});
function showChartTooltip(event) {
  const target = event.target.closest("[data-chart-tooltip]");
  const chart = target?.closest(".report-chart");
  if (!chart) return;
  const output = chart.querySelector(".chart-tooltip");
  output.textContent = target.dataset.chartTooltip;
  output.hidden = false;
}
document.addEventListener("pointerover", showChartTooltip);
document.addEventListener("focusin", showChartTooltip);
for (const eventName of ["pointerout", "focusout"])
  document.addEventListener(eventName, (event) => {
    const chart = event.target.closest(".report-chart");
    if (chart && !chart.contains(event.relatedTarget))
      chart.querySelector(".chart-tooltip")?.setAttribute("hidden", "");
  });
document.addEventListener("keydown", (event) => {
  if (event.target.matches("[data-chart-detail]") && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    event.target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  }
});
document.addEventListener("change", (event) => {
  if (event.target.id === "document-picker" && documentMap.has(event.target.value)) {
    activeDocument = event.target.value;
    renderPanel("plan", plan());
    recordAction("plan", "切换文档", activeDocument);
  } else if (event.target.id === "document-outline" && event.target.value)
    scrollDocumentHeading(event.target.value);
});
let searchTimer;
document.addEventListener("input", (event) => {
  if (event.target.id !== "change-search" || event.isComposing) return;
  clearTimeout(searchTimer);
  const input = event.target;
  searchTimer = setTimeout(() => {
    changeSearch = input.value.slice(0, 160);
    changePage = 1;
    const position = input.selectionStart;
    renderPanel("changes", changesView());
    if (activeTab === "changes") {
      const replacement = $("#change-search");
      replacement.focus({ preventScroll: true });
      if (position !== null) {
        try {
          replacement.setSelectionRange(position, position);
        } catch {}
      }
    }
    recordAction("changes", "搜索变更", changeSearch || "清空搜索");
  }, 220);
});
document.addEventListener("compositionend", (event) => {
  if (event.target.id === "change-search") event.target.dispatchEvent(new Event("input", { bubbles: true }));
});
$("#close").onclick = () => dialog.close();
dialog.addEventListener("close", () => {
  if (openedDetail) {
    recordAction(openedDetail.tab, "关闭明细", openedDetail.title);
    openedDetail = null;
  }
});
tabs.forEach((tab, i) =>
  tab.addEventListener("keydown", (e) => {
    const k = {
      ArrowRight: (i + 1) % tabs.length,
      ArrowLeft: (i + tabs.length - 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[e.key];
    if (k !== undefined) {
      e.preventDefault();
      tabs[k].focus();
      selectTab(tabs[k].dataset.tab);
    }
  }),
);
const summaryFields = [
  ["接口变更", "api", "个"],
  ["页面变更", "page", "个"],
  [
    report.metrics.aiLines.basis === "recorded_cumulative_generation" ? "AI 累计生成行" : "AI 交付新增行",
    "aiLines",
    "行",
  ],
  ["新增", "added", "行"],
  ["删除", "deleted", "行"],
  ["净增", "net", "行"],
  ["计划任务", "tasks", "项"],
  ["已实现", "implemented", "项"],
  ["已交付", "delivered", "项"],
  ["交付完成率", "completion", "%"],
  ["单测", "unit", "个"],
  ["E2E", "e2e", "个"],
  ["未测试", "untested", "项"],
  ["行覆盖率", "lineCoverage", "%"],
  ["分支覆盖率", "branchCoverage", "%"],
  ["用例执行覆盖", "caseCoverage", "%"],
  ["测试通过率", "testPassRate", "%"],
  ["开放风险", "open", "项"],
  ["人工待确认", "confirm", "项"],
  ["Review 记录", "reviewRecords", "项"],
  ["代码缺陷", "reviewFindings", "项"],
  ["Review 建议", "reviewAdvisories", "项"],
  ["Review 已修复", "reviewFixed", "项"],
  ["非阻断遗留", "nonBlocking", "项"],
];
function summary() {
  return [
    "# " + report.featureId + " 编码交付摘要",
    "",
    report.narrative.summary,
    "",
    report.deliveryStatus,
    ...summaryFields.map(([n, k, u]) => "- " + n + " " + metric(k) + u),
    "",
    "研发交付结论：" + releaseConclusion().title,
    "架构与安全专项",
    ...specialAssessments().map(
      (item) =>
        "- " +
        item.label +
        " " +
        item.value +
        " 项 · " +
        (item.status === "pending" ? "待补充" : label(item.status)) +
        " · " +
        item.reason,
    ),
    ...validationRows().map(
      (r) =>
        "- " + r.dimension + " / " + r.title + " · " + label(r.status) + " · " + r.count + " · " + r.basis,
    ),
    "发布检查 " + gatePassed + " / " + gateEvaluated + " 已评估项通过",
    "本轮交付内容",
    ...(report.deliveryBrief ?? []).flatMap((group) => [
      "## " + group.title,
      ...group.sections.map(
        (section) => section.title + "\n" + section.text + "\n来源 " + section.path + ":" + section.line,
      ),
    ]),
    "需求、设计与任务验收关联",
    ...report.tasks.map(
      (t) =>
        "- " +
        t.id +
        " · 需求 " +
        JSON.stringify(t.specRefs ?? []) +
        " · 设计 " +
        JSON.stringify(t.designRefs ?? []) +
        " · 验收 " +
        JSON.stringify(t.acceptance ?? 0),
    ),
    "Batch Review 历程",
    ...report.quality.checks
      .filter((c) => c.type === "Batch Review")
      .flatMap((c) =>
        (c.history ?? []).map(
          (h, n) =>
            "- " +
            c.batch +
            " · " +
            (h.disposition === "single_repair_accepted"
              ? "定向修复接受"
              : n === 0
                ? "首次留存 Review"
                : "后续 Review") +
            " · " +
            label(h.status) +
            " · " +
            label(h.recordType) +
            " · " +
            h.findings.map((f) => f.title).join("、") +
            " · 来源 " +
            h.sourceRefs.join("、"),
        ),
      ),
    "集成阻断与处理",
    ...integration.items.map(
      (item) =>
        "- " +
        label(item.kind) +
        " · " +
        item.repository +
        " / wave-" +
        item.wave +
        " · " +
        label(item.status) +
        " · " +
        (item.reason ?? item.files?.join("、") ?? "0") +
        " · 过程 " +
        JSON.stringify(item.history) +
        " · 来源 " +
        item.sourceRefs.join("、"),
    ),
    ...report.tasks.map(
      (t) => "- " + t.id + " 实现要点\n" + (t.implementationPoints ?? []).map((p) => "  - " + p).join("\n"),
    ),
    ...report.changes.flatMap((repo) =>
      repo.changes.flatMap((c) => [
        "- " + label(c.kind) + " " + label(c.operation) + " " + c.name,
        ...(c.implementationEvidence ?? []).map(
          (e) =>
            "  - 调用链 " +
            e.chain.map((n) => n.name).join(" → ") +
            " · " +
            e.path +
            ":" +
            (e.line ?? 0) +
            " · " +
            e.version,
        ),
      ]),
    ),
    ...report.risks.map((r) => {
      const scope = riskPlanScope(r),
        directModules = scopeNames(r.module);
      return (
        "- " +
        label(r.origin) +
        " · " +
        value(r.title) +
        " · " +
        issueStatus(r) +
        (r.blocksWorkflow === false ? " · 非阻断" : "") +
        " · " +
        (scope.basis === "batch_plan" ? "Batch任务 " : "任务 ") +
        value(scopeNames(scope.taskIds).join("、")) +
        " · " +
        (directModules.length ? "模块 " : scope.basis === "batch_plan" ? "Batch模块 " : "任务模块 ") +
        value((directModules.length ? directModules : scopeNames(scope.modules)).join("、"))
      );
    }),
    "AI 代码统计口径 " + (report.metrics.aiLines.description ?? 0),
    ...report.narrative.nextActions,
    "",
    "缺失字段暂按 0 显示。",
    "数据缺口 " + dataGaps.length + " 项",
    ...dataGaps.map((g) => "- " + diagnosticTitle(g) + " · " + g.reason),
    "识别覆盖说明 " + coverageNotes.length + " 项",
    ...coverageNotes.map((g) => "- " + diagnosticTitle(g) + " · " + g.reason),
    "阶段采集说明 " + collectionNotes.length + " 项",
    ...collectionNotes.map((g) => "- " + diagnosticTitle(g) + " · " + g.reason),
  ].join("\n");
}
$("#exportBtn").onclick = () => {
  recordAction(activeTab, "导出", "交付摘要 Markdown");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([summary()], { type: "text/markdown;charset=utf-8" }));
  a.download = report.featureId + "-交付摘要.md";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
document.title = report.featureId + " · 编码交付报告";
$("#project").textContent =
  report.project +
  " / " +
  report.featureId +
  " · 版本 " +
  value(report.quality.build.version) +
  " · 构建 " +
  value(report.quality.build.buildNumber);
$("#versions").innerHTML =
  '<span class="feature-name">' +
  esc(report.featureId) +
  "</span><span>统计截止 Code · 迭代 " +
  esc(report.scope.iteration) +
  '</span><span class="updated-at">' +
  esc(report.generatedAt) +
  "</span>";
const renderers = { overview, plan, changes: changesView, tests, risks, audit: auditView };
report.stageArtifacts
  .filter((s) => s.id !== "dev.delivery_report")
  .forEach((s, i) => detail("home-stage-" + i, s.label, s));
selectTab(location.hash.slice(1), false);
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1), false));
