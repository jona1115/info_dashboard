// U.S. Treasury yield curve. data: {tenors: [...], curves: [{label, date, values}], spreads: [{label, points}]}
import { categoryChart, dayMs, el, fmtDayY, legend, seriesColor, sparkline } from "../util.js";

export function render(body, data) {
  const curves = data.curves || [];
  const series = curves.map((c, i) => ({
    label: `${c.label} (${fmtDayY(dayMs(c.date))})`,
    color: seriesColor(i),
    dashed: i > 0,
    values: c.values,
  }));
  categoryChart(body, data.tenors || [], series, { yFormat: (v) => `${Number(v).toFixed(2)}%`, xTitle: "maturity" });
  body.append(legend(series));

  const rows = (data.spreads || []).map((s) => {
    const last = s.points[s.points.length - 1][1];
    return el("tr", {},
      el("td", {}, s.label),
      el("td", {}, el("strong", {}, `${last >= 0 ? "+" : "−"}${Math.abs(last).toFixed(2)} pp`),
        last < 0 ? el("span", { class: "tag" }, "inverted") : null),
      el("td", { class: "spark-col" }, sparkline(s.points, seriesColor(0))));
  });
  if (rows.length) {
    body.append(el("div", { class: "table-wrap" }, el("table", { class: "quotes" },
      el("thead", {}, el("tr", {}, el("th", {}, "Spread"), el("th", {}, "Now"), el("th", { class: "spark-col" }, "1 year"))),
      el("tbody", {}, rows))));
  }
  body.append(el("p", { class: "muted small" },
    "Par yields by maturity, from the U.S. Treasury. A negative spread (inverted curve) has often come before recessions."));
}
