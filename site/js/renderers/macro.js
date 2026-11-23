// World economy table. data: {columns: [{key, label, desc}], rows: [{label, cells: {key: {v, period, prev, src}}, cpi_hist}]}
import { el, seriesColor, sparkline } from "../util.js";

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
const SOURCES = { OECD: "OECD", BIS: "BIS", DOSM: "DOSM (data.gov.my)" };

function period(p) {
  const q = /^(\d{4})-Q(\d)$/.exec(p);
  if (q) return `Q${q[2]} ${q[1]}`;
  const m = /^(\d{4})-(\d{2})$/.exec(p);
  if (m) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  return p || "";
}

function cell(c) {
  if (!c) return el("td", { class: "muted" }, "—");
  let move = "";
  if (c.prev != null) {
    const d = Number((c.v - c.prev).toFixed(2));
    move = d > 0 ? ` ▲${d.toFixed(2)}` : d < 0 ? ` ▼${Math.abs(d).toFixed(2)}` : " ±0";
  }
  return el("td", { title: `${period(c.period)} · source: ${SOURCES[c.src] || c.src}${c.prev != null ? ` · previous ${c.prev.toFixed(2)}%` : ""}` },
    el("strong", {}, `${c.v.toFixed(1)}%`),
    el("span", { class: "sub" }, `${period(c.period)}${move}`));
}

export function render(body, data) {
  const cols = data.columns || [];
  const head = el("tr", {}, el("th", {}, ""), cols.map((c) => el("th", { title: c.desc }, c.label)),
    el("th", { class: "spark-col" }, "Inflation, 2y"));
  const rows = (data.rows || []).map((r) => el("tr", {},
    el("td", {}, r.label),
    cols.map((c) => cell(r.cells[c.key])),
    el("td", { class: "spark-col" }, sparkline(r.cpi_hist, seriesColor(0)))));
  body.append(el("div", { class: "table-wrap" }, el("table", { class: "quotes" }, el("thead", {}, head), el("tbody", {}, rows))));
  body.append(el("p", { class: "muted small" },
    "Latest release for each; the period is under the value, with the change from the previous reading (percentage points). " +
    "GDP: real, vs same quarter a year earlier. Inflation: CPI vs a year earlier. 10Y yield: monthly average. " +
    "Sources: OECD, BIS, and DOSM for Malaysia. — means no free source publishes it. Hover a value for details."));
}
