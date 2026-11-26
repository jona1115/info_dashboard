// Tabbed daily candlestick charts with moving averages.
// data: {periods: ["1W", ...], default_period, ma: [20, 50, 200],
//        tabs: [{symbol, label, name, currency, market_time, stale, candles: [[d, o, h, l, c, volume]], ma: {"20": [...]}}]}
import { absTime, candleChart, el, seriesColor } from "../util.js";
import { buttonGroup, uiState } from "../tabs.js";

const NUM = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const PERIOD_TITLES = {
  "1W": "1 week", "1M": "1 month", "3M": "3 months", "6M": "6 months",
  MTD: "Month to date", YTD: "Year to date", "1Y": "1 year",
};

function addMonths(y, m, d, delta) {
  const t = new Date(Date.UTC(y, m - 1 + delta, 1));
  const last = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() + 1, 0)).getUTCDate();
  return new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), Math.min(d, last)));
}

/** First date (YYYY-MM-DD) shown for a period, given the last candle's date. Exclusive bound for rolling periods. */
export function periodBounds(lastDate, period) {
  const [y, m, d] = lastDate.split("-").map(Number);
  const isoDay = (dt) => dt.toISOString().slice(0, 10);
  switch (period) {
    case "1W": return { after: isoDay(new Date(Date.UTC(y, m - 1, d - 7))) };
    case "1M": return { after: isoDay(addMonths(y, m, d, -1)) };
    case "3M": return { after: isoDay(addMonths(y, m, d, -3)) };
    case "6M": return { after: isoDay(addMonths(y, m, d, -6)) };
    case "1Y": return { after: isoDay(addMonths(y, m, d, -12)) };
    case "MTD": return { from: `${y}-${String(m).padStart(2, "0")}-01` };
    case "YTD": return { from: `${y}-01-01` };
    default: throw new Error(`unknown period ${period}`);
  }
}

/** Index of the first candle inside the period. */
export function periodStart(candles, period) {
  if (!candles.length) return 0;
  const b = periodBounds(candles[candles.length - 1][0], period);
  const i = candles.findIndex((c) => (b.from ? c[0] >= b.from : c[0] > b.after));
  return i < 0 ? candles.length - 1 : i;
}

function pct(v) {
  if (v == null || !Number.isFinite(v)) return el("span", { class: "muted" }, "—");
  const r = Number(v.toFixed(2));
  return el("span", { class: r > 0 ? "up" : r < 0 ? "down" : "" },
    r === 0 ? "0.00%" : `${r > 0 ? "▲ +" : "▼ −"}${Math.abs(r).toFixed(2)}%`);
}

function money(v, cur) {
  return v == null ? "—" : cur === "USD" || !cur ? `$${NUM.format(v)}` : `${NUM.format(v)} ${cur}`;
}

function drawTab(area, tab, period, data, site) {
  if (window.Chart) area.querySelectorAll("canvas").forEach((c) => window.Chart.getChart(c)?.destroy());
  area.replaceChildren();
  const all = tab.candles || [];
  if (!all.length) {
    area.append(el("p", { class: "muted" }, "No price data."));
    return;
  }
  const start = periodStart(all, period);
  const shown = all.slice(start);
  const last = all[all.length - 1];
  const prevClose = all.length > 1 ? all[all.length - 2][4] : null;
  const base = start > 0 ? all[start - 1][4] : shown[0][1];
  const url = `https://finance.yahoo.com/quote/${encodeURIComponent(tab.symbol)}/`;
  const mas = (data.ma || []).map((n, i) => ({
    n, color: seriesColor(i), values: ((tab.ma || {})[String(n)] || []).slice(start),
  }));

  area.append(el("div", { class: "quote" },
    el("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "price num" }, money(last[4], tab.currency)),
    el("span", { class: "num", title: "vs previous close" }, "Day ", pct(prevClose ? (last[4] / prevClose - 1) * 100 : null)),
    el("span", { class: "num", title: `${PERIOD_TITLES[period]} change` }, `${period} `, pct((last[4] / base - 1) * 100)),
    tab.stale ? el("span", { class: "tag" }, "stale") : null));
  area.append(el("p", { class: "muted small" },
    el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, tab.name || tab.symbol),
    ` · daily candles, ${PERIOD_TITLES[period].toLowerCase()} (${shown.length} trading days)` +
    (tab.market_time ? ` · last trade ${absTime(tab.market_time, site && site.timezone)}` : "")));

  candleChart(area, shown, mas.map((m) => ({ label: `MA${m.n}`, color: m.color, values: m.values })),
    { yFormat: (v) => NUM.format(v) });

  area.append(el("div", { class: "legend" },
    el("span", {}, el("span", { class: "candle-key up-bg" }), el("span", { class: "candle-key down-bg" }),
      shown.some((c) => c[5] != null) ? "Daily candle and volume (up / down)" : "Daily candle (up / down)"),
    mas.map((m) => {
      const v = m.values[m.values.length - 1];
      const rel = v == null ? "" : last[4] >= v ? " (price above)" : " (price below)";
      return el("span", { title: `${m.n}-day simple moving average of the close` },
        el("span", { class: "sw", style: { borderTopColor: m.color } }),
        `MA${m.n} ${v == null ? "—" : NUM.format(v)}${rel}`);
    })));
}

export function render(body, data, widget, site) {
  const tabs = data.tabs || [];
  if (!tabs.length) {
    body.append(el("p", { class: "muted" }, "No data."));
    return;
  }
  const state = uiState(widget.id);
  const periods = data.periods || ["3M"];
  let symbol = state.get("tab", tabs[0].symbol);
  if (!tabs.some((t) => t.symbol === symbol)) symbol = tabs[0].symbol;
  let period = state.get("period", data.default_period || periods[0]);
  if (!periods.includes(period)) period = data.default_period || periods[0];

  const area = el("div", { class: "tab-panel", role: "tabpanel" });
  const redraw = () => drawTab(area, tabs.find((t) => t.symbol === symbol), period, data, site);

  const controls = el("div", { class: "tab-controls" },
    buttonGroup(tabs.map((t) => ({ key: t.symbol, label: t.label || t.symbol, title: t.name })), symbol,
      (k) => { symbol = k; state.set("tab", k); redraw(); }, { kind: "tabs", label: "Ticker" }),
    buttonGroup(periods.map((p) => ({ key: p, label: p, title: PERIOD_TITLES[p] })), period,
      (k) => { period = k; state.set("period", k); redraw(); }, { kind: "seg", label: "Period" }));
  body.append(controls, area);
  redraw();
}
