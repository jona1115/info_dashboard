// Market sentiment: CNN Fear & Greed (score, gauge, history, 7 parts), crypto Fear & Greed, volatility indexes.
// data: {cnn: {score, rating, previous, history, components, stale}, crypto: {score, rating, history},
//        volatility: [{symbol, label, note, price, change_pct, closes, stale}], term: {near, far, ratio, state}}
import { dayMs, delta, el, lineChart, legend, seriesColor, sparkline, withAlpha, cssVar } from "../util.js";
import { buttonGroup, uiState } from "../tabs.js";

const PERIODS = [["1M", 1], ["3M", 3], ["6M", 6], ["1Y", 12]];
const CNN_PAGE = "https://www.cnn.com/markets/fear-and-greed";
const CRYPTO_PAGE = "https://alternative.me/crypto/fear-and-greed-index/";
const yahoo = (s) => `https://finance.yahoo.com/quote/${encodeURIComponent(s)}/`;

// CNN's bands (0-24, 25-44, 45-55, 56-75, 76-100).
export const FG_ZONES = [
  { from: 0, to: 25, label: "Extreme fear", tone: "down", a: 0.16 },
  { from: 25, to: 45, label: "Fear", tone: "down", a: 0.07 },
  { from: 45, to: 55, label: "Neutral", tone: null, a: 0 },
  { from: 55, to: 75, label: "Greed", tone: "up", a: 0.07 },
  { from: 75, to: 100, label: "Extreme greed", tone: "up", a: 0.16 },
];
const VIX_ZONES = [
  { from: 0, to: 15, label: "Calm" },
  { from: 15, to: 20, label: "Normal" },
  { from: 20, to: 30, label: "Elevated", tone: "down", a: 0.05 },
  { from: 30, to: 200, label: "High fear", tone: "down", a: 0.12 },
];

/** CNN zone for a score: 0-24, 25-44, 45-55, 56-75, 76-100 (scores rounded first). */
export function zoneOf(score) {
  const s = Math.round(score);
  return FG_ZONES[s < 25 ? 0 : s < 45 ? 1 : s <= 55 ? 2 : s <= 75 ? 3 : 4];
}

const shade = (z, k = 1) => (z.tone ? withAlpha(cssVar(z.tone === "up" ? "--up" : "--down"), Math.min(1, z.a * k)) : null);

/** First date (YYYY-MM-DD) kept for a period ending at lastDate. */
export function periodFrom(lastDate, months) {
  const [y, m, d] = lastDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1 - months, d)).toISOString().slice(0, 10);
}

function gauge(score) {
  const marker = el("span", { class: "gauge-marker", style: { left: `${Math.max(0, Math.min(100, score))}%` } });
  return el("div", { class: "gauge", role: "img", "aria-label": `Fear & Greed ${Math.round(score)} of 100` },
    el("div", { class: "gauge-track" },
      FG_ZONES.map((z) => el("span", { class: "gauge-zone", title: `${z.label} (${z.from}–${z.to})`,
        style: { width: `${z.to - z.from}%`, background: shade(z, 2.5) || cssVar("--grid") } })),
      marker),
    el("div", { class: "gauge-scale" }, el("span", {}, "0 · Extreme fear"), el("span", {}, "Neutral"),
      el("span", {}, "Extreme greed · 100")));
}

function headline(cnn, crypto) {
  const z = zoneOf(cnn.score);
  const prev = cnn.previous || {};
  const ago = [["close", "Prev close"], ["1_week", "1 week ago"], ["1_month", "1 month ago"], ["1_year", "1 year ago"]]
    .filter(([k]) => prev[k] != null)
    .map(([k, label]) => el("span", { title: zoneOf(prev[k]).label }, `${label} `, el("strong", { class: "num" }, Math.round(prev[k]))));
  return el("div", { class: "fg-head" },
    el("div", { class: "quote" },
      el("a", { href: CNN_PAGE, target: "_blank", rel: "noopener noreferrer", class: "price num" }, Math.round(cnn.score)),
      el("span", { class: "fg-rating" }, cnn.rating || z.label),
      el("span", { class: "muted small" }, "CNN Fear & Greed Index, 0–100"),
      cnn.stale ? el("span", { class: "tag" }, "stale") : null),
    gauge(cnn.score),
    el("div", { class: "fg-ago small" }, ago,
      crypto ? el("span", {}, "Crypto ", el("strong", { class: "num" }, Math.round(crypto.score)), ` (${crypto.rating})`,
        crypto.stale ? el("span", { class: "tag" }, "stale") : null) : null));
}

function drawChart(area, data, view, months) {
  if (window.Chart) area.querySelectorAll("canvas").forEach((c) => window.Chart.getChart(c)?.destroy());
  area.replaceChildren();
  let series, opts;
  if (view === "fg") {
    series = [];
    if (data.cnn) series.push({ label: "CNN Fear & Greed (stocks)", color: seriesColor(0), points: data.cnn.history || [] });
    if (data.crypto) series.push({ label: "Crypto Fear & Greed", color: seriesColor(1), points: data.crypto.history || [] });
    opts = { yMin: 0, yMax: 100, yFormat: (v) => String(Math.round(v)),
      bands: FG_ZONES.map((z) => ({ ...z, color: shade(z) })) };
  } else {
    const vix = (data.volatility || []).slice(0, 3);   // the first three share one scale (VIX-family, in %)
    series = vix.map((q, i) => ({ label: q.label, color: seriesColor(i), dashed: i > 0, points: q.closes || [] }));
    const hi = Math.max(...series.flatMap((s) => s.points.map((p) => p[1])), 30);
    opts = { yMin: 0, yMax: Math.ceil(hi / 10) * 10, yFormat: (v) => Number(v).toFixed(v < 10 ? 1 : 0),
      bands: VIX_ZONES.map((z) => ({ ...z, color: shade(z) })) };
  }
  series = series.filter((s) => s.points.length);
  if (!series.length) {
    area.append(el("p", { class: "muted" }, "No history to chart."));
    return;
  }
  const last = series.map((s) => s.points[s.points.length - 1][0]).sort().pop();
  const from = periodFrom(last, months);
  lineChart(area, series, { ...opts, minMs: dayMs(from), maxMs: dayMs(last) });
  area.append(legend(series));
}

