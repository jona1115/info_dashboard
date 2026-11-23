// Stocks / ETFs / indexes.
// data: {range, display: "cards"|"table", groups: [{label, quotes: [{symbol, label, type, price, change, change_pct,
//        chg_1m_pct, chg_range_pct, currency, market_time, closes}]}]}   (older data: {range, quotes})
import { absTime, cssVar, el, lineChart, money, seriesColor, sparkline } from "../util.js";

const NUM = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function price(q) {
  if (q.price == null) return "—";
  if (q.type === "INDEX") return NUM.format(q.price);
  return q.currency === "USD" ? `$${NUM.format(q.price)}` : `${NUM.format(q.price)} ${q.currency || ""}`;
}

/** Yahoo Finance quote page (where the data comes from). */
const quoteUrl = (sym) => `https://finance.yahoo.com/quote/${encodeURIComponent(sym)}/`;
const link = (sym, text) => el("a", { href: quoteUrl(sym), target: "_blank", rel: "noopener noreferrer" }, text);

/** Signed % with arrow + color class (direction never relies on color alone). */
function pct(v) {
  if (v == null || Number.isNaN(v)) return el("span", { class: "muted" }, "—");
  const r = Number(v.toFixed(2));
  const cls = r > 0 ? "up" : r < 0 ? "down" : "";
  const txt = r === 0 ? "0.00%" : `${r > 0 ? "▲ +" : "▼ −"}${Math.abs(r).toFixed(2)}%`;
  return el("span", { class: cls }, txt);
}

/** Last trade: time if today (dashboard time zone), else date. */
function when(iso, tz) {
  if (!iso) return null;
  const d = new Date(iso);
  const day = (x) => x.toLocaleDateString("en-CA", { timeZone: tz });
  if (day(d) === day(new Date())) return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: tz });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: tz });
}

function table(body, groups, data, site) {
  // Trend line is red when the stock lost value over the range (e.g. 6 months), blue otherwise.
  const trendColor = (q) => (q.chg_range_pct != null && q.chg_range_pct < 0 ? cssVar("--down") : seriesColor(0));
  const head = el("tr", {}, el("th", {}, ""), el("th", {}, "Price"), el("th", {}, "Day"),
    el("th", {}, "1M"), el("th", {}, data.range || "Range"), el("th", { class: "spark-col" }, `${data.range || ""} trend`));
  const rows = [];
  for (const g of groups) {
    if (g.label) rows.push(el("tr", { class: "group-row" }, el("td", { colspan: 6 }, g.label)));
    for (const q of g.quotes || []) {
      rows.push(el("tr", { title: [q.name, q.exchange, q.market_time ? `last trade ${absTime(q.market_time, site.timezone)}` : null].filter(Boolean).join(" · ") },
        el("td", {}, link(q.symbol, q.label || q.symbol),
          el("span", { class: "sub" }, [q.symbol.replace(/^\^/, ""), when(q.market_time, site.timezone)].filter(Boolean).join(" · ")),
          q.stale ? el("span", { class: "tag" }, "stale") : null),
        el("td", {}, price(q)),
        el("td", {}, pct(q.change_pct)),
        el("td", {}, pct(q.chg_1m_pct)),
        el("td", {}, pct(q.chg_range_pct)),
        el("td", { class: "spark-col" }, sparkline(q.closes, trendColor(q)))));
    }
  }
  body.append(el("div", { class: "table-wrap" }, el("table", { class: "quotes" }, el("thead", {}, head), el("tbody", {}, rows))));
  body.append(el("p", { class: "muted small" },
    `Day = vs previous close; 1M and ${data.range} = change over that period. Under each name: symbol · last trade ` +
    `(time if today, otherwise date).` +
    (groups.some((g) => (g.quotes || []).some((q) => q.type === "INDEX")) ? " Indexes are in points, not dollars." : "")));
}

function cards(body, groups, data, site) {
  for (const q of groups.flatMap((g) => g.quotes || [])) {
    body.append(el("div", { class: "quote" },
      link(q.symbol, el("span", { class: "price num" }, price(q))),
      el("span", { class: "num" }, pct(q.change_pct)),
      el("span", { class: "muted small" }, [q.symbol, q.exchange].filter(Boolean).join(" · ")),
      q.stale ? el("span", { class: "tag" }, "stale") : null));
    body.append(el("p", { class: "muted small" },
      link(q.symbol, q.label || q.name), `. Change vs previous close. Last trade ${absTime(q.market_time, site.timezone)}.`));
    if ((q.closes || []).length > 1) {
      const fmt = q.type === "INDEX" ? (v) => NUM.format(v) : (v) => money(v, v >= 100 ? 0 : 2, q.currency);
      lineChart(body, [{ label: `${q.symbol} close`, color: seriesColor(0), points: q.closes }], { yFormat: fmt, short: true });
      body.append(el("p", { class: "muted small" }, `Daily closes, last ${data.range}.`));
    }
  }
}

export function render(body, data, widget, site) {
  const groups = data.groups || [{ label: null, quotes: data.quotes || [] }];
  if (data.display === "table") table(body, groups, data, site);
  else cards(body, groups, data, site);
}
