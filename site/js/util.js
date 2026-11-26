// Shared helpers for renderers: DOM building, formatting, charts.

/** el("a", {href, class: "x"}, child, "text", ...) */
export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

/** Only http(s) links are rendered as links. */
export function safeUrl(u) {
  try {
    const url = new URL(u, location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch { return null; }
}

export function relTime(iso, now = Date.now()) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.round((now - t) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d}d ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function absTime(iso, tz) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: tz || undefined });
}

/** "2026-11-22" -> ms at UTC midnight (charts format in UTC so dates never shift). */
export function dayMs(d) {
  const [y, m, dd] = String(d).split("-").map(Number);
  return Date.UTC(y, (m || 1) - 1, dd || 1);
}

const DAY_FMT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const DAY_FMT_Y = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const MON_FMT = new Intl.DateTimeFormat(undefined, { month: "short", timeZone: "UTC" });
export const fmtDay = (ms) => DAY_FMT.format(ms);
export const fmtDayY = (ms) => DAY_FMT_Y.format(ms);

export function money(v, digits = 2, currency = "USD") {
  if (v == null || Number.isNaN(v)) return "—";
  const sym = currency === "USD" ? "$" : "";
  return `${sym}${Number(v).toFixed(digits)}${sym ? "" : " " + currency}`;
}

/** Signed delta with an arrow so direction never relies on color alone. */
export function delta(v, digits = 2, unit = "") {
  if (v == null || Number.isNaN(v)) return "—";
  const r = Number(v.toFixed(digits));
  if (r === 0) return `0${unit}`;
  return `${r > 0 ? "▲ +" : "▼ −"}${Math.abs(r).toFixed(digits)}${unit}`;
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const seriesColor = (i) => cssVar(`--series-${(i % 3) + 1}`);

// ---------------------------------------------------------------- charts

let chartSetupDone = false;
function setupChart() {
  if (chartSetupDone || !window.Chart) return;
  chartSetupDone = true;
  const { Chart } = window;
  // For each dataset, the point nearest the cursor on x (datasets have different dates).
  Chart.Interaction.modes.nearestEach = function (chart, e) {
    const pos = Chart.helpers.getRelativePosition(e, chart);
    const items = [];
    chart.getSortedVisibleDatasetMetas().forEach((meta) => {
      let best = null, bestD = Infinity;
      meta.data.forEach((elem, index) => {
        const d = Math.abs(elem.x - pos.x);
        if (d < bestD) { bestD = d; best = { element: elem, datasetIndex: meta.index, index }; }
      });
      if (best && bestD <= 28) items.push(best);
    });
    return items;
  };
  // Vertical crosshair at the hovered position.
  Chart.register({
    id: "crosshair",
    afterDatasetsDraw(chart) {
      const a = chart.tooltip && chart.tooltip.getActiveElements();
      if (!a || !a.length) return;
      const x = a[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = cssVar("--axis");
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      ctx.restore();
    },
  });
}

function monthTicks(min, max) {
  const out = [];
  const d = new Date(min);
  let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
  while (t <= max) {
    out.push({ value: t });
    const n = new Date(t);
    t = Date.UTC(n.getUTCFullYear(), n.getUTCMonth() + 1, 1);
  }
  return out;
}

/**
 * Line chart over dates.
 * series: [{label, color, dashed, points: [["YYYY-MM-DD", value], ...]}]
 */
export function lineChart(container, series, { minMs, maxMs, yFormat = (v) => money(v), short = false } = {}) {
  setupChart();
  const box = el("div", { class: short ? "chart short" : "chart" });
  const canvas = el("canvas", { role: "img", "aria-label": series.map((s) => s.label).join(", ") });
  box.append(canvas);
  container.append(box);
  if (!window.Chart) {
    box.replaceChildren(el("p", { class: "muted small" }, "Chart library failed to load."));
    return null;
  }
  const all = series.flatMap((s) => s.points.map((p) => dayMs(p[0])));
  const lo = minMs ?? Math.min(...all), hi = maxMs ?? Math.max(...all);
  const surface = cssVar("--surface");
  const datasets = series.map((s) => ({
    ...datasetStyle(s, surface),
    data: s.points.map(([d, v]) => ({ x: dayMs(d), y: v })).filter((p) => p.x >= lo && p.x <= hi),
  }));
  const opts = baseOptions(yFormat, (items) => (items.length ? fmtDayY(items[0].parsed.x) : ""),
    (item) => ` ${item.dataset.label}: ${yFormat(item.parsed.y)}  (${fmtDay(item.parsed.x)})`);
  opts.parsing = false;
  opts.scales.x = {
    type: "linear", min: lo, max: hi,
    afterBuildTicks: (scale) => { const t = monthTicks(lo, hi); if (t.length >= 2) scale.ticks = t; },
    ticks: { color: cssVar("--muted"), font: { size: 11 }, maxRotation: 0, callback: (v) => MON_FMT.format(v) },
    grid: { display: false }, border: { color: cssVar("--axis") },
  };
  return new window.Chart(canvas, { type: "line", data: { datasets }, options: opts });
}

function datasetStyle(s, surface) {
  return {
    label: s.label,
    borderColor: s.color,
    backgroundColor: s.color,
    borderWidth: 2,
    borderDash: s.dashed ? [5, 4] : [],
    pointRadius: (ctx) => (ctx.dataset.data.length < 3 ? 4 : 0),
    pointHoverRadius: 4,
    pointBorderColor: surface,
    pointBorderWidth: 2,
    tension: 0,
    spanGaps: true,
  };
}

function baseOptions(yFormat, titleCb, labelCb) {
  const surface = cssVar("--surface");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearestEach", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: surface, titleColor: cssVar("--ink"), bodyColor: cssVar("--ink-2"),
        borderColor: cssVar("--axis"), borderWidth: 1, padding: 8, boxPadding: 4, usePointStyle: true,
        callbacks: { title: titleCb, label: labelCb },
      },
    },
    scales: {
      y: {
        ticks: { color: cssVar("--muted"), font: { size: 11 }, callback: (v) => yFormat(v), maxTicksLimit: 6 },
        grid: { color: cssVar("--grid") }, border: { display: false },
      },
    },
  };
}