function componentsTable(cnn) {
  const rows = (cnn.components || []).map((c) => {
    const z = zoneOf(c.score);
    return el("tr", {},
      el("td", { class: "l" }, c.label, el("div", { class: "muted small" }, c.what)),
      el("td", { class: "bar-col" }, el("span", { class: "meter", title: `${c.score} of 100` },
        el("span", { class: "meter-fill", style: { width: `${Math.max(2, Math.min(100, c.score))}%`,
          background: z.tone ? cssVar(z.tone === "up" ? "--up" : "--down") : cssVar("--muted") } }))),
      el("td", { class: "num" }, Math.round(c.score)),
      el("td", { class: "l" }, c.rating || z.label));
  });
  if (!rows.length) return null;
  return el("div", { class: "table-wrap" }, el("table", { class: "quotes" },
    el("thead", {}, el("tr", {}, el("th", { class: "l" }, "CNN indicator"), el("th", { class: "bar-col" }, ""),
      el("th", {}, "Score"), el("th", { class: "l" }, "Reading"))),
    el("tbody", {}, rows)));
}

function volTable(vol) {
  if (!vol.length) return null;
  const rows = vol.map((q) => el("tr", {},
    el("td", { class: "l" }, el("a", { href: yahoo(q.symbol), target: "_blank", rel: "noopener noreferrer" }, q.label),
      q.stale ? el("span", { class: "tag" }, "stale") : null,
      q.note ? el("div", { class: "muted small" }, q.note) : null),
    el("td", { class: "num" }, q.price == null ? "—" : Number(q.price).toFixed(2)),
    el("td", { class: `num ${q.change_pct > 0 ? "down" : q.change_pct < 0 ? "up" : ""}`,
      title: "For volatility, up means more fear" }, delta(q.change_pct, 2, "%")),
    el("td", { class: "spark-col" }, sparkline((q.closes || []).slice(-126), seriesColor(0)))));
  return el("div", { class: "table-wrap" }, el("table", { class: "quotes" },
    el("thead", {}, el("tr", {}, el("th", { class: "l" }, "Volatility"), el("th", {}, "Level"), el("th", {}, "Day"),
      el("th", { class: "spark-col" }, "6 months"))),
    el("tbody", {}, rows)));
}

export function render(body, data, widget) {
  const { cnn, crypto } = data;
  const vol = data.volatility || [];
  if (!cnn && !crypto && !vol.length) {
    body.append(el("p", { class: "muted" }, "No data."));
    return;
  }
  if (cnn) body.append(headline(cnn, crypto));

  const state = uiState(widget.id);
  const views = [];
  if (cnn || crypto) views.push({ key: "fg", label: "Fear & Greed" });
  if (vol.length) views.push({ key: "vol", label: "Volatility (VIX)" });
  let view = state.get("view", views[0].key);
  if (!views.some((v) => v.key === view)) view = views[0].key;
  let period = state.get("period", "6M");
  if (!PERIODS.some(([p]) => p === period)) period = "6M";
  const months = () => PERIODS.find(([p]) => p === period)[1];

  const area = el("div", { class: "tab-panel", role: "tabpanel" });
  const redraw = () => drawChart(area, data, view, months());
  body.append(el("div", { class: "tab-controls" },
    buttonGroup(views, view, (k) => { view = k; state.set("view", k); redraw(); }, { kind: "tabs", label: "Chart" }),
    buttonGroup(PERIODS.map(([p]) => ({ key: p, label: p })), period,
      (k) => { period = k; state.set("period", k); redraw(); }, { kind: "seg", label: "Period" })), area);
  redraw();

  if (data.term) {
    const t = data.term;
    body.append(el("p", { class: "small" },
      `${t.near} ÷ ${t.far}: `, el("strong", { class: "num" }, t.ratio.toFixed(2)), " — ",
      t.state === "backwardation"
        ? "backwardation: near-term fear is above longer-term (stress)."
        : "contango: near-term volatility is below longer-term (normal, calm)."));
  }
  const tables = [cnn ? componentsTable(cnn) : null, volTable(vol)].filter(Boolean);
  if (tables.length) body.append(el("div", { class: "sent-tables" }, tables));

  body.append(el("p", { class: "muted small" },
    el("a", { href: CNN_PAGE, target: "_blank", rel: "noopener noreferrer" }, "CNN Fear & Greed"),
    " blends seven indicators into a 0–100 score (under 25 extreme fear, over 75 extreme greed). ",
    crypto ? el("a", { href: CRYPTO_PAGE, target: "_blank", rel: "noopener noreferrer" }, "Crypto Fear & Greed") : null,
    crypto ? " is alternative.me's index for Bitcoin. " : null,
    "Volatility indexes from Cboe via Yahoo Finance; a VIX above 30 usually means high fear."));
}
