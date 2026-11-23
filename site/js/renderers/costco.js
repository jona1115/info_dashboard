// Costco gas. data: {as_of, warehouses: [{id, name, address, distance_mi, prices, gas_hours}], history: {id: [[d, v]]}}
import { dayMs, el, fmtDayY, legend, lineChart, money, seriesColor } from "../util.js";

export function render(body, data) {
  const whs = data.warehouses || [];
  const hasDiesel = whs.some((w) => w.prices && w.prices.diesel != null);
  const regs = whs.map((w) => w.prices && w.prices.regular).filter((v) => v != null);
  const low = regs.length ? Math.min(...regs) : null;
  const charted = Object.keys(data.history || {});

  const head = el("tr", {}, el("th", {}, "Warehouse"), el("th", {}, "Miles"), el("th", {}, "Regular"),
    el("th", {}, "Premium"), hasDiesel ? el("th", {}, "Diesel") : null);
  const rows = whs.map((w) => {
    const ci = charted.indexOf(w.id);
    const p = w.prices || {};
    return el("tr", {},
      el("td", {},
        ci >= 0 ? el("span", { class: "key", style: { background: seriesColor(ci) } }) : el("span", { class: "key" }),
        w.name, el("span", { class: "sub" }, w.address)),
      el("td", {}, w.distance_mi.toFixed(1)),
      el("td", {}, el("strong", {}, money(p.regular, 3)), p.regular != null && p.regular === low ? el("span", { class: "tag" }, "lowest") : null),
      el("td", {}, money(p.premium, 3)),
      hasDiesel ? el("td", {}, money(p.diesel, 3)) : null);
  });
  body.append(el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head), el("tbody", {}, rows))));

  const series = charted.map((id, i) => {
    const w = whs.find((x) => x.id === id);
    return { label: w ? w.name : `#${id}`, color: seriesColor(i), points: data.history[id] || [] };
  }).filter((s) => s.points.length);
  const days = new Set(series.flatMap((s) => s.points.map((p) => p[0])));
  if (days.size >= 2) {
    lineChart(body, series, { short: true, yFormat: (v) => money(v) });
    body.append(legend(series));
    body.append(el("p", { class: "muted small" }, "Regular, $/gal, one reading per day."));
  } else {
    const first = [...days].sort()[0];
    body.append(el("p", { class: "muted small" },
      `Prices as of ${data.as_of ? fmtDayY(dayMs(data.as_of)) : "—"}. A price-history chart appears once there are two days of readings` +
      (first ? ` (recording since ${fmtDayY(dayMs(first))}).` : ".")));
  }
}