/** Line chart over category labels (e.g. bond maturities). series: [{label, color, dashed, values: [...]}] */
export function categoryChart(container, labels, series, { yFormat = (v) => v, short = false, xTitle = "" } = {}) {
  setupChart();
  const box = el("div", { class: short ? "chart short" : "chart" });
  const canvas = el("canvas", { role: "img", "aria-label": series.map((s) => s.label).join(", ") });
  box.append(canvas);
  container.append(box);
  if (!window.Chart) {
    box.replaceChildren(el("p", { class: "muted small" }, "Chart library failed to load."));
    return null;
  }
  const surface = cssVar("--surface");
  const datasets = series.map((s) => ({ ...datasetStyle(s, surface), data: s.values, pointRadius: 2 }));
  const opts = baseOptions(yFormat, (items) => (items.length ? `${items[0].label}${xTitle ? " " + xTitle : ""}` : ""),
    (item) => ` ${item.dataset.label}: ${yFormat(item.parsed.y)}`);
  opts.interaction = { mode: "index", intersect: false };
  opts.scales.x = {
    type: "category",
    ticks: { color: cssVar("--muted"), font: { size: 11 }, maxRotation: 0, autoSkip: false },
    grid: { display: false }, border: { color: cssVar("--axis") },
  };
  return new window.Chart(canvas, { type: "line", data: { labels, datasets }, options: opts });
}

/**
 * Daily candlestick chart with optional line overlays (e.g. moving averages).
 * candles: [["YYYY-MM-DD", open, high, low, close], ...]
 * lines:   [{label, color, values: [number|null, ...]}]   (same length as candles)
 */
