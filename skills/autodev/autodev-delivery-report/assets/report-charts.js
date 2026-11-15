"use strict";
// Offline SVG charts. Values are current delivery facts; no synthetic time series.
const ReportCharts = (() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  const number = (value) => (Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0);
  const color = (value) => (/^#[\da-f]{6}$/i.test(value ?? "") ? value : "#899cb7");
  const fixed = (value) => Number(value.toFixed(3));
  const tooltip = '<output class="chart-tooltip" hidden></output>';
  const hotspot = (id, text) =>
    ' data-chart-detail data-detail="' +
    esc(id) +
    '" data-chart-tooltip="' +
    esc(text) +
    '" role="button" tabindex="0" aria-label="' +
    esc(text + "，查看明细") +
    '"';
  function donut(
    id,
    { title, segments, value, unit = "", caption = "", emptyText = "暂无有效分母", register = () => {} },
  ) {
    const items = segments.map((s, index) => ({
      ...s,
      id: id + "-segment-" + index,
      value: number(s.value),
      color: color(s.color),
    }));
    const total = items.reduce((sum, s) => sum + s.value, 0);
    let offset = 0;
    const arcs = items
      .filter((s) => s.value > 0)
      .map((s) => {
        const pct = (100 * s.value) / total,
          size = pct - (pct === 100 ? 0 : Math.min(1.3, pct * 0.15));
        const text = s.name + " · " + s.value + " · " + Number(pct.toFixed(1)) + "%";
        register(s.id, s.name, {
          ...s.detail,
          value: s.value,
          percentage: Number(pct.toFixed(1)),
          basis: caption,
        });
        const html =
          '<g class="donut-hotspot"' +
          hotspot(s.id, text) +
          "><title>" +
          esc(text) +
          '</title><circle class="donut-arc" cx="90" cy="90" r="64" pathLength="100" stroke="' +
          s.color +
          '" stroke-dasharray="' +
          fixed(size) +
          ' 100" stroke-dashoffset="' +
          fixed(-offset) +
          '" style="--arc-size:' +
          fixed(size) +
          ";--arc-offset:" +
          fixed(-offset) +
          '"/></g>';
        offset += pct;
        return html;
      })
      .join("");
    for (const item of items)
      register(item.id, item.name, {
        ...item.detail,
        value: item.value,
        percentage: total ? Number(((100 * item.value) / total).toFixed(1)) : 0,
        basis: caption,
      });
    return (
      '<figure class="report-chart donut-chart" data-chart-id="' +
      esc(id) +
      '" aria-label="' +
      esc(title) +
      '"><div class="donut-visual"><svg class="donut-svg" viewBox="0 0 180 180" aria-label="' +
      esc(title) +
      '"><circle class="donut-orbit" cx="90" cy="90" r="80" pathLength="100"/><circle class="donut-track" cx="90" cy="90" r="64"/>' +
      arcs +
      '<circle class="donut-inner" cx="90" cy="90" r="49"/></svg><div class="donut-center"><strong>' +
      esc(value ?? total) +
      "<small>" +
      esc(unit) +
      "</small></strong><span>" +
      esc(total ? title : emptyText) +
      '</span></div></div><figcaption class="donut-legend">' +
      items
        .map(
          (item) =>
            '<button type="button" class="chart-legend-item" data-detail="' +
            esc(item.id) +
            '" data-chart-tooltip="' +
            esc(item.name + " · " + item.value) +
            '"><i style="--series-color:' +
            item.color +
            '"></i><span>' +
            esc(item.name) +
            "</span><strong>" +
            esc(item.value) +
            "</strong></button>",
        )
        .join("") +
      '<small class="chart-caption">' +
      esc(total ? caption : emptyText) +
      "</small></figcaption>" +
      tooltip +
      "</figure>"
    );
  }
  function line(id, { points, register = () => {} }) {
    if (!points.length)
      return (
        '<figure class="report-chart line-chart chart-empty" data-chart-id="' +
        esc(id) +
        '"><div class="chart-zero-grid" aria-hidden="true"></div><figcaption>0 · 暂无可核验 Batch 数据</figcaption></figure>'
      );
    const rows = [
      { label: "起点", planned: 0, delivered: 0 },
      ...points.map((p) => ({ ...p, planned: number(p.planned), delivered: number(p.delivered) })),
    ];
    const width = 560,
      height = 184,
      left = 34,
      right = 18,
      top = 18,
      bottom = 30;
    const max = Math.max(1, ...rows.flatMap((p) => [p.planned, p.delivered]));
    const x = (i) => left + ((width - left - right) * i) / (rows.length - 1);
    const y = (value) => top + (height - top - bottom) * (1 - value / max);
    const path = (key) =>
      rows.map((p, i) => (i ? "L" : "M") + fixed(x(i)) + " " + fixed(y(p[key]))).join(" ");
    const area =
      path("delivered") +
      " L " +
      fixed(x(rows.length - 1)) +
      " " +
      (height - bottom) +
      " L " +
      left +
      " " +
      (height - bottom) +
      " Z";
    const stride = Math.max(1, Math.ceil(points.length / 6));
    const grid = [...new Set([0, Math.ceil(max / 2), max])]
      .map(
        (r) =>
          '<g class="chart-grid"><line x1="' +
          left +
          '" y1="' +
          fixed(y(r)) +
          '" x2="' +
          (width - right) +
          '" y2="' +
          fixed(y(r)) +
          '"/><text x="' +
          (left - 8) +
          '" y="' +
          fixed(y(r) + 3) +
          '" text-anchor="end">' +
          fixed(r) +
          "</text></g>",
      )
      .join("");
    const labels = rows
      .map((p, i) =>
        i === 0 || i === rows.length - 1 || i % stride === 0
          ? '<text class="chart-axis-label" x="' +
            fixed(x(i)) +
            '" y="' +
            (height - 9) +
            '" text-anchor="' +
            (i === 0 ? "start" : i === rows.length - 1 ? "end" : "middle") +
            '">' +
            esc(String(p.label).slice(0, 14)) +
            "</text>"
          : "",
      )
      .join("");
    const dots = points
      .map((p, index) => {
        const i = index + 1,
          key = id + "-point-" + index,
          text = p.label + " · 计划累计 " + rows[i].planned + " · 已交付累计 " + rows[i].delivered;
        register(key, p.label, {
          ...p.detail,
          planned: rows[i].planned,
          delivered: rows[i].delivered,
          basis: "当前快照按 Batch 计划顺序累计，非执行时间或耗时趋势",
        });
        return (
          '<g class="line-hotspot" data-layer="delivered"' +
          hotspot(key, text) +
          "><title>" +
          esc(text) +
          '</title><circle class="line-point-halo" cx="' +
          fixed(x(i)) +
          '" cy="' +
          fixed(y(rows[i].delivered)) +
          '" r="10"/><circle class="line-point" cx="' +
          fixed(x(i)) +
          '" cy="' +
          fixed(y(rows[i].delivered)) +
          '" r="3.8"/></g>'
        );
      })
      .join("");
    return (
      '<figure class="report-chart line-chart" data-chart-id="' +
      esc(id) +
      '" aria-label="按 Batch 计划顺序累计的任务快照"><div class="chart-layer-legend"><span><i class="series-dot series-planned"></i>计划累计 ' +
      rows.at(-1).planned +
      '</span><button type="button" data-series="planned" aria-pressed="true">计划曲线</button><button type="button" data-series="delivered" aria-pressed="true">交付曲线</button></div><svg class="line-svg" viewBox="0 0 ' +
      width +
      " " +
      height +
      '" role="group" aria-label="当前快照，横轴不是执行时间"><defs><linearGradient id="' +
      esc(id) +
      '-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#63cfef" stop-opacity=".23"/><stop offset="100%" stop-color="#63cfef" stop-opacity=".01"/></linearGradient></defs>' +
      grid +
      '<path class="line-area" data-layer="delivered" d="' +
      area +
      '" fill="url(#' +
      esc(id) +
      '-fill)"/><path class="chart-line line-planned" data-layer="planned" pathLength="100" d="' +
      path("planned") +
      '"/><path class="chart-line line-delivered" data-layer="delivered" pathLength="100" d="' +
      path("delivered") +
      '"/>' +
      dots +
      labels +
      '</svg><figcaption class="chart-caption">按计划顺序累计已核验任务，非执行时间；点击节点查看 Batch 证据。</figcaption>' +
      tooltip +
      "</figure>"
    );
  }
  function bars(id, { rows, register = () => {} }) {
    if (!rows.length)
      return '<figure class="report-chart chart-empty"><figcaption>0 · 暂无模块源码行数记录</figcaption></figure>';
    const max = Math.max(1, ...rows.flatMap((r) => [number(r.added), number(r.deleted)]));
    return (
      '<figure class="report-chart source-bars" data-chart-id="' +
      esc(id) +
      '" aria-label="按仓库范围的源码新增与删除行数"><div class="chart-layer-legend"><span><i class="series-dot series-added"></i>新增</span><span><i class="series-dot series-deleted"></i>删除</span></div>' +
      rows
        .map((r, i) => {
          const key = id + "-repo-" + i;
          register(key, r.name, r.detail);
          return (
            '<button type="button" class="source-bar-row" data-detail="' +
            esc(key) +
            '" data-chart-tooltip="' +
            esc(r.name + " · 新增 " + number(r.added) + " · 删除 " + number(r.deleted)) +
            '"><span>' +
            esc(r.name) +
            '</span><span class="source-bar-tracks"><i class="bar-added" style="--bar-width:' +
            fixed((100 * number(r.added)) / max) +
            '%"></i><i class="bar-deleted" style="--bar-width:' +
            fixed((100 * number(r.deleted)) / max) +
            '%"></i></span><strong><em>+' +
            number(r.added) +
            "</em><em>−" +
            number(r.deleted) +
            "</em></strong></button>"
          );
        })
        .join("") +
      '<figcaption class="chart-caption">来自已有模块源码统计，排除生成目录与非源码文件；分段范围分别展示。</figcaption>' +
      tooltip +
      "</figure>"
    );
  }
  function reviewBars(id, { rows, register = () => {} }) {
    if (!rows.length)
      return (
        '<figure class="report-chart chart-empty" data-chart-id="' +
        esc(id) +
        '"><figcaption>0 · 暂无可核验 Batch Review 记录</figcaption></figure>'
      );
    const items = rows.map((r) => ({
      ...r,
      segments: r.segments.map((s) => ({ ...s, value: number(s.value), color: color(s.color) })),
    }));
    const max = Math.max(1, ...items.map((r) => r.segments.reduce((n, s) => n + s.value, 0)));
    const legend = new Map();
    for (const row of items)
      for (const s of row.segments) if (s.value || s.name !== "其他关闭") legend.set(s.name, s.color);
    return (
      '<figure class="report-chart review-bars" data-chart-id="' +
      esc(id) +
      '" aria-label="各批次 Review 代码缺陷处置与非阻断建议"><div class="chart-layer-legend">' +
      Array.from(
        legend,
        ([name, color]) =>
          '<span><i class="series-dot" style="background:' + color + '"></i>' + esc(name) + "</span>",
      ).join("") +
      '<span>建议单列</span></div><div class="review-bar-list">' +
      items
        .map((row, index) => {
          const key = id + "-batch-" + index;
          const total = row.segments.reduce((n, s) => n + s.value, 0);
          const summary = row.name + " · " + row.segments.map((s) => s.name + " " + s.value).join(" · ");
          register(key, row.name, row.detail);
          const segments = row.segments
            .filter((s) => s.value > 0)
            .map((s, i) => {
              const segmentKey = key + "-segment-" + i;
              const text = row.name + " · " + s.name + " " + s.value;
              register(segmentKey, text, {
                ...s.detail,
                value: s.value,
                basis: "本批次已留存的代码缺陷，按问题去重，非累计任务数",
              });
              return (
                '<button type="button" class="review-bar-segment" data-detail="' +
                esc(segmentKey) +
                '" data-chart-tooltip="' +
                esc(text) +
                '" aria-label="' +
                esc(text + "，查看依据") +
                '" style="--segment-width:' +
                fixed((100 * s.value) / max) +
                "%;--segment-color:" +
                s.color +
                '">' +
                s.value +
                "</button>"
              );
            })
            .join("");
          const advisoryKey = key + "-advisories";
          const advisoryCount = number(row.advisories?.value);
          register(advisoryKey, row.name + " · 非阻断建议", {
            ...row.advisories?.detail,
            value: advisoryCount,
            basis: "非阻断建议单列，不计入代码缺陷修复数量",
          });
          return (
            '<div class="review-bar-row"><div class="review-bar-label"><button type="button" data-detail="' +
            esc(key) +
            '" data-chart-tooltip="' +
            esc(summary) +
            '">' +
            esc(row.name) +
            '</button><small class="' +
            (row.complete ? "" : "review-coverage-partial") +
            '">' +
            (row.complete ? "记录已核验" : "记录待补齐") +
            '</small></div><div class="review-bar-track" aria-label="' +
            esc(summary) +
            '">' +
            (segments ||
              '<span class="review-bar-empty">0 · ' +
                (row.complete ? "留存记录无代码缺陷" : "记录待补齐") +
                "</span>") +
            '</div><button type="button" class="review-advisory-count" data-detail="' +
            esc(advisoryKey) +
            '">建议 <b>' +
            advisoryCount +
            "</b></button></div>"
          );
        })
        .join("") +
      '</div><figcaption class="chart-caption">按问题去重展示当前处置，建议单列；记录待补齐不代表零缺陷。点击分段查看发现与修复依据。</figcaption>' +
      tooltip +
      "</figure>"
    );
  }
  return Object.freeze({ donut, line, bars, reviewBars });
})();
