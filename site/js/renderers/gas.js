// AAA gas prices: today's table + 6-month chart (AAA daily recordings + EIA weekly backfill).
import { dayMs, delta, el, fmtDayY, legend, lineChart, money, seriesColor } from "../util.js";

const GRADE_NAME = { regular: "Regular", mid: "Mid-grade", premium: "Premium", diesel: "Diesel" };

export function render(body, data) {
  const grade = data.grade || "regular";
  const regions = data.regions || [];

  // ---- table
  const head = el("tr", {},
    el("th", {}, "Area"), el("th", {}, GRADE_NAME[grade] || grade),
    el("th", {}, "vs yesterday"), el("th", {}, "vs week ago"), el("th", {}, "vs month ago"), el("th", {}, "vs year ago"),
    el("th", {}, grade === "premium" ? "Regular" : "Premium"), el("th", {}, "Diesel"));
  const rows = regions.map((r, i) => {
    const p = r.prices || {};
    const cur = (p.current || {})[grade];
    const vs = (k) => { const old = (p[k] || {})[grade]; return cur != null && old != null ? delta(cur - old, 3) : "—"; };
    const other = (p.current || {})[grade === "premium" ? "regular" : "premium"];
    return el("tr", {},
      el("td", {}, el("span", { class: "key", style: { background: seriesColor(i) } }), r.label,
        r.stale ? el("span", { class: "tag" }, "stale") : null),
      el("td", { class: "num" }, el("strong", {}, money(cur, 3))),
      el("td", {}, vs("yesterday")), el("td", {}, vs("week_ago")), el("td", {}, vs("month_ago")), el("td", {}, vs("year_ago")),
      el("td", {}, money(other, 3)), el("td", {}, money((p.current || {}).diesel, 3)));
  });
  body.append(el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, rows))));

  // ---- chart
  const series = [];
  regions.forEach((r, i) => {
    const color = seriesColor(i);
    const daily = (data.history || {})[r.key] || [];
    const weekly = (data.eia || {})[r.key] || [];
    if (daily.length) series.push({ label: `${r.label} · AAA daily`, color, points: daily });
    if (weekly.length) series.push({ label: `${r.label} · EIA weekly`, color, dashed: true, points: weekly });
  });
  if (!series.length) return;
  const maxMs = Math.max(...series.flatMap((s) => s.points.map((p) => dayMs(p[0]))));
  const minMs = maxMs - (data.window_days || 180) * 86400000;
  lineChart(body, series, { minMs, maxMs, yFormat: (v) => money(v) });
  body.append(legend(series));
  const firstAaa = regions.map((r) => ((data.history || {})[r.key] || [])[0]).filter(Boolean).map((p) => p[0]).sort()[0];
  const asOf = regions.find((r) => r.as_of)?.as_of;
  body.append(el("p", { class: "muted small" },
    `${GRADE_NAME[grade] || grade}, $/gal. AAA prices as of ${asOf ? fmtDayY(dayMs(asOf)) : "—"}`,
    firstAaa ? `; daily AAA line recorded since ${fmtDayY(dayMs(firstAaa))}.` : ".",
    " Dashed: U.S. EIA weekly retail averages (no Austin series)."));
}