export function candleChart(container, candles, lines = [], { yFormat = (v) => v.toFixed(2), tall = true } = {}) {
  setupChart();
  const box = el("div", { class: tall ? "chart tall" : "chart" });
  const canvas = el("canvas", { role: "img", "aria-label": `Daily candlestick chart, ${candles.length} days` });
  box.append(canvas);
  container.append(box);
  if (!window.Chart) {
    box.replaceChildren(el("p", { class: "muted small" }, "Chart library failed to load."));
    return null;
  }
  const up = cssVar("--up"), down = cssVar("--down"), surface = cssVar("--surface");
  const colors = candles.map((c) => (c[4] >= c[1] ? up : down));
  const lo = Math.min(...candles.map((c) => c[3])), hi = Math.max(...candles.map((c) => c[2]));
  const eps = (hi - lo || 1) * 0.0015; // keep a doji (open == close) visible
  const labels = candles.map((c) => c[0]);
  const datasets = [
    ...lines.map((l) => ({
      type: "line", label: l.label, data: l.values, order: 0,
      borderColor: l.color, backgroundColor: l.color, borderWidth: 1.5,
      pointRadius: 0, pointHoverRadius: 3, pointBorderColor: surface, spanGaps: false, tension: 0,
    })),
    {
      type: "bar", label: "body", order: 1, grouped: false,
      data: candles.map((c) => (Math.abs(c[4] - c[1]) < eps ? [c[1] - eps, c[1] + eps] : [Math.min(c[1], c[4]), Math.max(c[1], c[4])])),
      backgroundColor: colors, borderColor: colors, borderWidth: 0,
      barPercentage: 0.75, categoryPercentage: 1, minBarLength: 1,
    },
    {
      type: "bar", label: "wick", order: 2, grouped: false,
      data: candles.map((c) => [c[3], c[2]]),
      backgroundColor: colors, borderWidth: 0, maxBarThickness: 1.5, barPercentage: 0.12, categoryPercentage: 1,
    },
  ];
  const opts = baseOptions(yFormat, (items) => (items.length ? fmtDayY(dayMs(labels[items[0].dataIndex])) : ""),
    (item) => {
      if (item.dataset.label === "body") {
        const [, o, h, l, c] = candles[item.dataIndex];
        const prev = item.dataIndex > 0 ? candles[item.dataIndex - 1][4] : null;
        const chg = prev ? ` (${c >= prev ? "▲ +" : "▼ −"}${Math.abs((c / prev - 1) * 100).toFixed(2)}%)` : "";
        return [` Open ${yFormat(o)}`, ` High ${yFormat(h)}`, ` Low ${yFormat(l)}`, ` Close ${yFormat(c)}${chg}`];
      }
      return item.parsed.y == null ? "" : ` ${item.dataset.label}: ${yFormat(item.parsed.y)}`;
    });
  opts.interaction = { mode: "index", intersect: false };
  opts.plugins.tooltip.filter = (item) => item.dataset.label !== "wick" && item.parsed.y != null;
  opts.plugins.tooltip.itemSort = (a, b) => (a.dataset.label === "body" ? -1 : b.dataset.label === "body" ? 1 : 0);
  opts.scales.y.beginAtZero = false;
  opts.scales.y.grace = "3%";
  opts.scales.x = {
    type: "category",
    ticks: {
      color: cssVar("--muted"), font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8,
      callback: (v, i) => fmtDay(dayMs(labels[i])),
    },
    grid: { display: false }, border: { color: cssVar("--axis") },
  };
  return new window.Chart(canvas, { data: { labels, datasets }, options: opts });
}

/** Tiny inline SVG trend line (no axes). */
export function sparkline(points, color, { width = 96, height = 26 } = {}) {
  const W = width, H = height, P = 2;
  const vals = (points || []).map((p) => p[1]).filter((v) => v != null);
  if (vals.length < 2) return el("span", { class: "muted" }, "—");
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const xy = vals.map((v, i) => `${(P + (i / (vals.length - 1)) * (W - 2 * P)).toFixed(1)},${(H - P - ((v - lo) / span) * (H - 2 * P)).toFixed(1)}`);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `trend from ${vals[0]} to ${vals[vals.length - 1]}`);
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("points", xy.join(" "));
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", color);
  line.setAttribute("stroke-width", "1.5");
  line.setAttribute("stroke-linejoin", "round");
  const dot = document.createElementNS(ns, "circle");
  const [lx, ly] = xy[xy.length - 1].split(",");
  dot.setAttribute("cx", lx); dot.setAttribute("cy", ly); dot.setAttribute("r", "2"); dot.setAttribute("fill", color);
  svg.append(line, dot);
  return svg;
}

export function legend(series) {
  return el("div", { class: "legend" }, series.map((s) =>
    el("span", {}, el("span", { class: s.dashed ? "sw dash" : "sw", style: { borderTopColor: s.color } }), s.label)));
}
