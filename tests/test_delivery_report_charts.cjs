const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const path = require("node:path");
const code = fs.readFileSync(
  path.resolve(__dirname, "../skills/autodev/autodev-delivery-report/assets/report-charts.js"),
  "utf8",
);
const scope = {};
vm.runInNewContext(code + ";globalThis.charts=ReportCharts;", scope);
const charts = scope.charts;
const source = [
  { name: "已交付", value: 1, color: "#69e4b1" },
  { name: "未交付", value: 3, color: "#ffd08a" },
];
const original = JSON.stringify(source);
const registered = new Map();
const html = charts.donut("sample", {
  title: "交付",
  segments: source,
  register: (id, name, item) => registered.set(name, item),
});
assert.equal(registered.get("已交付").percentage, 25);
assert.equal(registered.get("未交付").percentage, 75);
assert.equal(JSON.stringify(source), original);
assert(!/NaN|Infinity/.test(html));
const zero = charts.donut("zero", { title: "交付", segments: [], value: 0, unit: "%" });
assert(!/NaN|Infinity/.test(zero));
assert.match(zero, /暂无有效分母/);
assert(!zero.includes('class="donut-arc"'));
const labels = charts.donut("unsafe", {
  title: "<script>alert(1)</script>",
  segments: [{ name: "<img src=x onerror=alert(1)>", value: 2, color: "red;bad" }],
});
assert(!labels.includes("<script>"));
assert(!labels.includes("<img "));
assert(!labels.includes('stroke="red;bad"'));
const linePoints = [
  { label: "B001", planned: 2, delivered: 1 },
  { label: "B002", planned: 4, delivered: 3 },
];
const pointData = [];
const line = charts.line("batch", { points: linePoints, register: (id, name, item) => pointData.push(item) });
assert.equal(pointData.at(-1).planned, 4);
assert.equal(pointData.at(-1).delivered, 3);
assert.match(line, /非执行时间/);
assert(!/NaN|Infinity/.test(line));
assert.match(charts.line("none", { points: [] }), /暂无可核验 Batch/);
const bars = charts.bars("repo", { rows: [{ name: "项目A", added: 0, deleted: 0 }] });
assert(!/NaN|Infinity/.test(bars));
assert.match(bars, /--bar-width:0%/);
const reviewRows = [
  { name: "B001", complete: true, segments: [{ name: "已修复", value: 1, color: "#69e4b1", detail: { findings: ["BUG-1"] } }], advisories: { value: 9 } },
  { name: "B002", complete: false, segments: [{ name: "仍开放", value: 3, color: "#ff849f" }], advisories: { value: 0 } },
];
const reviewSource = JSON.stringify(reviewRows), reviewDetails = new Map();
const reviewBars = charts.reviewBars("review", { rows: reviewRows, register: (id, title, data) => reviewDetails.set(title, data) });
assert.match(reviewBars, /--segment-width:33.333%/);
assert.match(reviewBars, /--segment-width:100%/);
assert.equal(reviewDetails.get("B001 · 已修复 1").value, 1);
assert.equal(reviewDetails.get("B001 · 非阻断建议").value, 9);
assert.equal(JSON.stringify(reviewRows), reviewSource);
assert(!/NaN|Infinity/.test(reviewBars));
const unknownReview = charts.reviewBars("unknown", { rows: [{ name: "<img src=x>", complete: false, segments: [], advisories: {} }] });
assert.match(unknownReview, /记录待补齐/);
assert(!unknownReview.includes("留存记录无代码缺陷"));
assert(!unknownReview.includes("<img "));
assert.match(charts.reviewBars("empty", { rows: [] }), /暂无可核验 Batch Review/);
console.log(
  "PASS: chart ratios, zero denominators, per-batch defect counts, separate advisories, incomplete history, read-only inputs and safe labels",
);
